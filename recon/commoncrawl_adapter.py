"""
CommonCrawl CDX Index adapter + WARC content replay.

Fetches archived URLs from CommonCrawl index for domain discovery (CDX).





Optionally replays actual WARC content from CommonCrawl S3 bucket.

Pattern: mirrors intelligence/wayback_cdx.py (Sprint F234).
WARC replay: ISSUE-P8-002 (Sprint G2).

Sprint F250F / ISSUE-P8-002
"""
import asyncio
import io
import logging
import time as time_mod
from dataclasses import dataclass, field
from typing import AsyncIterator
import msgspec

from hledac.universal.utils.async_helpers import _check_gathered
try:
    import orjson
except ImportError:
    orjson = None
try:
    import httpx
except ImportError:
    httpx = None
from hledac.universal.knowledge.duckdb_store import CanonicalFinding
logger = logging.getLogger('hledac')
CC_INDEX_API = 'https://index.commoncrawl.org/'
CC_COLLINFO_URL = 'https://index.commoncrawl.org/collinfo.json'
CC_S3_BASE = 'https://data.commoncrawl.org'
_TIMEOUT_PER_REQUEST = 30.0
_TIMEOUT_WARC_REQUEST = 90.0
_MAX_FINDINGS_PER_DOMAIN = 200
_MAX_DATA_BYTES = 50 * 1024 * 1024
_RATE_LIMIT_DELAY = 2.0
_MAX_REQUESTS_PER_SPRINT = 3
_MAX_WARC_FETCHES_PER_SPRINT = 5
_MAX_WARC_BYTES = 15 * 1024 * 1024  # 15 MB per WARC record (M1 8GB safe)
_WARC_CONCURRENCY = 2
_SOURCE_TYPE = 'commoncrawl_cdx'
_SOURCE_TYPE_CONTENT = 'commoncrawl_warc'
_WAYBACK_BASE_URL = 'https://web.archive.org'

class CCSearchResult(msgspec.Struct, gc=False):
    """
    Single row from CommonCrawl CDX.

    Fields mirror CDXSearchResult from wayback_cdx.py.
    """
    url: str
    timestamp: str
    mimetype: str
    status_code: str
    length: str
    digest: str
    offset: str = ''
    filename: str = ''

    def __post_init__(self) -> None:
        if self.url and self.timestamp:
            safe_url = self.url[:500]
            self.replay_url = f'{_WAYBACK_BASE_URL}/web/{self.timestamp}/{safe_url}'
        else:
            self.replay_url = ''
    replay_url: str = ''

    def to_finding_dict(self) -> dict:
        return {'source': _SOURCE_TYPE, 'url': self.url, 'timestamp': self.timestamp, 'mimetype': self.mimetype, 'status_code': self.status_code, 'length': self.length, 'digest': self.digest, 'replay_url': self.replay_url}

    def _parse_timestamp(self) -> float:
        try:
            from datetime import datetime
            return datetime.strptime(self.timestamp[:14], '%Y%m%d%H%M%S').timestamp()
        except Exception:
            return 0.0

    def _build_payload(self) -> str:
        parts = [f'[CommonCrawl CDX] {self.url}', f'Archived: {self.timestamp}', f'Type: {self.mimetype}', f'Status: {self.status_code}', f'Size: {self.length} bytes', f'Digest: {self.digest}', f'File: {self.filename}', f'Replay: {self.replay_url}']
        return '\n'.join(parts)

    def to_canonical_finding(self, query: str, _sprint_id: str='') -> CanonicalFinding | None:
        """Convert to CanonicalFinding (mirrors CDXSearchResult.to_canonical_finding)."""
        import uuid
        try:
            payload_text = self._build_payload()
            ts = self._parse_timestamp()
            finding_id = str(uuid.uuid4())
            return CanonicalFinding(finding_id=finding_id, query=query, source_type=_SOURCE_TYPE, confidence=0.45, ts=ts, provenance=(_SOURCE_TYPE,), payload_text=payload_text)
        except Exception as e:
            logger.debug(f'[commoncrawl] to_canonical_finding failed: {e}')
            return None

class CommonCrawlResult(msgspec.Struct, frozen=True, gc=False):
    """Result of a CommonCrawl fetch (mirrors CDXDeepSearchResult)."""
    query: str
    match_type: str = 'domain'
    total_rows: int = 0
    results: list[CCSearchResult] = field(default_factory=list)
    err: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    rate_limited: bool = False

    def to_findings(self, query: str, sprint_id: str) -> list[CanonicalFinding]:
        if self.err:
            return []
        findings = []
        for r in self.results:
            f = r.to_canonical_finding(query, sprint_id)
            if f is not None:
                findings.append(f)
        return findings

