"""
Evidence log pro federated learning (downgrade a security events).
"""

import logging
from collections import deque
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


class FederationEvidenceEvent:
    """Evidence event pro federated learning."""

    def __init__(self, kind: str, summary: dict[str, Any], reasons: list[str],
                 refs: dict[str, str], confidence: float):
        self.kind = kind
        self.summary = summary
        self.reasons = reasons
        self.refs = refs
        self.confidence = confidence
        self.timestamp = datetime.utcnow()

    def to_dict(self) -> dict[str, Any]:
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

    def create_decision_event(self, kind: str, summary: dict[str, Any],
                              reasons: list[str], refs: dict[str, str],
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

    def get_recent(self, limit: int = 100) -> list[FederationEvidenceEvent]:
        """Vrátí poslední události."""
        return list(self.events)[-limit:]

    def get_by_kind(self, kind: str) -> list[FederationEvidenceEvent]:
        """Vrátí události podle typu."""
        return [e for e in self.events if e.kind == kind]
