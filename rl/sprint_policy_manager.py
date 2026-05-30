"""
SprintPolicyManager — opt-in RL sprint policy layer.
Plugged into SprintScheduler.run() as a policy advisor.
Does NOT own lifecycle or exec — only provides action hints.

Design:
- Disabled by default — zero effect on sprint behavior when not enabled
- Every 5th sprint is exploration (ACTION_DEEP_DIVE), rest are exploitation
- QMIX Q-network trained every N sprints from MARLReplayBuffer samples
- Policy persists via JSON file so state survives instance restarts
- Reward computed from real SprintSchedulerResult fields, not placeholder telemetry

Canonical owner: runtime/sprint_scheduler.py (integration point)
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

try:
    import compression.zstd as _zstd
    ZSTD_AVAILABLE = True
except (ImportError, Exception):
    ZSTD_AVAILABLE = False
    _zstd = None

log = logging.getLogger(__name__)

# Path for persisted policy state
_POLICY_PATH = Path(__file__).parent / ".sprint_policy_state.json"
# Exploration interval (every N sprints)
_EXPLORATION_INTERVAL = 5
# Epsilon for epsilon-greedy exploration
_DEFAULT_EPSILON = 0.1
# QMIX training interval (every N sprints)
_QMIX_TRAIN_INTERVAL = 10
# Replay buffer minimum size before training
_MIN_REPLAY_SIZE = 64
# Batch size for QMIX training
_TRAIN_BATCH_SIZE = 32

# QMIX field names in policy state JSON
_QMIX_FIELD = "qmix_weights"


@dataclass
class SprintPolicyState:
    """Serialized policy state persisted to disk."""
    sprint_sequence_number: int = 0
    epsilon: float = _DEFAULT_EPSILON
    total_reward: float = 0.0
    sprint_rewards: list[float] = field(default_factory=list)
    # QMIX network weights (serialized MLX arrays when MLX available)
    qmix_weights: dict[str, Any] | None = None
    last_train_sprint: int = -1
    last_action: int = 0  # F228F: last RL action taken


def _serialize_weights(weights: Any) -> dict[str, Any]:
    """Serialize MLX array weights to JSON-compatible dict. Returns {} if weights is None."""
    if weights is None:
        return {"flat": []}
    try:
        # F257FIX: weights is nested dict (mixer, agent_0, ...) with nested param dicts
        # Use tree_map to convert all mlx arrays to lists recursively
        from mlx.utils import tree_map
        flat_weights = tree_map(lambda x: x.tolist() if hasattr(x, 'tolist') else x, weights)
        # Flatten into list of key paths and values
        flat = []
        def collect(key, val, path=""):
            if isinstance(val, dict):
                for k, v in val.items():
                    collect(k, v, f"{path}.{k}" if path else k)
            else:
                flat.append({"key": path, "value": val})
        collect("_root", flat_weights)
        return {"flat": flat}
    except Exception:
        return {"flat": []}


def _deserialize_weights(data: dict[str, Any]) -> Any:
    """Reconstruct MLX array weights from serialized dict."""
    if not data or "flat" not in data:
        return None
    try:
        import mlx.core as mx
        # F257FIX: weights is nested dict (mixer, agent_0, ...) with nested param dicts
        # Reconstruct nested structure from flat list
        nested = {}
        for item in data["flat"]:
            key_parts = item["key"].split(".")
            value = mx.array(item["value"])
            # Navigate/create nested structure
            current = nested
            for part in key_parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            current[key_parts[-1]] = value
        return nested
    except Exception:
        return None


class SprintPolicyManager:
    """
    Opt-in RL policy advisor for sprint exec.

    Integration: called by SprintScheduler after each sprint run:
      1. policy.get_action() → action hint (exploration vs exploitation)
      2. policy.update(result) → compute reward + (optionally) train QMIX

    State persists via JSON at _POLICY_PATH — survives instance restarts.
    """

    def __init__(
        self,
        enabled: bool = os.environ.get("HLEDAC_DISABLE_RL") != "1",
        policy_path: Path | None = None,
        epsilon: float = _DEFAULT_EPSILON,
        exploration_interval: int = _EXPLORATION_INTERVAL,
        qmix_train_interval: int = _QMIX_TRAIN_INTERVAL,
        rl_train_mode: bool = False,
    ) -> None:
        """
        Args:
            enabled: If False (default), all methods are no-op — no effect on sprint behavior
            policy_path: Override path for persisted state; defaults to _POLICY_PATH
            epsilon: Epsilon for epsilon-greedy fallback (used only when QMIX unavailable)
            exploration_interval: Every N sprints is exploration (default 5)
            qmix_train_interval: Every N sprints run QMIX training step (default 10)
            rl_train_mode: If True, QMIX training is active; if False, inference-only (default)
        """
        self._enabled = enabled
        self._policy_path = policy_path or _POLICY_PATH
        self._epsilon = epsilon
        self._exploration_interval = exploration_interval
        self._qmix_train_interval = qmix_train_interval
        self._rl_train_mode = rl_train_mode
        self._state = SprintPolicyState()
        self._loaded = False
        self._pending_feedback: dict[str, dict[str, int]] = {}  # F228A: per-source quality feedback pending delegation

    @property
    def enabled(self) -> bool:
        """Expose _enabled for external callers (e.g., SprintScheduler F228A block)."""
        return self._enabled

    def inject_scheduler(self, scheduler: Any) -> None:
        """Inject SprintPolicyManager ref (opt-in RL layer)."""
        # No-op when disabled — F228A invariant: policy must be enabled before wiring
        if not self._enabled:
            return
        self._policy_manager = scheduler
        # Bidirectional wiring: allow policy manager to delegate quality feedback
        # adaptation back to this scheduler's _adapt_source_weights_from_feedback
        if hasattr(scheduler, "_adapt_source_weights_from_feedback"):
            self._scheduler = scheduler
            self._pending_feedback = {}  # F228A: reset pending on re-inject

        # QMIX components — initialized lazily on first enable
        self._replay_buffer = None
        self._state_extractor = None
        self._qmix_trainer = None
        self._agents = None
        self._reward_history: list = []  # F257FIX: always initialize (used in update regardless of rl_train_mode)
        if self._enabled:
            self._load()
            # F228F: initialize reward_history from loaded sprint_rewards
            if self._state.sprint_rewards:
                self._reward_history = list(self._state.sprint_rewards[-100:])

    # ── QMIX Initialization ─────────────────────────────────────────────────

    def _init_qmix(self) -> None:
        """Lazily init QMIX components: replay buffer, state extractor, agents, trainer."""
        if not self._enabled:
            return
        # F257FIX: Only initialize once — prevent buffer reset on every update()
        if self._qmix_trainer is not None:
            return
        try:
            from rl.qmix import QMIXAgent, QMixer, QMIXJointTrainer
            from rl.replay_buffer import MARLReplayBuffer
            from rl.state_extractor import StateExtractor

            self._state_extractor = StateExtractor(state_dim=12)

            self._replay_buffer = MARLReplayBuffer(
                capacity=50000,
                state_dim=12,
                n_agents=5,
            )

            # 5 agents: one per action type
            self._agents = {
                str(i): QMIXAgent(agent_id=str(i), state_dim=12, hidden_dim=64)
                for i in range(5)
            }

            mixer = QMixer(n_agents=5, state_dim=12, embedding_dim=32)
            target_mixer = QMixer(n_agents=5, state_dim=12, embedding_dim=32)
            self._qmix_trainer = QMIXJointTrainer(
                agents=self._agents,
                mixer=mixer,
                target_mixer=target_mixer,
                gamma=0.99,
                tau=0.005,
            )

            # F257FIX: Load persisted weights into joint_model after init
            # _load() ran earlier and set self._state.qmix_weights from disk
            # but weights were never deserialized and applied to the model
            if self._state.qmix_weights and hasattr(self._qmix_trainer, 'joint_model'):
                try:
                    loaded = _deserialize_weights(self._state.qmix_weights)
                    if loaded:
                        current_params = dict(self._qmix_trainer.joint_model.parameters())
                        updated_params = {k: loaded.get(k, v) for k, v in current_params.items()}
                        self._qmix_trainer.joint_model.update(updated_params)
                        log.debug("[SprintPolicyManager] Loaded %d weight tensors into joint_model", len(loaded))
                except Exception as e:
                    log.debug("[SprintPolicyManager] Weight loading failed (safe to ignore): %s", e)

            log.info("[SprintPolicyManager] QMIX components initialized (rl_train_mode=%s)", self._rl_train_mode)

        except ImportError as e:
            log.debug("[SprintPolicyManager] QMIX ImportError (MLX unavailable): %s", e)
            self._qmix_trainer = None
            self._agents = None
        except Exception as e:
            log.warning("[SprintPolicyManager] QMIX init failed: %s", e)
            self._qmix_trainer = None
            self._agents = None

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load persisted state from disk. Prefer .json.zst, fallback to .json."""
        if self._loaded:
            return
        self._loaded = True
        try:
            if self._policy_path.exists():
                suffix = self._policy_path.suffix
                if suffix == ".zst" or str(self._policy_path).endswith(".json.zst"):
                    if ZSTD_AVAILABLE and _zstd:
                        with open(self._policy_path, "rb") as f:
                            raw = _zstd.decompress(f.read())
                        data = json.loads(raw.decode("utf-8"))
                    else:
                        return
                else:
                    with open(self._policy_path, encoding="utf-8") as f:
                        data = json.load(f)
                self._state = SprintPolicyState(**data)
                log.debug(
                    "[SprintPolicyManager] Loaded state: sprint=%d epsilon=%.3f total_reward=%.2f",
                    self._state.sprint_sequence_number,
                    self._state.epsilon,
                    self._state.total_reward,
                )
        except Exception as e:
            log.debug("[SprintPolicyManager] _load failed (safe to ignore): %s", e)

    def _save(self) -> None:
        """Persist state to disk as .json.zst. Fail-safe — do not crash on write errors."""
        if not self._enabled:
            return
        try:
            payload = {
                "sprint_sequence_number": self._state.sprint_sequence_number,
                "epsilon": self._state.epsilon,
                "total_reward": self._state.total_reward,
                "sprint_rewards": self._state.sprint_rewards[-100:],
                _QMIX_FIELD: self._state.qmix_weights,
                "last_train_sprint": self._state.last_train_sprint,
            }
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if ZSTD_AVAILABLE and _zstd:
                compressed = _zstd.compress(encoded, level=3)
                with open(self._policy_path, "wb") as f:
                    f.write(compressed)
            else:
                with open(self._policy_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(payload))
            log.debug("[SprintPolicyManager] State persisted to %s", self._policy_path)
        except Exception as e:
            log.debug("[SprintPolicyManager] _save failed: %s", e)

    # ── Reward computation ───────────────────────────────────────────────────

    def _get_finding_count(self, result: SprintSchedulerResult, prefix: str) -> int:
        """F235: Fallback chain for finding count fields — M1 memory safe."""
        for suffix in ("accepted", "produced", "ingested"):
            val = getattr(result, f"{prefix}_findings_{suffix}", None)
            if val is not None:
                return val
        return 0

    def _compute_reward(self, result: SprintSchedulerResult) -> float:
        """
        Compute reward from real SprintSchedulerResult fields.

        Formula per F257 spec:
          reward = log1p(findings_accepted) * source_quality_multiplier
                  - time_penalty * (runtime / budget_seconds)
                  + novelty_bonus * new_iocs_ratio
          clipped to [-1.0, 5.0]
        """
        try:
            findings_accepted = getattr(result, "findings_accepted", 0) or 0
            runtime = getattr(result, "actual_duration_s", 0.0) or 0.0
            new_iocs = getattr(result, 'new_iocs', 0) or 0
            total_in = findings_accepted + getattr(result, 'findings_deduplicated', 0)

            # Budget seconds (default 1800s = 30min)
            budget_seconds = getattr(result, 'budget_seconds', 1800.0) or 1800.0

            # Source quality multiplier from acceptance ratio
            if total_in > 0:
                accepted_ratio = findings_accepted / total_in
                source_quality_mult = 0.5 + 1.5 * accepted_ratio  # ∈ [0.5, 2.0]
            else:
                source_quality_mult = 1.0

            # Log-scaled finding reward
            finding_reward = math.log1p(findings_accepted) * source_quality_mult

            # Time efficiency penalty (runtime / budget, capped at 1.0)
            time_penalty = min(runtime / max(budget_seconds, 1.0), 1.0)

            # Novelty bonus from new IOCs
            new_iocs_ratio = min(new_iocs / max(findings_accepted, 1), 1.0)
            novelty_bonus = 2.0  # Scale factor for new IOC ratio

            reward = (
                finding_reward
                - time_penalty
                + novelty_bonus * new_iocs_ratio
            )

            # F228F/F235: Dark web high-confidence finding reward (+0.3 per finding)
            for src in ("tor", "i2p", "nym", "dht"):
                count = self._get_finding_count(result, src)
                reward += count * 0.3

            # ipfs exists as findings_accepted directly
            reward += (getattr(result, 'ipfs_findings_accepted', 0) or 0) * 0.3

            # F228F/F235: Unindexed source reward (+0.5 per finding from Gopher)
            reward += self._get_finding_count(result, 'gopher') * 0.5

            # Dedup efficiency — higher dedup ratio = more wasted work
            if total_in > 0:
                dedup_ratio = findings_accepted / total_in
                reward += dedup_ratio * 0.5

            # Clamp to [-1.0, 5.0] per F257 spec
            return max(-1.0, min(5.0, reward))
        except Exception:
            return 0.0

    # ── Public API ──────────────────────────────────────────────────────────

    def update(self, result: SprintSchedulerResult) -> None:
        """
        Update policy state from the completed sprint result.

        Called by SprintScheduler after run() returns.
        Does nothing if policy is disabled.

        Steps:
          1. Compute reward from result fields
          2. Extract observation via StateExtractor
          3. Store (state, action, reward, next_state) in MARLReplayBuffer
          4. Every _qmix_train_interval sprints → run QMIX train_step()
          5. Persist updated state (including QMIX weights) to disk
        """
        if not self._enabled:
            return

        self._init_qmix()

        self._state.sprint_sequence_number += 1
        reward = self._compute_reward(result)

        # Accumulate reward stats
        self._state.total_reward += reward
        self._state.sprint_rewards.append(reward)
        # GHOST_INVARIANTS: sprint_rewards bounded — prevents unbounded list growth
        if len(self._state.sprint_rewards) > 100:
            self._state.sprint_rewards = self._state.sprint_rewards[-100:]
        # F228F: reward_history ring buffer update
        self._reward_history.append(reward)
        if len(self._reward_history) > 100:
            self._reward_history = self._reward_history[-100:]

        # ── Replay buffer storage ────────────────────────────────────────────
        if self._replay_buffer is not None and self._state_extractor is not None:
            try:
                # Current state observation
                state = self._state_extractor.extract(result)
                next_state = self._state_extractor.extract_next(result)

                # F257FIX: Convert numpy/mlx array to list for replay buffer
                if hasattr(state, 'tolist'):
                    state = state.tolist()
                if hasattr(next_state, 'tolist'):
                    next_state = next_state.tolist()

                # Last action (from result if available, else default)
                # F257FIX: Store as numpy array for push() method signature
                last_action = getattr(result, "last_rl_action", 0) % 5
                action_vector = np.array([last_action] * 5, dtype=np.int32)

                self._replay_buffer.push(
                    state=state,
                    actions=action_vector,
                    reward=reward,
                    next_state=next_state,
                    done=False,
                )
                log.debug(
                    "[SprintPolicyManager] Replay buffer size: %d, last reward=%.3f",
                    self._replay_buffer.size,
                    reward,
                )
            except Exception as e:
                log.debug("[SprintPolicyManager] replay buffer push failed: %s", e)

        # ── QMIX training step ────────────────────────────────────────────────
        if (
            self._rl_train_mode
            and self._qmix_trainer is not None
            and self._replay_buffer is not None
            and self._state.sprint_sequence_number > 0
            and self._state.sprint_sequence_number % self._qmix_train_interval == 0
            and self._replay_buffer.size >= _MIN_REPLAY_SIZE
        ):
            self._run_qmix_training()
        elif (
            self._state.sprint_sequence_number % 50 == 0
        ):
            log.debug(
                "[SprintPolicyManager] sprint=%d replay=%s qmix=%s train_mode=%s",
                self._state.sprint_sequence_number,
                self._replay_buffer.size if self._replay_buffer else None,
                self._qmix_trainer is not None,
                self._rl_train_mode,
            )

        self._save()

    def _run_qmix_training(self) -> None:
        """Sample batch from replay buffer and run QMIX joint training step."""
        if self._qmix_trainer is None or self._replay_buffer is None:
            return
        # G1: UMA budget pre-check — skip if M1 memory critical (2GB training limit)
        try:
            from hledac.universal.utils.uma_budget import get_uma_budget
            uma = get_uma_budget()
            if uma.is_critical():
                log.debug("[SprintPolicyManager] Skipping QMIX train_step — M1 memory critical")
                return
        except Exception:
            pass  # UMA check is advisory; proceed if unavailable
        try:
            batch = self._replay_buffer.sample(_TRAIN_BATCH_SIZE)
            # F257FIX: Check replay buffer size, not batch size
            if batch is None or self._replay_buffer.size < _MIN_REPLAY_SIZE:
                return

            # F257FIX: QMIXJointTrainer.update() not train_step() — defensive hasattr
            _train = getattr(self._qmix_trainer, 'update', None) or getattr(self._qmix_trainer, 'train_step', None)
            if _train is None:
                log.error("[SprintPolicyManager] No training method found on QMIXJointTrainer")
                return
            loss = _train(batch)

            # Persist updated weights
            if hasattr(self._qmix_trainer, "joint_model"):
                self._state.qmix_weights = _serialize_weights(
                    self._qmix_trainer.joint_model.parameters()
                )
            self._state.last_train_sprint = self._state.sprint_sequence_number

            # M1 memory management per GHOST_INVARIANTS I11
            try:
                import mlx.core as mx
                mx.eval([])
                mx.metal.clear_cache()
            except Exception:
                pass

            log.info(
                "[SprintPolicyManager] QMIX train step %d: loss=%.4f replay=%d",
                self._state.sprint_sequence_number,
                loss,
                self._replay_buffer.size,
            )
        except Exception as e:
            log.debug("[SprintPolicyManager] QMIX training failed: %s", e)

    def should_explore(self) -> bool:
        """
        Decide whether the next sprint should be exploration (deep dive) or exploitation.

        Exploration triggered when:
          - every _exploration_interval sprints, OR
          - epsilon-greedy random flip

        Returns False (exploitation) by default when disabled.
        """
        if not self._enabled:
            return False

        seq = self._state.sprint_sequence_number

        # Periodic exploration
        if seq % self._exploration_interval == 0:
            return True

        # Epsilon-greedy fallback
        import random
        if random.random() < self._epsilon:
            return True

        return False

    def get_action(self) -> int:
        """
        Return the RL action hint for the next sprint.

        Only valid to call when enabled; otherwise returns ACTION_CONTINUE.
        """
        if not self._enabled:
            from rl.actions import ACTION_CONTINUE
            return ACTION_CONTINUE

        if self.should_explore():
            from rl.actions import ACTION_DEEP_DIVE
            return ACTION_DEEP_DIVE

        # QMIX inference: if weights loaded and agents available, use argmax Q
        # Note: requires a result to extract state from — fallback to epsilon-greedy
        if self._qmix_trainer is not None and self._agents is not None and self._state_extractor is not None:
            try:
                # Try to get state from attached scheduler's current result
                if hasattr(self, '_scheduler') and self._scheduler is not None:
                    result = getattr(self._scheduler, '_result', None)
                    if result is not None:
                        state = self._state_extractor.extract(result)
                        best_action = 0
                        best_q = float("-inf")
                        for agent_id, agent in self._agents.items():
                            q_val = float(agent.q_net(state)[0].item())
                            if q_val > best_q:
                                best_q = q_val
                                best_action = int(agent_id)
                        # Map to action constants
                        from rl.actions import ACTION_BRANCH, ACTION_CONTINUE, ACTION_FETCH_MORE, ACTION_YIELD
                        ACTION_MAP = {0: ACTION_CONTINUE, 1: ACTION_FETCH_MORE, 2: ACTION_BRANCH, 3: ACTION_YIELD, 4: ACTION_CONTINUE}
                        return ACTION_MAP.get(best_action, ACTION_CONTINUE)
            except Exception:
                pass

        from rl.actions import ACTION_CONTINUE
        return ACTION_CONTINUE

    def update_with_quality_decisions(self, decisions: list, feed_url: str = "") -> None:
        """
        F228A: Receive per-source quality feedback from SprintScheduler.

        Called after quality decisions are computed (per-feed acceptance/rejection).
        Used to adapt source weights for next sprint's acquisition planning.

        Args:
            decisions: List of FindingQualityDecision (msgspec.Struct) or dict
            feed_url: Feed URL for dict-based decisions without source_family
        """
        if not self._enabled:
            return
        try:
            accepted_count = 0
            total_count = 0

            for decision in decisions:
                # Handle both FindingQualityDecision (msgspec.Struct) and dict (ActivationResult)
                if isinstance(decision, dict):
                    accepted = bool(decision.get("accepted", False))
                    source_family = str(decision.get("source_family", feed_url))
                else:
                    # msgspec.Struct — attr access
                    accepted = getattr(decision, "accepted", False)
                    source_family = str(getattr(decision, "source_family", feed_url))

                total_count += 1
                if accepted:
                    accepted_count += 1

                # F228A: source weight adaptation — mirror B.6 clamped delta logic from scheduler
            _ratio = accepted_count / total_count if total_count > 0 else 0.0
            if _ratio >= 0.7:
                _delta = 1.10  # +10%
            elif _ratio >= 0.4:
                _delta = 1.05  # +5%
            elif _ratio >= 0.15:
                _delta = 1.00  # neutral
            else:
                _delta = 0.95  # -5%

            _src = source_family or feed_url or "unknown"
            _cur = getattr(self, "_src_quality_weights", {}).get(_src, 1.0)
            _new = max(0.3, min(2.5, _cur * _delta))
            if not hasattr(self, "_src_quality_weights"):
                self._src_quality_weights: dict[str, float] = {}
            self._src_quality_weights[_src] = _new

            _delta_abs = abs(_new - _cur)
            if _delta_abs > 0.05:
                log.debug(
                    "[F228A] src weight adaptation: %s (%d/%d=%.0f%%) %.3f → %.3f",
                    _src, accepted_count, total_count, _ratio * 100, _cur, _new,
                )

            # F228A: accumulate per-source feedback into _pending_feedback
            # Bounded at 200 unique sources (fail-soft on overflow)
            _src_key = source_family or feed_url or "unknown"
            if len(self._pending_feedback) < 200 or _src_key in self._pending_feedback:
                if _src_key not in self._pending_feedback:
                    self._pending_feedback[_src_key] = {"fetched": 0, "accepted": 0}
                self._pending_feedback[_src_key]["fetched"] += total_count
                self._pending_feedback[_src_key]["accepted"] += accepted_count

            # F228A: delegate accumulated feedback to scheduler when available
            if self._scheduler is not None:
                try:
                    for _fk, _fv in self._pending_feedback.items():
                        if _fk not in self._scheduler._source_quality_feedback:
                            self._scheduler._source_quality_feedback[_fk] = {"fetched": 0, "accepted": 0}
                        self._scheduler._source_quality_feedback[_fk]["fetched"] += _fv["fetched"]
                        self._scheduler._source_quality_feedback[_fk]["accepted"] += _fv["accepted"]
                    self._pending_feedback.clear()
                except Exception:
                    pass  # fail-soft: delegation is best-effort

            log.debug(
                "[SprintPolicyManager] quality feedback: src=%s total=%d accepted=%d",
                feed_url or "unknown",
                total_count,
                accepted_count,
            )
        except Exception as e:
            log.debug("[SprintPolicyManager] update_with_quality_decisions failed: %s", e)

    def get_qmix_stats(self) -> dict[str, Any]:
        """Return QMIX training stats for observability."""
        return {
            "sprint_sequence": self._state.sprint_sequence_number,
            "total_reward": self._state.total_reward,
            "replay_size": self._replay_buffer.size if self._replay_buffer else 0,
            "last_train_sprint": self._state.last_train_sprint,
            "rl_train_mode": self._rl_train_mode,
            "qmix_available": self._qmix_trainer is not None,
        }

    # ── Next Pivot Advisory ─────────────────────────────────────────────────

    def suggest_next_pivot(
        self, current_findings: list, memory_snapshot: dict | None = None
    ) -> list[dict]:
        """
        F228F: Propose pivot directions based on accumulated reward patterns.

        Called by SprintScheduler at post-run advisory phase before next_pivots
        are generated. Policy may suggest direction hints derived from RL state.

        Args:
            current_findings: List of findings from the completed sprint.
            memory_snapshot: Optional memory/state snapshot from the scheduler.

        Returns:
            List of pivot suggestion dicts with keys: pivot_type, reason, confidence.
            Empty list when disabled.
        """
        if not self._enabled:
            return []

        # Fallback: no pivot suggestions when QMIX is unavailable
        if self._qmix_trainer is None or self._agents is None:
            return []

        try:
            suggestions: list[dict] = []

            if self._state_extractor is None:
                return []

            # F235: RL-guided pivot from Q-values
            try:
                state = self._state_extractor.extract(memory_snapshot or {})
            except Exception:
                state = None

            if state is not None:
                # Get Q-values from each agent, pick argmax
                best_action = 0
                best_q = float('-inf')
                for agent_id, agent in self._agents.items():
                    q_val = float(agent.q_net(state)[0].item())
                    if q_val > best_q:
                        best_q = q_val
                        best_action = int(agent_id)

                pivot_map = {
                    0: "standard",
                    1: "dark_surface",
                    2: "gopher",
                    3: "bgp_enrichment",
                    4: "academic",
                }
                pivot_type = pivot_map.get(best_action, "standard")
                confidence = float(best_q)

                suggestions.append({
                    "pivot_type": pivot_type,
                    "confidence": confidence,
                    "reason": f"Q={confidence:.3f} eps={self._epsilon:.3f}",
                })

            return suggestions
        except Exception:
            return []

    def get_telemetry(self) -> dict[str, Any]:
        """
        Return RL telemetry snapshot for sprint_scheduler telemetry reporting.

        F228F: rl_enabled, rl_epsilon, rl_total_reward, rl_last_action.
        """
        return {
            "rl_enabled": self._enabled,
            "rl_epsilon": self._epsilon,
            "rl_total_reward": self._state.total_reward,
            "rl_last_action": self._state.last_action,
        }

    def get_reward_stats(self) -> dict[str, Any]:
        """
        F228F: Return reward distribution statistics.
        """
        if not self._reward_history:
            return {"mean": 0.0, "min": 0.0, "max": 0.0, "last_10": [], "count": 0}
        last_10 = self._reward_history[-10:]
        return {
            "mean": sum(self._reward_history) / len(self._reward_history),
            "min": min(self._reward_history),
            "max": max(self._reward_history),
            "last_10": last_10,
            "count": len(self._reward_history),
        }

    def attach_scheduler(self, scheduler) -> None:
        """Attach scheduler reference for state extraction in get_action()."""
        self._scheduler = scheduler

    # ── Reset ────────────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Reset internal state and delete persisted file. Does nothing when disabled."""
        if not self._enabled:
            return
        self._state = SprintPolicyState()
        self._loaded = True
        try:
            if self._policy_path.exists():
                self._policy_path.unlink()
        except Exception as e:
            log.W(f"[SprintPolicyManager] Failed to delete policy state file: {e}")
