"""
META-007 + META-008: Contradiction Feedback Bridge + Auto-Retraction
====================================================================





Closed-loop contradiction detection -> re-fetch gating + auto-retraction.
Aggregates results from all 4+ contradiction detection engines and feeds
them back into the sprint pipeline so contradictions trigger corrective action.

ROLE: Quality-gate feedback loop — detects when independent sources
disagree about an entity and triggers (a) re-fetch from alternative sources
and (b) [META-008] automatic source retraction via JTMS.

ARCHITECTURE:
  1. WinddownOrchestrator calls run_contradiction_audit(findings)
  2. Bridge fans out to all 5 engines in parallel
  3. Aggregates contradictions, deduplicates by entity
  4. [META-008] Runs ConsistencyVerifier.check_batch() for auto-retraction
  5. [META-008] Calls auto_retract_callback for systematic dissenters
  6. Stores contradiction flags in sprint_delta (quality_gate_json)
  7. Pushes high-severity entities to subscribers for re-fetch
  8. Returns ContradictionAuditResult with aggregated signals + retractions

ENGINES:
  - AdversarialVerifier (hledac_hypothesis/adversarial.py:258)
    detect_contradictions(evidence_list) -> list[Contradiction]
  - InsightEngine (brain/insight_engine.py:358)
    _find_contradictions(data) -> list[Contradiction]
  - DempsterShafer (brain/evidence_fusion.py:232)
    detect_contradiction(threshold) -> bool
  - EvidenceNetworkAnalyzer (advanced_web/evidence_network_analyzer.py:489)
    detect_contradictions(evidence_a, evidence_b) -> dict | None
  - GraphRAG (knowledge/graph_rag.py:1226)
    _detect_contradictions(facts) -> contested + paths

AUTO-RETRACTION (META-008):
  - ConsistencyVerifier.check_batch(findings, signals) -> list[RetractionDecision]
  - SourceReliabilityTracker records claims and contradiction flags
  - Sources with >= 3 tri-source dissents or ratio > 0.3 get auto-retracted
  - Callback auto_retract_callback(source_id) -> bool triggers JTMS retraction

BOUNDS (M1 8GB safe):
  - MAX_FINDINGS_PER_AUDIT = 200
  - MAX_CONTRADICTIONS_PER_ENGINE = 50
  - AUDIT_TIMEOUT_S = 10.0
  - RE_FETCH_CANDIDATES_MAX = 20
  - AUTO_RETRACT_MAX = 10 (per audit)

Feature flags: HLEDAC_ENABLE_CONTRADICTION_FEEDBACK=1 (default ON)
               HLEDAC_ENABLE_SOURCE_RELIABILITY=1 (default ON, META-008)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from operator import attrgetter, itemgetter
logger = logging.getLogger(__name__)

# -- Bounds (M1 8GB safe) -----------------------------------------------------
MAX_FINDINGS_PER_AUDIT: int = 200
MAX_CONTRADICTIONS_PER_ENGINE: int = 50
AUDIT_TIMEOUT_S: float = 10.0
RE_FETCH_CANDIDATES_MAX: int = 20
CONTRADICTION_SEVERITY_THRESHOLD: float = 0.6  # Only re-fetch if severity >= this
AUTO_RETRACT_MAX: int = 10  # [META-008] Max sources to auto-retract per audit

# Env gate
_ENABLE_CONTRADICTION_FEEDBACK: bool = (
    os.environ.get("HLEDAC_ENABLE_CONTRADICTION_FEEDBACK", "1").lower()
    in ("1", "true", "yes", "on")
)


@dataclass
class ContradictionSignal:
    """A single contradiction detected by one of the engines."""
    engine: str  # adversarial | insight | dempster_shafer | evidence_network | causal | graph_rag
    entity_value: str = ""
    entity_type: str = "unknown"
    severity: float = 0.0  # 0.0-1.0
    contradiction_type: str = "unknown"  # negation | temporal | numeric | source_conflict
    claim_a: str = ""
    claim_b: str = ""
    description: str = ""
    resolution_hint: str = ""


@dataclass
class ReFetchCandidate:
    """An entity that should be re-fetched due to contradiction."""
    entity_value: str
    entity_type: str
    severity: float = 0.0
    contradiction_count: int = 0
    engines: list[str] = field(default_factory=list)
    reason: str = ""
    suggested_sources: list[str] = field(default_factory=list)


@dataclass
class ContradictionAuditResult:
    """Aggregated result from all contradiction engines."""
    audit_ts: float = 0.0
    findings_count: int = 0
    engines_run: int = 0
    engines_failed: int = 0
    total_contradictions: int = 0
    signals: list[ContradictionSignal] = field(default_factory=list)
    re_fetch_candidates: list[ReFetchCandidate] = field(default_factory=list)
    auto_retractions: list[str] = field(default_factory=list)  # [META-008] source_ids retracted
    quality_gate_passed: bool = True
    quality_gate_reason: str = ""
    audit_duration_ms: float = 0.0


class ContradictionFeedbackBridge:
    """Aggregates contradictions from all 4+ engines and feeds back.

    Pattern: mirrors EntropyFetchBridge (brain/uncertainty_quant.py) —
    centralized pub/sub for feedback signals. Producers (contradiction
    engines) emit signals; consumers (FetchCoordinator) re-fetch entities.

    Thread safety: all aggregation under asyncio.Lock.
    Fail-soft: any engine failure -> skip that engine, continue with others.
    """

    __slots__ = (
        "_enabled",
        "_lock",
        "_subscribers",
        "_audit_count",
        "_total_contradictions",
        "_total_re_fetches",
        "_last_audit_ts",
        "_retract_callback",  # [META-008]
        "_retract_count",     # [META-008]
    )

    def __init__(self) -> None:
        self._enabled: bool = _ENABLE_CONTRADICTION_FEEDBACK
        self._lock: asyncio.Lock = asyncio.Lock()
        self._subscribers: dict[str, asyncio.Queue[Any]] = {}
        self._audit_count: int = 0
        self._total_contradictions: int = 0
        self._total_re_fetches: int = 0
        self._last_audit_ts: float = 0.0
        self._retract_callback: Any = None  # [META-008] async callable(source_id) -> bool
        self._retract_count: int = 0        # [META-008]

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def subscribe(self, name: str, queue: asyncio.Queue[Any]) -> bool:
        """Subscribe a consumer to contradiction alerts.

        Args:
            name: Subscriber name (e.g. 'fetch_coordinator').
            queue: Queue to receive ReFetchCandidate objects.

        Returns:
            True if subscribed, False if already subscribed.
        """
        async with self._lock:
            if name in self._subscribers:
                return False
            self._subscribers[name] = queue
            return True

    async def unsubscribe(self, name: str) -> bool:
        """Unsubscribe a consumer."""
        async with self._lock:
            return self._subscribers.pop(name, None) is not None

    # -- [META-008] Auto-retraction callback -------------------------------

    def set_retract_callback(self, callback: Any) -> None:
        """Set the async callback for source auto-retraction.

        The callback receives a source_id string and should return True/False.
        Typically wired to IOCGraph.retract_source().

        Args:
            callback: Async callable(source_id: str) -> dict with 'facts_retracted' key,
                      or None to disable auto-retraction.

        Example:
            bridge.set_retract_callback(ioc_graph.retract_source)
        """
        self._retract_callback = callback
        if callback is not None:
            logger.info("[ContradictionFeedback] Auto-retract callback registered")

    async def _run_auto_retraction(
        self,
        findings: list[dict[str, Any]],
        signals: list[ContradictionSignal],
        sprint_id: str = "",
    ) -> list[str]:
        """[META-008] Run auto-retraction: ConsistencyVerifier + SourceReliability.

        Called at the end of run_contradiction_audit() after re-fetch publish.
        Identifies systematic dissenters and triggers JTMS retraction via callback.

        Args:
            findings: The same findings list passed to run_contradiction_audit.
            signals: The aggregated ContradictionSignal list.
            sprint_id: Current sprint ID for tracking.

        Returns:
            List of source_ids that were auto-retracted.
        """
        if self._retract_callback is None:
            logger.debug(
                "[ContradictionFeedback] Auto-retract skipped: no callback registered"
            )
            return []

        try:
            from hledac.universal.knowledge.consistency_verifier import (
                ConsistencyVerifier,
                get_consistency_verifier,
            )
            from hledac.universal.knowledge.source_reliability import (
                get_source_reliability_tracker,
            )
        except ImportError as e:
            logger.debug(
                "[ContradictionFeedback] Auto-retract modules not available: %s", e
            )
            return []

        # 1. Run ConsistencyVerifier — computes tri-source voting + ratio
        #    from findings + signals directly (no separate record_batch needed)
        decisions = verifier.check_batch(findings, signals)

        if not decisions:
            logger.debug(
                "[ContradictionFeedback] Auto-retract: no sources meet criteria"
            )
            return []

        # 2. Record verdicts in SourceReliabilityTracker for cross-sprint tracking
        #    (tri_source_voting verdicts get double weight — strong evidence)
        try:
            tracker = get_source_reliability_tracker()
            await tracker.record_decisions(decisions)
        except Exception as e:
            logger.debug(
                "[ContradictionFeedback] record_decisions failed (fail-soft): %s", e,
            )

        # Cap at AUTO_RETRACT_MAX
        decisions = decisions[:AUTO_RETRACT_MAX]

        # 3. Call retract_callback for each decision
        retracted: list[str] = []
        for decision in decisions:
            try:
                result = await self._retract_callback(decision.source_id)
                if result and isinstance(result, dict):
                    facts_retracted = result.get("facts_retracted", 0)
                    if facts_retracted > 0:
                        retracted.append(decision.source_id)
                        self._retract_count += 1
                        await tracker.mark_auto_retracted(
                            decision.source_id,
                            sprint_id=sprint_id,
                        )
                        logger.info(
                            "[ContradictionFeedback] AUTO-RETRACTED source '%s': "
                            "%s (dissent=%d, ratio=%.3f, facts_retracted=%d)",
                            decision.source_id,
                            decision.reason,
                            decision.dissent_count,
                            decision.ratio,
                            facts_retracted,
                        )
                    else:
                        logger.debug(
                            "[ContradictionFeedback] Auto-retract '%s': "
                            "callback returned 0 facts_retracted",
                            decision.source_id,
                        )
                else:
                    logger.debug(
                        "[ContradictionFeedback] Auto-retract '%s': "
                        "callback returned unexpected: %s",
                        decision.source_id, type(result).__name__,
                    )
            except Exception as e:
                logger.warning(
                    "[ContradictionFeedback] Auto-retract '%s' failed: %s",
                    decision.source_id, e,
                )

        return retracted

    async def _publish(self, candidates: list[ReFetchCandidate]) -> int:
        """Publish re-fetch candidates to all subscribers.

        Returns number of subscribers notified.
        """
        notified = 0
        async with self._lock:
            subscribers = dict(self._subscribers)
        for name, queue in subscribers.items():
            try:
                for candidate in candidates:
                    queue.put_nowait(candidate)
                notified += 1
            except asyncio.QueueFull:
                logger.debug(
                    "[ContradictionFeedback] Queue full for %s, dropping %d candidates",
                    name, len(candidates),
                )
            except Exception as e:
                logger.debug(
                    "[ContradictionFeedback] Publish to %s failed: %s", name, e
                )
        return notified

    async def run_contradiction_audit(
        self,
        findings: list[dict[str, Any]],
        sprint_id: str = "",
    ) -> ContradictionAuditResult:
        """Run all contradiction engines against a set of findings.

        Args:
            findings: List of finding dicts from DuckDB (must have at least
                       'payload_text' or 'claims_json' keys).
            sprint_id: Current sprint ID for quality gate tracking.

        Returns:
            ContradictionAuditResult with aggregated signals and re-fetch candidates.
        """
        if not self._enabled or not findings:
            return ContradictionAuditResult(
                audit_ts=_time.time(),
                findings_count=len(findings),
                quality_gate_passed=True,
            )

        t0 = _time.monotonic()
        findings = findings[:MAX_FINDINGS_PER_AUDIT]

        # Run all engines in parallel (each with timeout)
        tasks: dict[str, asyncio.Task[Any]] = {}
        engines_available: list[str] = []

        # 1. AdversarialVerifier (fail-soft: reqs HypothesisEngine)
        try:
            tasks["adversarial"] = asyncio.create_task(
                self._run_adversarial_verifier(findings)
            )
            engines_available.append("adversarial")
        except Exception:  # noqa: BLE001
            pass

        # 2. InsightEngine
        try:
            tasks["insight"] = asyncio.create_task(
                self._run_insight_engine(findings)
            )
            engines_available.append("insight")
        except Exception:  # noqa: BLE001
            pass

        # 3. DempsterShafer
        try:
            tasks["dempster_shafer"] = asyncio.create_task(
                self._run_dempster_shafer(findings)
            )
            engines_available.append("dempster_shafer")
        except Exception:  # noqa: BLE001
            pass

        # 4. EvidenceNetworkAnalyzer
        try:
            tasks["evidence_network"] = asyncio.create_task(
                self._run_evidence_network(findings)
            )
            engines_available.append("evidence_network")
        except Exception:  # noqa: BLE001
            pass

        # 5. GraphRAG (if available)
        try:
            tasks["graph_rag"] = asyncio.create_task(
                self._run_graph_rag(findings)
            )
            engines_available.append("graph_rag")
        except Exception:  # noqa: BLE001
            pass

        # Collect results (with timeout per engine)
        all_signals: list[ContradictionSignal] = []
        engines_run = 0
        engines_failed = 0

        for engine_name, task in tasks.items():
            try:
                result = await asyncio.wait_for(
                    task, timeout=AUDIT_TIMEOUT_S / max(len(tasks), 1)
                )
                if result:
                    all_signals.extend(result[:MAX_CONTRADICTIONS_PER_ENGINE])
                    engines_run += 1
                else:
                    engines_failed += 1
            except (asyncio.TimeoutError, asyncio.CancelledError):
                engines_failed += 1
            except Exception as e:
                logger.debug(
                    "[ContradictionFeedback] %s engine failed: %s", engine_name, e
                )
                engines_failed += 1

        # Deduplicate and rank signals by entity
        entity_signals: dict[str, list[ContradictionSignal]] = {}
        for signal in all_signals:
            key = signal.entity_value or f"{signal.claim_a}|{signal.claim_b}"
            if key not in entity_signals:
                entity_signals[key] = []
            entity_signals[key].append(signal)

        # Build re-fetch candidates
        re_fetch_candidates: list[ReFetchCandidate] = []
        for entity_key, sigs in entity_signals.items():
            max_severity = max(s.severity for s in sigs)
            if max_severity < CONTRADICTION_SEVERITY_THRESHOLD:
                continue

            primary = sigs[0]
            engines_used = list({s.engine for s in sigs})

            candidate = ReFetchCandidate(
                entity_value=primary.entity_value or entity_key,
                entity_type=primary.entity_type,
                severity=max_severity,
                contradiction_count=len(sigs),
                engines=engines_used,
                reason=(
                    f"Contradiction detected by {len(engines_used)} engine(s): "
                    f"{', '.join(engines_used)}"
                ),
                suggested_sources=_suggest_alternative_sources(primary.entity_type),
            )
            re_fetch_candidates.append(candidate)

        # Sort by severity, cap at max
        re_fetch_candidates.sort(key=attrgetter("severity"), reverse=True)
        re_fetch_candidates = re_fetch_candidates[:RE_FETCH_CANDIDATES_MAX]

        # Quality gate decision
        quality_gate_passed = (
            len(re_fetch_candidates) == 0
            or max((c.severity for c in re_fetch_candidates), default=0) < 0.8
        )
        quality_gate_reason = (
            "OK"
            if quality_gate_passed
            else (
                f"BLOCKED: {len(re_fetch_candidates)} high-severity "
                f"contradictions require re-fetch"
            )
        )

        # Publish re-fetch candidates to subscribers
        notified = 0
        if re_fetch_candidates:
            notified = await self._publish(re_fetch_candidates)

        # [META-008] Auto-retraction: identify + retract systematic dissenters
        auto_retracted: list[str] = []
        try:
            auto_retracted = await self._run_auto_retraction(
                findings, all_signals, sprint_id
            )
        except Exception as e:
            logger.debug(
                "[ContradictionFeedback] Auto-retraction failed (fail-soft): %s", e,
            )

        # Update counters
        async with self._lock:
            self._audit_count += 1
            self._total_contradictions += len(all_signals)
            self._total_re_fetches += len(re_fetch_candidates)
            self._last_audit_ts = _time.time()

        duration_ms = (_time.monotonic() - t0) * 1000

        logger.info(
            "[ContradictionFeedback] Audit #%d: %d findings -> %d contradictions "
            "(%d engines run, %d failed) -> %d re-fetch candidates (%d subscribers "
            "notified) in %.1fms | quality_gate=%s",
            self._audit_count,
            len(findings),
            len(all_signals),
            engines_run,
            engines_failed,
            len(re_fetch_candidates),
            notified,
            duration_ms,
            quality_gate_reason,
        )

        return ContradictionAuditResult(
            audit_ts=_time.time(),
            findings_count=len(findings),
            engines_run=engines_run,
            engines_failed=engines_failed,
            total_contradictions=len(all_signals),
            signals=all_signals[:MAX_CONTRADICTIONS_PER_ENGINE * len(tasks)],
            re_fetch_candidates=re_fetch_candidates,
            auto_retractions=auto_retracted,  # [META-008]
            quality_gate_passed=quality_gate_passed,
            quality_gate_reason=quality_gate_reason,
            audit_duration_ms=duration_ms,
        )

    # -- Engine runners (each returns list[ContradictionSignal] or None) ------

    async def _run_adversarial_verifier(
        self,
        findings: list[dict[str, Any]],
    ) -> list[ContradictionSignal] | None:
        """Run AdversarialVerifier.detect_contradictions().

        hledac_hypothesis/adversarial.py:258
        detect_contradictions(evidence_list: list[Evidence]) -> list[Contradiction]

        Requires HypothesisEngine for initialization — fail-soft if unavailable.
        """
        try:
            from hledac.universal.hledac_hypothesis.adversarial import (
                AdversarialVerifier,
            )
            from hledac.universal.hledac_hypothesis.types.evidence import Evidence

            # AdversarialVerifier requires hypothesis_engine — try to get it
            try:
                from hledac.universal.brain import HypothesisEngine
                hypothesis_engine = HypothesisEngine()
            except (ImportError, TypeError, Exception):
                hypothesis_engine = None  # type: ignore[assignment]

            if hypothesis_engine is None:
                return None  # fail-soft: can't create verifier without engine

            verifier = AdversarialVerifier(hypothesis_engine=hypothesis_engine)

            evidence_list = [
                Evidence(
                    evidence_id=f"ev_{uuid.uuid4().hex[:12]}",
                    source=f.get("source_type", "unknown"),
                    content=f.get("payload_text", "") or "",
                    timestamp=datetime.now(UTC),
                    reliability=f.get("confidence", 0.5),
                )
                for f in findings
                if f.get("payload_text")
            ][:MAX_FINDINGS_PER_AUDIT]

            if not evidence_list:
                return None

            contradictions = verifier.detect_contradictions(evidence_list)
            return [
                ContradictionSignal(
                    engine="adversarial",
                    entity_value=(
                        getattr(c, "claim_a", "")[:100]
                        if hasattr(c, "claim_a")
                        else ""
                    ),
                    entity_type="claim",
                    severity=float(getattr(c, "severity", 0.5)),
                    contradiction_type=getattr(
                        c, "contradiction_type", "negation"
                    ),
                    claim_a=getattr(c, "claim_a", ""),
                    claim_b=getattr(c, "claim_b", ""),
                    description=getattr(c, "resolution_notes", ""),
                )
                for c in contradictions[:MAX_CONTRADICTIONS_PER_ENGINE]
            ]
        except ImportError:
            return None
        except Exception as e:
            logger.debug("[ContradictionFeedback] AdversarialVerifier error: %s", e)
            return None

    async def _run_insight_engine(
        self,
        findings: list[dict[str, Any]],
    ) -> list[ContradictionSignal] | None:
        """Run InsightEngine._find_contradictions().

        brain/insight_engine.py:358
        _find_contradictions(data: list[dict]) -> list[Contradiction]
        """
        try:
            from hledac.universal.brain.insight_engine import InsightEngine

            engine = InsightEngine()
            data = [
                {
                    k: v
                    for k, v in f.items()
                    if k in ("payload_text", "source_type", "confidence")
                }
                for f in findings[:MAX_FINDINGS_PER_AUDIT]
            ]
            contradictions = engine._find_contradictions(data)
            return [
                ContradictionSignal(
                    engine="insight",
                    entity_value=getattr(c, "statement_a", "")[:100],
                    entity_type="statement",
                    severity=float(getattr(c, "severity", 0.5)),
                    contradiction_type="negation",
                    claim_a=getattr(c, "statement_a", ""),
                    claim_b=getattr(c, "statement_b", ""),
                )
                for c in contradictions[:MAX_CONTRADICTIONS_PER_ENGINE]
            ]
        except ImportError:
            return None
        except Exception as e:
            logger.debug("[ContradictionFeedback] InsightEngine error: %s", e)
            return None

    async def _run_dempster_shafer(
        self,
        findings: list[dict[str, Any]],
    ) -> list[ContradictionSignal] | None:
        """Run DempsterShafer.detect_contradiction().

        brain/evidence_fusion.py:232
        detect_contradiction(threshold: float = 0.5) -> bool

        add_evidence(hypothesis: str, mass: float, source_weight: float = 1.0,
                     source_id: str | None = None) -> str
        """
        try:
            from hledac.universal.brain.evidence_fusion import DempsterShafer

            engine = DempsterShafer()
            # Add evidence from findings — pass mass (confidence) not evidence_text
            for f in findings[:MAX_FINDINGS_PER_AUDIT]:
                source = f.get("source_type", "unknown")
                conf = f.get("confidence", 0.5)
                finding_id = f.get("id", None) or None
                if conf > 0:
                    engine.add_evidence(
                        hypothesis=source,
                        mass=conf,
                        source_weight=conf,
                        source_id=finding_id,
                    )

            if engine.detect_contradiction(threshold=0.5):
                return [
                    ContradictionSignal(
                        engine="dempster_shafer",
                        entity_type="hypothesis",
                        severity=min(engine.conflict_mass(), 1.0),
                        contradiction_type="source_conflict",
                        description=f"Conflict mass: {engine.conflict_mass():.3f}",
                    )
                ]
            return None
        except ImportError:
            return None
        except Exception as e:
            logger.debug("[ContradictionFeedback] DempsterShafer error: %s", e)
            return None

    async def _run_evidence_network(
        self,
        findings: list[dict[str, Any]],
    ) -> list[ContradictionSignal] | None:
        """Run EvidenceNetworkAnalyzer.detect_contradictions().

        advanced_web/evidence_network_analyzer.py:489
        detect_contradictions(evidence_a, evidence_b) -> dict | None
        """
        try:
            from hledac.universal.advanced_web.evidence_network_analyzer import (
                EvidenceNetworkAnalyzer,
            )

            analyzer = EvidenceNetworkAnalyzer()
            signals: list[ContradictionSignal] = []

            # Pairwise comparison (bounded: O(n^2) capped at sqrt of max)
            n = min(len(findings), int(MAX_FINDINGS_PER_AUDIT ** 0.5))
            for i in range(n):
                for j in range(i + 1, n):
                    fa = findings[i]
                    fb = findings[j]
                    try:
                        result = await analyzer.detect_contradictions(fa, fb)
                        if result and result.get("contradicts"):
                            signals.append(
                                ContradictionSignal(
                                    engine="evidence_network",
                                    entity_value=str(result.get("key", "")),
                                    entity_type=str(result.get("key", "entity")),
                                    severity=float(result.get("confidence", 0.5)),
                                    contradiction_type="numeric",
                                    description=str(result.get("reason", "")),
                                )
                            )
                    except Exception:
                        continue
                    if len(signals) >= MAX_CONTRADICTIONS_PER_ENGINE:
                        break
                if len(signals) >= MAX_CONTRADICTIONS_PER_ENGINE:
                    break

            return signals if signals else None
        except ImportError:
            return None
        except Exception as e:
            logger.debug("[ContradictionFeedback] EvidenceNetwork error: %s", e)
            return None

    async def _run_graph_rag(
        self,
        findings: list[dict[str, Any]],
    ) -> list[ContradictionSignal] | None:
        """Run GraphRAGOrchestrator._detect_contradictions().

        knowledge/graph_rag.py:1226
        _detect_contradictions(facts) -> (contested, primary_paths, counter_paths)
        """
        try:
            from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator

            # GraphRAGOrchestrator requires knowledge_layer — try with None
            # (fail-soft: returns empty facts list which produces no contradictions)
            try:
                orchestrator = GraphRAGOrchestrator(knowledge_layer=None)  # type: ignore[arg-type]
            except (TypeError, Exception):
                return None
            facts = [
                {
                    "source": f.get("source_type", "unknown"),
                    "claim": (f.get("payload_text", "") or "")[:500],
                    "confidence": f.get("confidence", 0.5),
                }
                for f in findings[:MAX_FINDINGS_PER_AUDIT]
            ]
            contested, primary, counter = orchestrator._detect_contradictions(facts)
            if contested and counter:
                signals: list[ContradictionSignal] = []
                for cp in counter[:MAX_CONTRADICTIONS_PER_ENGINE]:
                    signals.append(
                        ContradictionSignal(
                            engine="graph_rag",
                            entity_type="graph_fact",
                            severity=0.7,
                            contradiction_type="source_conflict",
                            description=(
                                str(cp)[:200] if cp else "counter-narrative detected"
                            ),
                        )
                    )
                return signals if signals else None
            return None
        except ImportError:
            return None
        except Exception as e:
            logger.debug("[ContradictionFeedback] GraphRAG error: %s", e)
            return None

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry counters."""
        return {
            "audit_count": self._audit_count,
            "total_contradictions": self._total_contradictions,
            "total_re_fetches": self._total_re_fetches,
            "last_audit_ts": self._last_audit_ts,
            "subscribers": len(self._subscribers),
            "retract_callback_set": self._retract_callback is not None,  # [META-008]
            "total_retractions": self._retract_count,  # [META-008]
        }


def _suggest_alternative_sources(entity_type: str) -> list[str]:
    """Suggest alternative source types for re-fetch based on entity type."""
    suggestions: dict[str, list[str]] = {
        "domain": ["CT", "passive_dns", "BGP", "Shodan", "Censys"],
        "ip": ["Shodan", "Censys", "BGP", "passive_dns"],
        "hash": ["VirusTotal", "MalwareBazaar", "ThreatFox"],
        "url": ["Wayback", "CommonCrawl", "urlscan.io"],
        "email": ["Hunter.io", "Dehashed", "HaveIBeenPwned"],
    }
    return suggestions.get(entity_type, ["CT", "Wayback", "passive_dns"])


# -- Singleton accessor --------------------------------------------------------
_contradiction_bridge: ContradictionFeedbackBridge | None = None


def get_contradiction_bridge() -> ContradictionFeedbackBridge:
    """Return the shared ContradictionFeedbackBridge singleton."""
    global _contradiction_bridge
    if _contradiction_bridge is None:
        _contradiction_bridge = ContradictionFeedbackBridge()
    return _contradiction_bridge
