"""
Metadata Deduplicator - Late-stage deduplication refinement using metadata fields.

Operates on small metadata dicts:
- url, canonical_url, title, description
- og:title, og:description, jsonld_types, published_at

Uses binning to reduce O(n^2) comparisons:
- Domain-based binning
- Simhash bucket binning
- Capped total comparisons

Stores minimal info to Decision Ledger:
- winner evidence_id/url, loser hash, score, top_field_reasons
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)

# Constants
TOP_K = 200  # Top candidates for metadata dedup
MAX_COMPARISONS = 50_000  # Cap comparisons per run
MAX_FIELD_REASONS = 5  # Max field reasons to store


@dataclass
class MetadataEntry:
    """A single metadata entry for deduplication."""
    url: str
    canonical_url: str = ""
    title: str = ""
    description: str = ""
    og_title: str = ""
    og_description: str = ""
    jsonld_types: list[str] = field(default_factory=list)
    published_at: str = ""
    evidence_id: str = ""  # For linking to evidence

    @property
    def domain(self) -> str:
        """Extract domain from URL."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(self.url)
            return parsed.netloc.lower() or parsed.path.lower()
        except Exception:
            return ""

    @property
    def hash(self) -> str:
        """Generate stable hash for this entry."""
        data = f"{self.canonical_url or self.url}|{self.title}|{self.description}"
        return hashlib.sha256(data.encode()).hexdigest()[:16]


@dataclass
class DedupResult:
    """Result of metadata deduplication."""
    winner: str  # evidence_id or url of winner
    loser_hash: str  # hash of loser
    score: float
    field_reasons: list[str]  # Why these match
    winner_url: str
    loser_url: str