@dataclass
class WARCReplayResult:
    """Result of replaying a single URL from CommonCrawl WARC."""
    url: str
    timestamp: str
    content: str  # decoded HTTP body text
    mimetype: str
    status_code: str
    source_type: str = _SOURCE_TYPE_CONTENT
    warc_file: str = ''
    fetched_at: float = 0.0

    def to_canonical_finding(self, query: str, sprint_id: str = '') -> CanonicalFinding | None:
        """Convert WARC replay to CanonicalFinding."""
        import uuid
        try:
            ts = 0.0
            if self.timestamp:
                from datetime import datetime
                try:
                    ts = datetime.strptime(self.timestamp[:14], '%Y%m%d%H%M%S').timestamp()
                except Exception:
                    pass
            finding_id = str(uuid.uuid4())
            payload_parts = [
                f'[CommonCrawl WARC] {self.url}',
                f'Archived: {self.timestamp}',
                f'Type: {self.mimetype}',
                f'Status: {self.status_code}',
                f'WARC: {self.warc_file}',
            ]
            payload_text = '\n'.join(payload_parts)
            return CanonicalFinding(
                finding_id=finding_id,
                query=query,
                source_type=self.source_type,
                confidence=0.55,  # slightly higher than CDX-only
                ts=ts,
                provenance=(self.source_type,),
                payload_text=payload_text,
            )
        except Exception as e:
            logger.debug(f'[commoncrawl] WARC to_canonical_finding failed: {e}')
            return None


