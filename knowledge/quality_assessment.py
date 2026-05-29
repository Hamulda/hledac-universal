"""
Quality Assessment — Sprint F216G refactor
==========================================

ROLE: Quality gate delegate for DuckDBShadowStore.

Handles quality decision logic (entropy, dedup, URL-first fingerprinting),
rejection ledger, and quality counters. Separated from canonical write seam
to keep DuckDBShadowStore focused on sprint facts storage.

DEPENDENCIES (passed in, not imported):
    - CanonicalFinding, FindingQualityDecision (from duckdb_store)
    - LMDB dedup cache (interface only, duckdb_store manages lifecycle)
    - Semantic dedup cache (interface only, duckdb_store manages lifecycle)

CANONICAL WRITE PATH: Remains in DuckDBShadowStore.async_ingest_findings_batch().
This module provides quality decision helpers that DuckDBShadowStore delegates to.
"""

from __future__ import annotations

import hashlib
import logging as _logging
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .duckdb_store import CanonicalFinding, FindingQualityDecision

__all__ = [
    "QualityRejectionRecord",
    "QualityAssessmentState",
    "QualityAssessor",
    "_QUALITY_ENTROPY_THRESHOLD",
    "_QUALITY_MIN_ENTROPY_LEN",
    "_normalize_for_quality",
    "_compute_entropy",
    "_normalize_osint_url",
    "_compute_dedup_fingerprint",
    "_compute_url_fingerprint",
]


# ---------------------------------------------------------------------------
# Rust URL engine availability (Sprint F216R)
# ---------------------------------------------------------------------------
try:
    from hledac_rust_extensions import fingerprint as _rust_fingerprint
    from hledac_rust_extensions import normalize as _rust_normalize

    _URL_ENGINE_AVAILABLE = True
except ImportError:
    _URL_ENGINE_AVAILABLE = False
    _rust_normalize = None
    _rust_fingerprint = None


# ---------------------------------------------------------------------------
# Quality helper constants and functions (module-level, stateless)
# ---------------------------------------------------------------------------

# Sprint 8W: Configurable entropy threshold (bits per character)
_QUALITY_ENTROPY_THRESHOLD: float = 0.5
# Strings shorter than this skip entropy filtering
_QUALITY_MIN_ENTROPY_LEN: int = 8


def _normalize_for_quality(text: str) -> str:
    """
    Sprint 8W: Normalize text for entropy and dedup quality checks.

    Normalization rules:
      - lowercase
      - strip leading/trailing whitespace
      - collapse internal whitespace to single space (includes tabs/newlines)
      - remove non-printable chars (ord < 32) that are NOT whitespace

    Tabs and newlines (ord < 32) are whitespace and get collapsed to space first.
    Other non-printable chars (BEL, NUL, etc.) are removed after whitespace normalization.

    No stemming, lemmatization, transliteration, or locale-dependent logic.
    """
    lowered = text.lower()
    stripped = lowered.strip()
    normalized = " ".join(stripped.split())
    import string
    whitespace_chars = set(string.whitespace)
    cleaned = "".join(ch for ch in normalized if ord(ch) >= 32 or ch in whitespace_chars)
    return cleaned


def _compute_entropy(text: str) -> float:
    """
    Sprint 8W: Compute Shannon entropy in bits per character.

    Uses collections.Counter for efficiency (no Python for-loop over characters).
    Returns 0.0 for empty text.
    """
    if not text:
        return 0.0
    char_counts = Counter(text)
    total = len(text)
    entropy = 0.0
    for count in char_counts.values():
        p = count / total
        if p > 0:
            import math as _math
            entropy -= p * _math.log2(p)
    return entropy


