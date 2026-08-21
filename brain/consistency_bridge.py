"""
META-007: Propositional Consistency Bridge
==========================================




Bridges PropositionalConsistencyVerifier with EntropyFetchBridge for the
"confident liar" detection feedback loop.

ARCHITECTURE:
    findings (from DuckDB)
        ↓
    PropositionalConsistencyBridge.check_batch(findings)
        ↓ emits PropositionalContradictionAlert
    EntropyFetchBridge.emit(alert)
        ↓ routes to
    FetchCoordinator._entropy_alert_consumer_loop()
        ↓ trigger_micro_sprint()
    Re-fetch from alternative sources

This module detects propositional contradictions that Shannon entropy cannot catch:
- Source A claims "domain X → 1.2.3.4"
- Source B claims "domain X → 5.6.7.8"
Both have high confidence (low byte entropy, high logprob) but disagree.

CONTRAST WITH ENTROPY:
    UncertaintyQuantifier: measures statistical uncertainty (byte randomness)
    PropositionalConsistencyBridge: measures logical disagreement between sources

M1 8GB: Single-pass O(N), bounded (500 findings max per batch).
"""

from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)
import os

_ENABLED = os.environ.get("HLEDAC_ENABLE_CONSISTENCY_VERIFIER", "1").lower() in ("1", "true", "yes", "on")
MAX_FINDINGS_PER_CHECK: int = 500
CONSISTENCY_SEVERITY_THRESHOLD: float = 0.6


