#!/usr/bin/env python3
"""
rl_health_report.py — F262OBS: RL training health observability tool.

Read-only diagnostic that inspects the persisted SprintPolicyState and reports
whether the QMIX Q-network is actually learning. Anomalies trigger exit 1.

Pure stdlib + json (plus optional compression.zstd for the on-disk format).
NO MLX, NO DuckDB, NO network, NO imports from runtime/ or pipeline/.

The state file at rl/.sprint_policy_state.json is zstd-compressed (magic
0x28B52FFD) — the loader auto-detects zstd vs plain JSON.

Anomaly rules (exits 1 if any trip):
  A1. training_steps_completed >= 50 AND epsilon has not decayed
      (i.e. last epsilon ≈ first epsilon, slope ~ 0)
  A2. any mean_q_value_history entry > 100 (Q-value explosion)
  A3. reward trend over the last 20 sprints < -0.1/sprint
      (i.e. linear regression slope of last 20 rewards < -0.1)

If training_steps_completed == 0 the report prints a "PRE-TRAINING" banner
and exits 0 (nothing to evaluate yet).

CLI:
  python tools/rl_health_report.py
  python tools/rl_health_report.py --state-path /custom/path.json
  python tools/rl_health_report.py --window 20   # reward window size
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ZSTD magic bytes (RFC 8478) — used to detect compressed state files
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
# Q-value explosion threshold — F263QCB: calibrated from dryrun baseline.
# Observed mean_q range across 3 train_steps: 0.96 - 1.60. Setting threshold
# to 50.0 (≈ 30× the max observed) gives a wide safety margin for normal
# training while still catching genuine divergence (e.g. mean_q > 10 after
# a TD-loss spike). Re-calibrate if production mean_q exceeds 10.
Q_EXPLOSION_THRESHOLD = 50.0
# Reward-trend slope (per-sprint) below which we flag decay
REWARD_TREND_SLOPE_THRESHOLD = -0.1
# Minimum training steps before evaluating epsilon decay
EPSILON_DECAY_MIN_TRAIN_STEPS = 50
# Rolling reward window size (default)
DEFAULT_REWARD_WINDOW = 10
# Trend-window for slope (default 20)
DEFAULT_TREND_WINDOW = 20


def _load_state(state_path: Path) -> dict:
    """Load state JSON. Auto-detects zstd-compressed vs plain JSON.

    Returns an empty dict if the file is missing or unreadable — the caller
    distinguishes "fresh" (no file) from "loaded" via returned keys.
    """
    if not state_path.exists():
        return {}
    raw = state_path.read_bytes()
    try:
        if raw[:4] == ZSTD_MAGIC:
            # Try stdlib zstd (Python 3.14+) first, then fall back
            try:
                import compression.zstd as _zstd
                plain = _zstd.decompress(raw)
            except Exception:
                try:
                    import zstandard as _zstd_lib
                    plain = _zstd_lib.ZstdDecompressor().decompress(raw)
                except Exception:
                    # Compressed but no decoder available — return empty
                    return {}
            return json.loads(plain.decode("utf-8"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _linear_regression_slope(values: list[float]) -> float:
    """OLS slope of `values` against x=0..len-1. Returns 0.0 for <2 points.

    Used for: epsilon decay slope, reward trend slope. Pure stdlib, no numpy.
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((xs[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


def _classify_trend(slope: float, threshold: float) -> str:
    """Return a one-glyph trend label for human-readable output."""
    if slope > threshold:
        return "↑"
    if slope < -threshold:
        return "↓"
    return "→"


def _rolling_mean(values: list[float], window: int) -> float:
    """Mean of the last `window` values. Returns 0.0 for empty input."""
    if not values:
        return 0.0
    tail = values[-window:]
    return sum(tail) / len(tail)


def _detect_anomalies(
    train_steps: int,
    epsilon_history: list[float],
    mean_q_history: list[float],
    reward_window: list[float],
) -> list[str]:
    """Return list of human-readable anomaly strings. Empty list = healthy."""
    anomalies: list[str] = []
    # A1: epsilon stuck after enough training steps
    if train_steps >= EPSILON_DECAY_MIN_TRAIN_STEPS and len(epsilon_history) >= 20:
        slope = _linear_regression_slope(epsilon_history)
        if abs(slope) < 1e-6 and abs(epsilon_history[-1] - epsilon_history[0]) < 1e-4:
            anomalies.append(
                f"A1: epsilon stuck at {epsilon_history[-1]:.4f} after {train_steps} train steps "
                f"(no decay)"
            )
    # A2: Q-value explosion — any single entry above threshold
    for i, q in enumerate(mean_q_history):
        if q > Q_EXPLOSION_THRESHOLD:
            anomalies.append(
                f"A2: Q-value explosion at step {i}: mean_q={q:.2f} > {Q_EXPLOSION_THRESHOLD}"
            )
            break  # report first occurrence
    # A3: reward decay — slope over last 20 below threshold
    if len(reward_window) >= 20:
        slope = _linear_regression_slope(reward_window)
        if slope < REWARD_TREND_SLOPE_THRESHOLD:
            anomalies.append(
                f"A3: reward trend slope={slope:.3f}/sprint over last 20 (below {REWARD_TREND_SLOPE_THRESHOLD})"
            )
    return anomalies


def _format_report(data: dict, reward_window_size: int, trend_window_size: int) -> str:
    """Produce the human-readable report body. Pure string ops."""
    seq = int(data.get("sprint_sequence_number", 0))
    eps = float(data.get("epsilon", 0.0))
    train_steps = int(data.get("training_steps_completed", 0))
    loss_hist = list(data.get("loss_history", []))
    q_hist = list(data.get("mean_q_value_history", []))
    eps_hist = list(data.get("epsilon_history", []))
    rewards = list(data.get("sprint_rewards", []))

    # ── Epsilon decay slope ──
    eps_slope = _linear_regression_slope(eps_hist) if eps_hist else 0.0
    eps_trend = _classify_trend(-eps_slope, 1e-5)  # negative slope = decay = good
    eps_label = "decaying" if eps_slope < -1e-5 else ("converging" if eps_slope < 0 else "stuck")

    # ── Mean Q-value trend (capped to last 100) ──
    q_trend_slope = _linear_regression_slope(q_hist[-trend_window_size:]) if q_hist else 0.0
    q_mean = (sum(q_hist[-reward_window_size:]) / len(q_hist[-reward_window_size:])
              if q_hist else 0.0)
    q_trend = "—" if not q_hist else _classify_trend(q_trend_slope, 0.001)

    # ── Reward rolling average ──
    reward_rolling = _rolling_mean(rewards, reward_window_size)
    reward_trend_window = rewards[-trend_window_size:] if rewards else []
    reward_slope = _linear_regression_slope(reward_trend_window)
    reward_trend = _classify_trend(reward_slope, 0.01)

    # ── Training frequency ──
    train_freq_str = (
        f"1 step / {max(1, seq // max(1, train_steps))} sprints"
        if train_steps > 0
        else "no training yet"
    )

    # ── Status verdict ──
    anomalies = _detect_anomalies(train_steps, eps_hist, q_hist, reward_trend_window)
    if train_steps == 0:
        status = "PRE-TRAINING (no train steps yet)"
        verdict_glyph = "⏳"
    elif anomalies:
        status = f"ANOMALY — {len(anomalies)} issue(s)"
        verdict_glyph = "❌"
    else:
        status = "LEARNING"
        verdict_glyph = "✅"

    lines: list[str] = []
    lines.append("=== RL Health Report ===")
    if train_steps == 0:
        lines.append(
            f"Sprints: {seq}   Train steps: {train_steps}   "
            f"Epsilon: {eps:.3f} (↓ decaying toward floor 0.05)"
        )
        lines.append("Q-value mean: — (no training steps recorded yet)")
        lines.append(
            f"Reward avg (last {reward_window_size}): "
            f"{reward_rolling:.2f} (trend over last {trend_window_size}: {reward_slope:+.3f}/sprint {reward_trend})"
        )
        lines.append(f"Training frequency: {train_freq_str}")
        lines.append(f"Status: {status} {verdict_glyph}")
        lines.append("")
        lines.append("Note: Enable --rl-train to start recording training metrics.")
        return "\n".join(lines)

    # Normal (post-training) report
    lines.append(
        f"Sprints: {seq}   Train steps: {train_steps}   "
        f"Epsilon: {eps:.3f} ({eps_trend} {eps_label})"
    )
    lines.append(
        f"Q-value mean: {q_mean:.2f} (trend {q_trend}, slope={q_trend_slope:+.4f}/step)"
    )
    lines.append(
        f"Reward avg (last {reward_window_size}): {reward_rolling:.2f} "
        f"({reward_trend} trend: {reward_slope:+.3f}/sprint)"
    )
    lines.append(f"Training frequency: {train_freq_str}")
    if loss_hist:
        loss_last = loss_hist[-1]
        loss_min = min(loss_hist)
        loss_max = max(loss_hist)
        lines.append(
            f"Loss (last/min/max): {loss_last:.4f} / {loss_min:.4f} / {loss_max:.4f}"
        )
    lines.append(f"Status: {status} {verdict_glyph}")
    if anomalies:
        lines.append("")
        lines.append("Anomalies detected:")
        for a in anomalies:
            lines.append(f"  - {a}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns 0 on healthy/pre-training, 1 on anomaly."""
    parser = argparse.ArgumentParser(
        description="Read-only RL training health report for SprintPolicyState."
    )
    default_path = (
        Path(__file__).parent.parent / "rl" / ".sprint_policy_state.json"
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=default_path,
        help=f"Path to sprint_policy_state.json (default: {default_path})",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=DEFAULT_REWARD_WINDOW,
        help=f"Rolling reward window size (default: {DEFAULT_REWARD_WINDOW})",
    )
    parser.add_argument(
        "--trend-window",
        type=int,
        default=DEFAULT_TREND_WINDOW,
        help=f"Reward-trend analysis window (default: {DEFAULT_TREND_WINDOW})",
    )
    args = parser.parse_args(argv)

    data = _load_state(args.state_path)
    if not data:
        print("=== RL Health Report ===")
        print(f"State file not found or unreadable: {args.state_path}")
        print("Status: NO STATE ❌")
        return 1

    report = _format_report(data, args.window, args.trend_window)
    print(report)

    # Decide exit code
    train_steps = int(data.get("training_steps_completed", 0))
    if train_steps == 0:
        return 0
    rewards = list(data.get("sprint_rewards", []))
    reward_trend_window = rewards[-args.trend_window:] if rewards else []
    anomalies = _detect_anomalies(
        train_steps,
        list(data.get("epsilon_history", [])),
        list(data.get("mean_q_value_history", [])),
        reward_trend_window,
    )
    return 1 if anomalies else 0


if __name__ == "__main__":
    sys.exit(main())