class MetadataDeduplicator:
    """
    Metadata-based deduplication for late-stage refinement.

    Uses weighted field comparison with binning for efficiency:
    - Group by domain first
    - Then compare within domain
    - Cap total comparisons
    """

    # Field weights for similarity scoring
    FIELD_WEIGHTS = {
        "canonical_url": 2.0,
        "title": 1.5,
        "description": 1.0,
        "og_title": 1.5,
        "og_description": 1.0,
        "jsonld_types": 0.5,
        "published_at": 0.3,
    }

    # Syndication patterns (same content, different source)
    SYNDICATION_PATTERNS = [
        r'syndication',
        r'feeds?',
        r'atom',
        r'rss',
        r'republish',
        r'share',
    ]

    def __init__(
        self,
        top_k: int = TOP_K,
        max_comparisons: int = MAX_COMPARISONS,
        threshold: float = 0.85
    ):
        """
        Initialize metadata deduplicator.

        Args:
            top_k: Number of top candidates to consider
            max_comparisons: Cap comparisons per run
            threshold: Minimum similarity to consider duplicate
        """
        self.top_k = top_k
        self.max_comparisons = max_comparisons
        self.threshold = threshold
        self.logger = logging.getLogger(__name__)

    def _parse_metadata(self, data: dict[str, Any]) -> MetadataEntry:
        """Parse metadata dict into MetadataEntry."""
        entry = MetadataEntry(
            url=data.get("url", ""),
            canonical_url=data.get("canonical_url", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            og_title=data.get("og:title", ""),
            og_description=data.get("og:description", ""),
            jsonld_types=data.get("jsonld_types", []),
            published_at=data.get("published_at", ""),
            evidence_id=data.get("evidence_id", "")
        )

        # Also check alternate keys
        if not entry.og_title:
            entry.og_title = data.get("og_title", "")
        if not entry.og_description:
            entry.og_description = data.get("og_description", "")

        return entry

    def _compute_similarity(
        self,
        a: MetadataEntry,
        b: MetadataEntry
    ) -> tuple[float, list[str]]:
        """
        Compute weighted similarity between two metadata entries.

        Returns:
            Tuple of (score, list of field reasons)
        """
        field_reasons = []
        weighted_sum = 0.0
        total_weight = 0.0

        # Compare canonical URLs
        if a.canonical_url and b.canonical_url:
            weight = self.FIELD_WEIGHTS["canonical_url"]
            if a.canonical_url == b.canonical_url:
                weighted_sum += weight
                field_reasons.append("canonical_url:exact")
            total_weight += weight

        # Compare titles
        if a.title and b.title:
            weight = self.FIELD_WEIGHTS["title"]
            score = self._text_similarity(a.title, b.title)
            if score > 0.8:
                weighted_sum += score * weight
                field_reasons.append(f"title:{score:.2f}")
            total_weight += weight

        # Compare descriptions
        if a.description and b.description:
            weight = self.FIELD_WEIGHTS["description"]
            score = self._text_similarity(a.description, b.description)
            if score > 0.6:
                weighted_sum += score * weight
                field_reasons.append(f"description:{score:.2f}")
            total_weight += weight

        # Compare OG title
        if a.og_title and b.og_title:
            weight = self.FIELD_WEIGHTS["og_title"]
            score = self._text_similarity(a.og_title, b.og_title)
            if score > 0.8:
                weighted_sum += score * weight
                field_reasons.append(f"og_title:{score:.2f}")
            total_weight += weight

        # Compare OG description
        if a.og_description and b.og_description:
            weight = self.FIELD_WEIGHTS["og_description"]
            score = self._text_similarity(a.og_description, b.og_description)
            if score > 0.6:
                weighted_sum += score * weight
                field_reasons.append(f"og_description:{score:.2f}")
            total_weight += weight

        # Compare JSON-LD types
        if a.jsonld_types and b.jsonld_types:
            weight = self.FIELD_WEIGHTS["jsonld_types"]
            score = self._list_similarity(a.jsonld_types, b.jsonld_types)
            if score > 0.5:
                weighted_sum += score * weight
                field_reasons.append(f"jsonld_types:{score:.2f}")
            total_weight += weight

        if total_weight == 0:
            return 0.0, []

        return weighted_sum / total_weight, field_reasons[:MAX_FIELD_REASONS]

    def _text_similarity(self, a: str, b: str) -> float:
        """Compute text similarity using SequenceMatcher."""
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def _list_similarity(self, a: list[str], b: list[str]) -> float:
        """Compute Jaccard similarity between two lists."""
        if not a or not b:
            return 0.0
        set_a = {str(x).lower() for x in a}
        set_b = {str(x).lower() for x in b}
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    def _is_syndication_variant(self, a: MetadataEntry, b: MetadataEntry) -> bool:
        """Check if entries are syndication variants of each other."""
        # Check domain difference
        if a.domain == b.domain:
            return False

        # Check if title and description are identical (common in syndication)
        if a.title == b.title and a.description == b.description:
            if a.title:  # Non-empty
                return True

        # Check URL patterns
        for pattern in self.SYNDICATION_PATTERNS:
            if re.search(pattern, a.url, re.IGNORECASE):
                if re.search(pattern, b.url, re.IGNORECASE):
                    return True

        return False

    def deduplicate(
        self,
        metadata_list: list[dict[str, Any]],
        log_callback: Callable[[DedupResult], None] | None = None
    ) -> list[DedupResult]:
        """
        Deduplicate metadata entries.

        Args:
            metadata_list: List of metadata dicts
            log_callback: Optional callback to log results to Decision Ledger

        Returns:
            List of DedupResult for duplicate pairs
        """
        if len(metadata_list) <= 1:
            return []

        # Parse entries
        entries = [self._parse_metadata(m) for m in metadata_list]

        # Filter to top_k (most relevant candidates)
        if len(entries) > self.top_k:
            entries = entries[:self.top_k]

        # Bin by domain
        domain_bins: dict[str, list[MetadataEntry]] = {}
        for entry in entries:
            domain = entry.domain
            if domain not in domain_bins:
                domain_bins[domain] = []
            domain_bins[domain].append(entry)

        results: list[DedupResult] = []
        comparisons = 0

        # Compare within each domain bin
        for domain, bin_entries in domain_bins.items():
            if comparisons >= self.max_comparisons:
                break

            for i, a in enumerate(bin_entries):
                if comparisons >= self.max_comparisons:
                    break

                for b in bin_entries[i + 1:]:
                    if comparisons >= self.max_comparisons:
                        break

                    comparisons += 1

                    # Compute similarity
                    score, reasons = self._compute_similarity(a, b)

                    if score >= self.threshold:
                        # Check if syndication variant
                        self._is_syndication_variant(a, b)

                        # Determine winner (by URL canonical form)
                        if a.canonical_url < b.canonical_url:
                            winner, loser = a, b
                        elif b.canonical_url < a.canonical_url:
                            winner, loser = b, a
                        else:
                            winner, loser = a, b

                        result = DedupResult(
                            winner=winner.evidence_id or winner.url,
                            loser_hash=loser.hash,
                            score=score,
                            field_reasons=reasons,
                            winner_url=winner.url,
                            loser_url=loser.url
                        )

                        results.append(result)

                        # Log if callback provided
                        if log_callback:
                            log_callback(result)

        self.logger.info(f"Metadata dedup: {len(results)} duplicates from {len(entries)} entries, {comparisons} comparisons")
        return results


def deduplicate_metadata(
    metadata_list: list[dict[str, Any]],
    threshold: float = 0.85
) -> list[DedupResult]:
    """
    Convenience function to deduplicate metadata.

    Args:
        metadata_list: List of metadata dicts
        threshold: Similarity threshold

    Returns:
        List of DedupResult
    """
    dedup = MetadataDeduplicator(threshold=threshold)
    return dedup.deduplicate(metadata_list)
