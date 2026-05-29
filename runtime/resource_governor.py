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
  model_unloaded path → fetch_limit=25| test_m1_resource_governor.py:test_governor_restores_fetch_limit_25_when_model_unloaded
  no_model_plus_renderer_concurrently| test_m1_resource_governor.py:test_no_renderer_when_model_loaded
  advisory_only_fails_soft           | test_m1_resource_governor.py:test_advisory_fails_soft
  sidecar_admission checks RSS/high_water | test_m1_mission_budget.py:test_sidecar_admission_rss_guard
  sidecar_admission checks uma_state | test_m1_mission_budget.py:test_sidecar_admission_uma_critical
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from hledac.universal.core.resource_governor import (
    UMA_STATE_CRITICAL,
    UMA_STATE_EMERGENCY,
    UMA_STATE_WARN,
    sample_uma_status,
)

logger = logging.getLogger(__name__)

# Default concurrency limits
DEFAULT_FETCH_LIMIT = 25
MODEL_LOADED_FETCH_LIMIT = 3  # F202H spec: limit=3 while model loaded

# F204J: Mission budget constants
MISSION_PEAK_RSS_GIB: float = 5.5
SIDECAR_DEFAULT_ESTIMATE_MB: int = 128
HEAVY_SIDECARS: tuple[str, ...] = ("embedding", "wayback_diff", "social_identity", "rir_correlation")
MAX_BUDGET_EVENTS: int = 100


@dataclass(frozen=True)
class SidecarAdmission:
    """F204J: Result of sidecar admission check."""
    allowed: bool
    sidecar_name: str
    reason: str
    rss_gib: float
    uma_state: str
    estimated_mb: int


@dataclass(frozen=True)
class RendererAdmission:
    """F214R: Result of renderer admission check.

    One unified answer to: can JS renderer be used right now?
    Combines model lifecycle + UMA state in one call.
    """
    allowed: bool
    reason: str
    uma_state: str
    model_loaded: bool


@dataclass(frozen=True)
class ModelAdmission:
    """F214R: Result of model load admission check.

    One unified answer to: can a new model load be initiated?
    Uses current UMA state (not model lifecycle — that's caller-provided).
    """
    allowed: bool
    reason: str
    uma_state: str
    free_uma_gib: float


@dataclass(frozen=True)
class BranchAdmission:
    """F214R: Result of branch admission check.

    Answers: can a named branch run given current memory state?
    estimated_mb is the expected RAM cost of the branch.
    """
    allowed: bool
    reason: str
    uma_state: str
    branch_concurrency: int
    estimated_mb: int


@dataclass(frozen=True)
class LaneAdmission:
    """F214R: Result of lane admission check.

    Answers: can a named lane be admitted given current memory state?
    risk_level: "low" | "medium" | "high" | "critical"
    estimated_mb: expected RAM cost of the lane.
    """
    allowed: bool
    reason: str
    uma_state: str
    risk_level: str


@dataclass(frozen=True)
class MissionBudgetSnapshot:
    """F204J: Budget snapshot for scorecard export."""
    sprint_id: str
    peak_rss_gib: float
    peak_uma_used_gib: float
    sidecars_skipped: tuple[str, ...]
    model_loaded: bool
    renderer_allowed: bool
    fetch_limit: int


@dataclass
class GovernorDecision:
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
    # F203J: Free UMA GiB hint for QuantizationSelector
    free_uma_gib: float = 0.0