def _normalize_osint_url(url: str) -> str:
    """
    Sprint 8AK: Normalize an OSINT URL for deterministic dedup fingerprinting.

    Rules:
      - lowercase scheme + host
      - strip fragment (#...)
      - strip trailing slash from non-root paths
      - remove common tracking query params (utm_source, utm_medium, utm_campaign, ref, etc.)
      - preserve query params that may affect content identity

    Returns normalized URL string.
    """
    if not url or not isinstance(url, str):
        return ""

    # Sprint F216R: Try Rust fast path first
    if _URL_ENGINE_AVAILABLE and _rust_normalize is not None:
        try:
            return _rust_normalize(url)
        except Exception:
            pass  # Fall through to Python implementation

    # Python fallback (original implementation)
    url = url.strip()

    try:
        parsed = urlparse(url)
    except Exception:
        return url

    scheme = parsed.scheme.lower() if parsed.scheme else "http"
    netloc = parsed.netloc.lower()
    fragment = ""
    path = parsed.path.rstrip("/") if len(parsed.path) > 1 else parsed.path

    TRACKING_QUERY_PARAMS = frozenset({
        "utm_source", "utm_medium", "utm_campaign",
        "utm_content", "utm_term",
        "fbclid",
        "ref",
    })
    try:
        query_params = parse_qsl(parsed.query, keep_blank_values=True)
        filtered = [(k, v) for k, v in query_params if k.lower() not in TRACKING_QUERY_PARAMS]
        query = urlencode(filtered) if filtered else ""
    except Exception:
        query = parsed.query

    normalized = f"{scheme}://{netloc}{path}"
    if query:
        normalized += f"?{query}"
    if fragment:
        normalized += f"#{fragment}"

    return normalized


def _compute_dedup_fingerprint(text: str) -> str:
    """
    Sprint 8W: Compute BLAKE2b-128 fingerprint of normalized text.

    Uses hashlib.blake2b (NOT Python built-in hash()).
    digest_size=16 → 32 hex chars.
    Stable across process restarts.
    """
    normalized = _normalize_for_quality(text)
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()


def _compute_url_fingerprint(url: str) -> str:
    """
    Sprint 8AK: URL-first dedup fingerprint.

    If a canonical URL is available in provenance, use it as the primary
    dedup signal (source-independent, deterministic). Falls back to
    BLAKE2b(text) when no URL is present.

    URL is normalized before fingerprinting per OSINT URL normalization rules.

    Returns 32-char hex BLAKE2b-128 fingerprint.

    Sprint F216R: Uses Rust url_engine.fingerprint (xxHash64 u64) when available,
    converting to hex string for backward compatibility with existing callers.
    """
    # Sprint F216R: Try Rust fast path for fingerprint
    if _URL_ENGINE_AVAILABLE and _rust_fingerprint is not None:
        try:
            fp = _rust_fingerprint(url)
            # Convert u64 to 16-char hex string (backward compatible)
            return format(fp, '016x')
        except Exception:
            pass  # Fall through to Python implementation

    # Python fallback: normalize then BLAKE2b
    normalized_url = _normalize_osint_url(url)
    if normalized_url:
        return hashlib.blake2b(normalized_url.encode("utf-8"), digest_size=16).hexdigest()
    return ""


# Sprint F216G: Quality Rejection Ledger
@dataclass(frozen=True, slots=True)
class QualityRejectionRecord:
    """
    Sprint F216G: Bounded per-finding quality gate rejection record.

    Records individual quality gate rejections for CanonicalFinding ingest,
    grouped by source_family and reason. Used to diagnose accepted=0
    without changing quality/dedup/storage behavior.

    Fields:
        source_family: source_type of the finding (e.g., "ct", "public", "wayback")
        reason:         FindingQualityDecision.reason (e.g., "low_entropy_rejected",
                       "persistent_duplicate", "semantic_duplicate")
        finding_id:     Bounded sample: first 40 chars of finding_id
        url_sample:      Bounded sample: provenance URL if available, else query (max 200 chars)
    """

    source_family: str
    reason: str
    finding_id: str
    url_sample: str


# Sprint 8AG §6.17: Persistent dedup config
_DEDUP_LMDB_MAP_SIZE: int = 64 * 1024 * 1024  # 64MB dedicated dedup LMDB
_DEDUP_HOT_CACHE_MAX: int = 10_000  # hard cap on in-memory dedup cache entries


