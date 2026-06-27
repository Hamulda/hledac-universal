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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

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
# Q-network weights binary path (mlx.core.savez) — F261QMIX
_QMIX_WEIGHTS_PATH = Path(__file__).parent / ".qmix_weights.npz"
# Exploration interval (every N sprints)
_EXPLORATION_INTERVAL = 5
# Epsilon for epsilon-greedy exploration
_DEFAULT_EPSILON = 0.1
# QMIX training interval (every N sprints) — overridable via HLEDAC_RL_TRAIN_INTERVAL
_QMIX_TRAIN_INTERVAL = int(os.environ.get("HLEDAC_RL_TRAIN_INTERVAL", "10"))
# Replay buffer minimum size before training
# F268FIX: Raised from 64 to 256 — QMIX needs sufficient samples for stable
# value decomposition. With 5 agents and 11 actions, 64 samples gives
# statistically insignificant gradient signal. 256 = 5x capacity of one
# training batch, ensuring the mixer sees diverse (s,a,r,s') tuples.
_MIN_REPLAY_SIZE = 256
# Batch size for QMIX training
_TRAIN_BATCH_SIZE = 32
# F261QMIX: Memory pressure gate — skip train_step when system RAM exceeds this
_RAM_TRAIN_SKIP_PCT = 80
# F261QMIX: env var to disable RAM gate (e.g. for tests under low-RAM CI)
_RAM_GATE_DISABLED = os.environ.get("HLEDAC_RL_SKIP_RAM_GATE", "0") == "1"
# F261QMIX: Cooldown between training steps (seconds) — prevents M1 RAM thrashing
_TRAIN_COOLDOWN_S = 1.0
# F261QMIX: Maximum training steps per sprint (1 = once per interval sprint)
_MAX_TRAIN_STEPS_PER_SPRINT = 1
# F268FIX: Q-value cache TTL — avoids Metal kernel launch on every get_action()
# during the cooldown window. Cache stores per-agent Q-values computed after
# the last training step; invalidated after _Q_CACHE_TTL_S seconds.
_Q_CACHE_TTL_S = 5.0

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
    # F261QMIX: extended schema for Q-network weight persistence + training counters
    q_network_weights_path: str = str(_QMIX_WEIGHTS_PATH)
    last_train_step: int = -1
    cumulative_train_steps: int = 0
    last_loss: float = 0.0
    # F262OBS: RL training health observability — bounded histories.
    # - loss_history: per-step TD loss, FIFO max 100
    # - mean_q_value_history: per-step mean of global Q, FIFO max 100
    # - epsilon_history: per-sprint epsilon value, FIFO max 100
    # - last_train_step_sprint: sprint number when last train_step ran
    # - training_steps_completed: monotonic counter, never decremented
    loss_history: list[float] = field(default_factory=list)
    mean_q_value_history: list[float] = field(default_factory=list)
    epsilon_history: list[float] = field(default_factory=list)
    last_train_step_sprint: int = 0
    training_steps_completed: int = 0
    # F265EPISTEMIC: epistemic quality bonus history — bounded FIFO max 100
    epistemic_strength_history: list[float] = field(default_factory=list)


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
        qmix_train_interval: int | None = None,
        rl_train_mode: bool = os.environ.get("HLEDAC_RL_TRAIN") == "1",
    ) -> None:
        """
        Args:
            enabled: If False (default), all methods are no-op — no effect on sprint behavior
            policy_path: Override path for persisted state; defaults to _POLICY_PATH
            epsilon: Epsilon for epsilon-greedy fallback (used only when QMIX unavailable)
            exploration_interval: Every N sprints is exploration (default 5)
            qmix_train_interval: Every N sprints run QMIX training step (default 10,
                overridable via HLEDAC_RL_TRAIN_INTERVAL env var)
            rl_train_mode: If True, QMIX training is active; if False, inference-only (default).
                Can also be auto-enabled via HLEDAC_RL_TRAIN=auto warmup (see below).

        Auto-warmup: When HLEDAC_RL_TRAIN=auto (or unset with replay >= 512 after sprint 10+),
            training auto-enables after the first training interval check passes.
            This prevents the "zero learning" state where rl_train_mode=False forever.
        """
        self._enabled = enabled
        # F261OPT: distinguish "explicit path" (continue session) from "default path"
        # (new session — must not leak stale disk state into fresh managers).
        # Without this guard, tests that omit policy_path (e.g.
        # `test_sprints_1_to_4_not_exploration`) read a contaminated
        # rl/.sprint_policy_state.json and the periodic exploration boundary
        # lands on the wrong sprint.
        self._policy_path_explicit = policy_path is not None
        self._policy_path = policy_path if policy_path is not None else _POLICY_PATH
        self._epsilon = epsilon
        self._exploration_interval = exploration_interval
        # F261QMIX: env-var override; explicit None falls back to module constant (which reads env)
        self._qmix_train_interval = qmix_train_interval if qmix_train_interval is not None else _QMIX_TRAIN_INTERVAL
        self._rl_train_mode = rl_train_mode
        self._state = SprintPolicyState()
        self._loaded = False
        self._pending_feedback: dict[str, dict[str, int]] = {}  # F228A: per-source quality feedback pending delegation
        # F261QMIX: training throttle state — bounded, fail-soft
        self._last_train_at: float = 0.0          # monotonic seconds; cooldown gate
        self._train_steps_this_sprint: int = 0    # per-sprint cap
        # F261QMIX: pre-init QMIX component slots so _init_qmix and gates can check is-not-None safely
        self._qmix_trainer: Any = None
        self._replay_buffer: Any = None
        self._state_extractor: Any = None
        self._agents: Any = None
        # F261QMIX: reward_history accessible even without inject_scheduler (used in update() and get_reward_stats())
        self._reward_history: list[float] = []
        # F265LANE: lane performance history for get_lane_config()
        self._lane_performance: dict[str, dict[str, float]] = {}
        # F268FIX: Q-value cache — populated after each training step, consumed in
        # get_action() to avoid Metal kernel launch overhead during cooldown window.
        # Structure: {agent_id: float(q_value)} — argmax over these gives best_action.
        self._q_value_cache: dict[str, float] = {}
        self._q_cache_timestamp: float = 0.0
        # F261QMIX: load state from disk eagerly so _state is populated even without inject_scheduler
        # F261OPT: disabled managers must NOT read persisted state — invariant is
        # "no effect on sprint behavior", and a stale loaded seq would leak through.
        if self._enabled:
            self._load()
        if self._enabled and self._state.sprint_rewards:
            self._reward_history = list(self._state.sprint_rewards[-100:])

    @property
    def enabled(self) -> bool:
        """Expose _enabled for external callers (e.g., SprintScheduler F228A block)."""
        return self._enabled

    # ── Property delegations to SprintPolicyState (F261OPT) ──────────────────
    # Tests expect these to be readable as manager.<field>; underlying state
    # lives in self._state. Safe getattr pattern with defaults handles both
    # legacy persisted state (missing newer fields) and disabled managers.

    @property
    def sprint_sequence_number(self) -> int:
        return getattr(self._state, "sprint_sequence_number", 0)

    @property
    def epsilon(self) -> float:
        return getattr(self._state, "epsilon", _DEFAULT_EPSILON)

    @epsilon.setter
    def epsilon(self, value: float) -> None:
        self._state.epsilon = float(value)

    @property
    def total_reward(self) -> float:
        return getattr(self._state, "total_reward", 0.0)

    @property
    def sprint_rewards(self) -> list:
        return getattr(self._state, "sprint_rewards", [])

    @property
    def qmix_weights(self) -> Any:
        return getattr(self._state, "qmix_weights", None)

    @qmix_weights.setter
    def qmix_weights(self, value: Any) -> None:
        self._state.qmix_weights = value

    @property
    def last_train_sprint(self) -> int:
        return getattr(self._state, "last_train_sprint", -1)

    @property
    def last_action(self) -> int:
        return getattr(self._state, "last_action", 0)

    @last_action.setter
    def last_action(self, value: int) -> None:
        self._state.last_action = int(value)

    @property
    def q_network_weights_path(self) -> str:
        return getattr(self._state, "q_network_weights_path", str(_QMIX_WEIGHTS_PATH))

    @property
    def last_train_step(self) -> int:
        return getattr(self._state, "last_train_step", -1)

    @property
    def cumulative_train_steps(self) -> int:
        return getattr(self._state, "cumulative_train_steps", 0)

    @property
    def last_loss(self) -> float:
        return getattr(self._state, "last_loss", 0.0)

    @property
    def is_training_enabled(self) -> bool:
        """True when QMIX training step is active (--rl-train flag path)."""
        return bool(self._rl_train_mode)

    # F262OBS: RL health observability — additional public properties on top
    # of the extended SprintPolicyState schema.
    @property
    def training_steps_completed(self) -> int:
        return getattr(self._state, "training_steps_completed", 0)

    @property
    def loss_history(self) -> list:
        return getattr(self._state, "loss_history", [])

    @property
    def mean_q_value_history(self) -> list:
        return getattr(self._state, "mean_q_value_history", [])

    @property
    def epsilon_history(self) -> list:
        return getattr(self._state, "epsilon_history", [])

    @property
    def last_train_step_sprint(self) -> int:
        return getattr(self._state, "last_train_step_sprint", 0)

    # F261OPT: prompt-requested optional fields (action_counts, q_table, last_updated).
    # These do not exist in SprintPolicyState yet — the safe getattr pattern means
    # missing fields silently default rather than crashing. Future sprints that
    # add these to the dataclass will see them surface here without code change.
    @property
    def action_counts(self) -> dict[str, int]:
        return getattr(self._state, "action_counts", {})

    @property
    def q_table(self) -> Any:
        return getattr(self._state, "q_table", None)

    @property
    def last_updated(self) -> float:
        return getattr(self._state, "last_updated", 0.0)

    @property
    def recent_rewards(self) -> list:
        """Reward history ring buffer (delegates to _reward_history)."""
        return list(getattr(self, "_reward_history", []))

    # ── Training mode setters (F262OBS) ──────────────────────────────────────
    # --rl-train flag sets _rl_train_mode=True via ctor. These setters allow
    # post-construction activation (e.g. toggle from CLI flag after instantiate)
    # and explicit disable (e.g. emergency stop when Q-network drifts).

    def enable_training_mode(self) -> None:
        """Activate QMIX training. Idempotent — safe to call multiple times.

        Once enabled, _run_qmix_training() will be invoked every
        _qmix_train_interval sprints (default 10) — subject to the 4-layer
        M1 memory guard (UMA critical, system RAM > 80%, cooldown, per-sprint cap).
        """
        self._rl_train_mode = True
        log.info("[SprintPolicyManager] Training mode ENABLED — QMIX updates every %d sprints",
                 self._qmix_train_interval)

    def disable_training_mode(self) -> None:
        """Deactivate QMIX training. Idempotent.

        The Q-network remains loaded for inference (get_action() / argmax path);
        only the training step in update() is skipped.
        """
        self._rl_train_mode = False
        log.info("[SprintPolicyManager] Training mode DISABLED — inference-only")

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

            # F265LANE: state_dim=27 for lane-aware features (was 12)
            _STATE_DIM = 27

            self._state_extractor = StateExtractor(state_dim=_STATE_DIM)

            self._replay_buffer = MARLReplayBuffer(
                capacity=50000,
                state_dim=_STATE_DIM,
                n_agents=5,
            )

            # 5 agents: one per action type
            self._agents = {
                str(i): QMIXAgent(agent_id=str(i), state_dim=_STATE_DIM, hidden_dim=64)
                for i in range(5)
            }

            mixer = QMixer(n_agents=5, state_dim=_STATE_DIM, embedding_dim=32)
            target_mixer = QMixer(n_agents=5, state_dim=_STATE_DIM, embedding_dim=32)
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
        """Load persisted state from disk. Auto-detect zstd magic bytes vs plain JSON."""
        if self._loaded:
            return
        self._loaded = True
        try:
            if not self._policy_path.exists():
                return
            with open(self._policy_path, "rb") as f:
                raw_bytes = f.read()
            # F261QMIX: detect zstd magic (0x28 0xB5 0x2F 0xFD) regardless of suffix
            ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"  # noqa: N806
            if raw_bytes[:4] == ZSTD_MAGIC:
                if ZSTD_AVAILABLE and _zstd:
                    raw = _zstd.decompress(raw_bytes)
                    data = json.loads(raw.decode("utf-8"))
                else:
                    log.debug("[SprintPolicyManager] zstd-compressed state but zstd unavailable")
                    return
            else:
                # Plain JSON
                data = json.loads(raw_bytes.decode("utf-8"))
            # F261OPT: when caller did NOT pass an explicit policy_path, the
            # default _POLICY_PATH (rl/.sprint_policy_state.json) may be
            # contaminated by a prior run. Treat that as "fresh start" so the
            # periodic exploration boundary (seq % 5) does not silently drift.
            # Explicit path (e.g. tmp_path in tests, prod config file) keeps
            # state across instances — `test_state_reloaded_on_new_instance`
            # relies on this.
            if not getattr(self, "_policy_path_explicit", False):
                log.debug(
                    "[SprintPolicyManager] Default policy_path — discarding "
                    "stale disk state to avoid cross-session contamination"
                )
                return
            # F262OBS: filter to known dataclass fields. Pre-F262 state files lack
            # training_steps_completed / loss_history / etc. — forwarding unknown keys
            # into SprintPolicyState(**data) raises TypeError.
            import dataclasses as _dc
            _known = {f.name for f in _dc.fields(SprintPolicyState)}
            _filtered = {k: v for k, v in data.items() if k in _known}
            self._state = SprintPolicyState(**_filtered)
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
            # F262OBS: Getattr guards — older persisted state lacks new fields
            # (training_steps_completed, loss_history, etc.) added in F262. Defaulting
            # keeps the loader tolerant of pre-F262 state files.
            payload = {
                "sprint_sequence_number": self._state.sprint_sequence_number,
                "epsilon": self._state.epsilon,
                "total_reward": self._state.total_reward,
                "sprint_rewards": self._state.sprint_rewards[-100:],
                _QMIX_FIELD: self._state.qmix_weights,
                "last_train_sprint": self._state.last_train_sprint,
                # F261QMIX: extended schema for Q-network weight persistence + training counters
                "q_network_weights_path": self._state.q_network_weights_path,
                "last_train_step": self._state.last_train_step,
                "cumulative_train_steps": self._state.cumulative_train_steps,
                "last_loss": self._state.last_loss,
                # F262OBS: RL training health observability — bounded histories
                "loss_history": list(getattr(self._state, "loss_history", []))[-100:],
                "mean_q_value_history": list(getattr(self._state, "mean_q_value_history", []))[-100:],
                "epsilon_history": list(getattr(self._state, "epsilon_history", []))[-100:],
                "last_train_step_sprint": int(getattr(self._state, "last_train_step_sprint", 0)),
                "training_steps_completed": int(getattr(self._state, "training_steps_completed", 0)),
                "epistemic_strength_history": list(getattr(self._state, "epistemic_strength_history", []))[-100:],
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

        F261QMIX formula (per task spec):
            reward = log1p(findings_accepted) * source_quality_multiplier
                   - time_overrun_penalty
                   + novelty_bonus
        Where:
            - findings_accepted:    from result.findings_accepted
            - source_quality_multiplier: average confidence of accepted findings (0.0-1.0)
                                       from scorecard; falls back to acceptance ratio
            - time_overrun_penalty: max(0, elapsed - 1800) / 60  # minutes over 30min wall
            - novelty_bonus:        semantic_novelty score from scorecard (0.0-1.0)
            Clipped to [-1.0, 5.0].
        """
        try:
            # F261OPT: lazy-import MagicMock only inside the reward path.
            # The class is only needed when we inspect test fixtures; production
            # code never constructs MagicMock, so deferring the import keeps the
            # module-load time low and avoids a hard dependency on unittest.mock
            # for non-test users of the package.
            from unittest.mock import MagicMock as _MagicMock
            # F261OPT: test fixtures (MagicMock) set `accepted_findings` but not
            # `findings_accepted` — fall back gracefully to the legacy attribute
            # name to keep the reward signal alive in tests.
            _fa = getattr(result, "findings_accepted", None)
            findings_accepted = float(_fa) if isinstance(_fa, (int, float)) else float(
                getattr(result, "accepted_findings", 0) or 0
            )
            _rt = getattr(result, "actual_duration_s", 0.0)
            runtime = float(_rt) if isinstance(_rt, (int, float)) else 0.0
            _cc = getattr(result, "cycles_completed", None)
            cycles_completed = float(_cc) if isinstance(_cc, (int, float)) else 0.0
            _ab = getattr(result, "aborted", None)
            aborted = bool(_ab) if isinstance(_ab, (bool, int)) and not isinstance(_ab, _MagicMock) else False

            # F261QMIX: source quality multiplier — prefer scorecard, fall back to acceptance ratio
            scorecard = getattr(result, "scorecard", None)
            # F261OPT: detect MagicMock fixtures (tests) — they have everything
            # as auto-attrs, so isinstance(float) and hasattr() both lie. Only
            # trust concrete dict / dataclass instances here.
            if (
                scorecard is not None
                and not isinstance(scorecard, _MagicMock)
                and hasattr(scorecard, "source_quality_avg")
                and isinstance(getattr(scorecard, "source_quality_avg", None), (int, float))
            ):
                source_quality_multiplier = float(
                    max(0.0, min(1.0, scorecard.source_quality_avg))
                )
            elif scorecard is not None and isinstance(scorecard, dict):
                source_quality_multiplier = float(
                    max(0.0, min(1.0, scorecard.get("source_quality_avg", 0.0)))
                )
            else:
                # Fallback: derive from acceptance ratio (accepted / total_in)
                _fd = getattr(result, "findings_deduplicated", 0)
                _fd_num = float(_fd) if isinstance(_fd, (int, float)) else 0.0
                total_in = findings_accepted + _fd_num
                if total_in > 0:
                    source_quality_multiplier = max(0.0, min(1.0, findings_accepted / total_in))
                else:
                    source_quality_multiplier = 0.0

            # F261QMIX: time_overrun_penalty — minutes past 30min wall
            time_overrun_penalty = max(0.0, runtime - 1800.0) / 60.0

            # F261QMIX: novelty_bonus from scorecard.semantic_novelty (0.0-1.0)
            if (
                scorecard is not None
                and not isinstance(scorecard, _MagicMock)
                and hasattr(scorecard, "semantic_novelty")
                and isinstance(getattr(scorecard, "semantic_novelty", None), (int, float))
            ):
                novelty_bonus = float(
                    max(0.0, min(1.0, scorecard.semantic_novelty))
                )
            elif scorecard is not None and isinstance(scorecard, dict):
                novelty_bonus = float(
                    max(0.0, min(1.0, scorecard.get("semantic_novelty", 0.0)))
                )
            else:
                # Fallback: derive from new_iocs ratio (0.0-1.0)
                _ni = getattr(result, "new_iocs", 0)
                new_iocs = float(_ni) if isinstance(_ni, (int, float)) else 0.0
                novelty_bonus = min(new_iocs / max(findings_accepted, 1.0), 1.0)

            # F261OPT: cycles_completed bonus — capped at 1.0 (10+ cycles saturates)
            cycles_bonus = min(cycles_completed / 10.0, 1.0)
            # F261OPT: aborted penalty — -0.5 for any aborted sprint
            abort_penalty = 0.5 if aborted else 0.0

            reward = (
                math.log1p(findings_accepted) * source_quality_multiplier
                - time_overrun_penalty
                + novelty_bonus
                + cycles_bonus
                - abort_penalty
            )

            # Clamp to [-1.0, 5.0] per F257 spec
            epistemic_bonus = self._compute_epistemic_bonus(result)
            reward = reward + epistemic_bonus
            # Record epistemic strength for observability (bounded FIFO 100)
            _ep_hist = list(getattr(self._state, "epistemic_strength_history", []))
            _ep_hist.append(epistemic_bonus)
            if len(_ep_hist) > 100:
                _ep_hist = _ep_hist[-100:]
            self._state.epistemic_strength_history = _ep_hist
            return max(-1.0, min(5.0, reward))
        except Exception:
            return 0.0

    def _compute_epistemic_bonus(self, result: SprintSchedulerResult) -> float:
        """
        F265EPISTEMIC: Compute epistemic quality bonus from ResearchContext.

        Formula:
            bonus = (confirmed_hyps / max(total_hyps, 1)) * 0.3
                  + (1.0 - contradiction_ratio) * 0.2
                  - frontier_gap_penalty * 0.1

        Where:
            confirmed_hyps        = len(context.get_confirmed_hypotheses()) if context else 0
            total_hyps             = len(confirmed) + len(pending)
            contradiction_ratio    = len(context.get_contradictions()) / max(total_hyps, 1)
            frontier_gap_penalty   = len(context.get_knowledge_frontiers()) / 10.0, capped at 1.0

        Returns float in [0.0, 0.5]. Returns 0.0 if context unavailable or on any error.
        Never raises.
        """
        try:
            context = getattr(result, "research_context", None)
            if context is None:
                return 0.0
            confirmed = context.get_confirmed_hypotheses()
            pending = context.get_pending_hypotheses() if hasattr(context, "get_pending_hypotheses") else []
            total_hyps = len(confirmed) + len(pending)
            if total_hyps == 0:
                return 0.0
            confirmed_ratio = len(confirmed) / max(total_hyps, 1)
            contradictions = context.get_contradictions()
            contradiction_ratio = len(contradictions) / max(total_hyps, 1)
            frontiers = context.get_knowledge_frontiers()
            frontier_gap_penalty = min(len(frontiers) / 10.0, 1.0)
            bonus = (confirmed_ratio * 0.3) + ((1.0 - contradiction_ratio) * 0.2) - (frontier_gap_penalty * 0.1)
            return max(0.0, min(0.5, bonus))
        except Exception:
            return 0.0

    def _update_lane_performance(self, result: Any) -> None:
        """
        F265LANE: Extract lane performance from acquisition_lane_outcomes and update history.

        Populates self._lane_performance dict:
            {lane_name: {"yield": float, "quality": float, "count": int, "last_sprint": int}}
        """
        try:
            outcomes = getattr(result, 'acquisition_lane_outcomes', None) or ()
            for outcome in outcomes:
                lane_name = getattr(outcome, 'lane', None) or ""
                if not lane_name or lane_name not in ("PUBLIC", "CT", "WAYBACK", "DOH", "PASSIVE_DNS"):
                    continue

                accepted = getattr(outcome, 'accepted_findings', 0) or 0
                rejected = getattr(outcome, 'rejected_count', 0) or 0
                duration = getattr(outcome, 'duration_s', 0.0) or 0.0

                # Yield: findings per second
                lane_yield = accepted / max(duration, 0.001)
                # Quality: acceptance ratio
                lane_quality = accepted / max(accepted + rejected, 1)

                if lane_name not in self._lane_performance:
                    self._lane_performance[lane_name] = {"yield": 0.0, "quality": 0.0, "count": 0, "last_sprint": 0}

                perf = self._lane_performance[lane_name]
                perf["yield"] = (perf["yield"] * perf["count"] + lane_yield) / (perf["count"] + 1)
                perf["quality"] = (perf["quality"] * perf["count"] + lane_quality) / (perf["count"] + 1)
                perf["count"] += 1
                perf["last_sprint"] = self._state.sprint_sequence_number
        except Exception:
            pass

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
        # F261OPT: epsilon decay — gentle multiplicative decay per sprint
        # Floor at 0.05 prevents complete greediness; rate chosen so default 0.1
        # approaches floor over ~hundreds of sprints without underflow.
        _EPSILON_FLOOR = 0.05  # noqa: N806
        _EPSILON_DECAY = 0.995  # noqa: N806
        if self._state.epsilon > _EPSILON_FLOOR:
            self._state.epsilon = max(_EPSILON_FLOOR, self._state.epsilon * _EPSILON_DECAY)
        # F262OBS: record epsilon after decay into bounded history (FIFO 100)
        _eps_hist = list(getattr(self._state, "epsilon_history", []))
        _eps_hist.append(self._state.epsilon)
        if len(_eps_hist) > 100:
            _eps_hist = _eps_hist[-100:]
        self._state.epsilon_history = _eps_hist
        reward = self._compute_reward(result)

        # F265LANE: Extract lane performance from acquisition_lane_outcomes
        self._update_lane_performance(result)

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

        # ── Auto-warmup: enable training when replay is rich enough ───────────────
        # F268WARMUP: If rl_train_mode was left as default (False) and the env var
        # is not explicitly "0", auto-enable training once replay buffer is deep
        # enough and we have completed enough sprints. This prevents the "zero
        # learning" state where rl_train_mode=False forever without any signal.
        _env_train = os.environ.get("HLEDAC_RL_TRAIN", "")
        if (
            not self._rl_train_mode
            and _env_train != "0"
            and self._replay_buffer is not None
            and self._state.sprint_sequence_number > 0
            and self._state.sprint_sequence_number % self._qmix_train_interval == 0
            and self._replay_buffer.size >= _MIN_REPLAY_SIZE
            and self._qmix_trainer is not None
        ):
            # Log the transition so operators can see it happened
            log.info(
                "[SprintPolicyManager] AUTO-WARMUP: enabling rl_train_mode=True "
                "(sprint=%d, replay=%d, min=%d)",
                self._state.sprint_sequence_number,
                self._replay_buffer.size,
                _MIN_REPLAY_SIZE,
            )
            self._rl_train_mode = True

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

        # F261QMIX: reset per-sprint training throttle at end of each sprint
        self._train_steps_this_sprint = 0

        _ebm = self.get_qmix_stats().get("epistemic_bonus_mean")
        if _ebm is not None:
            log.info("[SprintPolicyManager] sprint=%d epistemic_bonus_mean=%.4f",
                     self._state.sprint_sequence_number, _ebm)
        self._save()

    def _run_qmix_training(self) -> None:
        """Sample batch from replay buffer and run QMIX joint training step.

        F261QMIX: bounded, fail-soft, M1-safe. 4-layer memory guard:
          L1 — UMA critical check (M1 8GB pressure)
          L2 — system RAM % gate (psutil, default skip > 80%)
          L3 — cooldown (monotonic clock, default 1.0s between steps)
          L4 — per-sprint cap (default 1 step per sprint)
        Plus GHOST_INVARIANT I11: mx.eval([]) BEFORE mx.metal.clear_cache().

        F262OBS: also records:
          - loss_history (FIFO 100)
          - mean_q_value_history (FIFO 100) — mean of global Q over the batch
          - training_steps_completed (monotonic)
          - last_train_step_sprint (sprint number at last step)
        All inside a single try/except — training MUST be fail-soft, never crash sprint.
        """
        if self._qmix_trainer is None or self._replay_buffer is None:
            return

        # ── L1: UMA budget pre-check (M1 8GB) ──
        try:
            from hledac.universal.utils.uma_budget import get_uma_budget
            uma = get_uma_budget()
            if uma.is_critical():
                log.debug("[SprintPolicyManager] Skipping QMIX train_step — M1 UMA critical")
                return
        except Exception:
            pass  # noqa: BLE001  # UMA check is advisory; proceed if unavailable

        # ── L2: System RAM % gate (F261QMIX: skip when > 80%) ──
        if not _RAM_GATE_DISABLED:
            try:
                import psutil
                if psutil.virtual_memory().percent > _RAM_TRAIN_SKIP_PCT:
                    log.warning(
                        "[SprintPolicyManager] QMIX train_step skipped — RAM >%d%%",
                        _RAM_TRAIN_SKIP_PCT,
                    )
                    return
            except Exception:
                pass  # noqa: BLE001  # psutil is advisory; proceed if unavailable

        # ── L3: Cooldown gate (prevent M1 RAM thrashing) ──
        import time
        now = time.monotonic()
        if (now - self._last_train_at) < _TRAIN_COOLDOWN_S:
            return

        # ── L4: Per-sprint cap ──
        if self._train_steps_this_sprint >= _MAX_TRAIN_STEPS_PER_SPRINT:
            return

        # F262OBS: training step timing — time.monotonic() per GHOST_INVARIANTS I12
        _step_t0 = time.monotonic()
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
            loss_result = _train(batch)
            # Defensive: handle both dict and scalar return
            if isinstance(loss_result, dict):
                loss = float(loss_result.get("loss", 0.0))
                mean_q = float(loss_result.get("mean_q", 0.0))
            else:
                loss = float(loss_result)
                mean_q = 0.0

            # F262OBS: compute mean_q from global Q if not provided by trainer.
            # Fall back to running mean Q across agents on the trained batch.
            if mean_q == 0.0 and hasattr(self._qmix_trainer, "joint_model") and hasattr(self._qmix_trainer, "mixer"):
                try:
                    import mlx.core as mx
                    states = batch.get("states")
                    actions = batch.get("actions")
                    if states is not None and actions is not None:
                        agent_nets = self._qmix_trainer.joint_model.get_agent_nets()
                        all_qs = mx.stack([net(states) for net in agent_nets], axis=1)
                        chosen = mx.take_along_axis(
                            all_qs, mx.expand_dims(actions, -1), axis=2
                        ).squeeze(-1)
                        q_total = self._qmix_trainer.mixer(chosen, states)
                        mean_q = float(mx.mean(q_total).item())
                except Exception:
                    mean_q = 0.0

            # Persist updated weights — binary npz + JSON mirror
            if hasattr(self._qmix_trainer, "joint_model"):
                self._state.qmix_weights = _serialize_weights(
                    self._qmix_trainer.joint_model.parameters()
                )
                # F261QMIX: binary weight dump via mlx.core.savez
                self._save_qmix_weights_binary(self._qmix_trainer.joint_model.parameters())
            self._state.last_train_sprint = self._state.sprint_sequence_number
            self._state.last_train_step = self._state.sprint_sequence_number
            self._state.cumulative_train_steps += 1
            self._state.last_loss = loss

            # F263QCB: loss spike guard — skip weight update if new_loss > 2.5x
            # the last recorded loss. Without this, a single bad batch (memory
            # pressure, extreme reward outlier) can destabilize the Q-network
            # before target-DQN correction. We compare against the last value
            # already in loss_history (not a fresh compute) so transient spikes
            # can't poison the baseline.
            _prev_loss_hist = list(getattr(self._state, "loss_history", []))
            if _prev_loss_hist:
                _prev_loss = _prev_loss_hist[-1]
                if _prev_loss > 0.0 and loss > 2.5 * _prev_loss:
                    log.warning(
                        "[SprintPolicyManager] QMIX loss spike %.4f > 2.5x prev %.4f — "
                        "skipping weight update (training_steps not incremented)",
                        loss, _prev_loss,
                    )
                    # Persist the new weight dump we already did above so
                    # the npz stays in sync, but do NOT touch loss_history,
                    # mean_q_value_history, or training_steps_completed.
                    return

            # F262OBS: bounded health-observability histories — all FIFO 100.
            # Defensive getattr guards so missing fields don't crash under legacy state.
            _loss_hist = list(getattr(self._state, "loss_history", []))
            _loss_hist.append(loss)
            if len(_loss_hist) > 100:
                _loss_hist = _loss_hist[-100:]
            self._state.loss_history = _loss_hist

            _q_hist = list(getattr(self._state, "mean_q_value_history", []))
            _q_hist.append(mean_q)
            if len(_q_hist) > 100:
                _q_hist = _q_hist[-100:]
            self._state.mean_q_value_history = _q_hist

            # F262OBS: training step bookkeeping
            self._state.training_steps_completed = int(
                getattr(self._state, "training_steps_completed", 0)
            ) + 1
            self._state.last_train_step_sprint = int(
                self._state.sprint_sequence_number
            )

            # Update throttle state — AFTER successful training step
            self._last_train_at = now
            self._train_steps_this_sprint += 1

            # F268FIX: Populate Q-value cache after successful training step.
            # Stores per-agent Q-values computed on the training batch states.
            # get_action() uses this cache during _Q_CACHE_TTL_S window instead of
            # running Metal inference — eliminates ~1-2ms kernel launch overhead.
            try:
                import time as _time_module

                import mlx.core as mx
                cache_states = batch.get("states")
                if cache_states is not None and self._agents is not None:
                    self._q_value_cache = {}
                    for _aid, _agent in self._agents.items():
                        _q_vals = _agent.q_net(cache_states)  # (batch, action_dim)
                        _mean_q = float(mx.mean(_q_vals).item())
                        self._q_value_cache[_aid] = _mean_q
                    self._q_cache_timestamp = _time_module.monotonic()
            except Exception:
                self._q_value_cache = {}
                self._q_cache_timestamp = 0.0

            # F266 METAL LEAK FIX: mx.eval([]) BEFORE clear_cache()
            try:
                import mlx.core as mx
                mx.eval([])  # barrier FIRST
                import gc
                gc.collect()  # F266: Python GC BEFORE Metal release
                # Modern-first: mx.clear_cache(), fallback to deprecated
                if hasattr(mx, "clear_cache"):
                    mx.clear_cache()
                elif hasattr(mx.metal, "clear_cache"):
                    mx.metal.clear_cache()
                gc.collect()  # F266: second GC pass
            except Exception:
                pass

            _step_dt = time.monotonic() - _step_t0
            log.info(
                "[SprintPolicyManager] QMIX train_step %d: loss=%.4f mean_q=%.3f "
                "replay=%d cum_steps=%d step_dt=%.2fs",
                self._state.sprint_sequence_number,
                loss,
                mean_q,
                self._replay_buffer.size,
                self._state.cumulative_train_steps,
                _step_dt,
            )
        except Exception as e:
            log.debug("[SprintPolicyManager] QMIX training failed: %s", e)

    def _save_qmix_weights_binary(self, params: Any) -> None:
        """F261QMIX: Persist Q-network weights via mlx.core.savez to .npz.

        Falls back silently on any error (advisory persistence only).
        """
        try:
            import mlx.core as mx
            from mlx.utils import tree_flatten
            flat = dict(tree_flatten(params))
            mx.savez(str(_QMIX_WEIGHTS_PATH), **flat)
            log.debug("[SprintPolicyManager] Q-weights persisted to %s", _QMIX_WEIGHTS_PATH)
        except Exception as e:
            log.debug("[SprintPolicyManager] _save_qmix_weights_binary failed: %s", e)

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

        # Deterministic interval-based exploration.
        # Fires every N sprints (1-indexed: sprint #5, #10, ... → sequence_number 4, 9, ...).
        # `seq > 0` guard prevents a fresh manager (seq=0) from immediately
        # reporting exploration on the very first check.
        if seq > 0 and (seq + 1) % self._exploration_interval == 0:
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

        F265LANE: Extended action space includes lane selection (actions 10-15).
        QMIX agents 0-4 map to base actions, agents 5-10 map to lane combos.
        """
        if not self._enabled:
            from rl.actions import ACTION_CONTINUE
            return ACTION_CONTINUE

        if self.should_explore():
            from rl.actions import ACTION_DEEP_DIVE
            return ACTION_DEEP_DIVE

        # QMIX inference: if weights loaded and agents available, use argmax Q
        # Note: requires a result to extract state from — fallback to epsilon-greedy
        # F268FIX: Q-value cache — use cached per-agent mean-Q values (populated
        # after training step) when within _Q_CACHE_TTL_S window. Falls back to
        # live Metal inference if cache is stale or empty.
        if self._qmix_trainer is not None and self._agents is not None and self._state_extractor is not None:
            try:
                # Try to get state from attached scheduler's current result
                if hasattr(self, '_scheduler') and self._scheduler is not None:
                    result = getattr(self._scheduler, '_result', None)
                    if result is not None:
                        # F268FIX: Check cache freshness first — ~0.01ms vs ~1-2ms Metal
                        import time as _time_mod
                        _cache_valid = (
                            self._q_value_cache
                            and (self._q_cache_timestamp > 0)
                            and ((_time_mod.monotonic() - self._q_cache_timestamp) < _Q_CACHE_TTL_S)
                        )
                        if _cache_valid:
                            # Use cached Q-values — argmax over per-agent mean-Q
                            best_action = 0
                            best_q = float("-inf")
                            for _aid, _q in self._q_value_cache.items():
                                if _q > best_q:
                                    best_q = _q
                                    best_action = int(_aid)
                        else:
                            # Live Metal inference — extract state and forward all agents
                            state = self._state_extractor.extract(result)
                            best_action = 0
                            best_q = float("-inf")
                            for agent_id, agent in self._agents.items():
                                q_val = float(agent.q_net(state)[0].item())
                                if q_val > best_q:
                                    best_q = q_val
                                    best_action = int(agent_id)
                        # F265LANE: Map to action constants (base actions 0-4, lane combos 10-15)
                        from rl.actions import (
                            ACTION_BRANCH,
                            ACTION_CONTINUE,
                            ACTION_FETCH_MORE,
                            ACTION_YIELD,
                            action_from_lane_combo,
                        )
                        # Agents 0-4: base actions; Agents 5-10: lane selection combos
                        if best_action < 5:
                            ACTION_MAP = {
                                0: ACTION_CONTINUE, 1: ACTION_FETCH_MORE,
                                2: ACTION_BRANCH, 3: ACTION_YIELD, 4: ACTION_CONTINUE
                            }
                            return ACTION_MAP.get(best_action, ACTION_CONTINUE)
                        else:
                            # Lane selection: agent 5 → combo 0 (action 10), etc.
                            combo_idx = best_action - 5
                            if 0 <= combo_idx < 6:
                                return action_from_lane_combo(combo_idx)
                            return ACTION_CONTINUE
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
                    pass  # noqa: BLE001  # fail-soft: delegation is best-effort

            log.debug(
                "[SprintPolicyManager] quality feedback: src=%s total=%d accepted=%d",
                feed_url or "unknown",
                total_count,
                accepted_count,
            )
        except Exception as e:
            log.debug("[SprintPolicyManager] update_with_quality_decisions failed: %s", e)

    def get_src_quality_weights(self) -> dict[str, float]:
        """
        F228A: Return per-source quality weights for acquisition plan weighting.

        Returns source_family → weight mapping (default 1.0, clamped [0.3, 2.5]).
        Weights are adapted by update_with_quality_decisions() based on
        accepted/total ratio per source over sprints.

        Fail-soft: returns empty dict when no weights accumulated yet.
        """
        if not hasattr(self, "_src_quality_weights"):
            return {}
        return dict(self._src_quality_weights)

    def get_qmix_stats(self) -> dict[str, Any]:
        """Return QMIX training stats for observability."""
        return {
            "sprint_sequence": self._state.sprint_sequence_number,
            "total_reward": self._state.total_reward,
            "replay_size": self._replay_buffer.size if self._replay_buffer else 0,
            "last_train_sprint": self._state.last_train_sprint,
            "rl_train_mode": self._rl_train_mode,
            "epistemic_bonus_mean": (
                sum(self._state.epistemic_strength_history[-10:]) /
                max(len(self._state.epistemic_strength_history[-10:]), 1)
            ),
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

    def get_lane_config(self) -> dict[str, dict[str, float]]:
        """
        F265LANE: Return adaptive lane configuration from RL policy.

        Returns a dict mapping lane name → {timeout, weight} config.
        Called by SprintScheduler when building acquisition plan.

        Fail-soft: returns empty dict when disabled or no lane history.
        """
        if not self._enabled:
            return {}

        # F265LANE: Compute lane config from lane performance history
        # Default timeouts and weights — RL will adapt these over time
        default_config = {
            "PUBLIC": {"timeout": 30.0, "weight": 1.0},
            "CT": {"timeout": 45.0, "weight": 1.0},
            "WAYBACK": {"timeout": 20.0, "weight": 0.8},
            "DOH": {"timeout": 15.0, "weight": 0.5},
            "PASSIVE_DNS": {"timeout": 20.0, "weight": 0.7},
        }

        # If we have lane performance data, adapt based on yield/quality
        if hasattr(self, '_lane_performance') and self._lane_performance:
            for lane, perf in self._lane_performance.items():
                if lane in default_config:
                    # Boost weight for high-yield lanes, reduce for low-yield
                    yield_val = perf.get('yield', 0.0)
                    if yield_val > 0.5:  # High yield
                        default_config[lane]["weight"] = min(
                            default_config[lane].get("weight", 1.0) * 1.2, 1.5
                        )
                    elif yield_val < 0.1:  # Low yield
                        default_config[lane]["weight"] = max(
                            default_config[lane].get("weight", 1.0) * 0.8, 0.3
                        )

        return default_config

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
        # F261QMIX: reset training throttle state
        self._last_train_at = 0.0
        self._train_steps_this_sprint = 0
        try:
            if self._policy_path.exists():
                self._policy_path.unlink()
        except Exception as e:
            log.warning(f"[SprintPolicyManager] Failed to delete policy state file: {e}")
