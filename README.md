# sysmon

Terminal dashboards that show your **CPU, memory and GPU** usage live. There are three editions, for different people:

| Edition | File | For |
|---|---|---|
| **Friendly** (v2.0) | `sysmon_friendly.py` | Anyone. Plain language, big numbers, apps grouped by name, helpful tips. |
| **Dev** | `sysmon_dev.py` | Developers. Dense raw metrics, per-core breakdown, disk/net/PSI, sortable process table, JSON export. |
| **Classic** (v1.0) | `sysmon_classic.py` | The original. A clean, simple overview. |

## Install

```bash
git clone git@github.com:juujbyg/sysmon.git
cd sysmon
pip install -r requirements.txt   # psutil + rich
```

Linux only (it reads `/proc` and `/sys`). Python 3.9+.

## Usage

```bash
python3 sysmon_friendly.py        # friendly view
python3 sysmon_dev.py             # developer view
python3 sysmon_classic.py         # original v1.0 view
```

All editions accept `-i SECONDS` (refresh interval) and `--once` (print a single snapshot and exit).

### Friendly

- Status line in plain words, such as "Everything looks good" or "Memory is running low".
- A card each for Processor, Memory and Graphics, with a big number, a word for how busy it is (Relaxed → Maxed out) and a short history graph.
- **What's using your computer**: processes grouped into apps (e.g. "Brave ×47"). Shared memory is counted only once per app.
- **At a glance**: battery, disk space, internet speed, how long the computer has been on, and tips.

```
 sysmon   ●  Everything looks good                                                              Thursday 12:39
╭─ Processor ──────────────────────╮╭─ Memory ──────────────────────────╮╭─ Graphics ────────────────────────╮
│                                  ││                                   ││                                   │
│   ╺━┓┏━┓                         ││   ╻ ╻╺┓                           ││   ┏━┓                             │
│   ┏━┛┣━┫   Comfortable           ││   ┗━┫ ┃    Comfortable            ││   ┃ ┃   Relaxed                   │
│   ┗━╸┗━┛ %                       ││     ╹╺┻╸ %                        ││   ┗━┛ %                           │
│                                  ││                                   ││                                   │
│   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ ││   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ││   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                  ││                                   ││                                   │
│   4 cores  ·  Cool 56°C          ││   6.5 GB free of 11.1 GB          ││   Intel graphics                  │
│   Fan is spinning                ││   Using disk as backup (59%)      ││   Estimated from speed            │
│                                  ││                                   ││                                   │
│   ▃  last 1s                     ││   ▄  last 1s                      ││   ▁  last 1s                      │
╰──────────────────────────────────╯╰───────────────────────────────────╯╰───────────────────────────────────╯
╭─ What's using your computer ───────────────────────────────────╮╭─ At a glance ────────────────────────────╮
│ App                             Processor     Memory           ││ Battery    ━━━━━━━━━━━━ 95%              │
│ Python  ×5                      ━━━━━━  18%   ━━━━━━  181 MB   ││            Plugged in, charging          │
│ Claude Code  ×4                 ━━━━━━   4%   ━━━━━━  1.1 GB   ││ Disk       ━━━━━━━━━━━━ 44%              │
│ Brave  ×16                      ━━━━━━   0%   ━━━━━━  674 MB   ││            57.7 GB free of 109.0 GB      │
│ Chrome  ×16                     ━━━━━━   0%   ━━━━━━  630 MB   ││ Internet   ↓ 496 B/s   ↑ 439 B/s         │
│ Terminal  ×2                    ━━━━━━   4%   ━━━━━━  102 MB   ││ Turned on  6 days, 5 hours ago           │
│ Desktop  ×33                    ━━━━━━   1%   ━━━━━━  289 MB   ││                                          │
│ Node.js  ×10                    ━━━━━━   0%   ━━━━━━  426 MB   ││ ✦ All good. Nothing needs your           │
│ MATLAB  ×3                      ━━━━━━   0%   ━━━━━━  116 MB   ││ attention.                               │
│ System  ×33                     ━━━━━━   0%   ━━━━━━   96 MB   ││                                          │
│ FreeCAD                         ━━━━━━   0%   ━━━━━━   70 MB   ││                                          │
│ Software updates  ×4            ━━━━━━   0%   ━━━━━━   56 MB   ││                                          │
│ Sound  ×4                       ━━━━━━   0%   ━━━━━━   54 MB   ││                                          │
╰────────────────────────────────────────────────────────────────╯╰──────────────────────────────────────────╯
 Updates every second  ·  press Ctrl+C to close  ·  v2.0
```

### Dev

