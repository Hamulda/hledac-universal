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

import aiohttp

from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.transport.circuit_breaker import checked_aiohttp_get

from .duckduckgo_adapter import DiscoveryBatchResult, DiscoveryHit

__all__ = ["async_search_crtsh"]

logger = logging.getLogger(__name__)

# Hard cap — crt.sh can return thousands of certs for a popular domain
_MAX_CERTS = 50
_MAX_HITS = 20  # hard cap on DiscoveryHit results returned

# crt.sh endpoint — JSON output
_CRTSH_URL = "https://crt.sh/"

# Timeout for the HTTP call
_HTTP_TIMEOUT_S = 8.0

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
