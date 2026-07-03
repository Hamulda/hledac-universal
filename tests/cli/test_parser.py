"""tests/cli/test_parser.py — CLI parser tests."""
from __future__ import annotations

import subprocess
import sys
from textwrap import dedent


def _run(script: str, _argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, "-c", dedent(script)],
        input=b"",
        capture_output=True,
        timeout=30,
    )


# --------------------------------------------------------------------------- #
# Parser construction
# --------------------------------------------------------------------------- #

def test_build_parser_import() -> None:
    from cli.parser import build_parser
    p = build_parser()
    assert p is not None


def test_build_parser_legacy_sprint_args() -> None:
    """Legacy flat --sprint syntax parses correctly."""
    from cli.parser import build_parser
    import sys
    sys.argv = ["hledac", "--sprint", "LockBit ransomware", "--duration", "300"]
    p = build_parser()
    args = p.parse_args()
    assert args.sprint == "LockBit ransomware"
    assert args.duration == 300.0
    assert args._subcommand is None  # flat syntax — no subcommand


def test_build_parser_subcommand_sprint() -> None:
    """Modern sprint subcommand parses correctly."""
    from cli.parser import build_parser
    import sys
    sys.argv = ["hledac", "sprint", "--sprint", "CVE-2024", "--duration", "600"]
    p = build_parser()
    args = p.parse_args()
    assert args.sprint == "CVE-2024"
    assert args.duration == 600.0
    assert args._subcommand == "sprint"


def test_build_parser_subcommand_pivot() -> None:
    """Pivot subcommand parses correctly."""
    from cli.parser import build_parser
    import sys
    sys.argv = ["hledac", "pivot", "--pivot", "ransomware", "--pivot-k", "20"]
    p = build_parser()
    args = p.parse_args()
    assert args.pivot == "ransomware"
    assert args.pivot_k == 20
    assert args._subcommand == "pivot"


def test_build_parser_subcommand_ct() -> None:
    """CT subcommand parses correctly."""
    from cli.parser import build_parser
    import sys
    sys.argv = ["hledac", "ct", "--ct-pivot", "evilcorp.com"]
    p = build_parser()
    args = p.parse_args()
    assert args.ct_pivot == "evilcorp.com"
    assert args._subcommand == "ct"


def test_build_parser_all_common_args() -> None:
    """All common sprint args are parsed correctly."""
    from cli.parser import build_parser
    import sys
    sys.argv = [
        "hledac", "sprint",
        "--sprint", "test",
        "--duration", "900",
        "--windup-lead", "60",
        "--export-dir", "/tmp/hledac-reports",
        "--aggressive",
        "--ui",
        "--deep-probe",
        "--force",
        "--acquisition-profile", "deep_osint_m1",
        "--preset", "osint",
    ]
    p = build_parser()
    args = p.parse_args()
    assert args.sprint == "test"
    assert args.duration == 900.0
    assert args.windup_lead == 60.0
    assert args.export_dir == "/tmp/hledac-reports"
    assert args.aggressive is True
    assert args.ui is True
    assert args.deep_probe is True
    assert args.force is True
    assert args.acquisition_profile == "deep_osint_m1"
    assert args.preset == "osint"


def test_build_parser_no_aggressive() -> None:
    """--no-aggressive disables aggressive mode."""
    from cli.parser import build_parser
    import sys
    sys.argv = ["hledac", "--sprint", "test", "--no-aggressive"]
    p = build_parser()
    args = p.parse_args()
    assert args.aggressive is False


# --------------------------------------------------------------------------- #
# Exit codes via subprocess (real sys.argv)
# --------------------------------------------------------------------------- #

def test_help_exits_0() -> None:
    """--help exits 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "hledac.universal", "--help"],
        capture_output=True, timeout=10,
    )
    assert proc.returncode == 0
    assert b"usage:" in proc.stdout.lower() or b"usage:" in proc.stderr.lower()


def test_help_shows_legacy_and_modern_usage() -> None:
    """--help shows both legacy and modern CLI syntax."""
    proc = subprocess.run(
        [sys.executable, "-m", "hledac.universal", "--help"],
        capture_output=True, timeout=10,
    )
    combined = proc.stdout + proc.stderr
    assert b"sprint" in combined
    assert b"pivot" in combined
    assert b"ct" in combined


def test_ct_subcommand_routes_correctly() -> None:
    """CT subcommand is routed to _dispatch_ct without crashing on import."""
    # This test verifies the CLI dispatches ct subcommand without
    # crashing on ImportError / arg mismatch in dispatch.
    # Actual ct logic would need network access — just verify dispatch
    # doesn't hard-crash.
    proc = subprocess.run(
        [sys.executable, "-m", "hledac.universal", "ct", "--ct-pivot", "example.com"],
        capture_output=True, timeout=10,
    )
    # Should exit 1 (ct not implemented in core) not 3 (import error)
    assert proc.returncode in (0, 1), f"unexpected exit {proc.returncode}: {proc.stderr[-200:]}"


def test_pivot_subcommand_routes_correctly() -> None:
    """Pivot subcommand routes correctly."""
    proc = subprocess.run(
        [sys.executable, "-m", "hledac.universal", "pivot", "--pivot", "test"],
        capture_output=True, timeout=10,
    )
    # Should not hard-fail on ImportError
    assert proc.returncode in (0, 1), f"unexpected exit {proc.returncode}: {proc.stderr[-200:]}"


def test_unknown_subcommand_exits_2() -> None:
    """Unknown subcommand exits 2 (config error)."""
    proc = subprocess.run(
        [sys.executable, "-m", "hledac.universal", "unknown_cmd"],
        capture_output=True, timeout=10,
    )
    # argparse error → exit 2
    assert proc.returncode == 2


def test_empty_args_shows_help_and_exits_0() -> None:
    """Running with no args shows help and exits 0."""
    proc = subprocess.run(
        [sys.executable, "-m", "hledac.universal"],
        capture_output=True, timeout=10,
    )
    assert proc.returncode == 0
