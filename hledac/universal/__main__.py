"""
Hledac Universal - Package __main__ Shim
=======================================

PEP 744 Tier 2 JIT self-restart bridge.

When invoked via the console script entry point:
    hledac --sprint "query"
        ↓
    python -m hledac.universal          ← python -m path
        ↓
    hledac.universal.__main__.main()   ← THIS file (package __main__.py)

Problem: python -m hledac.universal loads hledac/universal/__main__.py
as __main__, not the root __main__.py. The root __main__.py has PEP 744
JIT guard under `if __name__ == "__main__"` — that guard is SKIPPED
when this file is loaded as the package __main__, because the entry
is a direct call to main(), not a script execution.

Fix: This file holds the PEP 744 guard as its FIRST action (module-level,
before any other imports), then delegates to the root main().

Usage:
    python -m hledac.universal --sprint "query"
    hledac --sprint "query"           ← console script → this file
"""

# ── PEP 744 Tier 2 JIT enablement ─────────────────────────────────────────────
# This MUST be the very first runtime action — before ANY other module import.
import os as _os
import sys as _sys

if not _os.environ.get("HLEDAC_NO_JIT"):
    if hasattr(_sys, "jit") and not _sys.jit:
        _executable = _sys.executable
        _sys.execv(_executable, [_executable, "-X", "jit"] + _sys.argv)

# ── Delegate to the canonical root main ─────────────────────────────────────────
# The root __main__.py holds all the real bootstrap logic (dotenv, logging,
# LMDB boot guard, CLI dispatch). This file only exists to bridge the
# console-script → PEP 744 gap.
# NOTE: cannot use `from hledac.universal.__main__` — that resolves to THIS
# file (circular). Use runpy to load the root __main__.py by file path.
import runpy as _runpy
import pathlib as _pathlib

_root_main = _pathlib.Path(__file__).resolve().parent.parent.parent / "__main__.py"
_main_mod = _runpy.run_path(str(_root_main), run_name="__hledac_main__")
_main = _main_mod["main"]
_main()
