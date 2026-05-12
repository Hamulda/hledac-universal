"""
Evidence log pro federated learning (downgrade a security events).
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from collections import deque

logger = logging.getLogger(__name__)


class FederationEvidenceEvent:
    """Evidence event pro federated learning."""

    def __init__(self, kind: str, summary: Dict[str, Any], reasons: List[str],
                 refs: Dict[str, str], confidence: float):
        self.kind = kind
        self.summary = summary
        self.reasons = reasons
        self.refs = refs
        self.confidence = confidence
        self.timestamp = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        return {
            'kind': self.kind,
            'summary': self.summary,
            'reasons': self.reasons,
            'refs': self.refs,
            'confidence': self.confidence,
            'timestamp': self.timestamp.isoformat()
        }


class FederationEvidenceLog:
    """Evidence log pro federated learning events."""

    def __init__(self, max_events: int = 1000):
        self.events = deque(maxlen=max_events)

    def create_decision_event(self, kind: str, summary: Dict[str, Any],
                              reasons: List[str], refs: Dict[str, str],
                              confidence: float) -> FederationEvidenceEvent:
        """Vytvoří decision event."""
        event = FederationEvidenceEvent(
            kind=kind,
            summary=summary,
            reasons=reasons,
            refs=refs,
            confidence=confidence
        )
        self.events.append(event)
        logger.info(f"Federation event: {kind} - {summary}")
        return event

    def get_recent(self, limit: int = 100) -> List[FederationEvidenceEvent]:
        """Vrátí poslední události."""
        return list(self.events)[-limit:]

    def get_by_kind(self, kind: str) -> List[FederationEvidenceEvent]:
        """Vrátí události podle typu."""
        return [e for e in self.events if e.kind == kind]
