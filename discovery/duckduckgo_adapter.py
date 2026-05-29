"""
DuckDuckGo public web discovery adapter.

Backend: duckduckgo_search v8.1.1 (sync-only; async via asyncio.to_thread compatibility fallback)

INVARIANTS (Sprint 8AC):
- Public/passive-only; no auth, no cookies, no credentials
- No AO imports; no storage writes; no pattern matcher calls
- No import-time network side effects
- max_results hard cap = 50; default = 10
- asyncio.timeout() for timeout; CancelledError re-raised
- fail-soft for RatelimitException / TimeoutException / generic backend errors
- Per-call URL dedup with preserve-first ordering
- msgspec.Struct(frozen=True, gc=False) for all DTOs
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import urllib.parse as urlparse
from typing import TYPE_CHECKING

import aiohttp
import msgspec
from hledac.universal.tools.discovery_replay import (
    read_cassette,
    replay_enabled,
    replay_strict_enabled,
    write_cassette,
)
from hledac.universal.transport.circuit_breaker import (
    checked_aiohttp_get,
)

_PUBLIC_REPLAY_ADAPTER = "public_duckduckgo"

# Backend: ddgs v9+ (primary) or duckduckgo_search v8.x (fallback)
# Both provide DDGS.text() — async via asyncio.to_thread compatibility wrapper
if TYPE_CHECKING:
    try:
        from ddgs import DDGS  # noqa: F401
    except ImportError:
        from duckduckgo_search import DDGS  # noqa: F401


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


class DiscoveryHit(msgspec.Struct, frozen=True, gc=False):
    """
    Single web discovery result.

    All string fields are never None — None is normalized to "".
    score is a query-aware rank signal in [0.0, 1.0]; higher = more relevant.
    reason is an optional short tag describing why this hit ranked well.
    """

    query: str
    title: str
    url: str
    snippet: str
    source: str  # always "duckduckgo"
    rank: int
    retrieved_ts: float
    score: float = 0.0   # relevance signal, not guaranteed to be populated
    reason: str | None = None  # short tag: "exact_domain", "quoted_match", etc.
    # F213A: CT/crt.sh metadata — populated when DiscoveryHit originates from crtsh_adapter
    ct_issuer_name: str | None = None
    ct_serial_number: str | None = None
    ct_not_before: str | None = None
    ct_not_after: str | None = None
    ct_entry_timestamp: str | None = None
    ct_name_value: str | None = None
    ct_common_name: str | None = None


class DiscoveryBatchResult(msgspec.Struct, frozen=True, gc=False):
    """
    Result surface for a single discovery call.

    On any backend error the hits tuple is empty and error is set.
    On cancel (asyncio.CancelledError) the error is NOT swallowed —
    the exception is re-raised after the call unwinds.

    fallback_triggered is set when a bounded fallback was attempted
    after a primary-backend failure (backend_error / timeout).
    Values:
      - None                     : no fallback needed or used
      - "primary_backend_failed_fallback_succeeded"  : fallback returned hits
      - "primary_backend_failed_fallback_failed"    : fallback also returned empty

    provider_name: canonical name of the provider that produced hits (F206AM).
    provider_chain: ordered tuple of providers consulted (F206AM).
    source_family: logical family — "search" | "archive" | "historical" | None (F206AM).
    elapsed_s: wall-clock seconds for this call (F206AM).
    error_type: F206AB taxonomy category (F206AM).
    """

    hits: tuple[DiscoveryHit, ...]
    error: str | None = None
    fallback_triggered: str | None = None
    # F207I-A: per-run cache hit flag (True when result came from cache)
    cache_hit: bool = False
    # F206AM: additive fields for providerless mesh
    provider_name: str | None = None
    provider_chain: tuple[str, ...] = ()
    source_family: str | None = None
    elapsed_s: float | None = None
    error_type: str | None = None
    # F234-FIX / F253B: provider selection debug context
    provider_status_debug: list[dict] | None = None


# ---------------------------------------------------------------------------
# Discovery error taxonomy — F206AB
# ---------------------------------------------------------------------------


def classify_discovery_error(
    error: str | Exception | None,
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
    if any(kw in err_str.lower() for kw in ("captcha", "blocked", "403", "bot detection", "forbidden", "access denied")):
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
                import duckduckgo_search
                _backend_version = getattr(duckduckgo_search, "__version__", "unknown")
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
    r"(?:\w+\.){1,6}(?:com|org|net|io|co|uk|edu|gov|mil|info|biz|ru|cn|de|fr|nl|pl|eu|us|ca|au|at|be|ch|jp|kr|br|mx|za|in|it|es|nl|se|no|fi|dk|cz|sk|hu|ro|gr|pt|tr|il|ae|sa|ng|ke|gh|eg|ua|rs|by|kz|uz|tj|ir|iq|pk|bd|kh|la|mm|vn|th|my|sg|ph|id|tl|tz|et|zm|zw|bw|na|ug|rw|mw|mz|ao|ci|cm|sn|gd|jm|ht|cu|do|ve|co|pe|bo|cl|ar|uy|p ypy|py|pr|pa|cr|ni|sv|gt|hn|bz|gy|sr|gf|ec|py)")
_IOC_IP_RE = __import__("re").compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _tokenize_raw_query(query: str) -> set[str]:
    """Lower-case word tokens from the non-quoted part of the query."""
    return {
        t.lower().strip(".,;:!?()[]{}")
        for t in query.split()
        if len(t) > 1
    }


def _build_signals(
    query: str,
    title: str,
    url: str,
    snippet: str,
) -> dict:
    """
    Compute a small dict of query-aware signals for ranking.
    All text fields are lower-cased before comparison.
    """
    quoted_phrases, raw_query = _extract_quoted_tokens(query)
    query_tokens = _tokenize_raw_query(raw_query)
    lower_title = title.lower()
    lower_url = url.lower()
    lower_snippet = snippet.lower()

    score = 0.0
    reasons: list[str] = []

    # Exact quoted phrase match in title → strong signal
    for phrase in quoted_phrases:
        if phrase.lower() in lower_title:
            score += 0.4
            reasons.append("quoted_title")
            break

    # Domain / host exact match — IOC-style domain in query matches URL host
    if _IOC_DOMAIN_RE.search(url):
        domain_in_url = _IOC_DOMAIN_RE.search(url).group(0) if _IOC_DOMAIN_RE.search(url) else ""
        if domain_in_url and domain_in_url.lower() in lower_url:
            score += 0.35
            reasons.append("domain_hit")

    # IP address in query matches URL
    if _IOC_IP_RE.search(query):
        ip = _IOC_IP_RE.search(query).group(0)
        if ip in url:
            score += 0.35
            reasons.append("ip_hit")

    # Title has substantial overlap with query tokens (excluding quoted part)
    if query_tokens:
        title_words = {
            w.strip(".,;:!?()[]{}") for w in lower_title.split() if len(w) > 2
        }
        overlap = query_tokens & title_words
        if overlap:
            score += min(0.3, len(overlap) * 0.07)
            reasons.append("title_overlap")

    # Snippet mentions query tokens (weaker signal)
    if query_tokens:
        snippet_words = {
            w.strip(".,;:!?()[]{}") for w in lower_snippet.split() if len(w) > 2
        }
        snippet_overlap = query_tokens & snippet_words
        if snippet_overlap:
            score += min(0.15, len(snippet_overlap) * 0.04)
            reasons.append("snippet_overlap")

    # Path depth signal: short paths tend to be more authoritative
    try:
        parsed = urlparse.urlparse(url)
        path_depth = len([s for s in parsed.path.split("/") if s])
        if path_depth <= 2:
            score += 0.05
        elif path_depth >= 5:
            score -= 0.05
    except Exception:
        pass

    # Clamp
    score = max(0.0, min(1.0, score))
    return {
        "score": score,
        "reasons": reasons,
    }


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
    """Extract lower-case host from a normalised URL (already urlparse'd)."""
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
    proxy: str | None,
) -> list[dict]:
    """
    Compatibility async wrapper around synchronous DDGS.text().

    Uses asyncio.to_thread() because duckduckgo_search v8.1.1 does NOT
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
        # Lazy import: ddgs v9+ (primary) or duckduckgo_search v8.x (fallback)
        try:
            from ddgs import DDGS  # noqa: F401
        except ImportError:
            from duckduckgo_search import DDGS  # noqa: T1009

        backend: DDGS = DDGS(timeout=int(timeout_s))
        try:
            results = list(backend.text(query, max_results=max_results))
            return results
        finally:
            try:
                backend.client.close()
            except Exception:  # pragma: no cover — best-effort
                pass

    hits: list[dict] = await asyncio.to_thread(_sync_search)
    return hits


# ---------------------------------------------------------------------------
# Per-run query cache (F207I-A): deduplicate identical DDG queries within one run.
# Lightweight: keyed by normalized query string, bounded to MAX_CACHE entries.
# Does NOT survive across runs — no persistent cache required.
# ---------------------------------------------------------------------------
from collections import OrderedDict

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


def _build_query_variants(query: str) -> list[str]:
    """
    Sprint F213B + F232: Generate bounded query variants for domain-aware queries.

    - Pure domain query ("example.com") → 4 site/subscription/infrastructure/subdomain variants
    - Mixed query ("mozilla.org certificate transparency") → extract domain token + CT-aware variants

    Returns [query] (single variant, no expansion) when no domain token found.

    Phase A (DSPy): if HLEDAC_ENABLE_DSPY=1, call brain.dspy_service.expand_query
    first for semantic query expansion before structural variants are built.
    """
    # Phase A: DSPy query expansion (semantic variants before structural ones)
    dspy_variants: list = []
    if os.getenv("HLEDAC_ENABLE_DSPY", "0") == "1":
        try:
            import asyncio

            from hledac.universal.brain.dspy_service import expand_query
            dspy_variants = asyncio.run(expand_query(query)) or []
            if dspy_variants:
                logger.debug("dspy_service: expand_query added %d semantic variants", len(dspy_variants))
        except Exception:
            dspy_variants = []

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


async def async_search_public_web(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    proxy: str | None = None,
) -> DiscoveryBatchResult:
    """
    Public web discovery via DuckDuckGo.

    F207I-A per-run cache: same normalized query returns cached result without
    calling the backend again within one pipeline run.
    F213B: domain-like queries trigger bounded variant expansion (max 4).
    """
    global _last_error

    # ---- input validation (must come before cache check for variant detection) ----
    if query is None:
        _last_error = "empty_query"
        return DiscoveryBatchResult(hits=(), error="empty_query")
    trimmed = query.strip() if isinstance(query, str) else str(query).strip()
    if not trimmed:
        _last_error = "empty_query"
        return DiscoveryBatchResult(hits=(), error="empty_query")

    # ---- bounds + type guard ----------------------------------------------
    try:
        max_results = max(1, min(int(max_results), HARD_MAX_RESULTS))
    except (TypeError, ValueError):
        max_results = DEFAULT_MAX_RESULTS

    start = time.monotonic()

    # ---- Sprint F253B: public discovery replay seam (read-first) ---------
    if replay_enabled():
        cassette = read_cassette("public_duckduckgo", trimmed)
        if cassette is not None:
            hits_list_cassette = [
                DiscoveryHit(
                    query=trimmed,
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
                hits=tuple(hits_list_cassette),
                error=cassette.get("error"),
                fallback_triggered=cassette.get("fallback_triggered"),
                cache_hit=False,
                provider_name=cassette.get("provider_name", "duckduckgo"),
                provider_chain=tuple(cassette.get("provider_chain", ["duckduckgo"])),
                source_family=cassette.get("source_family", "search"),
                elapsed_s=cassette.get("elapsed_s", 0.0),
                error_type=cassette.get("error_type"),
                provider_status_debug=cassette.get("provider_status_debug"),
            )
        elif replay_strict_enabled():
            # Cassette miss in strict mode: fail-soft, no live call
            elapsed = time.monotonic() - start
            return DiscoveryBatchResult(
                hits=(),
                error="replay_miss",
                error_type="replay_miss",
                provider_name="duckduckgo",
                provider_chain=("duckduckgo",),
                source_family="search",
                elapsed_s=elapsed,
                provider_status_debug=[
                    {
                        "provider": "public_duckduckgo",
                        "selected": False,
                        "reason": "replay_miss",
                    }
                ],
            )

    # ---- Sprint F213B: query variant expansion for domain-like queries ----
    variants = _build_query_variants(trimmed)
    if len(variants) > 1:
        # Multiple variants: run each with proportional budget, merge results
        per_variant_results = max(1, max_results // len(variants))
        all_hits: list[DiscoveryHit] = []
        variant_errors: list[str] = []

        async def search_variant(var_query: str) -> tuple[list[DiscoveryHit], str | None]:
            """Search a single variant, return (hits, error)."""
            # Check cache for this variant
            var_cached = _get_cached_discovery(var_query)
            if var_cached is not None:
                return (list(var_cached.hits), None)
            try:
                async with asyncio.timeout(timeout_s):
                    raw = await _ddgs_text_search(var_query, per_variant_results, timeout_s, proxy)
            except asyncio.CancelledError:
                return ([], "cancelled")
            except TimeoutError:
                return ([], "timeout")
            except Exception as e:
                return ([], f"variant_error:{type(e).__name__}")

            # Process raw hits (same logic as main path)
            seen_v: dict[str, int] = {}
            host_v: dict[str, int] = {}
            hits_v: list[DiscoveryHit] = []
            max_from_host = max(1, int(per_variant_results * MAX_HOST_SHARE_RATIO))
            for raw_item in raw:
                raw_url = raw_item.get("href") or raw_item.get("url") or ""
                title = (raw_item.get("title") or "").strip()
                snippet = (raw_item.get("body") or raw_item.get("snippet") or "").strip()
                if _is_noise_result(title, raw_url, snippet, var_query):
                    continue
                norm = _normalize_url_for_dedup(raw_url)
                if not norm or norm in seen_v:
                    continue
                host = _extract_host(norm)
                if host and host_v.get(host, 0) >= max_from_host:
                    continue
                seen_v[norm] = len(hits_v)
                host_v[host] = host_v.get(host, 0) + 1
                signals = _build_signals(var_query, title, raw_url, snippet)
                reason = signals["reasons"][0] if signals["reasons"] else None
                hits_v.append(DiscoveryHit(
                    query=var_query,
                    title=title,
                    url=raw_url,
                    snippet=snippet,
                    source=SOURCE_NAME,
                    rank=0,
                    retrieved_ts=time.time(),
                    score=signals["score"],
                    reason=reason,
                ))
            hits_v.sort(key=lambda h: (-h.score, h.rank))
            # Cache this variant's result
            _set_cached_discovery(var_query, DiscoveryBatchResult(hits=tuple(hits_v), error=None))
            return (hits_v, None)

        # Run all variants concurrently
        results = await asyncio.gather(*[search_variant(v) for v in variants], return_exceptions=True)
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

        # Determine error: if all variants failed, return error; otherwise None
        final_error = "|".join(variant_errors) if len(variant_errors) == len(variants) else None
        result = DiscoveryBatchResult(
            hits=final_hits,
            error=final_error,
            provider_status_debug=[
                {"provider": "ddg_mojeek", "state": "production", "selected": True, "reason": "multi_variant_search"},
            ],
        )
        _set_cached_discovery(trimmed, result)
        return result

    # ---- Sprint F253B: Replay — read from cassette if available (before live call) ----
    if replay_enabled():
        cached = read_cassette(_PUBLIC_REPLAY_ADAPTER, trimmed)
        if cached is not None:
            cached_hits = cached.get("hits", ())
            elapsed = time.monotonic() - start
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
        elif replay_strict_enabled():
            # Cassette miss in strict mode: fail-soft, no live call
            elapsed = time.monotonic() - start
            return DiscoveryBatchResult(
                hits=(),
                error="replay_miss",
                error_type="replay_miss",
                provider_name="duckduckgo",
                provider_chain=("duckduckgo",),
                source_family="search",
                elapsed_s=elapsed,
                provider_status_debug=[
                    {
                        "provider": "public_duckduckgo",
                        "selected": False,
                        "reason": "replay_miss",
                    }
                ],
            )
        # Non-strict miss: fall through to live call

    # ---- per-run query cache check (F207I-A) ---------------------------------
    cached = _get_cached_discovery(trimmed)
    if cached is not None:
        cached_cache_hit = DiscoveryBatchResult(
            hits=cached.hits,
            error=cached.error,
            fallback_triggered=cached.fallback_triggered,
            provider_name=cached.provider_name,
            provider_chain=cached.provider_chain,
            source_family=cached.source_family,
            elapsed_s=cached.elapsed_s,
            error_type=cached.error_type,
            cache_hit=True,
            # F234-FIX: cache hit preserves provider selection context
            provider_status_debug=getattr(cached, 'provider_status_debug', None),
        )
        return cached_cache_hit

    # ---- timeout wrapper ---------------------------------------------------
    try:
        async with asyncio.timeout(timeout_s):
            raw_hits: list[dict] = await _ddgs_text_search(
                trimmed, max_results, timeout_s, proxy
            )
    except asyncio.CancelledError:
        _last_error = "cancelled"
        raise  # always re-raise — do NOT swallow
    except TimeoutError:
        # asyncio.timeout raises TimeoutError from stdlib in __aexit__
        _last_error = "timeout"
        return DiscoveryBatchResult(
            hits=(),
            error="timeout",
            provider_status_debug=[
                {"provider": "ddg_mojeek", "state": "production", "selected": False, "reason": "timeout"},
            ],
        )
    except Exception as e:
        # ---- fail-soft: classify into concrete error taxonomy (F206AB) ----
        err_str = str(e)
        err_name = type(e).__name__
        error_tag: str
        if "ratelimit" in err_str.lower() or "RatelimitException" in err_name:
            error_tag = "rate_limited"
        elif "timeout" in err_str.lower() or "TimeoutException" in err_name or "TimeoutError" in err_name:
            error_tag = "timeout"
        elif "proxy" in err_str.lower() or "ProxyError" in err_name:
            error_tag = "proxy_error"
        elif "network" in err_str.lower() or "ConnectionError" in err_name or "HTTPError" in err_name:
            error_tag = "network_error"
        elif "server" in err_str.lower() or "500" in err_str or "502" in err_str or "503" in err_str or "504" in err_str:
            error_tag = "server_error"
        else:
            error_tag = "unknown_backend_error"

        _last_error = error_tag

        # ---- bounded fallback: backend_error variants / timeout only (NOT rate_limited) --
        _BACKEND_ERROR_TAGS = {"timeout", "proxy_error", "network_error", "server_error", "unknown_backend_error"}
        if error_tag not in _BACKEND_ERROR_TAGS and error_tag != "timeout":
            return DiscoveryBatchResult(
            hits=(),
            error=error_tag,
            # F234-FIX: provider selected before failure occurred
            provider_status_debug=[
                {"provider": "ddg_mojeek", "state": "production", "selected": False, "reason": f"non_backend_error_{error_tag}"},
            ],
        )

        try:
            fallback_hits = await _scrape_mojeek(trimmed, n=max_results)
        except Exception:
            fallback_hits = []
        if fallback_hits:
            # Convert list[dict] to list[DiscoveryHit] using same ranking logic
            seen_urls: dict[str, int] = {}
            host_counts: dict[str, int] = {}
            retrieved_ts = time.time()
            hits_list: list[DiscoveryHit] = []
            max_from_host = max(1, int(max_results * MAX_HOST_SHARE_RATIO))
            for raw in fallback_hits:
                raw_url = raw.get("url") or ""
                title = (raw.get("title") or "").strip()
                snippet = (raw.get("snippet") or "").strip()
                if _is_noise_result(title, raw_url, snippet, trimmed):
                    continue
                norm = _normalize_url_for_dedup(raw_url)
                if not norm or norm in seen_urls:
                    continue
                host = _extract_host(norm)
                if host and host_counts.get(host, 0) >= max_from_host:
                    continue
                seen_urls[norm] = len(hits_list)
                host_counts[host] = host_counts.get(host, 0) + 1
                signals = _build_signals(trimmed, title, raw_url, snippet)
                reason = signals["reasons"][0] if signals["reasons"] else None
                hits_list.append(
                    DiscoveryHit(
                        query=trimmed,
                        title=title,
                        url=raw_url,
                        snippet=snippet,
                        source=raw.get("source", "mojeek_scrape"),
                        rank=0,
                        retrieved_ts=retrieved_ts,
                        score=signals["score"],
                        reason=reason,
                    )
                )
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
        else:
            return DiscoveryBatchResult(
                hits=(),
                error=error_tag,
                fallback_triggered="primary_backend_failed_fallback_failed",
                provider_status_debug=[
                    {"provider": "ddg_mojeek", "state": "production", "selected": False, "reason": "fallback_failed_primary"},
                    {"provider": "mojeek_scrape", "state": "production", "selected": False, "reason": "fallback_failed"},
                ],
            )

    # ---- noise filter + signal-based ranking ---------------------------------
    seen_urls: dict[str, int] = {}
    host_counts: dict[str, int] = {}
    retrieved_ts = time.time()
    hits_list: list[DiscoveryHit] = []
    max_from_host = max(1, int(max_results * MAX_HOST_SHARE_RATIO))

    for raw in raw_hits:
        raw_url = raw.get("href") or raw.get("url") or ""
        title = (raw.get("title") or "").strip()
        snippet = (raw.get("body") or raw.get("snippet") or "").strip()

        # Skip empty / noise results early
        if _is_noise_result(title, raw_url, snippet, trimmed):
            continue

        norm = _normalize_url_for_dedup(raw_url)
        if not norm or norm in seen_urls:
            continue

        host = _extract_host(norm)
        if host and host_counts.get(host, 0) >= max_from_host:
            continue

        seen_urls[norm] = len(hits_list)
        host_counts[host] = host_counts.get(host, 0) + 1

        signals = _build_signals(trimmed, title, raw_url, snippet)
        reason = signals["reasons"][0] if signals["reasons"] else None

        hits_list.append(
            DiscoveryHit(
                query=trimmed,
                title=title,
                url=raw_url,
                snippet=snippet,
                source=SOURCE_NAME,
                rank=0,
                retrieved_ts=retrieved_ts,
                score=signals["score"],
                reason=reason,
            )
        )

    # Sort by signal score descending, then by rank (first-seen) as tiebreak
    hits_list.sort(key=lambda h: (-h.score, h.rank))

    # Re-rank to reflect sorted order
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
        for i, h in enumerate(hits_list[:max_results])
    )

    result = DiscoveryBatchResult(
        hits=final_hits,
        error=None,
        # F234-FIX: provider_status_debug tells _extract_provider_surface which
        # provider was selected so it does NOT fall through to "no_provider_selected"
        # when the search actually ran and returned (even empty) results.
        provider_status_debug=[
            {
                "provider": "ddg_mojeek",
                "state": "production",
                "selected": True,
                "reason": "primary_backend",
            }
        ],
    )
    # Sprint F253B: write cassette after successful live result
    if replay_enabled():
        write_cassette(_PUBLIC_REPLAY_ADAPTER, trimmed, {
            "hits": list(final_hits),
            "error": None,
            "fallback_triggered": None,
            "cache_hit": False,
            "provider_name": "duckduckgo",
            "provider_chain": ["duckduckgo"],
            "source_family": "search",
            "elapsed_s": result.elapsed_s,
            "error_type": None,
            "provider_status_debug": [
                {
                    "provider": "ddg_mojeek",
                    "state": "production",
                    "selected": True,
                    "reason": "primary_backend",
                }
            ],
        })
    _set_cached_discovery(query, result)
    return result


# ── Sprint 8VB: Multi-Engine Search ───────────────────────────────────────────

logger = logging.getLogger(__name__)


async def _scrape_mojeek(
    query: str, n: int = 10
) -> list[dict]:
    """Mojeek independent crawler, no CAPTCHA policy."""
    from bs4 import BeautifulSoup
    _UA = (
        "Mozilla/5.0 (Macintosh; ARM Mac OS X 14_0) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Safari/605.1.15"
    )
    results = []
    try:
        async with aiohttp.ClientSession() as s:
            resp, err = await checked_aiohttp_get(
                s,
                "https://www.mojeek.com/search",
                params={"q": query},
                headers={"User-Agent": _UA,
                         "Accept-Language": "en-US,en;q=0.9"},
                timeout=aiohttp.ClientTimeout(total=12),
                failure_kind="mojeek",
            )
            if err:
                logger.debug(f"[Mojeek] {err}")
                return []
            if resp.status != 200:
                return []
            soup = BeautifulSoup(await resp.text(), "html.parser")
            for li in soup.select("ul.results-standard li")[:n]:
                a = li.select_one("a.ob")
                p = li.select_one("p.s")
                if a and a.get("href"):
                    results.append({
                        "title":   a.get_text(strip=True),
                        "url":     a["href"],
                        "snippet": p.get_text(strip=True) if p else "",
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
    from hledac.universal.intelligence.archive_discovery import wayback_cdx_lookup

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
        async with aiohttp.ClientSession() as s:
            resp, err = await checked_aiohttp_get(
                s,
                "https://index.commoncrawl.org/CC-MAIN-2024-51-index",
                params={
                    "url":    url_pattern,
                    "output": "json",
                    "limit":  max_results,
                    "fl":     "url,timestamp,filename,offset,length"
                },
                timeout=aiohttp.ClientTimeout(total=25),
                failure_kind="commoncrawl_cdx",
            )
            if err:
                logger.warning(f"[CommonCrawl CDX] {err}")
                return []
            if resp.status != 200:
                return []
            for line in (await resp.text()).strip().split("\n")[:max_results]:
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
        async with aiohttp.ClientSession() as s:
            resp, err = await checked_aiohttp_get(
                s,
                f"https://internetdb.shodan.io/{ip}",
                timeout=aiohttp.ClientTimeout(total=8),
                failure_kind="shodan_internetdb",
            )
            if err:
                logger.debug(f"[ShodanInternetDB] {err}")
                return {}
            data = await resp.json()
            return {
                "ip":        ip,
                "ports":     data.get("ports", []),
                "cves":      data.get("cves", []),
                "hostnames": data.get("hostnames", []),
                "tags":      data.get("tags", []),
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
    _DOMAIN_CCX_RE = _re.compile(
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
                from hledac.universal.network.session_runtime import async_get_aiohttp_session
                s = await async_get_aiohttp_session()
                async with s.get(url) as r:
                    return await r.text()

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
    F192E: Parallel search: DDG + Mojeek + CommonCrawl(domain query only).
    Bing excluded — actively blocks + CAPTCHA.

    CommonCrawl CDX API is domain-specific (domain index, not general search).
    """
    ddg_task    = async_search_public_web(query, max_results=max_results // 2)
    mojeek_task = _scrape_mojeek(query, max_results // 2)
    cc_task     = _search_commoncrawl_domain(query, max_results=max_results // 4)

    all_results: list[dict] = []
    for batch in await asyncio.gather(
        ddg_task, mojeek_task, cc_task,
        return_exceptions=True
    ):
        if isinstance(batch, DiscoveryBatchResult) and batch.hits:
            all_results.extend([
                {"title": h.title, "url": h.url, "snippet": h.snippet, "source": h.source}
                for h in batch.hits
            ])
        elif isinstance(batch, list):
            all_results.extend(batch)

    seen: set[str] = set()
    deduped: list[dict] = []
    for r in all_results:
        raw_u = r.get("url", "")
        if not raw_u:
            continue
        norm = _normalize_url_for_dedup(raw_u)
        if norm and norm not in seen:
            seen.add(norm)
            deduped.append(r)
    return deduped[:max_results]
