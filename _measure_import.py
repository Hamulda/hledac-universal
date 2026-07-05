#!/usr/bin/env python3
"""Measure import costs for the full hledac.universal entry point."""
import subprocess, sys, time

py = sys.executable
TESTS = [
    # Base import only
    ("base", [py, "-c", """
import time; t0=time.perf_counter()
import hledac.universal
t1=time.perf_counter()
print('BASE:%.1fms' % ((t1-t0)*1000))
"""]),
    # python -m hledac.universal --help (fast path)
    ("--help", [py, "-m", "hledac.universal", "--help"]),
    # python -m hledac.universal --list-presets
    ("--list-presets", [py, "-m", "hledac.universal", "--list-presets"]),
    # --dry-run (triggers config loading but no MLX)
    ("--dry-run", [py, "-m", "hledac.universal", "--dry-run"]),
]

for name, cmd in TESTS:
    print(f"\n=== {name} ===")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                      cwd="/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
    print("STDOUT:", r.stdout[:300] if r.stdout else "(empty)")
    print("STDERR:", r.stderr[:200] if r.stderr else "(empty)")
    print("RC:", r.returncode)
