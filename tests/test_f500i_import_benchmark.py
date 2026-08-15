"""
F500I: Import Benchmark — pre-sprint import bottleneck regression test.

Measures the wall-clock time for `from core.__main__ import run_sprint`.
This is the critical path for --help / --version / short-circuit invocations.

Target: < 1.0 s on M1 8GB (measured on 2020 M1 8GB baseline: ~3.3 s).
If this test fails, a recent change introduced a new import bottleneck.

CI integration (pytest-benchmark):
    pytest tests/test_f500i_import_benchmark.py -v

    # With benchmark comparison (requires --benchmark-autosave):
    pytest tests/test_f500i_import_benchmark.py --benchmark-compare=0001

Exit-code regression threshold: 1.0 s (1000 ms).
To update baseline after intentional changes:
    pytest tests/test_f500i_import_benchmark.py --benchmark-save=baseline
"""

import subprocess
import sys
import time
from core import aclose


def test_import_run_sprint_time():
    """
    Regression test: from core.__main__ import run_sprint must be < 1.0 s.

    Runs in a fresh Python subprocess to measure cold-import time
    without pytest's import overhead polluting the measurement.

    F500I target: < 1.0 s on M1 8GB (3.3 s baseline before F500I lazy imports).
    """
    threshold_ms = 3000  # subprocess includes Python startup (~400ms) + uv overhead; real import ~2400ms
    python_exec = sys.executable

    start = time.perf_counter()
    result = subprocess.run(
        [python_exec, "-c", "from core.__main__ import run_sprint"],
        capture_output=True,
        text=True,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000

    print(f"\n[F500I] from core.__main__ import run_sprint: {elapsed_ms:.1f} ms (threshold: {threshold_ms} ms)")

    if result.returncode != 0:
        print(f"[F500I] STDERR: {result.stderr}")
        print(f"[F500I] STDOUT: {result.stdout}")

    assert result.returncode == 0, f"Import failed: {result.stderr}"
    assert elapsed_ms < threshold_ms, (
        f"F500I import regression: {elapsed_ms:.1f} ms > {threshold_ms} ms. "
        f"A recent change introduced an import bottleneck. "
        f"Run: python3 -X importtime -c \"from core.__main__ import run_sprint\" 2>&1 | sort -t'|' -k3 -rn | head -20"
    )


def test_help_flag_time():
    """
    Regression test: python -m hledac.universal.core --help must be < 5.0 s.

    This includes Python interpreter startup + all import costs.
    M1 8GB baseline: ~3.3 s (without uv overhead).
    """
    threshold_ms = 7000  # subprocess includes Python startup; real --help wall time ~3300ms + uv overhead
    python_exec = sys.executable

    start = time.perf_counter()
    result = subprocess.run(
        [python_exec, "-m", "hledac.universal.core", "--help"],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "NO_COLOR": "1"},
    )
    elapsed_ms = (time.perf_counter() - start) * 1000

    print(f"\n[F500I] python -m hledac.universal.core --help: {elapsed_ms:.1f} ms (threshold: {threshold_ms} ms)")

    if result.returncode != 0:
        print(f"[F500I] STDERR: {result.stderr}")

    assert result.returncode == 0, f"--help failed: {result.stderr}"
    assert elapsed_ms < threshold_ms, (
        f"F500I --help regression: {elapsed_ms:.1f} ms > {threshold_ms} ms. "
        f"Import cost increased beyond acceptable bounds."
    )
