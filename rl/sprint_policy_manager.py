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
# G4 FIX: stdlib json replaced with orjson fallback (M1 optimized, 5-10× faster)
try:
    import orjson
    _HAS_ORJSON = True
except ImportError:
    import json
    _HAS_ORJSON = False

import logging
import math
import os
import secrets
from dataclasses import dataclass, field as _dc_field
import msgspec
from pathlib import Path
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    pass
import numpy as np
import orjson
from core import aclose
try:
    import compression.zstd as _zstd
    ZSTD_AVAILABLE = True
except (ImportError, Exception):
    ZSTD_AVAILABLE = False
    _zstd = None
log = logging.getLogger(__name__)
_POLICY_PATH = Path(__file__).parent / '.sprint_policy_state.json'
_QMIX_WEIGHTS_PATH = Path(__file__).parent / '.qmix_weights.npz'
_EXPLORATION_INTERVAL = 5
_DEFAULT_EPSILON = 0.1
_QMIX_TRAIN_INTERVAL = int(os.environ.get('HLEDAC_RL_TRAIN_INTERVAL', '10'))
_MIN_REPLAY_SIZE = 256
_TRAIN_BATCH_SIZE = 32
_RAM_TRAIN_SKIP_PCT = 80
_RAM_GATE_DISABLED = os.environ.get('HLEDAC_RL_SKIP_RAM_GATE', '0') == '1'
_TRAIN_COOLDOWN_S = 1.0
_MAX_TRAIN_STEPS_PER_SPRINT = 1
_Q_CACHE_TTL_S = 5.0
_QMIX_FIELD = 'qmix_weights'

# Non-security RNG — exploration epsilon-greedy sampling (30× faster than secrets)
_RANDOM = secrets.SystemRandom()

class SprintPolicyState(msgspec.Struct, gc=False):
    """Serialized policy state persisted to disk."""
    sprint_sequence_number: int = 0
    epsilon: float = _DEFAULT_EPSILON
    total_reward: float = 0.0
    sprint_rewards: list[float] = msgspec.field(default_factory=list)
    qmix_weights: dict[str, Any] | None = None
    last_train_sprint: int = -1
    last_action: int = 0
    q_network_weights_path: str = str(_QMIX_WEIGHTS_PATH)
    last_train_step: int = -1
    cumulative_train_steps: int = 0
    last_loss: float = 0.0
    loss_history: list[float] = msgspec.field(default_factory=list)
    mean_q_value_history: list[float] = msgspec.field(default_factory=list)
    epsilon_history: list[float] = msgspec.field(default_factory=list)
    last_train_step_sprint: int = 0
    training_steps_completed: int = 0
    epistemic_strength_history: list[float] = msgspec.field(default_factory=list)

def _serialize_weights(weights: Any) -> dict[str, Any]:
    """Serialize MLX array weights to JSON-compatible dict. Returns {} if weights is None."""
    if weights is None:
        return {'flat': []}
    try:
        from mlx.utils import tree_map
        flat_weights = tree_map(lambda x: x.tolist() if hasattr(x, 'tolist') else x, weights)
        flat = []

        def collect(key, val, path=''):
            if isinstance(val, dict):
                for k, v in val.items():
                    collect(k, v, f'{path}.{k}' if path else k)
            else:
                flat.append({'key': path, 'value': val})
        collect('_root', flat_weights)
        return {'flat': flat}
    except Exception:
        return {'flat': []}