@dataclass
class GovernorSnapshot:
    """Snapshot of governor internal state for dashboard rendering."""
    uma_state: str
    model_loaded: bool
    fetch_limit: int
    branch_concurrency: int
    renderer_denied_count: int
    model_denied_count: int
    system_used_gib: float
    io_only: bool
    # F203J: Free UMA GiB hint for QuantizationSelector
    free_uma_gib: float = 0.0


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

    def __init__(self) -> None:
        self._fetch_limit = DEFAULT_FETCH_LIMIT
        self._renderer_denied_count = 0
        self._model_denied_count = 0
        self._model_loaded = False
        self._lock = asyncio.Lock()
        self._uma_state = "ok"

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

        Fails soft: returns safe defaults on any error.
        """
        async with self._lock:
            free_uma_gib = 0.0
            try:
                uma = sample_uma_status()
                self._uma_state = uma.state
                # F203J: Extract free UMA GiB for QuantizationSelector
                free_uma_gib = uma.system_available_gib
            except Exception as exc:
                logger.debug("[Governor] sample_uma_status failed: %s", exc)
                self._uma_state = "ok"

            # Get model lifecycle status via canonical read-only API
            try:
                model_status = self._get_model_status()
                self._model_loaded = model_status.get("loaded", False)
            except Exception as exc:
                logger.debug("[Governor] get_model_lifecycle_status failed: %s", exc)
                self._model_loaded = False

            # Decision logic
            fetch_limit = DEFAULT_FETCH_LIMIT
            allow_renderer = True
            allow_model_load = True
            branch_concurrency = 4

            # CRITICAL/EMERGENCY memory → force low concurrency
            if self._uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
                fetch_limit = MODEL_LOADED_FETCH_LIMIT
                allow_renderer = False
                allow_model_load = False
                branch_concurrency = 1
                reason = f"UMA {self._uma_state}: safe mode"
            # Model loaded → cap fetch concurrency
            elif self._model_loaded:
                fetch_limit = MODEL_LOADED_FETCH_LIMIT
                allow_renderer = False
                allow_model_load = False  # don't stack loads
                branch_concurrency = 2
                reason = "model_loaded: reduced concurrency"
            # WARN memory → reduced concurrency
            elif self._uma_state == UMA_STATE_WARN:
                fetch_limit = max(3, DEFAULT_FETCH_LIMIT // 2)
                allow_renderer = True
                allow_model_load = True
                branch_concurrency = 3
                reason = "UMA warn: reduced concurrency"
            else:
                fetch_limit = DEFAULT_FETCH_LIMIT
                allow_renderer = True
                allow_model_load = True
                branch_concurrency = 4
                reason = "normal: full concurrency"

            return GovernorDecision(
                fetch_limit=fetch_limit,
                allow_renderer=allow_renderer,
                allow_model_load=allow_model_load,
                branch_concurrency=branch_concurrency,
                reason=reason,
                uma_state=self._uma_state,
                model_loaded=self._model_loaded,
                renderer_denied_count=self._renderer_denied_count,
                model_denied_count=self._model_denied_count,
                free_uma_gib=free_uma_gib,
            )

    def sidecar_admission(self, sidecar_name: str, estimated_mb: int = SIDECAR_DEFAULT_ESTIMATE_MB) -> SidecarAdmission:
        """
        F204J: Check if a sidecar can be admitted given current memory state.

        Returns SidecarAdmission with:
        - allowed: True if sidecar should run
        - reason: human-readable denial reason
        - rss_gib: current RSS in GiB
        - uma_state: current UMA state
        - estimated_mb: the estimate that was evaluated

        Fails soft: returns allowed=True if any check fails.
        """
        try:
            uma = sample_uma_status()
            rss_gib = uma.system_used_gib / 1024**3 if uma.system_used_gib else 0.0
            uma_state = uma.state
        except Exception as exc:
            logger.debug("[Governor] sidecar_admission sample_uma_status failed: %s", exc)
            return SidecarAdmission(
                allowed=True,
                sidecar_name=sidecar_name,
                reason="uma_check_failed_allowing",
                rss_gib=0.0,
                uma_state="unknown",
                estimated_mb=estimated_mb,
            )

        # CRITICAL/EMERGENCY → block heavy sidecars
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            if sidecar_name in HEAVY_SIDECARS:
                return SidecarAdmission(
                    allowed=False,
                    sidecar_name=sidecar_name,
                    reason=f"uma_{uma_state}_blocking_heavy_sidecar",
                    rss_gib=rss_gib,
                    uma_state=uma_state,
                    estimated_mb=estimated_mb,
                )

        # RAM guard: skip heavy sidecar if RSS > 85% high_water or > 5.0 GiB
        if sidecar_name in HEAVY_SIDECARS:
            try:
                if hasattr(uma, "high_water") and uma.high_water > 0.85:
                    return SidecarAdmission(
                        allowed=False,
                        sidecar_name=sidecar_name,
                        reason="high_water_exceeded_85pct",
                        rss_gib=rss_gib,
                        uma_state=uma_state,
                        estimated_mb=estimated_mb,
                    )
                if rss_gib > MISSION_PEAK_RSS_GIB - 0.5:  # 0.5 GiB headroom
                    return SidecarAdmission(
                        allowed=False,
                        sidecar_name=sidecar_name,
                        reason="rss_exceeds_headroom_limit",
                        rss_gib=rss_gib,
                        uma_state=uma_state,
                        estimated_mb=estimated_mb,
                    )
            except Exception:
                pass  # Fail-soft: allow sidecar

        return SidecarAdmission(
            allowed=True,
            sidecar_name=sidecar_name,
            reason="admitted",
            rss_gib=rss_gib,
            uma_state=uma_state,
            estimated_mb=estimated_mb,
        )

    def _get_model_status(self) -> dict:
        """Read-only model status from canonical lifecycle API."""
        try:
            from hledac.universal.brain.model_lifecycle import get_model_lifecycle_status
            return get_model_lifecycle_status()
        except Exception as exc:
            logger.debug("[Governor] get_model_lifecycle_status failed: %s", exc)
            return {"loaded": False, "current_model": None, "initialized": False, "last_error": None}

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
        uma_state = "ok"
        try:
            uma = sample_uma_status()
            uma_state = uma.state
        except Exception as exc:
            logger.debug("[Governor] renderer_admission sample_uma_status failed: %s", exc)
            return RendererAdmission(allowed=False, reason="uma_check_failed", uma_state="unknown", model_loaded=False)

        model_loaded = False
        try:
            model_status = self._get_model_status()
            model_loaded = model_status.get("loaded", False)
        except Exception as exc:
            logger.debug("[Governor] renderer_admission get_model_status failed: %s", exc)

        # CRITICAL/EMERGENCY → deny renderer
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            return RendererAdmission(
                allowed=False,
                reason=f"uma_{uma_state}_blocking_renderer",
                uma_state=uma_state,
                model_loaded=model_loaded,
            )

        # Model loaded → deny renderer (M1 constraint: no model + JS renderer)
        if model_loaded:
            return RendererAdmission(
                allowed=False,
                reason="model_loaded_blocking_renderer",
                uma_state=uma_state,
                model_loaded=True,
            )

        return RendererAdmission(
            allowed=True,
            reason="admitted",
            uma_state=uma_state,
            model_loaded=model_loaded,
        )

    def model_admission(self, _model_name: str = "", estimated_mb: int = 0) -> ModelAdmission:
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
        uma_state = "ok"
        free_uma_gib = 0.0
        try:
            uma = sample_uma_status()
            uma_state = uma.state
            free_uma_gib = getattr(uma, "system_available_gib", 0.0)
        except Exception as exc:
            logger.debug("[Governor] model_admission sample_uma_status failed: %s", exc)
            return ModelAdmission(allowed=False, reason="uma_check_failed", uma_state="unknown", free_uma_gib=0.0)

        # CRITICAL/EMERGENCY → deny new model load
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            return ModelAdmission(
                allowed=False,
                reason=f"uma_{uma_state}_blocking_model_load",
                uma_state=uma_state,
                free_uma_gib=free_uma_gib,
            )

        # Check estimated MB vs free UMA
        if estimated_mb > 0 and free_uma_gib > 0:
            if estimated_mb / 1024 > free_uma_gib * 0.9:
                return ModelAdmission(
                    allowed=False,
                    reason="insufficient_uma_for_model_load",
                    uma_state=uma_state,
                    free_uma_gib=free_uma_gib,
                )

        return ModelAdmission(
            allowed=True,
            reason="admitted",
            uma_state=uma_state,
            free_uma_gib=free_uma_gib,
        )

    def branch_admission(self, _branch_name: str = "", estimated_mb: int = 0) -> BranchAdmission:
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
        uma_state = "ok"
        branch_concurrency = 4
        try:
            uma = sample_uma_status()
            uma_state = uma.state
        except Exception as exc:
            logger.debug("[Governor] branch_admission sample_uma_status failed: %s", exc)
            return BranchAdmission(allowed=True, reason="uma_check_failed_allowing", uma_state="unknown", branch_concurrency=4, estimated_mb=estimated_mb)

        # CRITICAL/EMERGENCY → force minimal concurrency
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            return BranchAdmission(
                allowed=True,
                reason=f"uma_{uma_state}_reduced_concurrency",
                uma_state=uma_state,
                branch_concurrency=1,
                estimated_mb=estimated_mb,
            )

        # Model loaded → reduced concurrency (evaluated before WARN to match evaluate() priority)
        model_loaded = False
        try:
            model_status = self._get_model_status()
            model_loaded = model_status.get("loaded", False)
        except Exception:
            pass
        if model_loaded:
            return BranchAdmission(
                allowed=True,
                reason="model_loaded_reduced_concurrency",
                uma_state=uma_state,
                branch_concurrency=2,
                estimated_mb=estimated_mb,
            )

        # WARN → reduced concurrency
        if uma_state == UMA_STATE_WARN:
            branch_concurrency = 3

        return BranchAdmission(
            allowed=True,
            reason="admitted",
            uma_state=uma_state,
            branch_concurrency=branch_concurrency,
            estimated_mb=estimated_mb,
        )

    def lane_admission(self, _lane_name: str = "", risk_level: str = "medium", _estimated_mb: int = 0) -> LaneAdmission:
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
        uma_state = "ok"
        try:
            uma = sample_uma_status()
            uma_state = uma.state
        except Exception as exc:
            logger.debug("[Governor] lane_admission sample_uma_status failed: %s", exc)
            return LaneAdmission(allowed=True, reason="uma_check_failed_allowing", uma_state="unknown", risk_level=risk_level)

        # CRITICAL/EMERGENCY → block high/critical risk lanes
        if uma_state in (UMA_STATE_CRITICAL, UMA_STATE_EMERGENCY):
            if risk_level in ("high", "critical"):
                return LaneAdmission(
                    allowed=False,
                    reason=f"uma_{uma_state}_blocking_{risk_level}_lane",
                    uma_state=uma_state,
                    risk_level=risk_level,
                )

        # RAM guard for heavy lanes
        if risk_level in ("high", "critical"):
            try:
                uma = sample_uma_status()
                rss_gib = uma.system_used_gib / (1024**3) if uma.system_used_gib else 0.0
                if hasattr(uma, "high_water") and uma.high_water > 0.85:
                    return LaneAdmission(
                        allowed=False,
                        reason="high_water_exceeded_85pct",
                        uma_state=uma_state,
                        risk_level=risk_level,
                    )
                if rss_gib > MISSION_PEAK_RSS_GIB - 0.5:
                    return LaneAdmission(
                        allowed=False,
                        reason="rss_exceeds_headroom_limit",
                        uma_state=uma_state,
                        risk_level=risk_level,
                    )
            except Exception:
                pass  # Fail-soft

        return LaneAdmission(
            allowed=True,
            reason="admitted",
            uma_state=uma_state,
            risk_level=risk_level,
        )

    def snapshot(self) -> GovernorSnapshot:
        """Current state snapshot for dashboard rendering."""
        # F203J: Get free_uma_gib from live sample for snapshot
        free_uma_gib = 0.0
        try:
            uma = sample_uma_status()
            free_uma_gib = uma.system_available_gib
        except Exception:
            pass
        return GovernorSnapshot(
            uma_state=self._uma_state,
            model_loaded=self._model_loaded,
            fetch_limit=self._fetch_limit,
            branch_concurrency=4,
            renderer_denied_count=self._renderer_denied_count,
            model_denied_count=self._model_denied_count,
            system_used_gib=0.0,
            io_only=False,
            free_uma_gib=free_uma_gib,
        )

    async def apply_decision(self, decision: GovernorDecision) -> None:
        """
        Apply governor decision to runtime surfaces (advisory only, fail-soft).

        - Updates FETCH_SEMAPHORE limit
        - Tracks denied counts for telemetry
        """
        async with self._lock:
            try:
                from hledac.universal.utils.concurrency import adjust_fetch_workers
                await adjust_fetch_workers(decision.fetch_limit)
                self._fetch_limit = decision.fetch_limit
            except Exception as exc:
                logger.debug("[Governor] adjust_fetch_workers failed: %s", exc)

            if not decision.allow_renderer:
                self._renderer_denied_count += 1
            if not decision.allow_model_load:
                self._model_denied_count += 1


# Singleton instance
_governor: M1ResourceGovernor | None = None


def get_governor() -> M1ResourceGovernor:
    """Get or create the singleton M1ResourceGovernor."""
    global _governor
    if _governor is None:
        _governor = M1ResourceGovernor()
    return _governor
