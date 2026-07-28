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
# The package lives at hledac/universal/ (relative to repo root).
_src_root = pathlib.Path(__file__).parent.resolve()
_package_main = _src_root / "hledac" / "universal" / "__main__.py"

# Delegate to the canonical package __main__ via importlib.
# Avoids runpy.run_path recursion trap when this file IS __main__.
import importlib.util
_spec = importlib.util.spec_from_file_location("hledac.universal.__main__", _package_main)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
# Set package context so relative imports work
_module.__package__ = "hledac.universal"  # type: ignore[attr-defined]
_spec.loader.exec_module(_module)
_module.main()
