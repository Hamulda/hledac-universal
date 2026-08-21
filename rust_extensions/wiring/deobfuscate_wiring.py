"""
Deobfuscate Rust Integration Wiring
===================================

Wires rust_extensions/src/deobfuscate.rs to knowledge/ioc_processor.py.

Purpose:
- CyberChef-style IOC deobfuscation pipeline
- +25% recall on defanged/encoded IOC (phishing, paste sites)
- NEON SIMD entropy probe, rayon parallelization

Integration Point:
- knowledge/ioc_processor.py IOCProcessor.extract() and extract_batch()
- Called BEFORE IOC regex extraction to deobfuscate candidate text

Pipeline:
    1. Deobfuscate input text(s) → extract decoded candidates
    2. Run IOC regex extraction on both original + decoded text
    3. Merge results for maximum recall

Usage:
    from rust_extensions.wiring.deobfuscate_wiring import (
        deobfuscate_wired,
        batch_decode_ioc_candidates,
        decode_ioc_candidates,
    )

    # In IOCProcessor.extract():
    decoded = batch_decode_ioc_candidates([text])
    iocs = extract_iocs_from_text(text)
    iocs.extend(extract_iocs_from_text(decoded[0]))
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from rust_extensions.integrations import get_deobfuscate

_deobfuscate = get_deobfuscate()


def deobfuscate_wired():
    """Get the wired deobfuscate integration."""
    return _deobfuscate


def decode_ioc_candidates(text: str, max_depth: int = 3) -> list[str]:
    """
    Deobfuscate IOC candidates in a single text.

    Pipeline: entropy probe → try-decode ladder → recursive re-entry.

    Args:
        text: Raw text to deobfuscate (max 16 MB per call)
        max_depth: Maximum nesting depth (default 3)

    Returns:
        List of decoded IOC candidates found in the text.
    """
    return _deobfuscate.decode_ioc_candidates(text, max_depth)


def batch_decode_ioc_candidates(texts: list[str], max_depth: int = 3) -> list[list[str]]:
    """
    Deobfuscate IOC candidates in batch of texts (parallel via rayon).

    Args:
        texts: List of raw texts to deobfuscate (max 1000 per batch)
        max_depth: Maximum nesting depth (default 3)

    Returns:
        List of decoded candidate lists, one per input text (in order).
    """
    return _deobfuscate.batch_decode_ioc_candidates(texts, max_depth)


def get_telemetry() -> dict[str, int]:
    """
    Get deobfuscation telemetry counters.

    Returns:
        Dict with keys: passes, layers_stripped, bytes_decoded
    """
    return _deobfuscate.get_telemetry()


def reset_telemetry() -> None:
    """Reset telemetry counters (call at sprint boundary)."""
    _deobfuscate.reset_telemetry()


if _deobfuscate.available:
    logger.info("[Deobfuscate] Rust deobfuscate.rs integration: ENABLED")
else:
    logger.info("[Deobfuscate] Rust deobfuscate.rs integration: DISABLED (using Python fallback)")
