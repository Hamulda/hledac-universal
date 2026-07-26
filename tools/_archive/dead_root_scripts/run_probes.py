#!/usr/bin/env python3
"""Run observability probe tests."""
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/probe_p12_observability/", "-v", "--timeout=30"],
    capture_output=True,
    text=True,
    cwd="/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"
)
print(result.stdout)
print(result.stderr)
sys.exit(result.returncode)