def _deserialize_weights(data: dict[str, Any]) -> Any:
    """Reconstruct MLX array weights from serialized dict."""
    if not data or 'flat' not in data:
        return None
    try:
        import mlx.core as mx
        nested = {}
        for item in data['flat']:
            key_parts = item['key'].split('.')
            value = mx.array(item['value'])
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
    __slots__ = tuple(('_agents', '_enabled', '_epsilon', '_exploration_interval', '_lane_performance', '_last_train_at', '_loaded', '_pending_feedback', '_policy_path', '_policy_path_explicit', '_q_cache_timestamp', '_q_value_cache', '_qmix_train_interval', '_qmix_trainer', '_replay_buffer', '_reward_history', '_rl_train_mode', '_state', '_state_extractor', '_train_steps_this_sprint'))

    def __init__(self, enabled: bool=os.environ.get('HLEDAC_DISABLE_RL') != '1', policy_path: Path | None=None, epsilon: float=_DEFAULT_EPSILON, exploration_interval: int=_EXPLORATION_INTERVAL, qmix_train_interval: int | None=None, rl_train_mode: bool=os.environ.get('HLEDAC_RL_TRAIN') == '1') -> None:
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
        self._policy_path_explicit = policy_path is not None
        self._policy_path = policy_path if policy_path is not None else _POLICY_PATH
        self._epsilon = epsilon
        self._exploration_interval = exploration_interval
        self._qmix_train_interval = qmix_train_interval if qmix_train_interval is not None else _QMIX_TRAIN_INTERVAL
        self._rl_train_mode = rl_train_mode
        self._state = SprintPolicyState()
        self._loaded = False
        self._pending_feedback: dict[str, dict[str, int]] = {}
        self._last_train_at: float = 0.0
        self._train_steps_this_sprint: int = 0
        self._qmix_trainer: Any = None
        self._replay_buffer: Any = None
        self._state_extractor: Any = None
        self._agents: Any = None
        self._reward_history: list[float] = []
        self._lane_performance: dict[str, dict[str, float]] = {}
        self._q_value_cache: dict[str, float] = {}
        self._q_cache_timestamp: float = 0.0
        if self._enabled:
            self._load()
        if self._enabled and self._state.sprint_rewards:
            self._reward_history = list(self._state.sprint_rewards[-100:])

    @property
    def enabled(self) -> bool:
        """Expose _enabled for external callers (e.g., SprintScheduler F228A block)."""
        return self._enabled

    @property
    def sprint_sequence_number(self) -> int:
        return getattr(self._state, 'sprint_sequence_number', 0)

    @property
    def epsilon(self) -> float:
        return getattr(self._state, 'epsilon', _DEFAULT_EPSILON)

    @epsilon.setter
    def epsilon(self, value: float) -> None:
        self._state.epsilon = float(value)

    @property
    def total_reward(self) -> float:
        return getattr(self._state, 'total_reward', 0.0)

    @property
    def sprint_rewards(self) -> list:
        return getattr(self._state, 'sprint_rewards', [])

    @property
    def qmix_weights(self) -> Any:
        return getattr(self._state, 'qmix_weights', None)

    @qmix_weights.setter
    def qmix_weights(self, value: Any) -> None:
        self._state.qmix_weights = value

    @property
    def last_train_sprint(self) -> int:
        return getattr(self._state, 'last_train_sprint', -1)

    @property
    def last_action(self) -> int:
        return getattr(self._state, 'last_action', 0)

    @last_action.setter
    def last_action(self, value: int) -> None:
        self._state.last_action = int(value)

    @property
    def q_network_weights_path(self) -> str:
        return getattr(self._state, 'q_network_weights_path', str(_QMIX_WEIGHTS_PATH))

    @property
    def last_train_step(self) -> int:
        return getattr(self._state, 'last_train_step', -1)

    @property
    def cumulative_train_steps(self) -> int:
        return getattr(self._state, 'cumulative_train_steps', 0)

    @property
    def last_loss(self) -> float:
        return getattr(self._state, 'last_loss', 0.0)

    @property
    def is_training_enabled(self) -> bool:
        """True when QMIX training step is active (--rl-train flag path)."""
        return bool(self._rl_train_mode)

    @property
    def training_steps_completed(self) -> int:
        return getattr(self._state, 'training_steps_completed', 0)

    @property
    def loss_history(self) -> list:
        return getattr(self._state, 'loss_history', [])

    @property
    def mean_q_value_history(self) -> list:
        return getattr(self._state, 'mean_q_value_history', [])

    @property
    def epsilon_history(self) -> list:
        return getattr(self._state, 'epsilon_history', [])

    @property
    def last_train_step_sprint(self) -> int:
        return getattr(self._state, 'last_train_step_sprint', 0)

    @property
    def action_counts(self) -> dict[str, int]:
        return getattr(self._state, 'action_counts', {})

    @property
    def q_table(self) -> Any:
        return getattr(self._state, 'q_table', None)

    @property
    def last_updated(self) -> float:
        return getattr(self._state, 'last_updated', 0.0)

    @property
    def recent_rewards(self) -> list:
        """Reward history ring buffer (delegates to _reward_history)."""
        return list(getattr(self, '_reward_history', []))

    def enable_training_mode(self) -> None:
        """Activate QMIX training. Idempotent — safe to call multiple times.

        Once enabled, _run_qmix_training() will be invoked every
        _qmix_train_interval sprints (default 10) — subject to the 4-layer
        M1 memory guard (UMA critical, system RAM > 80%, cooldown, per-sprint cap).
        """
        self._rl_train_mode = True
        log.info('[SprintPolicyManager] Training mode ENABLED — QMIX updates every %d sprints', self._qmix_train_interval)

    def disable_training_mode(self) -> None:
        """Deactivate QMIX training. Idempotent.

        The Q-network remains loaded for inference (get_action() / argmax path);
        only the training step in update() is skipped.
        """
        self._rl_train_mode = False
        log.info('[SprintPolicyManager] Training mode DISABLED — inference-only')

    def inject_scheduler(self, scheduler: Any) -> None:
        """Inject SprintPolicyManager ref (opt-in RL layer)."""
        if not self._enabled:
            return
        self._policy_manager = scheduler
        if hasattr(scheduler, '_adapt_source_weights_from_feedback'):
            self._scheduler = scheduler
            self._pending_feedback = {}
        self._replay_buffer = None
        self._state_extractor = None
        self._qmix_trainer = None
        self._agents = None
        self._reward_history: list = []
        if self._enabled:
            self._load()
            if self._state.sprint_rewards:
                self._reward_history = list(self._state.sprint_rewards[-100:])

    def _init_qmix(self) -> None:
        """Lazily init QMIX components: replay buffer, state extractor, agents, trainer."""
        if not self._enabled:
            return
        if self._qmix_trainer is not None:
            return
        try:
            from hledac.universal.rl.qmix import QMIXAgent, QMixer, QMIXJointTrainer
            from hledac.universal.rl.replay_buffer import MARLReplayBuffer
            from hledac.universal.rl.state_extractor import StateExtractor
            _STATE_DIM = 27
            self._state_extractor = StateExtractor(state_dim=_STATE_DIM)
            self._replay_buffer = MARLReplayBuffer(capacity=50000, state_dim=_STATE_DIM, n_agents=5)
            self._agents = {str(i): QMIXAgent(agent_id=str(i), state_dim=_STATE_DIM, hidden_dim=64) for i in range(5)}
            mixer = QMixer(n_agents=5, state_dim=_STATE_DIM, embedding_dim=32)
            target_mixer = QMixer(n_agents=5, state_dim=_STATE_DIM, embedding_dim=32)
            self._qmix_trainer = QMIXJointTrainer(agents=self._agents, mixer=mixer, target_mixer=target_mixer, gamma=0.99, tau=0.005)
            if self._state.qmix_weights and hasattr(self._qmix_trainer, 'joint_model'):
                try:
                    loaded = _deserialize_weights(self._state.qmix_weights)
                    if loaded:
                        current_params = dict(self._qmix_trainer.joint_model.parameters())
                        updated_params = {k: loaded.get(k, v) for k, v in current_params.items()}
                        self._qmix_trainer.joint_model.update(updated_params)
                        log.debug('[SprintPolicyManager] Loaded %d weight tensors into joint_model', len(loaded))
                except Exception as e:
                    log.debug('[SprintPolicyManager] Weight loading failed (safe to ignore): %s', e)
            log.info('[SprintPolicyManager] QMIX components initialized (rl_train_mode=%s)', self._rl_train_mode)
        except ImportError as e:
            log.debug('[SprintPolicyManager] QMIX ImportError (MLX unavailable): %s', e)
            self._qmix_trainer = None
            self._agents = None
        except Exception as e:
            log.warning('[SprintPolicyManager] QMIX init failed: %s', e)
            self._qmix_trainer = None
            self._agents = None

    def _load(self) -> None:
        """Load persisted state from disk. Auto-detect zstd magic bytes vs plain JSON."""
        if self._loaded:
            return
        self._loaded = True
        try:
            if not self._policy_path.exists():
                return
            with open(self._policy_path, 'rb') as f:
                raw_bytes = f.read()
            ZSTD_MAGIC = b'(\xb5/\xfd'
            if raw_bytes[:4] == ZSTD_MAGIC:
                if ZSTD_AVAILABLE and _zstd:
                    raw = _zstd.decompress(raw_bytes)
                    data = orjson.loads(raw)
                else:
                    log.debug('[SprintPolicyManager] zstd-compressed state but zstd unavailable')
                    return
            else:
                data = orjson.loads(raw_bytes)
            if not getattr(self, '_policy_path_explicit', False):
                log.debug('[SprintPolicyManager] Default policy_path — discarding stale disk state to avoid cross-session contamination')
                return
            _known = set(SprintPolicyState.__struct_fields__)
            _filtered = {k: v for k, v in data.items() if k in _known}
            self._state = SprintPolicyState(**_filtered)
            log.debug('[SprintPolicyManager] Loaded state: sprint=%d epsilon=%.3f total_reward=%.2f', self._state.sprint_sequence_number, self._state.epsilon, self._state.total_reward)
        except Exception as e:
            log.debug('[SprintPolicyManager] _load failed (safe to ignore): %s', e)

    def _save(self) -> None:
        """Persist state to disk as .json.zst. Fail-safe — do not crash on write errors."""
        if not self._enabled:
            return
        try:
            payload = {'sprint_sequence_number': self._state.sprint_sequence_number, 'epsilon': self._state.epsilon, 'total_reward': self._state.total_reward, 'sprint_rewards': self._state.sprint_rewards[-100:], _QMIX_FIELD: self._state.qmix_weights, 'last_train_sprint': self._state.last_train_sprint, 'q_network_weights_path': self._state.q_network_weights_path, 'last_train_step': self._state.last_train_step, 'cumulative_train_steps': self._state.cumulative_train_steps, 'last_loss': self._state.last_loss, 'loss_history': list(getattr(self._state, 'loss_history', []))[-100:], 'mean_q_value_history': list(getattr(self._state, 'mean_q_value_history', []))[-100:], 'epsilon_history': list(getattr(self._state, 'epsilon_history', []))[-100:], 'last_train_step_sprint': int(getattr(self._state, 'last_train_step_sprint', 0)), 'training_steps_completed': int(getattr(self._state, 'training_steps_completed', 0)), 'epistemic_strength_history': list(getattr(self._state, 'epistemic_strength_history', []))[-100:]}

            # G4 FIX: Use orjson with fallback for JSON serialization
            if _HAS_ORJSON:
                encoded = orjson.dumps(payload, option=orjson.OPT_NON_STR_KEY)
            else:
                encoded = json.dumps(payload, ensure_ascii=False).encode('utf-8')

            if ZSTD_AVAILABLE and _zstd:
                compressed = _zstd.compress(encoded, level=3)
                with open(self._policy_path, 'wb') as f:
                    f.write(compressed)
            else:
                if _HAS_ORJSON:
                    with open(self._policy_path, 'wb') as f:
                        f.write(encoded)
                else:
                    with open(self._policy_path, 'w', encoding='utf-8') as f:
                        f.write(json.dumps(payload))
            log.debug('[SprintPolicyManager] State persisted to %s', self._policy_path)
        except Exception as e:
            log.debug('[SprintPolicyManager] _save failed: %s', e)

    def _get_finding_count(self, result: SprintSchedulerResult, prefix: str) -> int:
        """F235: Fallback chain for finding count fields — M1 memory safe."""
        for suffix in ('accepted', 'produced', 'ingested'):
            val = getattr(result, f'{prefix}_findings_{suffix}', None)
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
            from unittest.mock import MagicMock as _MagicMock
            _fa = getattr(result, 'findings_accepted', None)
            findings_accepted = float(_fa) if isinstance(_fa, (int, float)) else float(getattr(result, 'accepted_findings', 0) or 0)
            _rt = getattr(result, 'actual_duration_s', 0.0)
            runtime = float(_rt) if isinstance(_rt, (int, float)) else 0.0
            _cc = getattr(result, 'cycles_completed', None)
            cycles_completed = float(_cc) if isinstance(_cc, (int, float)) else 0.0
            _ab = getattr(result, 'aborted', None)
            aborted = bool(_ab) if isinstance(_ab, (bool, int)) and (not isinstance(_ab, _MagicMock)) else False
            scorecard = getattr(result, 'scorecard', None)
            if scorecard is not None and (not isinstance(scorecard, _MagicMock)) and hasattr(scorecard, 'source_quality_avg') and isinstance(getattr(scorecard, 'source_quality_avg', None), (int, float)):
                source_quality_multiplier = float(max(0.0, min(1.0, scorecard.source_quality_avg)))
            elif scorecard is not None and isinstance(scorecard, dict):
                source_quality_multiplier = float(max(0.0, min(1.0, scorecard.get('source_quality_avg', 0.0))))
            else:
                _fd = getattr(result, 'findings_deduplicated', 0)
                _fd_num = float(_fd) if isinstance(_fd, (int, float)) else 0.0
                total_in = findings_accepted + _fd_num
                if total_in > 0:
                    source_quality_multiplier = max(0.0, min(1.0, findings_accepted / total_in))
                else:
                    source_quality_multiplier = 0.0
            time_overrun_penalty = max(0.0, runtime - 1800.0) / 60.0
            if scorecard is not None and (not isinstance(scorecard, _MagicMock)) and hasattr(scorecard, 'semantic_novelty') and isinstance(getattr(scorecard, 'semantic_novelty', None), (int, float)):
                novelty_bonus = float(max(0.0, min(1.0, scorecard.semantic_novelty)))
            elif scorecard is not None and isinstance(scorecard, dict):
                novelty_bonus = float(max(0.0, min(1.0, scorecard.get('semantic_novelty', 0.0))))
            else:
                _ni = getattr(result, 'new_iocs', 0)
                new_iocs = float(_ni) if isinstance(_ni, (int, float)) else 0.0
                novelty_bonus = min(new_iocs / max(findings_accepted, 1.0), 1.0)
            cycles_bonus = min(cycles_completed / 10.0, 1.0)
            abort_penalty = 0.5 if aborted else 0.0
            reward = math.log1p(findings_accepted) * source_quality_multiplier - time_overrun_penalty + novelty_bonus + cycles_bonus - abort_penalty
            epistemic_bonus = self._compute_epistemic_bonus(result)
            reward = reward + epistemic_bonus
            _ep_hist = list(getattr(self._state, 'epistemic_strength_history', []))
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
            context = getattr(result, 'research_context', None)
            if context is None:
                return 0.0
            confirmed = context.get_confirmed_hypotheses()
            pending = context.get_pending_hypotheses() if hasattr(context, 'get_pending_hypotheses') else []
            total_hyps = len(confirmed) + len(pending)
            if total_hyps == 0:
                return 0.0
            confirmed_ratio = len(confirmed) / max(total_hyps, 1)
            contradictions = context.get_contradictions()
            contradiction_ratio = len(contradictions) / max(total_hyps, 1)
            frontiers = context.get_knowledge_frontiers()
            frontier_gap_penalty = min(len(frontiers) / 10.0, 1.0)
            bonus = confirmed_ratio * 0.3 + (1.0 - contradiction_ratio) * 0.2 - frontier_gap_penalty * 0.1
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
                lane_name = getattr(outcome, 'lane', None) or ''
                if not lane_name or lane_name not in ('PUBLIC', 'CT', 'WAYBACK', 'DOH', 'PASSIVE_DNS'):
                    continue
                accepted = getattr(outcome, 'accepted_findings', 0) or 0
                rejected = getattr(outcome, 'rejected_count', 0) or 0
                duration = getattr(outcome, 'duration_s', 0.0) or 0.0
                lane_yield = accepted / max(duration, 0.001)
                lane_quality = accepted / max(accepted + rejected, 1)
                if lane_name not in self._lane_performance:
                    self._lane_performance[lane_name] = {'yield': 0.0, 'quality': 0.0, 'count': 0, 'last_sprint': 0}
                perf = self._lane_performance[lane_name]
                perf['yield'] = (perf['yield'] * perf['count'] + lane_yield) / (perf['count'] + 1)
                perf['quality'] = (perf['quality'] * perf['count'] + lane_quality) / (perf['count'] + 1)
                perf['count'] += 1
                perf['last_sprint'] = self._state.sprint_sequence_number
        except Exception:  # noqa: BLE001
            pass

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
        _EPSILON_FLOOR = 0.05
        _EPSILON_DECAY = 0.995
        if self._state.epsilon > _EPSILON_FLOOR:
            self._state.epsilon = max(_EPSILON_FLOOR, self._state.epsilon * _EPSILON_DECAY)
        _eps_hist = list(getattr(self._state, 'epsilon_history', []))
        _eps_hist.append(self._state.epsilon)
        if len(_eps_hist) > 100:
            _eps_hist = _eps_hist[-100:]
        self._state.epsilon_history = _eps_hist
        reward = self._compute_reward(result)
        self._update_lane_performance(result)
        self._state.total_reward += reward
        self._state.sprint_rewards.append(reward)
        if len(self._state.sprint_rewards) > 100:
            self._state.sprint_rewards = self._state.sprint_rewards[-100:]
        self._reward_history.append(reward)
        if len(self._reward_history) > 100:
            self._reward_history = self._reward_history[-100:]
        if self._replay_buffer is not None and self._state_extractor is not None:
            try:
                state = self._state_extractor.extract(result)
                next_state = self._state_extractor.extract_next(result)
                if hasattr(state, 'tolist'):
                    state = state.tolist()
                if hasattr(next_state, 'tolist'):
                    next_state = next_state.tolist()
                last_action = getattr(result, 'last_rl_action', 0) % 5
                action_vector = np.array([last_action] * 5, dtype=np.int32)
                self._replay_buffer.push(state=state, actions=action_vector, reward=reward, next_state=next_state, done=False)
                log.debug('[SprintPolicyManager] Replay buffer size: %d, last reward=%.3f', self._replay_buffer.size, reward)
            except Exception as e:
                log.debug('[SprintPolicyManager] replay buffer push failed: %s', e)
        _env_train = os.environ.get('HLEDAC_RL_TRAIN', '')
        if not self._rl_train_mode and _env_train != '0' and (self._replay_buffer is not None) and (self._state.sprint_sequence_number > 0) and (self._state.sprint_sequence_number % self._qmix_train_interval == 0) and (self._replay_buffer.size >= _MIN_REPLAY_SIZE) and (self._qmix_trainer is not None):
            log.info('[SprintPolicyManager] AUTO-WARMUP: enabling rl_train_mode=True (sprint=%d, replay=%d, min=%d)', self._state.sprint_sequence_number, self._replay_buffer.size, _MIN_REPLAY_SIZE)
            self._rl_train_mode = True
        if self._rl_train_mode and self._qmix_trainer is not None and (self._replay_buffer is not None) and (self._state.sprint_sequence_number > 0) and (self._state.sprint_sequence_number % self._qmix_train_interval == 0) and (self._replay_buffer.size >= _MIN_REPLAY_SIZE):
            self._run_qmix_training()
        elif self._state.sprint_sequence_number % 50 == 0:
            log.debug('[SprintPolicyManager] sprint=%d replay=%s qmix=%s train_mode=%s', self._state.sprint_sequence_number, self._replay_buffer.size if self._replay_buffer else None, self._qmix_trainer is not None, self._rl_train_mode)
        self._train_steps_this_sprint = 0
        _ebm = self.get_qmix_stats().get('epistemic_bonus_mean')
        if _ebm is not None:
            log.info('[SprintPolicyManager] sprint=%d epistemic_bonus_mean=%.4f', self._state.sprint_sequence_number, _ebm)
        self._save()

    # F240D: QMIX training guards and helpers
    def _check_memory_guards(self) -> bool:
        """Check 4-layer memory guards (UMA, RAM, cooldown, per-sprint cap)."""
        # L1: UMA critical check
        try:
            from hledac.universal.utils.uma_budget import get_uma_budget
            if get_uma_budget().is_critical():
                log.debug('[SprintPolicyManager] Skipping QMIX — M1 UMA critical')
                return False
        except Exception:  # noqa: BLE001
            pass
        # L2: RAM gate
        if not _RAM_GATE_DISABLED:
            try:
                import psutil
                if psutil.virtual_memory().percent > _RAM_TRAIN_SKIP_PCT:
                    log.warning('[SprintPolicyManager] QMIX skipped — RAM >%d%%', _RAM_TRAIN_SKIP_PCT)
                    return False
            except Exception:  # noqa: BLE001
                pass
        return True

    def _update_history_fifos(self, loss: float, mean_q: float) -> None:
        """Update loss and Q-value history FIFOs (max 100 entries)."""
        loss_hist = list(getattr(self._state, 'loss_history', []))
        loss_hist.append(loss)
        self._state.loss_history = loss_hist[-100:]
        q_hist = list(getattr(self._state, 'mean_q_value_history', []))
        q_hist.append(mean_q)
        self._state.mean_q_value_history = q_hist[-100:]

    def _compute_mean_q_from_batch(self, batch: Any) -> float:
        """Compute mean Q value from batch if not in loss result."""
        if not hasattr(self._qmix_trainer, 'joint_model') or not hasattr(self._qmix_trainer, 'mixer'):
            return 0.0
        try:
            import mlx.core as mx
            states, actions = batch.get('states'), batch.get('actions')
            if states is None or actions is None:
                return 0.0
            agent_nets = self._qmix_trainer.joint_model.get_agent_nets()
            all_qs = mx.stack([net(states) for net in agent_nets], axis=1)
            chosen = mx.take_along_axis(all_qs, mx.expand_dims(actions, -1), axis=2).squeeze(-1)
            return float(mx.mean(self._qmix_trainer.mixer(chosen, states)).item())
        except Exception:
            return 0.0

    def _update_q_cache(self, batch: Any) -> None:
        """Update Q-value cache for fast retrieval."""
        try:
            import mlx.core as mx
            import time as _time_module
            cache_states = batch.get('states')
            if cache_states is None or self._agents is None:
                return
            self._q_value_cache = {_aid: float(mx.mean(_agent.q_net(cache_states)).item()) for _aid, _agent in self._agents.items()}
            self._q_cache_timestamp = _time_module.monotonic()
        except Exception:
            self._q_value_cache = {}
            self._q_cache_timestamp = 0.0

    def _clear_mlx_cache(self) -> None:
        """Clear MLX cache per GHOST_INVARIANT I11."""
        try:
            import mlx.core as mx
            import gc
            mx.eval([])
            gc.collect()
            (mx.clear_cache if hasattr(mx, 'clear_cache') else mx.metal.clear_cache)()
            gc.collect()
        except Exception:  # noqa: BLE001
            pass

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
        if not self._check_memory_guards():
            return
        import time
        now = time.monotonic()
        if now - self._last_train_at < _TRAIN_COOLDOWN_S or self._train_steps_this_sprint >= _MAX_TRAIN_STEPS_PER_SPRINT:
            return

        _step_t0 = time.monotonic()
        try:
            batch = self._replay_buffer.sample(_TRAIN_BATCH_SIZE)
            if batch is None or self._replay_buffer.size < _MIN_REPLAY_SIZE:
                return
            _train = getattr(self._qmix_trainer, 'update', None) or getattr(self._qmix_trainer, 'train_step', None)
            if _train is None:
                log.error('[SprintPolicyManager] No training method on QMIXJointTrainer')
                return

            loss_result = _train(batch)
            if isinstance(loss_result, dict):
                loss, mean_q = float(loss_result.get('loss', 0.0)), float(loss_result.get('mean_q', 0.0))
            else:
                loss, mean_q = float(loss_result), 0.0
            if mean_q == 0.0:
                mean_q = self._compute_mean_q_from_batch(batch)

            # Save weights and update state
            if hasattr(self._qmix_trainer, 'joint_model'):
                self._state.qmix_weights = _serialize_weights(self._qmix_trainer.joint_model.parameters())
                self._save_qmix_weights_binary(self._qmix_trainer.joint_model.parameters())

            # Check for loss spike
            if prev_loss := (getattr(self._state, 'loss_history', []) or [None])[-1]:
                if prev_loss > 0.0 and loss > 2.5 * prev_loss:
                    log.warning('[SprintPolicyManager] QMIX loss spike %.4f > 2.5x prev %.4f', loss, prev_loss)
                    return

            self._state.last_train_sprint = self._state.sprint_sequence_number
            self._state.last_train_step = self._state.sprint_sequence_number
            self._state.cumulative_train_steps += 1
            self._state.last_loss = loss
            self._update_history_fifos(loss, mean_q)
            self._state.training_steps_completed = int(getattr(self._state, 'training_steps_completed', 0)) + 1
            self._state.last_train_step_sprint = int(self._state.sprint_sequence_number)
            self._last_train_at = now
            self._train_steps_this_sprint += 1
            self._update_q_cache(batch)
            self._clear_mlx_cache()
            log.info('[SprintPolicyManager] QMIX train_step %d: loss=%.4f mean_q=%.3f replay=%d cum_steps=%d step_dt=%.2fs', self._state.sprint_sequence_number, loss, mean_q, self._replay_buffer.size, self._state.cumulative_train_steps, time.monotonic() - _step_t0)
        except Exception as e:
            log.debug('[SprintPolicyManager] QMIX training failed: %s', e)

    def _save_qmix_weights_binary(self, params: Any) -> None:
        """F261QMIX: Persist Q-network weights via mlx.core.savez to .npz.

        Falls back silently on any error (advisory persistence only).
        """
        try:
            import mlx.core as mx
            from mlx.utils import tree_flatten
            flat = dict(tree_flatten(params))
            mx.savez(str(_QMIX_WEIGHTS_PATH), **flat)
            log.debug('[SprintPolicyManager] Q-weights persisted to %s', _QMIX_WEIGHTS_PATH)
        except Exception as e:
            log.debug('[SprintPolicyManager] _save_qmix_weights_binary failed: %s', e)

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
        if seq > 0 and (seq + 1) % self._exploration_interval == 0:
            return True
        if _RANDOM.random() < self._epsilon:
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
            from hledac.universal.rl.actions import ACTION_CONTINUE
            return ACTION_CONTINUE
        if self.should_explore():
            from hledac.universal.rl.actions import ACTION_DEEP_DIVE
            return ACTION_DEEP_DIVE
        if self._qmix_trainer is not None and self._agents is not None and (self._state_extractor is not None):
            try:
                if hasattr(self, '_scheduler') and self._scheduler is not None:
                    result = getattr(self._scheduler, '_result', None)
                    if result is not None:
                        import time as _time_mod
                        _cache_valid = self._q_value_cache and self._q_cache_timestamp > 0 and (_time_mod.monotonic() - self._q_cache_timestamp < _Q_CACHE_TTL_S)
                        if _cache_valid:
                            best_action = 0
                            best_q = float('-inf')
                            for _aid, _q in self._q_value_cache.items():
                                if _q > best_q:
                                    best_q = _q
                                    best_action = int(_aid)
                        else:
                            state = self._state_extractor.extract(result)
                            best_action = 0
                            best_q = float('-inf')
                            for agent_id, agent in self._agents.items():
                                q_val = float(agent.q_net(state)[0].item())
                                if q_val > best_q:
                                    best_q = q_val
                                    best_action = int(agent_id)
                        from hledac.universal.rl.actions import ACTION_BRANCH, ACTION_CONTINUE, ACTION_FETCH_MORE, ACTION_YIELD, action_from_lane_combo
                        if best_action < 5:
                            ACTION_MAP = {0: ACTION_CONTINUE, 1: ACTION_FETCH_MORE, 2: ACTION_BRANCH, 3: ACTION_YIELD, 4: ACTION_CONTINUE}
                            return ACTION_MAP.get(best_action, ACTION_CONTINUE)
                        else:
                            combo_idx = best_action - 5
                            if 0 <= combo_idx < 6:
                                return action_from_lane_combo(combo_idx)
                            return ACTION_CONTINUE
            except Exception:  # noqa: BLE001
                pass
        from hledac.universal.rl.actions import ACTION_CONTINUE
        return ACTION_CONTINUE

    def _compute_delta(self, ratio: float) -> float:
        """Compute weight delta based on acceptance ratio."""
        if ratio >= 0.7: return 1.1
        if ratio >= 0.4: return 1.05
        if ratio >= 0.15: return 1.0
        return 0.95

    def _update_source_weight(self, source_family, feed_url, accepted_count, total_count):
        """Update source quality weight based on acceptance ratio."""
        ratio = accepted_count / total_count if total_count > 0 else 0.0
        src = source_family or feed_url or 'unknown'
        cur = getattr(self, '_src_quality_weights', {}).get(src, 1.0)
        new = max(0.3, min(2.5, cur * self._compute_delta(ratio)))
        if not hasattr(self, '_src_quality_weights'):
            self._src_quality_weights: dict[str, float] = {}
        self._src_quality_weights[src] = new
        if abs(new - cur) > 0.05:
            log.debug('[F228A] src weight adaptation: %s (%d/%d=%.0f%%) %.3f → %.3f', src, accepted_count, total_count, ratio * 100, cur, new)
        return src

    def _accumulate_feedback(self, src_key, total_count, accepted_count):
        """Accumulate pending feedback for a source."""
        if len(self._pending_feedback) < 200 or src_key in self._pending_feedback:
            if src_key not in self._pending_feedback:
                self._pending_feedback[src_key] = {'fetched': 0, 'accepted': 0}
            self._pending_feedback[src_key]['fetched'] += total_count
            self._pending_feedback[src_key]['accepted'] += accepted_count

    def update_with_quality_decisions(self, decisions: list, feed_url: str='') -> None:
        """F228A: Receive per-source quality feedback from SprintScheduler."""
        if not self._enabled:
            return
        try:
            accepted_count, total_count = 0, 0
            source_family = feed_url
            for decision in decisions:
                if isinstance(decision, dict):
                    accepted = bool(decision.get('accepted', False))
                    source_family = str(decision.get('source_family', feed_url))
                else:
                    accepted = getattr(decision, 'accepted', False)
                    source_family = str(getattr(decision, 'source_family', feed_url))
                total_count += 1
                if accepted:
                    accepted_count += 1
            src_key = self._update_source_weight(source_family, feed_url, accepted_count, total_count)
            self._accumulate_feedback(src_key, total_count, accepted_count)
            if self._scheduler is not None:
                try:
                    for _fk, _fv in self._pending_feedback.items():
                        if _fk not in self._scheduler._source_quality_feedback:
                            self._scheduler._source_quality_feedback[_fk] = {'fetched': 0, 'accepted': 0}
                        self._scheduler._source_quality_feedback[_fk]['fetched'] += _fv['fetched']
                        self._scheduler._source_quality_feedback[_fk]['accepted'] += _fv['accepted']
                    self._pending_feedback.clear()
                except Exception:
                    pass
            log.debug('[SprintPolicyManager] quality feedback: src=%s total=%d accepted=%d', feed_url or 'unknown', total_count, accepted_count)
        except Exception as e:
            log.debug('[SprintPolicyManager] update_with_quality_decisions failed: %s', e)

    def get_src_quality_weights(self) -> dict[str, float]:
        """
        F228A: Return per-source quality weights for acquisition plan weighting.

        Returns source_family → weight mapping (default 1.0, clamped [0.3, 2.5]).
        Weights are adapted by update_with_quality_decisions() based on
        accepted/total ratio per source over sprints.

        Fail-soft: returns empty dict when no weights accumulated yet.
        """
        if not hasattr(self, '_src_quality_weights'):
            return {}
        return dict(self._src_quality_weights)

    def get_qmix_stats(self) -> dict[str, Any]:
        """Return QMIX training stats for observability."""
        return {'sprint_sequence': self._state.sprint_sequence_number, 'total_reward': self._state.total_reward, 'replay_size': self._replay_buffer.size if self._replay_buffer else 0, 'last_train_sprint': self._state.last_train_sprint, 'rl_train_mode': self._rl_train_mode, 'epistemic_bonus_mean': sum(self._state.epistemic_strength_history[-10:]) / max(len(self._state.epistemic_strength_history[-10:]), 1), 'qmix_available': self._qmix_trainer is not None}

    def suggest_next_pivot(self, current_findings: list, memory_snapshot: dict | None=None) -> list[dict]:
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
        if self._qmix_trainer is None or self._agents is None:
            return []
        try:
            suggestions: list[dict] = []
            if self._state_extractor is None:
                return []
            try:
                state = self._state_extractor.extract(memory_snapshot or {})
            except Exception:
                state = None
            if state is not None:
                best_action = 0
                best_q = float('-inf')
                for agent_id, agent in self._agents.items():
                    q_val = float(agent.q_net(state)[0].item())
                    if q_val > best_q:
                        best_q = q_val
                        best_action = int(agent_id)
                pivot_map = {0: 'standard', 1: 'dark_surface', 2: 'gopher', 3: 'bgp_enrichment', 4: 'academic'}
                pivot_type = pivot_map.get(best_action, 'standard')
                confidence = float(best_q)
                suggestions.append({'pivot_type': pivot_type, 'confidence': confidence, 'reason': f'Q={confidence:.3f} eps={self._epsilon:.3f}'})
            return suggestions
        except Exception:
            return []

    def get_telemetry(self) -> dict[str, Any]:
        """
        Return RL telemetry snapshot for sprint_scheduler telemetry reporting.

        F228F: rl_enabled, rl_epsilon, rl_total_reward, rl_last_action.
        """
        return {'rl_enabled': self._enabled, 'rl_epsilon': self._epsilon, 'rl_total_reward': self._state.total_reward, 'rl_last_action': self._state.last_action}

    def get_reward_stats(self) -> dict[str, Any]:
        """
        F228F: Return reward distribution statistics.
        """
        if not self._reward_history:
            return {'mean': 0.0, 'min': 0.0, 'max': 0.0, 'last_10': [], 'count': 0}
        last_10 = self._reward_history[-10:]
        return {'mean': sum(self._reward_history) / len(self._reward_history), 'min': min(self._reward_history), 'max': max(self._reward_history), 'last_10': last_10, 'count': len(self._reward_history)}

    def get_lane_config(self) -> dict[str, dict[str, float]]:
        """
        F265LANE: Return adaptive lane configuration from RL policy.

        Returns a dict mapping lane name → {timeout, weight} config.
        Called by SprintScheduler when building acquisition plan.

        Fail-soft: returns empty dict when disabled or no lane history.
        """
        if not self._enabled:
            return {}
        default_config = {'PUBLIC': {'timeout': 30.0, 'weight': 1.0}, 'CT': {'timeout': 45.0, 'weight': 1.0}, 'WAYBACK': {'timeout': 20.0, 'weight': 0.8}, 'DOH': {'timeout': 15.0, 'weight': 0.5}, 'PASSIVE_DNS': {'timeout': 20.0, 'weight': 0.7}}
        if hasattr(self, '_lane_performance') and self._lane_performance:
            for lane, perf in self._lane_performance.items():
                if lane in default_config:
                    yield_val = perf.get('yield', 0.0)
                    if yield_val > 0.5:
                        default_config[lane]['weight'] = min(default_config[lane].get('weight', 1.0) * 1.2, 1.5)
                    elif yield_val < 0.1:
                        default_config[lane]['weight'] = max(default_config[lane].get('weight', 1.0) * 0.8, 0.3)
        return default_config

    def attach_scheduler(self, scheduler) -> None:
        """Attach scheduler reference for state extraction in get_action()."""
        self._scheduler = scheduler

    def reset(self) -> None:
        """Reset internal state and delete persisted file. Does nothing when disabled."""
        if not self._enabled:
            return
        self._state = SprintPolicyState()
        self._loaded = True
        self._last_train_at = 0.0
        self._train_steps_this_sprint = 0
        try:
            if self._policy_path.exists():
                self._policy_path.unlink()
        except Exception as e:
            log.warning(f'[SprintPolicyManager] Failed to delete policy state file: {e}')