- Per-core stacked user/sys/iowait/irq/steal/nice, frequency and temperature. Context switches, interrupts and forks per second. PSI pressure.
- Full `/proc/meminfo` breakdown, swap in/out rate, page faults.
- Per-device disk throughput, IOPS and busy %. Per-interface network rates, errors and drops. TCP connections by state.
- GPU engine busy %, all clock speeds, RC6 residency, top GPU clients.
- Process table with PID, PPID, state, nice, threads, open files, CPU, RSS, VSZ, I/O rates and command line.
- `--json` prints one sample of every metric; `--log FILE` appends NDJSON while running.

**Keys:** `c`/`m`/`p`/`i`/`t` sort by cpu/mem/pid/io/threads · `r` reverse · `/` filter · `T` tree · `j`/`k` scroll · `space` pause · `+`/`-` interval · `q` quit

```
 sysmon-dev  my-laptop 7.0.0-38-generic up 6d05h02m load 0.98 1.21 1.57 (25%) run 2/1304 smp 141ms 12:39:04
╭─  CPU 4T  ──────────────────────────────────────────────────────────────╮╭─  MEM  ───────────────────────────────────╮
│ all ████········  34.7% u26.3 s 8.2 w 0.2 q 0.0 t 0.0 n 0.0 2441M 56°   ││ RAM  ████▍······  40.5% 4.5G/11.1G        │
│ c0  ████········  34.7% u25.8 s 8.9 w 0.0 q 0.0 t 0.0 n 0.0 2295M 56°   ││ SWAP ██████▌····  59.2% 1.2G/2.0G         │
│ c1  █████·······  38.4% u30.4 s 8.0 w 0.0 q 0.0 t 0.0 n 0.0 2296M 56°   ││ avail 6.6G          free  3.4G            │
│ c2  ████········  32.8% u24.8 s 7.2 w 0.8 q 0.0 t 0.0 n 0.0 2594M 50°   ││ buf   58.1M         cache 3.5G            │
│ c3  ████········  33.1% u25.0 s 8.1 w 0.0 q 0.0 t 0.0 n 0.0 2580M 50°   ││ shmem 821M          slab  1.1G            │
│ ctx 2.7k/s  intr 2.5k/s  sirq 1.1k/s  fork 1.6/s  blk 0  sysc n/a       ││ activ 3.9G          inact 2.1G            │
│ temp acpi 52° tp 52° pkg 56°  fan 0rpm                                  ││ dirty 1.6M          wback 0B              │
│ psi some cpu 1.71/1.42  mem 0.00/0.00  io 0.01/0.32  avg10/60           ││ swap in 1 out 0 pg/s                      │
│ psi full cpu 0.00/0.00  mem 0.00/0.00  io 0.00/0.28  avg10/60           ││ flt  min 324 maj 1 /s                     │
│ cpu                                                          ▃   35%    ││ mem                              ▄  40%   │
│                                                                         ││ swap                             ▅  59%   │
╰───────────────────────────────────────── user sys wio qirq tsteal nice ─╯╰───────────────────────────────────────────╯
╭─  DISK  ─────────────────────────────╮╭─  NET  ──────────────────────────────╮╭─  GPU  ──────────────────────────────╮
│ sda    R  3.2K W    0B u  0%         ││ enp0s25  ↓  555B/s ↑  559B/s         ││ Intel iGPU card1                     │
│    iops 1/0 r/w                      ││    pps 8/8  err 0 drop 43.8k         ││ engines n/a (no fdinfo)              │
│ io                          █ 3.2K   ││ wlp3s0   ↓    0B/s ↑    0B/s         ││ freq act 200 cur 500 MHz             │
│ /         ██▋···  44% 45.7G/109G     ││    pps 0/0  err 0 drop 420           ││      min 200 max 1000 bst 1000       │
│ /boot/efi ······   1% 6.3M/511M      ││ rx                          █ 555B   ││ rc6 92.9%  temp 56°C                 │
│                                      ││ tx                          █ 559B   ││ fdinfo: no DRM clients               │
│                                      ││ tcp  est 11 lis 13 tw 1 cw 4         ││ gpu                           n/a    │
```

## GPU support

| Vendor | Source |
|---|---|
| NVIDIA | `nvidia-smi` |
| AMD | amdgpu sysfs (`gpu_busy_percent`, VRAM, hwmon) |
| Intel | DRM fdinfo engine counters per process (no root needed) + i915 sysfs frequencies |

On Intel, usage comes from processes you're allowed to read. If none are readable, the friendly edition estimates usage from the GPU clock speed and says so.

## License

MIT
