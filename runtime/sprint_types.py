"""
Sprint Types Module - Extracted from sprint_scheduler.py (Tier 3 Sprint 2)

Auto-extracted standalone types, dataclasses, enums, and functions
that do not depend on SprintScheduler instance state.

MIGRATION STATUS:
- Phase 1: core/types.py (SourceTier, CTLossStage, LaneBudgetPool, etc.) ✓
- Phase 2: This module - remaining standalone items
- Phase 3: Lane methods extraction (deferred - SprintScheduler.__slots__ dependency)

CONTENTS:
- Result dataclasses: SprintResult, FeedSprintResult, PublicSprintResult, etc.
- Path utilities: _get_dedup_lmdb_path, _get_forensics_lmdb_path, _get_multimodal_lmdb_path
- Import helpers: _import_live_feed_pipeline, _import_live_public_pipeline, etc.
- GC callbacks: _gc_sprint_callback, _gc_sprint_sentinel
"""
from __future__ import annotations


import logging
from pathlib import Path
from typing import Any

# ── Re-export from scheduler/core/types.py for convenience ─────────────────────
from hledac.universal.runtime.scheduler.core.types import (
    LaneBudgetAllocation,
    LaneBudgetPool,
    SourceTier,
)

# ── LMDB Names (must be defined before path functions) ────────────────────────

_DEDUP_LMDB_NAME = "sprint_dedup.lmdb"
_FORENSICS_LMDB_NAME = "forensics_enrichment.lmdb"
_MULTIMODAL_LMDB_NAME = "multimodal_enrichment.lmdb"

# ── GC Callbacks ──────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)


def _gc_sprint_callback(phase: str, info: dict) -> None:
    """GC callback for sprint memory tracking."""
    # These are registered with gc.callback to track phase transitions
    # Actual implementation does nothing - gc module calls this on each collection
    pass  # pragma: no cover


def _gc_sprint_sentinel(_phase: object, _info: object) -> None:
    """Sentinel GC callback that does nothing."""
    pass  # pragma: no cover


# ── Path Utilities ────────────────────────────────────────────────────────────


def _get_dedup_lmdb_path() -> Path:
    """Get LMDB path for deduplication store."""
    from hledac.universal.paths import LMDB_ROOT

    return LMDB_ROOT / _DEDUP_LMDB_NAME


def _get_forensics_lmdb_path() -> Path:
    """Get LMDB path for forensics store."""
    from hledac.universal.paths import LMDB_ROOT

    return LMDB_ROOT / _FORENSICS_LMDB_NAME


def _get_multimodal_lmdb_path() -> Path:
    """Get LMDB path for multimodal store."""
    from hledac.universal.paths import LMDB_ROOT

    return LMDB_ROOT / _MULTIMODAL_LMDB_NAME


# ── Import Helpers (Lazy Loading) ─────────────────────────────────────────────

_live_feed_pipeline = None
_live_public_pipeline = None
_export_module = None
_correlate_findings = None
_hypothesis_engine = None


def _import_live_feed_pipeline():
    """Lazy import of live_feed_pipeline to avoid circular imports."""
    global _live_feed_pipeline
    if _live_feed_pipeline is None:
        from hledac.universal.pipeline import live_feed_pipeline

        _live_feed_pipeline = live_feed_pipeline
    return _live_feed_pipeline


def _import_live_public_pipeline():
    """Lazy import of live_public_pipeline to avoid circular imports."""
    global _live_public_pipeline
    if _live_public_pipeline is None:
        from hledac.universal.pipeline import live_public_pipeline

        _live_public_pipeline = live_public_pipeline
    return _live_public_pipeline


def _import_exporters():
    """Lazy import of exporters to avoid circular imports."""
    global _export_module
    if _export_module is None:
        from hledac.universal import export

        _export_module = export
    return _export_module


def _import_correlate_findings():
    """Lazy import of correlate_findings to avoid circular imports."""
    global _correlate_findings
    if _correlate_findings is None:
        from hledac.universal.knowledge import correlate_findings

        _correlate_findings = correlate_findings
    return _correlate_findings


def _import_hypothesis_engine():
    """Lazy import of hypothesis_engine to avoid circular imports."""
    global _hypothesis_engine
    if _hypothesis_engine is None:
        from hledac.universal.brain import hypothesis_engine

        _hypothesis_engine = hypothesis_engine
    return _hypothesis_engine


