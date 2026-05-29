"""
Temporal analysis: drift detection and archive fallback.
"""

import logging
import time
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# In‑memory cache of previous versions (simplified)
_previous_versions: dict[str, dict[str, Any]] = {}

# Constants for boundedness
MAX_ARCHIVE_FALLBACKS_PER_RUN = 5
MAX_TRACKED_URLS = 5000
_archive_fallback_count = 0


def record_previous_version(url: str, content_hash: str, title: str) -> None:
    """Store previous version data for a URL."""
    _previous_versions[url] = {
        "content_hash": content_hash,
        "title": title,
        "timestamp": time.time()
    }
    # Enforce boundedness: evict oldest if over limit
    if len(_previous_versions) > MAX_TRACKED_URLS:
        try:
            oldest_url = min(
                _previous_versions.keys(),
                key=lambda u: _previous_versions[u].get("timestamp", float('inf'))
            )
            del _previous_versions[oldest_url]
        except Exception:
            # Fail-safe: just clear oldest entries if min fails
            try:
                urls_to_remove = list(_previous_versions.keys())[:len(_previous_versions) - MAX_TRACKED_URLS]
                for u in urls_to_remove:
                    del _previous_versions[u]
            except Exception:
                pass


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
