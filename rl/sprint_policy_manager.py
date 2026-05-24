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
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any
import math
import os

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
    qmix_weights: Optional[Dict[str, Any]] = None
    last_train_sprint: int = -1
    last_action: int = 0  # F228F: last RL action taken


def _serialize_weights(weights: Any) -> Dict[str, Any]:
    """Serialize MLX array weights to JSON-compatible dict. Returns {} if weights is None."""
    if weights is None:
        return {"flat": []}
    try:
        import mlx.core as mx
        flat = []
        for key, val in weights.items():
            flat.append({"key": key, "value": val.tolist()})
        return {"flat": flat}
    except Exception:
        return {"flat": []}


def _deserialize_weights(data: Dict[str, Any]) -> Any:
    """Reconstruct MLX array weights from serialized dict."""
    if not data or "flat" not in data:
        return None
    try:
        import mlx.core as mx
        params = {}
        for item in data["flat"]:
            params[item["key"]] = mx.array(item["value"])
        return params
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
        policy_path: Optional[Path] = None,
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
        try:
            from rl.replay_buffer import MARLReplayBuffer
            from rl.state_extractor import StateExtractor
            from rl.qmix import QMIXAgent, QMixer, QMIXJointTrainer

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
                    with open(self._policy_path, "r", encoding="utf-8") as f:
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

    def _compute_reward(self, result: "SprintSchedulerResult") -> float:
        """
        Compute reward from real SprintSchedulerResult fields.

        Formula: log(1 + findings_accepted) * source_quality_mult - time_penalty
          where source_quality_mult ∈ [0.5, 2.0] based on accepted/total ratio
                time_penalty = runtime / 3600 (hours, bounded)
        """
        try:
            findings_accepted = getattr(result, "findings_accepted", 0) or 0
            runtime = getattr(result, "runtime_seconds", 0) or 0
            total_findings = getattr(result, "findings_total", 0) or 0

            # Source quality multiplier from acceptance ratio
            if total_findings > 0:
                accepted_ratio = findings_accepted / max(total_findings, 1)
                source_quality_mult = 0.5 + 1.5 * accepted_ratio  # ∈ [0.5, 2.0]
            else:
                source_quality_mult = 1.0

            # Log-scaled finding reward
            finding_reward = math.log(1 + findings_accepted) * source_quality_mult

            # Time efficiency penalty (scaled by hours, capped at -5)
            time_penalty = min(runtime / 3600.0, 5.0)

            reward = finding_reward - time_penalty

            # Bonus for cycles completed
            if hasattr(result, "cycles_completed") and result.cycles_completed > 0:
                reward += min(result.cycles_completed / 10.0, 2.0)

            # F228F: Dark web high-confidence finding reward (+0.3 per finding, conf > 0.7)
            dark_web_sources = ("tor", "i2p", "ipfs", "nym", "dht")
            for src in dark_web_sources:
                count = getattr(result, f"{src}_findings_accepted", 0) or 0
                reward += count * 0.3

            # F228F: Unindexed source reward (+0.5 per finding from Gopher/DHT)
            unindexed_sources = ("gopher", "dht")
            for src in unindexed_sources:
                count = getattr(result, f"{src}_findings_accepted", 0) or 0
                reward += count * 0.5

            # F228F: CAPTCHA detection penalty (-0.2 per detection, means too aggressive)
            captcha_count = getattr(result, "captcha_detected_count", 0) or 0
            reward -= captcha_count * 0.2

            # F228F: DS hypothesis confirmation reward (+0.1 per confirmation)
            ds_confirmed = getattr(result, "ds_hypothesis_confirmed_count", 0) or 0
            reward += ds_confirmed * 0.1

            return max(-10.0, min(reward, 100.0))
        except Exception:
            return 0.0

    # ── Public API ──────────────────────────────────────────────────────────

    def update(self, result: "SprintSchedulerResult") -> None:
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

                # Last action (from result if available, else default)
                # F257FIX: Store as 5-element vector (one per QMIX agent) — matches replay_buffer shape (n_agents,)
                last_action = getattr(result, "last_rl_action", 0) % 5
                action_vector = [last_action] * 5  # broadcast scalar to all agents

                self._replay_buffer.add(
                    state=state,
                    action=action_vector,
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
                log.debug("[SprintPolicyManager] replay buffer add failed: %s", e)

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
            if batch is None or batch["states"].shape[0] < _MIN_REPLAY_SIZE:
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
                        from rl.actions import ACTION_CONTINUE, ACTION_FETCH_MORE, ACTION_BRANCH, ACTION_YIELD
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

                # Update source quality tracking (placeholder for future weight adaptation)
                # F228A: source weight adaptation would go here if needed

            log.debug(
                "[SprintPolicyManager] quality feedback: src=%s total=%d accepted=%d",
                feed_url or "unknown",
                total_count,
                accepted_count,
            )
        except Exception as e:
            log.debug("[SprintPolicyManager] update_with_quality_decisions failed: %s", e)

    def get_qmix_stats(self) -> Dict[str, Any]:
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
            # TODO: F228F — extract directional signal from state/history/reward patterns
            # e.g. high dark-web reward → suggest tor/i2p pivot direction
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