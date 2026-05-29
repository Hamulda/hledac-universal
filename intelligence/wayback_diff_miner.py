"""
WaybackDiffMiner — Sprint F206AX
================================

Transport seam: injected fetch provider + canonical circuit breaker preflight.
No network at import time. Fail-soft when breaker unavailable.

Bounds:
    MAX_CDX_SNAPSHOTS_PER_DOMAIN = 50   — max CDX snapshots per domain/URL
    MAX_DOMAINS_PER_SPRINT = 100         — max domains/URLs per sprint
    MAX_CHANGE_EVENTS = 500              — max CDXDiffEvent output per sprint
    MAX_CONSECUTIVE_FAILURES = 3        — open circuit after 3 consecutive 429/503
    REQUEST_RATE_LIMIT = 0.5            — max 2 req/s (enforced via semaphore)
    TIMEOUT_PER_REQUEST = 30.0           — seconds

Guardrails:
    HTTP only, no JS renderer
    asyncio.gather return_exceptions=True + _check_gathered()
    Circuit opens after 3 consecutive 429/503 from Wayback CDX
    Fail-soft: errors never crash mining
    Optional injected fetch provider for test seam

Definition:
    change_type enum: "added" | "changed" | "disappeared" | "unchanged"
    "added"      = first seen in CDX run
    "changed"    = digest differs from previous snapshot
    "disappeared = previously seen digest no longer present in recent CDX window
    "unchanged"  = digest same as previous (skipped by default)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiohttp

try:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
except ImportError:
    CanonicalFinding = None

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_CDX_SNAPSHOTS_PER_DOMAIN: int = 50
MAX_DOMAINS_PER_SPRINT: int = 100
MAX_CHANGE_EVENTS: int = 500
MAX_CONSECUTIVE_FAILURES: int = 3
REQUEST_RATE_LIMIT: float = 0.5  # seconds between requests → 2 req/s max
TIMEOUT_PER_REQUEST: float = 30.0

# Wayback CDX API endpoint
WAYBACK_CDX_API: str = "https://web.archive.org/cdx/search/cdx"
WAYBACK_BASE_URL: str = "https://web.archive.org"


# ── Dataclasses ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CDXDiffEvent:
    """
    A single change event detected from Wayback CDX comparison.

    Fields:
        url:           Original URL that was queried
        timestamp:     ISO-8601 snapshot timestamp (YYYYMMDDHHMMSS)
        digest:        Content digest (Memento Digest header or proxy-approx)
        status_code:   HTTP status code of the snapshot (or None)
        change_type:   "added" | "changed" | "disappeared" | "unchanged"
        evidence_url:  Wayback Machine replay URL for this snapshot
    """
    url: str
    timestamp: str
    digest: str
    status_code: int | None
    change_type: str
    evidence_url: str


@dataclass
class WaybackDiffResult:
    """Result of a WaybackDiffMiner.mine() call."""
    input_count: int
    change_events: list[CDXDiffEvent] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    # F206AX telemetry
    transport_policy: str = "native_aiohttp"
    circuit_breaker_used: bool = False
    injected_fetch_used: bool = False
    fallback_reason: str | None = None
    archive_domain: str | None = None
    # F207F: normalized outcome fields
    attempted: bool = False
    raw_count: int = 0  # total CDX snapshot rows received before diff
    built_count: int = 0  # CDXDiffEvent records after diff
    accepted_count: None = None  # Wayback lane owns acceptance
    error: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    skip_reason: str | None = None

    def to_findings(
        self, query: str, sprint_id: str
    ) -> list[Any]:
        """Convert change events to CanonicalFinding list."""
        if CanonicalFinding is None:
            return []
        findings = []
        for event in self.change_events:
            try:
                payload = _build_payload(event)
                finding = CanonicalFinding(
                    finding_id=f"wdiff-{event.digest[:16]}-{event.timestamp}",
                    source_type="wayback_diff",
                    confidence=0.75,
                    query=query[:128],
                    ts=_timestamp_to_unix(event.timestamp),
                    payload_text=payload,
                    provenance=(
                        f"wayback:{event.url}",
                        f"digest:{event.digest}",
                        f"changed:{event.change_type}",
                        f"ts:{event.timestamp}",
                    ),
                )
                findings.append(finding)
            except Exception:
                continue
        return findings


# ── Helpers ────────────────────────────────────────────────────────────────────


def _timestamp_to_unix(ts: str) -> float:
    """Convert CDX timestamp string (YYYYMMDDHHMMSS) to Unix float."""
    try:
        return datetime.strptime(ts, "%Y%m%d%H%M%S").timestamp()
    except Exception:
        return 0.0


def _build_payload(event: CDXDiffEvent) -> str:
    """Build evidence envelope payload_text for CanonicalFinding."""
    return (
        f"[Wayback Diff] {event.change_type.upper()}: {event.url}\n"
        f"Snapshot: {event.timestamp} | Status: {event.status_code}\n"
        f"Digest: {event.digest}\n"
        f"Replay: {event.evidence_url}"
    )


def _extract_archive_domain(url: str = WAYBACK_CDX_API) -> str:
    """Extract netloc from a URL for circuit breaker lookup."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc
    except Exception:
        return "archive.org"