@dataclass(slots=True)
class PropositionalContradictionAlert:
    """
    Alert emitted when propositional contradiction is detected.

    This is distinct from EntropyAlert (statistical uncertainty):
    - EntropyAlert: "This data looks random / LLM might be hallucinating"
    - PropositionalContradictionAlert: "Different sources claim different values"

    Fields:
        entity_id: Entity identifier (e.g., "example.com", "malware.exe")
        entity_type: IOC type (e.g., "domain", "ip", "hash")
        contradiction_type: Type of contradiction detected
            - "ip_resolution_conflict": Same domain → different IPs
            - "domain_ownership_conflict": Same domain → different owners
            - "hash_conflict": Same filename → different hashes
            - "temporal_inconsistency": Same source → different values over time
            - "disputed_entity": 3+ sources in 1:1:1 split
            - "suspect_source": 2/3 consensus, 1 dissenter
        severity: Severity of contradiction [0.0-1.0]
        claim_a: First claim value
        claim_b: Second claim value
        source_a: Source making claim A
        source_b: Source making claim B
        consistency_score: Entity's consistency score [0.0-1.0]
        risk_level: "low" | "medium" | "high"
        timestamp: Unix epoch when alert was created
        metadata: Additional context
    """

    entity_id: str
    entity_type: str
    contradiction_type: str
    severity: float
    claim_a: str
    claim_b: str
    source_a: str
    source_b: str
    consistency_score: float
    risk_level: str = "medium"
    timestamp: float = field(default_factory=_time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for queue serialization."""
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "contradiction_type": self.contradiction_type,
            "severity": self.severity,
            "claim_a": self.claim_a,
            "claim_b": self.claim_b,
            "source_a": self.source_a,
            "source_b": self.source_b,
            "consistency_score": self.consistency_score,
            "risk_level": self.risk_level,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "alert_type": "propositional_contradiction",
            "entropy": 0.0,
            "threshold_exceeded": CONSISTENCY_SEVERITY_THRESHOLD,
            "confidence": 1.0 - self.severity,
        }

    @classmethod
    def from_contradiction(
        cls, contradiction: dict[str, Any], consistency_score: float
    ) -> PropositionalContradictionAlert:
        """Create alert from contradiction dict returned by Rust verifier."""
        severity = contradiction.get("severity", 0.5)
        if severity >= 0.8:
            risk_level = "high"
        elif severity >= 0.6:
            risk_level = "medium"
        else:
            risk_level = "low"
        return cls(
            entity_id=contradiction.get("entity", ""),
            entity_type=contradiction.get("attribute", "unknown"),
            contradiction_type=contradiction.get("contradiction_type", "source_conflict"),
            severity=severity,
            claim_a=contradiction.get("claim_a", ""),
            claim_b=contradiction.get("claim_b", ""),
            source_a=contradiction.get("source_a", "unknown"),
            source_b=contradiction.get("source_b", "unknown"),
            consistency_score=consistency_score,
            risk_level=risk_level,
            metadata={"resolution_hint": contradiction.get("resolution_hint", "")},
        )

    @property
    def conspiracy_type(self) -> str:
        """Typo-compatible alias for contradiction_type."""
        return self.contradiction_type


@dataclass(slots=True)
class ConsistencyCheckResult:
    """
    Result of a consistency check batch.

    Fields:
        clean: Findings that passed consistency checks
        contradictory: Findings with contradictions
        disputed: Findings from disputed entities
        contradictions: All detected contradictions
        suspect_sources: Sources flagged as suspect
        entity_scores: Per-entity consistency scores
        consistency_score: Batch-level consistency score
        facts_processed: Number of facts analyzed
        contradictions_found: Number of contradictions detected
        alerts: PropositionalContradictionAlert list for severe contradictions
        check_duration_ms: Time taken for consistency check
    """

    clean: list[dict[str, Any]] = field(default_factory=list)
    contradictory: list[dict[str, Any]] = field(default_factory=list)
    disputed: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    suspect_sources: list[dict[str, Any]] = field(default_factory=list)
    entity_scores: dict[str, float] = field(default_factory=dict)
    consistency_score: float = 1.0
    facts_processed: int = 0
    contradictions_found: int = 0
    alerts: list[PropositionalContradictionAlert] = field(default_factory=list)
    check_duration_ms: float = 0.0


class PropositionalConsistencyBridge:
    """
    Bridge for propositional consistency verification.

    Connects PropositionalConsistencyVerifier (Rust) to EntropyFetchBridge
    for the "confident liar" detection feedback loop.

    Architecture:
        - Takes findings batch as input
        - Calls Rust consistency_verifier (or Python fallback)
        - Emits PropositionalContradictionAlert for severe contradictions
        - Routes alerts through EntropyFetchBridge to FetchCoordinator

    M1 8GB safety:
        - Batched processing (500 findings max per call)
        - Single-pass O(N) algorithm
        - No persistent state beyond entity_scores
        - Alerts capped at 20 per batch
    """

    __slots__ = ("_enabled", "_stats")

    def __init__(self) -> None:
        self._enabled = _ENABLED
        self._stats = {
            "checks_performed": 0,
            "total_facts_processed": 0,
            "total_contradictions_found": 0,
            "alerts_emitted": 0,
            "checks_failed": 0,
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def check_batch(self, findings: list[dict[str, Any]], emit_alerts: bool = True) -> ConsistencyCheckResult:
        """
        Check a batch of findings for propositional contradictions.

        Args:
            findings: List of finding dicts
            emit_alerts: If True, emit alerts to EntropyFetchBridge

        Returns:
            ConsistencyCheckResult with findings categorized and alerts emitted
        """
        if not self._enabled:
            return ConsistencyCheckResult()
        if not findings:
            return ConsistencyCheckResult()
        t0 = _time.monotonic()
        findings = findings[:MAX_FINDINGS_PER_CHECK]
        try:
            domain = self._get_consistency_domain()
            raw_result = domain.check_finding_consistency(findings)
            result = ConsistencyCheckResult(
                clean=raw_result.get("clean", []),
                contradictory=raw_result.get("contradictory", []),
                disputed=raw_result.get("disputed", []),
                contradictions=raw_result.get("contradictions", []),
                suspect_sources=raw_result.get("suspect_sources", []),
                entity_scores=raw_result.get("entity_scores", {}),
                consistency_score=raw_result.get("consistency_score", 1.0),
                facts_processed=raw_result.get("facts_processed", 0),
                contradictions_found=raw_result.get("contradictions_found", 0),
            )
            alerts: list[PropositionalContradictionAlert] = []
            entity_scores = result.entity_scores
            for contradiction in result.contradictions:
                if contradiction.get("severity", 0.0) >= CONSISTENCY_SEVERITY_THRESHOLD:
                    entity = contradiction.get("entity", "")
                    score = entity_scores.get(entity, 1.0)
                    alert = PropositionalContradictionAlert.from_contradiction(contradiction, score)
                    alerts.append(alert)
            result.alerts = alerts[:20]
            result.check_duration_ms = (_time.monotonic() - t0) * 1000
            if emit_alerts and result.alerts:
                await self._emit_alerts(result.alerts)
            self._stats["checks_performed"] += 1
            self._stats["total_facts_processed"] += result.facts_processed
            self._stats["total_contradictions_found"] += result.contradictions_found
            self._stats["alerts_emitted"] += len(result.alerts)
            logger.info(
                "[CONSISTENCY] Check #%d: %d findings -> %d contradictions (%d alerts emitted) in %.1fms | batch_score=%.3f",
                self._stats["checks_performed"],
                len(findings),
                result.contradictions_found,
                len(result.alerts),
                result.check_duration_ms,
                result.consistency_score,
            )
            return result
        except Exception as e:
            self._stats["checks_failed"] += 1
            logger.debug(f"[CONSISTENCY] check_batch failed (fail-soft): {e}")
            return ConsistencyCheckResult()

    async def _emit_alerts(self, alerts: list[PropositionalContradictionAlert]) -> None:
        """
        Emit alerts to EntropyFetchBridge.

        PropositionalContradictionAlert is converted to EntropyAlert format
        for compatibility with the existing EntropyFetchBridge infrastructure.
        """
        try:
            from hledac.universal.brain.uncertainty_quant import EntropyAlert, get_entropy_bridge

            bridge = get_entropy_bridge()
            if bridge is None:
                return
            for alert in alerts:
                entropy_alert = EntropyAlert(
                    entity_id=alert.entity_id,
                    entropy=alert.severity,
                    threshold_exceeded=CONSISTENCY_SEVERITY_THRESHOLD,
                    confidence=alert.consistency_score,
                    risk_level=alert.risk_level,
                    metadata={
                        "alert_type": "propositional_contradiction",
                        "contradiction_type": alert.contradiction_type,
                        "claim_a": alert.claim_a,
                        "claim_b": alert.claim_b,
                        "source_a": alert.source_a,
                        "source_b": alert.source_b,
                        "consistency_score": alert.consistency_score,
                        "resolution_hint": alert.metadata.get("resolution_hint", ""),
                    },
                )
                await bridge.emit(entropy_alert)
        except Exception as e:
            logger.debug(f"[CONSISTENCY] Failed to emit alerts to EntropyFetchBridge: {e}")

    def _get_consistency_domain(self):
        """Get consistency domain (lazy import)."""
        from hledac.universal._core.rust_backend.consistency import get_consistency_domain

        return get_consistency_domain()

    def get_stats(self) -> dict[str, Any]:
        """Return bridge statistics."""
        return {**self._stats, "enabled": self._enabled, "max_findings_per_check": MAX_FINDINGS_PER_CHECK}


_consistency_bridge: PropositionalConsistencyBridge | None = None


def get_consistency_bridge() -> PropositionalConsistencyBridge:
    """
    Get or create the global PropositionalConsistencyBridge singleton.

    Returns:
        PropositionalConsistencyBridge instance
    """
    global _consistency_bridge
    if _consistency_bridge is None:
        _consistency_bridge = PropositionalConsistencyBridge()
    return _consistency_bridge
