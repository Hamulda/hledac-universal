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



import hashlib
import logging as _logging
import os
import math as _math
import re
import string as _string
from collections import Counter, OrderedDict
import collections.abc
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlparse

if TYPE_CHECKING:
    from .duckdb_store import CanonicalFinding
    from ._quality_types import FindingQualityDecision

__all__ = [
    "QualityRejectionRecord",
    "QualityAssessmentState",
    "QualityAssessor",
    "_QUALITY_ENTROPY_THRESHOLD",
    "_QUALITY_MIN_ENTROPY_LEN",
    "_normalize_for_quality",
    "_compute_entropy",
    "_compute_entropy_batch",
    "_normalize_osint_url",
    "_compute_dedup_fingerprint",
    "_compute_url_fingerprint",
]


# ---------------------------------------------------------------------------
# Rust backend — centralized access via core.rust_backend (F265C refactor)
# ---------------------------------------------------------------------------
from hledac.universal.core.rust_backend import rust as _rust_backend

# Convenience flags — LAZY, resolved on every call.
# Previously set at import time (False even if Rust became available later).
def _url_engine_available() -> bool:
    return _rust_backend.is_available and _rust_backend.url is not None

def _quality_gate_rust_available() -> bool:
    return _rust_backend.is_available and _rust_backend.quality is not None

def _quality_gate_batch_available() -> bool:
    return _quality_gate_rust_available()


# ---------------------------------------------------------------------------
# Quality helper constants and functions (module-level, stateless)
# ---------------------------------------------------------------------------

# Sprint 8W: Configurable entropy threshold (bits per character)
# Env var support for consistency with dominant DI config pattern (73% of knowledge/ files
# use direct env var access; DedupSettings.from_env() in config/settings.py is canonical)
_QUALITY_ENTROPY_THRESHOLD: float = float(os.environ.get(
    "HLEDAC_QUALITY_ENTROPY_THRESHOLD", "0.5"
))
# Strings shorter than this skip entropy filtering
_QUALITY_MIN_ENTROPY_LEN: int = int(os.environ.get(
    "HLEDAC_QUALITY_MIN_ENTROPY_LEN", "8"
))

# Sprint F265D: Feed source types that skip semantic dedup (high recall, low precision acceptable).
# Feed pipelines prioritize recall over precision — semantic dedup threshold 0.75 is too
# aggressive for feed content which naturally shares structure (titles, metadata).
_FEED_SOURCE_TYPES: frozenset[str] = frozenset({
    "rss_atom_pipeline",
    "ti_feed_adapter",
    "feed_pipeline",
})

# Sprint-F265B P2: High-confidence IoC bypass — SHA256/MD5/Hash patterns skip
# semantic dedup entirely (exact match = trust the hash as dedup key).
# Pattern matches hex hashes of common IOC types (SHA256, MD5, SHA1, Blake2b).
_HIGH_CONF_IOC_RE = re.compile(r"^[a-fA-F0-9]{32,128}$")


def _normalize_for_quality(text: str) -> str:
    """
    Sprint 8W + P1-5: Normalize text for entropy and dedup quality checks.

    Normalization rules:
      - lowercase
      - strip leading/trailing whitespace
      - collapse internal whitespace to single space (includes tabs/newlines)
      - remove non-printable chars (ord < 32) that are NOT whitespace

    Tabs and newlines (ord < 32) are whitespace and get collapsed to space first.
    Other non-printable chars (BEL, NUL, etc.) are removed after whitespace normalization.

    No stemming, lemmatization, transliteration, or locale-dependent logic.

    Sprint P1-5: Try Rust fast-path first (NEON-vectorized, ~5-8x faster on
    Apple Silicon). On any exception fall through to the Python implementation
    — bit-identical output verified by tests/probe_p15_quality_gate.py.
    """
    # Sprint P1-5: Rust fast-path via centralized rust.* namespace (F265C refactor)
    if _quality_gate_rust_available() and _rust_backend.quality is not None:
        try:
            return _rust_backend.quality.normalize_quality_text(text)
        except Exception:  # noqa: BLE001
            pass  # Fall through to Python implementation

    lowered = text.lower()
    stripped = lowered.strip()
    normalized = " ".join(stripped.split())
    whitespace_chars = frozenset(_string.whitespace)
    cleaned = "".join(ch for ch in normalized if ord(ch) >= 32 or ch in whitespace_chars)
    return cleaned