class QualityAssessmentState:
    """
    Sprint F216G: Quality counters and rejection ledger state.

    Kept separate from DuckDBShadowStore so quality state is independently
    testable and can be inspected without accessing the full store.
    """

    __slots__ = (
        "_quality_rejected_count",
        "_quality_duplicate_count",
        "_quality_fail_open_count",
        "_persistent_duplicate_count",
        "_quality_rejection_ledger",
        "_MAX_QUALITY_REJECTION_LEDGER",
        "_accepted_count",
        "_dedup_fingerprints",
        "_dedup_hot_cache",
        "_dedup_hot_cache_order",
    )

    def __init__(self) -> None:
        # Sprint 8W: Quality gate counters (separate from storage counters)
        self._quality_rejected_count: int = 0
        self._quality_duplicate_count: int = 0  # in-memory / quality-layer duplicate count
        self._quality_fail_open_count: int = 0  # quality helper exception → fail-open

        # Sprint 8AK: Persistent duplicate counter (LMDB-backed, cross-source dedup)
        self._persistent_duplicate_count: int = 0

        # Sprint F216G: Quality Rejection Ledger — bounded per-finding rejection records
        # Used to diagnose accepted=0 by source_family and reason
        # Max 200 entries; oldest dropped when cap reached
        self._quality_rejection_ledger: list[QualityRejectionRecord] = []
        self._MAX_QUALITY_REJECTION_LEDGER: int = 200

        # Sprint 8AV: Accepted findings counter (quality gate passed → stored)
        self._accepted_count: int = 0

        # Sprint 8W: In-memory dedup set (key = BLAKE2b fingerprint, val = finding_id)
        # Hot cache only — LMDB is the authority for persistence across restarts
        self._dedup_fingerprints: dict[str, str] = {}

        # Bounded hot cache — hard limit to prevent unbounded memory growth
        self._dedup_hot_cache: dict[str, str] = {}  # fp → finding_id, bounded
        self._dedup_hot_cache_order: OrderedDict = OrderedDict()  # FIFO order for eviction

    def record_rejection(
        self,
        finding: CanonicalFinding,
        decision: FindingQualityDecision,
    ) -> None:
        """
        Sprint F216G: Record a quality gate rejection to the bounded ledger.

        Bounded: max 200 entries; oldest dropped when cap exceeded.
        No full payload text stored — only bounded samples.
        """
        if decision.accepted:
            return
        source_family = getattr(finding, "source_type", "unknown") or "unknown"
        url = self._extract_url_from_provenance(getattr(finding, "provenance", ()) or ())
        url_sample = url[:200] if url else (getattr(finding, "query", "") or "")[:200]
        record = QualityRejectionRecord(
            source_family=source_family,
            reason=decision.reason or "unknown",
            finding_id=(getattr(finding, "finding_id", "") or "")[:40],
            url_sample=url_sample,
        )
        self._quality_rejection_ledger.append(record)
        if len(self._quality_rejection_ledger) > self._MAX_QUALITY_REJECTION_LEDGER:
            self._quality_rejection_ledger.pop(0)

    def get_rejection_history(self) -> tuple[QualityRejectionRecord, ...]:
        """
        Sprint F216G: Expose the quality rejection ledger to callers (e.g. scheduler).

        Returns a tuple (immutable view) of all recorded rejection records.
        """
        return tuple(self._quality_rejection_ledger)

    def rejection_rate(self) -> float:
        """
        Sprint F216G: Compute rejection rate across all quality gate decisions.

        Returns fraction of rejected findings [0.0, 1.0].
        Returns 0.0 if no decisions have been recorded yet.
        """
        total = self._accepted_count + self._quality_rejected_count + self._quality_duplicate_count
        if total == 0:
            return 0.0
        return self._quality_rejected_count / total

    def _extract_url_from_provenance(self, provenance: tuple[str, ...]) -> str:
        """Extract the first HTTP(S) URL from a provenance tuple."""
        if not provenance:
            return ""
        for item in provenance:
            if isinstance(item, str) and item.startswith("http"):
                return item
        return ""

    # Hot cache helpers (used by QualityAssessor)
    def hot_cache_lookup(self, fingerprint: str) -> str | None:
        """Look up fingerprint in hot cache. Returns finding_id or None."""
        return self._dedup_hot_cache.get(fingerprint)

    def add_to_hot_cache(self, fingerprint: str, finding_id: str) -> None:
        """Add fingerprint → finding_id to hot cache with FIFO eviction."""
        if fingerprint in self._dedup_hot_cache:
            self._dedup_hot_cache_order.move_to_end(fingerprint)
            return
        if len(self._dedup_hot_cache) >= _DEDUP_HOT_CACHE_MAX:
            oldest = next(iter(self._dedup_hot_cache_order))
            del self._dedup_hot_cache[oldest]
            del self._dedup_hot_cache_order[oldest]
        self._dedup_hot_cache[fingerprint] = finding_id
        self._dedup_hot_cache_order[fingerprint] = None


