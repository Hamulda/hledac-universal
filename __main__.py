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

# Import main as _main — do NOT call it here.
# The `if __name__ == "__main__"` guard in this file runs when this module
# is imported as the __main__ script (python -m hledac.universal), but it also
# runs when a test does `from hledac.universal.__main__ import main; main()`
# because sys.modules["hledac.universal.__main__"].__name__ == "__main__".
# We avoid calling main() at module scope to prevent double-execution.
from hledac.universal.cli.parser import main as _main
from _core import aclose

__all__ = ["main"]


def main() -> int:
    """Public entry point — mirrors cli.parser.main() for __main__ guard."""
    return _main()


if __name__ == "__main__":
    sys.exit(main())
