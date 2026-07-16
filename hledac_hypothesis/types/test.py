"""
Test types — hledac_hypothesis.types.test
==========================================

Extracted from hledac_hypothesis._types (C4 Sprint Refactoring).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

import msgspec
from dataclasses import dataclass, field


class TestType(Enum):
    """Types of tests that can be designed and executed."""
    EXISTENCE_CHECK = "existence_check"
    CORRELATION_TEST = "correlation_test"
    CAUSAL_TEST = "causal_test"
    IDENTITY_VERIFICATION = "identity_verification"
    TEMPORAL_ORDERING = "temporal_ordering"
    CONSISTENCY_CHECK = "consistency_check"
    PREDICTION_TEST = "prediction_test"


class TestResult(msgspec.Struct):
    """Result of executing a test against a hypothesis."""
    test_type: str
    result: str  # passed, failed, inconclusive
    confidence: float
    evidence_collected: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)

    def __post_init__(self) -> None:
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp)


class TestDesign(msgspec.Struct):
    """Design for testing a hypothesis."""
    test_type: str
    description: str
    required_data: list[str] = msgspec.field(default_factory=list)
    expected_outcome_if_true: str = ""
    expected_outcome_if_false: str = ""
    priority: float = 0.5  # 0-1, higher = test sooner
    cost_estimate: float = 1.0  # Estimated computational cost


def _utc_now() -> datetime:
    """Module-level factory for UTC now — required for msgspec default_factory."""
    from datetime import UTC
    return datetime.now(UTC)


class FalsificationResult(msgspec.Struct):
    """Result of a falsification attempt."""
    falsified: bool
    confidence: float
    counter_evidence: list[str] = msgspec.field(default_factory=list)
    reasoning: str = ""
    timestamp: datetime = msgspec.field(default_factory=_utc_now)


__all__ = [
    "TestType",
    "TestResult",
    "TestDesign",
    "FalsificationResult",
]
