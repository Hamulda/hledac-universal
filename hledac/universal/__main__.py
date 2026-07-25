"""
Hledac Universal - Package __main__ Shim
=======================================

Console-script entry point bridge.

When invoked via the `hledac` console script (pyproject.toml [project.scripts]):
    hledac --sprint "query"
        ↓
    hledac.universal.__main__.main()   ← THIS file (package __main__.py)
        ↓
    root __main__.py → main()           ← delegates to root

When invoked via `python -m hledac.universal`:
    python -m hledac.universal
        ↓
    root __main__.py → main()           ← direct, bypasses this shim

JIT: Python 3.14+ auto-enables JIT via PYTHON_JIT=1 in [tool.uv].env.
No sys.execv restart needed — eliminates cold-start penalty, pytest fixture
duplication, PyCharm debugger issues, and KeyboardInterrupt zombie processes.
"""

# ── JIT bootstrap: PYTHON_JIT=1 via [tool.uv].env in pyproject.toml ──────────
# Python 3.14+ automatically enables JIT when PYTHON_JIT=1 env var is set.
# No sys.execv restart needed — eliminates +80-120ms cold-start penalty,
# pytest fixture duplication, PyCharm debugger issues, and zombie processes.
# HLEDAC_NO_JIT=1 still respected as opt-out for CI/edge cases.
#
# Canonical entry: `hledac` console script → this file → root __main__.py
# The root __main__.py holds all real bootstrap logic (dotenv, logging,
# LMDB boot guard, CLI dispatch).

# ── Delegate to the canonical root main ─────────────────────────────────────────
# NOTE: cannot use `from hledac.universal.__main__` — that resolves to THIS
# file (circular). Use runpy to load the root __main__.py by file path.
import pathlib as _pathlib
import runpy as _runpy

_root_main = _pathlib.Path(__file__).resolve().parent.parent.parent / "__main__.py"
_main_mod = _runpy.run_path(str(_root_main), run_name="__hledac_main__")
_main_mod["main"]()
