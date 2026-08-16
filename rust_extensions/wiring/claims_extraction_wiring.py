"""
Claims Extraction Rust Integration Wiring
=======================================

Wires rust_extensions/src/claims_extraction.rs to:
- brain/research_hypothesis_engine.py

Purpose:
- Sentence-level claim extraction
- Polarity detection (positive/negative/neutral)
- Confidence scoring for evidence

Integration Point:
- Hypothesis evidence processing
- Claim confidence in belief updating

Usage:
    from rust_extensions.wiring.claims_extraction_wiring import claims_extraction_wired
    
    claims = claims_extraction_wired.extract_claims(
        text,
        title="Article Title",
        source_type="ct_log"
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Import the integration layer
from rust_extensions.integrations import get_claims_extraction

# Create singleton instance
_claims_extraction = get_claims_extraction()


def claims_extraction_wired():
    """Get the wired claims extraction integration."""
    return _claims_extraction


def extract_claims(
    text: str,
    title: str = "",
    summary: str = "",
    source_type: str = "PUBLIC",
    evidence_type: str = "web_content",
) -> list[dict]:
    """
    Extract claims from text with polarity and confidence.

    Args:
        text: Input text to extract claims from
        title: Optional title for context
        summary: Optional summary for context
        source_type: Source type (CT, FEED, WAYBACK, STEALTH, PUBLIC)
        evidence_type: Evidence type (web_content, ct_log, document, etc.)

    Returns:
        List of claim dicts with keys:
        - text: Claim sentence
        - polarity: "positive" | "negative" | "neutral"
        - confidence: Confidence score [0.0, 1.0]
        - source: Source identifier
        - evidence_type: Type of evidence
    """
    return _claims_extraction.extract_claims(
        text, title, summary, source_type, evidence_type
    )


def extract_hypothesis_claims(
    evidence_text: str,
    hypothesis_statement: str,
    source_type: str = "PUBLIC",
) -> list[dict]:
    """
    Extract claims for hypothesis evidence evaluation.

    Args:
        evidence_text: The evidence text to analyze
        hypothesis_statement: The hypothesis being evaluated
        source_type: Source type for confidence scoring

    Returns:
        List of claims with confidence scores.
    """
    return extract_claims(
        evidence_text,
        title=hypothesis_statement,
        source_type=source_type,
        evidence_type="hypothesis_evidence",
    )


def compute_claim_confidence(
    claims: list[dict],
) -> float:
    """
    Compute aggregate confidence from claims.

    Args:
        claims: List of claim dicts

    Returns:
        Aggregate confidence score [0.0, 1.0]
    """
    if not claims:
        return 0.0

    # Weight by polarity
    polarity_weights = {
        "positive": 1.0,
        "neutral": 0.5,
        "negative": 0.8,
    }

    total_weight = 0.0
    weighted_sum = 0.0

    for claim in claims:
        polarity = claim.get("polarity", "neutral")
        confidence = claim.get("confidence", 0.45)
        weight = polarity_weights.get(polarity, 0.5)
        weighted_sum += confidence * weight
        total_weight += weight

    return weighted_sum / total_weight if total_weight > 0 else 0.0


# Check availability at import time for logging
if _claims_extraction.available:
    logger.info("[ClaimsExtraction] Rust claims_extraction.rs integration: ENABLED")
else:
    logger.info("[ClaimsExtraction] Rust claims_extraction.rs integration: DISABLED (using Python fallback)")
