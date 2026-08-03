"""
R7: Canonical Async IOC Extraction — zero-copy batch via rayon channel
=======================================================================

Single entry point for all IOC extraction in Hledac Universal.
Replaces the scattered pattern of ``asyncio.to_thread(extract_iocs_flat, text)``
and ``asyncio.to_thread(extract_iocs_from_text, text)`` with a single,
zero-copy batch extraction that dispatches through Rust rayon pools.

Architecture
------------
  Caller (any module)
      │
      ├── extract_iocs_batch(texts)          → Tier 1: batch_ioc_extract_unified_python
      │                                          (zero-copy, Python list → Rust → Python list)
      │                                          dispatched via rayon_channel.dispatch_mixed
      │
      ├── extract_iocs_single(text)          → Tier 1 fallback for single texts
      │
      └── extract_iocs_from_findings(findings) → convenience: extracts from finding.text fields

TIERED FALLBACK (F266-2.3)
--------------------------
  Tier 1 (preferred):   batch_ioc_extract_unified_python(texts)
                        — Zero-copy: Python list[str] → Rust Vec → Python list[(type,value)]
                        — Dispatched via rayon mixed_pool (adaptive 1-2 threads)
                        — Available when: hledac_rust_extensions compiled + Python 3.12+

  Tier 2 (fallback):    batch_ioc_extract_unified(texts)
                        — rayon Vec return, still zero-copy but less efficient
                        — Available when: hledac_rust_extensions compiled (older)

  Tier 3 (final):       Per-text extract_iocs_from_text (pure Python)
                        — Always available, no Rust dep

USAGE
-----
  # Before (R7 anti-pattern):
  from hledac.universal.rust.ioc import extract_iocs_flat
  iocs = await asyncio.to_thread(extract_iocs_flat, text)

  # After (R7 preferred):
  from hledac.universal.utils.ioc_extract import extract_iocs_batch
  iocs_batch = await extract_iocs_batch([text1, text2, text3])

M1 8GB SAFETY
-------------
  - TEXT_MAX_BYTES = 65536 per text (Rust-side cap)
  - Batch size: auto-clamped to 4096 texts
  - Zero-copy: no intermediate Python allocations
  - Fail-soft: any error → empty list, never raises
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BATCH_SIZE = 4096  # F266-U5 calibrated for M1 8GB
TEXT_MAX_BYTES = 65536  # Rust-side cap (ioc_extract_fast.rs)

# ---------------------------------------------------------------------------
# Lazy Rust backend resolution — cached after first call
# ---------------------------------------------------------------------------

_tier1_func: Any = None
_tier2_func: Any = None
_resolved: bool = False


def _resolve_backends() -> None:
    """Resolve Rust IOC extraction backends — cached after first call."""
    global _tier1_func, _tier2_func, _resolved
    if _resolved:
        return
    _resolved = True
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal.core.rust_backend import rust
    _tier1_func = rust.raw.batch_ioc_extract_unified_python
    _tier2_func = rust.raw.batch_ioc_extract_unified
    if _tier1_func is not None or _tier2_func is not None:
        logger.debug("ioc_extract: Rust backends resolved (tier1=%s, tier2=%s)",
                     _tier1_func is not None, _tier2_func is not None)
    else:
        logger.debug("ioc_extract: hledac_rust_extensions not available, using pure Python fallback")


# ---------------------------------------------------------------------------
# Pure Python fallback
# ---------------------------------------------------------------------------


def _extract_iocs_python(texts: list[str]) -> list[list[tuple[str, str]]]:
    """Pure Python per-text IOC extraction — Tier 3 fallback."""
    try:
        from hledac.universal.pipeline.public_patterns import extract_iocs_from_text
        return [extract_iocs_from_text(t) for t in texts]
    except Exception:
        logger.debug("ioc_extract: pure Python fallback failed", exc_info=True)
        return [[] for _ in texts]


# ---------------------------------------------------------------------------
# Sync batch extraction (dispatched via rayon channel)
# ---------------------------------------------------------------------------


def _extract_iocs_sync(texts: list[str]) -> list[list[tuple[str, str]]]:
    """Synchronous batch IOC extraction — called on rayon pool thread.

    This function is dispatched via rayon_channel.dispatch_mixed().
    It runs on a Rust rayon pool thread with GIL released during
    the zero-copy Rust extraction.
    """
    _resolve_backends()

    # Clamp batch size
    if len(texts) > MAX_BATCH_SIZE:
        texts = texts[:MAX_BATCH_SIZE]

    # Tier 1: Zero-copy Python → Rust → Python
    if _tier1_func is not None:
        try:
            return _tier1_func(texts)
        except Exception:
            logger.debug("ioc_extract: tier1 (zero-copy) failed, trying tier2", exc_info=True)

    # Tier 2: rayon Vec return
    if _tier2_func is not None:
        try:
            return _tier2_func(texts)
        except Exception:
            logger.debug("ioc_extract: tier2 (rayon Vec) failed, falling back to Python", exc_info=True)

    # Tier 3: Pure Python
    return _extract_iocs_python(texts)


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def extract_iocs_batch(
    texts: list[str],
    *,
    timeout: float | None = None,
) -> list[list[tuple[str, str]]]:
    """Extract IOCs from a batch of texts via Rust rayon mixed_pool.

    This is the **canonical** entry point for all IOC extraction.
    All call sites should migrate to this function.

    Args:
        texts: List of text strings to extract IOCs from.
               Each text is capped at TEXT_MAX_BYTES (65536) in Rust.
               Batch is clamped to MAX_BATCH_SIZE (4096).
        timeout: Optional deadline in seconds. None = no timeout.

    Returns:
        List of lists: texts[i] → [(ioc_type, ioc_value), ...].
        Always returns a list of the same length as input.
        Errors return empty sub-lists — never raises.

    Example:
        iocs = await extract_iocs_batch(
            ["Contact admin@example.com", "Server 192.168.1.1"],
            timeout=30.0,
        )
        # iocs[0] → [("email", "admin@example.com")]
        # iocs[1] → [("ipv4", "192.168.1.1")]
    """
    if not texts:
        return []

    # Dispatch to rayon mixed_pool via channel
    try:
        from hledac.universal.utils.rayon_channel import dispatch_mixed
        result = await dispatch_mixed(
            len(texts),
            _extract_iocs_sync,
            texts,
            timeout=timeout,
        )
        if result is None:
            return [[] for _ in texts]
        return result
    except Exception:
        logger.debug("ioc_extract: rayon dispatch failed, using direct fallback", exc_info=True)
        try:
            return _extract_iocs_sync(texts)
        except Exception:
            return [[] for _ in texts]


async def extract_iocs_single(
    text: str,
    *,
    timeout: float | None = None,
) -> list[tuple[str, str]]:
    """Extract IOCs from a single text string.

    Convenience wrapper around ``extract_iocs_batch`` for single-text use.
    Prefer ``extract_iocs_batch`` when you have multiple texts.

    Args:
        text: Text string to extract IOCs from.
        timeout: Optional deadline in seconds.

    Returns:
        List of (ioc_type, ioc_value) tuples. Empty list on error or no matches.
    """
    if not text:
        return []
    results = await extract_iocs_batch([text], timeout=timeout)
    return results[0] if results else []


async def extract_iocs_from_findings(
    findings: list[Any],
    *,
    text_attr: str = "text",
    timeout: float | None = None,
) -> list[list[tuple[str, str]]]:
    """Extract IOCs from a list of finding objects.

    Convenience for pipeline code that works with CanonicalFinding objects.

    Args:
        findings: List of finding objects with a text attribute.
        text_attr: Name of the attribute containing text (default "text").
        timeout: Optional deadline in seconds.

    Returns:
        List of lists: findings[i] → [(ioc_type, ioc_value), ...].
    """
    texts = [getattr(f, text_attr, "") or "" for f in findings]
    return await extract_iocs_batch(texts, timeout=timeout)


__all__ = [
    "extract_iocs_batch",
    "extract_iocs_single",
    "extract_iocs_from_findings",
    "MAX_BATCH_SIZE",
    "TEXT_MAX_BYTES",
]
