"""
runtime/resource_governor.py — M1ResourceGovernor advisory safety layer

ROLE: Advisory safety layer for branch concurrency, model lease, and renderer lease.









NOT a sprint owner. Reads from canonical sources:
- brain/model_lifecycle.get_model_lifecycle_status() — model lease state
- core/resource_governor.sample_uma_status() — UMA memory state
- utils.concurrency.adjust_fetch_workers() — fetch concurrency control

CONSTRAINTS (from F202J spec):
- Model lifecycle authority remains brain/model_lifecycle.py
- No model + JS renderer concurrently
- FETCH_SEMAPHORE limit=3 while model loaded
- Governor fail-soft fallback is safe low-concurrency mode

F204J: Enforced M1 Mission Budget
- sidecar_admission() enforces sidecar skip on RAM pressure
- MissionBudgetSnapshot captures budget decisions for scorecard export
- MISSION_PEAK_RSS_GIB = 5.5 GiB hard ceiling
- SIDECAR_DEFAULT_ESTIMATE_MB = 128 MB per sidecar
- HEAVY_SIDECARS: embedding, wayback_diff, social_identity, rir_correlation

Invariant table:
  Invariant                          | Test file:method
  ─────────────────────────────────────────────────────────────────────
  model_loaded path → fetch_limit=3  | test_m1_resource_governor.py:test_governor_sets_fetch_limit_3_when_model_loaded
  model_unloaded path → fetch_limit=25| test_m1_resource_governor.py:test_governor_restores_fetch_limit_25_when_model_unloaded  # noqa: E501
  no_model_plus_renderer_concurrently| test_m1_resource_governor.py:test_no_renderer_when_model_loaded
  advisory_only_fails_soft           | test_m1_resource_governor.py:test_advisory_fails_soft
  sidecar_admission checks RSS/high_water | test_m1_mission_budget.py:test_sidecar_admission_rss_guard
  sidecar_admission checks uma_state | test_m1_mission_budget.py:test_sidecar_admission_uma_critical
"""
from __future__ import annotations
import asyncio
import logging
import threading
from hledac.universal.utils.async_helpers import safe_create_task
import msgspec
from hledac.universal.core.resource_governor import UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY, UMA_STATE_WARN, sample_uma_status
from hledac.universal.core.resource_governor import PressureState, uma_state_to_pressure_state
try:
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal.core.rust_backend import rust
    sync_adaptive_state = rust.raw.sync_adaptive_state
except Exception:
    sync_adaptive_state = None
logger = logging.getLogger(__name__)
DEFAULT_FETCH_LIMIT = 25
MODEL_LOADED_FETCH_LIMIT = 3
CRITICAL_FETCH_LIMIT = 6
CRITICAL_BRANCH_CONCURRENCY = 1
CRITICAL_NEAR_EMERGENCY_BRANCH_CONCURRENCY = 2
CRITICAL_MILD_BRANCH_CONCURRENCY = 3
MODEL_LOADED_BRANCH_CONCURRENCY = 2
CRITICAL_ALLOW_RENDERER = False
CRITICAL_ALLOW_MODEL_LOAD = False
_EMA_ALPHA = 0.3
MISSION_PEAK_RSS_GIB: float = 5.5
SIDECAR_DEFAULT_ESTIMATE_MB: int = 128
# [FINAL]-019: HEAVY_SIDECARS now includes memory cost metadata for budget accounting.
# In windup mode (WINDUP QoS level), these are skipped to reduce pressure.
HEAVY_SIDECARS: tuple[str, ...] = ('embedding', 'wayback_diff', 'social_identity', 'rir_correlation')
# [FINAL]-019: Memory cost in MB per sidecar instance. Used by sidecar_admission()
# to compute whether the sidecar fits within the remaining budget.
HEAVY_SIDECAR_COST_MB: dict[str, int] = {
    'embedding': 400,       # MLX embedding generation (~0.4 GB peak)
    'wayback_diff': 256,    # WARC replay + diff (~0.25 GB peak)
    'social_identity': 192,  # Social graph correlation (~0.2 GB peak)
    'rir_correlation': 256,  # BGP/RIR correlation (~0.25 GB peak)
}
# Light sidecar cost for budget accounting
LIGHT_SIDECAR_COST_MB: int = 128  # Default ~0.13 GB peak
MAX_BUDGET_EVENTS: int = 100

