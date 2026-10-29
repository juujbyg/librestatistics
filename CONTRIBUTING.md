# Contributing to LibreStatistics

Thanks for helping. LibreStatistics is part of the LibreProject family. It is pure Python with two
runtime dependencies (`psutil` and `rich`) and runs on Linux and Windows.

## Setup

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
librestatistics --once && librestatistics-dev --once && librestatistics-classic --once
librestatistics-dev --json
```

CI runs the same smoke test on Ubuntu and Windows with Python 3.9 and 3.13.

## Ground rules

- Python >= 3.9. Runtime dependencies are `psutil` and `rich` only; add nothing else.
- Every source file starts with `# SPDX-License-Identifier: GPL-2.0-or-later` (after the shebang,
  if there is one).
- Platform-specific code goes in `src/librestatistics/_compat.py` and degrades to "no data"
  instead of raising. A missing sensor or GPU must never crash an edition.
- No root needed: read only what an ordinary user can read.
- User-facing messages are in English. The friendly edition uses plain words, not jargon.
- Text files use LF line endings, except Windows scripts (enforced by `.gitattributes`).

## Layout

`friendly.py`, `dev.py` and `classic.py` are the three editions, each with its own `main()`;
`__main__.py` dispatches `python -m librestatistics [friendly|dev|classic]`. Packaging lives in
`debian/`, `snap/` and `packaging/`. Releasing is described in [docs/RELEASING.md](docs/RELEASING.md);
use `scripts/bump_version.py` to change the version.

## Reporting a problem

Open an issue with your OS, Python version, the edition you ran, and the output of
`librestatistics-dev --json`.
