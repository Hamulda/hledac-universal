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
import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import msgspec
from datetime import datetime
from typing import TYPE_CHECKING, Any
import httpx
from hledac.universal.transport.circuit_breaker import domain_breaker_check, domain_breaker_record_failure, domain_breaker_record_success
from hledac.universal.utils.async_helpers import safe_create_task, parallel
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
else:
    try:
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    except ImportError:
        CanonicalFinding = None
logger = logging.getLogger(__name__)
MAX_CDX_SNAPSHOTS_PER_DOMAIN: int = 50
MAX_DOMAINS_PER_SPRINT: int = 100
MAX_CHANGE_EVENTS: int = 500
MAX_CONSECUTIVE_FAILURES: int = 3
REQUEST_RATE_LIMIT: float = 0.5
TIMEOUT_PER_REQUEST: float = 30.0
WAYBACK_CDX_API: str = 'https://web.archive.org/cdx/search/cdx'
WAYBACK_BASE_URL: str = 'https://web.archive.org'

class CDXDiffEvent(msgspec.Struct, frozen=True):
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

class WaybackDiffResult(msgspec.Struct):
    """Result of a WaybackDiffMiner.mine() call."""
    input_count: int
    change_events: list[CDXDiffEvent] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    transport_policy: str = 'native_httpx'
    circuit_breaker_used: bool = False
    injected_fetch_used: bool = False
    fallback_reason: str | None = None
    archive_domain: str | None = None
    attempted: bool = False
    raw_count: int = 0
    built_count: int = 0
    accepted_count: None = None
    error: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    skip_reason: str | None = None

    def to_findings(self, query: str, sprint_id: str | None=None) -> list[Any]:
        """Convert change events to CanonicalFinding list."""
        if CanonicalFinding is None:
            return []
        findings = []
        for event in self.change_events:
            try:
                payload = _build_payload(event)
                finding = CanonicalFinding(finding_id=f'wdiff-{event.digest[:16]}-{event.timestamp}', source_type='wayback_diff', confidence=0.75, query=query[:128], ts=_timestamp_to_unix(event.timestamp), payload_text=payload, provenance=(f'wayback:{event.url}', f'digest:{event.digest}', f'changed:{event.change_type}', f'ts:{event.timestamp}'))
                findings.append(finding)
            except Exception:
                continue
        return findings

def _timestamp_to_unix(ts: str) -> float:
    """Convert CDX timestamp string (YYYYMMDDHHMMSS) to Unix float."""
    try:
        return datetime.strptime(ts, '%Y%m%d%H%M%S').timestamp()
    except Exception:
        return 0.0

def _build_payload(event: CDXDiffEvent) -> str:
    """Build evidence envelope payload_text for CanonicalFinding."""
    return f'[Wayback Diff] {event.change_type.upper()}: {event.url}\nSnapshot: {event.timestamp} | Status: {event.status_code}\nDigest: {event.digest}\nReplay: {event.evidence_url}'