# [FINAL]-019-07: Register sidecar costs in the global CapabilityCostRegistry.
# This migrates from hardcoded HEAVY_SIDECAR_COST_MB to the dynamic registry.
# QoSLadderController queries these for optimal triage decisions.
try:
    from hledac.universal.core.capability_cost import register_capability_cost
    register_capability_cost("embedding", rss_mb=400, peak_mb=600, tier="heavy", tags=("mlx", "embedding"))
    register_capability_cost("wayback_diff", rss_mb=256, peak_mb=384, tier="medium", tags=("archive", "diff"))
    register_capability_cost("social_identity", rss_mb=192, peak_mb=256, tier="light", tags=("graph", "social"))
    register_capability_cost("rir_correlation", rss_mb=256, peak_mb=384, tier="medium", tags=("network", "bgp"))
    # Additional sidecars from sidecar_bus.py _HEAVY_SIDECARS
    register_capability_cost("identity_stitching", rss_mb=192, peak_mb=256, tier="medium", tags=("graph", "identity"))
    register_capability_cost("sprint_diff", rss_mb=128, peak_mb=192, tier="light", tags=("diff", "export"))
    register_capability_cost("banner_grab", rss_mb=128, peak_mb=192, tier="light", tags=("network", "recon"))
    register_capability_cost("ipv6_recon", rss_mb=128, peak_mb=192, tier="light", tags=("network", "recon"))
    register_capability_cost("pattern_mining", rss_mb=256, peak_mb=384, tier="medium", tags=("analytics", "ml"))
except Exception:  # noqa: BLE001
    # Fail-soft: capability_cost module may not be available in all environments
    pass

class SidecarAdmission(msgspec.Struct, frozen=True, gc=False):
    """F204J: Result of sidecar admission check."""
    allowed: bool
    sidecar_name: str
    reason: str
    rss_gib: float
    uma_state: str
    estimated_mb: int

class RendererAdmission(msgspec.Struct, frozen=True, gc=False):
    """F214R: Result of renderer admission check.

    One unified answer to: can JS renderer be used right now?
    Combines model lifecycle + UMA state in one call.
    """
    allowed: bool
    reason: str
    uma_state: str
    model_loaded: bool

class ModelAdmission(msgspec.Struct, frozen=True, gc=False):
    """F214R: Result of model load admission check.

    One unified answer to: can a new model load be initiated?
    Uses current UMA state (not model lifecycle — that's caller-provided).
    """
    allowed: bool
    reason: str
    uma_state: str
    free_uma_gib: float

class BranchAdmission(msgspec.Struct, frozen=True, gc=False):
    """F214R: Result of branch admission check.

    Answers: can a named branch run given current memory state?
    estimated_mb is the expected RAM cost of the branch.
    """
    allowed: bool
    reason: str
    uma_state: str
    branch_concurrency: int
    estimated_mb: int

class LaneAdmission(msgspec.Struct, frozen=True, gc=False):
    """F214R: Result of lane admission check.

    Answers: can a named lane be admitted given current memory state?
    risk_level: "low" | "medium" | "high" | "critical"
    estimated_mb: expected RAM cost of the lane.
    """
    allowed: bool
    reason: str
    uma_state: str
    risk_level: str

class MissionBudgetSnapshot(msgspec.Struct, frozen=True, gc=False):
    """F204J: Budget snapshot for scorecard export."""
    sprint_id: str
    peak_rss_gib: float
    peak_uma_used_gib: float
    sidecars_skipped: tuple[str, ...]
    model_loaded: bool
    renderer_allowed: bool
    fetch_limit: int

class GovernorDecision(msgspec.Struct, frozen=True, gc=False):
    """Output of M1ResourceGovernor.evaluate()."""
    fetch_limit: int
    allow_renderer: bool
    allow_model_load: bool
    branch_concurrency: int
    reason: str
    uma_state: str
    model_loaded: bool
    renderer_denied_count: int = 0
    model_denied_count: int = 0
    free_uma_gib: float = 0.0
    system_used_gib: float = 0.0
    swap_detected: bool = False

class GovernorSnapshot(msgspec.Struct, frozen=True, gc=False):
    """Snapshot of governor internal state for dashboard rendering."""
    uma_state: str
    model_loaded: bool
    fetch_limit: int
    branch_concurrency: int
    renderer_denied_count: int
    model_denied_count: int
    system_used_gib: float
    io_only: bool
    free_uma_gib: float = 0.0
    swap_detected: bool = False
    ema_branch_pressure: float = 0.0

