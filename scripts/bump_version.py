#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Set the release version everywhere it is recorded.

    python3 scripts/bump_version.py 2.1.0 "Short note for the Debian changelog"

Updates src/librestatistics/__init__.py, snap/snapcraft.yaml and debian/changelog.
The Windows installer and Python packages read the version from the package.
Then: git commit -am "Release 2.1.0" && git tag v2.1.0 && git push --follow-tags
"""

import re
import subprocess
import sys
from email.utils import formatdate
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAINTAINER = "LibreStatistics contributors <147175691+juujbyg@users.noreply.github.com>"


def sub(path, pattern, repl):
    p = ROOT / path
    text, n = re.subn(pattern, repl, p.read_text(), count=1, flags=re.M)
    if n != 1:
        sys.exit(f"could not find version in {path}")
    p.write_text(text)


def main():
    if len(sys.argv) < 2 or not re.fullmatch(r"\d+\.\d+\.\d+", sys.argv[1]):
        sys.exit(__doc__)
    version = sys.argv[1]
    note = sys.argv[2] if len(sys.argv) > 2 else f"Release {version}."

    sub("src/librestatistics/__init__.py", r'^__version__ = ".*"$', f'__version__ = "{version}"')
    sub("snap/snapcraft.yaml", r"^version: .*$", f"version: '{version}'")

    changelog = ROOT / "debian/changelog"
    entry = (f"librestatistics ({version}) unstable; urgency=medium\n\n  * {note}\n\n"
             f" -- {MAINTAINER}  {formatdate(localtime=True)}\n\n")
    changelog.write_text(entry + changelog.read_text())

    print(f"Version set to {version}. Review with: git -C {ROOT} diff")
    subprocess.run(["git", "-C", str(ROOT), "diff", "--stat"])


if __name__ == "__main__":
    main()
