#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""librestatistics dev — developer-oriented live system monitor (TUI).

Usage:
    librestatistics-dev                 # live dashboard
    librestatistics-dev -i 0.5          # refresh every 0.5 s
    librestatistics-dev --once          # print a single snapshot and exit
    librestatistics-dev --json          # emit one JSON sample and exit
    librestatistics-dev --log FILE            # append NDJSON samples while running

Keys:  c/m/p/i/t sort by cpu/mem/pid/io/threads   r reverse   / filter (Enter ok, Esc clear)
       T tree toggle   j/k or arrows scroll   space pause   +/- interval   q quit

GPU backends: NVIDIA (nvidia-smi), AMD (amdgpu sysfs), Intel (DRM fdinfo + i915 sysfs).
Everything degrades gracefully when /proc, /sys or sensors are unreadable.
"""

import argparse
import glob
import json
import os
import platform
import re
import select
import shutil
import signal
import subprocess
import sys
import time
from collections import deque, Counter

import psutil
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from librestatistics import _compat
from librestatistics._compat import IS_WIN

try:  # Windows console input (imported at module level so freezers see it)
    import msvcrt
except ImportError:
    msvcrt = None

HISTORY = 120
SPARK = "▁▂▃▄▅▆▇█"
NCPU = psutil.cpu_count() or 1

# palette
C_LABEL = "grey62"
C_DIM = "grey35"
C_VAL = "bright_white"
B_CPU, B_MEM, B_DSK, B_NET, B_GPU, B_PRC = "cyan", "magenta", "dodger_blue2", "yellow", "green", "orange3"
SEG_COL = {"u": "green", "n": "blue", "s": "red", "w": "yellow", "q": "magenta", "t": "cyan"}


# ── helpers ──────────────────────────────────────────────────────────────────

def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def num(path):
    v = read(path)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fb(n, rate=False):
    """Compact bytes (or bytes/s) formatting, ~6 chars."""
    if n is None:
        return "   -"
    for unit in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024 or unit == "T":
            s = f"{n:.0f}{unit}" if unit == "B" else (f"{n:.1f}{unit}" if abs(n) < 100 else f"{n:.0f}{unit}")
            return s + ("/s" if rate else "")
        n /= 1024


def fcount(n):
    if n is None:
        return "-"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}k"
    return f"{n:.0f}"


def lvl(pct):
    if pct is None:
        return C_DIM
    return "green" if pct < 50 else "yellow" if pct < 80 else "red"


def T(*parts):
    """Non-wrapping Text from (text, style) parts."""
    t = Text(no_wrap=True, overflow="crop")
    for p in parts:
        if isinstance(p, str):
            t.append(p)
        elif isinstance(p, Text):
            t.append_text(p)
        else:
            t.append(*p)
    return t


def bar(pct, width, color=None):
    if pct is None:
        return T(("─" * width, C_DIM))
    pct = max(0.0, min(100.0, pct))
    filled = pct / 100 * width
    full = int(filled)
    part = int((filled - full) * 8)
    s = "█" * full + ("▏▎▍▌▋▊▉"[part - 1] if part else "")
    return T((s, color or lvl(pct)), ("·" * (width - len(s)), "grey23"))


def stacked(segs, width):
    """segs: list of (pct, style). Cumulative rounding so the bar never overflows."""
    t = T()
    used = 0
    acc = 0.0
    for pct, style in segs:
        acc += max(0.0, pct)
        end = min(width, int(round(acc / 100 * width)))
        if end > used:
            t.append("█" * (end - used), style=style)
            used = end
    t.append("·" * (width - used), style="grey23")
    return t


def spark(values, width, maxv=100.0):
    vals = [v for v in list(values)[-width:]]
    if maxv is None:
        maxv = max([v for v in vals if v is not None] + [1e-9])
    t = T()
    t.append(" " * (width - len(vals)))
    for v in vals:
        if v is None:
            t.append(" ")
        else:
            frac = max(0.0, min(1.0, v / maxv)) if maxv else 0
            t.append(SPARK[min(7, int(frac * 8))], style=lvl(frac * 100))
    return t


def trunc(s, n):
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


# ── GPU backends (from classic.py, Intel extended) ───────────────────────

class GPU:
    name = "GPU"

    def sample(self):
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
            if "," not in line:
                continue
            idx, name = [x.strip() for x in line.split(",", 1)]
            gpus.append(NvidiaGPU(idx, name))
        return gpus

    def sample(self):
        q = "utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,clocks.gr,clocks.max.gr"
        try:
            out = subprocess.run(
                ["nvidia-smi", "-i", self.index, f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return {}
        vals = [v.strip() for v in out.split(",")]

        def n(v):
            try:
                return float(v)
            except ValueError:
                return None

        u, mu, mt, temp, pw, f, fm = (n(v) for v in vals + [""] * (7 - len(vals)))
        return {
            "util": u, "mem_used": mu * 2**20 if mu is not None else None,
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
        temp = power = None
        for h in glob.glob(f"{self.dev}/hwmon/hwmon*/temp1_input"):
            t = num(h)
            if t is not None:
                temp = t / 1000
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
    """Intel iGPU: per-client DRM fdinfo engine busy-time counters + i915 sysfs.

    Only processes readable by the current user are visible (no root needed).
    """

    ENGINES = {"render": "3D", "video": "Vid", "video-enhance": "VEnh", "copy": "Blt"}

    def __init__(self, card):
        self.card = card
        self.sys = f"/sys/class/drm/{card}"
        self.name = f"Intel iGPU {card}"
        self._prev = None      # (wall_ns, {(pid, cid): {engine: ns}})
        self._prev_rc6 = None  # (wall_s, ms)
        self._pidname = {}
        self._tick, self._drm_pids = 0, set()

    @staticmethod
    def detect():
        return [IntelGPU(os.path.basename(c)) for c in sorted(glob.glob("/sys/class/drm/card[0-9]"))
                if read(f"{c}/device/vendor") == "0x8086" and os.path.exists(f"{c}/gt_cur_freq_mhz")]

    def _read_clients(self):
        clients, mem = {}, 0
        self._tick += 1
        if self._tick % 10 == 1:
            paths = glob.glob("/proc/[0-9]*/fdinfo/*")   # full discovery scan
            found = set()
        else:
            paths = [p for pid in self._drm_pids for p in glob.glob(f"/proc/{pid}/fdinfo/*")]
            found = None
        for fdinfo in paths:
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
            pid = int(fdinfo.split("/")[2])
            if found is not None:
                found.add(pid)
            if cid is None or (pid, cid) in clients:
                continue
            clients[(pid, cid)] = {
                k[len("drm-engine-"):]: int(v.split()[0])
                for k, v in fields.items()
                if k.startswith("drm-engine-") and not k.startswith("drm-engine-capacity")
            }
            for k, v in fields.items():
                if k.startswith("drm-resident-"):
                    n, *unit = v.split()
                    mult = {"KiB": 1024, "MiB": 2**20, "GiB": 2**30}.get(unit[0] if unit else "", 1)
                    mem += int(n) * mult
        if found is not None:
            self._drm_pids = found
        return clients, mem

    def _name(self, pid):
        if pid not in self._pidname:
            self._pidname[pid] = read(f"/proc/{pid}/comm") or "?"
        return self._pidname[pid]

    def sample(self):
        now = time.monotonic_ns()
        clients, mem = self._read_clients()
        engines = None
        per_client = []
        if self._prev and clients:
            prev_t, prev = self._prev
            dt = max(1, now - prev_t)
            names = set(self.ENGINES)
            for eng in clients.values():
                names |= set(eng)
            engines = {e: 0.0 for e in names}
            agg = {}
            for key, eng in clients.items():
                if key not in prev:
                    continue
                for e, ns in eng.items():
                    d = max(0, ns - prev[key].get(e, 0))
                    engines[e] += d
                    a = agg.setdefault(key[0], {})
                    a[e] = a.get(e, 0) + d
            engines = {self.ENGINES.get(e, e): min(100.0, v / dt * 100) for e, v in engines.items()}
            for pid, a in agg.items():
                pcts = {self.ENGINES.get(e, e): v / dt * 100 for e, v in a.items()}
                tot = max(pcts.values()) if pcts else 0
                per_client.append({"pid": pid, "name": self._name(pid), "busy": tot,
                                   "engines": {k: round(v, 2) for k, v in pcts.items() if v > 0}})
            per_client.sort(key=lambda c: -c["busy"])
        self._prev = (now, clients)

        wall = time.monotonic()
        rc6_ms = num(f"{self.sys}/power/rc6_residency_ms")
        rc6 = None
        if rc6_ms is not None and self._prev_rc6:
            dw = (wall - self._prev_rc6[0]) * 1000
            if dw > 0:
                rc6 = max(0.0, min(100.0, (rc6_ms - self._prev_rc6[1]) / dw * 100))
        if rc6_ms is not None:
            self._prev_rc6 = (wall, rc6_ms)

        temp = None
        try:
            for entry in _compat.temperatures().get("coretemp", []):
                if entry.label.startswith("Package"):
                    temp = entry.current
        except Exception:
            pass
        g = lambda n: num(f"{self.sys}/gt_{n}_freq_mhz")
        return {
            "util": max(engines.values()) if engines else None,
            "engines": engines,
            "mem_used": mem or None, "mem_total": None, "temp": temp,
            "freq": g("act") or g("cur"), "freq_act": g("act"), "freq_cur": g("cur"),
            "freq_min": g("min"), "freq_max": g("max"), "freq_boost": g("boost"),
            "freq_rp0": g("RP0"), "freq_rpn": g("RPn"),
            "rc6_pct": rc6, "rc6_ms": rc6_ms,
            "clients": per_client, "no_clients": not clients,
            "runtime_status": read(f"{self.sys}/power/runtime_status"),
        }


def detect_gpus():
    gpus = NvidiaGPU.detect() + AmdGPU.detect() + IntelGPU.detect()
    if not gpus and IS_WIN:
        gpus = _compat.WindowsGPU.detect(
            {"3D": "3D", "VideoDecode": "Vid", "VideoEncode": "Vid",
             "VideoProcessing": "VEnh", "Copy": "Blt"})
    return gpus


# ── sampling ─────────────────────────────────────────────────────────────────

def parse_kv_file(path, sep=None):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                parts = line.replace(":", " ").split() if sep == ":" else line.split()
                if len(parts) >= 2:
                    try:
                        out[parts[0]] = int(parts[1])
                    except ValueError:
                        pass
    except OSError:
        pass
    return out


def read_stat():
    d = {"cpu": {}}
    try:
        with open("/proc/stat") as f:
            for line in f:
                p = line.split()
                if p[0] in ("ctxt", "processes", "procs_running", "procs_blocked"):
                    d[p[0]] = int(p[1])
                elif p[0] in ("intr", "softirq"):
                    d[p[0]] = int(p[1])
    except (OSError, ValueError, IndexError):
        pass
    return d


def read_psi():
    out = {}
    for res in ("cpu", "memory", "io"):
        txt = read(f"/proc/pressure/{res}")
        if txt is None:
            continue
        r = {}
        for line in txt.splitlines():
            kind, *kvs = line.split()
            r[kind] = {k: float(v) for k, v in (x.split("=") for x in kvs) if k != "total"}
        out[res] = r
    return out


TCP_ST = {"01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1", "05": "FIN_WAIT2",
          "06": "TIME_WAIT", "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING"}


def tcp_states_psutil():
    """Windows: count TCP sockets via psutil (works without admin there)."""
    try:
        c = Counter(conn.status for conn in psutil.net_connections(kind="tcp"))
    except Exception:
        return None
    return dict(c)


def tcp_states():
    """Count TCP sockets by state straight from /proc/net/tcp{,6} (cheap, no root needed)."""
    c, ok = Counter(), False
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(f) as fh:
                next(fh)
                for line in fh:
                    c[TCP_ST.get(line.split()[3], "?")] += 1
            ok = True
        except (OSError, IndexError, StopIteration):
            pass
    return dict(c) if ok else None


REAL_FS = {"fat32", "fat", "refs", "ext2", "ext3", "ext4", "btrfs", "xfs", "vfat", "exfat", "ntfs", "ntfs3", "f2fs", "zfs", "fuseblk", "nfs", "nfs4"}


class Sampler:
    def __init__(self):
        self.gpus = detect_gpus()
        self.hist = {k: deque(maxlen=HISTORY) for k in ("cpu", "mem", "swap", "dio", "rx", "tx", "gpu")}
        self.prev = None
        self.prev_io = {}
        self.sample()  # prime counters (result discarded)
        self.prev_snap = None

    def sample(self):
        t0 = time.perf_counter()
        now = time.monotonic()
        prev = self.prev
        dt = (now - prev["t"]) if prev else None
        snap = {"ts": time.time(), "interval": dt}

        # header bits
        la = _compat.loadavg()
        lp = (read("/proc/loadavg") or "").split()
        if len(lp) > 3:
            run_tot = lp[3].split("/")
        else:
            run_tot = ["0", "0"] if not IS_WIN else None
        snap["sys"] = {
            "host": _compat.hostname(), "kernel": _compat.kernel(),
            "uptime_s": time.time() - psutil.boot_time(), "ncpu": NCPU,
            "load": list(la) if la else None, "load_norm": [x / NCPU for x in la] if la else None,
            "running": int(run_tot[0]) if run_tot else None, "total": int(run_tot[1]) if run_tot else None,
        }

        # CPU
        per = psutil.cpu_times_percent(percpu=True)
        tot = psutil.cpu_times_percent()

        def brk(c):
            g = lambda a: getattr(c, a, 0.0) or 0.0
            d = {"user": g("user"), "nice": g("nice"), "system": g("system"), "iowait": g("iowait"),
                 "irq": g("irq") + g("softirq") + g("interrupt") + g("dpc"), "steal": g("steal"), "idle": g("idle")}
            d["busy"] = max(0.0, 100.0 - d["idle"] - d["iowait"] * 0)  # iowait counts as busy-ish
            d["busy"] = max(0.0, 100.0 - d["idle"])
            return d

        st = read_stat()
        if IS_WIN and not st.get("ctxt"):
            try:
                cs = psutil.cpu_stats()
                st.update(ctxt=cs.ctx_switches, intr=cs.interrupts, softirq=cs.soft_interrupts)
            except Exception:
                pass
        rates = {}
        if prev and dt:
            for k in ("ctxt", "intr", "softirq", "processes"):
                if k in st and k in prev["stat"]:
                    rates[k] = (st[k] - prev["stat"][k]) / dt
        try:
            freqs = [f.current for f in psutil.cpu_freq(percpu=True)]
        except Exception:
            freqs = []
        if len(freqs) == 1 and NCPU > 1:   # Windows reports a single package-wide value
            freqs = freqs * NCPU
        temps = {}
        try:
            for name, ents in _compat.temperatures().items():
                temps[name] = [{"label": e.label or name, "c": e.current} for e in ents]
        except Exception:
            pass
        fans = {}
        try:
            for name, ents in _compat.fans().items():
                fans[name] = [e.current for e in ents]
        except Exception:
            pass
        snap["cpu"] = {
            "total": brk(tot), "percpu": [brk(c) for c in per], "freq_mhz": freqs,
            "ctx_s": rates.get("ctxt"), "intr_s": rates.get("intr"), "softirq_s": rates.get("softirq"),
            "fork_s": rates.get("processes"), "blocked": st.get("procs_blocked"),
            "temps": temps, "fans": fans,
        }
        snap["psi"] = read_psi()

        # memory
        mi = parse_kv_file("/proc/meminfo", ":")
        kb = lambda k: mi.get(k, 0) * 1024
        vm = parse_kv_file("/proc/vmstat")
        vr = {}
        if prev and dt:
            for k in ("pgfault", "pgmajfault", "pswpin", "pswpout"):
                if k in vm and k in prev["vm"]:
                    vr[k] = (vm[k] - prev["vm"][k]) / dt
        total = kb("MemTotal")
        avail = kb("MemAvailable")
        if not mi:  # no /proc/meminfo (Windows): basic numbers from psutil, Linux-only extras are None
            try:
                v = psutil.virtual_memory()
                sw = psutil.swap_memory()
                total, avail = v.total, v.available
                snap["mem"] = {
                    "total": total, "available": avail, "used": total - avail, "free": v.free,
                    "buffers": None, "cached": None, "shared": None, "active": None, "inactive": None,
                    "dirty": None, "writeback": None, "slab": None,
                    "swap_total": sw.total, "swap_used": sw.used,
                    "pct": (total - avail) / total * 100 if total else 0,
                    "swap_in_s": None, "swap_out_s": None, "minflt_s": None, "majflt_s": None,
                }
            except Exception:
                pass
        snap["mem"] = snap.get("mem") or {
            "total": total, "available": avail, "used": total - avail, "free": kb("MemFree"),
            "buffers": kb("Buffers"), "cached": kb("Cached"), "shared": kb("Shmem"),
            "active": kb("Active"), "inactive": kb("Inactive"), "dirty": kb("Dirty"),
            "writeback": kb("Writeback"), "slab": kb("Slab"),
            "swap_total": kb("SwapTotal"), "swap_used": kb("SwapTotal") - kb("SwapFree"),
            "pct": (total - avail) / total * 100 if total else 0,
            "swap_in_s": vr.get("pswpin"), "swap_out_s": vr.get("pswpout"),
            "minflt_s": (vr["pgfault"] - vr["pgmajfault"]) if "pgfault" in vr else None,
            "majflt_s": vr.get("pgmajfault"),
        }

        # disks
        disks = {}
        try:
            io = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            io = {}
        blocks = set(os.listdir("/sys/block")) if os.path.isdir("/sys/block") else set()
        for name, c in io.items():
            if (name not in blocks and not IS_WIN) or name.startswith(("loop", "ram", "zram", "dm-")):
                continue
            d = {"read_b": c.read_bytes, "write_b": c.write_bytes, "read_n": c.read_count,
                 "write_n": c.write_count, "busy_ms": getattr(c, "busy_time", None)}
            if prev and dt and name in prev["disk"]:
                p = prev["disk"][name]
                d.update(r_bps=(c.read_bytes - p["read_b"]) / dt, w_bps=(c.write_bytes - p["write_b"]) / dt,
                         r_iops=(c.read_count - p["read_n"]) / dt, w_iops=(c.write_count - p["write_n"]) / dt)
                if d["busy_ms"] is not None and p["busy_ms"] is not None:
                    d["util"] = max(0.0, min(100.0, (d["busy_ms"] - p["busy_ms"]) / (dt * 1000) * 100))
            disks[name] = d
        snap["disk"] = disks
        fs = []
        try:
            seen = set()
            for part in psutil.disk_partitions(all=False):
                if part.fstype.lower() not in REAL_FS or part.device in seen:
                    continue
                seen.add(part.device)
                try:
                    u = psutil.disk_usage(part.mountpoint)
                except OSError:
                    continue
                fs.append({"mount": part.mountpoint, "dev": part.device, "fstype": part.fstype,
                           "total": u.total, "used": u.used, "pct": u.percent})
        except Exception:
            pass
        snap["fs"] = fs

        # network
        nets = {}
        try:
            nio = psutil.net_io_counters(pernic=True)
            stats = psutil.net_if_stats()
        except Exception:
            nio, stats = {}, {}
        for name, c in nio.items():
            if name == "lo" or (name in stats and not stats[name].isup):
                continue
            d = {"rx_b": c.bytes_recv, "tx_b": c.bytes_sent, "rx_p": c.packets_recv, "tx_p": c.packets_sent,
                 "errin": c.errin, "errout": c.errout, "dropin": c.dropin, "dropout": c.dropout}
            if prev and dt and name in prev["net"]:
                p = prev["net"][name]
                d.update(rx_bps=(c.bytes_recv - p["rx_b"]) / dt, tx_bps=(c.bytes_sent - p["tx_b"]) / dt,
                         rx_pps=(c.packets_recv - p["rx_p"]) / dt, tx_pps=(c.packets_sent - p["tx_p"]) / dt)
            nets[name] = d
        snap["net"] = nets
        if IS_WIN:   # net_connections is comparatively slow on Windows: refresh every 5th sample
            self._tcp_tick = getattr(self, "_tcp_tick", -1) + 1
            if self._tcp_tick % 5 == 0:
                self._tcp = tcp_states_psutil()
            snap["tcp"] = getattr(self, "_tcp", None)
        else:
            snap["tcp"] = tcp_states()

        # gpu
        gpus = []
        for g in self.gpus:
            try:
                d = g.sample()
            except Exception as e:
                d = {"error": str(e)}
            d["name"] = g.name
            gpus.append(d)
        snap["gpu"] = gpus

        # processes
        snap["procs"] = self._procs(dt)

        # history
        h = self.hist
        if prev:
            h["cpu"].append(snap["cpu"]["total"]["busy"])
            h["mem"].append(snap["mem"]["pct"])
            h["swap"].append(snap["mem"]["swap_used"] / snap["mem"]["swap_total"] * 100
                             if snap["mem"]["swap_total"] else 0)
            h["dio"].append(sum((d.get("r_bps") or 0) + (d.get("w_bps") or 0) for d in disks.values()))
            h["rx"].append(sum(d.get("rx_bps") or 0 for d in nets.values()))
            h["tx"].append(sum(d.get("tx_bps") or 0 for d in nets.values()))
            h["gpu"].append(gpus[0].get("util") if gpus else None)

        self.prev = {"t": now, "stat": st, "vm": vm, "disk": disks, "net": nets}
        snap["latency_ms"] = (time.perf_counter() - t0) * 1000
        return snap

    def _procs(self, dt):
        attrs = ["pid", "ppid", "name", "username", "status", "nice", "num_threads", "cpu_percent",
                 "memory_info", "cmdline", "io_counters", "num_fds"]
        attrs = [a for a in attrs if hasattr(psutil.Process, a)]   # num_fds is POSIX-only
        out, new_io = [], {}
        for p in psutil.process_iter():
            try:
                i = p.as_dict(attrs=attrs, ad_value=None)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            if IS_WIN and i["pid"] == 0:   # System Idle Process: would top every CPU sort
                continue
            mi = i["memory_info"]
            if mi is None:
                rss = vsz = None
            else:
                rss, vsz = mi.rss, mi.vms
            io = i["io_counters"]
            rr = wr = None
            if io is not None:
                new_io[i["pid"]] = (io.read_bytes, io.write_bytes)
                pv = self.prev_io.get(i["pid"])
                if pv and dt:
                    rr, wr = (io.read_bytes - pv[0]) / dt, (io.write_bytes - pv[1]) / dt
            out.append({
                "pid": i["pid"], "ppid": i["ppid"], "name": i["name"] or "?",
                "user": _compat.short_user(i["username"]) or "?",
                "state": (i["status"] or "?")[:1].upper() if i["status"] != "disk-sleep" else "D",
                "nice": int(i["nice"]) if i["nice"] is not None else None,
                "threads": i["num_threads"], "fds": i.get("num_fds"),
                "cpu": i["cpu_percent"] or 0.0, "rss": rss, "vsz": vsz, "io_r": rr, "io_w": wr,
                "cmd": " ".join(i["cmdline"]) if i["cmdline"] else f"[{i['name']}]",
            })
        self.prev_io = new_io
        return out


def jsonable(snap, nprocs=25):
    d = dict(snap)
    procs = sorted(d.get("procs", []), key=lambda p: -p["cpu"])[:nprocs]
    d["procs"] = procs
    return d


# ── rendering ────────────────────────────────────────────────────────────────

def kv(label, value, vstyle=C_VAL, lw=0):
    return T((f"{label:<{lw}}", C_LABEL), (f"{value}", vstyle))


def cols(items, n, width):
    """Join Text items into rows of n equal-width columns."""
    cw = width // n
    lines = []
    for i in range(0, len(items), n):
        row = T()
        for it in items[i:i + n]:
            row.append_text(it)
            row.append(" " * max(0, cw - it.cell_len))
        lines.append(row)
    return lines


def panel(lines, title, color, width, height, subtitle=None):
    return Panel(Group(*lines), title=T((f" {title} ", f"bold {color}")), title_align="left",
                 subtitle=subtitle, subtitle_align="right", border_style=color, padding=(0, 1),
                 width=width, height=height)


def pct_s(v, w=5):
    return f"{v:{w}.1f}" if v is not None else " " * (w - 1) + "-"


def cpu_lines(s, hist, w):
    cpu = s["cpu"]
    out = []
    bw = max(8, min(40, w - 59))
    temps = cpu["temps"].get("coretemp", [])
    core_t = {}
    for e in temps:
        if e["label"].startswith("Core "):
            core_t[int(e["label"].split()[1])] = e["c"]
    # physical core index per logical cpu (best effort)
    def row(label, b, freq=None, temp=None):
        segs = [(b["user"], SEG_COL["u"]), (b["nice"], SEG_COL["n"]), (b["system"], SEG_COL["s"]),
                (b["irq"], SEG_COL["q"]), (b["iowait"], SEG_COL["w"]), (b["steal"], SEG_COL["t"])]
        t = T((f"{label:<4}", "bold"))
        t.append_text(stacked(segs, bw))
        t.append(f" {b['busy']:5.1f}%", style=lvl(b["busy"]))
        for k, key in (("u", "user"), ("s", "system"), ("w", "iowait"), ("q", "irq"), ("t", "steal"), ("n", "nice")):
            t.append(f" {k}", style=C_LABEL)
            v = b[key]
            t.append(f"{v:4.1f}", style=SEG_COL[k] if v >= 0.05 else C_DIM)
        if freq is not None:
            t.append(f" {freq:4.0f}M", style="cyan")
        if temp is not None:
            t.append(f" {temp:.0f}°", style=lvl((temp - 30) / 0.7))
        return t

    out.append(row("all", cpu["total"], sum(cpu["freq_mhz"]) / len(cpu["freq_mhz"]) if cpu["freq_mhz"] else None,
                   next((e["c"] for e in temps if e["label"].startswith("Package")), None)))
    for i, b in enumerate(cpu["percpu"]):
        f = cpu["freq_mhz"][i] if i < len(cpu["freq_mhz"]) else None
        # map logical cpu -> core temp via topology
        cid = read(f"/sys/devices/system/cpu/cpu{i}/topology/core_id")
        ct = core_t.get(int(cid)) if cid is not None and cid.isdigit() else None
        out.append(row(f"c{i}", b, f, ct))
    if IS_WIN:
        out.append(T(kv("ctx ", f"{fcount(cpu['ctx_s'])}/s  "), kv("intr ", f"{fcount(cpu['intr_s'])}/s  "),
                     kv("dpc ", f"{fcount(cpu['softirq_s'])}/s")))
    else:
        out.append(T(kv("ctx ", f"{fcount(cpu['ctx_s'])}/s  "), kv("intr ", f"{fcount(cpu['intr_s'])}/s  "),
                     kv("sirq ", f"{fcount(cpu['softirq_s'])}/s  "), kv("fork ", f"{cpu['fork_s'] or 0:.1f}/s  "),
                     kv("blk ", f"{cpu['blocked'] if cpu['blocked'] is not None else '-'}  "),
                     ("sysc n/a", C_DIM)))
    tl = T(("temp ", C_LABEL))
    shown = False
    for name, ents in cpu["temps"].items():
        if name == "coretemp":
            ents = [e for e in ents if e["label"].startswith("Package")]
        ents = [e for e in ents if e["c"] > 0][:1]
        for e in ents:
            lab = {"coretemp": "pkg", "acpitz_0": "acpi", "thinkpad": "tp"}.get(name, name[:6])
            tl.append(f"{lab} ", style=C_LABEL)
            tl.append(f"{e['c']:.0f}° ", style=lvl((e["c"] - 30) / 0.7))
            shown = True
    if not shown:
        tl.append("n/a", style=C_DIM)
    for name, vals in cpu["fans"].items():
        for v in vals:
            tl.append(f" fan {v}rpm", style="cyan")
    out.append(tl)
    psi = s["psi"]
    if psi:
        for kind in ("some", "full"):
            t = T((f"psi {kind} ", C_LABEL))
            for res in ("cpu", "memory", "io"):
                d = psi.get(res, {}).get(kind)
                if d is None:
                    continue
                t.append(f"{res[:3]} ", style=C_LABEL)
                t.append(f"{d['avg10']:.2f}", style=lvl(d["avg10"] * 4))
                t.append(f"/{d['avg60']:.2f}  ", style=C_DIM if d["avg60"] < 0.05 else "white")
            t.append("avg10/60", style=C_DIM)
            out.append(t)
    elif not IS_WIN:
        out.append(T(("psi n/a", C_DIM)))
    hw = max(10, w - 14)
    out.append(T(("cpu  ", C_LABEL), spark(hist["cpu"], hw), (f" {hist['cpu'][-1]:4.0f}%" if hist["cpu"] else "", "white")))
    return out


def mem_lines(s, hist, w):
    m = s["mem"]
    out = []
    bw = max(8, w - 30)
    t = T(("RAM  ", "bold"))
    t.append_text(bar(m["pct"], bw))
    t.append(f" {m['pct']:5.1f}% ", style=lvl(m["pct"]))
    t.append(f"{fb(m['used'])}/{fb(m['total'])}", style=C_LABEL)
    out.append(t)
    sp = m["swap_used"] / m["swap_total"] * 100 if m["swap_total"] else None
    t = T(("SWAP ", "bold"))
    t.append_text(bar(sp, bw))
    t.append(f" {sp:5.1f}% " if sp is not None else "   n/a ", style=lvl(sp))
    t.append(f"{fb(m['swap_used'])}/{fb(m['swap_total'])}" if m["swap_total"] else "none", style=C_LABEL)
    out.append(t)
    if m["cached"] is None:   # no /proc/meminfo (Windows): only what psutil can tell
        out += cols([kv("avail ", fb(m["available"])), kv("free  ", fb(m["free"]))], 3 if w >= 42 else 2, w)
        out.append(T(("cache/vmstat n/a here", C_DIM)))
    else:
        items = [kv("avail ", fb(m["available"])), kv("free  ", fb(m["free"])), kv("buf   ", fb(m["buffers"])),
                 kv("cache ", fb(m["cached"])), kv("shmem ", fb(m["shared"])), kv("slab  ", fb(m["slab"])),
                 kv("activ ", fb(m["active"])), kv("inact ", fb(m["inactive"])),
                 kv("dirty ", fb(m["dirty"]), "yellow" if m["dirty"] > 64 * 2**20 else C_VAL),
                 kv("wback ", fb(m["writeback"]))]
        out += cols(items, 3 if w >= 42 else 2, w)
        rt = lambda v: "-" if v is None else fcount(v)
        out.append(T(("swap ", C_LABEL), "in ", (rt(m["swap_in_s"]), C_VAL), " out ", (rt(m["swap_out_s"]), C_VAL),
                     (" pg/s", C_DIM)))
        out.append(T(("flt  ", C_LABEL), "min ", (rt(m["minflt_s"]), C_VAL), " maj ",
                     (rt(m["majflt_s"]), "red" if (m["majflt_s"] or 0) > 10 else C_VAL), (" /s", C_DIM)))
    hw = max(10, w - 12)
    out.append(T(("mem  ", C_LABEL), spark(hist["mem"], hw), (f" {m['pct']:3.0f}%", "white")))
    out.append(T(("swap ", C_LABEL), spark(hist["swap"], hw), (f" {sp or 0:3.0f}%", "white")))
    return out


def disk_lines(s, hist, w):
    out = []
    if not s["disk"]:
        out.append(T(("no block devices", C_DIM)))
    for name, d in s["disk"].items():
        u = d.get("util")
        t = T((f"{trunc(name, 7):<7}", "bold"), ("R", C_LABEL), (f"{fb(d.get('r_bps')):>6}", "green"),
              (" W", C_LABEL), (f"{fb(d.get('w_bps')):>6}", "red"))
        t.append(f" u{u:3.0f}%" if u is not None else " u  -", style=lvl(u))
        out.append(t)
        out.append(T(("   iops ", C_LABEL), (f"{d.get('r_iops') or 0:.0f}/{d.get('w_iops') or 0:.0f}", C_VAL),
                     (" r/w", C_DIM)))
    out.append(T(("io   ", C_LABEL), spark(hist["dio"], max(10, w - 12), None), (f" {fb(hist['dio'][-1] if hist['dio'] else 0)}", "white")))
    bw = max(4, w - 30)
    for f in s["fs"][:5]:
        t = T((f"{trunc(f['mount'], 10):<10}", "bold"))
        t.append_text(bar(f["pct"], bw))
        t.append(f" {f['pct']:3.0f}% {fb(f['used'])}/{fb(f['total'])}", style=C_LABEL)
        out.append(t)
    return out


def net_lines(s, hist, w):
    out = []
    ifs = sorted(s["net"].items(), key=lambda kv_: -((kv_[1].get("rx_bps") or 0) + (kv_[1].get("tx_bps") or 0)))[:3]
    if not ifs:
        out.append(T(("no active interfaces", C_DIM)))
    for name, d in ifs:
        out.append(T((f"{trunc(name, 9):<9}", "bold"), ("↓", "green"), (f"{fb(d.get('rx_bps'), True):>8}", "green"),
                     (" ↑", "red"), (f"{fb(d.get('tx_bps'), True):>8}", "red")))
        er = d["errin"] + d["errout"]
        dr = d["dropin"] + d["dropout"]
        out.append(T(("   pps ", C_LABEL), (f"{d.get('rx_pps') or 0:.0f}/{d.get('tx_pps') or 0:.0f}", C_VAL),
                     ("  err ", C_LABEL), (fcount(er), "red" if er else C_DIM),
                     (" drop ", C_LABEL), (fcount(dr), "red" if dr else C_DIM)))
    hw = max(10, w - 12)
    out.append(T(("rx   ", C_LABEL), spark(hist["rx"], hw, None), (f" {fb(hist['rx'][-1] if hist['rx'] else 0)}", "white")))
    out.append(T(("tx   ", C_LABEL), spark(hist["tx"], hw, None), (f" {fb(hist['tx'][-1] if hist['tx'] else 0)}", "white")))
    tcp = s["tcp"]
    if tcp is None:
        out.append(T(("tcp  n/a (permission)" if not IS_WIN else "tcp  n/a", C_DIM)))
    else:
        order = [("ESTABLISHED", "est"), ("LISTEN", "lis"), ("TIME_WAIT", "tw"), ("CLOSE_WAIT", "cw")]
        t = T(("tcp  ", C_LABEL))
        for k, lab in order:
            n = tcp.get(k, 0)
            t.append(f"{lab} ", style=C_LABEL)
            t.append(f"{n} ", style=C_VAL if n else C_DIM)
        other = sum(v for k, v in tcp.items() if k not in dict(order))
        if other:
            t.append(f"oth {other}", style=C_VAL)
        out.append(t)
    return out


def gpu_lines(s, hist, w):
    out = []
    if not s["gpu"]:
        return [T(("no supported GPU detected", C_DIM))]
    for gi, d in enumerate(s["gpu"]):
        if gi:
            out.append(T())
        out.append(T((trunc(d.get("name", "GPU"), w), "bold")))
        if "error" in d:
            out.append(T((d["error"], "red")))
            continue
        eng = d.get("engines")
        if eng:
            items = []
            bw = max(4, (w // 2) - 13)
            for n, v in eng.items():
                t = T((f"{n:<4}", C_LABEL))
                t.append_text(bar(v, bw))
                t.append(f"{v:5.1f}%", style=lvl(v))
                items.append(t)
            out += cols(items, 2, w)
        elif d.get("util") is not None:
            t = T(("load ", "bold"))
            t.append_text(bar(d["util"], max(4, w - 12)))
            t.append(f" {d['util']:4.0f}%", style=lvl(d["util"]))
            out.append(t)
        elif "no_clients" in d:
            out.append(T(("engines n/a (no fdinfo)", C_DIM)))
        if d.get("freq_act") is not None or d.get("freq_cur") is not None:
            out.append(T(("freq ", C_LABEL), "act ", (f"{d.get('freq_act') or 0:.0f}", "cyan"),
                         " cur ", (f"{d.get('freq_cur') or 0:.0f}", "cyan"), (" MHz", C_DIM)))
            out.append(T(("     ", C_LABEL), f"min {d.get('freq_min') or 0:.0f} max {d.get('freq_max') or 0:.0f}"
                         f" bst {d.get('freq_boost') or 0:.0f}"))
        elif d.get("freq"):
            out.append(T(("freq ", C_LABEL), (f"{d['freq']:.0f}/{d.get('freq_max') or 0:.0f} MHz", "cyan")))
        t = T()
        if d.get("rc6_pct") is not None:
            t.append("rc6 ", style=C_LABEL)
            t.append(f"{d['rc6_pct']:.1f}%", style="green" if d["rc6_pct"] > 50 else "yellow")
        elif d.get("rc6_ms") is not None:
            t.append(f"rc6 {d['rc6_ms']:.0f}ms", style=C_LABEL)
        for key, lab, fmt in (("temp", "temp", "{:.0f}°C"), ("power", "pwr", "{:.1f}W")):
            if d.get(key) is not None:
                t.append(f"  {lab} ", style=C_LABEL)
                t.append(fmt.format(d[key]), style=C_VAL)
        if d.get("mem_total"):
            t.append(f"  vram {fb(d['mem_used'])}/{fb(d['mem_total'])}", style=C_LABEL)
        elif d.get("mem_used"):
            t.append(f"  mem {fb(d['mem_used'])}", style=C_LABEL)
        if t.plain:
            out.append(t)
        if d.get("no_clients") and eng is None and "engines" in d:
            pass
        clients = d.get("clients") or []
        for c in clients[:3]:
            es = " ".join(f"{k}{v:.0f}" for k, v in c["engines"].items())
            out.append(T((f"{c['pid']:>6} ", C_LABEL), (trunc(c["name"], 12), "white"), (" " + es, "green")))
        if "clients" in d and not clients and not d.get("no_clients"):
            out.append(T(("clients idle", C_DIM)))
        if d.get("no_clients"):
            out.append(T(("fdinfo: no DRM clients", C_DIM)))
    out.append(T(("gpu  ", C_LABEL), spark(hist["gpu"], max(10, w - 12)),
                 (f" {hist['gpu'][-1]:3.0f}%" if hist["gpu"] and hist["gpu"][-1] is not None else " n/a", "white")))
    return out


def fmt_up(sec):
    d, r = divmod(int(sec), 86400)
    h, r = divmod(r, 3600)
    return f"{d}d{h:02d}h{r // 60:02d}m" if d else f"{h}h{r // 60:02d}m"


class UI:
    def __init__(self, interval):
        self.interval = interval
        self.sort = "cpu"
        self.rev = False
        self.filter = ""
        self.editing = False
        self.paused = False
        self.tree = False
        self.scroll = 0
        self.render_ms = 0.0


SORT_KEYS = {
    "cpu": lambda p: p["cpu"], "mem": lambda p: p["rss"] or 0, "pid": lambda p: p["pid"],
    "io": lambda p: (p["io_r"] or 0) + (p["io_w"] or 0), "threads": lambda p: p["threads"] or 0,
}
SORT_DEFAULT_DESC = {"cpu": True, "mem": True, "pid": False, "io": True, "threads": True}


def order_procs(procs, ui):
    flt = ui.filter.lower()
    if flt:
        procs = [p for p in procs if flt in p["name"].lower() or flt in p["cmd"].lower() or flt == str(p["pid"])]
    desc = SORT_DEFAULT_DESC[ui.sort] != ui.rev
    key = SORT_KEYS[ui.sort]
    if ui.tree and not flt:
        pids = {p["pid"] for p in procs}
        kids = {}
        for p in procs:
            kids.setdefault(p["ppid"] if p["ppid"] in pids and p["ppid"] != p["pid"] else None, []).append(p)
        res = []

        def walk(parent, depth, seen):
            for p in sorted(kids.get(parent, []), key=key, reverse=desc):
                if p["pid"] in seen:
                    continue
                res.append((p, depth))
                walk(p["pid"], depth + 1, seen | {p["pid"]})
        walk(None, 0, set())
        return res
    return [(p, 0) for p in sorted(procs, key=key, reverse=desc)]


def proc_panel(s, ui, width, height):
    nrows = max(1, height - 3)
    ordered = order_procs(s["procs"], ui)
    ui.scroll = max(0, min(ui.scroll, max(0, len(ordered) - nrows)))
    shown = ordered[ui.scroll: ui.scroll + nrows]

    def hdr(label, key=None):
        on = key == ui.sort
        arrow = ("▲" if (SORT_DEFAULT_DESC[ui.sort] == ui.rev) else "▼") if on else ""
        return Text(label + arrow, style="bold black on orange3" if on else "bold grey70", no_wrap=True)

    t = Table(box=None, expand=True, pad_edge=False, padding=(0, 1), header_style="bold grey70")
    spec = [("PID", "pid", 7, "right"), ("PPID", None, 7, "right"), ("USER", None, 8, "left"), ("S", None, 1, "left"),
            ("NI", None, 3, "right"), ("THR", "threads", 4, "right"), ("FD", None, 4, "right"),
            ("CPU%", "cpu", 6, "right"), ("RSS", "mem", 6, "right"), ("VSZ", None, 6, "right"),
            ("IO-R/s", "io", 7, "right"), ("IO-W/s", None, 7, "right")]
    for label, key, wd, just in spec:
        t.add_column(hdr(label, key), width=wd, justify=just, no_wrap=True, overflow="ellipsis")
    t.add_column("COMMAND", ratio=1, no_wrap=True, overflow="ellipsis", header_style="bold grey70")
    me = os.environ.get("USER") or os.environ.get("USERNAME", "")
    for p, depth in shown:
        cpu = p["cpu"]
        cmd = p["cmd"]
        prefix = ("  " * (depth - 1) + "└ ") if depth else ""
        ct = Text(no_wrap=True, overflow="ellipsis")
        ct.append(prefix, style=C_DIM)
        exe, _, rest = cmd.partition(" ")
        if IS_WIN:
            m = re.match(r'^"?([A-Za-z]:\\[^"]*?\.exe)"?\s*(.*)$', cmd, re.I)
            if m:
                exe, rest = m.group(1).rsplit("\\", 1)[-1], m.group(2)
        ct.append(os.path.basename(exe) if exe.startswith("/") else exe, style="bold white")
        ct.append(" " + rest if rest else "", style=C_LABEL)
        st = p["state"]
        t.add_row(
            str(p["pid"]), str(p["ppid"]), trunc(p["user"], 8) if p["user"] != me else Text(trunc(p["user"], 8), style=C_LABEL),
            Text(st, style={"R": "bold green", "D": "bold red", "Z": "bold magenta", "T": "yellow"}.get(st, C_LABEL)),
            "-" if p["nice"] is None else str(p["nice"]), str(p["threads"] or "-"),
            "-" if p["fds"] is None else str(p["fds"]),
            Text(f"{cpu:.1f}", style=lvl(cpu)), fb(p["rss"]).strip(), fb(p["vsz"]).strip(),
            fb(p["io_r"]).strip() if p["io_r"] is not None else Text("-", style=C_DIM),
            fb(p["io_w"]).strip() if p["io_w"] is not None else Text("-", style=C_DIM),
            ct)
    sub = T((f"{len(ordered)}/{len(s['procs'])} procs  sort:{ui.sort}{'↑' if ui.rev else ''}"
             f"{'  tree' if ui.tree else ''}{'  /' + ui.filter if ui.filter else ''}  CPU% 100=1 core", C_LABEL))
    return Panel(t, title=T((" PROCESSES ", f"bold {B_PRC}")), title_align="left", subtitle=sub,
                 subtitle_align="right", border_style=B_PRC, padding=(0, 1), width=width, height=height)


def header_line(s, ui, width):
    sy = s["sys"]
    t = T((" librestatistics-dev ", "bold black on cyan"), (" ", ""), (trunc(sy["host"], 22), "bold"))
    clock = f" {time.strftime('%H:%M:%S')}"
    extras = [
        (f" {sy['kernel']}", C_LABEL),
        (f" up {fmt_up(sy['uptime_s'])}", C_LABEL),
    ]
    if sy["load"]:
        load = T((" load ", C_LABEL))
        for v, n in zip(sy["load"], sy["load_norm"]):
            load.append(f"{v:.2f} ", style=lvl(n * 100))
        load.append(f"({sy['load_norm'][0] * 100:.0f}%)", style=C_DIM)
        extras.append(load)
    if sy["running"] is not None:
        extras.append((f" run {sy['running']}/{sy['total']}", C_LABEL))
    extras += [(f" smp {s['latency_ms']:.0f}ms", C_LABEL), (f" rnd {ui.render_ms:.0f}ms", C_LABEL)]
    for e in extras:
        piece = T(e)
        if t.cell_len + piece.cell_len + len(clock) + (8 if ui.paused else 0) <= width:
            t.append_text(piece)
    t.append(clock, style="white")
    if ui.paused:
        t.append(" PAUSED", style="bold black on yellow")
    return t


def footer_line(ui):
    t = T()
    if ui.editing:
        t.append(" filter/ ", style="bold black on yellow")
        t.append(ui.filter + "█", style="white")
        t.append("  Enter=apply Esc=clear", style=C_DIM)
        return t
    for k, d in (("c", "cpu"), ("m", "mem"), ("p", "pid"), ("i", "io"), ("t", "thr"), ("r", "rev"), ("/", "filter"),
                 ("T", "tree"), ("j/k", "scroll"), ("␣", "pause"), ("+/-", f"{ui.interval:g}s"), ("q", "quit")):
        t.append(f" {k}", style="bold cyan")
        t.append(f":{d}", style=C_LABEL)
    return t


def build_row(specs, W):
    """specs: [(ratio, builder(inner_w)->lines, title, color, subtitle)] -> grid with equal-height panels."""
    total = sum(r for r, *_ in specs)
    widths = [W * r // total for r, *_ in specs]
    widths[-1] = W - sum(widths[:-1])
    bodies = [b(w - 4) for (_, b, *_), w in zip(specs, widths)]
    h = max(len(b) for b in bodies) + 2
    grid = Table.grid(expand=False)
    for w in widths:
        grid.add_column(width=w, no_wrap=True)
    cells = [panel(b, spec[2], spec[3], w, h, spec[4] if len(spec) > 4 else None)
             for b, spec, w in zip(bodies, specs, widths)]
    grid.add_row(*cells)
    return grid, h


def render(s, hist, ui, console):
    W, H = console.size
    W = max(W, 80)
    legend = T(("u", "green"), ("ser ", C_LABEL), ("s", "red"), ("ys ", C_LABEL), ("w", "yellow"), ("io ", C_LABEL),
               ("q", "magenta"), ("irq ", C_LABEL), ("t", "cyan"), ("steal ", C_LABEL), ("n", "blue"), ("ice", C_LABEL))
    ncores = len(s["cpu"]["percpu"])
    row1, h1 = build_row([
        (5, lambda w: cpu_lines(s, hist, w), f"CPU {ncores}T", B_CPU, legend),
        (3, lambda w: mem_lines(s, hist, w), "MEM", B_MEM),
    ], W)
    row2, h2 = build_row([
        (1, lambda w: disk_lines(s, hist, w), "DISK", B_DSK),
        (1, lambda w: net_lines(s, hist, w), "NET", B_NET),
        (1, lambda w: gpu_lines(s, hist, w), "GPU", B_GPU),
    ], W)
    ph = max(5, H - 2 - h1 - h2)
    return Group(header_line(s, ui, W), row1, row2, proc_panel(s, ui, W, ph), footer_line(ui))


# ── terminal input ───────────────────────────────────────────────────────────

class RawTTY:
    WIN_KEYS = {"H": "UP", "P": "DOWN", "I": "PGUP", "Q": "PGDN"}

    def __init__(self):
        self.win = IS_WIN and msvcrt is not None
        try:
            self.fd = sys.stdin.fileno()
        except (OSError, ValueError, AttributeError):
            self.fd = None
        self.old = None

    def _win_keys(self, timeout):
        # Non-blocking poll: kbhit() + getwch(); arrows/PgUp/PgDn arrive as a '\x00' or '\xe0' prefix + code.
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            out = []
            try:
                while msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        k = self.WIN_KEYS.get(msvcrt.getwch())
                        if k:
                            out.append(k)
                    elif ch == "\x1b":
                        out.append("ESC")
                    else:
                        out.append(ch)
            except OSError:
                pass
            if out:
                return out
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            time.sleep(min(0.02, remaining))

    def __enter__(self):
        if self.win or IS_WIN or self.fd is None:
            return self
        import termios, tty
        self.termios = termios
        try:
            self.old = termios.tcgetattr(self.fd)
            tty.setcbreak(self.fd)
        except Exception:
            self.old = None
        return self

    def __exit__(self, *exc):
        if self.old is not None and not IS_WIN:
            self.termios.tcsetattr(self.fd, self.termios.TCSADRAIN, self.old)

    def keys(self, timeout):
        if self.win:
            return self._win_keys(timeout)
        if self.old is None:
            time.sleep(timeout)
            return []
        r, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
        if not r:
            return []
        try:
            data = os.read(self.fd, 64).decode("utf-8", "ignore")
        except OSError:
            return []
        out, i = [], 0
        while i < len(data):
            if data[i] == "\x1b":
                m = {"[A": "UP", "[B": "DOWN", "[5": "PGUP", "[6": "PGDN"}.get(data[i + 1:i + 3])
                if m:
                    out.append(m)
                    i += 4 if m in ("PGUP", "PGDN") else 3
                    continue
                out.append("ESC")
            else:
                out.append(data[i])
            i += 1
        return out


def handle_key(k, ui, nrows):
    """Returns True to quit."""
    if ui.editing:
        if k in ("\n", "\r"):
            ui.editing = False
        elif k == "ESC":
            ui.editing, ui.filter = False, ""
        elif k in ("\x7f", "\b"):
            ui.filter = ui.filter[:-1]
        elif len(k) == 1 and k.isprintable():
            ui.filter += k
        ui.scroll = 0
        return False
    if k in ("q", "\x03"):
        return True
    sorts = {"c": "cpu", "m": "mem", "p": "pid", "i": "io", "t": "threads"}
    if k in sorts:
        ui.sort, ui.rev = sorts[k], False
    elif k == "r":
        ui.rev = not ui.rev
    elif k == "/":
        ui.editing = True
    elif k == "T":
        ui.tree = not ui.tree
    elif k == " ":
        ui.paused = not ui.paused
    elif k in ("+", "="):
        ui.interval = min(10.0, ui.interval + (0.25 if ui.interval < 2 else 1.0))
    elif k in ("-", "_"):
        ui.interval = max(0.25, ui.interval - (0.25 if ui.interval <= 2 else 1.0))
    elif k in ("j", "DOWN"):
        ui.scroll += 1
    elif k in ("k", "UP"):
        ui.scroll = max(0, ui.scroll - 1)
    elif k == "PGDN":
        ui.scroll += nrows
    elif k == "PGUP":
        ui.scroll = max(0, ui.scroll - nrows)
    elif k == "ESC" and ui.filter:
        ui.filter = ""
    return False


# ── main ─────────────────────────────────────────────────────────────────────

def timed_render(s, sampler, ui, console):
    t0 = time.perf_counter()
    r = render(s, sampler.hist, ui, console)
    ui.render_ms = (time.perf_counter() - t0) * 1000
    return r


def main():
    ap = argparse.ArgumentParser(description="Developer-oriented live system monitor")
    ap.add_argument("-i", "--interval", type=float, default=1.0, help="refresh interval in seconds")
    ap.add_argument("--once", action="store_true", help="print one snapshot and exit")
    ap.add_argument("--json", action="store_true", help="emit one JSON sample to stdout and exit")
    ap.add_argument("--log", metavar="FILE", help="append NDJSON samples each tick")
    args = ap.parse_args()
    args.interval = max(0.1, args.interval)

    sampler = Sampler()
    ui = UI(args.interval)
    console = Console()

    if args.json or args.once:
        time.sleep(min(args.interval, 1.0) if args.once else min(args.interval, 0.5))
        snap = sampler.sample()
        if args.json:
            print(json.dumps(jsonable(snap), default=str))
            return
        console.print(timed_render(snap, sampler, ui, console))
        return

    logf = open(args.log, "a", buffering=1) if args.log else None
    try:
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(SystemExit(143)))
    except (AttributeError, ValueError, OSError):
        pass
    time.sleep(0.3)
    try:
        with RawTTY() as tty_in, Live(console=console, screen=True, auto_refresh=False) as live:
            snap, next_t, dirty = None, 0.0, True
            while True:
                now = time.monotonic()
                if not ui.paused and (snap is None or now >= next_t):
                    snap = sampler.sample()
                    if logf:
                        logf.write(json.dumps(jsonable(snap, 10), default=str) + "\n")
                    next_t = time.monotonic() + ui.interval
                    dirty = True
                if dirty and snap is not None:
                    live.update(timed_render(snap, sampler, ui, console), refresh=True)
                    dirty = False
                wait = 0.5 if ui.paused or ui.editing else max(0.0, next_t - time.monotonic())
                nrows = max(1, console.size.height - 10)
                for k in tty_in.keys(wait):
                    if handle_key(k, ui, nrows):
                        return
                    dirty = True
                    if k == " " and not ui.paused:
                        next_t = 0.0
    except KeyboardInterrupt:
        pass
    finally:
        if logf:
            logf.close()


if __name__ == "__main__":
    main()
