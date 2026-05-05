#!/usr/bin/env python3
"""
assert_py314_runtime.py — F214ENV Runtime Guard

Validates the active Python interpreter is Python 3.14+ with required
features: annotationlib, uuid.uuid7, concurrent.futures.InterpreterPoolExecutor.

Exit codes:
    0  = Python 3.14+ with all features available
    64 = NOT Python 3.14+ (version guard failure)
    65 = Python 3.14+ but missing one or more required features

Usage:
    python tools/assert_py314_runtime.py
    source .venv/bin/activate && python tools/assert_py314_runtime.py
"""

import sys


def _check_annotationlib():
    """Guard: annotationlib (new in 3.14)."""
    try:
        import annotationlib
        ver = getattr(annotationlib, "__version__", "no version attr")
        print(f"annotationlib: available ({ver})")
        return True
    except ImportError:
        print("ERROR: annotationlib not available (requires Python 3.14+)")
        return False


def _check_uuid7():
    """Guard: uuid.uuid7 (new in 3.14)."""
    try:
        import uuid
        if not hasattr(uuid, "uuid7"):
            print("ERROR: uuid.uuid7 not available (requires Python 3.14+)")
            return False
        print(f"uuid.uuid7:  available")
        return True
    except ImportError:
        print("ERROR: uuid module not available")
        return False


def _check_interpreter_pool():
    """Guard: concurrent.futures.InterpreterPoolExecutor (new in 3.14)."""
    try:
        from concurrent.futures import InterpreterPoolExecutor as IPE  # noqa: F401
        print(f"InterpreterPoolExecutor: available")
        return True
    except ImportError:
        print("ERROR: concurrent.futures.InterpreterPoolExecutor not available (requires Python 3.14+)")
        return False


def main() -> int:
    print(f"Executable: {sys.executable}")
    print(f"Version:     {sys.version}")

    # Guard: must be Python 3.14+
    if sys.version_info < (3, 14):
        print(f"ERROR: Requires Python 3.14+ (current: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro})")
        return 64

    ok = _check_uuid7() and _check_annotationlib() and _check_interpreter_pool()
    if not ok:
        return 65

    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())