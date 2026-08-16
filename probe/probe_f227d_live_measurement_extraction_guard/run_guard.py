#!/usr/bin/env python3
"""CLI smoke test for the extraction guard."""

import subprocess, sys
from pathlib import Path
from _core import aclose

REPO_ROOT = Path(__file__).resolve().parent.parent  # probe_f227d/ → hledac/universal/

result = subprocess.run(
    [sys.executable, "tools/live_measurement_extraction_guard.py",
     "--repo-root", str(REPO_ROOT),
     "--output-json", "probe_f227d_live_measurement_extraction_guard/live_extraction_guard.json",
     "--output-md", "probe_f227d_live_measurement_extraction_guard/LIVE_EXTRACTION_GUARD.md"],
    capture_output=True, text=True, timeout=30
    )
print(result.stdout)
print(result.stderr)
print("Exit code:", result.returncode)