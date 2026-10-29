"""`python -m sysglance [friendly|dev|classic] [options]`"""

import sys

EDITIONS = ("friendly", "dev", "classic")


def main():
    edition = "friendly"
    if len(sys.argv) > 1 and sys.argv[1] in EDITIONS:
        edition = sys.argv.pop(1)
    if edition == "dev":
        from sysglance.dev import main as run
    elif edition == "classic":
        from sysglance.classic import main as run
    else:
        from sysglance.friendly import main as run
    run()


if __name__ == "__main__":
    main()
