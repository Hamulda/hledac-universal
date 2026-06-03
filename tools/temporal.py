"""
Temporal analysis: drift detection and archive fallback.
"""

import logging
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# In‑memory cache of previous versions (simplified).
# OrderedDict provides O(1) insertion-order iteration for LRU eviction
# without the float('inf') loophole the previous min(key=...) approach had
# (entries missing a timestamp were unevictable because float('inf') is the
# max, not the min, of the key).
_previous_versions: "OrderedDict[str, dict[str, Any]]" = OrderedDict()

# Constants for boundedness
MAX_ARCHIVE_FALLBACKS_PER_RUN = 5
MAX_TRACKED_URLS = 5000
_archive_fallback_count = 0


def _evict_oldest() -> None:
    """O(1) FIFO eviction. Fail-soft: any error is logged, never raised."""
    try:
        # next(iter(...)) is the first-inserted key in O(1)
        oldest_url = next(iter(_previous_versions))
        del _previous_versions[oldest_url]
    except (StopIteration, KeyError):
        # Empty dict — nothing to evict
        pass
    except Exception as e:
        logger.warning(f"_previous_versions eviction failed: {e}")


def record_previous_version(url: str, content_hash: str, title: str) -> None:
    """Store previous version data for a URL."""
    # Update existing entry + bump to most-recently-inserted
    if url in _previous_versions:
        _previous_versions.move_to_end(url)
    _previous_versions[url] = {
        "content_hash": content_hash,
        "title": title,
        "timestamp": time.time()
    }
    # Enforce boundedness: evict oldest if over limit.
    # O(1) per eviction via OrderedDict insertion-order iteration.
    while len(_previous_versions) > MAX_TRACKED_URLS:
        _evict_oldest()


def detect_drift(url: str, current_content_hash: str, current_title: str) -> dict[str, Any] | None:
    """
    Compare with previous version. Return drift info if changed, else None.
    """
    prev = _previous_versions.get(url)
    if not prev:
        return None
    changes = {}
    if prev.get("content_hash") != current_content_hash:
        changes["content_hash"] = [prev.get("content_hash"), current_content_hash]
    if prev.get("title") != current_title:
        changes["title"] = [prev.get("title"), current_title]
    if changes:
        return {
            "url": url,
            "previous": prev,
            "current": {"content_hash": current_content_hash, "title": current_title},
            "changes": changes,
            "timestamp": time.time()
        }
    return None


def should_trigger_archive_fallback() -> bool:
    """Check if we haven't exceeded the limit."""
    global _archive_fallback_count
    return _archive_fallback_count < MAX_ARCHIVE_FALLBACKS_PER_RUN


def increment_archive_fallback() -> None:
    """Increment the counter (call only when actually performing fallback)."""
    global _archive_fallback_count
    _archive_fallback_count += 1


def is_high_value_url(url: str) -> bool:
    """Heuristic to detect high‑value URLs (archive, .gov, .edu, wikipedia)."""
    domain = urlparse(url).netloc.lower()
    # Archive domains
    if any(a in domain for a in ["web.archive.org", "archive.today", "archive.org"]):
        return True
    # Government/education domains - includes .gov.uk, .gov.au, etc.
    if ".gov" in domain or domain.endswith(".edu") or "wikipedia.org" in domain:
        return True
    return False


def reset_temporal_counters() -> None:
    """Reset counters (for testing)."""
    global _archive_fallback_count
    _archive_fallback_count = 0
    _previous_versions.clear()
