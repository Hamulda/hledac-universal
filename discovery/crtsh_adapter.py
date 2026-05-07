"""
discovery/crtsh_adapter.py — CT/crt.sh Providerless Pivot Adapter

Sprint F206AV: transport alignment with canonical session_runtime + circuit_breaker.

Replaces local aiohttp.ClientSession + local checked_aiohttp_get with:
- async_get_aiohttp_session() from network.session_runtime
- checked_aiohttp_get() from transport.circuit_breaker

Passive only — no auth/API key, no body fetch beyond crt.sh JSON endpoint.
Fail-soft throughout.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

import aiohttp

from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.transport.circuit_breaker import checked_aiohttp_get

from .duckduckgo_adapter import DiscoveryBatchResult, DiscoveryHit

__all__ = ["async_search_crtsh", "call_crtsh", "CTOutcome", "CTProviderStatus"]

# ---------------------------------------------------------------------------
# Provider status
# ---------------------------------------------------------------------------


class CTProviderStatus(Enum):
    """F217D: Explicit CT provider status tags. F219E adds cooldown states."""

    OK = "ok"
    HTTP_5XX = "http_5xx"
    HTTP_4XX = "http_4xx"
    TIMEOUT = "timeout"
    PARSE_ERROR = "parse_error"
    EMPTY = "empty"
    DISABLED = "disabled"
    CACHE_HIT_STALE = "cache_hit_stale"
    # F219E: cooldown states
    COOLDOWN_ACTIVE = "cooldown_active"
    PROVIDER_FAILURE = "provider_failure"


@dataclass(frozen=True)
class CTProviderStatusReport:
    """
    F217D: Explicit CT provider status report with bounded error sampling.
    F219E adds cooldown fields.

    Fields:
        provider_name:    Always "crtsh".
        attempted:       True if HTTP call was attempted (also True on cache hit).
        status:          CTProviderStatus tag.
        raw_count:       Certs from live call or cached response (0 if no call and no cache).
        error_sample:    Bounded error message (max 200 chars, None on success).
        ct_cache_used:   True if response came from stale cache.
        ct_cache_stale:  True if cached response was stale when served.
        ct_cache_age_s: Seconds since cache file was written (0 if not cached).
        # F219E: cooldown fields
        cooldown_active:              True if provider is in cooldown for this key.
        cooldown_reason:              Reason cooldown was entered (None if not in cooldown).
        cooldown_remaining_s:         Seconds remaining in cooldown (0 if not in cooldown).
        cooldown_started_at_monotonic: Monotonic timestamp when cooldown started (0 if not in cooldown).
        stale_cache_preferred:        True if stale cache was preferred due to cooldown.
        provider_attempt_suppressed:  True if provider call was suppressed due to cooldown.
    """

    provider_name: str = "crtsh"
    attempted: bool = False
    status: CTProviderStatus = CTProviderStatus.DISABLED
    raw_count: int = 0
    error_sample: Optional[str] = None
    ct_cache_used: bool = False
    ct_cache_stale: bool = False
    ct_cache_age_s: float = 0.0
    # F219E cooldown fields
    cooldown_active: bool = False
    cooldown_reason: Optional[str] = None
    cooldown_remaining_s: float = 0.0
    cooldown_started_at_monotonic: float = 0.0
    stale_cache_preferred: bool = False
    provider_attempt_suppressed: bool = False


# ---------------------------------------------------------------------------
# CTOutcome (legacy — extended with cache fields in call_crtsh)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CTOutcome:
    """
    Normalized CT adapter outcome — F207F, extended F217D with cache fields.

    Fields:
        attempted:    True if HTTP call was attempted (also True on cache hit).
        query:        Domain/query that was submitted.
        raw_count:    Certs received from crt.sh before filtering (0 if not attempted).
        built_count:  DiscoveryHit records built after filtering (0 if not attempted).
        accepted_count: Always None for CT — lane owns acceptance decision.
        error:        Error tag string or None on success.
        timeout:      True if call timed out.
        duration_s:   Wall-clock seconds for the call.
        skip_reason:  Reason for skip or None if attempted/errored.
        # F217D: cache fields
        ct_cache_used:  True if response was served from stale cache.
        ct_cache_stale: True if cached response was already stale when served.
        ct_cache_age_s: Seconds since cache was written (0 if not cached).
        provider_status: CTProviderStatus enum tag for explicit provider state.
    """
    attempted: bool = False
    query: str = ""
    raw_count: int = 0
    built_count: int = 0
    accepted_count: None = None
    error: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    skip_reason: str | None = None
    # F217D cache fields
    ct_cache_used: bool = False
    ct_cache_stale: bool = False
    ct_cache_age_s: float = 0.0
    provider_status: CTProviderStatus = CTProviderStatus.DISABLED

logger = logging.getLogger(__name__)

# Hard cap — crt.sh can return thousands of certs for a popular domain
_MAX_CERTS = 50
_MAX_HITS = 20  # hard cap on DiscoveryHit results returned

# crt.sh endpoint — JSON output
_CRTSH_URL = "https://crt.sh/"

# Timeout for the HTTP call
_HTTP_TIMEOUT_S = 8.0

# F217D: Stale cache window — up to 7 days old for diagnostic reuse
_STALE_THRESHOLD_S = 604800  # 7 days

# F219E: Provider cooldown — 300s default after 5xx/timeout
_COOLDOWN_DEFAULT_S = 300.0
_MAX_COOLDOWN_KEYS = 256

# Reserved/special names that are never valid public hosts.
_PRIVATE_HOSTNAMES = {
    "localhost",
    "invalid",
    "test",
}

# Wildcard-only domain pattern (crt.sh often returns certs like "*.example.com")
_WILDCARD_ONLY_RE = re.compile(r"^\*\.")


def _is_private_domain(domain: str) -> bool:
    """Return True if domain is private, internal, or reserved."""
    domain_lower = domain.lower()
    if domain_lower in _PRIVATE_HOSTNAMES:
        return True
    if _is_ip_like(domain_lower):
        return True
    return False


def _is_ip_like(value: str) -> bool:
    """Return True if value looks like an IP address (v4 or v6)."""
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", value):
        return True
    if ":" in value:
        return True
    return False


def _extract_domain_from_query(query: str) -> str | None:
    """
    Extract the best domain candidate from a query string.

    If the query looks like a domain already (has dots), return it.
    Otherwise scan tokens for the first domain-like token (has at least one dot).

    Returns None if no domain-like token found.
    """
    query = query.strip()
    if not query:
        return None

    if _looks_like_domain(query):
        return query

    for token in query.split():
        token = token.strip().lower()
        if "." in token and _looks_like_domain(token):
            parts = token.split(".")
            if len(parts) >= 2 and len(parts[0]) <= 63:
                return token

    return None


def _looks_like_domain(value: str) -> bool:
    """Return True if value looks like a domain name (not an IP, has TLD)."""
    if _is_ip_like(value):
        return False
    if not value or len(value) > 253:
        return False
    if "." not in value:
        return False
    parts = value.split(".")
    if len(parts) < 2:
        return False
    tld = parts[-1]
    if len(tld) < 1 or len(tld) > 63:
        return False
    if not re.match(r"^[a-z0-9.\-_]+$", tld):
        return False
    return True


def _is_wildcard_only(domain: str) -> bool:
    """Return True if domain is a wildcard cert (e.g. '*.example.com')."""
    return bool(_WILDCARD_ONLY_RE.match(domain))


# ---------------------------------------------------------------------------
# Cache helpers (F217D)
# ---------------------------------------------------------------------------


def _make_cache_key(domain: str) -> str:
    """Make a cache key for a domain using xxhash."""
    try:
        import xxhash

        return f"{xxhash.xxh64(domain.encode()).hexdigest()}.json"
    except ImportError:
        import hashlib

        return f"{hashlib.sha256(domain.encode()).hexdigest()[:16]}.json"


def _read_stale_cache(
    domain: str, cache_dir: Path | None, max_age_s: float
) -> tuple[Optional[list], float]:
    """
    F217D: Read a stale cache entry for diagnostic reuse.

    Returns (raw_data, age_s) if a cache file exists and is within max_age_s.
    Returns (None, 0.0) if no cache or cache is older than max_age_s.

    This does NOT count as fresh accepted evidence — callers must set
    ct_cache_used=True, ct_cache_stale=True on the returned outcome.
    """
    if cache_dir is None:
        return None, 0.0
    cache_key = _make_cache_key(domain)
    cache_path = cache_dir / cache_key
    if not cache_path.exists():
        return None, 0.0
    age_s = time.time() - cache_path.stat().st_mtime
    if age_s > max_age_s:
        return None, 0.0
    try:
        import orjson

        raw_data = orjson.loads(cache_path.read_bytes())
        return raw_data, age_s
    except Exception:
        return None, 0.0


def _build_hits_from_raw(
    raw_data: list, domain_candidate: str, query: str, max_results: int
) -> tuple[list[DiscoveryHit], int]:
    """
    F217D: Build DiscoveryHit list from raw crt.sh JSON data (live or cached).

    Used by stale-cache fallback path. Returns (hits, raw_count) where raw_count
    is the total certs before filtering (diagnostic signal, not accepted evidence).
    """
    seen_domains: set[str] = set()
    hits: list[DiscoveryHit] = []
    now = time.time()
    raw_count = len(raw_data) if isinstance(raw_data, list) else 0

    for cert in (raw_data if isinstance(raw_data, list) else [])[:_MAX_CERTS]:
        if not isinstance(cert, dict):
            continue
        name_value = cert.get("name_value", "")
        if not name_value:
            continue

        for subdomain in name_value.split("\n"):
            subdomain = subdomain.strip()
            if not subdomain:
                continue
            if _is_wildcard_only(subdomain):
                continue
            if _is_private_domain(subdomain):
                continue
            subdomain_lower = subdomain.lower()
            if subdomain_lower in seen_domains:
                continue
            if len(hits) >= max_results:
                break

            seen_domains.add(subdomain_lower)
            hits.append(
                DiscoveryHit(
                    query=query,
                    title=f"CT: {subdomain}",
                    url=f"https://{subdomain}/",
                    snippet=f"Certificate Transparency match via crt.sh — {subdomain}",
                    source="crtsh",
                    rank=len(hits),
                    retrieved_ts=now,
                    score=1.0 - (len(hits) / max_results),
                    reason="ct_subdomain",
                    ct_name_value=name_value,
                    ct_common_name=cert.get("common_name"),
                    ct_issuer_name=cert.get("issuer_name"),
                    ct_not_before=cert.get("not_before"),
                    ct_not_after=cert.get("not_after"),
                    ct_entry_timestamp=cert.get("entry_timestamp"),
                    ct_serial_number=cert.get("serial_number"),
                )
            )
        if len(hits) >= max_results:
            break

    return hits, raw_count


# F219E: Provider cooldown map — keyed by normalized domain, FIFO eviction at cap
# {domain_lower: (cooldown_started_at_monotonic, reason)}
_ct_provider_cooldown: dict[str, tuple[float, str]] = {}


def _enter_cooldown(domain: str, reason: str, now: float) -> None:
    """
    F219E: Enter cooldown for a domain after provider failure.

    Bounds: max _MAX_COOLDOWN_KEYS entries, FIFO eviction.
    """
    domain_key = domain.lower()
    # Evict oldest if at cap (simple FIFO: remove first inserted key)
    if len(_ct_provider_cooldown) >= _MAX_COOLDOWN_KEYS and domain_key not in _ct_provider_cooldown:
        oldest_key = next(iter(_ct_provider_cooldown))
        _ct_provider_cooldown.pop(oldest_key, None)
    _ct_provider_cooldown[domain_key] = (now, reason)


def _check_cooldown(domain: str, now: float) -> tuple[bool, float, str]:
    """
    F219E: Check if domain is in active cooldown.

    Returns (is_cooldown_active, remaining_s, reason).
    """
    domain_key = domain.lower()
    entry = _ct_provider_cooldown.get(domain_key)
    if entry is None:
        return False, 0.0, ""
    started_at, reason = entry
    remaining = _COOLDOWN_DEFAULT_S - (now - started_at)
    if remaining <= 0:
        _ct_provider_cooldown.pop(domain_key, None)
        return False, 0.0, ""
    return True, remaining, reason


def _clear_cooldown(domain: str) -> None:
    """F219E: Clear cooldown for a domain on provider success."""
    _ct_provider_cooldown.pop(domain.lower(), None)


# ---------------------------------------------------------------------------
# call_crtsh
# ---------------------------------------------------------------------------


async def call_crtsh(
    query: str,
    max_results: int = 20,
    timeout_s: float = 8.0,
    cache_dir: Path | None = None,
) -> tuple[DiscoveryBatchResult, CTOutcome]:
    """
    crt.sh search with normalized outcome — F207F, extended F217D.

    F217D adds stale-cache diagnostic reuse:
      - If live provider fails with HTTP 5xx/timeout and a stale cache exists
        (within _STALE_THRESHOLD_S), the cached response is returned with
        ct_cache_used=True and ct_cache_stale=True.
      - Cached raw is NOT counted as fresh accepted evidence.
      - Provider status is explicitly tagged via CTProviderStatus.

    Args:
        query:       Search query string (domain or free-text).
        max_results: Max hits to return (default 20, hard cap 50).
        timeout_s:   HTTP timeout in seconds (default 8.0).
        cache_dir:   Optional cache directory for stale-cache reuse (F217D).
                    When provided and live call fails, a stale cache (up to 7 days)
                    is used for diagnostic purposes.

    Returns:
        (DiscoveryBatchResult, CTOutcome) tuple.
        outcome.attempted=True on every code path including cache hits.
        outcome.raw_count = certs from live call or cached response (0 if neither).
        outcome.ct_cache_used=True when response is from stale cache.
        outcome.provider_status = CTProviderStatus tag.
    """
    start = time.monotonic()
    elapsed = start - start  # 0.0 placeholder

    # Bounds
    try:
        max_results = max(1, min(int(max_results), _MAX_HITS))
    except (TypeError, ValueError):
        max_results = 20

    query_stripped = query.strip() if query else ""
    if not query_stripped:
        elapsed = time.monotonic() - start
        outcome = CTOutcome(
            attempted=True,
            query=query_stripped,
            raw_count=0,
            built_count=0,
            error="empty_query",
            skip_reason="empty_query",
            duration_s=elapsed,
        )
        result = DiscoveryBatchResult(
            hits=(),
            error="empty_query",
            error_type="invalid_query",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )
        return result, outcome

    # Extract domain candidate from query
    domain_candidate = _extract_domain_from_query(query_stripped)
    if domain_candidate is None:
        elapsed = time.monotonic() - start
        outcome = CTOutcome(
            attempted=True,
            query=query_stripped,
            raw_count=0,
            built_count=0,
            error="no_domain_like_token",
            skip_reason="no_domain_like_token",
            duration_s=elapsed,
        )
        result = DiscoveryBatchResult(
            hits=(),
            error="no_domain_like_token",
            error_type="invalid_query",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )
        return result, outcome

    # F219E: Check cooldown before making any provider call
    cooldown_now = time.monotonic()
    in_cooldown, cooldown_remaining, cooldown_reason = _check_cooldown(domain_candidate, cooldown_now)
    if in_cooldown:
        # F219E: Cooldown active — check for stale cache before returning failure
        stale_data, stale_age = _read_stale_cache(
            domain_candidate, cache_dir, _STALE_THRESHOLD_S
        )
        elapsed = time.monotonic() - start
        if stale_data is not None:
            # Serve stale cache — diagnostic use, not accepted evidence
            stale_hits, stale_raw_count = _build_hits_from_raw(
                stale_data, domain_candidate, query_stripped, max_results
            )
            cache_outcome = CTOutcome(
                attempted=True,
                query=domain_candidate,
                raw_count=stale_raw_count,
                built_count=len(stale_hits),
                error="cooldown_stale_cache",
                timeout=False,
                duration_s=elapsed,
                ct_cache_used=True,
                ct_cache_stale=True,
                ct_cache_age_s=stale_age,
                provider_status=CTProviderStatus.CACHE_HIT_STALE,
            )
            cache_result = DiscoveryBatchResult(
                hits=tuple(stale_hits),
                error="cooldown_stale_cache",
                error_type="cooldown_cache_fallback",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )
            return cache_result, cache_outcome
        # No stale cache — explicit cooldown failure (not empty CT)
        outcome = CTOutcome(
            attempted=True,
            query=domain_candidate,
            raw_count=0,
            built_count=0,
            error="cooldown_active",
            duration_s=elapsed,
            provider_status=CTProviderStatus.COOLDOWN_ACTIVE,
        )
        result = DiscoveryBatchResult(
            hits=(),
            error="cooldown_active",
            error_type="cooldown_active",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )
        return result, outcome

    # Session via canonical shared session_runtime
    session: aiohttp.ClientSession | None = None
    raw_count = 0
    built_count = 0
    try:
        session = await async_get_aiohttp_session()
        timeout = aiohttp.ClientTimeout(total=min(timeout_s, _HTTP_TIMEOUT_S))

        params = {
            "q": domain_candidate,
            "output": "json",
        }

        try:
            async with asyncio.timeout(timeout_s):
                resp, err = await checked_aiohttp_get(
                    session,
                    _CRTSH_URL,
                    params=params,
                    headers={"User-Agent": "Hledac/1.0 (research bot)"},
                    timeout=timeout,
                    failure_kind="crtsh",
                )
        except asyncio.CancelledError:
            raise  # always re-raise

        elapsed = time.monotonic() - start

        if err:
            err_tag: str
            is_timeout = err == "timeout"
            if err.startswith("circuit_breaker_open:"):
                err_tag = "circuit_breaker_open"
            elif is_timeout:
                err_tag = "timeout"
            elif err == "client_error":
                err_tag = "network_error"
            else:
                err_tag = "network_error"

            # F219E: Enter cooldown on provider failure before stale-cache fallback
            _enter_cooldown(domain_candidate, err, cooldown_now)
            # F217D: Try stale cache on timeout/network errors before failing
            _stale_data, _stale_age = _read_stale_cache(
                domain_candidate, cache_dir, _STALE_THRESHOLD_S
            )
            if _stale_data is not None and is_timeout:
                _stale_hits, _stale_raw = _build_hits_from_raw(
                    _stale_data, domain_candidate, query_stripped, max_results
                )
                _cache_outcome = CTOutcome(
                    attempted=True,
                    query=domain_candidate,
                    raw_count=_stale_raw,
                    built_count=len(_stale_hits),
                    error=f"{err}_stale_cache",
                    timeout=True,
                    duration_s=elapsed,
                    ct_cache_used=True,
                    ct_cache_stale=True,
                    ct_cache_age_s=_stale_age,
                    provider_status=CTProviderStatus.CACHE_HIT_STALE,
                )
                _cache_result = DiscoveryBatchResult(
                    hits=tuple(_stale_hits),
                    error=f"{err}_stale_cache",
                    error_type="timeout_cache_fallback",
                    provider_name="crtsh",
                    provider_chain=("crtsh",),
                    source_family="ct",
                    elapsed_s=elapsed,
                )
                return _cache_result, _cache_outcome

            outcome = CTOutcome(
                attempted=True,
                query=domain_candidate,
                raw_count=0,
                built_count=0,
                error=err,
                timeout=is_timeout,
                duration_s=elapsed,
                provider_status=CTProviderStatus.TIMEOUT if is_timeout else CTProviderStatus.HTTP_5XX,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error=err,
                error_type=err_tag,
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )
            return result, outcome

        assert resp is not None
        if resp.status == 429:
            elapsed = time.monotonic() - start
            outcome = CTOutcome(
                attempted=True,
                query=domain_candidate,
                raw_count=0,
                built_count=0,
                error="rate_limited",
                duration_s=elapsed,
                provider_status=CTProviderStatus.HTTP_5XX,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error="rate_limited",
                error_type="http_429",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )
            return result, outcome
        if resp.status == 403:
            elapsed = time.monotonic() - start
            outcome = CTOutcome(
                attempted=True,
                query=domain_candidate,
                raw_count=0,
                built_count=0,
                error="captcha_or_blocked",
                duration_s=elapsed,
                provider_status=CTProviderStatus.HTTP_4XX,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error="captcha_or_blocked",
                error_type="http_403",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )
            return result, outcome
        if resp.status >= 500:
            elapsed = time.monotonic() - start
            # F219E: Enter cooldown on 5xx before stale-cache fallback
            _enter_cooldown(domain_candidate, f"http_{resp.status}", cooldown_now)
            # F217D: Try stale cache on 5xx before returning failure
            stale_data, stale_age = _read_stale_cache(
                domain_candidate, cache_dir, _STALE_THRESHOLD_S
            )
            if stale_data is not None:
                # Serve stale cache as diagnostic — NOT fresh accepted evidence
                stale_hits, stale_raw_count = _build_hits_from_raw(
                    stale_data, domain_candidate, query_stripped, max_results
                )
                elapsed = time.monotonic() - start
                cache_outcome = CTOutcome(
                    attempted=True,
                    query=domain_candidate,
                    raw_count=stale_raw_count,
                    built_count=len(stale_hits),
                    error=f"http_{resp.status}_stale_cache",
                    timeout=False,
                    duration_s=elapsed,
                    ct_cache_used=True,
                    ct_cache_stale=True,
                    ct_cache_age_s=stale_age,
                    provider_status=CTProviderStatus.CACHE_HIT_STALE,
                )
                cache_result = DiscoveryBatchResult(
                    hits=tuple(stale_hits),
                    error=f"http_{resp.status}_stale_cache",
                    error_type="http_5xx_cache_fallback",
                    provider_name="crtsh",
                    provider_chain=("crtsh",),
                    source_family="ct",
                    elapsed_s=elapsed,
                )
                return cache_result, cache_outcome
            # No cache — explicit provider failure
            outcome = CTOutcome(
                attempted=True,
                query=domain_candidate,
                raw_count=0,
                built_count=0,
                error=f"http_{resp.status}",
                duration_s=elapsed,
                provider_status=CTProviderStatus.HTTP_5XX,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error=f"http_{resp.status}",
                error_type="http_5xx",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )
            return result, outcome
        if resp.status >= 400:
            elapsed = time.monotonic() - start
            outcome = CTOutcome(
                attempted=True,
                query=domain_candidate,
                raw_count=0,
                built_count=0,
                error=f"http_{resp.status}",
                duration_s=elapsed,
                provider_status=CTProviderStatus.HTTP_4XX,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error=f"http_{resp.status}",
                error_type="http_4xx",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )
            return result, outcome

        try:
            data = await resp.json(content_type=None)
        except Exception as e:
            elapsed = time.monotonic() - start
            outcome = CTOutcome(
                attempted=True,
                query=domain_candidate,
                raw_count=0,
                built_count=0,
                error=f"parse_error:{e}",
                duration_s=elapsed,
                provider_status=CTProviderStatus.PARSE_ERROR,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error=f"parse_error:{e}",
                error_type="parse_error",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )
            return result, outcome

        if not isinstance(data, list):
            elapsed = time.monotonic() - start
            outcome = CTOutcome(
                attempted=True,
                query=domain_candidate,
                raw_count=0,
                built_count=0,
                error="unexpected_response_format",
                duration_s=elapsed,
                provider_status=CTProviderStatus.PARSE_ERROR,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error="unexpected_response_format",
                error_type="parse_error",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )
            return result, outcome

        # Count raw certs before filtering
        raw_count = len(data) if isinstance(data, list) else 0

        # Extract subdomains from certs
        seen_domains: set[str] = set()
        hits: list[DiscoveryHit] = []
        now = time.time()

        for cert in data[:_MAX_CERTS]:
            if not isinstance(cert, dict):
                continue
            name_value = cert.get("name_value", "")
            if not name_value:
                continue

            for subdomain in name_value.split("\n"):
                subdomain = subdomain.strip()
                if not subdomain:
                    continue

                if _is_wildcard_only(subdomain):
                    continue

                if _is_private_domain(subdomain):
                    continue

                subdomain_lower = subdomain.lower()
                if subdomain_lower in seen_domains:
                    continue

                if len(hits) >= max_results:
                    break

                seen_domains.add(subdomain_lower)
                hits.append(
                    DiscoveryHit(
                        query=query_stripped,
                        title=f"CT: {subdomain}",
                        url=f"https://{subdomain}/",
                        snippet=f"Certificate Transparency match via crt.sh — {subdomain}",
                        source="crtsh",
                        rank=len(hits),
                        retrieved_ts=now,
                        score=1.0 - (len(hits) / max_results),
                        reason="ct_subdomain",
                        ct_name_value=name_value,
                        ct_common_name=cert.get("common_name"),
                        ct_issuer_name=cert.get("issuer_name"),
                        ct_not_before=cert.get("not_before"),
                        ct_not_after=cert.get("not_after"),
                        ct_entry_timestamp=cert.get("entry_timestamp"),
                        ct_serial_number=cert.get("serial_number"),
                    )
                )

            if len(hits) >= max_results:
                break

        built_count = len(hits)
        elapsed = time.monotonic() - start

        if not hits:
            outcome = CTOutcome(
                attempted=True,
                query=domain_candidate,
                raw_count=raw_count,
                built_count=0,
                error="no_subdomains_found",
                duration_s=elapsed,
                provider_status=CTProviderStatus.EMPTY,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error="no_subdomains_found",
                error_type="provider_empty",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )
            return result, outcome

        # F219E: Clear cooldown on provider success
        _clear_cooldown(domain_candidate)

        outcome = CTOutcome(
            attempted=True,
            query=domain_candidate,
            raw_count=raw_count,
            built_count=built_count,
            error=None,
            duration_s=elapsed,
            provider_status=CTProviderStatus.OK,
        )
        result = DiscoveryBatchResult(
            hits=tuple(hits),
            error=None,
            error_type="none",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )
        return result, outcome

    except asyncio.CancelledError:
        raise  # re-raised

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        _dc_for_cache = domain_candidate if 'domain_candidate' in dir() else query_stripped
        # F219E: Enter cooldown on timeout before stale-cache fallback
        _enter_cooldown(_dc_for_cache, "timeout", start)
        _stale_d, _stale_a = _read_stale_cache(_dc_for_cache, cache_dir, _STALE_THRESHOLD_S)
        if _stale_d is not None:
            _s_hits, _s_raw = _build_hits_from_raw(
                _stale_d, _dc_for_cache, query_stripped, max_results
            )
            _cache_outcome = CTOutcome(
                attempted=True,
                query=_dc_for_cache,
                raw_count=_s_raw,
                built_count=len(_s_hits),
                error="timeout_stale_cache",
                timeout=True,
                duration_s=elapsed,
                ct_cache_used=True,
                ct_cache_stale=True,
                ct_cache_age_s=_stale_a,
                provider_status=CTProviderStatus.CACHE_HIT_STALE,
            )
            _cache_result = DiscoveryBatchResult(
                hits=tuple(_s_hits),
                error="timeout_stale_cache",
                error_type="timeout_cache_fallback",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )
            return _cache_result, _cache_outcome
        outcome = CTOutcome(
            attempted=True,
            query=_dc_for_cache,
            raw_count=raw_count if 'raw_count' in dir() else 0,
            built_count=0,
            error="timeout",
            timeout=True,
            duration_s=elapsed,
            provider_status=CTProviderStatus.TIMEOUT,
        )
        result = DiscoveryBatchResult(
            hits=(),
            error="timeout",
            error_type="timeout",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )
        return result, outcome

    except Exception as e:
        elapsed = time.monotonic() - start
        logger.warning(f"[crtsh] unexpected error: {e}")
        outcome = CTOutcome(
            attempted=True,
            query=domain_candidate if 'domain_candidate' in dir() else query_stripped,
            raw_count=raw_count if 'raw_count' in dir() else 0,
            built_count=0,
            error=str(e),
            duration_s=elapsed,
            provider_status=CTProviderStatus.PARSE_ERROR,
        )
        result = DiscoveryBatchResult(
            hits=(),
            error=str(e),
            error_type="provider_exception",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )
        return result, outcome


async def async_search_crtsh(
    query: str,
    max_results: int = 20,
    timeout_s: float = 8.0,
) -> DiscoveryBatchResult:
    """
    crt.sh Certificate Transparency search — no API key required.

    Args:
        query:       Search query string (domain or free-text).
        max_results: Max hits to return (default 20, hard cap 50).
        timeout_s:   HTTP timeout in seconds (default 8.0).

    Returns:
        DiscoveryBatchResult with CT-sourced subdomain hits.

    Fail-soft:
        - empty_query: no domain-like token found in query
        - timeout: asyncio.TimeoutError
        - http_429: rate limited
        - http_403: blocked
        - http_5xx: server error
        - http_4xx: client error
        - network_error: connection issue
        - parse_error: crt.sh JSON unparseable
        - provider_empty: no subdomains found
        - provider_exception: unexpected exception
        - circuit_breaker_open: domain temporarily blocked
    """
    start = time.monotonic()

    # Bounds
    try:
        max_results = max(1, min(int(max_results), _MAX_HITS))
    except (TypeError, ValueError):
        max_results = 20

    query = query.strip() if query else ""
    if not query:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error="empty_query",
            error_type="invalid_query",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )

    # Extract domain candidate from query
    domain_candidate = _extract_domain_from_query(query)
    if domain_candidate is None:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error="no_domain_like_token",
            error_type="invalid_query",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )

    # Session via canonical shared session_runtime
    session: aiohttp.ClientSession | None = None
    try:
        session = await async_get_aiohttp_session()
        timeout = aiohttp.ClientTimeout(total=min(timeout_s, _HTTP_TIMEOUT_S))

        params = {
            "q": domain_candidate,
            "output": "json",
        }

        try:
            async with asyncio.timeout(timeout_s):
                resp, err = await checked_aiohttp_get(
                    session,
                    _CRTSH_URL,
                    params=params,
                    headers={"User-Agent": "Hledac/1.0 (research bot)"},
                    timeout=timeout,
                    failure_kind="crtsh",
                )
        except asyncio.CancelledError:
            raise  # always re-raise

        elapsed = time.monotonic() - start

        if err:
            err_tag: str
            if err.startswith("circuit_breaker_open:"):
                err_tag = "circuit_breaker_open"
            elif err == "timeout":
                err_tag = "timeout"
            elif err == "client_error":
                err_tag = "network_error"
            else:
                err_tag = "network_error"

            return DiscoveryBatchResult(
                hits=(),
                error=err,
                error_type=err_tag,
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )

        # resp is non-None when err is None (canonical checked_aiohttp_get returns
        # (resp, None) for HTTP 4xx/5xx — caller checks resp.status)
        assert resp is not None
        if resp.status == 429:
            return DiscoveryBatchResult(
                hits=(),
                error="rate_limited",
                error_type="http_429",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=time.monotonic() - start,
            )
        if resp.status == 403:
            return DiscoveryBatchResult(
                hits=(),
                error="captcha_or_blocked",
                error_type="http_403",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=time.monotonic() - start,
            )
        if resp.status >= 500:
            return DiscoveryBatchResult(
                hits=(),
                error=f"http_{resp.status}",
                error_type="http_5xx",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=time.monotonic() - start,
            )
        if resp.status >= 400:
            return DiscoveryBatchResult(
                hits=(),
                error=f"http_{resp.status}",
                error_type="http_4xx",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=time.monotonic() - start,
            )

        try:
            data = await resp.json(content_type=None)
        except Exception as e:
            return DiscoveryBatchResult(
                hits=(),
                error=f"parse_error:{e}",
                error_type="parse_error",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=time.monotonic() - start,
            )

        if not isinstance(data, list):
            return DiscoveryBatchResult(
                hits=(),
                error="unexpected_response_format",
                error_type="parse_error",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=time.monotonic() - start,
            )

        # Extract subdomains from certs
        seen_domains: set[str] = set()
        hits: list[DiscoveryHit] = []
        now = time.time()

        for cert in data[:_MAX_CERTS]:
            if not isinstance(cert, dict):
                continue
            name_value = cert.get("name_value", "")
            if not name_value:
                continue

            for subdomain in name_value.split("\n"):
                subdomain = subdomain.strip()
                if not subdomain:
                    continue

                if _is_wildcard_only(subdomain):
                    continue

                if _is_private_domain(subdomain):
                    continue

                subdomain_lower = subdomain.lower()
                if subdomain_lower in seen_domains:
                    continue

                if len(hits) >= max_results:
                    break

                seen_domains.add(subdomain_lower)
                hits.append(
                    DiscoveryHit(
                        query=query,
                        title=f"CT: {subdomain}",
                        url=f"https://{subdomain}/",
                        snippet=f"Certificate Transparency match via crt.sh — {subdomain}",
                        source="crtsh",
                        rank=len(hits),
                        retrieved_ts=now,
                        score=1.0 - (len(hits) / max_results),
                        reason="ct_subdomain",
                        ct_name_value=name_value,
                        ct_common_name=cert.get("common_name"),
                        ct_issuer_name=cert.get("issuer_name"),
                        ct_not_before=cert.get("not_before"),
                        ct_not_after=cert.get("not_after"),
                        ct_entry_timestamp=cert.get("entry_timestamp"),
                        ct_serial_number=cert.get("serial_number"),
                    )
                )

            if len(hits) >= max_results:
                break

        elapsed = time.monotonic() - start

        if not hits:
            return DiscoveryBatchResult(
                hits=(),
                error="no_subdomains_found",
                error_type="provider_empty",
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )

        return DiscoveryBatchResult(
            hits=tuple(hits),
            error=None,
            error_type="none",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )

    except asyncio.CancelledError:
        raise  # re-raised — no session.close() needed with shared session

    except Exception as e:
        elapsed = time.monotonic() - start
        logger.warning(f"[crtsh] unexpected error: {e}")
        return DiscoveryBatchResult(
            hits=(),
            error=str(e),
            error_type="provider_exception",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )
