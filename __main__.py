"""
Hledac Universal - Convenience Entry Point Wrapper
=================================================

Sprint F350M-R: Single Canonical Entry Point

This file is a thin re-export shim that works when invoked as:
    python __main__.py --sprint "query"   (from repo root)

Canonical bootstrap logic lives in:
    hledac/universal/__main__.py  (hledac.universal package __main__)

Usage (all equivalent):
    python -m hledac.universal --sprint "query"
    python __main__.py --sprint "query"   (from repo root)
    hledac --sprint "query"              (console script)
"""

from __future__ import annotations

import pathlib
import runpy
import sys

# Resolve the canonical package __main__.py path.
# The canonical file manages its own sys.path for local module resolution.
_src_root = pathlib.Path(__file__).parent.resolve()
_package_main = _src_root / "hledac" / "universal" / "__main__.py"

# Delegate to the canonical package __main__ via runpy to avoid any
# circular import issues that can confuse type checkers / IDEs.
_main_mod = runpy.run_path(str(_package_main), run_name="__hledac_main__")
_main_mod["main"]()
