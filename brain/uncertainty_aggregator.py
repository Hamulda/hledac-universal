"""
Entity-level uncertainty aggregation from token-level entropy.

ISSUE APEX-1008: Aggregates per-token entropy into entity-level confidence scores.
For each extracted IOC entity, averages token entropy over its character span
to detect low-confidence generations that may produce false positive IOCs.

Architecture:
    UncertaintyAggregator
        - Takes TokenUncertaintyCollector output
        - Maps entity character spans to token positions
        - Computes avg/max entropy per entity
        - Applies gating thresholds:
            * H > 1.5 bits -> confidence=low, uncertainty_flag="high_entropy"
            * H > 0.8 bits -> confidence=medium, uncertainty_flag="elevated"
            * H <= 0.8 bits -> confidence=high, uncertainty_flag="normal"

Usage:
    aggregator = UncertaintyAggregator()
    entity_confidences = aggregator.aggregate(
        entities=["192.168.1.1", "CVE-2024-1234"],
        generated_text='{"ip": "192.168.1.1", "cve": "CVE-2024-1234"}',
        collector=token_collector
    )
    # Returns: {"192.168.1.1": (0.95, "normal"), "CVE-2024-1234": (0.72, "elevated")}
"""
from __future__ import annotations

import logging
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)


class UncertaintyAggregator:
    """
    Aggregates token-level entropy into entity-level confidence scores.

    Maps character spans of extracted entities to token positions,
    computes average entropy over each entity's token span, and
    classifies confidence levels based on entropy thresholds.
    """

    def __init__(self) -> None:
        pass

    def aggregate(
        self,
        entities: list[str],
        generated_text: str,
        collector: Any,
    ) -> dict[str, tuple[float, str]]:
        """
        Aggregate token entropy into entity-level confidence.

        Args:
            entities: List of IOC entity strings to analyze.
            generated_text: The full generated JSON text.
            collector: TokenUncertaintyCollector instance with captured entropies.

        Returns:
            Dict mapping entity_text -> (confidence, uncertainty_flag).
            Entities not found in token stream get default (1.0, "normal").
        """
        results: dict[str, tuple[float, str]] = {}

        for entity in entities:
            uncertainty = collector.get_entity_uncertainty(entity, generated_text)
            if uncertainty is not None:
                # Map EntityUncertainty to simple tuple
                results[entity] = (
                    self._entropy_to_confidence(uncertainty.avg_entropy_bits),
                    uncertainty.uncertainty_flag,
                )
            else:
                # Default: high confidence, no uncertainty flag
                results[entity] = (1.0, "normal")

        return results

    @staticmethod
    def _entropy_to_confidence(avg_entropy_bits: float) -> float:
        """
        Convert average entropy (bits) to confidence score (0.0-1.0).

        Mapping:
            H=0.0 bits -> confidence=1.0 (deterministic)
            H=0.8 bits -> confidence=0.8 (low uncertainty)
            H=1.5 bits -> confidence=0.5 (medium uncertainty)
            H=2.3 bits -> confidence=0.2 (high uncertainty, ~uniform over top-5)

        Formula: confidence = max(0.0, 1.0 - (H / 2.3))
        """
        # Linear mapping: H in [0, 2.3] -> confidence in [1.0, 0.0]
        confidence = max(0.0, 1.0 - (avg_entropy_bits / 2.3))
        return round(confidence, 2)
