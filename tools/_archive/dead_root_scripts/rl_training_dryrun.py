"""
rl_training_dryrun.py — Controlled QMIX training simulation on M1 8GB.

Loads production .sprint_policy_state.json (ZSTD-aware) into a temp copy,


runs 70 synthetic sprints, triggers QMIX train_steps every 10 sprints, and
prints a baseline convergence trace + health report. Production state is
NEVER mutated.

Usage:
    HLEDAC_RL_SKIP_RAM_GATE=1 uv run python tools/rl_training_dryrun.py

M1 budget: < 30 s wall time.

Output sections:
  1. Sprint-by-sprint training telemetry (only when training triggered).
  2. Final baseline metrics (loss trajectory, mean_q trajectory, epsilon).
  3. Full rl_health_report output.
"""
import argparse
import os
import random
import shutil
import sys
import time
from dataclasses import dataclass, field
import msgspec
from pathlib import Path
from typing import Any
os.environ.setdefault('HLEDAC_RL_SKIP_RAM_GATE', '1')
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from rl.sprint_policy_manager import SprintPolicyManager
PRODUCTION_STATE = PROJECT_ROOT / 'rl' / '.sprint_policy_state.json'
DEFAULT_NUM_SPRINTS = 90
DEFAULT_TRAIN_INTERVAL = 10

class SyntheticResult(msgspec.Struct, gc=False):
    """Minimal SprintSchedulerResult surface used by _compute_reward() and
    StateExtractor. Fields are populated randomly; reward math stays realistic
    (clamped [-1, 5]) so policy_manager.update() exercises the real code path.
    """
    sprint_id: str = 'dryrun'
    accepted_findings: int = 0
    produced_findings: int = 0
    ingested_findings: int = 0
    runtime_seconds: float = 0.0
    new_iocs: int = 0
    cycles_completed: int = 0
    aborted: bool = False
    source_quality_avg: float = 0.7
    semantic_novelty: float = 0.1
    last_rl_action: int = 0
    findings: list = field(default_factory=list)
    memory_pressure: float = 0.3
    error_count: int = 0
    fetch_count: int = 0

    @property
    def accepted_findings_count(self) -> int:
        return self.accepted_findings

def _make_synthetic(rng: random.Random) -> SyntheticResult:
    """Produce a realistic-looking sprint result.

    Distribution biased toward positive rewards (10-50 findings) with a small
    fraction of zero/negative events so the policy sees some variance.
    """
    if rng.random() < 0.1:
        accepted = rng.randint(0, 5)
        new_iocs = 0
    else:
        accepted = rng.randint(10, 50)
        new_iocs = rng.randint(0, 5)
    runtime = rng.uniform(20.0, 60.0)
    return SyntheticResult(sprint_id='dryrun-synth', accepted_findings=accepted, produced_findings=accepted + rng.randint(0, 5), ingested_findings=accepted, runtime_seconds=runtime, new_iocs=new_iocs, cycles_completed=rng.randint(1, 4), aborted=False, source_quality_avg=round(rng.uniform(0.5, 0.95), 3), semantic_novelty=round(new_iocs / max(accepted, 1), 3), last_rl_action=rng.randint(0, 4))

class TrainingSnapshot(msgspec.Struct, frozen=True, gc=False):
    sprint: int
    train_steps: int
    loss: float
    mean_q: float
    epsilon: float

def _snapshot(pm: SprintPolicyManager, sprint: int) -> TrainingSnapshot:
    return TrainingSnapshot(sprint=sprint, train_steps=pm.training_steps_completed, loss=pm._state.loss_history[-1] if pm._state.loss_history else 0.0, mean_q=pm._state.mean_q_value_history[-1] if pm._state.mean_q_value_history else 0.0, epsilon=pm.epsilon)

def _format_health_report(state: dict[str, Any]) -> str:
    """Reproduce the relevant slices of rl_health_report output for the
    dryrun-managed state. We don't shell out — the file may not exist yet
    (we wrote to a temp path).
    """
    seq = state.get('sprint_sequence_number', 0)
    train_steps = state.get('training_steps_completed', 0)
    eps = state.get('epsilon', 0.0)
    loss_hist = state.get('loss_history', [])
    q_hist = state.get('mean_q_value_history', [])
    state.get('epsilon_history', [])
    rewards = state.get('sprint_rewards', [])
    lines: list[str] = []
    lines.append('=== RL Health Report (dryrun temp state) ===')
    lines.append(f'Sprints: {seq}   Train steps: {train_steps}   Epsilon: {eps:.3f}')
    if q_hist:
        q_mean = sum(q_hist) / len(q_hist)
        lines.append(f'Q-value mean: {q_mean:.3f}  (last={q_hist[-1]:.3f}, n={len(q_hist)})')
    else:
        lines.append('Q-value mean: — (no training steps recorded yet)')
    if rewards:
        reward_rolling = sum(rewards[-10:]) / min(len(rewards), 10)
        lines.append(f'Reward avg (last 10): {reward_rolling:.3f}')
    if loss_hist:
        lines.append(f'Loss (last/min/max): {loss_hist[-1]:.4f} / {min(loss_hist):.4f} / {max(loss_hist):.4f}')
    q_max = max(q_hist) if q_hist else 0.0
    if q_max > 100.0:
        lines.append(f'  ⚠ A2: Q-value explosion — max mean_q={q_max:.2f} > 100')
    return '\n'.join(lines)

