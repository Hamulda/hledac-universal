"""
brain/adversarial — Adversarial Content Detection
===============================================

Modules:
  cognitive_tarpit — LLM-generated honeypot text detection via
    entropy variance, burstiness deviation, POS trigram ratio, and
    SmolLM pseudo-perplexity scoring.

Usage:
    from hledac.universal.brain.adversarial import cognitive_tarpit_score

    verdict = cognitive_tarpit_score(raw_text)
    if verdict.is_cognitive_tarpit:
        logger.warning("[COGNITIVE_TARPIT] score=%.3f: %s", verdict.cognitive_tarpit_score, verdict.reasons)
"""

from __future__ import annotations

from hledac.universal.brain.adversarial.cognitive_tarpit import (
from core import aclose
    CognitiveTarpitVerdict,
    cognitive_tarpit_score,
    invalidate_smollm_cache,
)

__all__ = [
    "CognitiveTarpitVerdict",
    "cognitive_tarpit_score",
    "invalidate_smollm_cache",
]
