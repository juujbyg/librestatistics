# LibreStatistics

[![CI](https://github.com/LibreProject/librestatistics/actions/workflows/ci.yml/badge.svg)](https://github.com/LibreProject/librestatistics/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/LibreProject/librestatistics)](https://github.com/LibreProject/librestatistics/releases/latest)
[![License: GPL-2.0-or-later](https://img.shields.io/badge/license-GPL--2.0--or--later-blue)](LICENSE)

Part of the **LibreProject** family (alongside LibrePrint).

Live **CPU, memory and GPU** dashboards for the terminal, in three editions for different people:

| Edition | Command | For |
|---|---|---|
| **Friendly** | `librestatistics` | Anyone. Plain language, big numbers, apps grouped by name, helpful tips. |
| **Developer** | `librestatistics-dev` | Developers. Dense raw metrics, per-core breakdown, disk, network and pressure stats, sortable process table, JSON export. |
| **Classic** (v1.0) | `librestatistics-classic` | The original. A clean, simple overview. |

Runs on **Linux** and **Windows**.

## Install

All channels install all three commands.

### Snap (Ubuntu and other Linux)
```bash
sudo snap install librestatistics
# allow it to read system statistics (once):
for i in system-observe hardware-observe mount-observe network-observe; do sudo snap connect librestatistics:$i; done
```
Snap commands are `librestatistics`, `librestatistics.dev` and `librestatistics.classic`.

### apt (Debian / Ubuntu)
```bash
sudo curl -fsSLo /usr/share/keyrings/librestatistics.gpg https://libreproject.github.io/librestatistics/apt/librestatistics.gpg
echo "deb [signed-by=/usr/share/keyrings/librestatistics.gpg] https://libreproject.github.io/librestatistics/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/librestatistics.list
sudo apt update && sudo apt install librestatistics
```
Or download `librestatistics_*_all.deb` from [Releases](https://github.com/LibreProject/librestatistics/releases) and run `sudo apt install ./librestatistics_*_all.deb`.

### Windows
Download from [Releases](https://github.com/LibreProject/librestatistics/releases):
- **`librestatistics-*-windows-x64-setup.exe`**: installer with Start-menu shortcuts that adds the commands to PATH.
- **`librestatistics-*-windows-x64.zip`**: portable `.exe` files, no install needed.

Run them in Windows Terminal for the best look. The files aren't code-signed, so SmartScreen may ask you to confirm the first time.

### pip (any OS with Python 3.9+)
```bash
pipx install librestatistics        # or: pip install librestatistics
pipx install git+https://github.com/LibreProject/librestatistics   # latest from GitHub
```

### From source
```bash
git clone https://github.com/LibreProject/librestatistics.git && cd librestatistics
pip install .
python -m librestatistics [friendly|dev|classic]
```

## Usage

Every edition accepts `-i SECONDS` (refresh interval) and `--once` (print one snapshot and exit).

### Friendly: `librestatistics`

- Status line in plain words, such as "Everything looks good" or "Memory is running low".
- A card each for Processor, Memory and Graphics, with a big number, a word for how busy it is (Relaxed → Maxed out) and a short history graph.
- **What's using your computer**: processes grouped into apps (e.g. "Brave ×47"). Shared memory is counted only once per app.
- **At a glance**: battery, disk space, internet speed, how long the computer has been on, and tips.

```
 LibreStatistics   ●  Everything looks good                                                     Thursday 20:17
╭─ Processor ──────────────────────╮╭─ Memory ──────────────────────────╮╭─ Graphics ────────────────────────╮
│                                  ││                                   ││                                   │
│   ╻ ╻╺━┓                         ││   ╻ ╻╺━┓                          ││   ┏━┓                             │
│   ┗━┫ ━┫   Comfortable           ││   ┗━┫  ┃   Comfortable            ││   ┃ ┃   Relaxed                   │
│     ╹╺━┛ %                       ││     ╹  ╹ %                        ││   ┗━┛ %                           │
│                                  ││                                   ││                                   │
│   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ ││   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  ││   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                  ││                                   ││                                   │
│   4 cores  ·  Cool 59°C          ││   5.9 GB free of 11.1 GB          ││   Intel graphics                  │
│   Fan is spinning                ││   Plenty of room                  ││   Estimated from speed            │
│                                  ││                                   ││                                   │
│   ▄  last 1s                     ││   ▄  last 1s                      ││   ▁  last 1s                      │
╰──────────────────────────────────╯╰───────────────────────────────────╯╰───────────────────────────────────╯
╭─ What's using your computer ───────────────────────────────────╮╭─ At a glance ────────────────────────────╮
│ App                             Processor     Memory           ││ Battery    ━━━━━━━━━━━━ 95%              │
│ Brave  ×15                      ━━━━━━  31%   ━━━━━━  2.1 GB   ││            Plugged in, charging          │
│ Python  ×4                      ━━━━━━   6%   ━━━━━━  203 MB   ││ Disk       ━━━━━━━━━━━━ 44%              │
│ Claude Code  ×2                 ━━━━━━   1%   ━━━━━━  676 MB   ││            57.7 GB free of 109.0 GB      │
│ Desktop  ×33                    ━━━━━━   2%   ━━━━━━  304 MB   ││ Internet   ↓ 440 B/s   ↑ 822 B/s         │
│ Node.js  ×6                     ━━━━━━   0%   ━━━━━━  316 MB   ││ Turned on  6 days, 6 hours ago           │
│ Terminal  ×2                    ━━━━━━   1%   ━━━━━━  119 MB   ││                                          │
│ Protonvpn-app                   ━━━━━━   0%   ━━━━━━  239 MB   ││ ✦ All good. Nothing needs your           │
│ Papers                          ━━━━━━   0%   ━━━━━━  194 MB   ││ attention.                               │
│ System  ×38                     ━━━━━━   0%   ━━━━━━  168 MB   ││                                          │
│ Files                           ━━━━━━   0%   ━━━━━━  145 MB   ││                                          │
│ MATLAB  ×3                      ━━━━━━   0%   ━━━━━━   92 MB   ││                                          │
│ Software updates  ×5            ━━━━━━   0%   ━━━━━━   64 MB   ││                                          │
╰────────────────────────────────────────────────────────────────╯╰──────────────────────────────────────────╯
 Updates every second  ·  press Ctrl+C to close  ·  v2.0.0
```

### Developer: `librestatistics-dev`

- Per-core stacked user/sys/iowait/irq/steal/nice, frequency and temperature. Context switches, interrupts and forks per second. Linux pressure stall (PSI) stats.
- Full memory breakdown, swap in/out rate, page faults.
- Per-device disk throughput, IOPS and busy %. Per-interface network rates, errors and drops. TCP connections by state.
- GPU engine busy %, clock speeds, power-saving residency, top GPU clients.
- Process table with PID, PPID, state, nice, threads, open files, CPU, RSS, VSZ, I/O rates and command line.
- `--json` prints one sample of every metric; `--log FILE` appends NDJSON samples while running.

**Keys:** `c`/`m`/`p`/`i`/`t` sort by cpu/mem/pid/io/threads · `r` reverse · `/` filter · `T` tree · `j`/`k` scroll · `space` pause · `+`/`-` interval · `q` quit

```
 librestatistics-dev  my-laptop 7.0.0-38-generic up 6d06h29m load 5.22 3.15 2.73 (131%) run 2/1173 20:17:53
╭─  CPU 4T  ──────────────────────────────────────────────────────────────╮╭─  MEM  ───────────────────────────────────╮
│ all █████·······  40.5% u35.0 s 5.3 w 0.0 q 0.2 t 0.0 n 0.0 2211M 58°   ││ RAM  █████▏·····  46.7% 5.2G/11.1G        │
│ c0  █████·······  37.7% u32.8 s 4.9 w 0.0 q 0.0 t 0.0 n 0.0 2124M 58°   ││ SWAP ████▊······  43.7% 896M/2.0G         │
│ c1  ██████······  50.0% u41.8 s 8.2 w 0.0 q 0.0 t 0.0 n 0.0 2224M 58°   ││ avail 5.9G          free  1.2G            │
│ c2  ████········  35.2% u31.1 s 4.1 w 0.0 q 0.0 t 0.0 n 0.0 2225M 55°   ││ buf   146M          cache 4.9G            │
│ c3  █████·······  37.9% u33.9 s 4.0 w 0.0 q 0.0 t 0.0 n 0.0 2269M 55°   ││ shmem 874M          slab  1.2G            │
│ ctx 5.9k/s  intr 4.6k/s  sirq 1.8k/s  fork 0.8/s  blk 0  sysc n/a       ││ activ 5.0G          inact 3.0G            │
│ temp acpi 56° tp 56° pkg 58°  fan 3236rpm                               ││ dirty 2.5M          wback 0B              │
│ psi some cpu 18.24/22.88  mem 0.00/0.00  io 0.00/0.00  avg10/60         ││ swap in 0 out 0 pg/s                      │
│ psi full cpu 0.00/0.00  mem 0.00/0.00  io 0.00/0.00  avg10/60           ││ flt  min 4.8k maj 0 /s                    │
│ cpu                                                          ▄   40%    ││ mem                              ▄  47%   │
│                                                                         ││ swap                             ▄  44%   │
╰───────────────────────────────────────── user sys wio qirq tsteal nice ─╯╰───────────────────────────────────────────╯
╭─  DISK  ─────────────────────────────╮╭─  NET  ──────────────────────────────╮╭─  GPU  ──────────────────────────────╮
│ sda    R    0B W    0B u  0%         ││ enp0s25  ↓  2.3K/s ↑  389B/s         ││ Intel iGPU card1                     │
│    iops 0/0 r/w                      ││    pps 7/6  err 0 drop 46.4k         ││ engines n/a (no fdinfo)              │
│ io                          ▁ 0B     ││ rx                          █ 2.3K   ││ freq act 200 cur 500 MHz             │
│ /         ██▋···  44% 45.7G/109G     ││ tx                          █ 389B   ││      min 200 max 1000 bst 1000       │
│ /boot/efi ······   1% 6.3M/511M      ││ tcp  est 13 lis 11 tw 2 cw 0         ││ rc6 47.9%  temp 58°C                 │
│                                      ││                                      ││ fdinfo: no DRM clients               │
│                                      ││                                      ││ gpu                           n/a    │
```

## GPU support

| GPU | Linux | Windows |
|---|---|---|
| NVIDIA | `nvidia-smi` | `nvidia-smi` |
| AMD | amdgpu sysfs (busy %, VRAM, temperature, power) | Windows GPU performance counters |
| Intel | per-process DRM fdinfo engine counters (no root needed) + i915 clock speeds | Windows GPU performance counters |

If no usage counters are readable, the friendly edition estimates GPU activity from clock speed and says so.

## Project layout

```
src/librestatistics/  the package: friendly.py, dev.py, classic.py
snap/                 snapcraft.yaml + desktop launchers
debian/               Debian packaging for the .deb / apt repository
packaging/windows/    PyInstaller build script + Inno Setup installer
packaging/apt/        script that builds the signed apt repository
packaging/linux/      .desktop launchers and icon
scripts/              bump_version.py
.github/workflows/    CI (Linux + Windows) and the release pipeline
```

Releasing and publishing to each store: see [docs/RELEASING.md](docs/RELEASING.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

GPL-2.0-or-later. See [LICENSE](LICENSE).
