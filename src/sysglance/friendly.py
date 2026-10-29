#!/usr/bin/env python3
"""sysglance (friendly edition) — a friendly, at-a-glance view of how your computer is doing.

Usage:
    sysglance              # live view (press Ctrl+C to close)
    sysglance -i 2         # update every 2 seconds
    sysglance --once       # show a single snapshot and exit

See also: sysglance-dev (developer edition) and sysglance-classic (v1.0).
"""

import argparse
import glob
import os
import shutil
import subprocess
import time
from collections import defaultdict, deque
from datetime import datetime

import psutil
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from sysglance import __version__ as VERSION
from sysglance import _compat
HISTORY = 40


def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


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
        gpus = _compat.WindowsGPU.detect(
            {"3D": "3D", "VideoDecode": "Video", "VideoEncode": "Video",
             "VideoProcessing": "VidEnh", "Copy": "Blit"})
    return gpus



# ── look & feel ──────────────────────────────────────────────────────────────

GREEN, YELLOW, RED = (134, 239, 172), (252, 211, 77), (248, 113, 113)
GREEN_HEX = "#86efac"
DIM = "#4b5563"
SOFT = "#9ca3af"
ACCENT = {"cpu": "#7dd3fc", "mem": "#c4b5fd", "gpu": "#86efac", "apps": "#fda4af", "side": "#fcd34d"}

MOODS = [
    (25, "Relaxed", "#86efac"),
    (50, "Comfortable", "#bef264"),
    (75, "Busy", "#fcd34d"),
    (90, "Working hard", "#fb923c"),
    (101, "Maxed out", "#f87171"),
]

BIG = {
    "0": ["┏━┓", "┃ ┃", "┗━┛"], "1": ["╺┓ ", " ┃ ", "╺┻╸"], "2": ["╺━┓", "┏━┛", "┗━╸"],
    "3": ["╺━┓", " ━┫", "╺━┛"], "4": ["╻ ╻", "┗━┫", "  ╹"], "5": ["┏━╸", "┗━┓", "╺━┛"],
    "6": ["┏━╸", "┣━┓", "┗━┛"], "7": ["╺━┓", "  ┃", "  ╹"], "8": ["┏━┓", "┣━┫", "┗━┛"],
    "9": ["┏━┓", "┗━┫", "╺━┛"],
}

FRIENDLY_NAMES = {
    "brave": "Brave", "chrome": "Chrome", "chromium": "Chromium", "firefox": "Firefox",
    "code": "VS Code", "gnome-shell": "Desktop", "Xorg": "Display", "Xwayland": "Display",
    "ptyxis": "Terminal", "ptyxis-agent": "Terminal", "gnome-terminal-server": "Terminal",
    "claude": "Claude Code", "python3": "Python", "python": "Python", "node": "Node.js",
    "MATLAB": "MATLAB", "MATLABWindow": "MATLAB", "MathWorksServiceHost": "MATLAB",
    "nautilus": "Files", "pipewire": "Sound", "pipewire-pulse": "Sound", "wireplumber": "Sound",
    "tmux: server": "tmux", "spotify": "Spotify", "slack": "Slack", "discord": "Discord",
    "Discord": "Discord", "thunderbird": "Thunderbird", "evolution": "Mail", "ibus-daemon": "Keyboard input",
}


def mood(pct):
    if pct is None:
        return "Unknown", SOFT
    for limit, word, color in MOODS:
        if pct < limit:
            return word, color
    return MOODS[-1][1:]


