"""
feed_pipeline_wrapper.py — Rust feed_entry_pipeline integration.

Wraps the Rust feed_entry_pipeline function for use in live_feed_pipeline.py.
Provides:
- Single-call parse + scan + dedup pipeline
- Fallback to Python implementation when Rust unavailable
- Compatible with existing _entry_to_pattern_findings interface

Issue E2: Pipeline overlap — eliminates 4-stage Python pipeline overhead.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

# Lazy import to avoid hard dependency at module load
_RUST_FEED_PIPELINE: Any | None = None
_RUST_FEED_BATCH_PIPELINE: Any | None = None
_FEED_PIPELINE_AVAILABLE: bool = False

def _try_import_rust_feed_pipeline() -> bool:
    """Try to import Rust feed pipeline functions. Returns True if available."""
    global _RUST_FEED_PIPELINE, _RUST_FEED_BATCH_PIPELINE, _FEED_PIPELINE_AVAILABLE
    if _FEED_PIPELINE_AVAILABLE:
        return True
    
    try:
        from hledac_rust_extensions import feed_entry_pipeline, feed_batch_pipeline
        _RUST_FEED_PIPELINE = feed_entry_pipeline
        _RUST_FEED_BATCH_PIPELINE = feed_batch_pipeline
        _FEED_PIPELINE_AVAILABLE = True
        return True
    except ImportError:
        _FEED_PIPELINE_AVAILABLE = False
        return False


def feed_entry_pipeline_fast(
    raw_xml: str,
    *,
    max_entries: int = 0,
    patterns: list[str],
    labels: list[str],
) -> list[tuple[int, str, list[tuple[int, int, str, str, str]], int, int, str]]:
    """
    Unified parse + scan + dedup via Rust feed_entry_pipeline.
    
    Replaces the 4-stage Python pipeline:
      1. Parse XML → entries (Python selectolax)
      2. Fetch article text (I/O)
      3. Scan patterns (Python → Rust)
      4. Dedup (Python dict)
    
    With single Rust call:
      - quick-xml parsing (no GIL, ~2-4ms)
      - Aho-Corasick scan (SIMD-accelerated, rayon parallel)
      - Inline dedup via HashSet
    
    Args:
        raw_xml: Raw RSS/Atom XML string
        max_entries: Maximum entries to process (0 = all)
        patterns: List of pattern strings for Aho-Corasick
        labels: Parallel list of labels for each pattern
    
    Returns:
        List of tuples: (entry_idx, entry_url, combined_hits, title_hits_count, desc_hits_count, assembly_phase)
        
        combined_hits: List of (start, end, pattern, label, value) tuples
    """
    if not _FEED_PIPELINE_AVAILABLE:
        _try_import_rust_feed_pipeline()
    
    if _RUST_FEED_PIPELINE is not None:
        return _RUST_FEED_PIPELINE(raw_xml, max_entries, patterns, labels)
    
    # Fallback: return empty list (caller handles via Python pipeline)
    return []


def feed_batch_pipeline_fast(
    feeds: list[tuple[str, int]],  # List of (xml, max_entries)
    patterns: list[str],
    labels: list[str],
) -> list[list[tuple[int, str, list[tuple[int, int, str, str, str]], int, int, str]]]:
    """
    Batch version — process multiple feeds in parallel via rayon.
    
    Args:
        feeds: List of (xml, max_entries) tuples
        patterns: List of pattern strings
        labels: Parallel list of labels
    
    Returns:
        List of feed results (same as feed_entry_pipeline_fast per feed)
    """
    if not _FEED_PIPELINE_AVAILABLE:
        _try_import_rust_feed_pipeline()
    
    if _RUST_FEED_BATCH_PIPELINE is not None:
        return _RUST_FEED_BATCH_PIPELINE(feeds, patterns, labels)
    
    # Fallback: return empty batch
    return [[] for _ in feeds]


def is_feed_pipeline_available() -> bool:
    """Check if Rust feed pipeline is available."""
    if not _FEED_PIPELINE_AVAILABLE:
        _try_import_rust_feed_pipeline()
    return _FEED_PIPELINE_AVAILABLE