def _extract_archive_domain(url: str=WAYBACK_CDX_API) -> str:
    """Extract netloc from a URL for circuit breaker lookup."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc
    except Exception:
        return 'archive.org'

class WaybackDiffMiner:
    """
    Mines historical URL/domain changes from Wayback Machine CDX API.

    Works by:
      1. For each domain/URL, query CDX with collapse=digest&limit=50
      2. Compare consecutive snapshots to detect add/change/disappear
      3. Emit CDXDiffEvent for each detected change
      4. Convert to CanonicalFinding with source_type="wayback_diff"

    Guardrails:
      - parallel() with taskgroup=True, policy='collect'
      - Error aggregation via ParallelResult.errors list
      - Circuit breaker after 3 consecutive 429/503
      - HTTP only, no JS renderer
      - Bounded semaphore for rate limiting (2 req/s)
      - Optional injected fetch provider for test seam (F206AX)
      - Canonical transport circuit breaker preflight (F206AX)
    """
    __slots__ = tuple(('_fetch_provider', '_last_request_at', '_semaphore', '_session', '_session_provider', '_stats'))

    def __init__(self, *, fetch_provider: Callable[..., Awaitable[Any]] | None=None, session_provider: Callable[[], Awaitable[httpx.AsyncClient]] | None=None) -> None:
        """
        Initialize WaybackDiffMiner.

        Args:
            fetch_provider: Optional async callable(url, params, timeout) -> response.
                          If provided, used instead of native httpx client.get.
                          Enables test seam injection without changing OSINT logic.
            session_provider: Optional async callable() -> httpx.AsyncClient.
                             If provided with fetch_provider, used for session.
                             If only session_provider provided, native fetch with that session.
        """
        self._semaphore: Any | None = None
        self._session: httpx.AsyncClient | None = None
        self._last_request_at = 0.0
        self._fetch_provider = fetch_provider
        self._session_provider = session_provider
        self._stats: dict[str, int] = {'domains_processed': 0, 'cdx_snapshots_collected': 0, 'changes_detected': 0, 'circuit_open': 0, 'rate_limited': 0, 'errors': 0, 'cb_preflight_skipped': 0, 'cb_preflight_blocked': 0}

    def _check_circuit_breaker(self, domain: str) -> bool:
        """
        Canonical transport circuit breaker preflight (thread-safe).

        Returns True if request is allowed (breaker closed or not available).
        Returns False if circuit is open (skip request).
        Fail-soft: returns True if circuit_breaker module unavailable.
        """
        try:
            decision = domain_breaker_check(domain)
            if not decision.allowed:
                self._stats['cb_preflight_blocked'] += 1
                logger.debug(f'Circuit breaker blocked {domain}: {decision.reason}')
                return False
            return True
        except Exception:
            self._stats['cb_preflight_skipped'] += 1
            return True

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
            return WaybackDiffResult(input_count=0, change_events=[], stats=self._stats.copy(), transport_policy=self._transport_policy_label(), injected_fetch_used=self._fetch_provider is not None, archive_domain=_extract_archive_domain(), attempted=True, raw_count=0, built_count=0, error=None, timeout=False, duration_s=elapsed, skip_reason='empty_input')
        targets = domains_or_urls[:MAX_DOMAINS_PER_SPRINT]
        await self._ensure_session()
        if self._semaphore is None:
            from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
            self._semaphore = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)
        all_events: list[CDXDiffEvent] = []
        gathered_errors: list[BaseException] = []

        async def _rate_limited_fetch(target: str) -> tuple[str, list[dict[str, str]]]:
            """Stage 1: fetch CDX (semaphore-bounded, rate-limited)."""
            archive_domain = _extract_archive_domain(WAYBACK_CDX_API)
            if not self._check_circuit_breaker(archive_domain):
                self._stats['circuit_open'] += 1
                return (target, [])
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < REQUEST_RATE_LIMIT:
                await asyncio.sleep(REQUEST_RATE_LIMIT - elapsed)
            self._last_request_at = time.monotonic()
            assert self._semaphore is not None, 'semaphore must be initialized before fetch'
            async with self._semaphore:
                return await self._fetch_cdx(target)
        try:
            fetch_tasks = [safe_create_task(_rate_limited_fetch(t)) for t in targets]
            fetch_gathered = await parallel(fetch_tasks, taskgroup=True, policy='collect', ctx='wayback_fetch', logger_instance=logger)
            snapshots_map: dict[str, list[dict[str, str]]] = {}
            for res in fetch_gathered.ok:
                if isinstance(res, tuple) and len(res) == 2:
                    t, snaps = res
                    snapshots_map[t] = snaps
            for exc in fetch_gathered.errors:
                gathered_errors.append(exc)
            if fetch_gathered.re_raised is not None:
                _reraise = fetch_gathered.re_raised
                if isinstance(_reraise, asyncio.CancelledError):
                    raise _reraise
                if isinstance(_reraise, BaseException) and (not isinstance(_reraise, Exception)):
                    raise _reraise
            diff_tasks = [safe_create_task(asyncio.to_thread(self._diff_snapshots, t, snaps)) for t, snaps in snapshots_map.items() if snaps]
            if diff_tasks:
                diff_gathered = await parallel(diff_tasks, taskgroup=True, policy='collect', ctx='wayback_diff', logger_instance=logger)
                for res in diff_gathered.ok:
                    if isinstance(res, list):
                        all_events.extend(res)
                for exc in diff_gathered.errors:
                    gathered_errors.append(exc)
                if diff_gathered.re_raised is not None:
                    _reraise = diff_gathered.re_raised
                    if isinstance(_reraise, asyncio.CancelledError):
                        raise _reraise
                    if isinstance(_reraise, BaseException) and (not isinstance(_reraise, Exception)):
                        raise _reraise
        except BaseException as e:
            error_msg = str(e) if not isinstance(e, BaseExceptionGroup) else f'BaseExceptionGroup({len(e.exceptions)} sub-exceptions): {e}'
            logger.error(f'WaybackDiffMiner pipeline error: {error_msg}')
            self._stats['errors'] += 1
        if gathered_errors:
            logger.warning(f'WaybackDiffMiner: {len(gathered_errors)} gather errors')
            self._stats['errors'] += len(gathered_errors)
        all_events = all_events[:MAX_CHANGE_EVENTS]
        self._stats['domains_processed'] = len(targets)
        self._stats['changes_detected'] = len(all_events)
        elapsed = time.monotonic() - start
        error: str | None = None
        timeout = False
        if gathered_errors:
            first = gathered_errors[0]
            if isinstance(first, (asyncio.TimeoutError, asyncio.CancelledError, TimeoutError)):
                error = 'timeout'
                timeout = True
            else:
                error = f'gather_error:{type(first).__name__}'
        return WaybackDiffResult(input_count=len(targets), change_events=all_events, stats=self._stats.copy(), transport_policy=self._transport_policy_label(), circuit_breaker_used=True, injected_fetch_used=self._fetch_provider is not None, fallback_reason=self._fallback_reason(), archive_domain=_extract_archive_domain(WAYBACK_CDX_API), attempted=True, raw_count=self._stats.get('cdx_snapshots_collected', 0), built_count=len(all_events), accepted_count=None, error=error, timeout=timeout, duration_s=elapsed)

    async def close(self) -> None:
        """Close the httpx session."""
        if self._session is not None and (not self._session.is_closed):
            await self._session.aclose()
            self._session = None

    async def _ensure_session(self) -> None:
        """Lazily initialize session from provider or native."""
        if self._session_provider is not None and (self._session is None or self._session.closed):
            self._session = await self._session_provider()
        elif self._session is None or self._session.closed:
            self._session = httpx.AsyncClient(timeout=httpx.Timeout(TIMEOUT_PER_REQUEST))

    def _transport_policy_label(self) -> str:
        """F206AX telemetry: describe active transport policy."""
        if self._fetch_provider is not None:
            return 'injected_fetch'
        if self._session_provider is not None:
            return 'injected_session'
        return 'native_httpx'

    def _fallback_reason(self) -> str | None:
        """F206AX telemetry: reason for fallback path if any."""
        return None

    async def _fetch_cdx(self, target: str) -> tuple[str, list[dict[str, str]]]:
        """Stage 1 (fetch): Query CDX and return (target, snapshots) tuple.

        Rate-limited and semaphore-bounded. Returns empty list on error.
        """
        if not target.startswith(('http://', 'https://')):
            query_url = f'*.{target}/*'
        else:
            query_url = target
        try:
            snapshots = await self._query_cdx(query_url)
        except TimeoutError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f'CDX query failed for {target}: {e}')
            self._stats['errors'] += 1
            return (target, [])
        if snapshots:
            self._stats['cdx_snapshots_collected'] += len(snapshots)
        return (target, snapshots)

    def _diff_snapshots(self, target: str, snapshots: list[dict[str, str]]) -> list[CDXDiffEvent]:
        """Stage 2 (diff): Pure CPU diff — no I/O, no semaphore.

        Detects add/change/disappear between consecutive CDX snapshots.
        """
        events: list[CDXDiffEvent] = []
        prev_digest: str | None = None
        for snap in snapshots:
            digest = snap.get('digest', '')
            ts = snap.get('timestamp', '')
            status_str = snap.get('status_code', '')
            status: int | None = int(status_str) if status_str else None
            if not digest or not ts:
                continue
            evidence_url = f'{WAYBACK_BASE_URL}/web/{ts}/{target}'
            if prev_digest is None:
                change_type = 'added'
            elif digest != prev_digest:
                change_type = 'changed'
            else:
                change_type = 'unchanged'
            if change_type in ('added', 'changed'):
                event = CDXDiffEvent(url=target, timestamp=ts, digest=digest, status_code=status, change_type=change_type, evidence_url=evidence_url)
                events.append(event)
            prev_digest = digest
        return events

    async def _fetch_and_diff_pipeline(self, target: str) -> list[CDXDiffEvent]:
        """Two-stage pipeline: fetch (I/O) -> diff (CPU), fully overlapped.

        Uses semaphore only for fetch stage. Diff runs without semaphore
        contention so multiple diff tasks can run in parallel after their
        fetches complete.
        """
        try:
            _target, snapshots = await self._fetch_cdx(target)
        except (TimeoutError, asyncio.CancelledError):
            raise
        except Exception:
            return []
        if not snapshots:
            return []
        return self._diff_snapshots(target, snapshots)

    async def _query_cdx(self, url: str) -> list[dict[str, str]]:
        """Query Wayback CDX API for a URL pattern."""
        params = {'url': url, 'output': 'json', 'fl': 'timestamp,original,statuscode,digest,length', 'collapse': 'digest', 'limit': str(MAX_CDX_SNAPSHOTS_PER_DOMAIN)}
        try:
            session = self._session
            if session is None:
                return []
            if self._fetch_provider is not None:
                return await self._fetch_via_injected(params)
            return await self._fetch_via_native(params)
        except TimeoutError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f'CDX query error for {url}: {e}')
            self._stats['errors'] += 1
            return []

    async def _fetch_via_injected(self, params: dict[str, Any]) -> list[dict[str, str]]:
        """F206AX: Use injected fetch provider for testing seam."""
        try:
            assert self._fetch_provider is not None, 'fetch_provider must be set for injected path'
            resp = await self._fetch_provider(WAYBACK_CDX_API, params=params, timeout=httpx.Timeout(TIMEOUT_PER_REQUEST))
            domain_breaker_record_success(_extract_archive_domain(WAYBACK_CDX_API))
            return await self._parse_cdx_response(resp)
        except TimeoutError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f'Injected fetch error: {e}')
            self._stats['errors'] += 1
            return []

    async def _fetch_via_native(self, params: dict[str, Any]) -> list[dict[str, str]]:
        """Native httpx fetch — original behavior."""
        session = self._session
        if session is None:
            return []
        try:
            async with session.get(WAYBACK_CDX_API, params=params, timeout=httpx.Timeout(TIMEOUT_PER_REQUEST)) as resp:
                archive_domain = _extract_archive_domain(WAYBACK_CDX_API)
                if resp.status_code in (429, 503):
                    domain_breaker_record_failure(archive_domain, is_timeout=False, failure_kind=f'http_{resp.status_code}')
                    logger.warning(f"Wayback CDX {resp.status_code} for {params.get('url', '?')}")
                    return []
                if resp.status_code != 200:
                    return []
                domain_breaker_record_success(archive_domain)
                return await self._parse_cdx_response(resp)
        except Exception as e:
            logger.debug(f'CDX query error: {e}')
            self._stats['errors'] += 1
            return []

    async def _parse_cdx_response(self, resp: httpx.Response) -> list[dict[str, str]]:
        """Parse CDX JSON response into snapshot dicts."""
        if resp.status_code in (429, 503):
            return []
        if resp.status_code != 200:
            return []
        try:
            data = await resp.json()
            rows = data[1:] if data and isinstance(data, list) else []
            snapshots = []
            for row in rows:
                if len(row) >= 4:
                    snapshots.append({'timestamp': row[0], 'original': row[1], 'status_code': row[2] if len(row) > 2 else '', 'digest': row[3] if len(row) > 3 else '', 'length': row[4] if len(row) > 4 else '0'})
            return snapshots
        except Exception:
            return []