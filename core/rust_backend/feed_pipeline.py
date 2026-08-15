# feed_pipeline.py — Rust feed pipeline domain
"""
Rust-backed unified RSS/Atom parse + scan + dedup pipeline.


feed_entry_pipeline: parse XML, Aho-Corasick scan, dedup in one rayon-parallel pass.
feed_batch_pipeline: batch process multiple feeds in parallel.

Replaces Python live_feed_pipeline.py parse+scan+dedup path with
Rust Aho-Corasick automaton + rayon parallelism — ~10-50× faster for
high-volume feed sources (1000+ entries).

M1 8GB: rayon mixed_pool (1-2 threads), bounded HashSet dedup.
GIL released during parallel scan via _py.allow_threads().
"""

from __future__ import annotations

from typing import Any


def get_domain() -> "FeedPipelineDomain":
    from hledac.universal.rust_extensions import hledac_rust_extensions as _ext

    _probe = getattr(_ext, "feed_entry_pipeline", None)
    if _probe is None:
        msg = "hledac_rust_extensions.feed_entry_pipeline not available"
        raise ImportError(msg)
    return FeedPipelineDomain(_ext)


# Type for a single entry result from Rust
# (entry_idx, entry_url, combined_hits, 0, 0, assembly_phase)
FeedEntryResult = tuple[int, str, list[tuple[int, int, str, str, str]], int, int, str]
FeedResults = list[FeedEntryResult]


class FeedPipelineDomain:
    """Rust-backed RSS/Atom parse + scan + dedup pipeline."""

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def entry_pipeline(
        self,
        raw_xml: str,
        max_entries: int,
        patterns: list[str],
        labels: list[str],
    ) -> FeedResults:
        """Process a single feed: parse XML, scan for patterns, dedup by GUID.

        Args:
            raw_xml: RSS or Atom XML content as string.
            max_entries: Maximum entries to process (0 = all).
            patterns: List of Aho-Corasick patterns to search for.
            labels: Labels for each pattern (parallel list).

        Returns:
            List of (entry_idx, entry_url, combined_hits, 0, 0, assembly_phase)
            where combined_hits is list of (start, end, pattern, label, value).
        """
        return self._ext.feed_entry_pipeline(raw_xml, max_entries, patterns, labels)

    def batch_pipeline(
        self,
        feeds: list[tuple[str, int]],
        patterns: list[str],
        labels: list[str],
    ) -> list[FeedResults]:
        """Process multiple feeds in rayon-parallel batches.

        Args:
            feeds: List of (raw_xml, max_entries) tuples.
            patterns: Aho-Corasick patterns (shared across feeds).
            labels: Labels for each pattern (shared across feeds).

        Returns:
            List of FeedResults (one per feed, in same order as feeds).
        """
        return self._ext.feed_batch_pipeline(feeds, patterns, labels)


# ---------------------------------------------------------------------------
# Python fallback — pure Python implementation
# ---------------------------------------------------------------------------

import re
from collections import deque
from core._util import aclose


class PythonFallbackFeedPipelineDomain:
    """Pure-Python fallback for feed pipeline."""

    __slots__ = ()

    RSS_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.DOTALL | re.IGNORECASE)
    RSS_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.DOTALL | re.IGNORECASE)
    RSS_LINK_RE = re.compile(r"<link>(.*?)</link>", re.DOTALL | re.IGNORECASE)
    RSS_DESC_RE = re.compile(r"<description>(.*?)</description>", re.DOTALL | re.IGNORECASE)
    RSS_GUID_RE = re.compile(r"<guid>(.*?)</guid>", re.DOTALL | re.IGNORECASE)
    ATOM_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.DOTALL | re.IGNORECASE)

    def entry_pipeline(
        self,
        raw_xml: str,
        max_entries: int,
        patterns: list[str],
        labels: list[str],
    ) -> FeedResults:
        import html

        # Detect RSS vs Atom
        is_atom = bool(self.ATOM_ENTRY_RE.search(raw_xml))
        item_re = self.ATOM_ENTRY_RE if is_atom else self.RSS_ITEM_RE

        seen_guids: set[str] = set()
        results: FeedResults = []

        for match in item_re.finditer(raw_xml):
            if max_entries > 0 and len(results) >= max_entries:
                break

            item_xml = match.group(1)
            title = html.unescape(self._extract(item_xml, self.RSS_TITLE_RE))
            link = html.unescape(self._extract(item_xml, self.RSS_LINK_RE))
            description = html.unescape(self._extract(item_xml, self.RSS_DESC_RE))
            guid = html.unescape(self._extract(item_xml, self.RSS_GUID_RE) or link)

            guid_lower = guid.lower()
            if guid_lower in seen_guids:
                continue
            seen_guids.add(guid_lower)

            # Scan patterns
            assembly_text = f"{title}\n\n{description}".lower() if description else title.lower()
            combined_hits: list[tuple[int, int, str, str, str]] = []
            for i, pat in enumerate(patterns):
                label = labels[i] if i < len(labels) else ""
                for m in re.finditer(re.escape(pat), assembly_text):
                    combined_hits.append((m.start(), m.end(), pat, label, m.group()))
                    break  # Only first occurrence per pattern

            entry_url = link or f"urn:feed:entry:{title[:64]}"
            assembly_phase = "title_description" if description else "title_only"
            results.append((len(results), entry_url, combined_hits, 0, 0, assembly_phase))

        return results

    def _extract(self, xml: str, pattern: re.Pattern) -> str:
        m = pattern.search(xml)
        return m.group(1).strip() if m else ""

    def batch_pipeline(
        self,
        feeds: list[tuple[str, int]],
        patterns: list[str],
        labels: list[str],
    ) -> list[FeedResults]:
        return [self.entry_pipeline(xml, max_e, patterns, labels) for xml, max_e in feeds]