class WARCContentAdapter:
    """
    Replay actual HTTP content from CommonCrawl WARC files.

    Uses HTTP Range requests to fetch only the relevant WARC record slice
    from data.commoncrawl.org S3 bucket, then decompresses and parses
    the WARC record to extract the HTTP response body.

    M1 8GB bounds:
      - Max 5 WARC fetches per sprint (bandwidth budget)
      - Max 15 MB per record (memory budget)
      - Max 2 concurrent Range requests (I/O budget)
      - Lazy import of fastwarc (native extension loaded only when WARC replay is used)

    Invariants:
      - Fail-soft: errors return empty content, never raise
      - asyncio-only: streaming decompression via fastwarc GZipStream
      - Bounded: per-sprint concurrency and byte limits enforced
    """

    __slots__ = (
        '_warc_stats', '_warc_request_count', '_warc_bytes_total',
        '_warc_semaphore', '_warc_last_request', '_warc_rate_limited',
    )

    def __init__(self) -> None:
        self._warc_stats: dict[str, int] = {
            'warc_fetches': 0, 'warc_bytes': 0, 'warc_errors': 0, 'warc_rate_limited': 0,
        }
        self._warc_request_count: int = 0
        self._warc_bytes_total: int = 0
        self._warc_semaphore: asyncio.Semaphore = asyncio.Semaphore(_WARC_CONCURRENCY)
        self._warc_last_request: float = 0.0
        self._warc_rate_limited: bool = False

    def _get_warc_bytes_limit(self) -> int:
        """Dynamic byte limit based on M1 RAM pressure if available."""
        limit = _MAX_WARC_BYTES
        try:
            import psutil
            mem = psutil.virtual_memory()
            if mem.available < 2 * 1024 * 1024 * 1024:  # < 2 GB available
                limit = 5 * 1024 * 1024  # 5 MB
            elif mem.available < 4 * 1024 * 1024 * 1024:  # < 4 GB available
                limit = 10 * 1024 * 1024  # 10 MB
        except Exception:
            pass
        return limit

    async def _fetch_warc_range(
        self,
        session: httpx.AsyncClient,
        filename: str,
        offset: int,
        length: int,
    ) -> bytes | None:
        """
        Fetch a byte range from a WARC file on CommonCrawl S3.

        Uses HTTP Range header to fetch only the needed slice (zero-copy style,
        minimal memory footprint on M1).
        """
        url = f'{CC_S3_BASE}/{filename}'
        warc_bytes_limit = self._get_warc_bytes_limit()
        fetch_length = min(length, warc_bytes_limit)
        headers = {
            'Range': f'bytes={offset}-{offset + fetch_length - 1}',
            'User-Agent': 'Mozilla/5.0 (compatible; HledacBot/1.0; +mailto@investigace)',
            'Accept-Encoding': 'identity',  # we decompress ourselves
        }
        try:
            resp = await session.get(url, headers=headers, timeout=httpx.Timeout(_TIMEOUT_WARC_REQUEST))
            if resp.status_code not in (200, 206):
                logger.debug(f'[commoncrawl] WARC Range fetch {url} returned {resp.status_code}')
                return None
            return resp.content
        except Exception as e:
            logger.debug(f'[commoncrawl] WARC Range fetch failed: {e}')
            return None

    async def _stream_warc_records(
        self,
        warc_bytes: bytes,
    ) -> AsyncIterator[dict]:
        """
        Parse WARC records from raw gzipped bytes.

        Uses Python's gzip module for bounded decompression, then
        extracts HTTP response records using fastwarc if available
        (lazy import), falling back to naive WARC/1.1 parsing.

        Lazy import: fastwarc loaded only when this method is called.
        """
        import gzip as _gzip

        # Step 1: bounded decompression (M1 8GB safe, no fastwarc needed)
        try:
            bio = io.BytesIO(warc_bytes)
            with _gzip.GzipFile(fileobj=bio, mode='rb') as gf:
                decompressed = b''
                byte_limit = self._get_warc_bytes_limit()
                while True:
                    chunk = gf.read(65536)
                    if not chunk:
                        break
                    decompressed += chunk
                    if len(decompressed) > byte_limit:
                        break
        except Exception as e:
            logger.debug(f'[commoncrawl] gzip decompress failed: {e}')
            decompressed = b''

        if not decompressed:
            return

        # Step 2: try fastwarc first, then naive fallback
        parsed_via_fastwarc = False
        try:
            from fastwarc import warc as _warc_module
            from fastwarc import stream_io as _stream_io

            bio_warc = io.BytesIO(decompressed)
            bio_warc.seek(0)

            reader_cls = getattr(_warc_module, 'WarcReader', None) or getattr(_warc_module, 'WARCReader', None)
            if reader_cls is None:
                raise AttributeError('no WARC reader found')

            for record in reader_cls(bio_warc, record_types=None):
                if record is None:
                    continue
                try:
                    record_type = getattr(record, 'type', None)
                    if record_type != 'response':
                        continue
                    content_length = getattr(record, 'content_length', 0) or 0
                    if content_length > self._get_warc_bytes_limit():
                        continue
                    http_response = record.http_response
                    if http_response is None:
                        continue
                    body_bytes = http_response.body
                    if body_bytes:
                        content_type = http_response.headers.get('Content-Type', '')
                        status = str(http_response.status) if http_response.status else ''
                        yield {
                            'body': body_bytes,
                            'content_type': content_type,
                            'status': status,
                        }
                        parsed_via_fastwarc = True
                except Exception:
                    continue
        except Exception:
            pass

        # Step 3: naive WARC/1.1 parsing (fallback when fastwarc unavailable/failed)
        if not parsed_via_fastwarc:
            try:
                text = decompressed.decode('latin-1')
                # WARC/1.1 response record: 'WARC/1.1' header, then HTTP response block
                offset = 0
                byte_limit = self._get_warc_bytes_limit()
                while offset < len(text) and offset < byte_limit:
                    warc_header_pos = text.find('WARC/1.1', offset)
                    if warc_header_pos < 0:
                        break
                    # Find Content-Length header in WARC record
                    header_end = text.find('\r\n\r\n', warc_header_pos)
                    if header_end < 0:
                        break
                    header_block = text[warc_header_pos:header_end]
                    content_length = 0
                    for line in header_block.split('\r\n'):
                        if line.lower().startswith('content-length:'):
                            try:
                                content_length = int(line.split(':', 1)[1].strip())
                            except Exception:
                                pass
                            break
                    if content_length <= 0 or content_length > byte_limit:
                        offset = warc_header_pos + 1
                        continue
                    body_start = header_end + 4
                    body_bytes = decompressed[body_start:body_start + content_length]
                    if body_bytes:
                        # Parse HTTP status line from body
                        body_text = body_bytes.decode('latin-1', errors='replace')
                        status_code = ''
                        content_type = ''
                        nl_pos = body_text.find('\r\n')
                        if nl_pos >= 0:
                            status_line = body_text[:nl_pos]
                            if status_line.startswith('HTTP/'):
                                parts = status_line.split(' ', 2)
                                if len(parts) >= 2:
                                    status_code = parts[1]
                            # Find Content-Type
                            rest = body_text[nl_pos + 2:]
                            header_end_in_body = rest.find('\r\n\r\n')
                            if header_end_in_body >= 0:
                                http_headers = rest[:header_end_in_body]
                                for hline in http_headers.split('\r\n'):
                                    if hline.lower().startswith('content-type:'):
                                        content_type = hline.split(':', 1)[1].strip()
                                        break
                        yield {
                            'body': body_bytes,
                            'content_type': content_type,
                            'status': status_code,
                        }
                    offset = warc_header_pos + 1
            except Exception as e:
                logger.debug(f'[commoncrawl] naive WARC parse error: {e}')

    async def _extract_http_body(self, warc_bytes: bytes) -> tuple[bytes, str, str]:
        """
        Extract HTTP response body from WARC record bytes.

        Fallback: if fastwarc is unavailable or parsing fails, attempts
        naive WARC/1.1 HTTP response extraction (works for non-gzipped records).

        Returns: (body_bytes, content_type, status_code)
        """
        # Try fastwarc first (preferred path)
        record_dicts: list[dict] = []
        async for rec in self._stream_warc_records(warc_bytes):
            record_dicts.append(rec)
            if record_dicts:
                break  # we only need the first record

        if record_dicts:
            rec = record_dicts[0]
            body = rec['body']
            ct = rec.get('content_type', '')
            status = rec.get('status', '')
            return body, ct, status

        # Fallback: naive HTTP response extraction from raw bytes
        # WARC/1.1 response record structure:
        # WARC/1.1\r\n
        # Header: Content-Length: N\r\n
        # \r\n
        # HTTP/1.1 STATUS\r\n
        # Headers...\r\n
        # \r\n
        # <body>
        try:
            text = warc_bytes.decode('latin-1')
            header_end = text.find('\r\n\r\n')
            if header_end < 0:
                return b'', '', ''
            header_block = text[:header_end]
            status_line_end = header_block.find('\r\n')
            if status_line_end < 0:
                return b'', '', ''
            status_line = header_block[:status_line_end]
            status_code = ''
            if status_line.startswith('HTTP/'):
                parts = status_line.split(' ', 2)
                if len(parts) >= 2:
                    status_code = parts[1]
            headers: dict[str, str] = {}
            for line in header_block.split('\r\n')[1:]:
                if ':' in line:
                    k, v = line.split(':', 1)
                    headers[k.strip().lower()] = v.strip()
            body_start = header_end + 4
            body = warc_bytes[body_start:]
            content_type = headers.get('content-type', '')
            if 'gzip' in headers.get('content-encoding', ''):
                import gzip
                try:
                    body = gzip.decompress(body)
                except Exception:
                    pass
            return body, content_type, status_code
        except Exception:
            return b'', '', ''

    async def replay_url(
        self,
        result: CCSearchResult,
    ) -> WARCReplayResult | None:
        """
        Replay a single URL from CommonCrawl WARC archive.

        Fetches the WARC record via HTTP Range request, parses it,
        and returns the decoded HTTP body.

        Args:
            result: CCSearchResult with populated filename and offset

        Returns:
            WARCReplayResult with decoded content, or None on failure
        """
        if not result.filename or not result.offset:
            return None
        if self._warc_request_count >= _MAX_WARC_FETCHES_PER_SPRINT:
            self._warc_rate_limited = True
            return None
        try:
            offset = int(result.offset)
            length = int(result.length) if result.length else 0
            if length <= 0 or length > 50 * 1024 * 1024:
                length = _MAX_WARC_BYTES
        except (ValueError, OverflowError):
            return None

        async with self._warc_semaphore:
            try:
                from hledac.universal.transport.session_pool import session_pool
                session = await session_pool.httpx()
                warc_bytes = await self._fetch_warc_range(
                    session, result.filename, offset, length,
                )
                if warc_bytes is None:
                    self._warc_stats['warc_errors'] += 1
                    return None

                self._warc_request_count += 1
                self._warc_bytes_total += len(warc_bytes)
                self._warc_stats['warc_fetches'] += 1
                self._warc_stats['warc_bytes'] += len(warc_bytes)

                body_bytes, content_type, status_code = await self._extract_http_body(warc_bytes)

                # Decode to text (best effort)
                text = ''
                if body_bytes:
                    for encoding in ('utf-8', 'latin-1', 'cp1252'):
                        try:
                            text = body_bytes.decode(encoding, errors='replace')
                            break
                        except Exception:
                            continue

                return WARCReplayResult(
                    url=result.url,
                    timestamp=result.timestamp,
                    content=text,
                    mimetype=content_type or result.mimetype,
                    status_code=status_code or result.status_code,
                    source_type=_SOURCE_TYPE_CONTENT,
                    warc_file=result.filename,
                    fetched_at=time_mod.monotonic(),
                )
            except Exception as e:
                self._warc_stats['warc_errors'] += 1
                logger.debug(f'[commoncrawl] WARC replay error: {e}')
                return None

    async def replay_urls(
        self,
        results: list[CCSearchResult],
        _domain: str = '',
        max_fetch: int | None = None,
    ) -> list[WARCReplayResult]:
        """
        Replay multiple URLs from CommonCrawl WARC.

        Fetches WARC records for up to `max_fetch` CCSearchResults (default: 3).
        Uses bounded concurrency (max 2 parallel Range requests).

        Args:
            results: List of CCSearchResults with populated filename/offset
            _domain: Reserved for future domain-based rate limiting scope
            max_fetch: Max number of WARC fetches (default: 3)

        Returns:
            List of WARCReplayResult with decoded content
        """
        if max_fetch is None:
            max_fetch = 3
        to_fetch = [r for r in results if r.filename and r.offset][:max_fetch]
        if not to_fetch:
            return []

        tasks = [self.replay_url(r) for r in to_fetch]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        ok_results, errors = _check_gathered(gathered)
        for err in errors:
            logger.debug(f'[commoncrawl] replay_urls gather exception: {err}')
        out: list[WARCReplayResult] = [r for r in ok_results if isinstance(r, WARCReplayResult)]
        self._warc_stats['warc_errors'] += len(errors)
        return out

    def get_warc_stats(self) -> dict[str, int]:
        """Return WARC replay statistics."""
        return self._warc_stats.copy()

    @property
    def warc_rate_limited(self) -> bool:
        return self._warc_rate_limited


