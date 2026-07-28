"""Trace lock registrations during test run."""
import sys
import os

# Ensure the project root is on the path
sys.path.insert(0, os.getcwd())

# Pre-import locks module and patch
import hledac.universal.core.locks as locks_mod

_orig = locks_mod._register_lock
_registrations = []

def tracer(category, lock, name, frame_info):
    _registrations.append((name, frame_info, hex(id(lock))))
    return _orig(category, lock, name, frame_info)

locks_mod._register_lock = tracer

import pytest
exit_code = pytest.main(["-x", "tests/test_sprint_scheduler.py", "-q", "--tb=short"])

# Write registrations to file
with open("/tmp/lock_registrations.txt", "w") as f:
    for name, frame, lock_hex in _registrations:
        f.write(f"{name} [{lock_hex}] @ {frame}\n")

print(f"Captured {len(_registrations)} registrations", file=sys.stderr)
sys.exit(exit_code)
