"""
F256: HermesInferenceOutput contract — cross-sprint data transfer object.

Canonical home for Hermes3Engine inference results used by:
- pivot_planner.py    (score_with_hermes_output, _pivot_from_hermes_output)
- sprint_advisory_runner.py (from_dict loading from DuckDB)
- live_public_pipeline.py  (construction + to_dict persistence)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
MAX_INFERENCE_ITEMS: int = 50  # cap hermes_outputs list in advisory runner

# --------------------------------------------------------------------------- #
# Dataclass
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class HermesInferenceOutput:
    """Hermes3Engine structured inference output for pivot planning."""

    output_id: str = ""
    source_finding_id: str = ""
    inference_type: str = ""           # e.g. "report_synthesis"
    timestamp: float = 0.0
    primary_text: str = ""             # unused by pivot_planner but stored
    confidence: float = 0.0           # 0.0–1.0

    # Core pivot extraction targets
    key_iocs: list[str] = field(default_factory=list)     # domains, IPs, hashes, emails
    key_entities: list[str] = field(default_factory=list)  # extracted entities
    pivot_suggestions: list[str] = field(default_factory=list)  # LLM-suggested queries

    # Metadata
    bounded: bool = False
    tokens_used: int = 0
    model_name: str = ""
    source_hints: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------------- #
    # Serialisation
    # ------------------------------------------------------------------------- #
    def to_dict(self) -> dict[str, Any]:
        return {
            "output_id": self.output_id,
            "source_finding_id": self.source_finding_id,
            "inference_type": self.inference_type,
            "timestamp": self.timestamp,
            "primary_text": self.primary_text,
            "confidence": self.confidence,
            "key_iocs": self.key_iocs,
            "key_entities": self.key_entities,
            "pivot_suggestions": self.pivot_suggestions,
            "bounded": self.bounded,
            "tokens_used": self.tokens_used,
            "model_name": self.model_name,
            "source_hints": list(self.source_hints),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HermesInferenceOutput":
        return cls(
            output_id=payload.get("output_id", ""),
            source_finding_id=payload.get("source_finding_id", ""),
            inference_type=payload.get("inference_type", ""),
            timestamp=payload.get("timestamp", 0.0),
            primary_text=payload.get("primary_text", ""),
            confidence=payload.get("confidence", 0.0),
            key_iocs=payload.get("key_iocs", []),
            key_entities=payload.get("key_entities", []),
            pivot_suggestions=payload.get("pivot_suggestions", []),
            bounded=payload.get("bounded", False),
            tokens_used=payload.get("tokens_used", 0),
            model_name=payload.get("model_name", ""),
            source_hints=tuple(payload.get("source_hints", [])),
        )

    # ------------------------------------------------------------------------- #
    # __all__
    # ------------------------------------------------------------------------- #
    __all__ = ["HermesInferenceOutput", "MAX_INFERENCE_ITEMS"]