def blend(pos):
    """Colour along green → yellow → red for pos in [0, 1]."""
    a, b, t = (GREEN, YELLOW, pos * 2) if pos < 0.5 else (YELLOW, RED, (pos - 0.5) * 2)
    r, g, bl = (int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return f"#{r:02x}{g:02x}{bl:02x}"


def gradient_bar(pct, width):
    t = Text()
    filled = round((pct or 0) / 100 * width)
    for i in range(width):
        t.append("━", style=blend(i / max(1, width - 1)) if i < filled else DIM)
    return t


def solid_bar(pct, width, color):
    filled = round(max(0, min(100, pct)) / 100 * width)
    return Text("━" * filled, style=color) + Text("━" * (width - filled), style=DIM)


def big_number(pct, color, label):
    digits = str(round(pct)) if pct is not None else None
    rows = [Text("  ") for _ in range(3)]
    for r in range(3):
        rows[r].append("".join(BIG[d][r] for d in digits) if digits else ["   ", " ? ", "   "][r],
                       style=f"bold {color}")
    rows[2].append(" %", style=color)
    rows[1].append(f"   {label}", style=f"bold {color}")
    return rows


def sparkline(values, color):
    blocks = "▁▂▃▄▅▆▇█"
    t = Text()
    for v in values:
        t.append(blocks[min(7, int(max(0, v) / 100 * 8))] if v is not None else " ", style=color)
    return t


def size(n):
    g = n / 2**30
    return f"{g:.1f} GB" if g >= 1 else f"{n / 2**20:.0f} MB"


def rate(bps):
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1000 or unit == "GB/s":
            return f"{bps:.0f} {unit}" if unit == "B/s" else f"{bps:.1f} {unit}"
        bps /= 1000


def friendly_duration(seconds):
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d:
        parts.append(f"{d} day{'s' * (d != 1)}")
    if h:
        parts.append(f"{h} hour{'s' * (h != 1)}")
    if not d and m:
        parts.append(f"{m} minute{'s' * (m != 1)}")
    return ", ".join(parts[:2]) or "a moment"


FRIENDLY_PREFIXES = [
    ("matlab", "MATLAB"), ("mathworks", "MATLAB"), ("node", "Node.js"), ("npm", "Node.js"),
    ("localsearch", "File search"), ("tracker", "File search"), ("mutter", "Desktop"),
    ("gjs", "Desktop"), ("gnome-", "Desktop"), ("gsd-", "Desktop"), ("xdg-", "Desktop"),
    ("evolution", "Mail & calendar"), ("ibus", "Keyboard input"), ("ipp", "Printing"),
    ("cups", "Printing"), ("pulse", "Sound"), ("systemd", "System"), ("dbus", "System"),
    ("snap", "Software updates"), ("packagekit", "Software updates"), ("fwupd", "Software updates"),
    ("chrome_crashpad", "Chrome"), ("web content", "Firefox"), ("isolated web", "Firefox"),
]


WIN_NAMES = {
    "msedge": "Edge", "msedgewebview2": "Edge", "chrome": "Chrome", "firefox": "Firefox",
    "explorer": "File Explorer", "code": "VS Code",
    "svchost": "System", "system": "System", "registry": "System", "smss": "System",
    "csrss": "System", "wininit": "System", "services": "System", "lsass": "System",
    "winlogon": "System", "fontdrvhost": "System", "memory compression": "System",
    "dwm": "Desktop", "windowsterminal": "Terminal", "conhost": "Terminal",
    "openconsole": "Terminal", "msmpeng": "Antivirus",
}


def app_name(proc_name, user):
    if _compat.IS_WIN:
        if proc_name.lower().endswith(".exe"):
            proc_name = proc_name[:-4]
        friendly = WIN_NAMES.get(proc_name.lower())
        if friendly:
            return friendly
    if proc_name in FRIENDLY_NAMES:
        return FRIENDLY_NAMES[proc_name]
    low = proc_name.lower()
    for prefix, friendly in FRIENDLY_PREFIXES:
        if low.startswith(prefix):
            return friendly
    if _compat.is_system_user(user):
        return "System"
    base = proc_name.split(":")[0].split("/")[0].removesuffix("-bin").rstrip("-0123456789")
    return base[:1].upper() + base[1:]


# ── dashboard ────────────────────────────────────────────────────────────────

class Dashboard:
    def __init__(self, interval):
        self.interval = interval
        self.gpu = (detect_gpus() or [None])[0]
        self.hist = {k: deque(maxlen=HISTORY) for k in ("cpu", "mem", "gpu")}
        self.ncpu = psutil.cpu_count() or 1
        psutil.cpu_percent()
        list(psutil.process_iter(["cpu_percent"]))
        if self.gpu:
            self.gpu.sample()
        self.net_prev = (time.monotonic(), psutil.net_io_counters())

    def update(self):
        self.cpu = psutil.cpu_percent()
        self.vm = psutil.virtual_memory()
        self.swap = psutil.swap_memory()
        self.gpu_data = self.gpu.sample() if self.gpu else {}
        self.gpu_pct, self.gpu_estimated = self.gpu_data.get("util"), False
        freq, fmax = self.gpu_data.get("freq"), self.gpu_data.get("freq_max")
        if self.gpu_pct is None and freq and fmax:
            # No usage counters available: estimate from how far above idle clock it runs.
            fmin = float(read(f"{getattr(self.gpu, 'sys', '')}/gt_min_freq_mhz", 0) or 0)
            self.gpu_pct = max(0.0, (freq - fmin) / max(1, fmax - fmin) * 100)
            self.gpu_estimated = True
        for k, v in (("cpu", self.cpu), ("mem", self.vm.percent), ("gpu", self.gpu_pct)):
            self.hist[k].append(v)

        temps = _compat.temperatures()
        self.temp = next((e.current for e in temps.get("coretemp", []) if e.label.startswith("Package")),
                         next((e.current for es in temps.values() for e in es), None))
        fans = _compat.fans()
        self.fan = next((f.current for fs in fans.values() for f in fs if f.current), None)
        self.battery = _compat.battery()
        self.disk = psutil.disk_usage(_compat.root_path())

        now, net = time.monotonic(), psutil.net_io_counters()
        dt = max(1e-3, now - self.net_prev[0])
        self.down = (net.bytes_recv - self.net_prev[1].bytes_recv) / dt
        self.up = (net.bytes_sent - self.net_prev[1].bytes_sent) / dt
        self.net_prev = (now, net)

        # Memory per app = private memory of every process + the largest shared chunk,
        # so 40 browser tabs sharing the same libraries aren't counted 40 times.
        apps = defaultdict(lambda: {"cpu": 0.0, "mem": 0, "shared": 0, "count": 0})
        for p in psutil.process_iter(["name", "username", "cpu_percent", "memory_info"]):
            mi = p.info["memory_info"]
            if not mi or not mi.rss or not p.info["name"] or (_compat.IS_WIN and p.pid == 0):
                continue
            a = apps[app_name(p.info["name"], p.info["username"])]
            shared = getattr(mi, "shared", 0)
            a["cpu"] += (p.info["cpu_percent"] or 0) / self.ncpu
            a["mem"] += mi.rss - shared
            a["shared"] = max(a["shared"], shared)
            a["count"] += 1
        for a in apps.values():
            a["mem"] += a["shared"]
        total = self.vm.total
        self.apps = sorted(apps.items(), key=lambda kv: kv[1]["cpu"] + kv[1]["mem"] / total * 100,
                           reverse=True)

    # ── text helpers ──

    def temp_words(self):
        if self.temp is None:
            return None
        t = self.temp
        word, color = (("Cool", GREEN_HEX) if t < 60 else ("Warm", "#fcd34d") if t < 75
                       else ("Hot", "#fb923c") if t < 90 else ("Very hot", "#f87171"))
        return Text(word, style=color) + Text(f" {t:.0f}°C", style=SOFT)

    def status(self):
        """One plain-language sentence about overall health."""
        if self.vm.percent > 90 or (self.swap.percent > 80 and self.vm.percent > 70):
            return "Memory is running low", "#fb923c"
        if self.temp and self.temp > 90:
            return "Your computer is running hot", "#f87171"
        if self.cpu > 85:
            return "Your processor is working hard", "#fcd34d"
        if self.battery and not self.battery.power_plugged and self.battery.percent < 15:
            return "Battery is almost empty", "#f87171"
        return "Everything looks good", GREEN_HEX

    def tips(self):
        tips = []
        top_mem = max(self.apps, key=lambda kv: kv[1]["mem"], default=None)
        if self.swap.total and self.swap.percent > 75 and self.vm.percent > 55:
            tip = "Memory is tight, so your computer is borrowing space from the disk. That can make things feel slow."
            if top_mem:
                tip += f" Closing {top_mem[0]} would free about {size(top_mem[1]['mem'])}."
            tips.append(tip)
        elif self.vm.percent > 85:
            tips.append(f"Memory is almost full. {top_mem[0]} is using the most ({size(top_mem[1]['mem'])})."
                        if top_mem else "Memory is almost full.")
        if self.temp and self.temp > 85:
            tips.append("It's running hot. Make sure the air vents aren't blocked.")
        if self.disk.percent > 90:
            tips.append(f"Your disk is nearly full: only {size(self.disk.free)} left.")
        if self.battery and not self.battery.power_plugged and self.battery.percent < 20:
            tips.append("Battery is low. Plug in soon.")
        return tips or ["All good. Nothing needs your attention."]

    # ── panels ──

    def header(self, width):
        msg, color = self.status()
        g = Table.grid(expand=True)
        g.add_column()
        g.add_column(justify="right")
        left = Text(" sysglance ", style="bold #111827 on #7dd3fc")
        left.append("  ●  ", style=color)
        left.append(msg, style=f"bold {color}")
        g.add_row(left, Text(f"{datetime.now():%A %H:%M}", style=SOFT))
        return g

    def card(self, key, title, pct, lines, width):
        word, color = mood(pct)
        inner = max(10, width - 6)
        body = [*big_number(pct, color, word), Text(),
                Text("  ") + gradient_bar(pct, inner), Text()]
        for ln in lines:
            ln = Text("  ") + ln
            ln.no_wrap, ln.overflow = True, "ellipsis"
            body.append(ln)
        secs = len(self.hist[key]) * self.interval
        body += [Text(),
                 Text("  ") + sparkline(list(self.hist[key])[-inner + 12:], ACCENT[key]) +
                 Text(f"  last {secs:.0f}s", style=DIM)]
        return Panel(Group(*body), title=f"[bold {ACCENT[key]}]{title}[/]", title_align="left",
                     border_style=ACCENT[key], padding=(1, 1, 0, 1))

    def cpu_card(self, width):
        lines = []
        tw = self.temp_words()
        lines.append(Text(f"{self.ncpu} cores", style=SOFT) + (Text("  ·  ", style=DIM) + tw if tw else Text()))
        if self.fan:
            word = "quiet" if self.fan < 2500 else "spinning" if self.fan < 4000 else "loud"
            lines.append(Text(f"Fan is {word}", style=SOFT))
        else:
            lines.append(Text())
        return self.card("cpu", "Processor", self.cpu, lines, width)

    def mem_card(self, width):
        vm = self.vm
        lines = [Text(f"{size(vm.available)}", style="bold") + Text(f" free of {size(vm.total)}", style=SOFT)]
        if self.swap.total and self.swap.percent > 50:
            lines.append(Text(f"Using disk as backup ({self.swap.percent:.0f}%)", style="#fb923c"))
        else:
            lines.append(Text("Plenty of room" if vm.percent < 70 else "Getting full", style=SOFT))
        return self.card("mem", "Memory", vm.percent, lines, width)

    def gpu_card(self, width):
        if not self.gpu:
            return self.card("gpu", "Graphics", None, [Text("No graphics card found", style=SOFT), Text()], width)
        name = self.gpu.name.replace("integrated GPU", "graphics")
        d = self.gpu_data
        lines = [Text(name, style=SOFT)]
        video = (d.get("engines") or {}).get("Video")
        if video and video > 1:
            lines.append(Text(f"Playing video ({video:.0f}%)", style=SOFT))
        elif d.get("mem_total"):
            lines.append(Text(f"{size(d['mem_total'] - d['mem_used'])} free of {size(d['mem_total'])}", style=SOFT))
        elif self.gpu_estimated:
            lines.append(Text("Estimated from speed", style=DIM))
        else:
            lines.append(Text())
        return self.card("gpu", "Graphics", self.gpu_pct, lines, width)

    def apps_panel(self, rows, width):
        t = Table(expand=True, box=None, pad_edge=False, header_style=f"bold {SOFT}", padding=(0, 1))
        bw = max(6, min(14, (width - 40) // 4))
        t.add_column("App", ratio=1, no_wrap=True)
        t.add_column("Processor", width=bw + 6, no_wrap=True)
        t.add_column("Memory", width=bw + 9, no_wrap=True)
        for name, a in self.apps[:max(1, rows)]:
            label = Text(name, style="bold")
            if a["count"] > 1:
                label.append(f"  ×{a['count']}", style=DIM)
            cpu_word, cpu_color = mood(a["cpu"] * 2)
            mem_pct = a["mem"] / self.vm.total * 100
            _, mem_color = mood(mem_pct * 4)
            t.add_row(label,
                      solid_bar(a["cpu"] * 2, bw, cpu_color) + Text(f" {a['cpu']:3.0f}%", style=cpu_color),
                      solid_bar(mem_pct * 4, bw, mem_color) + Text(f" {size(a['mem']):>7}", style=mem_color))
        return Panel(t, title=f"[bold {ACCENT['apps']}]What's using your computer[/]", title_align="left",
                     border_style=ACCENT["apps"], padding=(0, 1))

    def side_panel(self, width):
        bw = max(8, width - 32)
        g = Table.grid(padding=(0, 2))
        g.add_column(style=f"bold {SOFT}", no_wrap=True)
        g.add_column()
        b = self.battery
        if b:
            color = GREEN_HEX if b.percent > 40 else "#fcd34d" if b.percent > 15 else "#f87171"
            if b.power_plugged:
                state = "full" if b.percent > 98 else "charging"
                state = f"Plugged in, {state}"
            elif b.secsleft and b.secsleft > 0:
                state = f"{friendly_duration(b.secsleft)} left"
            else:
                state = "On battery"
            g.add_row("Battery", solid_bar(b.percent, bw, color) + Text(f" {b.percent:.0f}%", style=color))
            g.add_row("", Text(state, style=SOFT))
        free_pct = 100 - self.disk.percent
        g.add_row("Disk", solid_bar(self.disk.percent, bw, "#7dd3fc") + Text(f" {self.disk.percent:.0f}%", style="#7dd3fc"))
        g.add_row("", Text(f"{size(self.disk.free)} free of {size(self.disk.total)}", style=SOFT if free_pct > 10 else "#f87171"))
        g.add_row("Internet", Text("↓ ", style=GREEN_HEX) + Text(rate(self.down)) +
                  Text("   ↑ ", style="#c4b5fd") + Text(rate(self.up)))
        g.add_row("Turned on", Text(f"{friendly_duration(time.time() - psutil.boot_time())} ago", style=SOFT))

        tips = [Text("✦ ", style=ACCENT["side"]) + Text(tip, style="#e5e7eb") for tip in self.tips()]
        return Panel(Group(g, Text(), *tips), title=f"[bold {ACCENT['side']}]At a glance[/]",
                     title_align="left", border_style=ACCENT["side"], padding=(0, 1))

    def footer(self):
        every = "second" if self.interval == 1 else f"{self.interval:g} seconds"
        return Text(f" Updates every {every}  ·  press Ctrl+C to close  ·  v{VERSION}", style=DIM)

    def render(self, width, height):
        card_h = 13
        layout = Layout()
        layout.split_column(
            Layout(self.header(width), size=1),
            Layout(name="cards", size=card_h),
            Layout(name="bottom"),
            Layout(self.footer(), size=1),
        )
        cw = width // 3
        layout["cards"].split_row(Layout(self.cpu_card(cw)), Layout(self.mem_card(cw)),
                                  Layout(self.gpu_card(cw)))
        app_rows = height - 2 - card_h - 3
        aw = width * 3 // 5
        layout["bottom"].split_row(Layout(self.apps_panel(app_rows, aw), ratio=3),
                                   Layout(self.side_panel(width - aw), ratio=2))
        return layout



def main():
    ap = argparse.ArgumentParser(description="A friendly view of how your computer is doing")
    ap.add_argument("-i", "--interval", type=float, default=1.0, help="seconds between updates")
    ap.add_argument("--once", action="store_true", help="show one snapshot and exit")
    args = ap.parse_args()

    dash = Dashboard(args.interval)
    console = Console()
    if args.once:
        time.sleep(min(args.interval, 1.0))
        dash.update()
        console.print(dash.render(console.width, console.height), height=console.height)
        return

    time.sleep(0.3)
    dash.update()
    try:
        with Live(dash.render(console.width, console.height), console=console,
                  screen=True, auto_refresh=False) as live:
            while True:
                time.sleep(args.interval)
                dash.update()
                live.update(dash.render(console.width, console.height), refresh=True)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
