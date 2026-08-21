"""
Sprint F350M-R: Structured exit-code regression tests.

Verifies the catch-all envelope in both `__main__.py` and `core/__main__.py`
translates failure modes into deterministic, distinguishable exit codes
that CI/CD and monitoring systems can branch on.

Exit code convention (Sprint F350M-R):
    0   = clean success
    1   = runtime error (unexpected)
    2   = config/validation error (e.g. F221-ABORT windup guard)
    3   = programmer error / regression (NameError, AttributeError, ImportError)
    130 = SIGINT (KeyboardInterrupt)

These tests run as subprocesses so the actual sys.exit() exit code is
observable — pytest process traps (SystemExit) would mask the code.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
HLEDAC_PARENT = "/Users/vojtechhamada/PycharmProjects/Hledac"
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python"

# Pick the venv interpreter if present, else fall back to current.
PYTHON = str(VENV_PY) if VENV_PY.exists() else sys.executable


def _cli_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build subprocess env for testing hledac.universal exit codes.

    NOTE: The editable install (uv) produces an empty MAPPING in the
    __editable__ finder, so the subprocess cannot resolve hledac.universal
    without explicit PYTHONPATH. We add HLEDAC_PARENT so the namespace
    package hledac/ is found — the actual package path (universal/) is
    then discovered via hledac/__init__.py namespace arrangement.
    """
    env = os.environ.copy()
    # Remove stale PYTHONPATH to avoid dual-path import conflicts.
    env.pop("PYTHONPATH", None)
    # Add HLEDAC_PARENT so 'hledac' namespace package is found.
    # universal/ is a sibling under hledac/, found via namespace __init__.
    env["PYTHONPATH"] = HLEDAC_PARENT
    # Force default profile so the F221-ABORT windup guard path is exercised.
    env["HLEDAC_ACQUISITION_PROFILE"] = "default"
    # Silence mlx/duckdb warmup chatter — exit code is what we test.
    env["HLEDAC_LOG_LEVEL"] = "ERROR"
    if extra:
        env.update(extra)
    return env


