#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""librestatistics classic (v1.0) — a clean live terminal dashboard for CPU, RAM and GPU usage.

Usage:
    librestatistics-classic              # live dashboard (Ctrl+C to quit)
    librestatistics-classic -i 0.5       # refresh every 0.5 s
    librestatistics-classic --once       # print a single snapshot and exit

GPU support:
    NVIDIA  via nvidia-smi
    AMD     via amdgpu sysfs (gpu_busy_percent, VRAM)
    Intel   via DRM fdinfo engine counters + sysfs frequency
    Windows any vendor via PDH "GPU Engine" counters (when no NVIDIA GPU)
"""

import argparse
import glob
import os
import shutil
import subprocess
import time
from collections import deque
from datetime import datetime

import psutil
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from librestatistics import _compat

HISTORY = 50
SPARK = "▁▂▃▄▅▆▇█"


# ── helpers ──────────────────────────────────────────────────────────────────

def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def fmt_bytes(n):
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n:.0f}B"
        n /= 1024


def level_color(pct):
    if pct is None:
        return "grey50"
    if pct < 50:
        return "green"
    if pct < 80:
        return "yellow"
    return "red"


def bar(pct, width=24):
    """Horizontal bar with eighth-block precision."""
    if pct is None:
        return Text("─" * width, style="grey30")
    pct = max(0.0, min(100.0, pct))
    filled = pct / 100 * width
    full = int(filled)
    part = int((filled - full) * 8)
    s = "█" * full + ("▏▎▍▌▋▊▉"[part - 1] if part else "")
    t = Text(s, style=level_color(pct))
    t.append("·" * (width - len(s)), style="grey23")
    return t


def sparkline(values, width=HISTORY):
    vals = list(values)[-width:]
    t = Text()
    for v in vals:
        if v is None:
            t.append(" ")
        else:
            idx = min(len(SPARK) - 1, int(max(0, v) / 100 * len(SPARK)))
            t.append(SPARK[idx], style=level_color(v))
    return t


def meter_row(label, pct, detail="", width=24):
    t = Text()
    t.append(f"{label:<6}", style="bold")
    t.append_text(bar(pct, width))
    t.append(f" {pct:5.1f}%" if pct is not None else "   n/a", style=level_color(pct))
    if detail:
        t.append(f"  {detail}", style="grey62")
    return t


# ── GPU backends ─────────────────────────────────────────────────────────────

class GPU:
    name = "GPU"

    def sample(self):
        """Return dict: util, mem_used, mem_total, temp, power, freq, freq_max, engines."""
        raise NotImplementedError


class NvidiaGPU(GPU):
    def __init__(self, index, name):
        self.index, self.name = index, name

    @staticmethod
    def detect():
        if not shutil.which("nvidia-smi"):
            return []
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=3,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        gpus = []
        for line in out.strip().splitlines():
            idx, name = [x.strip() for x in line.split(",", 1)]
            gpus.append(NvidiaGPU(idx, name))
        return gpus

    def sample(self):
        q = "utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,clocks.gr,clocks.max.gr"
        try:
            out = subprocess.run(
                ["nvidia-smi", "-i", self.index, f"--query-gpu={q}",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        vals = [v.strip() for v in out.split(",")]

        def num(v):
            try:
                return float(v)
            except ValueError:
                return None

        u, mu, mt, temp, pw, f, fm = (num(v) for v in vals + [""] * (7 - len(vals)))
        return {
            "util": u,
            "mem_used": mu * 2**20 if mu is not None else None,
            "mem_total": mt * 2**20 if mt is not None else None,
            "temp": temp, "power": pw, "freq": f, "freq_max": fm,
        }


class AmdGPU(GPU):
    def __init__(self, card):
        self.card = card
        self.dev = f"/sys/class/drm/{card}/device"
        self.name = read(f"{self.dev}/product_name") or f"AMD GPU ({card})"

    @staticmethod
    def detect():
        cards = sorted(glob.glob("/sys/class/drm/card[0-9]"))
        return [AmdGPU(os.path.basename(c)) for c in cards
                if os.path.exists(f"{c}/device/gpu_busy_percent")]

    def sample(self):
        def num(p):
            v = read(p)
            return float(v) if v is not None else None

        temp = None
        for h in glob.glob(f"{self.dev}/hwmon/hwmon*/temp1_input"):
            t = num(h)
            if t is not None:
                temp = t / 1000
        power = None
        for h in glob.glob(f"{self.dev}/hwmon/hwmon*/power1_average"):
            p = num(h)
            if p is not None:
                power = p / 1e6
        return {
            "util": num(f"{self.dev}/gpu_busy_percent"),
            "mem_used": num(f"{self.dev}/mem_info_vram_used"),
            "mem_total": num(f"{self.dev}/mem_info_vram_total"),
            "temp": temp, "power": power,
        }


class IntelGPU(GPU):
    """Intel iGPU: utilisation from per-client DRM fdinfo engine busy-time counters.

    Only processes readable by the current user are counted (no root needed),
    which on a desktop session usually covers the compositor and your apps.
    """

    ENGINES = {"render": "3D", "video": "Video", "video-enhance": "VidEnh", "copy": "Blit"}

    def __init__(self, card):
        self.card = card
        self.sys = f"/sys/class/drm/{card}"
        self.name = "Intel integrated GPU"
        self._prev = None  # (wall_ns, {client_id: {engine: ns}})

    @staticmethod
    def detect():
        gpus = []
        for c in sorted(glob.glob("/sys/class/drm/card[0-9]")):
            if read(f"{c}/device/vendor") == "0x8086" and os.path.exists(f"{c}/gt_cur_freq_mhz"):
                gpus.append(IntelGPU(os.path.basename(c)))
        return gpus

    def _read_clients(self):
        clients, mem = {}, 0
        for fdinfo in glob.glob("/proc/[0-9]*/fdinfo/*"):
            try:
                with open(fdinfo) as f:
                    data = f.read()
            except OSError:
                continue
            if "drm-engine-" not in data:
                continue
            fields = {}
            for line in data.splitlines():
                k, _, v = line.partition(":")
                fields[k.strip()] = v.strip()
            cid = fields.get("drm-client-id")
            if cid is None or cid in clients:
                continue
            clients[cid] = {
                k[len("drm-engine-"):]: int(v.split()[0])
                for k, v in fields.items()
                if k.startswith("drm-engine-") and not k.startswith("drm-engine-capacity")
            }
            for k, v in fields.items():
                if k.startswith("drm-resident-"):
                    num, *unit = v.split()
                    mult = {"KiB": 1024, "MiB": 2**20, "GiB": 2**30}.get(unit[0] if unit else "", 1)
                    mem += int(num) * mult
        return clients, mem

    def sample(self):
        now = time.monotonic_ns()
        clients, mem = self._read_clients()
        engines = None
        if self._prev and clients:
            prev_t, prev = self._prev
            dt = now - prev_t
            engines = {e: 0.0 for e in self.ENGINES}
            for cid, eng in clients.items():
                if cid not in prev:
                    continue
                for e, ns in eng.items():
                    if e in engines:
                        engines[e] += max(0, ns - prev[cid].get(e, 0))
            engines = {self.ENGINES[e]: min(100.0, v / dt * 100) for e, v in engines.items()}
        self._prev = (now, clients)

        def num(p):
            v = read(p)
            return float(v) if v is not None else None

        temp = None
        temps = _compat.temperatures()
        for entry in temps.get("coretemp", []):
            if entry.label.startswith("Package"):
                temp = entry.current  # iGPU shares the CPU package
        return {
            "util": max(engines.values()) if engines else None,
            "engines": engines,
            "mem_used": mem or None,
            "mem_total": None,
            "temp": temp,
            "freq": num(f"{self.sys}/gt_act_freq_mhz") or num(f"{self.sys}/gt_cur_freq_mhz"),
            "freq_max": num(f"{self.sys}/gt_max_freq_mhz"),
            "no_clients": not clients,
        }


def detect_gpus():
    gpus = NvidiaGPU.detect() + AmdGPU.detect() + IntelGPU.detect()
    if not gpus and _compat.IS_WIN:
        gpus = _compat.WindowsGPU.detect()
    return gpus


# ── dashboard ────────────────────────────────────────────────────────────────

class Monitor:
    def __init__(self):
        self.gpus = detect_gpus()
        self.cpu_hist = deque(maxlen=HISTORY)
        self.ram_hist = deque(maxlen=HISTORY)
        self.gpu_hist = [deque(maxlen=HISTORY) for _ in self.gpus]
        self.gpu_data = [{} for _ in self.gpus]
        psutil.cpu_percent(percpu=True)
        for _ in psutil.process_iter(["cpu_percent"]):
            pass
        for g in self.gpus:
            g.sample()

    def update(self):
        self.per_core = psutil.cpu_percent(percpu=True)
        self.cpu_total = sum(self.per_core) / len(self.per_core)
        self.vm = psutil.virtual_memory()
        self.swap = psutil.swap_memory()
        self.cpu_hist.append(self.cpu_total)
        self.ram_hist.append(self.vm.percent)
        for i, g in enumerate(self.gpus):
            self.gpu_data[i] = g.sample()
            self.gpu_hist[i].append(self.gpu_data[i].get("util"))

        procs = []
        for p in psutil.process_iter(["pid", "name", "username", "cpu_percent", "memory_info"]):
            info = p.info
            if info["memory_info"] is None or (_compat.IS_WIN and info["pid"] == 0):
                continue
            procs.append(info)
        self.procs = sorted(procs, key=lambda x: (x["cpu_percent"] or 0, x["memory_info"].rss),
                            reverse=True)[:60]

    # panels

    def header(self):
        boot = datetime.fromtimestamp(psutil.boot_time())
        up = datetime.now() - boot
        h, rem = divmod(int(up.total_seconds()), 3600)
        t = Text()
        t.append(" LibreStatistics classic ", style="bold black on cyan")
        t.append(f"  {_compat.hostname()}", style="bold")
        t.append(f"  ·  up {h // 24}d {h % 24}h {rem // 60}m", style="grey62")
        la = _compat.loadavg()
        if la:
            t.append(f"  ·  load {la[0]:.2f} {la[1]:.2f} {la[2]:.2f}", style="grey62")
        t.append(f"  ·  {datetime.now():%H:%M:%S}", style="grey62")
        return t

    def cpu_panel(self):
        try:
            freq = psutil.cpu_freq()
        except Exception:
            freq = None
        detail = f"{freq.current / 1000:.2f} GHz" if freq else ""
        temps = _compat.temperatures()
        for e in temps.get("coretemp", []):
            if e.label.startswith("Package"):
                detail += f"  {e.current:.0f}°C"
        fans = _compat.fans()
        for entries in fans.values():
            for f in entries:
                if f.current:
                    detail += f"  {f.current}rpm"
                    break

        rows = [meter_row("Total", self.cpu_total, detail), Text()]
        cores = Table.grid(padding=(0, 2))
        cols = 2 if len(self.per_core) > 4 else 1
        for _ in range(cols):
            cores.add_column()
        cells = [meter_row(f"#{i}", pct, width=14) for i, pct in enumerate(self.per_core)]
        for i in range(0, len(cells), cols):
            cores.add_row(*cells[i:i + cols], *[""] * (cols - len(cells[i:i + cols])))
        rows += [cores, Text(), Text("history ", style="grey50") + sparkline(self.cpu_hist)]
        return Panel(Group(*rows), title=f"[bold]CPU[/] · {psutil.cpu_count()} threads",
                     border_style="cyan", title_align="left")

    def mem_panel(self):
        vm, sw = self.vm, self.swap
        rows = [
            meter_row("RAM", vm.percent, f"{fmt_bytes(vm.used)} / {fmt_bytes(vm.total)}"),
            meter_row("Swap", sw.percent if sw.total else None,
                      f"{fmt_bytes(sw.used)} / {fmt_bytes(sw.total)}" if sw.total else "none"),
            Text(),
        ]
        g = Table.grid(padding=(0, 3))
        g.add_column(style="grey62")
        g.add_column(justify="right")
        g.add_row("available", fmt_bytes(vm.available))
        if hasattr(vm, "cached"):  # Linux/BSD only
            g.add_row("cached / buffers", fmt_bytes(getattr(vm, "cached", 0) + getattr(vm, "buffers", 0)))
            g.add_row("shared", fmt_bytes(getattr(vm, "shared", 0)))
        else:
            g.add_row("free", fmt_bytes(vm.free))
        rows += [g, Text(), Text("history ", style="grey50") + sparkline(self.ram_hist)]
        return Panel(Group(*rows), title="[bold]Memory[/]", border_style="magenta",
                     title_align="left")

    def gpu_panel(self):
        if not self.gpus:
            return Panel(Text("No supported GPU detected.", style="grey50"),
                         title="[bold]GPU[/]", border_style="green", title_align="left")
        blocks = []
        for gpu, d, hist in zip(self.gpus, self.gpu_data, self.gpu_hist):
            extra = []
            if d.get("freq"):
                extra.append(f"{d['freq']:.0f}" + (f"/{d['freq_max']:.0f}" if d.get("freq_max") else "") + " MHz")
            if d.get("temp") is not None:
                extra.append(f"{d['temp']:.0f}°C")
            if d.get("power") is not None:
                extra.append(f"{d['power']:.1f} W")
            blocks.append(Text(gpu.name, style="bold") + Text("   " + "  ".join(extra), style="grey62"))
            blocks.append(meter_row("Load", d.get("util")))
            if d.get("freq") and d.get("freq_max"):
                blocks.append(meter_row("Freq", d["freq"] / d["freq_max"] * 100))
            if d.get("engines"):
                eng = Table.grid(padding=(0, 2))
                eng.add_column()
                eng.add_column()
                cells = [meter_row(n, v, width=10) for n, v in d["engines"].items()]
                for i in range(0, len(cells), 2):
                    eng.add_row(*cells[i:i + 2])
                blocks.append(eng)
            if d.get("mem_total"):
                pct = d["mem_used"] / d["mem_total"] * 100
                blocks.append(meter_row("VRAM", pct, f"{fmt_bytes(d['mem_used'])} / {fmt_bytes(d['mem_total'])}"))
            elif d.get("mem_used"):
                blocks.append(Text(f"{'Mem':<6}", style="bold") +
                                  Text(f"{fmt_bytes(d['mem_used'])} in use (shared system RAM)", style="grey62"))
            if d.get("no_clients"):
                blocks.append(Text("load n/a: no readable GPU clients in /proc/*/fdinfo; "
                                   "see Freq", style="grey50 italic"))
            blocks.append(Text("history ", style="grey50") + sparkline(hist))
            blocks.append(Text())
        return Panel(Group(*blocks[:-1]), title="[bold]GPU[/]", border_style="green",
                     title_align="left")

    def proc_panel(self):
        t = Table(expand=True, box=None, header_style="bold grey70", pad_edge=False)
        t.add_column("PID", justify="right", style="grey62", width=7)
        t.add_column("Name", ratio=3, no_wrap=True)
        t.add_column("User", ratio=1, style="grey62", no_wrap=True)
        t.add_column("CPU%", justify="right", width=6)
        t.add_column("RSS", justify="right", width=8)
        ncpu = psutil.cpu_count() or 1
        for p in self.procs:
            cpu = (p["cpu_percent"] or 0) / ncpu
            t.add_row(str(p["pid"]), p["name"] or "?", _compat.short_user(p["username"]) or "?",
                      Text(f"{cpu:.1f}", style=level_color(cpu * 2)),
                      fmt_bytes(p["memory_info"].rss))
        return Panel(t, title="[bold]Top processes[/]", border_style="blue", title_align="left")

    def top_height(self):
        cols = 2 if len(self.per_core) > 4 else 1
        cpu_rows = 5 + -(-len(self.per_core) // cols)
        return max(cpu_rows, 9) + 2  # + borders

    def render(self):
        layout = Layout()
        layout.split_column(
            Layout(self.header(), name="header", size=1),
            Layout(name="top", size=self.top_height()),
            Layout(name="bottom"),
        )
        layout["top"].split_row(Layout(self.cpu_panel()), Layout(self.mem_panel()))
        layout["bottom"].split_row(Layout(self.gpu_panel()), Layout(self.proc_panel()))
        return layout


def main():
    ap = argparse.ArgumentParser(description="Live CPU / RAM / GPU dashboard")
    ap.add_argument("-i", "--interval", type=float, default=1.0, help="refresh interval in seconds")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    args = ap.parse_args()

    mon = Monitor()
    console = Console()
    if args.once:
        time.sleep(min(args.interval, 1.0))
        mon.update()
        console.print(mon.render(), height=console.height or 40)
        return

    time.sleep(0.2)
    mon.update()
    try:
        with Live(mon.render(), console=console, screen=True, auto_refresh=False) as live:
            while True:
                time.sleep(args.interval)
                mon.update()
                live.update(mon.render(), refresh=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
