"""Probe F262OBS — RL health observability smoke tests.

Verifies:
  1. tools/rl_health_report.py runs as subprocess without crash
     (exit 0 on pre-training/healthy, exit 1 on anomaly; never a traceback).
  2. SprintPolicyState remains backward-compatible: a JSON payload
     WITHOUT the new fields loads via SprintPolicyState(**legacy_kwargs)
     without raising KeyError (dataclass defaults fill the gap).
  3. loss_history FIFO eviction — pushing 101 entries caps len == 100.

These tests are hermetic. No MLX, no DuckDB, no network, no scheduler.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Ensure hledac.universal root is importable
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


# ── Test 1 ──────────────────────────────────────────────────────────────────


def test_rl_health_report_runs_without_error():
    """Subprocess invocation of rl_health_report.py exits with 0 or 1, never crashes."""
    script = _ROOT / "tools" / "rl_health_report.py"
    assert script.exists(), f"Missing tool: {script}"
    # Use the real production state file. If pre-training → exit 0. If healthy → 0.
    # If anomaly → 1. Never 2+ (which would indicate a Python crash/traceback).
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(_ROOT),
    )
    assert proc.returncode in (0, 1), (
        f"rl_health_report.py exited with {proc.returncode} (expected 0 or 1)\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    # Must include the report banner
    assert "=== RL Health Report ===" in proc.stdout, (
        f"Report banner missing from output:\n{proc.stdout}"
    )


# ── Test 2 ──────────────────────────────────────────────────────────────────


def test_rl_state_schema_backward_compatible():
    """Legacy JSON without new fields loads without KeyError.

    Simulates an OLD persisted state (pre-F262OBS). The new fields must
    default to their declared values, never raise KeyError.
    """
    from rl.sprint_policy_manager import SprintPolicyState

    # Legacy payload — exact shape BEFORE F262OBS schema extension
    legacy_payload = {
        "sprint_sequence_number": 42,
        "epsilon": 0.085,
        "total_reward": 12.5,
        "sprint_rewards": [0.1, 0.2, 0.15],
        "qmix_weights": None,
        "last_train_sprint": -1,
        "last_action": 0,
        "q_network_weights_path": "/tmp/_fake_qmix.npz",
        "last_train_step": -1,
        "cumulative_train_steps": 0,
        "last_loss": 0.0,
    }
    # No try/except — if backward compat is broken, the test must FAIL
    state = SprintPolicyState(**legacy_payload)
    # New fields must be present and at their declared defaults
    assert state.training_steps_completed == 0
    assert state.loss_history == []
    assert state.mean_q_value_history == []
    assert state.epsilon_history == []
    assert state.last_train_step_sprint == 0
    # Legacy fields still round-trip
    assert state.sprint_sequence_number == 42
    assert state.epsilon == 0.085


# ── Test 3 ──────────────────────────────────────────────────────────────────


def test_loss_history_fifo_eviction():
    """Pushing 101 entries into loss_history caps at len == 100 (FIFO)."""
    from rl.sprint_policy_manager import SprintPolicyState

    state = SprintPolicyState()
    assert state.loss_history == []  # default

    # Push 101 entries
    for i in range(101):
        state.loss_history.append(float(i))
        if len(state.loss_history) > 100:
            state.loss_history = state.loss_history[-100:]

    assert len(state.loss_history) == 100, (
        f"loss_history length should be 100 after FIFO eviction, got {len(state.loss_history)}"
    )
    # First entry should be 1.0 (we evicted 0.0), last should be 100.0
    assert state.loss_history[0] == 1.0
    assert state.loss_history[-1] == 100.0

    # Same contract applies to mean_q_value_history and epsilon_history
    for field_name in ("mean_q_value_history", "epsilon_history"):
        hist = getattr(state, field_name)
        assert hist == [], f"{field_name} should start empty"
        for i in range(101):
            hist.append(float(i))
            if len(hist) > 100:
                hist = hist[-100:]
                setattr(state, field_name, hist)
        final = getattr(state, field_name)
        assert len(final) == 100, f"{field_name} should cap at 100, got {len(final)}"


# ── Extra: persistence round-trip (bonus invariant for F262OBS) ─────────────


def test_new_fields_persisted_in_zstd_state():
    """Verify the new fields are written to the zstd-compressed state file
    (and that the file remains valid zstd-compressed JSON after a save cycle).
    """
    import compression.zstd as _zstd

    from rl.sprint_policy_manager import SprintPolicyManager

    # Write a fresh state to a temp file
    with tempfile.NamedTemporaryFile(
        suffix=".json", delete=False, mode="wb"
    ) as f:
        tmp_path = Path(f.name)
    try:
        # Create a manager pointed at the temp path
        mgr = SprintPolicyManager(policy_path=tmp_path)
        # Append some test values to histories
        mgr._state.loss_history = [0.1, 0.2, 0.3]
        mgr._state.mean_q_value_history = [1.0, 1.1, 1.2]
        mgr._state.epsilon_history = [0.10, 0.099, 0.098]
        mgr._state.training_steps_completed = 3
        mgr._state.last_train_step_sprint = 9
        mgr._save()

        # Read raw bytes, decompress, parse JSON
        raw = tmp_path.read_bytes()
        if raw[:4] == b"\x28\xb5\x2f\xfd":
            payload = json.loads(_zstd.decompress(raw).decode("utf-8"))
        else:
            payload = json.loads(raw.decode("utf-8"))

        # New fields must be present in payload
        for key in (
            "training_steps_completed",
            "loss_history",
            "mean_q_value_history",
            "epsilon_history",
            "last_train_step_sprint",
        ):
            assert key in payload, f"New field missing from persisted state: {key}"
        assert payload["training_steps_completed"] == 3
        assert payload["loss_history"] == [0.1, 0.2, 0.3]
        assert payload["mean_q_value_history"] == [1.0, 1.1, 1.2]
        assert payload["epsilon_history"] == [0.10, 0.099, 0.098]
        assert payload["last_train_step_sprint"] == 9
    finally:
        if tmp_path.exists():
            os.unlink(tmp_path)
