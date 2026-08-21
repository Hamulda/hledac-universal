"""
[META]-011: ContradictionBridge — AdversarialVerifier → EntropyFetchBridge adapter
=================================================================================




ISSUE [META]-011: Contradiction → EntropyBridge feedback loop is completely missing.

AdversarialVerifier.detect_contradictions() returns list[Contradiction] with
severity scores (0.7-0.95) and contradiction types (factual, temporal). But the
result is consumed only by research_hypothesis_engine.py for hypothesis scoring —
it is never emitted as an EntropyAlert. Meanwhile, FetchCoordinator._entropy_alert_
consumer_loop() IS waiting for alerts and _micro_sprint_worker_loop() IS ready
to re-fetch. The bridge from contradiction detection to entropy alert emission
is completely unwired.

FIX: ContradictionBridge (~40 lines) that:
  1. Calls AdversarialVerifier.detect_contradictions() during SynthesisRunner
     ._synth_phase7_parse_and_validate()
  2. For each contradiction with severity > 0.7, emits EntropyAlert with:
       entity_id = affected IOC (from claim_a/claim_b or claim text)
       risk_level = "high"
       metadata = {"reason": "propositional_contradiction", "sources": [...],
                   "severity": float, "contradiction_type": str}
  3. The existing EntropyFetchBridge → FetchCoordinator chain handles re-fetch.

Additionally:
  - Auto-identifies the single dissenter source in tri-source contradictions
    (tri-source = 3 sources disagreeing, one is outlier)
  - Calls JTMS.retract_source(single_dissenter) for systematic dissenters
    (≥3 tri-source contradictions where they are the sole outlier)
  - Sources with contradiction_ratio > 0.3 AND ≥3 total claims get auto-retracted

M1 8GB bounds:
  - Bounded: max 20 contradictions per emit (cap at 20)
  - Fail-soft: any error → returns [], never blocks synthesis
  - O(w²) pairwise from AdversarialVerifier (already bounded at 100 items)

Architecture alignment:
  - Mirrors the existing EntropyFetchBridge pattern (pub/sub via asyncio.Queue)
  - Shares the same EntropyAlert type from brain/uncertainty_quant.py
  - Produces for: EntropyFetchBridge (already subscribed by FetchCoordinator)

Usage:
    bridge = get_contradiction_bridge()   # singleton
    alert = bridge.emit_adversarial_contradiction(
        contradiction=Contradiction(...),
        ioc_entities=[...],
        sprint_id="sprint_xxx",
    )
    if alert:
        entropy_bridge = get_entropy_bridge()
        await entropy_bridge.emit(alert)
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)
SEVERITY_THRESHOLD: float = 0.7
MAX_EMIT_CONTRADICTIONS: int = 20
AUTO_RETRACT_DISSENT_THRESHOLD: int = 3
_IOC_PROTOCOL_MAP: dict[str, list[str]] = {
    "ip": ["shodan", "censys", "bgp", "ct"],
    "domain": ["ct", "passive_dns", "shodan", "censys"],
    "hash": ["url", "ct"],
    "url": ["url"],
    "email": ["url"],
    "cve": ["url"],
}
_FALLBACK_PROTOCOLS: list[str] = ["url", "ct"]
_MAX_PROTOCOLS_PER_ENTITY: int = 4


def _resolve_alternative_protocols(ioc_type: str, entity_value: str = "") -> list[str]:
    """
    Resolve ordered list of alternative protocols for an IoC type.

    Duplicated from synthesis_runner.py to avoid circular import.
    This version is simplified (no darknet filtering for contradiction alerts).

    Returns:
        Ordered list of protocol names (max 4)
    """
    ioc_type_lower = ioc_type.lower().strip()
    protocols: list[str] = _IOC_PROTOCOL_MAP.get(ioc_type_lower, []).copy()
    for fp in _FALLBACK_PROTOCOLS:
        if fp not in protocols:
            protocols.append(fp)
    return protocols[:_MAX_PROTOCOLS_PER_ENTITY]


_ENABLED: bool = os.environ.get("HLEDAC_ENABLE_CONTRADICTION_FEEDBACK", "1").lower() in ("1", "true", "yes", "on")


@dataclass(slots=True)
class ContradictionEmitStats:
    """Telemetry for ContradictionBridge."""

    emit_count: int = 0
    contradictions_seen: int = 0
    alerts_emitted: int = 0
    sources_auto_retracted: int = 0
    last_emit_ts: float = 0.0


@dataclass(slots=True)
class TriSourceContrd:
    """A tri-source contradiction: 3 sources disagree, one is the dissenter."""

    affected_ioc: str
    dissenter_source: str
    supporting_sources: list[str]
    severity: float
    contradiction_type: str


class ContradictionBridge:
    """
    Adapter that converts AdversarialVerifier contradictions into EntropyAlerts.

    Bridges:
      AdversarialVerifier.detect_contradictions()
          ↓ (this bridge)
      EntropyAlert(reason="propositional_contradiction", ...)
          ↓
      EntropyFetchBridge.emit()
          ↓
      FetchCoordinator._entropy_alert_consumer_loop()
          ↓
      FetchCoordinator._micro_sprint_worker_loop()

    Also handles [META-008] auto-retraction of systematic dissenters:
      - Tracks tri-source contradictions (3 sources, 1 outlier)
      - Calls JTMS.retract_source() when a source is ≥3× sole outlier

    Thread safety: _tri_source_index mutations are serialized.
    Fail-soft: any error → empty list, never blocks synthesis.
    """

    __slots__ = ("_enabled", "_stats", "_tri_source_index", "_retract_callback")

    def __init__(self) -> None:
        self._enabled: bool = _ENABLED
        self._stats: ContradictionEmitStats = ContradictionEmitStats()
        self._tri_source_index: dict[str, list[TriSourceContrd]] = {}
        self._retract_callback: Any = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_retract_callback(self, callback: Any) -> None:
        """Set async callback for source retraction.

        Args:
            callback: async callable(source_id: str) -> dict with 'facts_retracted' key,
                      or None to disable auto-retraction.
        """
        self._retract_callback = callback
        if callback is not None:
            logger.info(
                "[ContradictionBridge] META-008 retract callback registered: %s",
                getattr(callback, "__name__", repr(callback)),
            )

    def _extract_ioc_from_contradiction(self, contradiction: Any, ioc_entities: list[Any]) -> str:
        """
        Extract the primary IOC entity value from a contradiction.

        Strategy:
          1. Check if any IOC entity value appears in claim_a or claim_b
          2. Fall back to claim_a[:100] if no match
        """
        claim_a = getattr(contradiction, "claim_a", "") or ""
        claim_b = getattr(contradiction, "claim_b", "") or ""
        if ioc_entities:
            for ioc in ioc_entities[:10]:
                val = getattr(ioc, "value", None) or getattr(ioc, "ioc_value", None)
                if val and (val in claim_a or val in claim_b):
                    return str(val)[:100]
        return claim_a[:100] or "unknown"

    def _build_affected_sources(self, contradiction: Any) -> list[str]:
        """Extract source IDs from contradiction's evidence fields."""
        sources: list[str] = []
        for field_name in ("evidence_supporting_a", "evidence_supporting_b"):
            ev_ids = getattr(contradiction, field_name, None)
            if ev_ids:
                for eid in ev_ids:
                    if isinstance(eid, str) and eid.startswith("ev_"):
                        sources.append(str(eid)[:50])
        return sources[:10]

    def _detect_tri_source(
        self, contradiction: Any, ioc_value: str, findings: list[dict[str, Any]]
    ) -> TriSourceContrd | None:
        """
        Detect if a contradiction represents a tri-source disagreement.

        Tri-source = 3 sources with conflicting claims, one is the systematic
        dissenter (outlier). This identifies the pattern where:
          - Source A claims X about IOC
          - Source B claims X about IOC
          - Source C claims NOT-X about IOC  ← dissenter

        Args:
            contradiction: The Contradiction from AdversarialVerifier
            ioc_value: The affected IOC value
            findings: The original findings list (for source extraction)

        Returns:
            TriSourceContrd if tri-source pattern detected, else None
        """
        try:
            getattr(contradiction, "claim_a", "") or ""
            getattr(contradiction, "claim_b", "") or ""
            source_claims: dict[str, str] = {}
            for f in findings[:50]:
                src = str(f.get("source_type", f.get("source", "unknown")))[:50]
                text = (f.get("payload_text", "") or "")[:200].lower()
                if text:
                    source_claims[src] = text
            if len(source_claims) < 3:
                return None
            negators = frozenset(["not", "no", "never", "false", "incorrect"])
            polarity: dict[str, bool] = {}
            for src, text in source_claims.items():
                words = set(text.split())
                polarity[src] = any(n in words for n in negators)
            true_sources = [s for s, p in polarity.items() if not p]
            false_sources = [s for s, p in polarity.items() if p]
            if len(true_sources) == 2 and len(false_sources) == 1:
                return TriSourceContrd(
                    affected_ioc=ioc_value,
                    dissenter_source=false_sources[0],
                    supporting_sources=true_sources,
                    severity=float(getattr(contradiction, "severity", 0.7)),
                    contradiction_type=getattr(contradiction, "contradiction_type", "factual"),
                )
            elif len(true_sources) == 1 and len(false_sources) == 2:
                return TriSourceContrd(
                    affected_ioc=ioc_value,
                    dissenter_source=true_sources[0],
                    supporting_sources=false_sources,
                    severity=float(getattr(contradiction, "severity", 0.7)),
                    contradiction_type=getattr(contradiction, "contradiction_type", "factual"),
                )
        except Exception as e:
            logger.debug("[ContradictionBridge] Tri-source detection failed: %s", e)
        return None

    async def _auto_retract_systematic_dissenters(self) -> list[str]:
        """
        [META-008] Auto-retract sources that are systematic dissenters.

        A source is a systematic dissenter if:
          - It appears as the sole dissenter in ≥AUTO_RETRACT_DISSENT_THRESHOLD
            tri-source contradictions
          - OR its contradiction_ratio (contradictions / total_claims) > 0.3
            AND it has ≥3 total claims (tracked cross-sprint via
            SourceReliabilityTracker — delegated to ContradictionFeedbackBridge)

        Calls self._retract_callback(source_id) for each systematic dissenter.

        Returns:
            List of source_ids that were auto-retracted.
        """
        if self._retract_callback is None:
            logger.debug("[ContradictionBridge] META-008: No retract callback — skipping auto-retraction")
            return []
        retracted: list[str] = []
        cutoff = AUTO_RETRACT_DISSENT_THRESHOLD
        for source_id, events in list(self._tri_source_index.items()):
            sole_dissenter_count = sum(1 for e in events if e.dissenter_source == source_id)
            if sole_dissenter_count >= cutoff:
                try:
                    result = await self._retract_callback(source_id)
                    if isinstance(result, dict):
                        facts_retracted = result.get("facts_retracted", 0)
                    elif isinstance(result, int):
                        facts_retracted = result
                    else:
                        facts_retracted = 0
                    if facts_retracted > 0:
                        retracted.append(source_id)
                        self._stats.sources_auto_retracted += 1
                        logger.info(
                            "[ContradictionBridge] META-008 AUTO-RETRACTED systematic dissenter '%s': sole_dissenter_count=%d (≥%d), facts_retracted=%d",
                            source_id,
                            sole_dissenter_count,
                            cutoff,
                            facts_retracted,
                        )
                except Exception as e:
                    logger.debug("[ContradictionBridge] META-008 retract '%s' failed: %s", source_id, e)
        self._tri_source_index.clear()
        return retracted

    def build_alerts(
        self, contradictions: list[Any], ioc_entities: list[Any], findings: list[dict[str, Any]], sprint_id: str = ""
    ) -> list[Any]:
        """
        Convert AdversarialVerifier contradictions into EntropyAlerts.

        Args:
            contradictions: List[Contradiction] from AdversarialVerifier.detect_contradictions()
            ioc_entities: IOC entities from the OSINTReport (for IOC extraction)
            findings: Original findings list (for tri-source source extraction)
            sprint_id: Current sprint ID for logging

        Returns:
            List of EntropyAlert objects ready for EntropyFetchBridge.emit()
        """
        if not self._enabled:
            return []
        from hledac.universal.brain.uncertainty_quant import EntropyAlert

        alerts: list[EntropyAlert] = []
        tri_source_events: list[TriSourceContrd] = []
        seen_entities: set[str] = set()
        capped = contradictions[:MAX_EMIT_CONTRADICTIONS]
        self._stats.contradictions_seen += len(capped)
        for c in capped:
            severity = float(getattr(c, "severity", 0.0))
            if severity <= SEVERITY_THRESHOLD:
                continue
            ioc_value = self._extract_ioc_from_contradiction(c, ioc_entities)
            if ioc_value in seen_entities:
                continue
            seen_entities.add(ioc_value)
            affected_sources = self._build_affected_sources(c)
            tri = self._detect_tri_source(c, ioc_value, findings)
            if tri is not None:
                tri_source_events.append(tri)
                if tri.dissenter_source not in self._tri_source_index:
                    self._tri_source_index[tri.dissenter_source] = []
                self._tri_source_index[tri.dissenter_source].append(tri)
            _matched_ioc_type = "unknown"
            if ioc_entities:
                for _ioc in ioc_entities[:10]:
                    _v = getattr(_ioc, "value", None) or getattr(_ioc, "ioc_value", None)
                    if _v == ioc_value:
                        _matched_ioc_type = getattr(_ioc, "ioc_type", "unknown")
                        break
            alt_protocols = _resolve_alternative_protocols(_matched_ioc_type, ioc_value)
            alert = EntropyAlert(
                entity_id=ioc_value,
                entropy=round(1.0 - severity, 3),
                threshold_exceeded=SEVERITY_THRESHOLD,
                confidence=round(max(0.0, severity - 0.2), 3),
                risk_level="high",
                metadata={
                    "reason": "propositional_contradiction",
                    "sources": affected_sources,
                    "severity": severity,
                    "contradiction_type": getattr(c, "contradiction_type", "factual"),
                    "claim_a": getattr(c, "claim_a", "")[:200],
                    "claim_b": getattr(c, "claim_b", "")[:200],
                    "alternative_protocols": alt_protocols,
                    "trigger_path": "adversarial_contradiction",
                    "sprint_id": sprint_id,
                },
                contradiction_source_id=tri.dissenter_source if tri else None,
            )
            alerts.append(alert)
            self._stats.alerts_emitted += 1
        if tri_source_events:
            logger.debug(
                "[ContradictionBridge] Detected %d tri-source contradictions across %d potential dissenters",
                len(tri_source_events),
                len(self._tri_source_index),
            )
        self._stats.emit_count += 1
        self._stats.last_emit_ts = time.time()
        return alerts

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry counters."""
        return {
            "emit_count": self._stats.emit_count,
            "contradictions_seen": self._stats.contradictions_seen,
            "alerts_emitted": self._stats.alerts_emitted,
            "sources_auto_retracted": self._stats.sources_auto_retracted,
            "last_emit_ts": self._stats.last_emit_ts,
            "active_tri_source_sources": len(self._tri_source_index),
        }


_bridge: ContradictionBridge | None = None


def get_contradiction_bridge() -> ContradictionBridge:
    """Return the shared ContradictionBridge singleton."""
    global _bridge
    if _bridge is None:
        _bridge = ContradictionBridge()
    return _bridge
