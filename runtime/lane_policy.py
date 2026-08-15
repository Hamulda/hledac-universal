"""
runtime/lane_policy.py — Unified Lane Policy Abstractions
==========================================================




Problem: lane_quality assessment, policy evaluation, a budget calculation
jsou rozptýleny napříč několika moduly bez shared protokolu.

Řešení: Unified LanePolicy protocol + shared domain abstractions.

Shared abstractions (dříve duplicated):
- LanePolicy protocol — kontrakt pro všechny lane policies
- QualityScore — immutable quality score s confidence
- LaneContext — runtime context pro policy evaluation
- PolicyResult — výsledek policy evaluation
- BudgetFraction — fraction of lane budget allocation

Benefit: Eliminates ~200 lines of duplication across:
- runtime/scheduler/core/types.py (LaneBudgetPool, LaneBudgetAllocation)
- runtime/opsec_policy.py (TransportPolicy, RendererPolicy)

M1 8GB constraints:
- msgspec.Struct(frozen=True, gc=False) for frozen types (~40B/instance, no GC)
- Bounded collections (MAX_BUDGET_ITEMS=512)
- Fail-safe: every method returns sensible defaults on error

Invariant: Always-on, bounded, fail-safe — no feature flags.
"""
from __future__ import annotations

import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct
from enum import Enum, auto
from typing import Protocol, runtime_checkable, Any


# ── Enums ────────────────────────────────────────────────────────────────────

class LaneDecision(Enum):
    """Canonical lane admission/activation decision."""
    ALLOW = auto()
    DENY = auto()
    DEFER = auto()
    THROTTLE = auto()


class ResearchPhase(Enum):
    """Sprint research phase for context."""
    PRELUDE = auto()
    ACTIVE = auto()
    WINDUP = auto()
    ADVISORY = auto()


# ── Frozen Domain Types ────────────────────────────────────────────────────────

@runtime_checkable
class LanePolicy(Protocol):
    """
    Protocol pro všechny lane policies.

    Implementace musí být fail-safe — vrací PolicyResult s DENY
    při jakékoliv chybě, nikdy nehazuje exception.
    """
    def evaluate(self, ctx: LaneContext) -> PolicyResult: ...
    def get_budget_fraction(self, total_budget: float) -> float: ...


class QualityScore(Struct, frozen=True):
    """
    Immutable quality score s confidence.

    Migrated from @dataclass(frozen=True, slots=True) → msgspec.Struct.
    Používá se pro lane quality assessment, source quality tracking,
    a candidate scoring.

    Invariants:
        - score: 0.0-1.0
        - confidence: 0.0-1.0
        - components: tuple pro audit trail (immutable)
    """
    score: float          # 0.0-1.0
    confidence: float    # 0.0-1.0
    components: tuple[str, ...] = ()  # pro audit trail

    @staticmethod
    def compose(*scores: QualityScore) -> QualityScore:
        """Compose multiple scores into one (weighted average)."""
        if not scores:
            return QualityScore(score=0.0, confidence=0.0)
        total_confidence = sum(s.confidence for s in scores)
        if total_confidence == 0:
            return QualityScore(score=0.5, confidence=0.0)
        weighted = sum(s.score * s.confidence for s in scores) / total_confidence
        components = tuple(c for s in scores for c in s.components)
        return QualityScore(
            score=weighted,
            confidence=min(total_confidence, 1.0),
            components=components
        )

    def is_high_quality(self, threshold: float = 0.7) -> bool:
        """True pokud score >= threshold s dostatečnou confidence."""
        return self.score >= threshold and self.confidence >= 0.5


class LaneContext(Struct, frozen=True):
    """
    Runtime context for lane policy evaluation.

    Migrated from @dataclass(frozen=True, slots=True) → msgspec.Struct.
    Všechny field jsou immutable (frozen=True) pro bezpečné sdílení
    mezi async tasky.
    """
    query: str
    phase: ResearchPhase
    uma_state: str = 'unknown'
    memory_pressure: float = 0.0  # 0.0-1.0
    active_lanes: frozenset[str] = frozenset()
    duration_s: float = 0.0
    aggressive_mode: bool = False
    has_domain: bool = False
    has_url: bool = False
    has_crypto: bool = False
    has_ip: bool = False
    is_academic: bool = False
    is_deep_osint_m1: bool = False

    def with_memory_pressure(self, pressure: float) -> LaneContext:
        """Return new context with updated memory pressure."""
        return LaneContext(
            query=self.query,
            phase=self.phase,
            uma_state=self.uma_state,
            memory_pressure=pressure,
            active_lanes=self.active_lanes,
            duration_s=self.duration_s,
            aggressive_mode=self.aggressive_mode,
            has_domain=self.has_domain,
            has_url=self.has_url,
            has_crypto=self.has_crypto,
            has_ip=self.has_ip,
            is_academic=self.is_academic,
            is_deep_osint_m1=self.is_deep_osint_m1,
        )


