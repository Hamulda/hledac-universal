"""
discovery/crtsh_adapter.py — CT/crt.sh Providerless Pivot Adapter

Sprint F206AU: replaces the ct_pivots stub in discovery_planner with a real
passive Certificate Transparency adapter backed by crt.sh JSON endpoint.

Passive only — no auth/API key, no body fetch beyond crt.sh JSON endpoint.
Fail-soft throughout.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

import aiohttp

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
# Does NOT include public TLDs like "example.com" (those are valid CT targets).
_PRIVATE_HOSTNAMES = {
    "localhost",
    "invalid",
    # RFC 6761 special-use names (must not be looked up in DNS)
    "localhost",
    "invalid",
    "test",
}

# Wildcard-only domain pattern (crt.sh often returns certs like "*.example.com")
_WILDCARD_ONLY_RE = re.compile(r"^\*\.")


def _is_private_domain(domain: str) -> bool:
    """Return True if domain is private, internal, or reserved."""
    domain_lower = domain.lower()
    # Check reserved hostnames (localhost, invalid, test, etc.)
    if domain_lower in _PRIVATE_HOSTNAMES:
        return True
    # IP address check
    if _is_ip_like(domain_lower):
        return True
    return False


def _is_ip_like(value: str) -> bool:
    """Return True if value looks like an IP address (v4 or v6)."""
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", value):
        return True
    if ":" in value:  # likely IPv6
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

    # If the whole query looks like a domain, use it directly
    if _looks_like_domain(query):
        return query

    # Scan tokens for domain-like
    for token in query.split():
        token = token.strip().lower()
        if "." in token and _looks_like_domain(token):
            # Skip single-label with a dot (like "foo.bar" where "bar" is TLD)
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
    # Must contain at least one dot
    if "." not in value:
        return False
    parts = value.split(".")
    if len(parts) < 2:
        return False
    # TLD-like last component
    tld = parts[-1]
    if len(tld) < 1 or len(tld) > 63:
        return False
    # No spaces or special chars
    if not re.match(r"^[a-z0-9.\-_]+$", tld):
        return False
    return True


def _is_wildcard_only(domain: str) -> bool:
    """Return True if domain is a wildcard cert (e.g. '*.example.com')."""
    return bool(_WILDCARD_ONLY_RE.match(domain))


async def checked_aiohttp_get(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    timeout: aiohttp.ClientTimeout,
) -> tuple[aiohttp.ClientResponse | None, str]:
    """
    Perform GET and return (response, error_string).

    error_string is "" on success.
    On failure, returns (None, err_string).

    Maps aiohttp errors to taxonomy:
      - asyncio.TimeoutError → "timeout"
      - aiohttp.ClientError → "network_error"
      - HTTP status 429 → "rate_limited"
      - HTTP status 403 → "captcha_or_blocked"
      - HTTP status 5xx → "server_error"
      - HTTP status 4xx → "client_error"
    """
    try:
        async with session.get(url, params=params, headers=headers, timeout=timeout) as resp:
            if resp.status == 429:
                return (None, "rate_limited")
            if resp.status == 403:
                return (None, "captcha_or_blocked")
            if resp.status >= 500:
                return (None, "server_error")
            if resp.status >= 400:
                # 4xx — return response so caller can attempt parse
                return (resp, "")
            return (resp, "")  # success
    except asyncio.TimeoutError:
        return (None, "timeout")
    except aiohttp.ClientError as e:
        return (None, f"network_error:{e}")
    except Exception as e:
        return (None, f"unknown_error:{e}")


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

    # Session setup
    try:
        import aiohttp as _aiohttp
    except ImportError:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error="aiohttp_not_available",
            error_type="import_error",
            provider_name="crtsh",
            provider_chain=("crtsh",),
            source_family="ct",
            elapsed_s=elapsed,
        )

    connector = _aiohttp.TCPConnector(
        limit=10,
        limit_per_host=3,
        ttl_dns_cache=300,
    )
    session: _aiohttp.ClientSession | None = None

    try:
        session = _aiohttp.ClientSession(connector=connector)
        timeout = _aiohttp.ClientTimeout(total=min(timeout_s, _HTTP_TIMEOUT_S))

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
                )
        except asyncio.CancelledError:
            raise  # always re-raise

        elapsed = time.monotonic() - start

        if err:
            # Map error string to taxonomy
            err_tag: str
            if err == "rate_limited":
                err_tag = "http_429"
            elif err == "captcha_or_blocked":
                err_tag = "http_403"
            elif err.startswith("server_error"):
                err_tag = "http_5xx"
            elif err == "client_error":
                err_tag = "http_4xx"
            elif err == "timeout":
                err_tag = "timeout"
            elif err.startswith("network_error"):
                err_tag = "network_error"
            else:
                err_tag = "provider_exception"

            return DiscoveryBatchResult(
                hits=(),
                error=err,
                error_type=err_tag,
                provider_name="crtsh",
                provider_chain=("crtsh",),
                source_family="ct",
                elapsed_s=elapsed,
            )

        # resp is guaranteed non-None here because err == "" means success
        assert resp is not None
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

            # crt.sh can return multiple names per cert (separated by newlines)
            for subdomain in name_value.split("\n"):
                subdomain = subdomain.strip()
                if not subdomain:
                    continue

                # Skip wildcard-only certs (often noise)
                if _is_wildcard_only(subdomain):
                    continue

                # Skip private/internal domains
                if _is_private_domain(subdomain):
                    continue

                # Skip duplicates
                subdomain_lower = subdomain.lower()
                if subdomain_lower in seen_domains:
                    continue

                # Stop at max_results
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
                        score=1.0 - (len(hits) / max_results),  # rank-based score
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
        # Re-raise — do not swallow
        if session:
            await session.close()
        raise

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

    finally:
        if session:
            await session.close()