def _run(code: str, args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Run `python -c <code> <args>` and return the completed process."""
    return subprocess.run(
        [PYTHON, "-c", code, *args],
        env=_cli_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Programmer-error path → exit 3
# ---------------------------------------------------------------------------


def test_nameerror_in_run_sprint_exits_3() -> None:
    """A NameError raised inside run_sprint() must propagate to exit code 3.

    The structured envelope in __main__.main() catches (NameError,
    AttributeError, ImportError) explicitly and calls _fatal(e, code=3),
    distinct from generic Exception → exit 1.
    """
    patch_script = textwrap.dedent(
        """
        import sys
        # Use --duration 300 to pass the F221-ABORT windup guard
        # (effective_windup=180 → active_window=120 > MIN_ACTIVE_WINDOW_S=30).
        # The NameError must fire inside run_sprint(), not be intercepted
        # by the windup pre-flight.
        sys.argv = [
            "hledac.universal",  # argv[0] = program name; parser.parse_args uses sys.argv[1:]
            "--sprint", "exit_code_probe", "--duration", "300",
        ]
        # Patch the canonical sprint owner: __main__.py does
        #   from .core.__main__ import run_sprint as _core_run_sprint
        # so we must patch `core.__main__.run_sprint`, not `core.run_sprint`.
        from hledac.universal._core import __main__ as _core_main
        def _boom(*_a, **_kw):
            raise NameError("exit_code_probe: mocked regression")
        _core_main.run_sprint = _boom
        from hledac.universal.__main__ import main
        sys.exit(main())
        """
    )
    proc = _run(patch_script, [])
    assert proc.returncode == 3, (
        f"expected exit 3 (programmer error), got {proc.returncode}\n"
        f"stdout: {proc.stdout[-500:]}\nstderr: {proc.stderr[-500:]}"
    )
    # The _MAIN_FATAL prefix is required for log-parser compatibility.
    assert "_MAIN_FATAL" in proc.stderr or "_MAIN_FATAL" in proc.stdout, (
        f"_MAIN_FATAL prefix missing from logs\nstdout: {proc.stdout[-500:]}\nstderr: {proc.stderr[-500:]}"
    )


def test_importerror_in_run_sprint_exits_3() -> None:
    """An ImportError raised inside run_sprint() must also exit 3."""
    patch_script = textwrap.dedent(
        """
        import sys
        sys.argv = [
            "hledac.universal",
            "--sprint", "exit_code_probe", "--duration", "300",
        ]
        from hledac.universal._core import __main__ as _core_main
        def _boom(*_a, **_kw):
            raise ImportError("exit_code_probe: missing optional dep")
        _core_main.run_sprint = _boom
        from hledac.universal.__main__ import main
        sys.exit(main())
        """
    )
    proc = _run(patch_script, [])
    assert proc.returncode == 3, (
        f"expected exit 3 (programmer error), got {proc.returncode}\nstderr: {proc.stderr[-500:]}"
    )


# ---------------------------------------------------------------------------
# Config/validation error path → exit 2
# ---------------------------------------------------------------------------


def test_windup_guard_short_duration_exits_2() -> None:
    """--duration below the active-window floor must exit 2 (F221-ABORT).

    F221-ABORT: 30% of duration, clamped [30, 180]. For duration=30,
    raw=9, effective=30, active=0 → 0 < MIN_ACTIVE_WINDOW_S=30 → exit 2.
    """
    proc = subprocess.run(
        [
            PYTHON,
            "-m",
            "hledac.universal",
            "--sprint",
            "exit_code_probe",
            "--duration",
            "30",
        ],
        env=_cli_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2, (
        f"expected exit 2 (F221-ABORT windup guard), got {proc.returncode}\n"
        f"stdout: {proc.stdout[-500:]}\nstderr: {proc.stderr[-500:]}"
    )
    # The guard logs F221-ABORT before exiting — keep log parser happy.
    # F265C: also accept HARD_BLOCK (swap guard) which exits 2 before windup guard runs.
    combined = proc.stdout + proc.stderr
    assert "F221-ABORT" in combined or "windup" in combined.lower() or "HARD_BLOCK" in combined, (
        f"windup guard diagnostic missing from logs\ncombined: {combined[-500:]}"
    )


# ---------------------------------------------------------------------------
# Sanity: clean success path still exits 0
# ---------------------------------------------------------------------------


def test_help_exits_0() -> None:
    """Sanity check: --help path exits 0 (verify the new envelope didn't break it)."""
    proc = subprocess.run(
        [PYTHON, "-m", "hledac.universal", "--help"],
        env=_cli_env(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"expected exit 0 (--help), got {proc.returncode}\nstderr: {proc.stderr[-500:]}"


# ---------------------------------------------------------------------------
# SystemExit / KeyboardInterrupt semantics
# ---------------------------------------------------------------------------


def test_keyboardinterrupt_exits_130() -> None:
    """KeyboardInterrupt must exit 130 (SIGINT convention), not 0.

    Regression: pre-F350M-R, root __main__.main() did sys.exit(0) on
    KeyboardInterrupt, which masked operator interrupts as success.
    """
    patch_script = textwrap.dedent(
        """
        import sys
        sys.argv = [
            "hledac.universal",
            "--sprint", "exit_code_probe", "--duration", "300",
        ]
        from hledac.universal._core import __main__ as _core_main
        def _boom(*_a, **_kw):
            raise KeyboardInterrupt()
        _core_main.run_sprint = _boom
        from hledac.universal.__main__ import main
        sys.exit(main())
        """
    )
    proc = _run(patch_script, [])
    assert proc.returncode == 130, f"expected exit 130 (SIGINT), got {proc.returncode}\nstderr: {proc.stderr[-500:]}"


def test_systemexit_not_swallowed_by_catchall() -> None:
    """sys.exit(2) raised inside run_sprint() must propagate as exit 2, not exit 1.

    The structured envelope has `except SystemExit: raise` so a deliberate
    sys.exit(N) from deep code is not turned into a generic exit 1.
    """
    patch_script = textwrap.dedent(
        """
        import sys
        sys.argv = [
            "hledac.universal",
            "--sprint", "exit_code_probe", "--duration", "300",
        ]
        from hledac.universal._core import __main__ as _core_main
        def _boom(*_a, **_kw):
            sys.exit(2)
        _core_main.run_sprint = _boom
        from hledac.universal.__main__ import main
        sys.exit(main())
        """
    )
    proc = _run(patch_script, [])
    assert proc.returncode == 2, (
        f"expected sys.exit(2) to propagate, got {proc.returncode}\nstderr: {proc.stderr[-500:]}"
    )
