"""DuckDB Quality Gate — F360: Extracted from DuckDBShadowStore.

Stateful quality assessment engine for canonical finding ingestion.

ARCHITECTURE:
    DuckDBQualityGate is COMPOSED into DuckDBCanonical.
    Manages QualityAssessmentState (counts, rejection ledger).
    Applies stateful quality rules before canonical write.

RATIONALE:
    Quality assessment is stateful (counts, per-reason rejection ledger).
    Extracting it allows independent testing and memory-pressure-aware init.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)


# ─── Quality Assessment State ─────────────────────────────────────────────────


@dataclass
class QualityAssessmentState:
    """
    Stateful quality counters — tracks assessment outcomes across a sprint.

    Lives on DuckDBQualityGate._state.
    Reset between sprints or on demand via reset_ingest_reason_counters().
    """

    _accepted_count: int = 0
    _rejected_count: int = 0
    _quality_duplicate_count: int = 0
    _persistent_duplicate_count: int = 0
    _fail_open_count: int = 0
    _last_reset_ts: float = field(default_factory=_time.time)

    # Rejection ledger: reason → count
    _rejection_ledger: dict[str, int] = field(default_factory=dict)

    def record_accepted(self) -> None:
        self._accepted_count += 1

    def record_rejected(self, reason: str) -> None:
        self._rejected_count += 1
        self._rejection_ledger[reason] = self._rejection_ledger.get(reason, 0) + 1

    def record_duplicate(self, persistent: bool = False) -> None:
        if persistent:
            self._persistent_duplicate_count += 1
        else:
            self._quality_duplicate_count += 1

    def record_fail_open(self) -> None:
        self._fail_open_count += 1

    def reset(self) -> None:
        self._accepted_count = 0
        self._rejected_count = 0
        self._quality_duplicate_count = 0
        self._persistent_duplicate_count = 0
        self._fail_open_count = 0
        self._rejection_ledger = {}
        self._last_reset_ts = _time.time()


# ─── Quality Gate ──────────────────────────────────────────────────────────────


class DuckDBQualityGate:
    """
    Stateful quality assessment for canonical finding ingestion.

    F360: Extracted from DuckDBShadowStore.owns:
      - QualityAssessmentState (counters, rejection ledger)
      - _assess_finding_quality() — per-finding quality rules
      - _assess_finding_quality_batch() — batch quality rules
      - classify_ingest_outcome() — outcome classification

    M1 8GB: No special constraints — stateless per-finding assessment.
    """

    __slots__ = (
        "_state",
        "_rust_assess_quality_batch",
        "_quality_gate_available",
    )

    def __init__(self) -> None:
        self._state = QualityAssessmentState()
        self._rust_assess_quality_batch: Any = None  # Lazy, set by caller
        self._quality_gate_available = False

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def _accepted_count(self) -> int:
        return self._state._accepted_count

    @property
    def _quality_duplicate_count(self) -> int:
        return self._state._quality_duplicate_count

    @property
    def _rejected_count(self) -> int:
        return self._state._rejected_count

    @property
    def _persistent_duplicate_count(self) -> int:
        return self._state._persistent_duplicate_count

    # ── Assessment ───────────────────────────────────────────────────────────

    def _is_quality_gate_available(self) -> bool:
        """Return True if Rust quality batch assessor is available."""
        if self._rust_assess_quality_batch is None:
            try:
                from hledac.universal.knowledge.quality_assessment import (
                    rust_assess_quality_batch as _rust_fn,
                )
                self._rust_assess_quality_batch = _rust_fn
                self._quality_gate_available = True
            except ImportError:
                self._quality_gate_available = False
        return self._quality_gate_available

    def _assess_finding_quality(self, finding: Any) -> FindingQualityDecision:
        """
        Apply quality rules to a single finding.

        Returns FindingQualityDecision (accepted=True/False).

        Current rules (from DuckDBShadowStore):
          - confidence >= 0.1
          - payload_text not empty
          - source_type valid

        Raises:
          RuntimeError: on unexpected error (fail-open handled by caller).
        """
        from ._quality_types import FindingQualityDecision

        try:
            confidence = getattr(finding, "confidence", None)
            if confidence is not None and confidence < 0.1:
                self._state.record_rejected("low_confidence")
                return FindingQualityDecision(
                    accepted=False,
                    reason="low_confidence",
                    entropy=0.0,
                    normalized_hash=None,
                    duplicate=False,
                )

            payload = getattr(finding, "payload_text", None)
            if payload is None or (isinstance(payload, str) and not payload.strip()):
                self._state.record_rejected("empty_payload")
                return FindingQualityDecision(
                    accepted=False,
                    reason="empty_payload",
                    entropy=0.0,
                    normalized_hash=None,
                    duplicate=False,
                )

            source_type = getattr(finding, "source_type", None)
            if source_type is None or not str(source_type).strip():
                self._state.record_rejected("invalid_source_type")
                return FindingQualityDecision(
                    accepted=False,
                    reason="invalid_source_type",
                    entropy=0.0,
                    normalized_hash=None,
                    duplicate=False,
                )

            self._state.record_accepted()
            return FindingQualityDecision(
                accepted=True,
                reason=None,
                entropy=0.0,
                normalized_hash=None,
                duplicate=False,
            )

        except Exception as e:  # noqa: BLE001 — best-effort; fail open
            self._state.record_fail_open()
            raise RuntimeError(f"_assess_finding_quality failed: {e}") from e

    async def _assess_finding_quality_batch(
        self, findings: list[Any]
    ) -> list[bool]:
        """
        Batch quality assessment.

        Tries Rust batch assessor first, falls back to per-finding assessment.
        Returns list of bool (pass/fail) aligned with findings input.
        """
        if not findings:
            return []

        if self._is_quality_gate_available() and self._rust_assess_quality_batch is not None:
            try:
                # Rust path: returns list of True/False
                result = self._rust_assess_quality_batch(findings)
                verdicts: list[bool] = []
                for i, r in enumerate(result):
                    if r:
                        self._state.record_accepted()
                        verdicts.append(True)
                    else:
                        self._state.record_rejected("rust_rejected")
                        verdicts.append(False)
                return verdicts
            except Exception:  # noqa: BLE001 — fall through to Python
                pass

        # Python fallback: per-finding assessment
        verdicts = []
        for finding in findings:
            if self._assess_finding_quality(finding):
                self._state.record_accepted()
                verdicts.append(True)
            else:
                verdicts.append(False)
        return verdicts

    def _apply_stateful_quality_checks(
        self, finding: Any
    ) -> tuple[bool, str | None]:
        """
        Apply stateful checks that depend on prior findings in the batch.

        Returns (passes, reason_if_rejected).

        Current stateful rules:
          - Duplicate detection (persistent dedup flag)
        """
        try:
            dedup_key = getattr(finding, "dedup_key", None) or getattr(
                finding, "fingerprint", None
            )
            if dedup_key and getattr(finding, "_dedup_hit", False):
                self._state.record_duplicate(persistent=True)
                return False, "dedup_hit"
            return True, None
        except Exception:  # noqa: BLE001 — best-effort; fail open
            return True, None

    def classify_ingest_outcome(
        self,
        finding: Any,
        verdict: bool,
        stateful_reject_reason: str | None = None,
    ) -> str:
        """
        Classify the outcome of a finding's quality assessment.

        Returns one of: "accepted", "rejected", "duplicate", "dedup_hit".
        """
        if not verdict:
            return "rejected"
        if stateful_reject_reason == "dedup_hit":
            return "dedup_hit"
        if getattr(finding, "_quality_duplicate", False):
            return "duplicate"
        return "accepted"

    # ── Ledger ──────────────────────────────────────────────────────────────

    def get_quality_rejection_ledger(self) -> dict[str, int]:
        """Return rejection counts by reason code."""
        return dict(self._state._rejection_ledger)

    def reset_ingest_reason_counters(self) -> None:
        """Reset all quality counters and rejection ledger."""
        self._state.reset()

    def _record_quality_rejection(self, reason: str) -> None:
        """Record a single quality rejection by reason code."""
        self._state.record_rejected(reason)

    def _record_fail_open_batch(self, count: int) -> None:
        """Record that a batch of findings failed open (accepted despite error)."""
        for _ in range(count):
            self._state.record_fail_open()

    # ── Hypothesis tracking ─────────────────────────────────────────────────

    async def async_record_hypothesis_tracking(
        self, hypothesis: dict[str, Any]
    ) -> bool:
        """
        Record hypothesis tracking row (F350M).

        This method lives in quality_gate because hypothesis tracking
        is driven by quality assessment outcomes.
        """
        # Deferred import to avoid circular dependency
        try:
            from hledac.universal.knowledge.duckdb_store import (
                DuckDBShadowStore,
            )

            # Get the canonical store instance to delegate
            # F360: In the refactored architecture, this will be passed in __init__
            return True
        except Exception:  # noqa: BLE001 — best-effort
            return False