class CommonCrawlAdapter:
    """
    Fetch archived URLs from CommonCrawl CDX index.

    Transport: httpx (mirrors wayback_cdx.py pattern).
    Rate limit: max 3 requests/sprint, 2s between requests.
    Fail-soft: any exception returns empty list.

    Invariants:
      - Max 3 requests/sprint
      - 2s sleep between requests
      - 50 MB data cap per sprint
      - Offline-graceful: network failure → empty list
    """
    __slots__ = ('_stats', '_last_request', '_request_count', '_rate_limited', '_bloom', '_warc')

    def __init__(self) -> None:
        self._stats = {'domains_searched': 0, 'total_results': 0, 'errors': 0, 'rate_limited': 0}
        self._last_request: float = 0.0
        self._request_count: int = 0
        self._rate_limited: bool = False
        self._bloom: object | None = None
        self._warc: WARCContentAdapter | None = None

    async def fetch_index(self, domain: str, max_results: int=_MAX_FINDINGS_PER_DOMAIN) -> CommonCrawlResult:
        """
        Fetch CommonCrawl CDX records for a domain.

        Args:
            domain: Target domain (e.g. "example.com")
            max_results: Max CDX records to return

        Returns:
            CommonCrawlResult with parsed CCSearchResult list
        """
        t0 = time_mod.monotonic()
        if self._request_count >= _MAX_REQUESTS_PER_SPRINT:
            return CommonCrawlResult(query=domain, err='rate_limit_exceeded', rate_limited=True)
        elapsed = time_mod.monotonic() - self._last_request
        if elapsed < _RATE_LIMIT_DELAY:
            await asyncio.sleep(_RATE_LIMIT_DELAY - elapsed)
        url = f'{CC_INDEX_API}CC-MAIN-2025-40-index/cdx'
        params = {'url': f'*.{domain}', 'output': 'json', 'limit': str(max_results), 'fl': 'url,timestamp,mimetype,statuscode,length,digest,offset,filename'}
        if httpx is None:
            return CommonCrawlResult(query=domain, err='httpx_not_available')
        try:
            # F-01: session_pool.httpx() returns shared singleton
            from hledac.universal.transport.session_pool import session_pool
            session = await session_pool.httpx()
            resp = await session.get(
                url,
                params=params,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; HledacBot/1.0; +mailto@ investigace)'},
                timeout=httpx.Timeout(_TIMEOUT_PER_REQUEST),
            )
            if resp.status_code == 429:
                self._stats['rate_limited'] += 1
                return CommonCrawlResult(query=domain, err='rate_limited', rate_limited=True, duration_s=time_mod.monotonic() - t0)
            if resp.status_code != 200:
                return CommonCrawlResult(query=domain, err=f'HTTP_{resp.status_code}', duration_s=time_mod.monotonic() - t0)
            body = b''
            async for chunk in resp.iter_bytes(chunk_size=65536):
                body += chunk
                if len(body) > _MAX_DATA_BYTES:
                    break
            text = body.decode('utf-8', errors='replace')
        except TimeoutError:
            return CommonCrawlResult(query=domain, err='timeout', timeout=True, duration_s=time_mod.monotonic() - t0)
        except Exception as e:
            self._stats['errors'] += 1
            logger.debug(f'[commoncrawl] fetch failed for {domain}: {e}')
            return CommonCrawlResult(query=domain, err=str(e), duration_s=time_mod.monotonic() - t0)
        results = self._parse_response(text, domain)
        self._request_count += 1
        self._last_request = time_mod.monotonic()
        self._stats['domains_searched'] += 1
        self._stats['total_results'] += len(results)
        return CommonCrawlResult(query=domain, total_rows=len(results), results=results, duration_s=time_mod.monotonic() - t0)

    def _parse_response(self, text: str, domain: str) -> list[CCSearchResult]:
        """Parse CDX JSON Lines response into CCSearchResult list."""
        results: list[CCSearchResult] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            if orjson is None:
                continue
            try:
                row = orjson.loads(line)
            except Exception:
                continue
            if len(row) < 6:
                continue
            raw_url = str(row[0]) if row[0] else ''
            if not raw_url or self._is_noise_url(raw_url):
                continue
            result = CCSearchResult(url=raw_url, timestamp=str(row[1]) if len(row) > 1 else '', mimetype=str(row[2]) if len(row) > 2 else '', status_code=str(row[3]) if len(row) > 3 else '', length=str(row[4]) if len(row) > 4 else '', digest=str(row[5]) if len(row) > 5 else '', offset=str(row[6]) if len(row) > 6 else '', filename=str(row[7]) if len(row) > 7 else '')
            results.append(result)
        return results

    @staticmethod
    def _is_noise_url(url: str) -> bool:
        """Filter CDN/pkg noise URLs that are not real content."""
        if not url:
            return True
        lower = url.lower()
        noise = ('.css?', '.js?', '.ico?', '.png?', '.jpg?', '.jpeg?', '.gif?', '.svg?', '.woff2?', '.woff?', '.ttf?', '.eot?', '/node_modules/', '/dist/', '/build/', '/static/', 'cdn.', 'static.', 'assets.', 'media.', '.min.js', '.min.css')
        return any((p in lower for p in noise))

    def get_stats(self) -> dict:
        """Return adapter statistics."""
        return self._stats.copy()

    @property
    def rate_limited(self) -> bool:
        return self._rate_limited

    async def close(self) -> None:
        """Close any held resources. Safe to call even with session-less architecture."""
        pass

    def _ensure_warc(self) -> WARCContentAdapter:
        """Lazily create WARC content adapter on first use."""
        if self._warc is None:
            self._warc = WARCContentAdapter()
        return self._warc

    async def fetch_content(
        self,
        results: list[CCSearchResult],
        domain: str = '',
        max_fetch: int = 3,
    ) -> list[WARCReplayResult]:
        """
        Replay WARC content for a list of CCSearchResults.

        Uses HTTP Range requests to fetch WARC records from
        data.commoncrawl.org S3 bucket. Only fetches from results
        that have populated filename and offset fields.

        Args:
            results: List of CCSearchResults from fetch_index()
            domain: Optional domain for stats scope
            max_fetch: Max WARC fetches per call (default 3, max 5/sprint)

        Returns:
            List of WARCReplayResult with decoded HTTP body text
        """
        warc = self._ensure_warc()
        return await warc.replay_urls(results, _domain=domain, max_fetch=max_fetch)

    def get_warc_stats(self) -> dict[str, int]:
        """Return WARC replay statistics."""
        warc = self._ensure_warc()
        return warc.get_warc_stats()