class M1ResourceGovernor:
    """
    Advisory safety layer for M1 8GB sprint execution.

    Governs: branch concurrency, model lease, renderer lease.
    Always-on, fail-soft. Never blocks the sprint — only advises.

    Read-only surfaces:
        brain.model_lifecycle.get_model_lifecycle_status()
        core.resource_governor.sample_uma_status()
        utils.concurrency.FETCH_SEMAPHORE.limit()
    """
    __slots__ = tuple(('_branch_concurrency', '_current_workers', '_ema_branch_timeouts', '_fetch_limit', '_lock', '_model_denied_count', '_model_loaded', '_renderer_denied_count', '_snapshot_lock', '_uma_state', '_worker_adjust_queue', '_worker_adjust_task'))

    def __init__(self) -> None:
        self._fetch_limit = DEFAULT_FETCH_LIMIT
        self._renderer_denied_count = 0
        self._model_denied_count = 0
        self._model_loaded = False
        self._lock = asyncio.Lock()
        self._snapshot_lock = threading.RLock()
        self._uma_state = 'ok'
        self._ema_branch_timeouts: float = 0.0
        self._branch_concurrency: int = 4
        self._worker_adjust_queue: asyncio.Queue[int] = asyncio.Queue(maxsize=64)
        self._worker_adjust_task: asyncio.Task[None] | None = None
        self._current_workers: int = DEFAULT_FETCH_LIMIT

    @property
    def ema_branch_pressure(self) -> float:
        """Return current EMA branch timeout pressure for telemetry."""
        return self._ema_branch_timeouts

    def record_branch_timeout(self) -> None:
        """
        Record a branch timeout for EMA tracking.

        Call this wherever branch_timeout_count is incremented.
        EMA formula: ema = alpha * 1.0 + (1 - alpha) * ema
        with alpha = 0.3 (responsive without hyperreactivity).

        Issue #22: _snapshot_lock prevents torn reads/writes when snapshot()
        reads _ema_branch_timeouts concurrently.
        """
        with self._snapshot_lock:
            self._ema_branch_timeouts = _EMA_ALPHA * 1.0 + (1 - _EMA_ALPHA) * self._ema_branch_timeouts

    def record_branch_success(self) -> None:
        """
        Record a successful branch completion for EMA decay.

        Decays the EMA toward 0: ema = (1 - alpha) * ema

        Issue #22: _snapshot_lock prevents torn reads/writes when snapshot()
        reads _ema_branch_timeouts concurrently.
        """
        with self._snapshot_lock:
            self._ema_branch_timeouts = (1 - _EMA_ALPHA) * self._ema_branch_timeouts

    def _sync_adaptive_threshold(self, uma_state: str) -> None:
        """Push memory pressure to Rust adaptive_scheduler for thread pool adaptation."""
        if sync_adaptive_state is None:
            return
        pressure: int
        if uma_state in ('ok', 'soft_warn'):
            pressure = 0
        elif uma_state == 'warn':
            pressure = 1
        else:
            pressure = 2
        try:
            sync_adaptive_state(pressure, 0)
        except Exception:  # noqa: BLE001
            pass

    def _ensure_consumer_running(self) -> None:
        """
        Start the worker-adjust consumer task if not already running.

        Called by evaluate() / evaluate_adaptive() / apply_decision() before
        enqueuing a request. Idempotent — safe to call multiple times.
        """
        if self._worker_adjust_task is None or self._worker_adjust_task.done():
            self._worker_adjust_task = safe_create_task(self._worker_adjust_consumer())

    async def _worker_adjust_consumer(self) -> None:
        """
        Background consumer that applies worker count changes while holding self._lock.

        This is the ONLY place where self._current_workers is written.
        The lock is held only during the actual semaphore update — never blocks
        the producer path (evaluate/evaluate_adaptive/apply_decision).
        """
        while True:
            try:
                new_count = await self._worker_adjust_queue.get()
            except asyncio.CancelledError:
                break
            try:
                async with self._lock:
                    self._current_workers = new_count
                    await self._adjust_workers_locked(new_count)
            except Exception as exc:
                logger.debug('[Governor] _adjust_workers_locked failed: %s', exc)

    async def _adjust_workers_locked(self, new_count: int) -> None:
        """
        Apply worker count change to concurrency primitives.

        Called while holding self._lock from _worker_adjust_consumer().
        """
        try:
            from hledac.universal.utils.concurrency import adjust_fetch_workers
            await adjust_fetch_workers(new_count)
            self._fetch_limit = new_count
        except Exception as exc:
            logger.debug('[Governor] adjust_fetch_workers failed: %s', exc)

    def _try_enqueue_adjust(self, fetch_limit: int) -> None:
        """
        Enqueue fetch_limit adjustment with back-pressure on overflow.

        P1-2 fix: asyncio.Queue(maxsize=64) replaces unbounded Queue().
        On overflow put_nowait drops the message and logs a warning — the
        governor's AIMD loop will eventually converge via the next evaluate()
        call. This prevents unbounded queue growth during degraded/emergency
        mode where evaluate() is called every 5s but _worker_adjust_consumer
        may fall behind.
        """
        try:
            self._worker_adjust_queue.put_nowait(fetch_limit)
        except asyncio.QueueFull:
            logger.warning(
                '[Governor] adjust queue overflow (maxsize=64) — dropping '
                'fetch_limit=%d. Consumer may lag; AIMD convergence via next cycle.',
                fetch_limit,
            )

    async def evaluate(self) -> GovernorDecision:
        """
        Evaluate governor decisions for the current cycle.

        Returns GovernorDecision with:
        - fetch_limit: new FETCH_SEMAPHORE limit
        - allow_renderer: True if JS renderer may be used
        - allow_model_load: True if model load is permitted
        - branch_concurrency: recommended branch parallelism
        - reason: human-readable decision rationale
        - free_uma_gib: available UMA GiB for QuantizationSelector
        - system_used_gib: system memory used in GiB (F265H)
        - swap_detected: True if swap > 3.5 GiB (F265H)

        Self-applying: calls apply_decision() before returning so all
        decision fields (fetch_limit, counters) are propagated to runtime
        surfaces. This eliminates the 90% drift problem where evaluate() was
        called everywhere but apply_decision() was called only 2×.

        Fails soft: returns safe defaults on any error.
        """
        async with self._lock:
            decision = self._evaluate_locked()
        self._ensure_consumer_running()
        self._try_enqueue_adjust(decision.fetch_limit)
        return decision

    def _evaluate_locked(self) -> GovernorDecision:
        """
        Build GovernorDecision while caller holds self._lock.

        Called by evaluate() (holds lock) and evaluate_adaptive() (holds lock).
        Updates self._uma_state, self._model_loaded, counters on self.
        """
        free_uma_gib = 0.0
        system_used_gib = 0.0
        swap_detected = False
        try:
            uma = sample_uma_status()
            self._uma_state = uma.state
            system_used_gib = uma.system_used_gib
            swap_detected = uma.swap_detected
            free_uma_gib = uma.system_available_gib
            self._sync_adaptive_threshold(uma.state)
        except Exception as exc:
            logger.debug('[Governor] sample_uma_status failed: %s', exc)
            self._uma_state = 'ok'
        try:
            model_status = self._get_model_status()
            self._model_loaded = model_status.get('loaded', False)
        except Exception as exc:
            logger.debug('[Governor] get_model_lifecycle_status failed: %s', exc)
            self._model_loaded = False
        if not self._model_loaded:
            pass
        fetch_limit = DEFAULT_FETCH_LIMIT
        allow_renderer = True
        allow_model_load = True
        branch_concurrency = 4
        if self._uma_state == UMA_STATE_EMERGENCY:
            fetch_limit = CRITICAL_FETCH_LIMIT
            allow_renderer = CRITICAL_ALLOW_RENDERER
            allow_model_load = CRITICAL_ALLOW_MODEL_LOAD
            branch_concurrency = CRITICAL_BRANCH_CONCURRENCY
            logger.info('[Governor] emergency triggered: uma_state=%s system_used_gib=%.2f swap_detected=%s → fetch_limit=%d branch_concurrency=%d', self._uma_state, system_used_gib, swap_detected, fetch_limit, branch_concurrency)
            reason = f'UMA {self._uma_state}: safe mode'
        elif self._uma_state == UMA_STATE_CRITICAL:
            fetch_limit = CRITICAL_FETCH_LIMIT
            allow_renderer = CRITICAL_ALLOW_RENDERER
            allow_model_load = CRITICAL_ALLOW_MODEL_LOAD
            if system_used_gib >= 6.85:
                branch_concurrency = CRITICAL_NEAR_EMERGENCY_BRANCH_CONCURRENCY
                reason = f'UMA {self._uma_state}: near_emergency reduced concurrency'
            else:
                branch_concurrency = CRITICAL_MILD_BRANCH_CONCURRENCY
                reason = f'UMA {self._uma_state}: mild reduced concurrency'
            logger.info('[Governor] critical triggered: uma_state=%s system_used_gib=%.2f swap_detected=%s → fetch_limit=%d branch_concurrency=%d', self._uma_state, system_used_gib, swap_detected, fetch_limit, branch_concurrency)
        elif self._model_loaded:
            fetch_limit = MODEL_LOADED_FETCH_LIMIT
            allow_renderer = False
            allow_model_load = False
            branch_concurrency = 2
            reason = 'model_loaded: reduced concurrency'
        elif self._uma_state == UMA_STATE_WARN:
            fetch_limit = max(3, DEFAULT_FETCH_LIMIT // 2)
            allow_renderer = True
            allow_model_load = True
            branch_concurrency = 3
            reason = 'UMA warn: reduced concurrency'
        else:
            fetch_limit = DEFAULT_FETCH_LIMIT
            allow_renderer = True
            allow_model_load = True
            branch_concurrency = 4
            reason = 'normal: full concurrency'
        if not allow_renderer:
            self._renderer_denied_count += 1
        if not allow_model_load:
            self._model_denied_count += 1
        self._branch_concurrency = branch_concurrency
        return GovernorDecision(fetch_limit=fetch_limit, allow_renderer=allow_renderer, allow_model_load=allow_model_load, branch_concurrency=branch_concurrency, reason=reason, uma_state=self._uma_state, model_loaded=self._model_loaded, renderer_denied_count=self._renderer_denied_count, model_denied_count=self._model_denied_count, free_uma_gib=free_uma_gib, system_used_gib=system_used_gib, swap_detected=swap_detected)

    async def evaluate_adaptive(self) -> GovernorDecision:
        """
        F2-2: EMA-adaptive governor evaluation.

        Runs the base evaluate() logic, then applies EMA timeout pressure override
        on top of branch_concurrency only. The EMA tracks sustained timeout
        pressure (0.0 = no pressure, 1.0 = continuous timeouts) and degrades
        branch concurrency accordingly before the base UMA state would.

        This is additive — it does NOT replace evaluate(). The EMA override is
        applied as a post-processing step to the base decision's branch_concurrency.

        EMA thresholds:
          ema > 0.7  → sustained high pressure  → branch_concurrency = 1
          ema > 0.4  → medium pressure          → branch_concurrency = min(base, 2)
          ema ≤ 0.4   → no/low pressure         → branch_concurrency unchanged

        Fails soft: falls back to safe defaults on any error.
        """
        async with self._lock:
            try:
                base = self._evaluate_locked()
            except Exception as exc:
                logger.debug('[Governor] evaluate_adaptive base _evaluate_locked failed: %s', exc)
                return GovernorDecision(fetch_limit=DEFAULT_FETCH_LIMIT, allow_renderer=True, allow_model_load=True, branch_concurrency=4, reason='evaluate_adaptive_fallback: base_evaluate_locked_failed', uma_state='ok', model_loaded=False)
            ema = self._ema_branch_timeouts
            if ema > 0.7:
                branch_concurrency = 1
                reason = f'{base.reason} | ema_timeout:{ema:.2f}>0.7→branch=1'
            elif ema > 0.4:
                branch_concurrency = min(base.branch_concurrency, 2)
                reason = f'{base.reason} | ema_timeout:{ema:.2f}>0.4→branch=min({base.branch_concurrency},2)'
            else:
                branch_concurrency = base.branch_concurrency
                reason = base.reason
            decision = GovernorDecision(fetch_limit=base.fetch_limit, allow_renderer=base.allow_renderer, allow_model_load=base.allow_model_load, branch_concurrency=branch_concurrency, reason=reason, uma_state=base.uma_state, model_loaded=base.model_loaded, renderer_denied_count=base.renderer_denied_count, model_denied_count=base.model_denied_count, free_uma_gib=base.free_uma_gib, system_used_gib=base.system_used_gib, swap_detected=base.swap_detected)
            self._branch_concurrency = branch_concurrency
        self._ensure_consumer_running()
        self._try_enqueue_adjust(decision.fetch_limit)
        return decision

    def sidecar_admission(self, sidecar_name: str, estimated_mb: int=SIDECAR_DEFAULT_ESTIMATE_MB) -> SidecarAdmission:
        """
        F204J: Check if a sidecar can be admitted given current memory state.

        [FINAL]-019-07: Now queries CapabilityCostRegistry for dynamic cost metadata.
        If a registered cost is available, uses rss_mb instead of estimated_mb.
        This enables the QoS ladder to make optimal triage decisions.

        Returns SidecarAdmission with:
        - allowed: True if sidecar should run
        - reason: human-readable denial reason (now includes tier + savings info)
        - rss_gib: current RSS in GiB
        - uma_state: current UMA state
        - estimated_mb: the actual cost used (registered rss_mb or provided estimate)

        Fails soft: returns allowed=True if any check fails.
        """
        # [FINAL]-019-07: Use registered cost if available
        try:
            from hledac.universal.core.capability_cost import get_capability_cost
            cost = get_capability_cost(sidecar_name, default_rss_mb=estimated_mb)
            actual_mb = cost.rss_mb
        except Exception:
            actual_mb = estimated_mb
            cost = None

        try:
            uma = sample_uma_status()
            rss_gib = uma.system_used_gib / 1024 ** 3 if uma.system_used_gib else 0.0
            uma_state = uma.state
        except Exception as exc:
            logger.debug('[Governor] sidecar_admission sample_uma_status failed: %s', exc)
            return SidecarAdmission(allowed=True, sidecar_name=sidecar_name, reason='uma_check_failed_allowing', rss_gib=0.0, uma_state='unknown', estimated_mb=actual_mb)

        # [FINAL]-019-07: Block HEAVY/CRITICAL tier capabilities under critical/emergency
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            is_heavy = sidecar_name in HEAVY_SIDECARS
            is_heavy_tier = cost is not None and cost.tier in ("heavy", "critical") if cost else is_heavy
            if is_heavy or is_heavy_tier:
                tier_info = f" ({cost.tier} tier, saves {cost.savings_mb}MB)" if cost else ""
                return SidecarAdmission(
                    allowed=False,
                    sidecar_name=sidecar_name,
                    reason=f'uma_{uma_state}_blocking_heavy_sidecar{tier_info}',
                    rss_gib=rss_gib,
                    uma_state=uma_state,
                    estimated_mb=actual_mb,
                )
        if sidecar_name in HEAVY_SIDECARS:
            try:
                if hasattr(uma, 'high_water') and uma.high_water > 0.85:
                    tier_info = f" ({cost.tier} tier)" if cost else ""
                    return SidecarAdmission(allowed=False, sidecar_name=sidecar_name, reason=f'high_water_exceeded_85pct{tier_info}', rss_gib=rss_gib, uma_state=uma_state, estimated_mb=actual_mb)
                if rss_gib > MISSION_PEAK_RSS_GIB - 0.5:
                    tier_info = f" ({cost.tier} tier)" if cost else ""
                    return SidecarAdmission(allowed=False, sidecar_name=sidecar_name, reason=f'rss_exceeds_headroom_limit{tier_info}', rss_gib=rss_gib, uma_state=uma_state, estimated_mb=actual_mb)
            except Exception:  # noqa: BLE001
                pass
        return SidecarAdmission(allowed=True, sidecar_name=sidecar_name, reason='admitted', rss_gib=rss_gib, uma_state=uma_state, estimated_mb=actual_mb)

    def _get_model_status(self) -> dict:
        """Read-only model status from canonical lifecycle API."""
        try:
            from hledac.universal.brain.model_lifecycle import get_model_lifecycle_status
            return get_model_lifecycle_status()
        except Exception as exc:
            logger.debug('[Governor] get_model_lifecycle_status failed: %s', exc)
            return {'loaded': False, 'current_model': None, 'initialized': False, 'last_error': None}

    def renderer_admission(self) -> RendererAdmission:
        """
        F214R: Canonical renderer admission check.
        @pending_integration: no confirmed production call sites as of F214R audit.
        See tests/test_resource_governor_authority_seal.py::TestPendingIntegrationMarkers.

        Returns RendererAdmission with:
        - allowed: True if JS renderer may be used
        - reason: human-readable denial reason
        - uma_state: current UMA state
        - model_loaded: whether model is currently loaded

        Combines model lifecycle + UMA state in one authoritative call.
        Fail-soft: returns allowed=False with "unknown" reason on errors.
        """
        uma_state = 'ok'
        try:
            uma = sample_uma_status()
            uma_state = uma.state
        except Exception as exc:
            logger.debug('[Governor] renderer_admission sample_uma_status failed: %s', exc)
            return RendererAdmission(allowed=False, reason='uma_check_failed', uma_state='unknown', model_loaded=False)
        model_loaded = False
        try:
            model_status = self._get_model_status()
            model_loaded = model_status.get('loaded', False)
        except Exception as exc:
            logger.debug('[Governor] renderer_admission get_model_status failed: %s', exc)
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            return RendererAdmission(allowed=False, reason=f'uma_{uma_state}_blocking_renderer', uma_state=uma_state, model_loaded=model_loaded)
        if model_loaded:
            return RendererAdmission(allowed=False, reason='model_loaded_blocking_renderer', uma_state=uma_state, model_loaded=True)
        return RendererAdmission(allowed=True, reason='admitted', uma_state=uma_state, model_loaded=model_loaded)

    def model_admission(self, _model_name: str='', estimated_mb: int=0) -> ModelAdmission:
        """
        F214R: Canonical model load admission check.
        @pending_integration: no confirmed production call sites as of F214R audit.
        See tests/test_resource_governor_authority_seal.py::TestPendingIntegrationMarkers.

        Returns ModelAdmission with:
        - allowed: True if model load is permitted
        - reason: human-readable denial reason
        - uma_state: current UMA state
        - free_uma_gib: available UMA GiB

        Note: actual model lifecycle is managed by brain/model_lifecycle.py.
        This only checks UMA state suitability for a new load.
        Fail-soft: returns allowed=False with "unknown" reason on errors.
        """
        uma_state = 'ok'
        free_uma_gib = 0.0
        try:
            uma = sample_uma_status()
            uma_state = uma.state
            free_uma_gib = getattr(uma, 'system_available_gib', 0.0)
        except Exception as exc:
            logger.debug('[Governor] model_admission sample_uma_status failed: %s', exc)
            return ModelAdmission(allowed=False, reason='uma_check_failed', uma_state='unknown', free_uma_gib=0.0)
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            return ModelAdmission(allowed=False, reason=f'uma_{uma_state}_blocking_model_load', uma_state=uma_state, free_uma_gib=free_uma_gib)
        if estimated_mb > 0 and free_uma_gib > 0:
            if estimated_mb / 1024 > free_uma_gib * 0.9:
                return ModelAdmission(allowed=False, reason='insufficient_uma_for_model_load', uma_state=uma_state, free_uma_gib=free_uma_gib)
        return ModelAdmission(allowed=True, reason='admitted', uma_state=uma_state, free_uma_gib=free_uma_gib)

    def branch_admission(self, _branch_name: str='', estimated_mb: int=0) -> BranchAdmission:
        """
        F214R: Canonical branch admission check.
        @pending_integration: no confirmed production call sites as of F214R audit.
        See tests/test_resource_governor_authority_seal.py::TestPendingIntegrationMarkers.

        Returns BranchAdmission with:
        - allowed: True if branch can run
        - reason: human-readable denial reason
        - uma_state: current UMA state
        - branch_concurrency: recommended concurrency for this branch
        - estimated_mb: the estimate that was evaluated

        Fail-soft: returns allowed=True with normal concurrency on errors.
        """
        uma_state = 'ok'
        system_used_gib = 5.0
        branch_concurrency = 4
        try:
            uma = sample_uma_status()
            uma_state = uma.state
            system_used_gib = uma.system_used_gib
        except Exception as exc:
            logger.debug('[Governor] branch_admission sample_uma_status failed: %s', exc)
            return BranchAdmission(allowed=True, reason='uma_check_failed_allowing', uma_state='unknown', branch_concurrency=4, estimated_mb=estimated_mb)
        if uma_state == UMA_STATE_EMERGENCY:
            return BranchAdmission(allowed=True, reason=f'uma_{uma_state}_reduced_concurrency', uma_state=uma_state, branch_concurrency=1, estimated_mb=estimated_mb)
        elif uma_state == UMA_STATE_CRITICAL:
            if system_used_gib >= 6.85:
                branch_concurrency = 2
            else:
                branch_concurrency = 3
            return BranchAdmission(allowed=True, reason=f'uma_{uma_state}_graduated_concurrency', uma_state=uma_state, branch_concurrency=branch_concurrency, estimated_mb=estimated_mb)
        model_loaded = False
        try:
            model_status = self._get_model_status()
            model_loaded = model_status.get('loaded', False)
        except Exception:  # noqa: BLE001
            pass
        if model_loaded:
            return BranchAdmission(allowed=True, reason='model_loaded_reduced_concurrency', uma_state=uma_state, branch_concurrency=2, estimated_mb=estimated_mb)
        if uma_state == UMA_STATE_WARN:
            branch_concurrency = 3
        return BranchAdmission(allowed=True, reason='admitted', uma_state=uma_state, branch_concurrency=branch_concurrency, estimated_mb=estimated_mb)

    def lane_admission(self, _lane_name: str='', risk_level: str='medium', _estimated_mb: int=0) -> LaneAdmission:
        """
        F214R: Canonical lane admission check.

        Returns LaneAdmission with:
        - allowed: True if lane can be admitted
        - reason: human-readable denial reason
        - uma_state: current UMA state
        - risk_level: the risk level that was evaluated

        risk_level: "low" | "medium" | "high" | "critical"
        Heavy lanes (high/critical risk) are blocked under critical/emergency UMA.
        Fail-soft: returns allowed=True on errors.
        """
        uma_state = 'ok'
        try:
            uma = sample_uma_status()
            uma_state = uma.state
        except Exception as exc:
            logger.debug('[Governor] lane_admission sample_uma_status failed: %s', exc)
            return LaneAdmission(allowed=True, reason='uma_check_failed_allowing', uma_state='unknown', risk_level=risk_level)
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            if risk_level in ('high', 'critical'):
                try:
                    from metrics_registry import get_metrics_registry
                    get_metrics_registry().inc('lane_blocked_reason')
                except Exception:  # noqa: BLE001
                    pass
                return LaneAdmission(allowed=False, reason=f'uma_{uma_state}_blocking_{risk_level}_lane', uma_state=uma_state, risk_level=risk_level)
        if risk_level in ('high', 'critical'):
            try:
                uma = sample_uma_status()
                rss_gib = uma.system_used_gib / 1024 ** 3 if uma.system_used_gib else 0.0
                if hasattr(uma, 'high_water') and uma.high_water > 0.85:
                    try:
                        from metrics_registry import get_metrics_registry
                        get_metrics_registry().inc('lane_blocked_reason')
                    except Exception:  # noqa: BLE001
                        pass
                    return LaneAdmission(allowed=False, reason='high_water_exceeded_85pct', uma_state=uma_state, risk_level=risk_level)
                if rss_gib > MISSION_PEAK_RSS_GIB - 0.5:
                    try:
                        from metrics_registry import get_metrics_registry
                        get_metrics_registry().inc('lane_blocked_reason')
                    except Exception:  # noqa: BLE001
                        pass
                    return LaneAdmission(allowed=False, reason='rss_exceeds_headroom_limit', uma_state=uma_state, risk_level=risk_level)
            except Exception:  # noqa: BLE001
                pass
        return LaneAdmission(allowed=True, reason='admitted', uma_state=uma_state, risk_level=risk_level)

    def snapshot(self) -> GovernorSnapshot:
        """Current state snapshot for dashboard rendering.

        Issue #22: protected by _snapshot_lock (threading.RLock) to prevent
        torn reads when executor threads mutate _ema_branch_timeouts via
        record_branch_timeout()/record_branch_success().
        """
        with self._snapshot_lock:
            free_uma_gib = 0.0
            system_used_gib = 0.0
            swap_detected = False
            io_only = False
            try:
                uma = sample_uma_status()
                free_uma_gib = uma.system_available_gib
                system_used_gib = uma.system_used_gib
                swap_detected = uma.swap_detected
                io_only = uma.io_only
            except Exception:  # noqa: BLE001
                pass
            return GovernorSnapshot(uma_state=self._uma_state, model_loaded=self._model_loaded, fetch_limit=self._fetch_limit, branch_concurrency=self._branch_concurrency, renderer_denied_count=self._renderer_denied_count, model_denied_count=self._model_denied_count, system_used_gib=system_used_gib, io_only=io_only, free_uma_gib=free_uma_gib, swap_detected=swap_detected, ema_branch_pressure=round(self._ema_branch_timeouts, 3))

    async def get_pressure(self) -> PressureState:
        """Get canonical pressure state (UMAGovernor protocol)."""
        return uma_state_to_pressure_state(self._uma_state)

    async def apply_decision(self, decision: GovernorDecision) -> None:
        """
        Apply governor decision to runtime surfaces (advisory only, fail-soft).

        - Updates FETCH_SEMAPHORE limit via queue (Issue #6: lock-free)
        - Tracks denied counts for telemetry
        """
        async with self._lock:
            if not decision.allow_renderer:
                self._renderer_denied_count += 1
            if not decision.allow_model_load:
                self._model_denied_count += 1
        self._ensure_consumer_running()
        self._try_enqueue_adjust(decision.fetch_limit)
_governor: M1ResourceGovernor | None = None

def get_governor() -> M1ResourceGovernor:
    """Get or create the singleton M1ResourceGovernor."""
    global _governor
    if _governor is None:
        _governor = M1ResourceGovernor()
    return _governor