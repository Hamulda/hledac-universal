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

MAX_BATCH_SIZE = 4096  # F266-U5 calibrated for M1 8GB
TEXT_MAX_BYTES = 65536  # Rust-side cap (ioc_extract_fast.rs)

# ADVERSARY-003: CyberChef-Pipeline deobfuscation
# HLEDAC_ENABLE_DEOBFUSCATE=0 to opt out (default ON)
_DEOBFUSCATE_ENABLED: bool | None = None
_decode_fn: Any = None
_decode_resolved: bool = False


def _is_deobfuscate_enabled() -> bool:
    """Check if deobfuscation is enabled (cached)."""
    global _DEOBFUSCATE_ENABLED
    if _DEOBFUSCATE_ENABLED is None:
        import os as _os

        val = _os.environ.get("HLEDAC_ENABLE_DEOBFUSCATE", "1")
        _DEOBFUSCATE_ENABLED = val not in ("0", "false", "False", "no")
    return _DEOBFUSCATE_ENABLED


def _resolve_deobfuscate() -> Any:
    """Lazily resolve decode_ioc_candidates from Rust backend."""
    global _decode_fn, _decode_resolved
    if _decode_resolved:
        return _decode_fn
    _decode_resolved = True
    try:
        from hledac.universal._core.rust_backend import rust

        if rust.is_available and hasattr(rust, "ioc"):
            _decode_fn = getattr(rust.ioc, "decode_ioc_candidates", None)
        else:
            _decode_fn = None
    except Exception:
        _decode_fn = None
    return _decode_fn


def _deobfuscate_texts(texts: list[str]) -> list[str]:
    """Deobfuscate texts via CyberChef-Pipeline (ADVERSARY-003).

    Sliding-window entropy probe → recursive decode ladder (Base64/Hex/Base58/URL/ROT13/XOR).
    Decoded candidates are appended to the original text as a space-separated string,
    preserving the original content for IOC scanning.

    Returns texts with decoded candidates appended. If deobfuscation is disabled or
    unavailable, returns texts unchanged.
    """
    if not _is_deobfuscate_enabled():
        return texts
    decode_fn = _resolve_deobfuscate()
    if decode_fn is None:
        return texts

    # ADVERSARY-003: batch deobfuscation — single GIL acquisition, serial on rayon thread
    try:
        results = decode_fn(texts, max_depth=3)  # type: ignore[call-arg]
        # results: list[DeobfuscateResult] or list[list[str]]
        augmented: list[str] = []
        for r in results:
            if hasattr(r, "candidates"):
                candidates: list[str] = r.candidates
            elif isinstance(r, list):
                candidates = r
            else:
                candidates = []
            if candidates:
                # Append decoded candidates as space-separated string
                augmented.append(" ".join(candidates))
            else:
                augmented.append("")
        # Merge augmented candidates into original texts
        return [f"{text} {suffix}".strip() if suffix else text for text, suffix in zip(texts, augmented, strict=False)]
    except Exception:
        logger.debug("ioc_extract: deobfuscation failed, proceeding without it", exc_info=True)
        return texts


# Re-export DedupBloom singleton from canonical wiring module.
# Production code uses _get_dedup_bloom() in knowledge/ioc_processor.py instead.
from rust_extensions.wiring.dedup_bloom_wiring import get_dedup_bloom as get_dedup_bloom_singleton

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
    from hledac.universal._core.rust_backend import rust

    _tier1_func = rust.raw.batch_ioc_extract_unified_python
    _tier2_func = rust.raw.batch_ioc_extract_unified
    if _tier1_func is not None or _tier2_func is not None:
        logger.debug(
            "ioc_extract: Rust backends resolved (tier1=%s, tier2=%s)", _tier1_func is not None, _tier2_func is not None
        )
    else:
        logger.debug("ioc_extract: hledac_rust_extensions not available, using pure Python fallback")


def _extract_iocs_python(texts: list[str]) -> list[list[tuple[str, str]]]:
    """Pure Python per-text IOC extraction — Tier 3 fallback."""
    try:
        from hledac.universal.pipeline.public_patterns import extract_iocs_from_text

        return [extract_iocs_from_text(t) for t in texts]
    except Exception:
        logger.debug("ioc_extract: pure Python fallback failed", exc_info=True)
        return [[] for _ in texts]


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

    # ADVERSARY-003: CyberChef-Pipeline — deobfuscate BEFORE extraction.
    # Decoded candidates are appended to texts so the IOC scanner (Rust or Python)
    # scans both the original content and the decoded payloads in a single pass.
    texts = _deobfuscate_texts(texts)

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
    "get_dedup_bloom_singleton",
    "MAX_BATCH_SIZE",
    "TEXT_MAX_BYTES",
]