class PolicyResult(Struct, frozen=True):
    """
    Výsledek policy evaluation.

    Migrated from @dataclass(frozen=True, slots=True) → msgspec.Struct.
    Sjednocuje různé policy decision typy do jednoho formátu.
    """
    decision: LaneDecision
    score: QualityScore
    budget_fraction: float  # 0.0-1.0 of lane budget
    reason: str
    metadata: tuple[str, ...] = ()  # additional context tags

    @staticmethod
    def allow(score: QualityScore, budget_fraction: float, reason: str, **kwargs: Any) -> PolicyResult:
        """Factory for ALLOW decision."""
        return PolicyResult(
            decision=LaneDecision.ALLOW,
            score=score,
            budget_fraction=budget_fraction,
            reason=reason,
            metadata=tuple(kwargs.get('metadata', ()))
        )

    @staticmethod
    def deny(score: QualityScore, reason: str, **kwargs: Any) -> PolicyResult:
        """Factory for DENY decision."""
        return PolicyResult(
            decision=LaneDecision.DENY,
            score=score,
            budget_fraction=0.0,
            reason=reason,
            metadata=tuple(kwargs.get('metadata', ()))
        )

    @staticmethod
    def defer(score: QualityScore, reason: str, **kwargs: Any) -> PolicyResult:
        """Factory for DEFER decision (wait for more info)."""
        return PolicyResult(
            decision=LaneDecision.DEFER,
            score=score,
            budget_fraction=0.0,
            reason=reason,
            metadata=tuple(kwargs.get('metadata', ()))
        )

    @staticmethod
    def throttle(score: QualityScore, budget_fraction: float, reason: str, **kwargs: Any) -> PolicyResult:
        """Factory for THROTTLE decision (limited execution)."""
        return PolicyResult(
            decision=LaneDecision.THROTTLE,
            score=score,
            budget_fraction=budget_fraction,
            reason=reason,
            metadata=tuple(kwargs.get('metadata', ()))
        )

    @staticmethod
    def default_deny() -> PolicyResult:
        """Fail-safe default: deny everything on error."""
        return PolicyResult(
            decision=LaneDecision.DENY,
            score=QualityScore(score=0.0, confidence=0.0),
            budget_fraction=0.0,
            reason="policy_evaluation_error",
        )


# ── Source Tier (moved from scheduler/core/types.py) ───────────────────────────

class SourceTier(Enum):
    """Feed source priority tier."""
    SURFACE = auto()
    STRUCTURED_TI = auto()
    DEEP = auto()
    ARCHIVE = auto()
    OTHER = auto()


_TIER_ORDER: list[SourceTier] = [
    SourceTier.SURFACE,
    SourceTier.STRUCTURED_TI,
    SourceTier.DEEP,
    SourceTier.ARCHIVE,
    SourceTier.OTHER,
]

_DEFAULT_SOURCE_TIER_MAP: dict[str, SourceTier] = {
    'cisa_kev': SourceTier.STRUCTURED_TI,
    'threatfox_ioc': SourceTier.STRUCTURED_TI,
    'urlhaus_recent': SourceTier.STRUCTURED_TI,
    'feodo_ip': SourceTier.STRUCTURED_TI,
    'openphish_feed': SourceTier.STRUCTURED_TI,
}


# ── Risk Level ────────────────────────────────────────────────────────────────

# ── Re-export FeedDominanceBudget from canonical location ─────────────────────
# Canonical definition lives in runtime/acquisition/budget.py (msgspec.Struct, fail-safe)
# This re-export keeps lane_policy.py as the unified entry point for lane policies
from hledac.universal.runtime.acquisition.budget import (
from core import aclose
    FeedDominanceBudget,
)


# ── Re-exports for convenience ────────────────────────────────────────────────

__all__ = [
    # Protocol
    'LanePolicy',
    # Enums
    'LaneDecision',
    'ResearchPhase',
    'SourceTier',
    # Domain types
    'QualityScore',
    'LaneContext',
    'PolicyResult',
    # Constants (imported by scheduler/core/types.py for backwards compatibility)
    '_TIER_ORDER',
    '_DEFAULT_SOURCE_TIER_MAP',
]
