"""
discovery/circl_pdns_adapter.py — CIRCL Passive DNS Discovery Adapter

Sprint F229: CIRCL PDNS adapter aligned with discovery/source_registry tier-1.

Mirrors crtsh_adapter.py pattern:
  - async_get_aiohttp_session() from network.session_runtime
  - checked_aiohttp_get() from transport.circuit_breaker
  - DiscoveryBatchResult + DiscoveryHit from duckduckgo_adapter

CIRCL endpoint: https://www.circl.lu/pdns/query/{domain}
CIRCL returns: plain text, one JSON object per line (not JSON array)

No API key required — CIRCL PDNS community tier is keyless.
Fail-soft throughout.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum

import aiohttp

from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.security.passive_dns import parse_circl_pdns_text
from hledac.universal.transport.circuit_breaker import checked_aiohttp_get

from hledac.universal.tools.discovery_replay import (
    read_cassette,
    replay_enabled,
    replay_strict_enabled,
    write_cassette,
)

from .duckduckgo_adapter import DiscoveryBatchResult, DiscoveryHit

__all__ = [
    "async_search_circl_pdns",
    "call_circl_pdns",
    "PDNSOutcome",
    "PDNSProviderStatus",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider status
# ---------------------------------------------------------------------------


class PDNSProviderStatus(Enum):
    """F229: CIRCL PDNS provider status tags."""

    OK = "ok"
    HTTP_5XX = "http_5xx"
    HTTP_4XX = "http_4xx"
    TIMEOUT = "timeout"
    PARSE_ERROR = "parse_error"
    EMPTY = "empty"
    DISABLED = "disabled"
    COOLDOWN_ACTIVE = "cooldown_active"
    PROVIDER_FAILURE = "provider_failure"


@dataclass(frozen=True)
class PDNSOutcome:
    """
    Normalized CIRCL PDNS adapter outcome — F229.

    Fields:
        attempted:       True if HTTP call was attempted.
        query:           Domain that was submitted.
        result_count:    IP records returned (0 if not attempted or on error).
        error:           Error tag string or None on success.
        timeout:         True if call timed out.
        duration_s:      Wall-clock seconds for the call.
        skip_reason:     Reason for skip or None if attempted.
        cooldown_active: True if provider is in cooldown for this domain.
        cooldown_remaining_s: Seconds remaining in cooldown (0 if not in cooldown).
    """
    attempted: bool = False
    query: str = ""
    result_count: int = 0
    error: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    skip_reason: str | None = None
    cooldown_active: bool = False
    cooldown_remaining_s: float = 0.0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CIRCL_PDNS_URL = "https://www.circl.lu/pdns/query"
_HTTP_TIMEOUT_S = 8.0
_RATE_LIMIT_SLEEP_S = 2.0  # CIRCL ~30 req/min → conservative 1 req/2s
_MAX_HITS = 50
_COOLDOWN_DEFAULT_S = 300.0
_MAX_COOLDOWN_KEYS = 64

# Re-exported from security.passive_dns for backward compatibility with existing tests
from hledac.universal.security.passive_dns import (  # noqa: E402, F401
    _is_private_ip,
)


def _parse_pdns_line(line: str):
    """
    Parse a single CIRCL PDNS JSON line (for backward compatibility).

    Returns (ip, rrname, rrtype) or None if unparseable.
    CIRCL format: {"rrname":"...", "rrtype":"A", "rdata":"1.2.3.4", ...}
    Strict: returns None for plain text, empty rrname, or missing rdata.
    """
    import orjson

    try:
        record = orjson.loads(line)
    except Exception:
        return None

    rrname = record.get("rrname", "")
    rrtype = record.get("rrtype", "")
    rdata = record.get("rdata", "")

    if not rrname or not rdata:
        return None

    return str(rdata).strip(), str(rrname).strip(), str(rrtype).strip()


def _normalize_domain(domain: str) -> str:
    """Strip whitespace and lowercase the domain."""
    return domain.strip().lower()


# ---------------------------------------------------------------------------
# Cooldown map — keyed by normalized domain, FIFO eviction at cap
# ---------------------------------------------------------------------------

_pdns_cooldown: dict[str, tuple[float, str]] = {}


def _enter_cooldown(domain: str, reason: str, now: float) -> None:
    """Enter cooldown for a domain after provider failure."""
    domain_key = _normalize_domain(domain)
    if len(_pdns_cooldown) >= _MAX_COOLDOWN_KEYS and domain_key not in _pdns_cooldown:
        oldest_key = next(iter(_pdns_cooldown))
        _pdns_cooldown.pop(oldest_key, None)
    _pdns_cooldown[domain_key] = (now, reason)


def _check_cooldown(domain: str, now: float) -> tuple[bool, float, str]:
    """Check if domain is in active cooldown. Returns (active, remaining_s, reason)."""
    domain_key = _normalize_domain(domain)
    entry = _pdns_cooldown.get(domain_key)
    if entry is None:
        return False, 0.0, ""
    started_at, reason = entry
    remaining = _COOLDOWN_DEFAULT_S - (now - started_at)
    if remaining <= 0:
        _pdns_cooldown.pop(domain_key, None)
        return False, 0.0, ""
    return True, remaining, reason


def _clear_cooldown(domain: str) -> None:
    """Clear cooldown for a domain on provider success."""
    _pdns_cooldown.pop(_normalize_domain(domain), None)


# ---------------------------------------------------------------------------
# async_search_circl_pdns
# ---------------------------------------------------------------------------


async def async_search_circl_pdns(
    domain: str,
    max_results: int = 50,
    timeout_s: float = 5.0,
) -> DiscoveryBatchResult:
    """
    Search CIRCL PDNS for a domain — returns DiscoveryBatchResult.

    Args:
        domain:       Domain to query (e.g. "example.com").
        max_results: Max IP hits to return (default 50, hard cap 50).
        timeout_s:   HTTP timeout in seconds (default 5.0).

    Returns:
        DiscoveryBatchResult with PDNS-sourced IP hits.
        error is None on success, set on all failure paths.

    Fail-soft:
        - empty_query: no domain provided
        - timeout: asyncio.TimeoutError
        - http_5xx: server error
        - http_4xx: client error
        - network_error: connection issue
        - parse_error: CIRCL response unparseable
        - cooldown_active: domain in cooldown
        - no_records: domain in CIRCL but no IP records
    """
    start = time.monotonic()

    domain_norm = _normalize_domain(domain)
    if not domain_norm:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error="empty_query",
            error_type="invalid_query",
            provider_name="circl_pdns",
            provider_chain=("circl_pdns",),
            source_family="pdns",
            elapsed_s=elapsed,
        )

    # Check cooldown
    cooldown_now = time.monotonic()
    in_cooldown, _, _ = _check_cooldown(domain_norm, cooldown_now)
    if in_cooldown:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error="cooldown_active",
            error_type="cooldown_active",
            provider_name="circl_pdns",
            provider_chain=("circl_pdns",),
            source_family="pdns",
            elapsed_s=elapsed,
        )

    # Rate limit sleep before call
    await asyncio.sleep(_RATE_LIMIT_SLEEP_S)

    session: aiohttp.ClientSession | None = None
    try:
        session = await async_get_aiohttp_session()
        timeout = aiohttp.ClientTimeout(total=min(timeout_s, _HTTP_TIMEOUT_S))
        url = f"{_CIRCL_PDNS_URL}/{domain_norm}"

        try:
            async with asyncio.timeout(timeout_s):
                resp, err = await checked_aiohttp_get(
                    session,
                    url,
                    headers={"User-Agent": "Hledac/1.0 (research bot)"},
                    timeout=timeout,
                    failure_kind="circl_pdns",
                )
        except asyncio.CancelledError:
            raise

        elapsed = time.monotonic() - start

        if err:
            err_tag = "network_error"
            is_timeout = err == "timeout"
            if err.startswith("circuit_breaker_open:"):
                err_tag = "circuit_breaker_open"
            elif is_timeout:
                err_tag = "timeout"

            _enter_cooldown(domain_norm, err, cooldown_now)
            return DiscoveryBatchResult(
                hits=(),
                error=err,
                error_type=err_tag,
                provider_name="circl_pdns",
                provider_chain=("circl_pdns",),
                source_family="pdns",
                elapsed_s=elapsed,
            )

        assert resp is not None

        if resp.status >= 500:
            _enter_cooldown(domain_norm, f"http_{resp.status}", cooldown_now)
            return DiscoveryBatchResult(
                hits=(),
                error=f"http_{resp.status}",
                error_type="http_5xx",
                provider_name="circl_pdns",
                provider_chain=("circl_pdns",),
                source_family="pdns",
                elapsed_s=elapsed,
            )

        if resp.status == 404 or resp.status >= 400:
            return DiscoveryBatchResult(
                hits=(),
                error=f"http_{resp.status}" if resp.status >= 400 else None,
                error_type="provider_empty" if resp.status == 404 else "http_4xx",
                provider_name="circl_pdns",
                provider_chain=("circl_pdns",),
                source_family="pdns",
                elapsed_s=elapsed,
            )

        text = await resp.text()
        now_ts = time.time()

        records = parse_circl_pdns_text(text, max_results=max_results)
        hits: list[DiscoveryHit] = []

        for record in records:
            if len(hits) >= max_results:
                break

            reason = "pdns_aaaa_record" if record.rrtype.upper() == "AAAA" else "pdns_a_record"
            snippet = f"CIRCL PDNS: {record.rrname} → {record.ip} ({record.rrtype})"
            hits.append(
                DiscoveryHit(
                    query=domain,
                    title=f"PDNS: {record.ip}",
                    url=f"https://{record.rrname}/",
                    snippet=snippet,
                    source="circl_pdns",
                    rank=len(hits),
                    retrieved_ts=now_ts,
                    score=1.0 - (len(hits) / _MAX_HITS),
                    reason=reason,
                )
            )

        elapsed = time.monotonic() - start

        if not hits:
            return DiscoveryBatchResult(
                hits=(),
                error=None,
                error_type="provider_empty",
                provider_name="circl_pdns",
                provider_chain=("circl_pdns",),
                source_family="pdns",
                elapsed_s=elapsed,
            )

        _clear_cooldown(domain_norm)

        return DiscoveryBatchResult(
            hits=tuple(hits),
            error=None,
            error_type="none",
            provider_name="circl_pdns",
            provider_chain=("circl_pdns",),
            source_family="pdns",
            elapsed_s=elapsed,
        )

    except asyncio.CancelledError:
        raise

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        _enter_cooldown(domain_norm, "timeout", start)
        return DiscoveryBatchResult(
            hits=(),
            error="timeout",
            error_type="timeout",
            provider_name="circl_pdns",
            provider_chain=("circl_pdns",),
            source_family="pdns",
            elapsed_s=elapsed,
        )

    except Exception as e:
        elapsed = time.monotonic() - start
        logger.warning(f"[circl_pdns] unexpected error: {e}")
        return DiscoveryBatchResult(
            hits=(),
            error=str(e),
            error_type="provider_exception",
            provider_name="circl_pdns",
            provider_chain=("circl_pdns",),
            source_family="pdns",
            elapsed_s=elapsed,
        )


# ---------------------------------------------------------------------------
# call_circl_pdns
# ---------------------------------------------------------------------------


async def call_circl_pdns(
    domain: str,
    timeout_s: float = 5.0,
) -> tuple[DiscoveryBatchResult, PDNSOutcome]:
    """
    CIRCL PDNS lookup with normalized outcome — returns (DiscoveryBatchResult, PDNSOutcome).

    Args:
        domain:    Domain to query.
        timeout_s: HTTP timeout in seconds (default 5.0).

    Returns:
        (DiscoveryBatchResult, PDNSOutcome) tuple.
        outcome.attempted=True on every code path.
    """
    start = time.monotonic()

    domain_norm = _normalize_domain(domain)
    if not domain_norm:
        elapsed = time.monotonic() - start
        outcome = PDNSOutcome(
            attempted=True,
            query=domain,
            result_count=0,
            skip_reason="empty_query",
            duration_s=elapsed,
        )
        result = DiscoveryBatchResult(
            hits=(),
            error="empty_query",
            error_type="invalid_query",
            provider_name="circl_pdns",
            provider_chain=("circl_pdns",),
            source_family="pdns",
            elapsed_s=elapsed,
        )
        return result, outcome

    # Check cooldown
    cooldown_now = time.monotonic()
    in_cooldown, _, _ = _check_cooldown(domain_norm, cooldown_now)
    if in_cooldown:
        elapsed = time.monotonic() - start
        outcome = PDNSOutcome(
            attempted=True,
            query=domain_norm,
            result_count=0,
            skip_reason="cooldown_active",
            cooldown_active=True,
            cooldown_remaining_s=_COOLDOWN_DEFAULT_S,
            duration_s=elapsed,
        )
        result = DiscoveryBatchResult(
            hits=(),
            error="cooldown_active",
            error_type="cooldown_active",
            provider_name="circl_pdns",
            provider_chain=("circl_pdns",),
            source_family="pdns",
            elapsed_s=elapsed,
        )
        return result, outcome

    # F239A: Replay — read from cassette if available
    if replay_enabled():
        cached = read_cassette("circl_pdns", domain_norm)
        if cached is not None:
            cached_hits = cached.get("hits", ())
            elapsed = time.monotonic() - start
            outcome = PDNSOutcome(
                attempted=True,
                query=domain_norm,
                result_count=len(cached_hits),
                error=None,
                duration_s=elapsed,
            )
            result = DiscoveryBatchResult(
                hits=tuple(cached_hits) if isinstance(cached_hits, list) else cached_hits,
                error=None,
                error_type="replay_hit",
                provider_name="circl_pdns",
                provider_chain=("circl_pdns",),
                source_family="pdns",
                elapsed_s=elapsed,
            )
            return result, outcome
        elif replay_strict_enabled():
            # Cassette miss in strict mode: fail-soft, no live call
            elapsed = time.monotonic() - start
            outcome = PDNSOutcome(
                attempted=True,
                query=domain_norm,
                result_count=0,
                error="replay_miss",
                duration_s=elapsed,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error="replay_miss",
                error_type="replay_miss",
                provider_name="circl_pdns",
                provider_chain=("circl_pdns",),
                source_family="pdns",
                elapsed_s=elapsed,
            )
            return result, outcome
        # Non-strict miss: fall through to live call

    # Rate limit sleep
    await asyncio.sleep(_RATE_LIMIT_SLEEP_S)

    session: aiohttp.ClientSession | None = None
    raw_count = 0
    try:
        session = await async_get_aiohttp_session()
        timeout = aiohttp.ClientTimeout(total=min(timeout_s, _HTTP_TIMEOUT_S))
        url = f"{_CIRCL_PDNS_URL}/{domain_norm}"

        try:
            async with asyncio.timeout(timeout_s):
                resp, err = await checked_aiohttp_get(
                    session,
                    url,
                    headers={"User-Agent": "Hledac/1.0 (research bot)"},
                    timeout=timeout,
                    failure_kind="circl_pdns",
                )
        except asyncio.CancelledError:
            raise

        elapsed = time.monotonic() - start

        if err:
            err_tag = "network_error"
            is_timeout = err == "timeout"
            if err.startswith("circuit_breaker_open:"):
                err_tag = "circuit_breaker_open"
            elif is_timeout:
                err_tag = "timeout"

            _enter_cooldown(domain_norm, err, cooldown_now)
            outcome = PDNSOutcome(
                attempted=True,
                query=domain_norm,
                result_count=0,
                error=err,
                timeout=is_timeout,
                cooldown_active=True,
                cooldown_remaining_s=_COOLDOWN_DEFAULT_S,
                duration_s=elapsed,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error=err,
                error_type=err_tag,
                provider_name="circl_pdns",
                provider_chain=("circl_pdns",),
                source_family="pdns",
                elapsed_s=elapsed,
            )
            return result, outcome

        assert resp is not None

        if resp.status >= 500:
            _enter_cooldown(domain_norm, f"http_{resp.status}", cooldown_now)
            outcome = PDNSOutcome(
                attempted=True,
                query=domain_norm,
                result_count=0,
                error=f"http_{resp.status}",
                cooldown_active=True,
                cooldown_remaining_s=_COOLDOWN_DEFAULT_S,
                duration_s=elapsed,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error=f"http_{resp.status}",
                error_type="http_5xx",
                provider_name="circl_pdns",
                provider_chain=("circl_pdns",),
                source_family="pdns",
                elapsed_s=elapsed,
            )
            return result, outcome

        if resp.status == 404 or resp.status >= 400:
            elapsed = time.monotonic() - start
            outcome = PDNSOutcome(
                attempted=True,
                query=domain_norm,
                result_count=0,
                error=f"http_{resp.status}" if resp.status >= 400 else None,
                duration_s=elapsed,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error=f"http_{resp.status}" if resp.status >= 400 else None,
                error_type="provider_empty" if resp.status == 404 else "http_4xx",
                provider_name="circl_pdns",
                provider_chain=("circl_pdns",),
                source_family="pdns",
                elapsed_s=elapsed,
            )
            return result, outcome

        text = await resp.text()
        now_ts = time.time()

        records = parse_circl_pdns_text(text, max_results=_MAX_HITS)
        hits: list[DiscoveryHit] = []
        raw_count = 0

        for record in records:
            raw_count += 1
            if len(hits) >= _MAX_HITS:
                break

            reason = "pdns_aaaa_record" if record.rrtype.upper() == "AAAA" else "pdns_a_record"
            snippet = f"CIRCL PDNS: {record.rrname} → {record.ip} ({record.rrtype})"
            hits.append(
                DiscoveryHit(
                    query=domain,
                    title=f"PDNS: {record.ip}",
                    url=f"https://{record.rrname}/",
                    snippet=snippet,
                    source="circl_pdns",
                    rank=len(hits),
                    retrieved_ts=now_ts,
                    score=1.0 - (len(hits) / _MAX_HITS),
                    reason=reason,
                )
            )

        elapsed = time.monotonic() - start
        built_count = len(hits)

        if not hits:
            _clear_cooldown(domain_norm)
            outcome = PDNSOutcome(
                attempted=True,
                query=domain_norm,
                result_count=0,
                duration_s=elapsed,
            )
            result = DiscoveryBatchResult(
                hits=(),
                error=None,
                error_type="provider_empty",
                provider_name="circl_pdns",
                provider_chain=("circl_pdns",),
                source_family="pdns",
                elapsed_s=elapsed,
            )
            return result, outcome

        _clear_cooldown(domain_norm)

        # F239A: Record successful response for replay
        if replay_enabled():
            import msgspec

            # Convert msgspec.Struct hits to JSON-serializable dicts
            hits_data = [msgspec.json.decode(msgspec.json.encode(h)) for h in hits]
            write_cassette(
                "circl_pdns",
                domain_norm,
                {"hits": hits_data},
            )

        outcome = PDNSOutcome(
            attempted=True,
            query=domain_norm,
            result_count=built_count,
            error=None,
            duration_s=elapsed,
        )
        result = DiscoveryBatchResult(
            hits=tuple(hits),
            error=None,
            error_type="none",
            provider_name="circl_pdns",
            provider_chain=("circl_pdns",),
            source_family="pdns",
            elapsed_s=elapsed,
        )
        return result, outcome

    except asyncio.CancelledError:
        raise

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        _enter_cooldown(domain_norm, "timeout", start)
        outcome = PDNSOutcome(
            attempted=True,
            query=domain_norm,
            result_count=0,
            error="timeout",
            timeout=True,
            cooldown_active=True,
            cooldown_remaining_s=_COOLDOWN_DEFAULT_S,
            duration_s=elapsed,
        )
        result = DiscoveryBatchResult(
            hits=(),
            error="timeout",
            error_type="timeout",
            provider_name="circl_pdns",
            provider_chain=("circl_pdns",),
            source_family="pdns",
            elapsed_s=elapsed,
        )
        return result, outcome

    except Exception as e:
        elapsed = time.monotonic() - start
        logger.warning(f"[circl_pdns] unexpected error: {e}")
        outcome = PDNSOutcome(
            attempted=True,
            query=domain_norm,
            result_count=0,
            error=str(e),
            duration_s=elapsed,
        )
        result = DiscoveryBatchResult(
            hits=(),
            error=str(e),
            error_type="provider_exception",
            provider_name="circl_pdns",
            provider_chain=("circl_pdns",),
            source_family="pdns",
            elapsed_s=elapsed,
        )
        return result, outcome