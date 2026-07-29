"""
hledac.universal.__main__ — Canonical CLI entry point (argparse, F350M-R A-05).

Invoke as:
    python -m hledac.universal --sprint "query"
    python -m hledac.universal sprint --sprint "query"
    python -m hledac.universal pivot --pivot "ransomware"
    python -m hledac.universal ct --ct-pivot example.com
    hledac --sprint "query"   (console script)
"""

from __future__ import annotations

import sys

from hledac.universal.cli.parser import async_main

if __name__ == "__main__":
    sys.exit(async_main())
