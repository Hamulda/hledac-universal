#!/usr/bin/env python3
"""
F206BH LIVE SPRINT MEASUREMENT HARNESS

Canonical live sprint measurement: run 180s/300s/600s measured sprints and capture
metrics reproducibly.

Safety invariants:
- Default is --dry-run (no live sprint)
- Live execution requires explicit --live flag
- No stealth default, no aggressive default
- Duration < 180s blocked unless --allow-smoke passed
- No live network during tests
- No MLX model load during tests

Profiles:
  smoke180  → 180s sprint (smoke test)
  active300 → 300s sprint (standard)
  active600 → 600s sprint (extended)

Usage:
  # Dry-run (default, hermetic)
  python benchmarks/live_sprint_measurement.py --profile smoke180 --query "LockBit ransomware"
  python benchmarks/live_sprint_measurement.py --profile active300 --query "APT29" --dry-run

  # Live execution (requires --live)
  python benchmarks/live_sprint_measurement.py --profile active300 --query "LockBit" --live
  python benchmarks/live_sprint_measurement.py --profile active600 --query "ransomware" --live --output-json /tmp/live_measure.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

PROFILE_DURATION: dict[str, int] = {
    "smoke180": 180,
    "active300": 300,
    "active600": 600,
}

MIN_DURATION_S = 180

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

class RunMode(Enum):
    DRY_RUN = "dry_run"
    LIVE = "live"


class MeasurementStatus(Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass
class LiveMeasurementResult:
    # Identity
    measurement_id: str
    sprint_id: str | None
    mode: RunMode
    status: MeasurementStatus

    # Timing
    start_time_iso: str | None
    end_time_iso: str | None
    planned_duration_s: float | None
    actual_duration_s: float | None

    # Config
    query: str
    profile: str
    duration_s: int = 0
    aggressive_mode: bool = False
    deep_probe: bool = False

    # UMA
    uma_pre_used_gib: float | None = None
    uma_pre_swap_gib: float | None = None
    uma_pre_state: str | None = None
    uma_post_used_gib: float | None = None
    uma_post_swap_gib: float | None = None
    uma_post_state: str | None = None

    # Sprint results (live mode only)
    findings_count: int | None = None
    cycles_completed: int | None = None
    cycles_started: int | None = None
    accepted_findings: int | None = None
    runtime_truth: str | None = None
    timing_truth: str | None = None
    checkpoint_zero_category: str | None = None

    # Signal
    primary_signal_source: str | None = None

    # Export
    export_paths: list[str] = field(default_factory=list)
    report_json_path: str | None = None

    # Error
    error: str | None = None

    # Readiness artifacts
    stabilization_seal_present: bool = False
    hermetic_regression_manifest_present: bool = False
    transport_authority_status_present: bool = False
    mlx_wired_limit_seal_present: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mode"] = self.mode.value
        d["status"] = self.status.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


# ---------------------------------------------------------------------------
# Readiness artifact paths (READ-ONLY)
# ---------------------------------------------------------------------------

READINESS_ARTIFACTS = {
    "stabilization_seal": Path(__file__).parent.parent / "probe_f206an_stabilization" / "stabilization_seal.json",
    "hermetic_regression_manifest": Path(__file__).parent.parent / "probe_f206aq_hermetic_regression" / "hermetic_regression_manifest.json",
    "transport_authority_status": Path(__file__).parent.parent / "probe_transport_authority_f206bc" / "transport_authority_status_refreshed.json",
    "mlx_wired_limit_seal": Path(__file__).parent.parent / "probe_f206ao_mlx_wired_limit" / "mlx_wired_limit_seal.json",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_readiness_artifacts() -> dict[str, bool]:
    """Fail-soft check for readiness artifacts. Returns dict of artifact → present."""
    results = {}
    for name, path in READINESS_ARTIFACTS.items():
        results[name] = path.exists()
    return results


def _make_measurement_id() -> str:
    ts = time.time_ns() // 1_000_000
    uid = uuid.uuid4().hex[:6]
    return f"lsm_{ts}_{uid}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _capture_uma() -> dict:
    """Capture UMA status. Fail-soft: returns None on error."""
    try:
        from hledac.universal.core.resource_governor import sample_uma_status
        s = sample_uma_status()
        return {
            "used_gib": round(s.system_used_gib, 3),
            "swap_gib": round(s.swap_used_gib, 3),
            "state": s.state,
        }
    except Exception:
        return {"used_gib": None, "swap_gib": None, "state": None}


def _parse_sprint_report(report_path: str | None) -> dict | None:
    """Parse sprint JSON report for findings count. Fail-soft."""
    if not report_path:
        return None
    try:
        with open(report_path) as f:
            data = json.load(f)
        # Navigate to canonical_run_summary
        summary = data.get("canonical_run_summary", {})
        return {
            "findings_count": summary.get("findings_count"),
            "cycles_completed": summary.get("cycles_completed"),
            "cycles_started": summary.get("cycles_started"),
            "accepted_findings": summary.get("accepted_findings"),
            "runtime_truth": summary.get("runtime_truth"),
            "timing_truth": summary.get("timing_truth"),
            "checkpoint_zero_category": summary.get("checkpoint_zero_category"),
            "primary_signal_source": summary.get("primary_signal_source"),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------

async def _run_dry_run(
    query: str,
    profile: str,
    duration_s: int,
    aggressive_mode: bool,
    deep_probe: bool,
) -> LiveMeasurementResult:
    """Validate command construction without running sprint."""

    # Check readiness artifacts
    readiness = _check_readiness_artifacts()

    # Build planned command
    export_dir = str(Path.home() / ".hledac" / "reports")
    planned_cmd = [
        sys.executable, "-m", "hledac.universal.core",
        "--sprint",
        f"--query={query}",
        f"--duration={duration_s}",
        f"--export-dir={export_dir}",
    ]
    if aggressive_mode:
        planned_cmd.append("--aggressive")
    if deep_probe:
        planned_cmd.append("--deep-probe")

    result = LiveMeasurementResult(
        measurement_id=_make_measurement_id(),
        sprint_id=None,
        mode=RunMode.DRY_RUN,
        status=MeasurementStatus.PLANNED,
        start_time_iso=_now_iso(),
        end_time_iso=None,
        planned_duration_s=float(duration_s),
        actual_duration_s=None,
        query=query,
        profile=profile,
        aggressive_mode=aggressive_mode,
        deep_probe=deep_probe,
        uma_pre_used_gib=None,
        uma_pre_swap_gib=None,
        uma_pre_state=None,
        uma_post_used_gib=None,
        uma_post_swap_gib=None,
        uma_post_state=None,
        findings_count=None,
        cycles_completed=None,
        cycles_started=None,
        accepted_findings=None,
        runtime_truth=None,
        timing_truth=None,
        checkpoint_zero_category=None,
        primary_signal_source=None,
        error=None,
        stabilization_seal_present=readiness.get("stabilization_seal", False),
        hermetic_regression_manifest_present=readiness.get("hermetic_regression_manifest", False),
        transport_authority_status_present=readiness.get("transport_authority_status", False),
        mlx_wired_limit_seal_present=readiness.get("mlx_wired_limit_seal", False),
    )

    # Log planned command
    logging.info("[DRY-RUN] Planned command: %s", " ".join(planned_cmd))

    # Log readiness artifact status
    for name, present in readiness.items():
        status_str = "PRESENT" if present else "MISSING"
        logging.info("[DRY-RUN] Readiness artifact [%s]: %s", name, status_str)

    # Simulate validation
    errors: list[str] = []
    if duration_s < MIN_DURATION_S:
        errors.append(f"Duration {duration_s}s < minimum {MIN_DURATION_S}s (use --allow-smoke to override)")

    if errors:
        result.status = MeasurementStatus.ABORTED
        result.error = "; ".join(errors)
    else:
        result.status = MeasurementStatus.PLANNED
        logging.info(
            "[DRY-RUN] Validation PASSED — ready for live execution with --live flag"
        )

    return result


# ---------------------------------------------------------------------------
# Live mode
# ---------------------------------------------------------------------------

async def _run_live_sprint(
    query: str,
    profile: str,
    duration_s: int,
    aggressive_mode: bool,
    deep_probe: bool,
    export_dir: str,
) -> LiveMeasurementResult:
    """Run canonical sprint and capture metrics."""
    measurement_id = _make_measurement_id()
    start_time_iso = _now_iso()
    start_ts = time.monotonic()

    # Capture pre-sprint UMA
    uma_pre = await _capture_uma()

    result = LiveMeasurementResult(
        measurement_id=measurement_id,
        sprint_id=None,
        mode=RunMode.LIVE,
        status=MeasurementStatus.RUNNING,
        start_time_iso=start_time_iso,
        end_time_iso=None,
        planned_duration_s=float(duration_s),
        actual_duration_s=None,
        query=query,
        profile=profile,
        aggressive_mode=aggressive_mode,
        deep_probe=deep_probe,
        uma_pre_used_gib=uma_pre.get("used_gib"),
        uma_pre_swap_gib=uma_pre.get("swap_gib"),
        uma_pre_state=uma_pre.get("state"),
        uma_post_used_gib=None,
        uma_post_swap_gib=None,
        uma_post_state=None,
        findings_count=None,
        cycles_completed=None,
        cycles_started=None,
        accepted_findings=None,
        runtime_truth=None,
        timing_truth=None,
        checkpoint_zero_category=None,
        primary_signal_source=None,
        error=None,
        stabilization_seal_present=READINESS_ARTIFACTS["stabilization_seal"].exists(),
        hermetic_regression_manifest_present=READINESS_ARTIFACTS["hermetic_regression_manifest"].exists(),
        transport_authority_status_present=READINESS_ARTIFACTS["transport_authority_status"].exists(),
        mlx_wired_limit_seal_present=READINESS_ARTIFACTS["mlx_wired_limit_seal"].exists(),
    )

    # Import canonical sprint entry — outside try so we can restore in finally
    from hledac.universal.core import __main__ as core_main
    from hledac.universal.paths import get_sprint_json_report_path

    # Generate harness-side sprint_id for tracking
    import uuid
    ts = time.time_ns() // 1_000_000
    harness_sprint_id = f"8sa_{ts}_{uuid.uuid4().hex[:6]}"
    result.sprint_id = harness_sprint_id

    # Patch _make_sprint_id so run_sprint uses our harness_sprint_id.
    # This ensures get_sprint_json_report_path(harness_sprint_id) finds the report.
    _original_make_sprint_id = core_main._make_sprint_id
    _patched_sprint_ids = [harness_sprint_id]

    def _patched_make_sprint_id() -> str:
        return _patched_sprint_ids.pop(0) if _patched_sprint_ids else _original_make_sprint_id()

    core_main._make_sprint_id = _patched_make_sprint_id

    try:
        logging.info(
            "[LIVE] Starting sprint measurement_id=%s sprint_id=%s profile=%s duration=%ds",
            measurement_id, harness_sprint_id, profile, duration_s
        )

        # Run canonical sprint
        await core_main.run_sprint(
            query=query,
            duration_s=float(duration_s),
            export_dir=export_dir,
            aggressive_mode=aggressive_mode,
            deep_probe_enabled=deep_probe,
            ui_mode=False,
        )

        end_time_iso = _now_iso()
        actual_duration_s = time.monotonic() - start_ts

        # Capture post-sprint UMA
        uma_post = await _capture_uma()
        result.uma_post_used_gib = uma_post.get("used_gib")
        result.uma_post_swap_gib = uma_post.get("swap_gib")
        result.uma_post_state = uma_post.get("state")

        result.end_time_iso = end_time_iso
        result.actual_duration_s = round(actual_duration_s, 1)
        result.status = MeasurementStatus.COMPLETED

        # Parse sprint report — path uses harness_sprint_id (patched into run_sprint)
        report_path = get_sprint_json_report_path(harness_sprint_id)
        if report_path.exists():
            result.report_json_path = str(report_path)
            parsed = _parse_sprint_report(str(report_path))
            if parsed:
                result.findings_count = parsed.get("findings_count")
                result.cycles_completed = parsed.get("cycles_completed")
                result.cycles_started = parsed.get("cycles_started")
                result.accepted_findings = parsed.get("accepted_findings")
                result.runtime_truth = parsed.get("runtime_truth")
                result.timing_truth = parsed.get("timing_truth")
                result.checkpoint_zero_category = parsed.get("checkpoint_zero_category")
                result.primary_signal_source = parsed.get("primary_signal_source")

        logging.info(
            "[LIVE] Completed measurement_id=%s findings=%s cycles=%s duration=%.1fs",
            measurement_id, result.findings_count, result.cycles_completed, actual_duration_s
        )

    except Exception as exc:
        result.status = MeasurementStatus.FAILED
        result.end_time_iso = _now_iso()
        result.error = f"{type(exc).__name__}: {exc}"
        logging.error("[LIVE] Failed measurement_id=%s: %s", measurement_id, exc, exc_info=True)

    finally:
        # Restore original _make_sprint_id — critical for test isolation
        core_main._make_sprint_id = _original_make_sprint_id

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="F206BH Live Sprint Measurement Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Profiles:
  smoke180  180s sprint (smoke test)
  active300 300s sprint (standard)
  active600 600s sprint (extended)

Safety:
  Default is --dry-run (no live sprint execution).
  Live execution requires explicit --live flag.
  No stealth or aggressive mode by default.

Examples:
  python benchmarks/live_sprint_measurement.py --profile smoke180 --query "LockBit ransomware"
  python benchmarks/live_sprint_measurement.py --profile active300 --query "APT29" --live
  python benchmarks/live_sprint_measurement.py --profile active600 --query "ransomware" --live --output-json /tmp/measure.json
        """,
    )

    parser.add_argument(
        "--profile",
        type=str,
        choices=list(PROFILE_DURATION.keys()),
        default="active300",
        help="Measurement profile (determines duration). Default: active300",
    )
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="Sprint query string",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Override profile duration (seconds). Use with --allow-smoke for <180s",
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        help="Enable aggressive mode (8s branch budgets, parallel branches)",
    )
    parser.add_argument(
        "--deep-probe",
        action="store_true",
        help="Enable deep probe research post-sprint",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute live sprint (default is --dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate command construction without running sprint (default)",
    )
    parser.add_argument(
        "--allow-smoke",
        action="store_true",
        help="Allow duration < 180s (smoke profile override)",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="Path to write JSON measurement result",
    )
    parser.add_argument(
        "--output-md",
        type=str,
        default=None,
        help="Path to write markdown summary",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser


def _render_md(result: LiveMeasurementResult) -> str:
    """Render measurement result as markdown."""
    lines = [
        f"# Live Sprint Measurement: {result.measurement_id}",
        "",
        f"**Mode:** {result.mode.value}",
        f"**Status:** {result.status.value}",
        f"**Profile:** {result.profile}",
        f"**Query:** `{result.query}`",
        "",
        "## Timing",
        "",
        f"- Duration (planned): {result.planned_duration_s}s",
        f"- Actual duration: {result.actual_duration_s}s" if result.actual_duration_s else "- Actual duration: N/A",
        f"- Start: {result.start_time_iso}",
        f"- End: {result.end_time_iso}",
        "",
        "## Configuration",
        "",
        f"- Aggressive mode: {result.aggressive_mode}",
        f"- Deep probe: {result.deep_probe}",
        "",
        "## UMA Memory",
        "",
        f"- Pre-sprint: {result.uma_pre_used_gib} GiB used, {result.uma_pre_swap_gib} GiB swap, state={result.uma_pre_state}",
        f"- Post-sprint: {result.uma_post_used_gib} GiB used, {result.uma_post_swap_gib} GiB swap, state={result.uma_post_state}",
        "",
        "## Sprint Results" if result.mode == RunMode.LIVE else "## Sprint Results (not executed in dry-run)",
        "",
    ]

    if result.mode == RunMode.LIVE:
        lines.extend([
            f"- Findings count: {result.findings_count}",
            f"- Cycles completed: {result.cycles_completed}",
            f"- Cycles started: {result.cycles_started}",
            f"- Accepted findings: {result.accepted_findings}",
            f"- Runtime truth: {result.runtime_truth}",
            f"- Timing truth: {result.timing_truth}",
            f"- Checkpoint zero: {result.checkpoint_zero_category}",
            f"- Primary signal source: {result.primary_signal_source}",
            f"- Report: {result.report_json_path}",
        ])
    else:
        lines.append("- *(Sprint not executed in dry-run mode)*")

    lines.extend([
        "",
        "## Readiness Artifacts",
        "",
        f"- stabilization_seal.json: {'PRESENT' if result.stabilization_seal_present else 'MISSING'}",
        f"- hermetic_regression_manifest.json: {'PRESENT' if result.hermetic_regression_manifest_present else 'MISSING'}",
        f"- transport_authority_status_refreshed.json: {'PRESENT' if result.transport_authority_status_present else 'MISSING'}",
        f"- mlx_wired_limit_seal.json: {'PRESENT' if result.mlx_wired_limit_seal_present else 'MISSING'}",
    ])

    if result.error:
        lines.extend(["", f"## Error", "", f"```\n{result.error}\n```"])

    return "\n".join(lines)


async def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolve duration
    duration_s = args.duration or PROFILE_DURATION[args.profile]

    # Safety: duration < 180 requires --allow-smoke
    if duration_s < MIN_DURATION_S and not args.allow_smoke:
        logging.error(
            "Duration %ds < minimum %ds. Pass --allow-smoke to override.",
            duration_s, MIN_DURATION_S
        )
        return 1

    # Determine mode
    is_live = args.live and not args.dry_run
    mode_str = "LIVE" if is_live else "DRY-RUN"
    logging.info("[%s] Profile=%s duration=%ds query=%r aggressive=%s",
                 mode_str, args.profile, duration_s, args.query, args.aggressive)

    # Execute
    if is_live:
        export_dir = str(Path.home() / ".hledac" / "reports")
        result = await _run_live_sprint(
            query=args.query,
            profile=args.profile,
            duration_s=duration_s,
            aggressive_mode=args.aggressive,
            deep_probe=args.deep_probe,
            export_dir=export_dir,
        )
    else:
        result = await _run_dry_run(
            query=args.query,
            profile=args.profile,
            duration_s=duration_s,
            aggressive_mode=args.aggressive,
            deep_probe=args.deep_probe,
        )

    # Write outputs
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(result.to_json())
        logging.info("JSON result written to %s", out_path)

    if args.output_md:
        md_path = Path(args.output_md)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        with open(md_path, "w") as f:
            f.write(_render_md(result))
        logging.info("Markdown summary written to %s", md_path)

    # Print summary to stdout
    print(f"[{mode_str}] measurement_id={result.measurement_id} status={result.status.value}")
    if result.error:
        print(f"  ERROR: {result.error}")
    elif result.status == MeasurementStatus.PLANNED:
        print(f"  Validated — ready for live execution. Use --live to run sprint.")

    # Exit code
    if result.status in (MeasurementStatus.COMPLETED, MeasurementStatus.PLANNED):
        return 0
    elif result.status == MeasurementStatus.ABORTED:
        return 2
    else:
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
