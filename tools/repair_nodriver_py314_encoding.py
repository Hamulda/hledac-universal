#!/usr/bin/env python3
"""
Repair script for nodriver CDP files with CRLF line endings.

Python 3.14 enforces stricter line ending rules. CDP files generated from
the Chrome DevTools Protocol specification may contain CRLF line endings
which can cause import-time issues on some Python 3.14 configurations.

This script:
- Locates nodriver via importlib.util.find_spec
- Finds the CDP network.py file (known to have CRLF in nodriver 0.48.x)
- Converts CRLF to LF (the known fix for this specific issue)
- Is idempotent: returns exit code 0 if already fixed or after fixing
- Never runs automatically; call explicitly after install/update

Usage:
    python tools/repair_nodriver_py314_encoding.py
    echo $?  # 0 = success (fixed or already correct)
"""

from __future__ import annotations

import importlib.util
import sys
import os

# CDP files known to have CRLF issues in nodriver 0.48.x
_CDP_FILES_TO_REPAIR = [
    "cdp/network.py",
]

_ALREADY_FIXED: list[str] = []
_PATCHED: list[str] = []


def _find_nodriver_install() -> str | None:
    """Find the nodriver installation path via importlib."""
    spec = importlib.util.find_spec("nodriver")
    if spec is None or spec.submodule_search_locations is None:
        return None
    # nodriver is a package, use its __init__.py location
    return os.path.dirname(spec.origin) if spec.origin else None


def _has_crlf(file_path: str) -> bool:
    """Check if a file contains CRLF line endings."""
    try:
        with open(file_path, "rb") as fh:
            return b"\r\n" in fh.read()
    except OSError:
        return False


def _repair_file(file_path: str) -> bool:
    """
    Repair a single file by converting CRLF to LF.

    Returns:
        True if the file was patched,
        False if it was already clean or couldn't be read.
    """
    if not os.path.exists(file_path):
        return False

    if not _has_crlf(file_path):
        _ALREADY_FIXED.append(file_path)
        return False

    # Read with universal newlines, write with LF only
    with open(file_path, "r", newline=None) as fh:
        content = fh.read()

    with open(file_path, "w", newline="\n") as fh:
        fh.write(content)

    _PATCHED.append(file_path)
    return True


def _main() -> int:
    nodriver_root = _find_nodriver_install()

    if nodriver_root is None:
        print("nodriver not installed — nothing to repair")
        return 0

    print(f"nodriver root: {nodriver_root}")

    for rel_path in _CDP_FILES_TO_REPAIR:
        full_path = os.path.join(nodriver_root, rel_path)
        if not os.path.exists(full_path):
            print(f"  [skip] {rel_path} not found")
            continue
        if _repair_file(full_path):
            print(f"  [patched] {rel_path}")
        else:
            print(f"  [ok]     {rel_path}")

    if _PATCHED:
        print(f"\nRepaired {len(_PATCHED)} file(s):")
        for f in _PATCHED:
            print(f"  - {os.path.basename(os.path.dirname(f))}/{os.path.basename(f)}")
        return 0

    if _ALREADY_FIXED:
        print("\nAll files already clean — no repair needed")
        return 0

    print("\nNo CRLF issues detected")
    return 0


if __name__ == "__main__":
    sys.exit(_main())