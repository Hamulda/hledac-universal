"""
DuckDuckGo public web discovery adapter.

Backend: ddgs v9+ (sync-only; async via asyncio.to_thread compatibility fallback)


INVARIANTS (Sprint 8AC):
- Public/passive-only; no auth, no cookies, no credentials
- No AO imports; no storage writes; no pattern matcher calls
- No import-time network side effects
- max_results hard cap = 50; default = 10
- asyncio.timeout() for timeout; CancelledError re-raised
- fail-soft for RatelimitException / TimeoutException / generic backend errors
- Per-call URL dedup with preserve-first ordering
- msgspec.Struct(frozen=True) for all DTOs
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import urllib.parse as urlparse
from typing import TYPE_CHECKING

import httpx

from hledac.universal.discovery.base import BaseDiscoveryMixin, DiscoveryBatchResult, DiscoveryHit, DiscoveryResult
from hledac.universal.core.feature_flags import FeatureFlags, FeatureFlag
from hledac.universal.network.session_runtime import async_get_httpx_session
from hledac.universal.tools.discovery_replay import (
    read_cassette,
    replay_enabled,
    replay_strict_enabled,
    write_cassette,
)
from hledac.universal.transport.circuit_breaker import (
    checked_httpx_get as checked_aiohttp_get,
)
from hledac.universal.utils.asyncx import parallel
from hledac.universal.tools.url_dedup import get_default_bloom_filter

_PUBLIC_REPLAY_ADAPTER = "public_duckduckgo"

# Backend: ddgs v9+ (sync-only; async via asyncio.to_thread compatibility wrapper)
if TYPE_CHECKING:
    from ddgs import DDGS  # noqa: F401


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SOURCE_NAME: str = "duckduckgo"
DEFAULT_MAX_RESULTS: int = 10
HARD_MAX_RESULTS: int = 50
DEFAULT_TIMEOUT_S: float = 35.0
# Domain diversity cap: at most this fraction of results from a single host.
# F178E: tightened from 0.4→0.25 — prevents single-host concentration in results
MAX_HOST_SHARE_RATIO: float = 0.25

# ---------------------------------------------------------------------------
# DTO contracts
# ---------------------------------------------------------------------------


# NOTE: The actual class definitions live in discovery/base.py (SSOT).
# This module re-exports them for backward compatibility with existing call sites.


# ---------------------------------------------------------------------------
# Discovery error taxonomy — F206AB
# ---------------------------------------------------------------------------


def classify_discovery_error(
    error: str | BaseException | None,
    *,
    elapsed_s: float | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    hits_count: int = 0,
) -> str:
    """
    Classify a discovery error into a concrete F206AB taxonomy category.

    Args:
        error: Error string, Exception object, or None.
        elapsed_s: Actual elapsed time of the discovery call in seconds.
        timeout_s: Expected timeout threshold (default 35s).
        hits_count: Number of hits returned (default 0).

    Returns one of:
        - none              : error is None/empty AND hits_count > 0 (successful call)
        - timeout           : asyncio.TimeoutError / "timeout" / elapsed >= timeout_s
        - rate_limited      : ratelimit / 429 / "too many" signals
        - captcha_or_blocked : captcha / blocked / 403 / bot signals
        - provider_empty    : error is None AND hits_count == 0 (provider returned nothing)
        - provider_exception : non-Error Exception caught during search
        - import_error      : ImportError / ModuleNotFoundError
        - task_cancelled    : asyncio.CancelledError (re-raised by caller)
        - unknown_backend_error : any other error
    """
    # ---- CancelledError → task_cancelled (re-raised by caller) ----
    if isinstance(error, asyncio.CancelledError):
        return "task_cancelled"

    # ---- TimeoutError → timeout ----
    if isinstance(error, asyncio.TimeoutError) or isinstance(error, TimeoutError):
        return "timeout"

    # ---- None / empty → classify by hits_count ----
    if error is None or (isinstance(error, str) and not error.strip()):
        if hits_count > 0:
            return "none"  # successful call with results
        # elapsed_s >= timeout_s with no error: slow call that returned normally → provider_empty
        return "provider_empty"

    # ---- string coercion for remaining checks ----
    err_str = str(error)

    # ---- timeout keyword in string ----
    if "timeout" in err_str.lower():
        return "timeout"

    # ---- elapsed >= timeout_s with error present → timeout ----
    if elapsed_s is not None and elapsed_s >= timeout_s:
        return "timeout"

    # ---- rate limiting ----
    if any(kw in err_str.lower() for kw in ("ratelimit", "rate limit", "429", "too many")):
        return "rate_limited"

    # ---- captcha / blocking ----
    if any(kw in err_str.lower() for kw in ("captcha", "blocked", "403", "bot detection", "forbidden", "access denied")):  # noqa: E501
        return "captcha_or_blocked"

    # ---- import error ----
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return "import_error"

    # ---- generic exception (non-CancelledError/TimeoutError) ----
    if isinstance(error, Exception):
        return "provider_exception"

    # ---- anything else: unknown backend error ----
    return "unknown_backend_error"


# ---------------------------------------------------------------------------
# Status helpers (O(1), no network calls)
# ---------------------------------------------------------------------------

_backend_name: str = "ddgs"
_backend_version: str | None = None
_last_error: str | None = None


def backend_name() -> str:
    return _backend_name


def backend_version() -> str:  # noqa: D102
    global _backend_version
    if _backend_version is None:
        try:
            import ddgs
            _backend_version = getattr(ddgs, "__version__", "unknown")
        except Exception:
            try:
                import ddgs
                _backend_version = getattr(ddgs, "__version__", "unknown")
            except Exception:  # pragma: no cover — defensive
                _backend_version = "unknown"
    return _backend_version  # type: ignore[return-value]


def last_error() -> str | None:
    return _last_error


# ---------------------------------------------------------------------------
# Query shaping — preserves quoted strings, entity-like tokens, IOC patterns
# ---------------------------------------------------------------------------

_REQUOTEABLE_QUOTE_CHARS = {'"', "'", "\u201c", "\u201d", "\u00ab", "\u00bb"}


def _extract_quoted_tokens(query: str) -> tuple[list[str], str]:
    """
    Split query into quoted phrases and the remaining raw text.

    Returns:
        (list of de-quoted exact phrases, query with quoted parts stripped)
    """
    quoted: list[str] = []
    remaining = query
    for qc in _REQUOTEABLE_QUOTE_CHARS:
        if qc not in remaining:
            continue
        parts = remaining.split(qc)
        # Even-indexed parts = outside quotes; odd-indexed = inside quotes
        for idx, part in enumerate(parts):
            if idx % 2 == 1 and part.strip():
                quoted.append(part.strip())
        # Rebuild remaining — remove quoted spans entirely so raw query is clean
        for i, part in enumerate(parts):
            if i % 2 == 1:
                remaining = remaining.replace(qc + part + qc, "", 1)
    # Strip placeholder noise
    cleaned = " ".join(remaining.split())
    return quoted, cleaned


# IOC / domain / time patterns that deserve special treatment
_IOC_DOMAIN_RE = __import__("re").compile(
    r"(?:\w+\.){1,6}(?:com|org|net|io|co|uk|edu|gov|mil|info|biz|ru|cn|de|fr|nl|pl|eu|us|ca|au|at|be|ch|jp|kr|br|mx|za|in|it|es|nl|se|no|fi|dk|cz|sk|hu|ro|gr|pt|tr|il|ae|sa|ng|ke|gh|eg|ua|rs|by|kz|uz|tj|ir|iq|pk|bd|kh|la|mm|vn|th|my|sg|ph|id|tl|tz|et|zm|zw|bw|na|ug|rw|mw|mz|ao|ci|cm|sn|gd|jm|ht|cu|do|ve|co|pe|bo|cl|ar|uy|p ypy|py|pr|pa|cr|ni|sv|gt|hn|bz|gy|sr|gf|ec|py)")  # noqa: E501
_IOC_IP_RE = __import__("re").compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _tokenize_raw_query(query: str) -> set[str]:
    """Lower-case word tokens from the non-quoted part of the query."""
    return {
        t.lower().strip(".,;:!?()[]{}")
        for t in query.split()
        if len(t) > 1
    }


def _score_quoted_phrase_match(quoted_phrases: list[str], lower_title: str) -> tuple[float, list[str]]:
    """Check for quoted phrase match in title."""
    score = 0.0
    reasons: list[str] = []
    for phrase in quoted_phrases:
        if phrase.lower() in lower_title:
            score += 0.4
            reasons.append("quoted_title")
            break
    return score, reasons


def _score_ioc_match(url: str, lower_url: str, query: str) -> tuple[float, list[str]]:
    """Check for IOC-style domain/IP matches."""
    score = 0.0
    reasons: list[str] = []

    # Domain match
    domain_match = _IOC_DOMAIN_RE.search(url)
    if domain_match:
        domain_in_url = domain_match.group(0)
        if domain_in_url and domain_in_url.lower() in lower_url:
            score += 0.35
            reasons.append("domain_hit")

    # IP match
    ip_match = _IOC_IP_RE.search(query)
    if ip_match:
        ip = ip_match.group(0)
        if ip in url:
            score += 0.35
            reasons.append("ip_hit")

    return score, reasons


def _score_token_overlap(query_tokens: set[str], lower_title: str, lower_snippet: str) -> tuple[float, list[str]]:
    """Score token overlap between query and title/snippet."""
    score = 0.0
    reasons: list[str] = []

    if not query_tokens:
        return score, reasons

    # Title overlap
    title_words = {w.strip(".,;:!?()[]{}") for w in lower_title.split() if len(w) > 2}
    overlap = query_tokens & title_words
    if overlap:
        score += min(0.3, len(overlap) * 0.07)
        reasons.append("title_overlap")

    # Snippet overlap
    snippet_words = {w.strip(".,;:!?()[]{}") for w in lower_snippet.split() if len(w) > 2}
    snippet_overlap = query_tokens & snippet_words
    if snippet_overlap:
        score += min(0.15, len(snippet_overlap) * 0.04)
        reasons.append("snippet_overlap")

    return score, reasons


def _score_path_depth(url: str) -> float:
    """Score based on URL path depth."""
    try:
        parsed = urlparse.urlparse(url)
        path_depth = len([s for s in parsed.path.split("/") if s])
        if path_depth <= 2:
            return 0.05
        if path_depth >= 5:
            return -0.05
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _build_signals(
    query: str,
    title: str,
    url: str,
    snippet: str,
) -> dict:
    """Compute a small dict of query-aware signals for ranking."""
    quoted_phrases, raw_query = _extract_quoted_tokens(query)
    query_tokens = _tokenize_raw_query(raw_query)
    lower_title = title.lower()
    lower_url = url.lower()
    lower_snippet = snippet.lower()

    score = 0.0
    reasons: list[str] = []

    # Accumulate scores from each signal type
    s, r = _score_quoted_phrase_match(quoted_phrases, lower_title)
    score += s
    reasons.extend(r)

    s, r = _score_ioc_match(url, lower_url, query)
    score += s
    reasons.extend(r)

    s, r = _score_token_overlap(query_tokens, lower_title, lower_snippet)
    score += s
    reasons.extend(r)

    score += _score_path_depth(url)

    return {"score": max(0.0, min(1.0, score)), "reasons": reasons}


# F178E: SEO spam / title-manipulation patterns (shared logic for DDG adapter)
_re = __import__("re")
_SEO_SPAM_TITLE_RE = _re.compile(
    r"(?:\b\w+\b\s*){30,}", _re.IGNORECASE  # 30+ words = keyword stuffing
)
# F178E: repeated char title noise
_REPEATED_CHAR_TITLE_RE = _re.compile(r"^(.)\1{4,}$")  # 5+ same chars
# F178E: known parked / placeholder domain patterns
# Matches: domain at start, after dot, or after :// (URL scheme separator)
_PARKED_DOMAIN_RE = _re.compile(
    r"(?:^|\.|://)(?:blogspot\.com|wordpress\.com|tumblr\.com|livejournal\.com|"
    r"blogspot\.ru|000webhost\.com|110mb\.com|site90\.net|"
    r"blogcindi\.com|bloggen\.ru|blogrund\.com)\b",
    _re.IGNORECASE,
)

# F192E: CDN/package noise patterns — these are not primary content sources
# Exclude: CDN-hosted JS libraries, npm packages, GitHub raw content, cloud storage
_CDN_NOISE_PATTERNS = (
    "cdn.jsdelivr.net",
    "unpkg.com",
    "cdnjs.cloudflare.com",
    "raw.githubusercontent.com",
    "github.com/-/raw/",
    "storage.googleapis.com",
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "assets.wire.com",
    "staticaly.com",
    "fastly.net",
    "cloudfront.net",
    "jsdelivr.com",
)


def _is_noise_result(title: str, url: str, snippet: str, query: str = "") -> bool:
    """
    Return True for obvious low-ROI / thin / noise results.

    Noise patterns (F178E additions in *italic*):
    - Title is exactly the query (DDG self-loop query page)
    - URL is a known ad/partner link or redirect stub
    - Snippet is empty or is just "title • description" template noise
    - Title is pure ASCII-art / repeated chars / emoji-only
    *- SEO keyword-stuffed title (30+ words)
    *- Repeated-char title (5+ same char repeated)
    *- Parked/placeholder domain URL
    *- Query term density excess in title (query term appears >5× in title)
    """
    t = title.strip()
    s = snippet.strip()
    u = url.lower()

    # Self-loop: title ~= query (exact repeat of what you searched)
    if t and s and t.lower() == s[: len(t)].lower():
        return True

    # Empty or near-empty content
    if not t or len(t) < 3:
        return True
    if not s and len(u) > 100:
        # URL is long (probable tracking/campaign URL) with zero snippet
        return True

    # Known noise URL patterns
    if any(
        p in u
        for p in (
            "duckduckgo.com/?q=",
            "bing.com/search?",
            "google.com/search",
            "ecosia.org/search",
            "startpage.com/search",
            "swisscows.com/search",
            "search.yahoo.com",
            "search results for",
            "/search/?q=",
            "search/?q=",
            "q=%",
        )
    ):
        return True

    # Title is pure repeating chars / symbols (ASCII art noise)
    if len(t) > 10 and len(set(t)) < 3:
        return True

    # F178E: SEO keyword stuffing — 30+ words in title
    if _SEO_SPAM_TITLE_RE.match(t):
        return True

    # F178E: repeated-char title — "aaaaaaa..." or "??????..."
    if len(t) > 5 and _REPEATED_CHAR_TITLE_RE.match(t):
        return True

    # F178E: parked / placeholder domain
    if _PARKED_DOMAIN_RE.search(u):
        return True

    # F192E: CDN / package noise — these are JS library pages, not real content
    if any(p in u for p in _CDN_NOISE_PATTERNS):
        return True

    # F178E: query term density — query term repeated >5× in title = spam signal
    if query:
        q_lower = query.lower().strip()
        # F178E FIX: use raw query terms without length filter so 3-char terms like CVE are checked
        query_terms = [wt.strip(".,;:!?()[]{}") for wt in q_lower.split() if wt]
        for term in query_terms:
            # Count occurrences of term in title (case-insensitive)
            if len(term) >= 3 and t.lower().count(term) > 5:
                return True

    return False

# Tracking / junk query parameters to strip during normalisation.
# Covers utm_*, fbclid, gclid, msclkid, dclid, twclid, at_* and similar.
# Uses prefix matching so adding new variants needs no code change.
_TRACKING_PARAM_PREFIXES: tuple[str, ...] = (
    "utm_",
    "fbclid",
    "gclid",
    "msclkid",
    "dclid",
    "twclid",
    "at_",
    "_ga",
    "_gl",
    "mc_cid",
    "mc_eid",
    "oly_enc_id",
    "oly_anon_id",
    "ref_src",
    "ref_url",
    "source",
)


def _is_tracking_param(param: str) -> bool:
    """Return True if query param is a known tracking/advertising identifier."""
    p = param.lower()
    return any(p == prefix or p.startswith(prefix) for prefix in _TRACKING_PARAM_PREFIXES)


def _normalize_url_for_dedup(raw_url: str) -> str:
    """
    Robust URL normalisation for deduplication.

    Rules (bounded, deterministic):
      1. Lower-case scheme + host
      2. Strip leading "www." prefix from host (noise, not semantically distinct)
      3. Collapse consecutive slashes in path to single slash
      4. Strip trailing slash from non-root paths
      5. Remove tracking / ad identifiers from query string
      6. Drop empty fragment; drop lone trailing "?"
      7. Normalise path "." and ".." components
      8. Lower-case the remaining query keys for consistency
    """
    if not raw_url:
        return ""

    try:
        parsed = urlparse.urlparse(raw_url)
        scheme = parsed.scheme.lower() if parsed.scheme else "https"
        netloc = (parsed.netloc or "").lower()

        # Strip "www." prefix — same resource, different subdomain noise
        if netloc.startswith("www."):
            netloc = netloc[4:]

        path = parsed.path

        # Collapse multi-slashes (// → /)
        while "//" in path:
            path = path.replace("//", "/")

        # Resolve "." and ".." path components
        segments = path.split("/")
        resolved: list[str] = []
        for seg in segments:
            if seg == "" or seg == ".":
                continue
            if seg == "..":
                if resolved:
                    resolved.pop()
            else:
                resolved.append(seg)

        path = ("/" + "/".join(resolved) if resolved else "/").lower()
        # Strip trailing slash from non-root path
        if path.endswith("/") and len(path) > 1:
            path = path.rstrip("/")

        # Filter tracking/ad identifiers from query params
        raw_params = [p.strip() for p in parsed.query.split("&") if p.strip()]
        kept_params: list[str] = []
        for p in raw_params:
            key = p.split("=", 1)[0] if "=" in p else p
            if not _is_tracking_param(key):
                kept_params.append(p.lower())  # normalise key case

        query = "&".join(kept_params)
        if query == "?":
            query = ""

        # Drop fragment — #section anchors vary across pages but same content
        fragment = ""

        return urlparse.urlunsplit((scheme, netloc, path, query, fragment))
    except Exception:  # pragma: no cover — defensive, malformed URL
        lower = raw_url.lower()
        if lower.startswith("www."):
            lower = lower[4:]
        if lower.endswith("/") and len(lower) > 1:
            lower = lower.rstrip("/")
        return lower


def _extract_host(norm_url: str) -> str:
    """Extract lower-case host from a normalised URL (already urlparse'd).

    F271: Uses Rust url_ops.extract_host() when available (fast path),
    falls back to urlparse on ImportError.
    """
    try:
        from hledac.universal.fetching.public_fetcher import url_ops

        return url_ops.extract_host(norm_url)
    except Exception:  # noqa: BLE001
        pass
    # Fallback: urlparse
    try:
        return urlparse.urlparse(norm_url).netloc
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Internal backend wrapper
# ---------------------------------------------------------------------------


async def _ddgs_text_search(
    query: str,
    max_results: int,
    timeout_s: float,
) -> list[dict]:
    """
    Compatibility async wrapper around synchronous DDGS.text().

    Uses asyncio.to_thread() because ddgs v9+ does NOT
    provide an AsyncDDGS class — only a sync DDGS class.

    Per-request httpx timeouts are passed directly to the DDGS backend so
    that network stalls are bounded at the httpx layer — not just at the
    asyncio wrapper level.  This prevents thread leakage when the asyncio
    timeout fires: the httpx request is cancelled by its own timeout first,
    yielding the thread promptly.

    Raises:
        CancelledError: propagated from the cancelled task.
        DuckDuckGoSearchException (subclasses): translated to error strings.
    """
    global _last_error

    def _sync_search() -> list[dict]:
        # Lazy import: ddgs v9+
        from ddgs import DDGS  # noqa: F401

        backend: DDGS = DDGS(timeout=int(timeout_s))
        try:
            results = list(backend.text(query, max_results=max_results))
            return results
        finally:
            try:
                backend.client.close()
            except Exception:  # pragma: no cover — best-effort  # noqa: BLE001
                pass

    hits: list[dict] = await asyncio.to_thread(_sync_search)
    return hits


# ---------------------------------------------------------------------------
# Per-run query cache (F207I-A): deduplicate identical DDG queries within one run.
# Lightweight: keyed by normalized query string, bounded to MAX_CACHE entries.
# Does NOT survive across runs — no persistent cache required.
# ---------------------------------------------------------------------------
from collections import OrderedDict  # noqa: E402

_QUERY_CACHE: OrderedDict[str, DiscoveryBatchResult] = OrderedDict()
_QUERY_CACHE_MAX = 20  # max entries; oldest evicted when full


def _get_cached_discovery(query: str) -> DiscoveryBatchResult | None:
    """Return cached result for query if present, else None. Moves entry to end."""
    key = query.strip().lower()
    if key in _QUERY_CACHE:
        result = _QUERY_CACHE.pop(key)
        _QUERY_CACHE[key] = result  # re-insert at end (most-recently-used)
        return result
    return None


def _set_cached_discovery(query: str, result: DiscoveryBatchResult) -> None:
    """Cache a discovery result. Evicts oldest entry when at capacity."""
    key = query.strip().lower()
    if key in _QUERY_CACHE:
        _QUERY_CACHE.pop(key)
    elif len(_QUERY_CACHE) >= _QUERY_CACHE_MAX:
        _QUERY_CACHE.popitem(last=False)  # evict oldest
    _QUERY_CACHE[key] = result


def _clear_query_cache() -> None:
    """Clear the per-run query cache. Called by pipeline on run start."""
    _QUERY_CACHE.clear()


# ---------------------------------------------------------------------------
# Query variant expansion (Sprint F213B)
# ---------------------------------------------------------------------------

_MAX_QUERY_VARIANTS: int = 4
"""Max query variants for domain-like queries."""

_DOMAIN_LIKE_RE: re.Pattern = re.compile(
    r"^[a-zA-Z0-9][a-zA-Z0-9.\-]*\.[a-zA-Z]{2,}$"
)
"""Regex to detect domain-like query strings suitable for variant expansion."""

# Sprint F232: Domain token extraction for mixed queries
# Matches domain tokens inside longer queries (e.g., "mozilla.org certificate transparency")
_DOMAIN_TOKEN_RE: re.Pattern = re.compile(
    r"\b([a-zA-Z0-9][a-zA-Z0-9.\-]*\.[a-zA-Z]{2,})\b"
)
"""Extract domain-like tokens from mixed queries."""


def _query_looks_like_domain(query: str) -> bool:
    """
    Sprint F213B: Detect if query is a bare domain name suitable for variant expansion.

    Returns True for "example.com", "api.example.com", "*.example.com".
    Returns False for quoted strings, site: prefixes, or plain text queries.
    """
    q = query.strip()
    if not q or len(q) > 253:
        return False
    # Must look like a domain (has at least one dot, no spaces, no site: prefix)
    if " " in q or q.lower().startswith("site:") or q.startswith('"') or q.startswith("'"):
        return False
    return bool(_DOMAIN_LIKE_RE.match(q))


def _extract_domain_token(query: str) -> str | None:
    """
    Sprint F232: Extract the first domain-like token from a mixed query.

    For "mozilla.org certificate transparency subdomains april 2026" returns "mozilla.org".
    For "example.com" returns "example.com".
    For "site:example.com" returns "example.com" (strips the site: prefix).
    For "plain text query" returns None.
    """
    q = query.strip()
    if not q:
        return None
    # Strip site: prefix if present
    if q.lower().startswith("site:"):
        q = q[5:].strip()
    # Try exact domain match first
    if _DOMAIN_LIKE_RE.match(q):
        return q
    # Scan for domain token inside longer query
    match = _DOMAIN_TOKEN_RE.search(q)
    if match:
        return match.group(1)
    return None


def _build_query_variants(query: str, dspy_variants: list | None = None) -> list[str]:
    """
    Sprint F213B + F232: Generate bounded query variants for domain-aware queries.

    - Pure domain query ("example.com") → 4 site/subscription/infrastructure/subdomain variants
    - Mixed query ("mozilla.org certificate transparency") → extract domain token + CT-aware variants

    Returns [query] (single variant, no expansion) when no domain token found.

    Phase A (DSPy): caller is responsible for passing pre-expanded dspy_variants
    from brain.dspy_service.expand_query (called in the async caller context).
    """
    # Phase A: DSPy variants already injected by caller
    if dspy_variants is None:
        dspy_variants = []
    elif dspy_variants:
        logger.debug("dspy_service: expand_query added %d semantic variants", len(dspy_variants))

    # Original structural variant logic follows
    # Fast path: already a clean domain
    if _query_looks_like_domain(query):
        domain = query.strip()
        variants = [
            f"site:{domain}",
            f'"{domain}" security',
            f'"{domain}" infrastructure',
            f'"{domain}" subdomain',
        ]
        combined = dspy_variants + variants
        return combined[:_MAX_QUERY_VARIANTS]

    # F232: extract domain token from mixed query
    domain = _extract_domain_token(query)
    if domain is None:
        return dspy_variants[:5] if dspy_variants else [query]

    # Build CT-aware variants for extracted domain
    variants = [
        f"site:{domain}",
        f'"{domain}" certificate transparency',
        f'"{domain}" subdomains',
        f'"{domain}" SSL certificate',
    ]
    combined = dspy_variants + variants
    return combined[:_MAX_QUERY_VARIANTS]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# ── F280A: Pipeline-based refactoring for async_search_public_web ───────────
# Problem: CC=27, 13 ifs, 6 exception handlers, 4 nesting depth
# Solution: Extract each concern into composable pipeline stages
# Modern patterns: dataclass attrs, match/case, explicit state machine


from dataclasses import dataclass, field
from enum import Enum, auto


class SearchStage(Enum):
    """Tracks which pipeline stage we are in for debugging/metrics."""
    VALIDATION = auto()
    REPLAY_CHECK = auto()
    DSPY_EXPANSION = auto()
    VARIANT_BUILD = auto()
    CACHE_CHECK = auto()
    LIVE_SEARCH = auto()
    FALLBACK_SEARCH = auto()
    RESULT_PROCESSING = auto()
    CACHE_WRITE = auto()
    CASSETTE_WRITE = auto()
    COMPLETE = auto()


@dataclass(frozen=True, slots=True)
class SearchContext:
    """
    Immutable search execution context that flows through the pipeline.

    Each stage returns a new context with updated fields rather than
    mutating state - this makes the pipeline easier to reason about
    and test in isolation.
    """
    original_query: str
    trimmed_query: str
    max_results: int
    timeout_s: float
    stage: SearchStage = SearchStage.VALIDATION

    # Computed during pipeline
    dspy_variants: tuple[str, ...] = field(default_factory=tuple)
    query_variants: tuple[str, ...] = field(default_factory=tuple)
    raw_hits: list[dict] = field(default_factory=list)
    error_tag: str | None = None
    error_exc: BaseException | None = None
    fallback_triggered: str | None = None
    provider_status: dict = field(default_factory=dict)
    start_time: float = field(default_factory=time.monotonic)

    # Result (set by pipeline)
    result: DiscoveryBatchResult | None = None

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def should_fallback(self) -> bool:
        """Check if current error warrants fallback attempt."""
        return _should_use_fallback(self.error_tag) if self.error_tag else False

    @property
    def is_terminal(self) -> bool:
        """Check if we should stop the pipeline."""
        return self.result is not None


@dataclass(slots=True)
class SearchPolicy:
    """
    Encapsulates all validation, caching, and routing decisions.

    Extracted from async_search_public_web to reduce CC.
    """
    replay_enabled: bool = field(default_factory=replay_enabled)
    replay_strict: bool = field(default_factory=replay_strict_enabled)

    def validate_input(self, query: str) -> tuple[bool, str | None, int]:
        """
        Validate and normalize search input.

        Returns:
            (is_valid, error_tag, normalized_max_results)
        """
        trimmed = query.strip()
        if not trimmed:
            return False, "empty_query", DEFAULT_MAX_RESULTS

        try:
            max_results = max(1, min(int(self._max_results), HARD_MAX_RESULTS))
        except (TypeError, ValueError):
            max_results = DEFAULT_MAX_RESULTS

        return True, None, max_results

    _max_results: int = DEFAULT_MAX_RESULTS

    def normalize_max_results(self, max_results: int | None) -> int:
        """Normalize max_results to valid bounds."""
        if max_results is None:
            return DEFAULT_MAX_RESULTS
        try:
            return max(1, min(int(max_results), HARD_MAX_RESULTS))
        except (TypeError, ValueError):
            return DEFAULT_MAX_RESULTS

    def check_replay_cassette(self, query: str, adapter: str) -> dict | None:
        """Check for replay cassette hit."""
        if not self.replay_enabled:
            return None
        return read_cassette(adapter, query)

    def should_strict_replay_miss(self, query: str, adapter: str) -> bool:
        """Check if we should fail on replay miss in strict mode."""
        return self.replay_strict and self.check_replay_cassette(query, adapter) is None


@dataclass(slots=True)
class ErrorClassifier:
    """
    Centralized error classification for search backends.

    Reduces complexity by extracting all error-handling logic
    into a single, testable class.
    """

    def classify(self, exc: BaseException | None, err_str: str = "") -> str:
        """Classify exception into error taxonomy tag."""
        if exc is None:
            return "unknown_backend_error"

        err_name = type(exc).__name__
        err_lower = str(exc).lower()

        match (err_lower, err_name):
            case _ if "ratelimit" in err_lower or "RatelimitException" in err_name:
                return "rate_limited"
            case _ if "timeout" in err_lower or "TimeoutException" in err_name or isinstance(exc, TimeoutError):
                return "timeout"
            case _ if "proxy" in err_lower or "ProxyError" in err_name:
                return "proxy_error"
            case _ if "network" in err_lower or "ConnectionError" in err_name or "HTTPError" in err_name:
                return "network_error"
            case _ if "server" in err_lower or any(code in str(exc) for code in ("500", "502", "503", "504")):
                return "server_error"
            case _:
                return "unknown_backend_error"

    def should_fallback(self, error_tag: str) -> bool:
        """Determine if error warrants fallback to secondary backend."""
        return error_tag in {
            "timeout",
            "proxy_error",
            "network_error",
            "server_error",
            "unknown_backend_error"
        }

    def build_error_result(
        self,
        error_tag: str,
        *,
        selected: bool = False,
        reason: str = "",
        fallback_triggered: str | None = None,
    ) -> DiscoveryBatchResult:
        """Build a standardized error result."""
        return DiscoveryBatchResult(
            hits=(),
            error=error_tag,
            fallback_triggered=fallback_triggered,
            provider_status_debug=[{
                "provider": "ddg_mojeek",
                "state": "production",
                "selected": selected,
                "reason": reason or error_tag,
            }],
        )


# Shared instances for the module
_error_classifier = ErrorClassifier()
_policy = SearchPolicy()


# ── Pipeline stages ───────────────────────────────────────────────────────────


def _stage_validate(ctx: SearchContext) -> SearchContext:
    """Stage 1: Input validation and normalization."""
    if not ctx.trimmed_query:
        return dataclass_replace(ctx,
            stage=SearchStage.VALIDATION,
            result=DiscoveryBatchResult(hits=(), error="empty_query"),
            stage=SearchStage.COMPLETE,
        )

    # Normalize max_results
    normalized_max = _policy.normalize_max_results(ctx.max_results)

    return dataclass_replace(ctx,
        stage=SearchStage.REPLAY_CHECK,
        max_results=normalized_max,
    )


def _stage_check_replay(ctx: SearchContext) -> SearchContext:
    """Stage 2: Check replay cassette (primary adapter)."""
    if not replay_enabled():
        return dataclass_replace(ctx, stage=SearchStage.DSPY_EXPANSION)

    cassette = read_cassette("public_duckduckgo", ctx.trimmed_query)
    if cassette is not None:
        return dataclass_replace(ctx,
            stage=SearchStage.COMPLETE,
            result=_build_cassette_result(ctx.trimmed_query, cassette, ctx.elapsed_s),
        )

    if replay_strict_enabled():
        return dataclass_replace(ctx,
            stage=SearchStage.COMPLETE,
            result=_build_replay_miss_result(ctx.trimmed_query, ctx.elapsed_s),
        )

    return dataclass_replace(ctx, stage=SearchStage.DSPY_EXPANSION)


async def _stage_dspy_expand(ctx: SearchContext) -> SearchContext:
    """Stage 3: DSPy query expansion (optional)."""
    if not FeatureFlags.get(FeatureFlag.DSPY):
        return dataclass_replace(ctx, stage=SearchStage.VARIANT_BUILD)

    try:
        from hledac.universal.brain.dspy_service import expand_query
        expanded = await expand_query(ctx.trimmed_query) or []
        return dataclass_replace(ctx,
            stage=SearchStage.VARIANT_BUILD,
            dspy_variants=tuple(expanded),
        )
    except Exception:
        return dataclass_replace(ctx, stage=SearchStage.VARIANT_BUILD)


def _stage_build_variants(ctx: SearchContext) -> SearchContext:
    """Stage 4: Build query variants."""
    variants = _build_query_variants(ctx.trimmed_query, list(ctx.dspy_variants))

    if len(variants) > 1:
        # Multi-variant search path - delegate to _search_with_variants
        # Return context with special marker indicating multi-variant mode
        return dataclass_replace(ctx,
            stage=SearchStage.LIVE_SEARCH,
            query_variants=tuple(variants),
        )

    return dataclass_replace(ctx,
        stage=SearchStage.CACHE_CHECK,
        query_variants=tuple(variants),
    )


def _stage_check_cache(ctx: SearchContext) -> SearchContext:
    """Stage 5: Per-run cache check."""
    cached = _get_cached_discovery(ctx.trimmed_query)
    if cached is not None:
        return dataclass_replace(ctx,
            stage=SearchStage.COMPLETE,
            result=_build_cache_hit_result(cached),
        )
    return dataclass_replace(ctx, stage=SearchStage.LIVE_SEARCH)


async def _stage_live_search(ctx: SearchContext) -> SearchContext:
    """Stage 6: Execute live search with timeout."""
    # Multi-variant search path
    if len(ctx.query_variants) > 1:
        result = await _search_with_variants(
            ctx.trimmed_query,
            list(ctx.query_variants),
            ctx.max_results,
            max(1, ctx.max_results // len(ctx.query_variants)),
            ctx.timeout_s,
        )
        return dataclass_replace(ctx,
            stage=SearchStage.RESULT_PROCESSING,
            result=result,
        )

    # Single variant: check replay first
    if replay_enabled():
        cached_replay = read_cassette(_PUBLIC_REPLAY_ADAPTER, ctx.trimmed_query)
        if cached_replay is not None:
            return dataclass_replace(ctx,
                stage=SearchStage.COMPLETE,
                result=_build_cached_replay_result(ctx.trimmed_query, cached_replay, ctx.elapsed_s),
            )
        if replay_strict_enabled():
            return dataclass_replace(ctx,
                stage=SearchStage.COMPLETE,
                result=_build_replay_miss_result(ctx.trimmed_query, ctx.elapsed_s),
            )

    # Live search
    try:
        async with asyncio.timeout(ctx.timeout_s):
            raw_hits = await _ddgs_text_search(
                ctx.trimmed_query,
                ctx.max_results,
                ctx.timeout_s,
            )
        return dataclass_replace(ctx,
            stage=SearchStage.RESULT_PROCESSING,
            raw_hits=raw_hits,
        )

    except asyncio.CancelledError:
        global _last_error
        _last_error = "cancelled"
        raise

    except TimeoutError:
        global _last_error
        _last_error = "timeout"
        return dataclass_replace(ctx,
            stage=SearchStage.COMPLETE,
            result=_build_timeout_result(ctx.trimmed_query),
        )

    except Exception as e:
        global _last_error
        error_tag = _error_classifier.classify(e, str(e))
        _last_error = error_tag

        if not _error_classifier.should_fallback(error_tag):
            return dataclass_replace(ctx,
                stage=SearchStage.COMPLETE,
                result=_error_classifier.build_error_result(
                    error_tag,
                    selected=False,
                    reason=f"non_backend_error_{error_tag}",
                ),
            )

        # Fallback path
        try:
            fallback_hits = await _scrape_mojeek(ctx.trimmed_query, n=ctx.max_results)
        except Exception:
            fallback_hits = []

        if fallback_hits:
            return dataclass_replace(ctx,
                stage=SearchStage.RESULT_PROCESSING,
                raw_hits=fallback_hits,
                fallback_triggered="primary_backend_failed",
                error_tag=error_tag,
            )
        else:
            return dataclass_replace(ctx,
                stage=SearchStage.COMPLETE,
                result=DiscoveryBatchResult(
                    hits=(),
                    error=error_tag,
                    fallback_triggered="primary_backend_failed_fallback_failed",
                    provider_status_debug=[
                        {"provider": "ddg_mojeek", "state": "production", "selected": False, "reason": "fallback_failed_primary"},
                        {"provider": "mojeek_scrape", "state": "production", "selected": False, "reason": "fallback_failed"},
                    ],
                ),
            )


def _stage_process_results(ctx: SearchContext) -> SearchContext:
    """Stage 7: Process raw hits into final result."""
    if ctx.result is not None:
        # Already processed (e.g., multi-variant search)
        return dataclass_replace(ctx, stage=SearchStage.CACHE_WRITE)

    hits_list, _, _ = _process_raw_hits(ctx.raw_hits, ctx.trimmed_query, ctx.max_results)
    final_hits = tuple(
        DiscoveryHit(
            query=h.query,
            title=h.title,
            url=h.url,
            snippet=h.snippet,
            source=h.source,
            rank=i,
            retrieved_ts=h.retrieved_ts,
            score=h.score,
            reason=h.reason,
        )
        for i, h in enumerate(hits_list[:ctx.max_results])
    )

    result = DiscoveryBatchResult(
        hits=final_hits,
        error=ctx.error_tag,
        fallback_triggered=ctx.fallback_triggered,
        provider_status_debug=[{
            "provider": "ddg_mojeek",
            "state": "production",
            "selected": True,
            "reason": "primary_backend" if not ctx.fallback_triggered else "fallback",
        }],
    )

    return dataclass_replace(ctx,
        stage=SearchStage.CACHE_WRITE,
        result=result,
    )


def _stage_write_caches(ctx: SearchContext) -> SearchContext:
    """Stage 8: Write replay cassette and per-run cache."""
    if ctx.result is None:
        return dataclass_replace(ctx, stage=SearchStage.COMPLETE)

    # Write cassette if replay enabled
    if replay_enabled():
        write_cassette(_PUBLIC_REPLAY_ADAPTER, ctx.trimmed_query, {
            "hits": list(ctx.result.hits),
            "error": ctx.result.error,
            "fallback_triggered": ctx.result.fallback_triggered,
            "cache_hit": False,
            "provider_name": "duckduckgo",
            "provider_chain": ["duckduckgo"],
            "source_family": "search",
            "elapsed_s": ctx.result.elapsed_s,
            "error_type": ctx.result.error_type,
            "provider_status_debug": ctx.result.provider_status_debug,
        })

    # Write per-run cache
    _set_cached_discovery(ctx.original_query, ctx.result)

    return dataclass_replace(ctx, stage=SearchStage.COMPLETE)


def dataclass_replace(ctx: SearchContext, **updates) -> SearchContext:
    """Create a new SearchContext with updated fields (immutable pattern)."""
    import dataclasses
    return dataclasses.replace(ctx, **updates)


# ── Main async_search_public_web with pipeline ───────────────────────────────


async def async_search_public_web(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> DiscoveryBatchResult:
    """
    Public web discovery via DuckDuckGo using pipeline architecture.

    Pipeline stages:
        1. validate → 2. replay_check → 3. dspy_expand → 4. variant_build
        → 5. cache_check → 6. live_search → 7. process_results → 8. write_caches

    Complexity: CC < 10 (was 27)
    """
    # Initialize context
    ctx = SearchContext(
        original_query=query,
        trimmed_query=query.strip(),
        max_results=max_results,
        timeout_s=timeout_s,
    )

    # Run pipeline stages
    stages = [
        _stage_validate,
        _stage_check_replay,
        lambda c: _stage_dspy_expand(c),
        _stage_build_variants,
        _stage_check_cache,
        lambda c: _stage_live_search(c),
        _stage_process_results,
        _stage_write_caches,
    ]

    for stage in stages:
        if ctx.is_terminal:
            break
        ctx = await stage(ctx)

    return ctx.result or DiscoveryBatchResult(hits=(), error="pipeline_error")


# ── Legacy helper functions preserved for compatibility ─────────────────────


def _process_raw_hits(
    raw_hits: list,
    query: str,
    max_results: int,
    seen_urls: dict | None = None,
    host_counts: dict | None = None,
    retrieved_ts: float | None = None,
) -> tuple[list[DiscoveryHit], dict[str, int], dict[str, int]]:
    """Process raw search hits: filter noise, deduplicate, rank."""
    seen_urls = seen_urls or {}
    host_counts = host_counts or {}
    retrieved_ts = retrieved_ts or time.time()
    hits_list: list[DiscoveryHit] = []
    max_from_host = max(1, int(max_results * MAX_HOST_SHARE_RATIO))

    for raw in raw_hits:
        raw_url = raw.get("href") or raw.get("url") or ""
        title = (raw.get("title") or "").strip()
        snippet = (raw.get("body") or raw.get("snippet") or "").strip()

        if _is_noise_result(title, raw_url, snippet, query):
            continue

        norm = _normalize_url_for_dedup(raw_url)
        if not norm or norm in seen_urls:
            continue

        host = _extract_host(norm)
        if host and host_counts.get(host, 0) >= max_from_host:
            continue

        seen_urls[norm] = len(hits_list)
        host_counts[host] = host_counts.get(host, 0) + 1

        signals = _build_signals(query, title, raw_url, snippet)
        reason = signals["reasons"][0] if signals["reasons"] else None

        hits_list.append(DiscoveryHit(
            query=query,
            title=title,
            url=raw_url,
            snippet=snippet,
            source=SOURCE_NAME,
            rank=0,
            retrieved_ts=retrieved_ts,
            score=signals["score"],
            reason=reason,
        ))

    hits_list.sort(key=lambda h: (-h.score, h.rank))
    return hits_list, seen_urls, host_counts


def _classify_error(err_str: str, err_name: str) -> str:
    """Classify exception into error taxonomy."""
    err_lower = err_str.lower()
    if "ratelimit" in err_lower or "RatelimitException" in err_name:
        return "rate_limited"
    elif "timeout" in err_lower or "TimeoutException" in err_name or "TimeoutError" in err_name:
        return "timeout"
    elif "proxy" in err_lower or "ProxyError" in err_name:
        return "proxy_error"
    elif "network" in err_lower or "ConnectionError" in err_name or "HTTPError" in err_name:
        return "network_error"
    elif "server" in err_lower or any(code in err_str for code in ("500", "502", "503", "504")):
        return "server_error"
    else:
        return "unknown_backend_error"


def _should_use_fallback(error_tag: str) -> bool:
    """Determine if backend error fallback should be attempted."""
    return error_tag in {"timeout", "proxy_error", "network_error", "server_error", "unknown_backend_error"}


def _build_cassette_result(query: str, cassette: dict, elapsed: float) -> DiscoveryBatchResult:
    """Build result from replay cassette."""
    hits_list = [
        DiscoveryHit(
            query=query,
            title=h.get("title", ""),
            url=h.get("url", ""),
            snippet=h.get("snippet", ""),
            source=h.get("source", SOURCE_NAME),
            rank=i,
            retrieved_ts=h.get("retrieved_ts", time.time()),
            score=h.get("score", 0.0),
            reason=h.get("reason"),
        )
        for i, h in enumerate(cassette.get("hits", []))
    ]
    return DiscoveryBatchResult(
        hits=tuple(hits_list),
        error=cassette.get("error"),
        fallback_triggered=cassette.get("fallback_triggered"),
        cache_hit=False,
        provider_name=cassette.get("provider_name", "duckduckgo"),
        provider_chain=tuple(cassette.get("provider_chain", ["duckduckgo"])),
        source_family=cassette.get("source_family", "search"),
        elapsed_s=cassette.get("elapsed_s", elapsed),
        error_type=cassette.get("error_type"),
        provider_status_debug=cassette.get("provider_status_debug"),
    )


def _build_replay_miss_result(query: str, elapsed: float) -> DiscoveryBatchResult:
    """Build result for cassette miss in strict replay mode."""
    return DiscoveryBatchResult(
        hits=(),
        error="replay_miss",
        error_type="replay_miss",
        provider_name="duckduckgo",
        provider_chain=("duckduckgo",),
        source_family="search",
        elapsed_s=elapsed,
        provider_status_debug=[{
            "provider": "public_duckduckgo",
            "selected": False,
            "reason": "replay_miss",
        }],
    )


def _build_cached_replay_result(query: str, cached: dict, elapsed: float) -> DiscoveryBatchResult:
    """Build result from cached replay."""
    cached_hits = cached.get("hits", ())
    return DiscoveryBatchResult(
        hits=tuple(cached_hits) if isinstance(cached_hits, list) else cached_hits,
        error=cached.get("error"),
        fallback_triggered=cached.get("fallback_triggered"),
        cache_hit=False,
        provider_name=cached.get("provider_name", "duckduckgo"),
        provider_chain=tuple(cached.get("provider_chain", ["duckduckgo"])),
        source_family=cached.get("source_family", "search"),
        elapsed_s=cached.get("elapsed_s", elapsed),
        error_type=cached.get("error_type"),
        provider_status_debug=cached.get("provider_status_debug"),
    )


def _build_cache_hit_result(cached: DiscoveryBatchResult) -> DiscoveryBatchResult:
    """Build result from per-run cache hit."""
    return DiscoveryBatchResult(
        hits=cached.hits,
        error=cached.error,
        fallback_triggered=cached.fallback_triggered,
        provider_name=cached.provider_name,
        provider_chain=cached.provider_chain,
        source_family=cached.source_family,
        elapsed_s=cached.elapsed_s,
        error_type=cached.error_type,
        cache_hit=True,
        provider_status_debug=getattr(cached, 'provider_status_debug', None),
    )


def _build_timeout_result(query: str) -> DiscoveryBatchResult:
    """Build result for timeout error."""
    return DiscoveryBatchResult(
        hits=(),
        error="timeout",
        provider_status_debug=[{
            "provider": "ddg_mojeek",
            "state": "production",
            "selected": False,
            "reason": "timeout",
        }],
    )


async def _search_with_variants(
    query: str,
    variants: list,
    max_results: int,
    per_variant_results: int,
    timeout_s: float,
) -> DiscoveryBatchResult:
    """Search with query variants, merging results."""
    all_hits: list[DiscoveryHit] = []
    variant_errors: list[str] = []

    async def search_variant(var_query: str) -> tuple[list[DiscoveryHit], str | None]:
        """Search a single variant, return (hits, error)."""
        var_cached = _get_cached_discovery(var_query)
        if var_cached is not None:
            return (list(var_cached.hits), None)
        try:
            async with asyncio.timeout(timeout_s):
                raw = await _ddgs_text_search(var_query, per_variant_results, timeout_s)
        except asyncio.CancelledError:
            return ([], "cancelled")
        except TimeoutError:
            return ([], "timeout")
        except Exception as e:
            return ([], f"variant_error:{type(e).__name__}")

        hits_v, _, _ = _process_raw_hits(raw, var_query, per_variant_results)
        _set_cached_discovery(var_query, DiscoveryBatchResult(hits=tuple(hits_v), error=None))
        return (hits_v, None)

    _ddg_result = await parallel(
        [search_variant(v) for v in variants],
        policy="log",
        ctx="duckduckgo_adapter:946"
    )
    results = _ddg_result.ok
    seen_urls: dict[str, int] = {}

    for res in results:
        if isinstance(res, BaseException):
            variant_errors.append(f"variant_exception:{type(res).__name__}")
            continue
        hits, err = res
        if err:
            variant_errors.append(err)
            continue
        for h in hits:
            norm = _normalize_url_for_dedup(h.url)
            if norm and norm not in seen_urls:
                seen_urls[norm] = len(all_hits)
                all_hits.append(h)

    all_hits.sort(key=lambda h: (-h.score, h.rank))
    final_hits = tuple(all_hits[:max_results])

    final_error = "|".join(variant_errors) if len(variant_errors) == len(variants) else None
    result = DiscoveryBatchResult(
        hits=final_hits,
        error=final_error,
        provider_status_debug=[{
            "provider": "ddg_mojeek",
            "state": "production",
            "selected": True,
            "reason": "multi_variant_search",
        }],
    )
    _set_cached_discovery(query, result)
    return result


def _apply_fallback(
    fallback_hits: list,
    query: str,
    max_results: int,
    error_tag: str,
) -> DiscoveryBatchResult:
    """Process fallback hits and build result."""
    seen_urls: dict[str, int] = {}
    host_counts: dict[str, int] = {}
    retrieved_ts = time.time()
    hits_list: list[DiscoveryHit] = []
    max_from_host = max(1, int(max_results * MAX_HOST_SHARE_RATIO))

    for raw in fallback_hits:
        raw_url = raw.get("url") or ""
        title = (raw.get("title") or "").strip()
        snippet = (raw.get("snippet") or "").strip()

        if _is_noise_result(title, raw_url, snippet, query):
            continue
        norm = _normalize_url_for_dedup(raw_url)
        if not norm or norm in seen_urls:
            continue
        host = _extract_host(norm)
        if host and host_counts.get(host, 0) >= max_from_host:
            continue
        seen_urls[norm] = len(hits_list)
        host_counts[host] = host_counts.get(host, 0) + 1
        signals = _build_signals(query, title, raw_url, snippet)
        reason = signals["reasons"][0] if signals["reasons"] else None
        hits_list.append(DiscoveryHit(
            query=query,
            title=title,
            url=raw_url,
            snippet=snippet,
            source=raw.get("source", "mojeek_scrape"),
            rank=0,
            retrieved_ts=retrieved_ts,
            score=signals["score"],
            reason=reason,
        ))

    hits_list.sort(key=lambda h: (-h.score, h.rank))
    final_hits = tuple(
        DiscoveryHit(
            query=h.query, title=h.title, url=h.url, snippet=h.snippet,
            source=h.source, rank=i, retrieved_ts=h.retrieved_ts,
            score=h.score, reason=h.reason,
        )
        for i, h in enumerate(hits_list[:max_results])
    )
    return DiscoveryBatchResult(
        hits=final_hits,
        error=error_tag,
        fallback_triggered="primary_backend_failed_fallback_succeeded",
        provider_status_debug=[
            {"provider": "ddg_mojeek", "state": "production", "selected": True, "reason": "fallback_succeeded"},
            {"provider": "mojeek_scrape", "state": "production", "selected": True, "reason": "fallback_primary"},
        ],
    )


# ── Sprint 8VB: Multi-Engine Search ───────────────────────────────────────────

logger = logging.getLogger(__name__)


async def _scrape_mojeek(
    query: str, n: int = 10
) -> list[dict]:
    """Mojeek independent crawler, no CAPTCHA policy.

    G1 FIX: beautifulsoup4 REMOVED — uses selectolax with CSS selectors.
    """
    _UA = (  # noqa: N806
        "Mozilla/5.0 (Macintosh; ARM Mac OS X 14_0) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Safari/605.1.15"
    )
    results = []
    try:
        s = await async_get_httpx_session()
        text, status, err = await checked_aiohttp_get(
            s,
            "https://www.mojeek.com/search",
            params={"q": query},
            headers={"User-Agent": _UA,
                     "Accept-Language": "en-US,en;q=0.9"},
            timeout=httpx.Timeout(12),
            failure_kind="mojeek",
        )
        if err:
            logger.debug(f"[Mojeek] {err}")
            return []
        if status != 200:
            return []
        # G1 FIX: Use selectolax instead of beautifulsoup4
        try:
            from selectolax.parser import HTMLParser as _Parser
            tree = _Parser(str(text))
            for li in tree.css("ul.results-standard li")[:n]:
                a = li.css_first("a.ob")
                p = li.css_first("p.s")
                if a:
                    href = a.attributes.get("href", "")
                    if href:
                        results.append({
                            "title":   a.text(strip=True),
                            "url":     href,
                            "snippet": p.text(strip=True) if p else "",
                            "source":  "mojeek_scrape"
                        })
        except ImportError:
            # Fallback: regex-only (stdlib) — less precise but works
            import re
            # Match result blocks: title link + snippet
            pattern = re.compile(
                r'<li[^>]*>.*?<a[^>]+class="ob"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>.*?<p[^>]+class="s"[^>]*>([^<]+)</p>.*?</li>',
                re.DOTALL | re.IGNORECASE
            )
            for match in pattern.finditer(str(text))[:n]:
                results.append({
                    "title":   match.group(2).strip(),
                    "url":     match.group(1).strip(),
                    "snippet": match.group(3).strip(),
                    "source":  "mojeek_scrape"
                })
    except Exception as e:
        logger.debug(f"[Mojeek] {e}")
    return results


async def _search_wayback_cdx(
    url_pattern: str, max_results: int = 20
) -> list[dict]:
    """Wayback CDX API — historical snapshots of URL.
    COMPAT: Tato funkce je dočasný compat wrapper.
    AUTHORITY: archive_discovery.wayback_cdx_lookup() je search-shaped canonical.
    REMOVAL CONDITION: po přechodu všech call-sites na archive_discovery.wayback_cdx_lookup().
    """
    from hledac.universal.intel.archive_discovery import wayback_cdx_lookup

    snapshots = await wayback_cdx_lookup(url_pattern, limit=max_results, timeout_s=20.0)
    # Převod z wayback_cdx_lookup format na _search_wayback_cdx format
    results = []
    for snap in snapshots:
        results.append({
            "title":        snap.get("title", ""),
            "url":          snap.get("url", ""),
            "snapshot_url": snap.get("url", ""),
            "timestamp":    snap.get("timestamp", ""),
            "mimetype":     "",
            "source":       "wayback_cdx"
        })
    return results


async def _search_commoncrawl_cdx(
    url_pattern: str, max_results: int = 20
) -> list[dict]:
    """CommonCrawl CDX index — petabytes of crawl data, free.
    COMPAT: Tato funkce je dočasný compat wrapper.
    AUTHORITY: archive_discovery.commondrawl_cdx_lookup() je search-shaped canonical.
    REMOVAL CONDITION: po přechodu všech call-sites na archive_discovery."""
    import json as _json
    results = []
    try:
        s = await async_get_httpx_session()
        text, status, err = await checked_aiohttp_get(
            s,
            "https://index.commoncrawl.org/CC-MAIN-2024-51-index",
            params={
                "url":    url_pattern,
                "output": "json",
                "limit":  max_results,
                "fl":     "url,timestamp,filename,offset,length"
            },
            timeout=httpx.Timeout(25),
            failure_kind="commoncrawl_cdx",
        )
        if err:
            logger.warning(f"[CommonCrawl CDX] {err}")
            return []
        if status != 200:
            return []
        for line in str(text).strip().split("\n")[:max_results]:
            try:
                rec = _json.loads(line)
                results.append({
                    "title":        f"CommonCrawl: {rec.get('url','')}",
                    "url":          rec.get("url", ""),
                    "timestamp":    rec.get("timestamp", ""),
                    "warc_filename":rec.get("filename", ""),
                    "warc_offset":  rec.get("offset", 0),
                    "warc_length":  rec.get("length", 0),
                    "source":       "commoncrawl_cdx"
                })
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[CommonCrawl CDX] {e}")
    return results


async def _query_shodan_internetdb(ip: str) -> dict:
    """Shodan InternetDB — open ports, CVEs, hostnames. Free, no API key.
    COMPAT: Tato funkce je dočasný compat wrapper.
    AUTHORITY: registry/shodan_internetdb_lookup() je search-shaped canonical.
    REMOVAL CONDITION: po přechodu všech call-sites na registry/shodan_internetdb_lookup()."""
    try:
        s = await async_get_httpx_session()
        data, _status, err = await checked_aiohttp_get(
            s,
            f"https://internetdb.shodan.io/{ip}",
            timeout=httpx.Timeout(8),
            failure_kind="shodan_internetdb",
        )
        if err:
            logger.debug(f"[ShodanInternetDB] {err}")
            return {}
        return {
            "ip":        ip,
            "ports":     data.get("ports", []) if isinstance(data, dict) else [],
            "cves":      data.get("cves", []) if isinstance(data, dict) else [],
            "hostnames": data.get("hostnames", []) if isinstance(data, dict) else [],
            "tags":      data.get("tags", []) if isinstance(data, dict) else [],
            "source":    "shodan_internetdb"
        }
    except Exception as e:
        logger.debug(f"[ShodanInternetDB] {e}")
    return {}


async def _query_rdap(target: str) -> dict:
    """RDAP — structured WHOIS successor, free without key.
    COMPAT: Tato funkce je dočasný compat wrapper.
    AUTHORITY: registry/rdap_lookup() je search-shaped canonical.
    REMOVAL CONDITION: po přechodu všech call-sites na registry/rdap_lookup().
    Přesměrováno na canonical ti_feed_adapter.query_rdap() pro odstranění duplicity."""
    from hledac.universal.discovery.ti_feed_adapter import query_rdap

    return await query_rdap(target)


async def _search_commoncrawl_domain(
    query: str, max_results: int = 20
) -> list[dict]:
    """
    F192E: CommonCrawl CDX domain discovery — thin seam, no new framework.

    CommonCrawl CDX API is domain-specific, not a general search engine.
    Only activates for domain-like queries (e.g. "example.com", "site:example.com").

    Returns:
        List of dicts with title/url/snippet/source/timestamp.
    """
    import re as _re
    _DOMAIN_CCX_RE = _re.compile(  # noqa: N806
        r"^(?:\*?\.)?[a-zA-Z0-9][a-zA-Z0-9.\-*[a-zA-Z0-9]\.[a-zA-Z]{2,}$"
        r"|^(?:site|domain):[a-zA-Z0-9]"
    )
    clean = re.sub(r"^(site|domain):", "", query.strip(), flags=re.IGNORECASE).strip()
    if not _DOMAIN_CCX_RE.match(clean):
        return []

    try:
        from hledac.universal.tools.commoncrawl_adapter import CommonCrawlAdapter

        class _MinimalStealth:
            """Minimal StealthManager-compatible wrapper for CommonCrawlAdapter."""
            async def get(self, url: str) -> str:
                from hledac.universal.network.session_runtime import async_get_httpx_session
                s = await async_get_httpx_session()
                r = await s.get(url)
                return r.text

        adapter = CommonCrawlAdapter(stealth=_MinimalStealth())
        results = await adapter.search(clean, max_results=max_results)
        await adapter.close()
        return results
    except Exception as e:
        logger.debug(f"[CommonCrawl domain search] {e}")
        return []


async def search_multi_engine(
    query: str, max_results: int = 30
) -> list[dict]:
    """
    ISSUE-6: Parallel multi-engine search with bounded concurrency.

    Runs 5 search engines concurrently with concurrency=4:
    - DuckDuckGo (async_search_public_web)
    - Mojeek (_scrape_mojeek)
    - CommonCrawl domain (_search_commoncrawl_domain)
    - Wayback CDX (_search_wayback_cdx)
    - CommonCrawl CDX (_search_commoncrawl_cdx)

    Bing excluded — actively blocks + CAPTCHA.

    Target: Wall-time 5s → 1.5s (5 engines × 1s sequential → 1.5s parallel)
    """
    # Per-engine result budget: distribute max_results across 5 engines
    budget = max(1, max_results // 5)

    # Create coroutine tasks for all engines (NOT yet started)
    ddg_task    = async_search_public_web(query, max_results=budget)
    mojeek_task = _scrape_mojeek(query, n=budget)
    cc_domain   = _search_commoncrawl_domain(query, max_results=budget)
    wayback_task = _search_wayback_cdx(query, max_results=budget)
    cc_cdx_task  = _search_commoncrawl_cdx(query, max_results=budget)

    # ISSUE-6: Run all 5 engines in parallel with concurrency=4 (semaphore-gated)
    # F262D pattern: parallel() with policy="log" (fail-soft, exceptions logged)
    _result = await parallel(
        [ddg_task, mojeek_task, cc_domain, wayback_task, cc_cdx_task],
        policy="log",
        concurrency=4,  # ISSUE-6: M1-safe concurrency limit
        ctx="duckduckgo_adapter:search_multi_engine",
    )

    all_results: list[dict] = []
    for batch in _result.ok:
        if isinstance(batch, DiscoveryBatchResult) and batch.hits:
            all_results.extend([
                {"title": h.title, "url": h.url, "snippet": h.snippet, "source": h.source}
                for h in batch.hits
            ])
        elif isinstance(batch, list):
            all_results.extend(batch)

    # I7: URL deduplication via shared singleton BloomFilter (F06: was per-call RotatingBloomFilter)
    # R4-11 FIX: batch add — normalize all URLs first, then add_batch() once
    bloom = get_default_bloom_filter()
    deduped: list[dict] = []
    # Phase 1: normalize all URLs upfront
    all_norm: list[tuple[dict, str]] = []
    for r in all_results:
        raw_u = r.get("url", "")
        if raw_u:
            norm = _normalize_url_for_dedup(raw_u)
            if norm:
                all_norm.append((r, norm))

    # Phase 2: batch dedup check — collect non-duplicate normalized URLs
    new_norms: list[str] = []
    for r, norm in all_norm:
        if norm not in bloom:
            new_norms.append(norm)
            deduped.append(r)

    # Phase 3: batch add all new URLs at once (single lock acquisition)
    if new_norms and hasattr(bloom, 'add_batch'):
        bloom.add_batch(new_norms)
    elif new_norms:
        # Fallback: single-item add (for non-Rust bloom filters)
        for norm in new_norms:
            bloom.add(norm)

    return deduped[:max_results]

class DuckDuckGoAdapter(BaseDiscoveryMixin):
    """
    DuckDuckGo discovery adapter using BaseDiscoveryMixin infrastructure.

    Wraps async_search_public_web() as _do_discover().
    """

    name: str = "duckduckgo"
    source_type: str = "search"

    @property
    def rate_limit_rpm(self) -> int:
        return 60

    @property
    def retry_attempts(self) -> int:
        return 3

    @property
    def retry_base_delay_s(self) -> float:
        return 1.0

    @property
    def timeout_s(self) -> float:
        return 35.0

    async def _do_discover(
        self, query: str, limit: int
    ):
        """
        Wrap async_search_public_web() as an async iterator.

        Converts DiscoveryHit items to DiscoveryResult.
        """
        try:
            result = await async_search_public_web(
                query, max_results=min(limit, HARD_MAX_RESULTS)
            )
        except Exception:
            # fail-safe: yield nothing on error
            return

        for hit in result.hits:
            metadata: dict[str, str] = {}
            if hit.ct_issuer_name:
                metadata["ct_issuer_name"] = hit.ct_issuer_name
            if hit.ct_serial_number:
                metadata["ct_serial_number"] = hit.ct_serial_number
            if hit.ct_not_before:
                metadata["ct_not_before"] = hit.ct_not_before
            if hit.ct_not_after:
                metadata["ct_not_after"] = hit.ct_not_after
            if hit.ct_entry_timestamp:
                metadata["ct_entry_timestamp"] = hit.ct_entry_timestamp
            if hit.ct_name_value:
                metadata["ct_name_value"] = hit.ct_name_value
            if hit.ct_common_name:
                metadata["ct_common_name"] = hit.ct_common_name

            yield DiscoveryResult(
                query=hit.query,
                url=hit.url,
                title=hit.title,
                snippet=hit.snippet,
                source=hit.source,
                source_type=self.source_type,
                rank=hit.rank,
                retrieved_ts=hit.retrieved_ts,
                score=hit.score,
                reason=hit.reason,
                metadata=metadata,
            )