class QualityAssessor:
    """
    Sprint 8W + 8AG + 8AK + F216G: Quality gate delegate.

    Encapsulates quality decision logic (entropy check, dedup, URL-first fingerprint).
    Delegates to DuckDBShadowStore for LMDB persistence and semantic dedup cache.

    DuckDBShadowStore holds this as an attribute and calls it from
    async_ingest_findings_batch() to keep canonical write path clean.
    """

    __slots__ = ("_state", "_lmdb_lookup_fn", "_lmdb_store_fn", "_semantic_dedup_cache")

    def __init__(
        self,
        state: QualityAssessmentState,
        lmdb_lookup_fn: callable | None = None,
        lmdb_store_fn: callable | None = None,
        semantic_dedup_cache: object | None = None,
    ) -> None:
        """
        Args:
            state: QualityAssessmentState instance (owned by DuckDBShadowStore)
            lmdb_lookup_fn: fn(fingerprint) -> finding_id | None (from DuckDBShadowStore)
            lmdb_store_fn: fn(fingerprint, finding_id) -> None (from DuckDBShadowStore)
            semantic_dedup_cache: optional semantic dedup cache instance
        """
        self._state = state
        self._lmdb_lookup_fn = lmdb_lookup_fn
        self._lmdb_store_fn = lmdb_store_fn
        self._semantic_dedup_cache = semantic_dedup_cache

    def assess(self, finding: CanonicalFinding) -> FindingQualityDecision:
        """
        Sprint 8W + 8AG + 8AK: Assess a single finding's quality via entropy + dedup.

        Sprint 8AK: URL-first fingerprint — if a canonical URL is present in
        provenance, use it (normalized) as the primary dedup signal, independent
        of source_type or payload position. Falls back to payload_text.

        Sprint 8AG §6.17: Persistent dedup via LMDB with hot-cache read-through.
        Lookup order: hot cache → persistent LMDB → store if miss.
        LMDB is the authority; hot cache is a bounded read-through cache.

        Returns FindingQualityDecision (frozen, immutable).
        Fail-open: any exception → accept with reason="quality_check_error".

        Text mapping: URL (if present) or payload_text (if exists and non-empty), else query.
        If both are empty, falls back to query (may accept trivially).
        """
        _logger = _logging.getLogger(__name__)

        # Sprint 8AK: URL-first fingerprint
        url_from_provenance = self._state._extract_url_from_provenance(finding.provenance)
        url_fingerprint = _compute_url_fingerprint(url_from_provenance) if url_from_provenance else ""

        # Map text for quality checks (only needed for entropy when no URL)
        if url_fingerprint:
            fingerprint = url_fingerprint
            entropy = 0.0  # not meaningful when URL is identity
        else:
            text = finding.payload_text if finding.payload_text else finding.query
            if not text or not text.strip():
                text = finding.query
            normalized = _normalize_for_quality(text)
            entropy = _compute_entropy(normalized)
            fingerprint = _compute_dedup_fingerprint(normalized)

        # Tier 1: hot cache (fast path, bounded)
        duplicate = self._state.hot_cache_lookup(fingerprint)
        if duplicate is not None:
            self._state._quality_duplicate_count += 1
            reason = "persistent_duplicate" if url_fingerprint else "duplicate_detected"
            return self._make_decision(
                accepted=False,
                reason=reason,
                entropy=entropy,
                fingerprint=fingerprint,
                duplicate=True,
            )

        # Tier 2: persistent LMDB (authority)
        if self._lmdb_lookup_fn is not None:
            stored_finding_id = self._lmdb_lookup_fn(fingerprint)
            if stored_finding_id is not None:
                self._state.add_to_hot_cache(fingerprint, stored_finding_id)
                self._state._persistent_duplicate_count += 1
                reason = "persistent_duplicate" if url_fingerprint else "duplicate_detected"
                return self._make_decision(
                    accepted=False,
                    reason=reason,
                    entropy=entropy,
                    fingerprint=fingerprint,
                    duplicate=True,
                )

        # URL-first path: short-circuit to store (no entropy check needed)
        if url_fingerprint:
            if self._lmdb_store_fn is not None:
                self._lmdb_store_fn(fingerprint, finding.finding_id)
            self._state.add_to_hot_cache(fingerprint, finding.finding_id)
            return self._make_decision(
                accepted=True,
                reason=None,
                entropy=entropy,
                fingerprint=fingerprint,
                duplicate=False,
            )

        # Short strings (< 8 chars) skip entropy filter — accept immediately
        # WITHOUT storing to LMDB/hotcache. Storage deferred to after semantic dedup pass.
        if len(fingerprint) < _QUALITY_MIN_ENTROPY_LEN:
            if self._semantic_dedup_cache is not None:
                try:
                    text_for_embed = url_from_provenance or (finding.payload_text or finding.query)
                    if text_for_embed and len(text_for_embed) >= 16:
                        is_dup = self._semantic_dedup_cache.check_and_cache(
                            text_for_embed, threshold=0.90
                        )
                        if is_dup:
                            self._state._quality_duplicate_count += 1
                            return self._make_decision(
                                accepted=False,
                                reason="semantic_duplicate",
                                entropy=entropy,
                                fingerprint=fingerprint,
                                duplicate=True,
                            )
                except Exception as e:
                    _logger.warning(f"Quality gate error (short_string path): {e}")
            # Short string + no semantic duplicate → store and accept
            if self._lmdb_store_fn is not None:
                self._lmdb_store_fn(fingerprint, finding.finding_id)
            self._state.add_to_hot_cache(fingerprint, finding.finding_id)
            return self._make_decision(
                accepted=True,
                reason="short_string_skip",
                entropy=entropy,
                fingerprint=fingerprint,
                duplicate=False,
            )

        # Entropy threshold check
        if entropy < _QUALITY_ENTROPY_THRESHOLD:
            self._state._quality_rejected_count += 1
            return self._make_decision(
                accepted=False,
                reason="low_entropy_rejected",
                entropy=entropy,
                fingerprint=fingerprint,
                duplicate=False,
            )

        # Sprint F197B: Semantic dedup BEFORE storing
        if self._semantic_dedup_cache is not None:
            try:
                text_for_embed = url_from_provenance or (finding.payload_text or finding.query)
                if text_for_embed and len(text_for_embed) >= 16:
                    is_dup = self._semantic_dedup_cache.check_and_cache(
                        text_for_embed, threshold=0.90
                    )
                    if is_dup:
                        self._state._quality_duplicate_count += 1
                        return self._make_decision(
                            accepted=False,
                            reason="semantic_duplicate",
                            entropy=entropy,
                            fingerprint=fingerprint,
                            duplicate=True,
                        )
            except Exception as e:
                _logger.warning(f"Quality gate error (entropy path): {e}")

        # Only reach here if semantic dedup passed or was skipped (fail-open)
        # Now safe to commit to LMDB + hot cache
        if self._lmdb_store_fn is not None:
            self._lmdb_store_fn(fingerprint, finding.finding_id)
        self._state.add_to_hot_cache(fingerprint, finding.finding_id)

        return self._make_decision(
            accepted=True,
            reason=None,
            entropy=entropy,
            fingerprint=fingerprint,
            duplicate=False,
        )

    def _make_decision(
        self,
        accepted: bool,
        reason: str | None,
        entropy: float,
        fingerprint: str,
        duplicate: bool,
    ) -> FindingQualityDecision:
        """Construct a FindingQualityDecision. Import lazily to avoid circular deps."""
        from .duckdb_store import FindingQualityDecision

        return FindingQualityDecision(
            accepted=accepted,
            reason=reason,
            entropy=entropy,
            normalized_hash=fingerprint,
            duplicate=duplicate,
        )

    def record_rejection(
        self,
        finding: CanonicalFinding,
        decision: FindingQualityDecision,
    ) -> None:
        """Delegate to QualityAssessmentState.record_rejection()."""
        self._state.record_rejection(finding, decision)

    def get_rejection_history(self) -> tuple[QualityRejectionRecord, ...]:
        """Delegate to QualityAssessmentState.get_rejection_history()."""
        return self._state.get_rejection_history()

    def increment_accepted(self) -> None:
        """Increment accepted count when finding passes quality gate."""
        self._state._accepted_count += 1

    def increment_fail_open(self) -> None:
        """Increment fail-open counter when quality check raises."""
        self._state._quality_fail_open_count += 1

    def reset_counters(self) -> None:
        """Reset all counters. Called on store reset."""
        self._state._quality_rejected_count = 0
        self._state._quality_duplicate_count = 0
        self._state._quality_fail_open_count = 0
        self._state._persistent_duplicate_count = 0
        self._state._accepted_count = 0