# ── Circuit Breaker ────────────────────────────────────────────────────────────


class _WaybackCircuitBreaker:
    """Local circuit breaker: opens after MAX_CONSECUTIVE_FAILURES 429/503."""

    def __init__(self, failure_threshold: int = MAX_CONSECUTIVE_FAILURES) -> None:
        self._threshold = failure_threshold
        self._consecutive_failures = 0
        self._open_until = 0.0

    def is_open(self) -> bool:
        if self._consecutive_failures < self._threshold:
            return False
        if time.monotonic() > self._open_until:
            # Auto-reset after cooldown
            self._consecutive_failures = 0
            self._open_until = 0.0
            return False
        return True

    def record_failure(self, status: int) -> None:
        if status in (429, 503):
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._threshold:
                self._open_until = time.monotonic() + 60.0
                logger.warning(
                    f"WaybackDiffMiner circuit OPEN (cooldown 60s) "
                    f"after {self._consecutive_failures} consecutive 429/503"
                )
        else:
            self._consecutive_failures = 0

    def record_success(self) -> None:
        self._consecutive_failures = 0


# ── Miner ────────────────────────────────────────────────────────────────────────


class WaybackDiffMiner:
    """
    Mines historical URL/domain changes from Wayback Machine CDX API.

    Works by:
      1. For each domain/URL, query CDX with collapse=digest&limit=50
      2. Compare consecutive snapshots to detect add/change/disappear
      3. Emit CDXDiffEvent for each detected change
      4. Convert to CanonicalFinding with source_type="wayback_diff"

    Guardrails:
      - asyncio.gather return_exceptions=True
      - _check_gathered() after each gather batch
      - Circuit breaker after 3 consecutive 429/503
      - HTTP only, no JS renderer
      - Bounded semaphore for rate limiting (2 req/s)
      - Optional injected fetch provider for test seam (F206AX)
      - Canonical transport circuit breaker preflight (F206AX)
    """

    def __init__(
        self,
        *,
        fetch_provider: Callable[..., Awaitable[Any]] | None = None,
        session_provider: Callable[[], Awaitable[aiohttp.ClientSession]] | None = None,
    ) -> None:
        """
        Initialize WaybackDiffMiner.

        Args:
            fetch_provider: Optional async callable(url, params, timeout) -> response.
                          If provided, used instead of native aiohttp session.get.
                          Enables test seam injection without changing OSINT logic.
            session_provider: Optional async callable() -> aiohttp.ClientSession.
                             If provided with fetch_provider, used for session.
                             If only session_provider provided, native fetch with that session.
        """
        self._breaker = _WaybackCircuitBreaker()
        self._semaphore: Any | None = None
        self._session: aiohttp.ClientSession | None = None
        self._last_request_at = 0.0
        # F206AX: injected providers — fail-soft if unavailable
        self._fetch_provider = fetch_provider
        self._session_provider = session_provider
        self._stats: dict[str, int] = {
            "domains_processed": 0,
            "cdx_snapshots_collected": 0,
            "changes_detected": 0,
            "circuit_open": 0,
            "rate_limited": 0,
            "errors": 0,
            "cb_preflight_skipped": 0,
            "cb_preflight_blocked": 0,
        }

    def _check_circuit_breaker(self, domain: str) -> bool:
        """
        F206AX: Canonical transport circuit breaker preflight.

        Returns True if request is allowed (breaker closed or not available).
        Returns False if circuit is open (skip request).
        Fail-soft: returns True if circuit_breaker module unavailable.
        """
        try:
            from hledac.universal.transport.circuit_breaker import domain_breaker_check
            decision = domain_breaker_check(domain)
            if not decision.allowed:
                self._stats["cb_preflight_blocked"] += 1
                logger.debug(f"Circuit breaker blocked {domain}: {decision.reason}")
                return False
            return True
        except Exception:
            # Fail-soft: if breaker unavailable, allow request
            self._stats["cb_preflight_skipped"] += 1
            return True

    # ── Public API ───────────────────────────────────────────────────────────

    async def mine(self, domains_or_urls: list[str]) -> WaybackDiffResult:
        """
        Mine Wayback CDX for each domain/URL and detect changes.

        Args:
            domains_or_urls: List of domains or full URLs to query (max 100)

        Returns:
            WaybackDiffResult with change_events list and F207F outcome fields.
        """
        start = time.monotonic()

        if not domains_or_urls:
            elapsed = time.monotonic() - start
            return WaybackDiffResult(
                input_count=0, change_events=[], stats=self._stats.copy(),
                transport_policy=self._transport_policy_label(),
                circuit_breaker_used=self._breaker is not None,
                injected_fetch_used=self._fetch_provider is not None,
                archive_domain=_extract_archive_domain(),
                attempted=True,
                raw_count=0,
                built_count=0,
                error=None,
                timeout=False,
                duration_s=elapsed,
                skip_reason="empty_input",
            )

        # Bound input
        targets = domains_or_urls[:MAX_DOMAINS_PER_SPRINT]

        # Lazy-init session and semaphore
        await self._ensure_session()
        if self._semaphore is None:
            import asyncio
            self._semaphore = asyncio.Semaphore(2)  # 2 concurrent CDX requests

        all_events: list[CDXDiffEvent] = []
        gathered_errors: list[BaseException] = []

        async def _fetch_one(target: str) -> list[CDXDiffEvent]:
            if self._breaker.is_open():
                self._stats["circuit_open"] += 1
                return []

            # F206AX: canonical circuit breaker preflight
            archive_domain = _extract_archive_domain(WAYBACK_CDX_API)
            if not self._check_circuit_breaker(archive_domain):
                return []

            # Rate limiting
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < REQUEST_RATE_LIMIT:
                import asyncio
                await asyncio.sleep(REQUEST_RATE_LIMIT - elapsed)
            self._last_request_at = time.monotonic()

            async with self._semaphore:  # type: ignore[union-attr]
                events = await self._fetch_and_diff(target)
                return events

        # Batch process with gather
        try:
            import asyncio
            results = await asyncio.gather(
                *[_fetch_one(t) for t in targets],
                return_exceptions=True,
            )

            # Collect exceptions
            for res in results:
                if isinstance(res, BaseException):
                    gathered_errors.append(res)

            for events in results:
                if isinstance(events, list):
                    all_events.extend(events)

        except Exception as e:
            logger.error(f"WaybackDiffMiner gather error: {e}")
            self._stats["errors"] += 1

        # Check gathered errors
        if gathered_errors:
            logger.warning(f"WaybackDiffMiner: {len(gathered_errors)} gather errors")
            self._stats["errors"] += len(gathered_errors)

        # Cap output
        all_events = all_events[:MAX_CHANGE_EVENTS]
        self._stats["domains_processed"] = len(targets)
        self._stats["changes_detected"] = len(all_events)
        elapsed = time.monotonic() - start

        # Determine error from gathered_errors
        error: str | None = None
        timeout = False
        if gathered_errors:
            # surface first error type
            first = gathered_errors[0]
            if isinstance(first, (asyncio.TimeoutError, asyncio.CancelledError, TimeoutError)):
                error = "timeout"
                timeout = True
            else:
                error = f"gather_error:{type(first).__name__}"

        return WaybackDiffResult(
            input_count=len(targets),
            change_events=all_events,
            stats=self._stats.copy(),
            transport_policy=self._transport_policy_label(),
            circuit_breaker_used=True,
            injected_fetch_used=self._fetch_provider is not None,
            fallback_reason=self._fallback_reason(),
            archive_domain=_extract_archive_domain(WAYBACK_CDX_API),
            attempted=True,
            raw_count=self._stats.get("cdx_snapshots_collected", 0),
            built_count=len(all_events),
            accepted_count=None,
            error=error,
            timeout=timeout,
            duration_s=elapsed,
        )

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _ensure_session(self) -> None:
        """Lazily initialize session from provider or native."""
        if self._session_provider is not None and (
            self._session is None or self._session.closed
        ):
            self._session = await self._session_provider()
        elif self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()

    def _transport_policy_label(self) -> str:
        """F206AX telemetry: describe active transport policy."""
        if self._fetch_provider is not None:
            return "injected_fetch"
        if self._session_provider is not None:
            return "injected_session"
        return "native_aiohttp"

    def _fallback_reason(self) -> str | None:
        """F206AX telemetry: reason for fallback path if any."""
        # No fallback needed — fetch_provider/session_provider are opt-in seam, not fallback
        return None

    async def _fetch_and_diff(self, target: str) -> list[CDXDiffEvent]:
        """Fetch CDX for target and diff consecutive snapshots."""
        # Normalize: if it's a bare domain, query CDX for that domain wildcard
        if not target.startswith(("http://", "https://")):
            query_url = f"*.{target}/*"
        else:
            query_url = target

        try:
            snapshots = await self._query_cdx(query_url)
        except TimeoutError:
            raise  # propagate to gather for timeout detection
        except asyncio.CancelledError:
            raise  # propagate to gather
        except Exception as e:
            logger.debug(f"CDX query failed for {target}: {e}")
            self._stats["errors"] += 1
            return []

        if not snapshots:
            return []

        self._stats["cdx_snapshots_collected"] += len(snapshots)

        # Diff: detect changes between consecutive snapshots
        events: list[CDXDiffEvent] = []
        prev_digest: str | None = None

        for snap in snapshots:
            digest = snap.get("digest", "")
            ts = snap.get("timestamp", "")
            status_str = snap.get("status_code", "")
            status: int | None = int(status_str) if status_str else None

            if not digest or not ts:
                continue

            evidence_url = f"{WAYBACK_BASE_URL}/web/{ts}/{target}"

            if prev_digest is None:
                change_type = "added"
            elif digest != prev_digest:
                change_type = "changed"
            else:
                change_type = "unchanged"

            # Only emit meaningful changes (skip unchanged)
            if change_type in ("added", "changed"):
                event = CDXDiffEvent(
                    url=target,
                    timestamp=ts,
                    digest=digest,
                    status_code=status,
                    change_type=change_type,
                    evidence_url=evidence_url,
                )
                events.append(event)

            prev_digest = digest

        return events

    async def _query_cdx(self, url: str) -> list[dict[str, str]]:
        """Query Wayback CDX API for a URL pattern."""
        params = {
            "url": url,
            "output": "json",
            "fl": "timestamp,original,statuscode,digest,length",
            "collapse": "digest",
            "limit": str(MAX_CDX_SNAPSHOTS_PER_DOMAIN),
        }

        try:
            session = self._session
            if session is None:
                return []

            # F206AX: use injected fetch provider if available
            if self._fetch_provider is not None:
                return await self._fetch_via_injected(params)
            return await self._fetch_via_native(params)

        except TimeoutError:
            raise  # propagate to gather for timeout detection
        except asyncio.CancelledError:
            raise  # propagate to gather
        except Exception as e:
            logger.debug(f"CDX query error for {url}: {e}")
            self._stats["errors"] += 1
            return []

    async def _fetch_via_injected(
        self, params: dict[str, Any]
    ) -> list[dict[str, str]]:
        """F206AX: Use injected fetch provider for testing seam."""
        try:
            # Injected fetch_provider(url, params, timeout) -> response
            assert self._fetch_provider is not None, "fetch_provider must be set for injected path"
            resp = await self._fetch_provider(
                WAYBACK_CDX_API,
                params=params,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_PER_REQUEST),
            )
            self._breaker.record_success()
            return await self._parse_cdx_response(resp)
        except TimeoutError:
            raise  # propagate to gather for timeout detection
        except asyncio.CancelledError:
            raise  # propagate to gather
        except Exception as e:
            logger.debug(f"Injected fetch error: {e}")
            self._stats["errors"] += 1
            return []

    async def _fetch_via_native(
        self, params: dict[str, Any]
    ) -> list[dict[str, str]]:
        """Native aiohttp fetch — original behavior."""
        session = self._session
        if session is None:
            return []
        try:
            async with session.get(
                WAYBACK_CDX_API,
                params=params,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_PER_REQUEST),
            ) as resp:
                self._breaker.record_success()

                if resp.status in (429, 503):
                    self._breaker.record_failure(resp.status)
                    logger.warning(f"Wayback CDX 429/503 for {params.get('url', '?')}")
                    return []

                if resp.status != 200:
                    return []

                return await self._parse_cdx_response(resp)

        except Exception as e:
            logger.debug(f"CDX query error: {e}")
            self._stats["errors"] += 1
            return []

    async def _parse_cdx_response(
        self, resp: aiohttp.ClientResponse
    ) -> list[dict[str, str]]:
        """Parse CDX JSON response into snapshot dicts."""
        if resp.status in (429, 503):
            self._breaker.record_failure(resp.status)
            return []
        if resp.status != 200:
            return []

        try:
            data = await resp.json()
            # CDX returns [header_row, ...data_rows] — skip header
            rows = data[1:] if data and isinstance(data, list) else []
            snapshots = []
            for row in rows:
                if len(row) >= 4:
                    snapshots.append({
                        "timestamp": row[0],
                        "original": row[1],
                        "status_code": row[2] if len(row) > 2 else "",
                        "digest": row[3] if len(row) > 3 else "",
                        "length": row[4] if len(row) > 4 else "0",
                    })
            return snapshots
        except Exception:
            return []
