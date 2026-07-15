"""
Evidence types — hledac_hypothesis.types.evidence
=================================================

Extracted from hledac_hypothesis._types (C4 Sprint Refactoring).
M1 8GB: msgspec.Struct zero-copy, ~0 KB overhead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import msgspec


def _utc_now() -> datetime:
    """Module-level factory for UTC now — required for msgspec default_factory."""
    return datetime.now(UTC)


class Evidence(msgspec.Struct):
    """Evidence item supporting or conflicting with a hypothesis."""
    evidence_id: str
    source: str
    content: str
    timestamp: datetime
    reliability: float = 1.0  # 0-1, source reliability
    relevance: float = 1.0    # 0-1, relevance to hypothesis
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


# SourceCredibility is kept as @dataclass — has runtime update method update_accuracy()
from dataclasses import dataclass, field


@dataclass(slots=True)
class SourceCredibility:
    """
    Credibility assessment for an evidence source.

    Tracks historical accuracy, bias indicators, and overall trustworthiness.
    Used to weight evidence by source quality.
    """
    source_id: str
    credibility_score: float  # 0-1, overall credibility
    bias_indicators: list[str] = field(default_factory=list)
    historical_accuracy: float = 0.5  # 0-1, based on past verification
    last_updated: datetime = field(default_factory=datetime.now)
    total_claims: int = 0
    verified_claims: int = 0
    contradiction_count: int = 0

    def update_accuracy(self, was_correct: bool) -> None:
        """Update historical accuracy with a new verification result."""
        self.total_claims += 1
        if was_correct:
            self.verified_claims += 1
        self.historical_accuracy = self.verified_claims / self.total_claims
        # Recalculate credibility score
        self.credibility_score = (
            self.historical_accuracy * 0.7 +
            (1.0 - min(1.0, self.contradiction_count / 10)) * 0.3
        )
        self.last_updated = datetime.now(UTC)  # noqa: DTZ005


class Event(msgspec.Struct):
    """Temporal event for consistency checking."""
    event_id: str
    description: str
    timestamp: datetime
    source: str
    metadata: dict[str, Any] = msgspec.field(default_factory=dict)


__all__ = [
    "Evidence",
    "SourceCredibility",
    "Event",
]