def _compute_entropy(text: str) -> float:
    """
    Sprint 8W + P1-5: Compute Shannon entropy in bits per character.

    Uses collections.Counter for efficiency (no Python for-loop over characters).
    Returns 0.0 for empty text.

    Sprint P1-5: Try Rust fast-path first (256-bin histogram + f64::log2 in
    native code, ~10-30x faster than Counter() on Apple Silicon). On any
    exception fall through to the Python implementation — output is
    bit-identical because both paths operate on UTF-8 bytes after lowercase.
    """
    # Sprint P1-5: Rust fast-path via centralized rust.* namespace (F265C refactor)
    if _quality_gate_rust_available() and _rust_backend.quality is not None:
        return _rust_backend.quality.compute_entropy(text)

    if not text:
        return 0.0
    char_counts = Counter(text)
    total = len(text)
    entropy = 0.0
    for count in char_counts.values():
        p = count / total
        if p > 0:
            entropy -= p * _math.log2(p)
    return entropy


def _compute_entropy_batch(texts: list[str]) -> list[float]:
    """
    Sprint F320: Batch entropy — Rust path uses NEON SIMD rayon, ~10-30× faster.

    Fallback: serial list comprehension calling _compute_entropy per item.
    Output is bit-identical to single-call _compute_entropy per text.
    """
    if _quality_gate_rust_available() and _rust_backend.quality is not None:
        try:
            return _rust_backend.quality.batch_entropy(texts)
        except Exception:  # noqa: BLE001
            pass
    return [_compute_entropy(t) for t in texts]


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

    # Sprint F216R: Rust fast path via centralized rust.* namespace (F265C refactor)
    if _url_engine_available() and _rust_backend.url is not None:
        return _rust_backend.url.normalize(url)

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

    TRACKING_QUERY_PARAMS = frozenset({  # noqa: N806
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
    Sprint 8W + P1-5: Compute BLAKE2b-128 fingerprint of normalized text.

    Uses hashlib.blake2b (NOT Python built-in hash()).
    digest_size=16 → 32 hex chars.
    Stable across process restarts.

    Sprint P1-5: Try Rust fast-path first (NEON-vectorized BLAKE2b in Rust,
    ~2-3x faster than the CPython C extension on Apple Silicon). The Rust
    implementation is bit-for-bit compatible with hashlib.blake2b(digest_size=16)
    so existing LMDB-persisted fingerprints remain valid. On any exception
    fall through to the Python fallback.

    IMPORTANT: Only payload text goes through this path. URL fingerprints use
    `_compute_url_fingerprint` (Sprint F216R, xxHash64 format) to preserve
    the existing LMDB key format.
    """
    # Sprint P1-5: Rust fast-path via centralized rust.* namespace (F265C refactor)
    if _quality_gate_rust_available() and _rust_backend.quality is not None:
        return _rust_backend.quality.dedup_fingerprint(text)

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
    # Sprint F216R: Rust fast path via centralized rust.* namespace (F265C refactor)
    if _url_engine_available() and _rust_backend.url is not None:
        fp = _rust_backend.url.fingerprint(url)
        # Convert u64 to 16-char hex string (backward compatible)
        return format(fp, '016x')

    # Python fallback: normalize then BLAKE2b
    normalized_url = _normalize_osint_url(url)
    if normalized_url:
        return hashlib.blake2b(normalized_url.encode("utf-8"), digest_size=16).hexdigest()
    return ""


# Sprint F216G: Quality Rejection Ledger
class QualityRejectionRecord(msgspec.Struct, frozen=True, gc=False):
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
from hledac.universal.config.dedup_config import DEDUP_HOT_CACHE_MAX, DEDUP_LMDB_MAP_SIZE  # noqa: E402

# Backward compatibility: module-level aliases (DEPRECATED — use config.dedup_config)
_DEDUP_LMDB_MAP_SIZE: int = DEDUP_LMDB_MAP_SIZE
_DEDUP_HOT_CACHE_MAX: int = DEDUP_HOT_CACHE_MAX


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
        """Extract the first HTTP(S) URL from a provenance tuple.

        Handles two formats:
        - Raw URL: "https://example.com"
        - Tagged URL: "url:https://example.com" (PUBLIC lane format from _build_public_finding)
        """
        if not provenance:
            return ""
        for item in provenance:
            if not isinstance(item, str):
                continue
            # Raw URL format
            if item.startswith("http"):
                return item
            # Tagged URL format: "url:https://..."
            if item.startswith("url:"):
                url = item[4:]  # Strip "url:" prefix
                if url.startswith("http"):
                    return url
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

    def reset_hot_cache(self) -> None:
        """Sprint F259B: Clear in-memory dedup hot cache + fingerprint set per-sprint.

        Bounded: both dicts are bounded (_DEDUP_HOT_CACHE_MAX) so clear is O(1) amortized.
        Fail-soft: any exception is swallowed — caller is the per-sprint reset path
        and must never crash the scheduler.
        """
        try:
            self._dedup_hot_cache.clear()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._dedup_hot_cache_order.clear()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._dedup_fingerprints.clear()
        except Exception:  # noqa: BLE001
            pass


class QualityAssessor:
    """
    Sprint 8W + 8AG + 8AK + F216G: Quality gate delegate.

    Encapsulates quality decision logic (entropy check, dedup, URL-first fingerprint).
    Delegates to DuckDBShadowStore for LMDB persistence and semantic dedup cache.

    DuckDBShadowStore holds this as an attribute and calls it from
    async_ingest_findings_batch() to keep canonical write path clean.
    """

    __slots__ = ("_state", "_lmdb_lookup_fn", "_lmdb_store_fn", "_semantic_dedup_cache", "_sprint_id")

    def __init__(
        self,
        state: QualityAssessmentState,
        lmdb_lookup_fn: collections.abc.Callable | None = None,
        lmdb_store_fn: collections.abc.Callable | None = None,
        semantic_dedup_cache: object | None = None,
        sprint_id: str | None = None,
        ) -> None:
        """
        Args:
        state: QualityAssessmentState instance (owned by DuckDBShadowStore)
        lmdb_lookup_fn: fn(fingerprint) -> finding_id | None (from DuckDBShadowStore)
        lmdb_store_fn: fn(fingerprint, finding_id) -> None (from DuckDBShadowStore)
            semantic_dedup_cache: optional semantic dedup cache instance
            sprint_id: sprint-scoped dedup namespace (Sprint-F265B P2)
        """
        self._state = state
        self._lmdb_lookup_fn = lmdb_lookup_fn
        self._lmdb_store_fn = lmdb_store_fn
        self._semantic_dedup_cache = semantic_dedup_cache
        self._sprint_id = sprint_id

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

        # Sprint F265D: Compute source flags once at outer scope for all paths below
        is_feed_source = finding.source_type in _FEED_SOURCE_TYPES

        # Short strings (< 8 chars) skip entropy filter — accept immediately
        # WITHOUT storing to LMDB/hotcache. Storage deferred to after semantic dedup pass.
        if len(fingerprint) < _QUALITY_MIN_ENTROPY_LEN:
            # Sprint-F265B P2: High-confidence IoC bypass — hex hashes skip semantic dedup
            # Sprint F265D: Feed sources also skip semantic dedup
            text_for_embed = url_from_provenance or (finding.payload_text or finding.query)
            is_high_conf_ioc = (
                text_for_embed is not None
                and _HIGH_CONF_IOC_RE.match(text_for_embed.strip()) is not None
            )
            if self._semantic_dedup_cache is not None and not is_high_conf_ioc and not is_feed_source:
                try:
                    if text_for_embed and len(text_for_embed) >= 16:
                        is_dup = self._semantic_dedup_cache.check_and_cache(
                            text_for_embed, threshold=0.75  # Sprint-F265B: was 0.80, too tight for IOC data (caused 100% rejection in sprint 300s)
                        )
                        if is_dup:
                            self._state._quality_duplicate_count += 1
                            _logger.debug(
                                "[QUALITY] short_string semantic_dup hit fp=%s url=%s",
                                fingerprint[:16],
                                (url_from_provenance or "")[:80],
                            )
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

        # Sprint-F265B P2: High-confidence IoC bypass — hex hashes skip semantic dedup
        text_for_embed = url_from_provenance or (finding.payload_text or finding.query)
        is_high_conf_ioc = (
            text_for_embed is not None
            and _HIGH_CONF_IOC_RE.match(text_for_embed.strip()) is not None
        )

        # Entropy threshold check
        if entropy < _QUALITY_ENTROPY_THRESHOLD:
            self._state._quality_rejected_count += 1
            _logger.debug(
                "[QUALITY] low_entropy rejected entropy=%.3f threshold=%.3f fp=%s url=%s text=%s",
                entropy,
                _QUALITY_ENTROPY_THRESHOLD,
                fingerprint[:16],
                (url_from_provenance or "")[:80],
                (finding.payload_text or "")[:60],
            )
            return self._make_decision(
                accepted=False,
                reason="low_entropy_rejected",
                entropy=entropy,
                fingerprint=fingerprint,
                duplicate=False,
            )

        # Sprint F197B + Sprint-F265B P2: Semantic dedup BEFORE storing
        # High-confidence IoC (hex hash) bypass: hashes are exact-match dedup keys,
        # semantic similarity is meaningless for cryptographic hashes.
        # Sprint F265D: Feed sources skip semantic dedup (feed content naturally shares
        # structure — titles, metadata — and recall > precision is desirable there).
        if (
            self._semantic_dedup_cache is not None
            and not is_high_conf_ioc
            and not is_feed_source
        ):
            try:
                text_for_embed = url_from_provenance or (finding.payload_text or finding.query)
                if text_for_embed and len(text_for_embed) >= 16:
                    is_dup = self._semantic_dedup_cache.check_and_cache(
                        text_for_embed, threshold=0.75  # Sprint-F265B: was 0.80, too tight for IOC data (caused 100% rejection in sprint 300s)
                    )
                    if is_dup:
                        self._state._quality_duplicate_count += 1
                        _logger.debug(
                            "[QUALITY] semantic_dup hit fp=%s url=%s",
                            fingerprint[:16],
                            (url_from_provenance or "")[:80],
                        )
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

    # ---------------------------------------------------------------------------
    # Sprint P1-2: Batch quality gate — rayon-parallel Rust kernels
    # ---------------------------------------------------------------------------

    def assess_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[FindingQualityDecision]:
        """
        Sprint P1-2: Batch quality gate — rayon-parallel via Rust batch_* APIs.

        Applies identical decision logic as per-finding assess(), but in a single
        batch call per chunk. Phase 1 pre-computes all fingerprints + entropies via
        Rust batch APIs; Phase 2 walks findings applying URL-first → hot_cache →
        LMDB → short_string → entropy → semantic_dedup.

        Bounded: caller should chunk at 4096 max (Rust BATCH_HARD_CAP).
        Below 100 items falls through to sequential Rust single-call (avoids rayon overhead).
        Returns list[FindingQualityDecision] in same order as findings.
        Fail-soft: any exception in batch pre-compute falls back to per-finding assess().
        """
        import logging as _batch_logger

        n = len(findings)
        results: list[FindingQualityDecision | None] = [None] * n

        # --- Phase 1: pre-compute fingerprints + entropies via Rust batch ---
        url_fingerprints: list[str] = [''] * n
        entropies: list[float] = [0.0] * n
        fingerprints: list[str] = [''] * n
        url_indices: list[int] = []
        payload_indices: list[int] = []
        texts: list[str] = []

        for idx, f in enumerate(findings):
            url = self._state._extract_url_from_provenance(f.provenance) if f.provenance else ''
            if url:
                url_fingerprints[idx] = url
                url_indices.append(idx)
                texts.append('')
            else:
                payload_text = f.payload_text if f.payload_text else f.query
                if not (payload_text and payload_text.strip()):
                    payload_text = f.query
                texts.append(payload_text)
                payload_indices.append(idx)

        # Batch URL fingerprints (URL-first items skip entropy)
        if url_indices:
            url_texts = [url_fingerprints[i] for i in url_indices]
            if _quality_gate_batch_available() and _rust_backend.quality is not None:
                try:
                    batch_urls: list[str] = _rust_backend.quality.batch_url_fingerprints(url_texts)
                    for j, idx in enumerate(url_indices):
                        url_fingerprints[idx] = batch_urls[j]
                except Exception:
                    try:
                        for j, idx in enumerate(url_indices):
                            url_fingerprints[idx] = _rust_backend.quality.url_fingerprint(url_texts[j])
                    except Exception:
                        for j, idx in enumerate(url_indices):
                            url_fingerprints[idx] = _compute_url_fingerprint(url_texts[j])
            else:
                for j, idx in enumerate(url_indices):
                    url_fingerprints[idx] = _compute_url_fingerprint(url_texts[j])

        # Batch payload fingerprints + entropies
        if payload_indices:
            payload_texts = [texts[i] for i in payload_indices]

            # Normalize via Rust batch (F265C refactor — centralized rust.* namespace)
            if _quality_gate_rust_available() and _rust_backend.quality is not None:
                try:
                    normalized_batch: list[str] = _rust_backend.quality.batch_normalize_quality_text(payload_texts)
                except Exception:
                    normalized_batch = [_normalize_for_quality(t) for t in payload_texts]
            else:
                normalized_batch = [_normalize_for_quality(t) for t in payload_texts]

            # Batch entropy via Rust rayon pool (F265C refactor) + zero-copy (F266-ZC)
            if _quality_gate_batch_available() and _rust_backend.quality is not None:
                try:
                    entropies_batch: list[float] = _rust_backend.quality.batch_entropy(normalized_batch)
                except Exception:
                    entropies_batch = [_compute_entropy(t) for t in normalized_batch]
            else:
                entropies_batch = [_compute_entropy(t) for t in normalized_batch]

            # Batch dedup fingerprints via Rust rayon pool (F265C refactor) + zero-copy (F266-ZC)
            if _quality_gate_batch_available() and _rust_backend.quality is not None:
                try:
                    fps_batch: list[str] = _rust_backend.quality.batch_dedup_fingerprints(normalized_batch)
                except Exception:
                    fps_batch = [_compute_dedup_fingerprint(t) for t in normalized_batch]
            else:
                fps_batch = [_compute_dedup_fingerprint(t) for t in normalized_batch]

            for j, idx in enumerate(payload_indices):
                entropies[idx] = entropies_batch[j]
                fingerprints[idx] = fps_batch[j]

        # --- Phase 2: apply decision logic per finding (same as assess()) ---
        _batch_logger = _logging.getLogger(__name__)
        for idx, f in enumerate(findings):
            url_fp = url_fingerprints[idx]
            fp = fingerprints[idx]
            entropy = entropies[idx]
            is_feed_source = f.source_type in _FEED_SOURCE_TYPES
            text_for_embed = url_fp or (f.payload_text or f.query)
            is_high_conf_ioc = (
                text_for_embed is not None
                and _HIGH_CONF_IOC_RE.match(text_for_embed.strip()) is not None
            )

            # Tier 1: hot cache
            duplicate_hit = self._state.hot_cache_lookup(fp)
            if duplicate_hit is not None:
                self._state._quality_duplicate_count += 1
                reason = "persistent_duplicate" if url_fp else "duplicate_detected"
                results[idx] = self._make_decision(False, reason, entropy, fp, True)
                continue

            # Tier 2: LMDB
            if self._lmdb_lookup_fn is not None:
                stored_id = self._lmdb_lookup_fn(fp)
                if stored_id is not None:
                    self._state.add_to_hot_cache(fp, stored_id)
                    self._state._persistent_duplicate_count += 1
                    reason = "persistent_duplicate" if url_fp else "duplicate_detected"
                    results[idx] = self._make_decision(False, reason, entropy, fp, True)
                    continue

            # URL-first: store and accept (no entropy)
            if url_fp:
                if self._lmdb_store_fn is not None:
                    self._lmdb_store_fn(fp, f.finding_id)
                if not is_feed_source:
                    self._state.add_to_hot_cache(fp, f.finding_id)
                results[idx] = self._make_decision(True, None, entropy, fp, False)
                continue

            # Short strings: semantic dedup check
            if len(fp) < _QUALITY_MIN_ENTROPY_LEN:
                # Sprint F265D: Feed sources skip semantic dedup
                if self._semantic_dedup_cache is not None and not is_high_conf_ioc and not is_feed_source:
                    try:
                        if text_for_embed and len(text_for_embed) >= 16:
                            is_dup = self._semantic_dedup_cache.check_and_cache(
                                text_for_embed, threshold=0.75,
                            )
                            if is_dup:
                                self._state._quality_duplicate_count += 1
                                _batch_logger.debug(
                                    "[QUALITY] short_string semantic_dup hit fp=%s",
                                    fp[:16] if fp else "",
                                )
                                results[idx] = self._make_decision(
                                    False, "semantic_duplicate", entropy, fp, True,
                                )
                                continue
                    except Exception as e:
                        _batch_logger.warning(f"Quality gate err (short_string batch): {e}")
                if self._lmdb_store_fn is not None:
                    self._lmdb_store_fn(fp, f.finding_id)
                if not is_feed_source:
                    self._state.add_to_hot_cache(fp, f.finding_id)
                results[idx] = self._make_decision(True, "short_string_skip", entropy, fp, False)
                continue

            # Entropy threshold
            if entropy < _QUALITY_ENTROPY_THRESHOLD:
                self._state._quality_rejected_count += 1
                _batch_logger.debug(
                    "[QUALITY] low_entropy rejected entropy=%.3f threshold=%.3f fp=%s",
                    entropy, _QUALITY_ENTROPY_THRESHOLD, fp[:16] if fp else "",
                )
                results[idx] = self._make_decision(False, "low_entropy_rejected", entropy, fp, False)
                continue

            # Semantic dedup
            if self._semantic_dedup_cache is not None and not is_high_conf_ioc and not is_feed_source:
                try:
                    if text_for_embed and len(text_for_embed) >= 16:
                        is_dup = self._semantic_dedup_cache.check_and_cache(
                            text_for_embed, threshold=0.75,
                        )
                        if is_dup:
                            self._state._quality_duplicate_count += 1
                            _batch_logger.debug(
                                "[QUALITY] semantic_dup hit fp=%s",
                                fp[:16] if fp else "",
                            )
                            results[idx] = self._make_decision(
                                False, "semantic_duplicate", entropy, fp, True,
                            )
                            continue
                except Exception as e:
                    _batch_logger.warning(f"Quality gate err (entropy batch): {e}")

            # All passed — store and accept
            if self._lmdb_store_fn is not None:
                self._lmdb_store_fn(fp, f.finding_id)
            if not is_feed_source:
                self._state.add_to_hot_cache(fp, f.finding_id)
            results[idx] = self._make_decision(True, None, entropy, fp, False)

        assert None not in results, "assess_batch: 1:1 invariant violated"
        return results  # type: ignore[return-value]

    def _make_decision(
        self,
        accepted: bool,
        reason: str | None,
        entropy: float,
        fingerprint: str,
        duplicate: bool,
    ) -> FindingQualityDecision:
        """Construct a FindingQualityDecision."""
        from ._quality_types import FindingQualityDecision

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
