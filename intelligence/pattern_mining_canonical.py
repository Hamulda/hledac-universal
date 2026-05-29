"""
Pattern Mining Canonical Adapter — Sprint F250
==============================================

Canonical adapter wrapping PatternMiningEngine for the sprint pipeline.

Responsibilities:
  1. Accept CanonicalFinding list from sprint findings
  2. Convert to PatternMiningEngine input types
  3. Run bounded pattern mining
  4. Produce derived pattern CanonicalFinding objects

Role: deterministic sidecar, NOT the main write path.
Derived findings go through async_ingest_findings_batch() like any other finding.

M1 8GB CEILING:
  - MAX_FINDINGS=500 findings per sprint
  - MAX_PATTERNS=200 patterns per sprint
  - MAX_ANOMALIES=50 anomalies per sprint
  - All methods fail-soft: sprint continues on any error

OSINT Pattern Types:
  - Temporal: Kdy se objevují nové IoC? Sezónnost útoků?
  - Behavioral: Jak se mění chování threat actorů?
  - Communication: Kdo komunikuje s kým v rámci kampaně?
  - Transaction: Kryptoměnové flow vzory
  - Sequential: Sekvence aktivit (scan → exploit → exfil)
  - Anomaly: Nové vzory chování — outlier detection
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_FINDINGS: int = 500
MAX_PATTERNS: int = 200
MAX_ANOMALIES: int = 50
MIN_EVENTS_FOR_TEMPORAL: int = 10
MIN_ACTIONS_FOR_BEHAVIORAL: int = 5

# ── Imports ──────────────────────────────────────────────────────────────────

try:
    from intelligence.pattern_mining import (
        Action,
        Event,
        PatternMiningEngine,
        create_pattern_mining_engine,
    )
    _PATTERN_MINING_AVAILABLE = True
except ImportError:
    _PATTERN_MINING_AVAILABLE = False
    PatternMiningEngine = None
    Event = None
    Action = None
    create_pattern_mining_engine = None

try:
    from knowledge.duckdb_store import CanonicalFinding
except ImportError:
    CanonicalFinding = None


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class PatternCandidate:
    """A derived pattern candidate produced by the pattern mining engine."""
    pattern_id: str
    pattern_type: str  # temporal, behavioral, anomaly, etc.
    pattern_data: dict[str, Any]
    confidence: float  # 0-1
    severity: float  # 0-1
    description: str
    source_findings: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type,
            "pattern_data": self.pattern_data,
            "confidence": self.confidence,
            "severity": self.severity,
            "description": self.description,
            "source_findings": self.source_findings,
            "metadata": self.metadata,
        }


@dataclass
class PatternMiningResult:
    """Aggregated result of pattern mining on sprint findings."""
    temporal_patterns: list[PatternCandidate] = field(default_factory=list)
    behavioral_patterns: list[PatternCandidate] = field(default_factory=list)
    anomalies: list[PatternCandidate] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)


# ── Adapter ───────────────────────────────────────────────────────────────────

class PatternMiningAdapter:
    """
    Canonical adapter for PatternMiningEngine in the sprint pipeline.

    Wraps PatternMiningEngine with:
      - Bounded findings processing (MAX_FINDINGS)
      - Fail-soft: errors never crash the sprint
      - Conversion to CanonicalFinding for async_ingest_findings_batch()

    Usage:
        adapter = PatternMiningAdapter()
        result = adapter.extract_and_mine(findings)
        findings = adapter.to_derived_findings(result, query)
    """

    def __init__(
        self,
        use_mlx: bool = True,
        min_support: float = 0.1,
        min_confidence: float = 0.5,
    ):
        if not _PATTERN_MINING_AVAILABLE:
            raise ImportError(
                "pattern_mining module not available — "
                "install numpy for pattern detection"
            )

        self._engine = create_pattern_mining_engine(
            use_mlx=use_mlx,
            min_support=min_support,
            min_confidence=min_confidence,
        )
        self._use_mlx = use_mlx
        self._stats: dict[str, int] = {
            "findings_processed": 0,
            "events_extracted": 0,
            "actions_extracted": 0,
            "temporal_patterns_found": 0,
            "behavioral_patterns_found": 0,
            "anomalies_found": 0,
            "findings_produced": 0,
        }

    def _to_events(self, findings: list[Any]) -> list[Event]:
        """Convert CanonicalFinding list to Event list for temporal patterns."""
        events: list[Event] = []
        for f in findings[:MAX_FINDINGS]:
            try:
                ts = f.ts if hasattr(f, 'ts') else time.time()
                entity_id = f.finding_id if hasattr(f, 'finding_id') else str(id(f))
                event_type = f.source_type if hasattr(f, 'source_type') else "unknown"

                value = 0.5
                if hasattr(f, 'payload_text') and f.payload_text:
                    try:
                        payload = json.loads(f.payload_text)
                        value = payload.get('confidence', 0.5)
                    except (json.JSONDecodeError, TypeError):
                        pass

                events.append(Event(
                    timestamp=datetime.fromtimestamp(ts),
                    entity_id=entity_id,
                    event_type=event_type,
                    value=value,
                    metadata={"query": getattr(f, 'query', "")},
                ))
            except Exception:
                pass

        self._stats["events_extracted"] = len(events)
        return events

    def _to_actions(self, findings: list[Any]) -> list[Action]:
        """Convert CanonicalFinding list to Action list for behavioral patterns."""
        actions: list[Action] = []
        for f in findings[:MAX_FINDINGS]:
            try:
                ts = f.ts if hasattr(f, 'ts') else time.time()
                user_id = getattr(f, 'query', "unknown")
                entity_id = f.finding_id if hasattr(f, 'finding_id') else str(id(f))
                action_type = f.source_type if hasattr(f, 'source_type') else "unknown"

                actions.append(Action(
                    timestamp=datetime.fromtimestamp(ts),
                    user_id=user_id,
                    action_type=action_type,
                    target=entity_id,
                    metadata={"source_type": action_type},
                ))
            except Exception:
                pass

        self._stats["actions_extracted"] = len(actions)
        return actions

    def extract_and_mine(self, findings: list[Any]) -> PatternMiningResult:
        """
        Run pattern mining on a list of CanonicalFinding objects.

        Bounded: MAX_FINDINGS=500, MAX_PATTERNS=200.
        Fail-soft: returns empty result on any error.
        """
        if not findings:
            return PatternMiningResult()

        self._stats["findings_processed"] = len(findings)

        try:
            result = PatternMiningResult()

            # Extract input types
            events = self._to_events(findings)
            actions = self._to_actions(findings)
            finding_ids = [getattr(f, 'finding_id', "") for f in findings[:50]]

            # Temporal patterns
            if len(events) >= MIN_EVENTS_FOR_TEMPORAL:
                try:
                    temporal = self._engine.mine_temporal_patterns(
                        events, min_events=MIN_EVENTS_FOR_TEMPORAL
                    )
                    for p in temporal[:MAX_PATTERNS]:
                        trend = p.trend.name if hasattr(p, 'trend') and p.trend else "STABLE"
                        seasonality = p.seasonality.name if hasattr(p, 'seasonality') and p.seasonality else "NONE"
                        result.temporal_patterns.append(PatternCandidate(
                            pattern_id=getattr(p, 'pattern_id', f"temp_{id(p)}"),
                            pattern_type="temporal",
                            pattern_data={
                                "trend": trend,
                                "seasonality": seasonality,
                                "burst_count": len(p.burst_times) if hasattr(p, 'burst_times') else 0,
                            },
                            confidence=getattr(p, 'confidence', 0.5),
                            severity=0.5,
                            description=f"Temporal: trend={trend}, season={seasonality}",
                            source_findings=finding_ids,
                        ))
                    self._stats["temporal_patterns_found"] = len(result.temporal_patterns)
                except Exception as e:
                    logger.debug(f"PatternMiningAdapter: temporal error: {e}")

            # Behavioral patterns
            if len(actions) >= MIN_ACTIONS_FOR_BEHAVIORAL:
                try:
                    behavioral = self._engine.mine_behavioral_patterns(
                        actions, min_actions=MIN_ACTIONS_FOR_BEHAVIORAL
                    )
                    for p in behavioral[:MAX_PATTERNS]:
                        user_id = getattr(p, 'user_id', "unknown")
                        seq = getattr(p, 'action_sequence', [])[:5]
                        result.behavioral_patterns.append(PatternCandidate(
                            pattern_id=getattr(p, 'pattern_id', f"behav_{id(p)}"),
                            pattern_type="behavioral",
                            pattern_data={
                                "user_id": user_id,
                                "action_sequence": seq,
                                "frequency": getattr(p, 'frequency_per_day', 0.0),
                            },
                            confidence=getattr(p, 'confidence', 0.5),
                            severity=0.5,
                            description=f"Behavioral: user={user_id}, actions={len(seq)}",
                            source_findings=finding_ids,
                        ))
                    self._stats["behavioral_patterns_found"] = len(result.behavioral_patterns)
                except Exception as e:
                    logger.debug(f"PatternMiningAdapter: behavioral error: {e}")

            result.stats = self._stats.copy()
            return result

        except Exception as e:
            logger.warning(f"PatternMiningAdapter.extract_and_mine error: {e}")
            return PatternMiningResult()

    def to_derived_findings(
        self,
        result: PatternMiningResult,
        query: str,
    ) -> list[Any]:
        """
        Convert PatternMiningResult to CanonicalFinding list.

        Fail-soft: returns empty list on error.
        """
        if not result or CanonicalFinding is None:
            return []

        findings: list[Any] = []
        try:
            ts = time.time()

            for cand in result.temporal_patterns[:MAX_PATTERNS]:
                fid = f"pattern_temporal_{cand.pattern_id[:24]}_{int(ts * 1000) % 1000000:06d}"
                payload = cand.to_dict()

                findings.append(CanonicalFinding(
                    finding_id=fid,
                    query=query,
                    source_type="pattern_temporal",
                    confidence=cand.confidence,
                    ts=ts,
                    provenance=("pattern_mining", "temporal"),
                    payload_text=json.dumps(payload),
                ))

            for cand in result.behavioral_patterns[:MAX_PATTERNS]:
                fid = f"pattern_behavioral_{cand.pattern_id[:24]}_{int(ts * 1000) % 1000000:06d}"
                payload = cand.to_dict()

                findings.append(CanonicalFinding(
                    finding_id=fid,
                    query=query,
                    source_type="pattern_behavioral",
                    confidence=cand.confidence,
                    ts=ts,
                    provenance=("pattern_mining", "behavioral"),
                    payload_text=json.dumps(payload),
                ))

            self._stats["findings_produced"] = len(findings)
            return findings

        except Exception as e:
            logger.warning(f"PatternMiningAdapter.to_derived_findings error: {e}")
            return []

    def get_stats(self) -> dict[str, int]:
        """Return adapter statistics."""
        return self._stats.copy()

    def clear(self) -> None:
        """Clear engine state and reset stats."""
        if hasattr(self._engine, 'clear'):
            self._engine.clear()
        self._stats = dict.fromkeys(self._stats, 0)


# ── Factory ────────────────────────────────────────────────────────────────────

def create_pattern_mining_adapter(
    use_mlx: bool = True,
    min_support: float = 0.1,
    min_confidence: float = 0.5,
) -> PatternMiningAdapter:
    """Factory to create PatternMiningAdapter."""
    return PatternMiningAdapter(
        use_mlx=use_mlx,
        min_support=min_support,
        min_confidence=min_confidence,
    )


__all__ = [
    "PatternCandidate",
    "PatternMiningResult",
    "PatternMiningAdapter",
    "create_pattern_mining_adapter",
    "MAX_FINDINGS",
    "MAX_PATTERNS",
    "MAX_ANOMALIES",
]
