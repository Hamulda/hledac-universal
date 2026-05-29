#!/usr/bin/env python3
"""
E2E Sprint Probe — F206S Canonical Run

Subprocess-based bounded E2E runner. No live network mock.
Writes JSON artifact to probe_e2e_readiness/e2e_run_result.json.

Default env:
    HLEDAC_ENABLE_TEMPORAL_STORE=1
    HLEDAC_ENABLE_CURL_CFFI=0
    HLEDAC_ENABLE_HTTPX_H2=0

Run directly:
    python benchmarks/e2e_sprint_probe.py
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

# ─── Constants ────────────────────────────────────────────────────────────────

UNIVERSAL_ROOT = pathlib.Path(__file__).parent.parent.resolve()
PROBE_DIR = UNIVERSAL_ROOT / "probe_e2e_readiness"
ARTIFACT_PATH = PROBE_DIR / "e2e_run_result.json"

DEFAULT_QUERY = "ransomware infrastructure leak"
DEFAULT_DURATION_S = 360
DEFAULT_AGGRESSIVE_MODE = False

DEFAULT_ENV = {
    "HLEDAC_ENABLE_TEMPORAL_STORE": "1",
    "HLEDAC_ENABLE_CURL_CFFI": "0",
    "HLEDAC_ENABLE_HTTPX_H2": "0",
}


# ─── Artifact helpers ────────────────────────────────────────────────────────


def _default_artifact(
    command: list[str],
    env: dict[str, str],
    requested_duration: float,
) -> dict[str, Any]:
    return {
        "command": command,
        "env_flags": {k: v for k, v in env.items() if k.startswith("HLEDAC_")},
        "python_executable": sys.executable,
        "cwd": str(UNIVERSAL_ROOT),
        "started_at": None,
        "duration_requested_s": requested_duration,
        "duration_actual_s": None,
        "exit_code": None,
        "stdout_tail": "",
        "stderr_tail": "",
        "report_paths_found": [],
        "temporal_summary_present": False,
        "temporal_priority_hints_present": False,
        "transport_counters_present": False,
        "runtime_truth_present": False,
        "timing_truth_present": False,
        "memory_truth_present": False,
        "accepted_findings": 0,
        "public_accepted_findings": 0,
        "feed_findings": 0,
        "ct_findings": 0,
        "cycles_started": 0,
        "cycles_completed": 0,
        "errors": [],
        "status": "RUNNING",
    }


def _parse_report_for_stats(report_path: pathlib.Path) -> dict[str, Any]:
    """Parse a sprint JSON report and extract metrics."""
    stats: dict[str, Any] = {
        "accepted_findings": 0,
        "public_accepted_findings": 0,
        "feed_findings": 0,
        "ct_findings": 0,
        "cycles_started": 0,
        "cycles_completed": 0,
        "temporal_summary_present": False,
        "temporal_priority_hints_present": False,
        "transport_counters_present": False,
        "runtime_truth_present": False,
        "timing_truth_present": False,
        "memory_truth_present": False,
    }
    if not report_path.exists():
        return stats

    try:
        content = report_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception:
        return stats

    summary = data.get("canonical_run_summary", {})
    if summary:
        stats["runtime_truth_present"] = True

    # memory_truth: additive field from canonical_run_summary
    mem_truth = data.get("memory_truth", {})
    if mem_truth:
        stats["memory_truth_present"] = True

    timing = data.get("timing_truth", {})
    if timing:
        stats["timing_truth_present"] = True

    cycles = data.get("cycles", []) or []
    stats["cycles_started"] = len(cycles)
    stats["cycles_completed"] = sum(1 for c in cycles if c.get("completed"))

    branch_verdicts = data.get("public_branch_verdicts", []) or []
    stats["public_accepted_findings"] = sum(
        v.get("accepted_findings", 0) for v in branch_verdicts
    )

    feed_data = data.get("feed_branch", {})
    if feed_data:
        stats["feed_findings"] = feed_data.get("accepted_findings", 0)

    ct_data = data.get("ct_branch", {})
    if ct_data:
        stats["ct_findings"] = ct_data.get("accepted_findings", 0)

    stats["accepted_findings"] = (
        stats["public_accepted_findings"]
        + stats["feed_findings"]
        + stats["ct_findings"]
    )

    # Check temporal fields in public branch verdict
    for verdict in branch_verdicts:
        if "temporal_signal_summary" in verdict and verdict["temporal_signal_summary"]:
            stats["temporal_summary_present"] = True
        if "temporal_priority_hints" in verdict and verdict["temporal_priority_hints"]:
            stats["temporal_priority_hints_present"] = True
        if "transport_counters" in verdict:
            stats["transport_counters_present"] = True

    return stats


# ─── Subprocess run ──────────────────────────────────────────────────────────


def run_sprint_subprocess(
    query: str,
    duration_s: float,
    aggressive_mode: bool,
    extra_env: dict[str, str] | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """
    Run canonical sprint as subprocess. Returns result dict.
    """
    # Build command
    cmd = [
        sys.executable,
        "-m",
        "hledac.universal",
        "--sprint",
        query,
        str(int(duration_s)),
    ]
    if aggressive_mode:
        cmd.append("--aggressive")

    # Build env
    env: dict[str, str] = os.environ.copy()
    for k, v in (extra_env or {}).items():
        if v is not None:
            env[k] = v
        else:
            env.pop(k, None)

    started_at = datetime.now(UTC).isoformat()
    elapsed_requested = duration_s + 60  # grace for startup/teardown
    wall_start = time.monotonic()

    artifact = _default_artifact(cmd, env, duration_s)
    artifact["started_at"] = started_at

    proc = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(UNIVERSAL_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid,
        )

        try:
            stdout_bytes, stderr_bytes = proc.communicate(
                timeout=timeout_s or elapsed_requested
            )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            # Kill the whole process group
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            time.sleep(2)
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            stdout_bytes, stderr_bytes = proc.communicate()
            exit_code = -1
            artifact["errors"].append("TIMEOUT")

        stdout_text = stdout_bytes.decode("utf-8", errors="replace")
        stderr_text = stderr_bytes.decode("utf-8", errors="replace")

        artifact["exit_code"] = exit_code
        artifact["stdout_tail"] = stdout_text[-3000:]
        artifact["stderr_tail"] = stderr_text[-3000:]

        # Find report paths
        report_paths: list[pathlib.Path] = []
        for line in stdout_text.splitlines():
            if "report" in line.lower() and ".json" in line:
                for part in line.split():
                    p = pathlib.Path(part.strip())
                    if p.suffix == ".json" and p.exists():
                        report_paths.append(p)

        artifact["report_paths_found"] = [str(p) for p in report_paths]

        # Parse stats from first report found
        if report_paths:
            stats = _parse_report_for_stats(report_paths[0])
            artifact.update(stats)

    except Exception as e:
        artifact["errors"].append(str(e))
        artifact["exit_code"] = -1

    finally:
        if proc:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass

    # Mark completed
    artifact["duration_actual_s"] = time.monotonic() - wall_start
    artifact["status"] = "COMPLETED" if artifact["exit_code"] == 0 else "FAILED"
    return artifact


# ─── Memory truth helper ───────────────────────────────────────────────────────


def _sample_memory_truth() -> dict[str, Any]:
    """Fail-soft memory snapshot for E2E artifact. Returns additive dict."""
    try:
        from hledac.universal.core.resource_governor import sample_uma_status
        status = sample_uma_status()
        return {
            "sample_source": "core.resource_governor.sample_uma_status",
            "rss_gib_start": status.get("rss_gib"),
            "system_used_gib_start": status.get("system_used_gib"),
            "system_available_gib_start": status.get("system_available_gib"),
            "swap_used_gib_start": status.get("swap_used_gib"),
            "swap_detected": status.get("swap_detected", False),
            "uma_state_start": status.get("state"),
            "io_only_start": status.get("io_only", False),
            "metal_cache_limit_bytes": status.get("metal_cache_limit_bytes"),
            "metal_wired_limit_bytes": status.get("metal_wired_limit_bytes"),
            "rss_gib_end": None,
            "system_used_gib_end": None,
            "uma_state_end": None,
            "io_only_end": None,
        }
    except Exception:
        return {"error": "sample_unaavailable"}


def _finalize_memory_truth(snap: dict[str, Any]) -> dict[str, Any]:
    """Fill in end snapshot values."""
    try:
        from hledac.universal.core.resource_governor import sample_uma_status
        status = sample_uma_status()
        snap["rss_gib_end"] = status.get("rss_gib")
        snap["system_used_gib_end"] = status.get("system_used_gib")
        snap["uma_state_end"] = status.get("state")
        snap["io_only_end"] = status.get("io_only", False)
        return snap
    except Exception:
        return snap


# ─── Direct async run (alternative) ──────────────────────────────────────────


async def run_sprint_direct(
    query: str,
    duration_s: float,
    aggressive_mode: bool = False,
) -> dict[str, Any]:
    """
    Run canonical sprint directly via asyncio (no subprocess).
    Returns result dict. Requires event loop already running.
    """
    from hledac.universal.core.__main__ import run_sprint

    started_at = datetime.now(UTC).isoformat()
    artifact = _default_artifact(
        [sys.executable, "-m", "hledac.universal", "--sprint", query, str(int(duration_s))],
        {},
        duration_s,
    )
    artifact["started_at"] = started_at

    mem_snap = _sample_memory_truth()

    start = time.monotonic()
    errors: list[str] = []
    exit_code: int = 0

    try:
        await run_sprint(
            query=query,
            duration_s=duration_s,
            aggressive_mode=aggressive_mode,
        )
    except Exception as e:
        errors.append(str(e))
        exit_code = -1

    elapsed = time.monotonic() - start
    artifact["duration_actual_s"] = elapsed
    artifact["exit_code"] = exit_code
    artifact["errors"] = errors
    artifact["status"] = "COMPLETED" if exit_code == 0 else "FAILED"
    artifact["memory_truth"] = _finalize_memory_truth(mem_snap)

    return artifact


# ─── Main ───────────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="E2E Sprint Probe — F206S")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_S)
    parser.add_argument("--aggressive", action="store_true")
    parser.add_argument(
        "--mode",
        choices=["subprocess", "direct"],
        default="subprocess",
        help="subprocess = safe isolation; direct = faster, shares process",
    )
    parser.add_argument(
        "--env",
        action="append",
        help="Extra env vars in KEY=VALUE form",
    )
    args = parser.parse_args()

    # Parse extra env
    extra_env: dict[str, str] = {}
    for item in (args.env or []):
        if "=" in item:
            k, v = item.split("=", 1)
            extra_env[k] = v

    # Merge with defaults (extra env overrides defaults)
    env = DEFAULT_ENV.copy()
    env.update(extra_env)

    print(f"[F206S E2E] query={args.query!r} duration={args.duration}s mode={args.mode}")
    print(f"[F206S E2E] temporal_store={env['HLEDAC_ENABLE_TEMPORAL_STORE']} "
          f"curl={env['HLEDAC_ENABLE_CURL_CFFI']} httpx={env['HLEDAC_ENABLE_HTTPX_H2']}")
    print(f"[F206S E2E] artifact={ARTIFACT_PATH}")

    PROBE_DIR.mkdir(parents=True, exist_ok=True)

    if args.mode == "direct":
        artifact = asyncio.run(
            run_sprint_direct(args.query, args.duration, args.aggressive)
        )
    else:
        artifact = run_sprint_subprocess(
            args.query,
            args.duration,
            args.aggressive,
            extra_env=env,
        )

    # Write artifact
    artifact_path = ARTIFACT_PATH
    artifact_path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    dur = artifact.get("duration_actual_s")
    dur_str = f"{dur:.1f}" if dur is not None else "N/A"
    print(f"[F206S E2E] exit_code={artifact['exit_code']} "
          f"actual_s={dur_str} "
          f"findings={artifact['accepted_findings']} "
          f"status={artifact['status']}")
    print(f"[F206S E2E] artifact written to {artifact_path}")


if __name__ == "__main__":
    main()
