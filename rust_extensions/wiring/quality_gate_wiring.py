"""
Quality Gate Rust Integration Wiring
===================================

Wires rust_extensions/src/quality_gate.rs to knowledge/quality_assessment.py.

Purpose:
- NEON-accelerated entropy computation
- Fast text normalization
- BLAKE2b-128 fingerprinting

Integration Point:
- knowledge/quality_assessment.py QualityAssessor class
- Replaces pure Python _compute_entropy, _normalize_for_quality, _dedup_fingerprint

Usage:
    from rust_extensions.wiring.quality_gate_wiring import quality_gate_wired
    
    # In QualityAssessor.assess():
    entropy = quality_gate_wired.compute_entropy(text)
    normalized = quality_gate_wired.normalize_quality_text(text)
    fingerprint = quality_gate_wired.dedup_fingerprint(text)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Import the integration layer
from rust_extensions.integrations import get_quality_gate

# Create singleton instance
_quality_gate = get_quality_gate()


def quality_gate_wired():
    """Get the wired quality gate integration."""
    return _quality_gate


def compute_entropy(text: str) -> float:
    """
    Compute Shannon entropy using Rust fast-path when available.

    Falls back to pure Python Counter-based implementation.
    """
    return _quality_gate.compute_entropy(text)


def normalize_text(text: str) -> str:
    """
    Normalize text for quality checks using Rust fast-path.

    Falls back to pure Python implementation.
    """
    return _quality_gate.normalize_quality_text(text)


def batch_entropy(texts: list[str]) -> list[float]:
    """
    Batch entropy computation using Rust rayon parallelization.

    Falls back to serial Python implementation.
    """
    return _quality_gate.batch_entropy(texts)


def dedup_fingerprint(text: str) -> str:
    """
    Compute BLAKE2b-128 hex fingerprint for deduplication.

    Falls back to Python hashlib.blake2b.
    """
    return _quality_gate.dedup_fingerprint(text)


def batch_dedup_fingerprint(texts: list[str]) -> list[str]:
    """
    Batch fingerprint computation using Rust rayon parallelization.

    Falls back to serial Python implementation.
    """
    return _quality_gate.batch_dedup_fingerprint_par(texts)


# Check availability at import time for logging
if _quality_gate.available:
    logger.info("[QualityGate] Rust quality_gate.rs integration: ENABLED")
else:
    logger.info("[QualityGate] Rust quality_gate.rs integration: DISABLED (using Python fallback)")