def main(argv: list[str] | None=None) -> int:
    parser = argparse.ArgumentParser(description='Controlled QMIX training dryrun (no real sprint, M1-safe).')
    parser.add_argument('--num-sprints', type=int, default=DEFAULT_NUM_SPRINTS, help=f'Synthetic sprint count (default: {DEFAULT_NUM_SPRINTS}, minimum 70 needed to get 7 train_steps at interval 10)')
    parser.add_argument('--train-interval', type=int, default=DEFAULT_TRAIN_INTERVAL, help='Override HLEDAC_RL_TRAIN_INTERVAL (default: 10)')
    parser.add_argument('--seed', type=int, default=42, help='RNG seed for reproducibility')
    parser.add_argument('--state-path', type=Path, default=PRODUCTION_STATE, help=f'Source state file (default: {PRODUCTION_STATE})')
    args = parser.parse_args(argv)
    if args.num_sprints < 90:
        print(f'[warn] num_sprints={args.num_sprints} < 90 → may yield only 1-2 train_steps (replay buffer must reach 64 first). Results may not show a stable baseline.')
    os.environ['HLEDAC_RL_TRAIN_INTERVAL'] = str(args.train_interval)
    tmp_state = Path('/tmp/hledac_dryrun_state.json')
    if args.state_path.exists():
        shutil.copy(args.state_path, tmp_state)
        print(f'[setup] copied {args.state_path} → {tmp_state}')
    else:
        tmp_state.unlink(missing_ok=True)
        print(f'[setup] no source state at {args.state_path}; starting fresh')
    pm = SprintPolicyManager(enabled=True, policy_path=tmp_state, rl_train_mode=True)
    pm.enable_training_mode()
    assert pm.is_training_enabled, 'training mode did not enable'
    base_train_steps = pm.training_steps_completed
    print(f'[setup] baseline: train_steps={base_train_steps}, epsilon={pm.epsilon:.4f}, replay_size={(pm._replay_buffer.size if pm._replay_buffer else 0)}')
    rng = random.Random(args.seed)
    snapshots: list[TrainingSnapshot] = []
    t0 = time.monotonic()
    for sprint in range(1, args.num_sprints + 1):
        result = _make_synthetic(rng)
        prev_train_steps = pm.training_steps_completed
        try:
            pm.update(result)
        except Exception as e:
            print(f'[sprint {sprint}] update() raised (continuing): {e}')
            continue
        pm._last_train_at = 0.0
        if pm.training_steps_completed > prev_train_steps:
            snap = _snapshot(pm, sprint)
            snapshots.append(snap)
            print(f'[TRAIN sprint={sprint:3d}] steps={snap.train_steps:3d}  loss={snap.loss:.4f}  mean_q={snap.mean_q:.4f}  epsilon={snap.epsilon:.4f}')
    elapsed = time.monotonic() - t0
    print(f'\n[done] {args.num_sprints} sprints in {elapsed:.2f}s ({elapsed / args.num_sprints * 1000:.1f} ms/sprint), train_steps_delta={pm.training_steps_completed - base_train_steps}')
    print('\n=== BASELINE METRICS ===')
    print(f'train_steps_total: {pm.training_steps_completed}')
    print(f'loss_history ({len(pm._state.loss_history)} entries):')
    for i, l in enumerate(pm._state.loss_history, 1):
        print(f'  step {i}: {l:.4f}')
    print(f'mean_q_history ({len(pm._state.mean_q_value_history)} entries):')
    for i, q in enumerate(pm._state.mean_q_value_history, 1):
        print(f'  step {i}: {q:.4f}')
    eps_hist = list(pm._state.epsilon_history)
    print(f'epsilon_history (last 5): {eps_hist[-5:]}')
    if len(pm._state.loss_history) >= 2:
        first, last = (pm._state.loss_history[0], pm._state.loss_history[-1])
        if first > 0:
            drop_pct = (1.0 - last / first) * 100.0
            print(f'loss drop step 1→{len(pm._state.loss_history)}: {drop_pct:+.1f}%')
            if drop_pct > 50:
                print('  → fast convergence, refined estimate: 150-250 sprints')
            elif drop_pct > 20:
                print('  → normal convergence, refined estimate: 200-400 sprints')
            else:
                print('  → slow convergence, refined estimate: 400-600 sprints')
        if last > 2.0 * first:
            print('  ⚠ loss increased — Q-network divergence risk, guard recommended')
    if pm._state.mean_q_value_history:
        q_max = max(pm._state.mean_q_value_history)
        if q_max < 1.0:
            print('  → mean_q range observed: < 1.0; recommended A2 threshold: 5.0')
        elif q_max < 10.0:
            print('  → mean_q range observed: 0-10; recommended A2 threshold: 50.0')
        else:
            print('  → mean_q range observed: >= 10; keep A2 threshold: 100.0')
    print('\n' + _format_health_report(pm._state.__dict__))
    if args.state_path.exists():
        prod_hash = hash(args.state_path.read_bytes())
        print(f'\n[verify] production state at {args.state_path} still present (hash={prod_hash})')
    print(f'[verify] dryrun temp state at {tmp_state} (will be cleaned by /tmp GC)')
    return 0
if __name__ == '__main__':
    raise SystemExit(main())