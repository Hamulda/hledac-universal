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

Invariant table:
  Invariant                          | Test file:method
  ─────────────────────────────────────────────────────────────────────
  model_loaded path → fetch_limit=3  | test_m1_resource_governor.py:test_governor_sets_fetch_limit_3_when_model_loaded
  model_unloaded path → fetch_limit=25| test_m1_resource_governor.py:test_governor_restores_fetch_limit_25_when_model_unloaded
  no_model_plus_renderer_concurrently| test_m1_resource_governor.py:test_no_renderer_when_model_loaded
  advisory_only_fails_soft           | test_m1_resource_governor.py:test_advisory_fails_soft
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from hledac.universal.core.resource_governor import (
    sample_uma_status,
    UMA_STATE_CRITICAL,
    UMA_STATE_EMERGENCY,
    UMA_STATE_WARN,
)

logger = logging.getLogger(__name__)

# Default concurrency limits
DEFAULT_FETCH_LIMIT = 25
MODEL_LOADED_FETCH_LIMIT = 3  # F202H spec: limit=3 while model loaded


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
                system_used_gib = uma.system_used_gib
                # F203J: Extract free UMA GiB for QuantizationSelector
                free_uma_gib = uma.system_available_gib
            except Exception as exc:
                logger.debug("[Governor] sample_uma_status failed: %s", exc)
                self._uma_state = "ok"
                system_used_gib = 0.0

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
        """
        Evaluate governor decisions for the current cycle.

        Returns GovernorDecision with:
        - fetch_limit: new FETCH_SEMAPHORE limit
        - allow_renderer: True if JS renderer may be used
        - allow_model_load: True if model load is permitted
        - branch_concurrency: recommended branch parallelism
        - reason: human-readable decision rationale

        Fails soft: returns safe defaults on any error.
        """
        async with self._lock:
            try:
                uma = sample_uma_status()
                self._uma_state = uma.state
                system_used_gib = uma.system_used_gib
            except Exception as exc:
                logger.debug("[Governor] sample_uma_status failed: %s", exc)
                self._uma_state = "ok"
                system_used_gib = 0.0

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
            )

    def _get_model_status(self) -> dict:
        """Read-only model status from canonical lifecycle API."""
        try:
            from hledac.universal.brain.model_lifecycle import get_model_lifecycle_status
            return get_model_lifecycle_status()
        except Exception as exc:
            logger.debug("[Governor] get_model_lifecycle_status failed: %s", exc)
            return {"loaded": False, "current_model": None, "initialized": False, "last_error": None}

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
_governor: Optional[M1ResourceGovernor] = None


def get_governor() -> M1ResourceGovernor:
    """Get or create the singleton M1ResourceGovernor."""
    global _governor
    if _governor is None:
        _governor = M1ResourceGovernor()
    return _governor