# ── Sentinel ──────────────────────────────────────────────────────────────────


class _Sentinel:
    """Sentinel value for unset attributes."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Sentinel()


def _safe_getattr(obj: Any, attr: str, default: Any = _UNSET) -> Any:
    """Safe getattr that returns default if attribute not found."""
    try:
        return object.__getattribute__(obj, attr)
    except AttributeError:
        if default is _UNSET:
            raise
        return default


# ── Result Dataclasses ────────────────────────────────────────────────────────
# These are extracted from sprint_scheduler.py for modular organization


class EarlyExitClass:
    """Classification of why a sprint exited early."""

    TIMEOUT = "timeout"
    EMPTY_CYCLES = "empty_cycles"
    WINDUP = "windup"
    ERROR = "error"
    ABORT = "abort"
    UNKNOWN = "unknown"


class FeedDominanceGuardResult:
    """Result of feed dominance guard computation."""

    __slots__ = ("_suppressed", "_reason", "_feed_ratio", "_nonfeed_lanes_terminal")

    def __init__(
        self,
        suppressed: bool,
        reason: str,
        feed_ratio: float,
        nonfeed_lanes_terminal: bool,
    ):
        self._suppressed = suppressed
        self._reason = reason
        self._feed_ratio = feed_ratio
        self._nonfeed_lanes_terminal = nonfeed_lanes_terminal

    @property
    def suppressed(self) -> bool:
        return self._suppressed

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def feed_ratio(self) -> float:
        return self._feed_ratio

    @property
    def nonfeed_lanes_terminal(self) -> bool:
        return self._nonfeed_lanes_terminal

    def __repr__(self) -> str:
        return f"FeedDominanceGuardResult(suppressed={self._suppressed}, reason={self._reason!r})"


# ── Source Economics ───────────────────────────────────────────────────────────


class SourceEconomics:
    """Track economics of a source (quality, latency, hit rate)."""

    __slots__ = (
        "_quality",
        "_latency_ema",
        "_hit_count",
        "_miss_count",
        "_last_seen",
    )

    def __init__(
        self,
        quality: float = 0.5,
        latency_ema: float = 0.0,
        hit_count: int = 0,
        miss_count: int = 0,
    ):
        self._quality = quality
        self._latency_ema = latency_ema
        self._hit_count = hit_count
        self._miss_count = miss_count
        self._last_seen: float | None = None

    @property
    def quality(self) -> float:
        return self._quality

    @property
    def latency_ema(self) -> float:
        return self._latency_ema

    @property
    def hit_count(self) -> int:
        return self._hit_count

    @property
    def miss_count(self) -> int:
        return self._miss_count

    def record_hit(self, latency: float) -> None:
        """Record a source hit with latency."""
        self._hit_count += 1
        self._last_seen = latency
        # Update latency EMA
        alpha = 0.3
        if self._latency_ema == 0:
            self._latency_ema = latency
        else:
            self._latency_ema = alpha * latency + (1 - alpha) * self._latency_ema

    def record_miss(self) -> None:
        """Record a source miss."""
        self._miss_count += 1

    def quality_score(self) -> float:
        """Compute quality score from hit/miss ratio."""
        total = self._hit_count + self._miss_count
        if total == 0:
            return 0.5
        return self._hit_count / total


# ── Re-export from scheduler/core/types.py ─────────────────────────────────────

__all__ = [
    # From scheduler/core/types.py
    "LaneBudgetAllocation",
    "LaneBudgetPool",
    "SourceTier",
    # Local
    "EarlyExitClass",
    "FeedDominanceGuardResult",
    "SourceEconomics",
    "_DEDUP_LMDB_NAME",
    "_FORENSICS_LMDB_NAME",
    "_MULTIMODAL_LMDB_NAME",
    "_gc_sprint_callback",
    "_gc_sprint_sentinel",
    "_get_dedup_lmdb_path",
    "_get_forensics_lmdb_path",
    "_get_multimodal_lmdb_path",
    "_import_correlate_findings",
    "_import_exporters",
    "_import_hypothesis_engine",
    "_import_live_feed_pipeline",
    "_import_live_public_pipeline",
    "_safe_getattr",
    "_Sentinel",
    "_UNSET",
]
