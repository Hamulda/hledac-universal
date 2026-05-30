"""
HypothesisBuilder — Hypothesis Generation and Causal Reasoning Export
======================================================================

Hledac Universal OSINT platform - Causal hypothesis reasoning export layer.

Sprint F259: Uses brain/hypothesis_engine.py (canonical) for:
- Co-occurrence matrix analysis (numpy float16)
- Temporal sequence detection
- Anomaly detection
- Causal hypothesis generation

Wired into sprint_scheduler post-STIX-export phase.

M1 8GB constraints:
- HLEDAC_ENABLE_HYPOTHESIS=1 env var gate
- RAM check < 70% before running
- All processing bounded and fail-soft
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# =============================================================================
# Feature gate
# =============================================================================

HYPOTHESIS_ENABLED = os.environ.get("HLEDAC_ENABLE_HYPOTHESIS", "0") == "1"

# RAM threshold for M1 safety
RAM_THRESHOLD = 0.70


# =============================================================================
# Data classes
# =============================================================================

@dataclass
class HypothesisResult:
    """Result of hypothesis generation run."""
    enabled: bool
    hypotheses_generated: int
    entities_extracted: int
    temporal_sequences: int
    anomalies_detected: int
    execution_time_s: float
    stix_bundle_path: str | None = None
    error: str | None = None


# =============================================================================
# HypothesisBuilder
# =============================================================================

class HypothesisBuilder:
    """
    Hypothesis generation and causal reasoning for sprint exports.

    Uses brain/hypothesis_engine.py (canonical) for all causal reasoning.

    Integration:
    - Wired into sprint_scheduler post-STIX-export
    - Gate: HLEDAC_ENABLE_HYPOTHESIS=1 and RAM < 70%

    M1 8GB optimizations:
    - Lazy import of HypothesisEngine
    - RAM check before execution
    - fail-soft throughout
    """

    def __init__(self, output_dir: str | None = None) -> None:
        self.output_dir = output_dir
        self._engine: Any | None = None

    @property
    def engine(self) -> Any:
        """Lazy load HypothesisEngine from brain."""
        if self._engine is None:
            from brain.hypothesis_engine import HypothesisEngine

            self._engine = HypothesisEngine(
                max_hypotheses=200,
                enable_adversarial_verification=False,  # Causal reasoning only
                use_dempster_shafer=False,  # Causal reasoning only
            )
        return self._engine

    def _check_ram(self) -> bool:
        """Check if RAM usage is below threshold."""
        try:
            import psutil
            memory = psutil.virtual_memory()
            return memory.percent / 100.0 < RAM_THRESHOLD
        except ImportError:
            return True

    async def run_hypothesis_generation(
        self,
        findings: list[Any],
        sprint_id: str = "",
        output_dir: str | None = None,
    ) -> HypothesisResult:
        """
        Run hypothesis generation on findings using brain/hypothesis_engine.py.

        Args:
            findings: List of CanonicalFinding objects
            sprint_id: Sprint identifier
            output_dir: Optional output directory for STIX bundle

        Returns:
            HypothesisResult with execution metrics
        """
        start_time = time.time()

        if not HYPOTHESIS_ENABLED:
            return HypothesisResult(
                enabled=False,
                hypotheses_generated=0,
                entities_extracted=0,
                temporal_sequences=0,
                anomalies_detected=0,
                execution_time_s=time.time() - start_time,
                error="Hypothesis generation disabled (HLEDAC_ENABLE_HYPOTHESIS!=1)",
            )

        if not self._check_ram():
            return HypothesisResult(
                enabled=True,
                hypotheses_generated=0,
                entities_extracted=0,
                temporal_sequences=0,
                anomalies_detected=0,
                execution_time_s=time.time() - start_time,
                error="RAM usage above threshold (70%), skipping",
            )

        try:
            logger.info(f"HypothesisBuilder: starting for sprint {sprint_id} with {len(findings)} findings")

            # Generate causal hypotheses using brain/hypothesis_engine.py
            hypotheses = await self.engine.generate_causal_hypotheses(findings)

            # Detect anomalies
            anomalies = self.engine.detect_causal_anomalies(findings)

            # Get metrics
            entities_extracted = len(self.engine._causal_entities)
            temporal_sequences = len(self.engine._temporal_sequences)

            # Export STIX bundle if output_dir provided
            stix_path = None
            if output_dir and output_dir and hypotheses:
                stix_bundle = self._to_stix_bundle(hypotheses)
                import json
                import os
                os.makedirs(output_dir, exist_ok=True)
                stix_path = os.path.join(output_dir, f"hypotheses_{sprint_id}.json")
                with open(stix_path, "w") as f:
                    json.dump(stix_bundle, f, indent=2)
                logger.info(f"HypothesisBuilder: exported STIX bundle to {stix_path}")

            execution_time = time.time() - start_time
            logger.info(
                f"HypothesisBuilder: completed in {execution_time:.2f}s - "
                f"{len(hypotheses)} hypotheses, {entities_extracted} entities, "
                f"{temporal_sequences} sequences, {len(anomalies)} anomalies"
            )

            return HypothesisResult(
                enabled=True,
                hypotheses_generated=len(hypotheses),
                entities_extracted=entities_extracted,
                temporal_sequences=temporal_sequences,
                anomalies_detected=len(anomalies),
                execution_time_s=execution_time,
                stix_bundle_path=stix_path,
            )

        except Exception as e:
            logger.error(f"HypothesisBuilder: failed with {e}")
            return HypothesisResult(
                enabled=True,
                hypotheses_generated=0,
                entities_extracted=0,
                temporal_sequences=0,
                anomalies_detected=0,
                execution_time_s=time.time() - start_time,
                error=str(e),
            )

    def _to_stix_bundle(self, hypotheses: list[Any]) -> dict[str, Any]:
        """Convert hypotheses to STIX 2.1 relationship bundle."""
        import uuid
        from datetime import datetime, timezone

        bundle_id = f"bundle--{uuid.uuid4()}"
        objects = []

        # Add identities for entities
        entity_ids: set[str] = set()
        for hyp in hypotheses:
            entity_ids.add(hyp.source_entity)
            entity_ids.add(hyp.target_entity)

        for entity_id in entity_ids:
            identity_id = f"identity--{abs(hash(entity_id)) % (2**32)}"
            parts = entity_id.split("_", 1)
            entity_type = parts[0] if len(parts) > 1 else "unknown"
            objects.append({
                "type": "identity",
                "spec_version": "2.1",
                "id": identity_id,
                "created": datetime.now(timezone.utc).isoformat(),
                "modified": datetime.now(timezone.utc).isoformat(),
                "name": entity_id,
                "identity_class": entity_type,
            })

        # Add relationships for hypotheses
        rel_type_map = {
            "causal": "causes",
            "correlative": "related-to",
            "temporal": "preceded-by",
        }

        for hyp in hypotheses:
            rel_id = f"relationship--{uuid.uuid4()}"
            stix_rel_type = rel_type_map.get(hyp.hypothesis_type, "related-to")

            objects.append({
                "type": "relationship",
                "spec_version": "2.1",
                "id": rel_id,
                "created": datetime.now(timezone.utc).isoformat(),
                "modified": datetime.now(timezone.utc).isoformat(),
                "source_ref": f"identity--{abs(hash(hyp.source_entity)) % (2**32)}",
                "target_ref": f"identity--{abs(hash(hyp.target_entity)) % (2**32)}",
                "relationship_type": stix_rel_type,
                "description": hyp.statement,
                "confidence": int(hyp.confidence * 100),
            })

        return {
            "type": "bundle",
            "id": bundle_id,
            "spec_version": "2.1",
            "objects": objects,
        }

    def reset(self) -> None:
        """Reset builder state."""
        self._engine = None


async def run_hypothesis_if_enabled(
    findings: list[Any],
    sprint_id: str = "",
    output_dir: str | None = None,
) -> HypothesisResult:
    """
    Convenience function to run hypothesis generation if enabled.

    Args:
        findings: List of CanonicalFinding objects
        sprint_id: Sprint identifier
        output_dir: Optional output directory

    Returns:
        HypothesisResult
    """
    builder = HypothesisBuilder(output_dir=output_dir)
    return await builder.run_hypothesis_generation(findings, sprint_id, output_dir)


__all__ = [
    "HypothesisBuilder",
    "HypothesisResult",
    "run_hypothesis_if_enabled",
    "HYPOTHESIS_ENABLED",
    "RAM_THRESHOLD",
]