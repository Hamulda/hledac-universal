"""
Temporal Archaeologist
======================

Advanced temporal content recovery and timeline reconstruction system.

Features:
- Deleted content recovery from multiple archive sources
- Version history reconstruction
- Temporal entity resolution (tracking identity changes over time)
- Cross-temporal correlation (finding related events across time)
- Temporal anomaly detection (gaps, sudden changes, disappearances)
- Timeline reconstruction from fragmented data

Archive Sources:
- Wayback Machine (Internet Archive)
- Archive.today / archive.ph
- Google Cache
- Bing Cache
- Common Crawl (index querying)
- Git history mining
- Social media archives

M1 8GB Optimized:
- Async archive queries
- Streaming content processing
- Incremental timeline building
- Memory-efficient diff algorithms
"""
import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
import msgspec
from datetime import UTC, datetime
from difflib import SequenceMatcher
from enum import Enum, StrEnum
from typing import Any
import httpx
from urllib.parse import quote, urlparse
import numpy as np
from hledac.universal.utils.async_helpers import safe_gather_ok
from hledac.universal.transport.session_pool import session_pool
from hledac.universal.utils.rate_limiter import RateLimitConfig, RateLimiter
logger = logging.getLogger(__name__)

class TemporalError(StrEnum):
    """String-based error codes for temporal archaeology operations."""
    SOURCE_ERROR = '{source}: {error}'

class ArchiveSource(Enum):
    """Sources of archived content."""
    WAYBACK = 'wayback'
    ARCHIVE_TODAY = 'archive_today'
    GOOGLE_CACHE = 'google_cache'
    BING_CACHE = 'bing_cache'
    COMMON_CRAWL = 'common_crawl'
    GIT_HISTORY = 'git_history'
    SOCIAL_ARCHIVE = 'social_archive'
    IPFS = 'ipfs'

class AnomalyType(Enum):
    """Types of temporal anomalies."""
    DISAPPEARANCE = 'disappearance'
    IDENTITY_CHANGE = 'identity_change'
    CONTENT_WIPE = 'content_wipe'
    ACTIVITY_GAP = 'activity_gap'
    SUDDEN_CHANGE = 'sudden_change'
    DATA_CORRUPTION = 'data_corruption'
    FREQUENCY_SHIFT = 'frequency_shift'

class EntityType(Enum):
    """Types of entities that can be tracked."""
    URL = 'url'
    USERNAME = 'username'
    EMAIL = 'email'
    DOMAIN = 'domain'
    CONTENT_HASH = 'content_hash'
    REPOSITORY = 'repository'

class ArchivedVersion(msgspec.Struct):
    """Represents a single archived version of content."""
    url: str
    timestamp: datetime
    content_hash: str
    content: str | None
    source: str
    is_deleted: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.content_hash and self.content:
            self.content_hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]

    @property
    def age_days(self) -> int:
        """Calculate age in days from now."""
        return (datetime.now(UTC) - self.timestamp).days

    def to_dict(self) -> dict[str, Any]:
        return {'url': self.url, 'timestamp': self.timestamp.isoformat(), 'content_hash': self.content_hash, 'source': self.source, 'is_deleted': self.is_deleted, 'metadata': self.metadata, 'age_days': self.age_days}

class EntitySnapshot(msgspec.Struct):
    """Snapshot of an entity at a specific point in time."""
    timestamp: datetime
    identifier: str
    content_hash: str
    content_preview: str
    metadata: dict[str, Any] = field(default_factory=dict)

class IdentityChange(msgspec.Struct):
    """Represents an identity change event."""
    from_identifier: str
    to_identifier: str
    timestamp: datetime
    change_type: str
    confidence: float
    evidence: list[str] = field(default_factory=list)

class TemporalGap(msgspec.Struct, frozen=True):
    """Represents a gap in temporal data."""
    start_time: datetime
    end_time: datetime
    duration_days: int
    gap_type: str
    severity: float

class EntityTimeline(msgspec.Struct, frozen=True):
    """Complete timeline for an entity."""
    entity_id: str
    entity_type: str
    snapshots: list[EntitySnapshot]
    identity_changes: list[IdentityChange]
    temporal_gaps: list[TemporalGap]
    confidence_score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.snapshots:
            self.snapshots.sort(key=lambda x: x.timestamp)

    @property
    def first_seen(self) -> datetime | None:
        return self.snapshots[0].timestamp if self.snapshots else None

    @property
    def last_seen(self) -> datetime | None:
        return self.snapshots[-1].timestamp if self.snapshots else None

    @property
    def total_snapshots(self) -> int:
        return len(self.snapshots)

    @property
    def lifespan_days(self) -> int:
        if self.first_seen and self.last_seen:
            return (self.last_seen - self.first_seen).days
        return 0

class TemporalAnomaly(msgspec.Struct, frozen=True):
    """Detected temporal anomaly."""
    type: str
    description: str
    severity: float
    timestamp: datetime | None
    evidence: list[str] = field(default_factory=list)
    affected_snapshots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {'type': self.type, 'description': self.description, 'severity': self.severity, 'timestamp': self.timestamp.isoformat() if self.timestamp else None, 'evidence': self.evidence, 'affected_snapshots': self.affected_snapshots}

class TemporalCorrelation(msgspec.Struct, frozen=True):
    """Correlation between two entities across time."""
    entity_a: str
    entity_b: str
    correlation_score: float
    overlapping_periods: list[tuple[datetime, datetime]]
    shared_attributes: dict[str, Any] = field(default_factory=dict)
    temporal_proximity: list[dict[str, Any]] = field(default_factory=list)

class ResolvedEntity(msgspec.Struct, frozen=True):
    """Result of temporal entity resolution."""
    canonical_id: str
    all_identifiers: set[str]
    timeline: EntityTimeline
    resolution_confidence: float
    resolution_method: str

class RecoveryResult(msgspec.Struct, frozen=True):
    """Result of content recovery operation."""
    success: bool
    recovered_versions: list[ArchivedVersion]
    total_sources_checked: int
    sources_succeeded: int
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

class TemporalArchaeologist:
    """
    Advanced temporal content recovery and timeline reconstruction system.

    This class provides comprehensive tools for:
    - Recovering deleted content from multiple archive sources
    - Reconstructing version history from fragmented data
    - Tracking entity identity changes over time
    - Finding correlations between events across time
    - Detecting temporal anomalies (gaps, sudden changes, disappearances)
    - Building timelines from scattered archival sources

    M1 8GB Optimizations:
    - Async concurrent queries to multiple archives
    - Streaming content processing with chunked reading
    - Incremental timeline building to minimize memory usage
    - Memory-efficient diff algorithms using rolling hashes
    """
    WAYBACK_CDX_URL = 'https://web.archive.org/cdx/search/cdx'
    WAYBACK_RAW_URL = 'https://web.archive.org/web/{timestamp}id_/{url}'
    ARCHIVE_TODAY_URL = 'https://archive.today'
    GOOGLE_CACHE_URL = 'https://webcache.googleusercontent.com/search?q=cache:'
    BING_CACHE_URL = 'https://r.jina.ai/http://'
    COMMON_CRAWL_INDEX = 'https://index.commoncrawl.org'
    __slots__ = tuple(('_anomalies_detected', '_cache', '_fetched_snapshots', '_queries_made', '_rate_limiter', '_session', '_versions_recovered', 'cache_enabled', 'max_concurrent_requests', 'max_content_size', 'request_timeout'))

    def __init__(self, max_concurrent_requests: int=3, request_timeout: float=30.0, cache_enabled: bool=True, max_content_size_mb: float=10.0):
        """
        Initialize TemporalArchaeologist.

        Args:
            max_concurrent_requests: Maximum concurrent archive requests
            request_timeout: Timeout for archive requests in seconds
            cache_enabled: Whether to cache results
            max_content_size_mb: Maximum content size to process in MB
        """
        self.max_concurrent_requests = max_concurrent_requests
        self.request_timeout = request_timeout
        self.cache_enabled = cache_enabled
        self.max_content_size = max_content_size_mb * 1024 * 1024
        self._session: Any | None = None
        self._cache: dict[str, Any] = {}
        self._rate_limiter = RateLimiter(RateLimitConfig(base_rate=1.0))
        self._fetched_snapshots: set[str] = set()
        self._queries_made = 0
        self._versions_recovered = 0
        self._anomalies_detected = 0
        logger.info('TemporalArchaeologist initialized')

    async def __aenter__(self):
        """Async context manager entry."""
        self._session = await httpx.AsyncClient()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit — pool manages session lifecycle."""
        self._session = None

    async def recover_deleted_content(self, url: str, sources: list[str] | None=None, from_date: datetime | None=None, to_date: datetime | None=None, include_content: bool=True) -> RecoveryResult:
        """
        Recover deleted content from multiple archive sources.

        Args:
            url: URL to recover
            sources: List of sources to check (default: all)
            from_date: Start date for recovery
            to_date: End date for recovery
            include_content: Whether to fetch full content

        Returns:
            RecoveryResult with recovered versions
        """
        if sources is None:
            sources = ['wayback', 'archive_today', 'google_cache', 'bing_cache']
        logger.info(f'Recovering deleted content for: {url}')
        self._queries_made += 1
        recovered_versions: list[ArchivedVersion] = []
        errors: list[str] = []
        sources_succeeded = 0
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
        semaphore = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)

        async def check_source(source: str) -> tuple[list[ArchivedVersion], str | None]:
            async with semaphore:
                try:
                    if source == 'wayback':
                        versions = await self._recover_from_wayback(url, from_date, to_date, include_content)
                    elif source == 'archive_today':
                        versions = await self._recover_from_archive_today(url, include_content)
                    elif source == 'google_cache':
                        versions = await self._recover_from_google_cache(url, include_content)
                    elif source == 'bing_cache':
                        versions = await self._recover_from_bing_cache(url, include_content)
                    elif source == 'common_crawl':
                        versions = await self._recover_from_common_crawl(url, include_content)
                    else:
                        return ([], f'Unknown source: {source}')
                    return (versions, None)
                except Exception as e:
                    logger.warning(f'Recovery from {source} failed: {e}')
                    return ([], str(e))
        tasks = [check_source(source) for source in sources]
        results = await safe_gather_ok(*tasks, label='temporal_archaeologist:391')
        for source, result in zip(sources, results, strict=False):
            if isinstance(result, Exception):
                errors.append(TemporalError.SOURCE_ERROR.format(source=source, error=str(result)))
            else:
                versions, error = result
                if error:
                    errors.append(TemporalError.SOURCE_ERROR.format(source=source, error=str(error)))
                else:
                    recovered_versions.extend(versions)
                    if versions:
                        sources_succeeded += 1
        recovered_versions.sort(key=lambda x: x.timestamp, reverse=True)
        seen_hashes = set()
        unique_versions = []
        for version in recovered_versions:
            if version.content_hash not in seen_hashes:
                seen_hashes.add(version.content_hash)
                unique_versions.append(version)
        self._versions_recovered += len(unique_versions)
        logger.info(f'Recovery complete: {len(unique_versions)} unique versions from {sources_succeeded}/{len(sources)} sources')
        return RecoveryResult(success=unique_versions, recovered_versions=unique_versions, total_sources_checked=len(sources), sources_succeeded=sources_succeeded, errors=errors, metadata={'url': url, 'date_range': (from_date.isoformat() if from_date else None, to_date.isoformat() if to_date else None)})

    async def reconstruct_version_history(self, identifier: str, id_type: str='url', from_date: datetime | None=None, to_date: datetime | None=None) -> EntityTimeline:
        """
        Reconstruct version history for an entity.

        Args:
            identifier: Entity identifier (URL, username, etc.)
            id_type: Type of identifier (url, username, email, etc.)
            from_date: Start date for reconstruction
            to_date: End date for reconstruction

        Returns:
            EntityTimeline with reconstructed history
        """
        logger.info(f'Reconstructing version history for {id_type}: {identifier}')
        if id_type == 'url':
            recovery_result = await self.recover_deleted_content(identifier, from_date=from_date, to_date=to_date)
            versions = recovery_result.recovered_versions
        elif id_type == 'repository':
            versions = await self._recover_from_git_history(identifier)
        else:
            versions = await self._search_by_entity(identifier, id_type)
        snapshots = []
        for version in versions:
            content_preview = ''
            if version.content:
                content_preview = version.content[:500] + '...' if len(version.content) > 500 else version.content
            snapshot = EntitySnapshot(timestamp=version.timestamp, identifier=version.url, content_hash=version.content_hash, content_preview=content_preview, metadata={'source': version.source, 'is_deleted': version.is_deleted, **version.metadata})
            snapshots.append(snapshot)
        snapshots.sort(key=lambda x: x.timestamp)
        identity_changes = self._detect_identity_changes(snapshots)
        temporal_gaps = self._detect_temporal_gaps(snapshots)
        confidence_score = self._calculate_timeline_confidence(snapshots, temporal_gaps)
        return EntityTimeline(entity_id=identifier, entity_type=id_type, snapshots=snapshots, identity_changes=identity_changes, temporal_gaps=temporal_gaps, confidence_score=confidence_score, metadata={'total_versions': len(versions), 'date_range': (snapshots[0].timestamp.isoformat() if snapshots else None, snapshots[-1].timestamp.isoformat() if snapshots else None)})

    def detect_temporal_anomalies(self, timeline: EntityTimeline) -> list[TemporalAnomaly]:
        """
        Detect temporal anomalies in a timeline.

        Args:
            timeline: EntityTimeline to analyze

        Returns:
            List of detected anomalies
        """
        logger.info(f'Detecting anomalies for: {timeline.entity_id}')
        anomalies = []
        if not timeline.snapshots:
            return anomalies

        # ISSUE-026 FIX #2: Run all 5 independent detectors sequentially.
        # All detectors are pure functions on the same timeline — no shared state.
        # Removed ThreadPoolExecutor: overhead for 5 small tasks outweighs parallelism gain.
        for _detector_name, _detector_fn in [
            ('disappearance', self._detect_disappearances),
            ('content_wipe', self._detect_content_wipes),
            ('activity_gap', self._detect_activity_gaps),
            ('sudden_change', self._detect_sudden_changes),
            ('frequency_shift', self._detect_frequency_shifts),
        ]:
            try:
                anomalies.extend(_detector_fn(timeline))
            except Exception as e:
                logger.warning(f'Detector {_detector_name} failed: {e}')
        self._anomalies_detected += len(anomalies)
        anomalies.sort(key=lambda x: x.severity, reverse=True)
        logger.info(f'Detected {len(anomalies)} anomalies')
        return anomalies

    async def cross_temporal_correlation(self, entity_a: str, entity_b: str, id_type: str='url') -> TemporalCorrelation:
        """
        Find correlations between two entities across time.

        Args:
            entity_a: First entity identifier
            entity_b: Second entity identifier
            id_type: Type of identifiers

        Returns:
            TemporalCorrelation with correlation analysis
        """
        logger.info(f'Analyzing cross-temporal correlation: {entity_a} vs {entity_b}')

        # ISSUE-026 FIX #1: Parallel reconstruction — both timelines are independent.
        # Run simultaneously instead of sequentially to halve wall-clock time.
        # safe_gather_ok drops failed coroutines, so we validate we got both results.
        results = await safe_gather_ok(
            self.reconstruct_version_history(entity_a, id_type),
            self.reconstruct_version_history(entity_b, id_type),
            label='cross_temporal_correlation:reconstruct',
        )

        if len(results) < 2:
            # At least one reconstruction failed — fall back to empty correlation.
            # Log warning but don't crash; caller gets a zero-score result.
            logger.warning(
                f'cross_temporal_correlation: only {len(results)}/2 timelines succeeded '
                f'for {entity_a} vs {entity_b}'
            )
            return TemporalCorrelation(
                entity_a=entity_a,
                entity_b=entity_b,
                correlation_score=0.0,
                overlapping_periods=[],
                shared_attributes={},
                temporal_proximity=[],
            )

        timeline_a, timeline_b = results[0], results[1]
        overlapping_periods = self._find_overlapping_periods(timeline_a, timeline_b)
        correlation_score = self._calculate_correlation_score(timeline_a, timeline_b, overlapping_periods)
        shared_attributes = self._find_shared_attributes(timeline_a, timeline_b)
        temporal_proximity = self._find_temporal_proximity(timeline_a, timeline_b)
        return TemporalCorrelation(entity_a=entity_a, entity_b=entity_b, correlation_score=correlation_score, overlapping_periods=overlapping_periods, shared_attributes=shared_attributes, temporal_proximity=temporal_proximity)

    def temporal_entity_resolution(self, snapshots: list[ArchivedVersion], resolution_threshold: float=0.8) -> ResolvedEntity:
        """
        Resolve entity identity across multiple snapshots.

        Args:
            snapshots: List of archived versions
            resolution_threshold: Minimum similarity for identity matching

        Returns:
            ResolvedEntity with canonical identity
        """
        logger.info(f'Performing temporal entity resolution on {len(snapshots)} snapshots')
        if not snapshots:
            return ResolvedEntity(canonical_id='', all_identifiers=set(), timeline=EntityTimeline(entity_id='', entity_type='unknown', snapshots=[], identity_changes=[], temporal_gaps=[], confidence_score=0.0), resolution_confidence=0.0, resolution_method='none')
        groups = self._group_similar_snapshots(snapshots, resolution_threshold)
        canonical_group = max(groups, key=len)
        canonical_id = canonical_group[0].url
        all_identifiers = {snap.url for snap in snapshots}
        all_identifiers.update({snap.metadata.get('redirect_url', '') for snap in snapshots})
        all_identifiers.discard('')
        entity_snapshots = [EntitySnapshot(timestamp=snap.timestamp, identifier=snap.url, content_hash=snap.content_hash, content_preview=snap.content[:200] if snap.content else '', metadata=snap.metadata) for snap in canonical_group]
        timeline = EntityTimeline(entity_id=canonical_id, entity_type='resolved', snapshots=entity_snapshots, identity_changes=[], temporal_gaps=self._detect_temporal_gaps(entity_snapshots), confidence_score=len(canonical_group) / len(snapshots))
        resolution_confidence = len(canonical_group) / len(snapshots)
        return ResolvedEntity(canonical_id=canonical_id, all_identifiers=all_identifiers, timeline=timeline, resolution_confidence=resolution_confidence, resolution_method='similarity_clustering')

    async def deep_historical_search(self, query: str, time_range: tuple[datetime, datetime], sources: list[str] | None=None) -> list[ArchivedVersion]:
        """
        Perform deep historical search across archives.

        Args:
            query: Search query
            time_range: Tuple of (start_date, end_date)
            sources: List of sources to search

        Returns:
            List of archived versions matching query

        ISSUE-003: Parallelized source search (was sequential for-loop).
        """
        logger.info(f"Deep historical search: '{query}' from {time_range[0]} to {time_range[1]}")
        if sources is None:
            sources = ['wayback', 'common_crawl']

        async def _search_source(source: str) -> tuple[str, list[ArchivedVersion]]:
            """Search a single source, return (source_name, results)."""
            try:
                if source == 'wayback':
                    results = await self._search_wayback_by_query(query, time_range)
                elif source == 'common_crawl':
                    results = await self._search_common_crawl(query, time_range)
                else:
                    results = []
                return (source, results)
            except Exception as e:
                logger.warning(f'Search on {source} failed: {e}')
                return (source, [])

        # ISSUE-003: Parallelize across sources with bounded concurrency
        results_gathered = await safe_gather_ok(*[_search_source(s) for s in sources], label='deep_historical_search')
        all_results = []
        for source, results in results_gathered:
            all_results.extend(results)
        filtered_results = [result for result in all_results if time_range[0] <= result.timestamp <= time_range[1]]
        filtered_results.sort(key=lambda x: x.timestamp, reverse=True)
        logger.info(f'Deep search found {len(filtered_results)} results')
        return filtered_results

    async def _check_snapshot_available(self, wayback_url: str) -> bool:
        """
        Check if a Wayback snapshot is available via HEAD request (Fix 1).

        Args:
            wayback_url: URL to check

        Returns:
            True if snapshot is available (status 200)
        """
        if not self._session:
            return False
        try:
            async with self._session.head(wayback_url, follow_redirects=True) as resp:
                return resp.status == 200
        except Exception:
            return False

    async def _recover_from_wayback(self, url: str, from_date: datetime | None, to_date: datetime | None, include_content: bool) -> list[ArchivedVersion]:
        """Recover content from Wayback Machine."""
        if not self._session:
            raise RuntimeError('Session not initialized')
        versions = []
        target_domain = urlparse(url).netloc
        await self._rate_limiter.acquire(domain=target_domain)
        params = {'url': url, 'output': 'json', 'collapse': 'digest', 'fl': 'timestamp,original,mimetype,statuscode,digest,length'}
        if from_date:
            params['from'] = from_date.strftime('%Y%m%d')
        if to_date:
            params['to'] = to_date.strftime('%Y%m%d')
        try:
            async with self._session.get(self.WAYBACK_CDX_URL, params=params) as resp:
                if resp.status != 200:
                    return versions
                data = await resp.text()
                lines = data.strip().split('\n')
                if len(lines) <= 1:
                    return versions
                cdx_entries: list[tuple[datetime, str, dict[str, str]]] = []
                for line in lines[1:]:
                    try:
                        parts = line.split(' ')
                        if len(parts) >= 6:
                            timestamp_str = parts[0]
                            timestamp = datetime.strptime(timestamp_str, '%Y%m%d%H%M%S')
                            wayback_url = self.WAYBACK_RAW_URL.format(timestamp=timestamp_str, url=parts[1])
                            snapshot_key = f'{wayback_url}'
                            if snapshot_key in self._fetched_snapshots:
                                continue
                            cdx_entries.append((timestamp, wayback_url, {'status_code': parts[2], 'mimetype': parts[2] if len(parts) > 2 else '', 'length': parts[5] if len(parts) > 5 else '0', 'content_hash': parts[3] if len(parts) > 3 else ''}))
                    except Exception as e:
                        logger.debug(f'Failed to parse Wayback line: {e}')
                        continue
                if not cdx_entries:
                    return versions
                if include_content:
                    from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
                    semaphore = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)

                    async def fetch_snapshot(entry: tuple[datetime, str, dict[str, str]]) -> ArchivedVersion | None:
                        timestamp, wayback_url, meta = entry
                        async with semaphore:
                            snapshot_domain = urlparse(wayback_url).netloc
                            await self._rate_limiter.acquire(domain=snapshot_domain)
                            if not await self._check_snapshot_available(wayback_url):
                                logger.debug(f'Wayback snapshot unavailable: {wayback_url}')
                                return None
                            content = await self._fetch_wayback_content(wayback_url)
                            if content is None:
                                return None
                            return ArchivedVersion(url=wayback_url, timestamp=timestamp, content_hash=meta['content_hash'], content=content, source='wayback', is_deleted=False, metadata={'status_code': meta['status_code'], 'mimetype': meta['mimetype'], 'length': meta['length']})
                    tasks = [fetch_snapshot(entry) for entry in cdx_entries]
                    results = await safe_gather_ok(*tasks, label='temporal_archaeologist:_recover_from_wayback')
                    for result in results:
                        if result is not None and isinstance(result, ArchivedVersion):
                            versions.append(result)
                else:
                    for timestamp, wayback_url, meta in cdx_entries:
                        versions.append(ArchivedVersion(url=wayback_url, timestamp=timestamp, content_hash=meta['content_hash'], content=None, source='wayback', is_deleted=False, metadata={'status_code': meta['status_code'], 'mimetype': meta['mimetype'], 'length': meta['length']}))
        except Exception as e:
            logger.warning(f'Wayback recovery failed: {e}')
        return versions

    async def _fetch_wayback_content(self, wayback_url: str) -> str | None:
        """Fetch content from Wayback Machine URL."""
        if not self._session:
            return None
        try:
            async with self._session.get(wayback_url) as resp:
                if resp.status == 200:
                    content = await resp.text()
                    if len(content) <= self.max_content_size:
                        self._fetched_snapshots.add(wayback_url)
                        return content
        except Exception as e:
            logger.debug(f'Failed to fetch Wayback content: {e}')
        return None

    async def _recover_from_archive_today(self, url: str, include_content: bool) -> list[ArchivedVersion]:
        """Recover content from Archive.today."""
        if not self._session:
            raise RuntimeError('Session not initialized')
        versions = []
        try:
            search_url = f'{self.ARCHIVE_TODAY_URL}/search/?q={quote(url)}'
            async with self._session.get(search_url) as resp:
                if resp.status != 200:
                    return versions
                html = await resp.text()
                pattern = 'href="(https://archive\\.today/[^"]+)"[^>]*>([^<]+)'
                matches = re.findall(pattern, html)
                for archive_url, title in matches[:10]:
                    content = None
                    if include_content:
                        content = await self._fetch_archive_today_content(archive_url)
                    version = ArchivedVersion(url=archive_url, timestamp=datetime.now(UTC), content_hash='', content=content, source='archive_today', is_deleted=False, metadata={'title': title})
                    versions.append(version)
        except Exception as e:
            logger.warning(f'Archive.today recovery failed: {e}')
        return versions

    async def _fetch_archive_today_content(self, archive_url: str) -> str | None:
        """Fetch content from Archive.today."""
        if not self._session:
            return None
        try:
            async with self._session.get(archive_url) as resp:
                if resp.status == 200:
                    content = await resp.text()
                    if len(content) <= self.max_content_size:
                        return content
        except Exception as e:
            logger.debug(f'Failed to fetch Archive.today content: {e}')
        return None

    async def _recover_from_google_cache(self, url: str, include_content: bool) -> list[ArchivedVersion]:
        """Recover content from Google Cache."""
        if not self._session:
            raise RuntimeError('Session not initialized')
        versions = []
        try:
            cache_url = f'{self.GOOGLE_CACHE_URL}{quote(url)}'
            async with self._session.get(cache_url) as resp:
                if resp.status == 200:
                    content = None
                    if include_content:
                        content = await resp.text()
                        if len(content) > self.max_content_size:
                            content = None
                    version = ArchivedVersion(url=cache_url, timestamp=datetime.now(UTC), content_hash=hashlib.sha256((content or '').encode()).hexdigest()[:16], content=content, source='google_cache', is_deleted=False, metadata={})
                    versions.append(version)
        except Exception as e:
            logger.warning(f'Google Cache recovery failed: {e}')
        return versions

    async def _recover_from_bing_cache(self, url: str, include_content: bool) -> list[ArchivedVersion]:
        """Recover content from Bing Cache via jina.ai."""
        if not self._session:
            raise RuntimeError('Session not initialized')
        versions = []
        try:
            cache_url = f'{self.BING_CACHE_URL}{quote(url)}'
            async with self._session.get(cache_url) as resp:
                if resp.status == 200:
                    content = None
                    if include_content:
                        content = await resp.text()
                        if len(content) > self.max_content_size:
                            content = None
                    version = ArchivedVersion(url=cache_url, timestamp=datetime.now(UTC), content_hash=hashlib.sha256((content or '').encode()).hexdigest()[:16], content=content, source='bing_cache', is_deleted=False, metadata={})
                    versions.append(version)
        except Exception as e:
            logger.warning(f'Bing Cache recovery failed: {e}')
        return versions

    async def _recover_from_common_crawl(self, url: str, include_content: bool) -> list[ArchivedVersion]:
        """Recover content from Common Crawl index."""
        logger.debug('Common Crawl recovery not fully implemented')
        return []

    async def _recover_from_git_history(self, repo_path: str) -> list[ArchivedVersion]:
        """Recover content from Git history."""
        versions = []
        try:
            import subprocess
            result = subprocess.run(['git', '-C', repo_path, 'log', '--format=%H|%aI|%s', '--all'], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n')[:50]:
                    parts = line.split('|', 2)
                    if len(parts) >= 3:
                        commit_hash, timestamp_str, message = parts
                        try:
                            timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                            version = ArchivedVersion(url=f'git:{commit_hash}', timestamp=timestamp, content_hash=commit_hash[:16], content=None, source='git_history', is_deleted=False, metadata={'message': message, 'commit': commit_hash})
                            versions.append(version)
                        except Exception:
                            continue
        except Exception as e:
            logger.warning(f'Git history recovery failed: {e}')
        return versions

    async def _search_by_entity(self, identifier: str, id_type: str) -> list[ArchivedVersion]:
        """Search for archived versions by entity identifier."""
        return []

    async def _search_wayback_by_query(self, query: str, time_range: tuple[datetime, datetime]) -> list[ArchivedVersion]:
        """Search Wayback by query string."""
        return []

    async def _search_common_crawl(self, query: str, time_range: tuple[datetime, datetime]) -> list[ArchivedVersion]:
        """Search Common Crawl index."""
        return []

    def _detect_disappearances(self, timeline: EntityTimeline) -> list[TemporalAnomaly]:
        """Detect content disappearances."""
        anomalies = []
        if not timeline.snapshots:
            return anomalies
        last_snapshot = timeline.snapshots[-1]
        days_since_last = (datetime.now(UTC) - last_snapshot.timestamp).days
        if days_since_last > 365:
            anomalies.append(TemporalAnomaly(type=AnomalyType.DISAPPEARANCE.value, description=f'Entity not seen for {days_since_last} days', severity=min(1.0, days_since_last / 1000), timestamp=last_snapshot.timestamp, evidence=[f'Last seen: {last_snapshot.timestamp.isoformat()}'], affected_snapshots=[last_snapshot.identifier]))
        return anomalies

    def _detect_content_wipes(self, timeline: EntityTimeline) -> list[TemporalAnomaly]:
        """Detect sudden content wipes."""
        anomalies = []
        if len(timeline.snapshots) < 2:
            return anomalies
        for i in range(1, len(timeline.snapshots)):
            prev = timeline.snapshots[i - 1]
            curr = timeline.snapshots[i]
            if prev.content_hash and curr.content_hash:
                similarity = self._content_similarity(prev.content_preview, curr.content_preview)
                if similarity < 0.3:
                    anomalies.append(TemporalAnomaly(type=AnomalyType.CONTENT_WIPE.value, description='Sudden content change detected', severity=1.0 - similarity, timestamp=curr.timestamp, evidence=[f'Similarity: {similarity:.2%}', f'Previous hash: {prev.content_hash}', f'Current hash: {curr.content_hash}'], affected_snapshots=[prev.identifier, curr.identifier]))
        return anomalies

    def _detect_activity_gaps(self, timeline: EntityTimeline) -> list[TemporalAnomaly]:
        """Detect unusual gaps in activity."""
        anomalies = []
        if len(timeline.snapshots) < 3:
            return anomalies
        gaps = []
        for i in range(1, len(timeline.snapshots)):
            gap = (timeline.snapshots[i].timestamp - timeline.snapshots[i - 1].timestamp).days
            gaps.append(gap)
        if not gaps:
            return anomalies
        avg_gap = np.mean(gaps)
        std_gap = np.std(gaps)
        for i, gap in enumerate(gaps):
            if gap > avg_gap + 3 * std_gap:
                anomalies.append(TemporalAnomaly(type=AnomalyType.ACTIVITY_GAP.value, description=f'Unusual activity gap of {gap} days', severity=min(1.0, gap / (avg_gap * 5)) if avg_gap > 0 else 0.5, timestamp=timeline.snapshots[i].timestamp, evidence=[f'Gap duration: {gap} days', f'Average gap: {avg_gap:.1f} days'], affected_snapshots=[timeline.snapshots[i].identifier]))
        return anomalies

    def _detect_sudden_changes(self, timeline: EntityTimeline) -> list[TemporalAnomaly]:
        """Detect sudden changes in metadata or content."""
        anomalies = []
        return anomalies

    def _detect_frequency_shifts(self, timeline: EntityTimeline) -> list[TemporalAnomaly]:
        """Detect shifts in update frequency."""
        anomalies = []
        if len(timeline.snapshots) < 6:
            return anomalies
        mid = len(timeline.snapshots) // 2
        first_half_gaps = []
        for i in range(1, mid):
            gap = (timeline.snapshots[i].timestamp - timeline.snapshots[i - 1].timestamp).days
            first_half_gaps.append(gap)
        second_half_gaps = []
        for i in range(mid + 1, len(timeline.snapshots)):
            gap = (timeline.snapshots[i].timestamp - timeline.snapshots[i - 1].timestamp).days
            second_half_gaps.append(gap)
        if not first_half_gaps or not second_half_gaps:
            return anomalies
        first_freq = np.mean(first_half_gaps)
        second_freq = np.mean(second_half_gaps)
        if first_freq > 0 and second_freq > 0:
            ratio = max(second_freq, first_freq) / min(second_freq, first_freq)
            if ratio > 3:
                anomalies.append(TemporalAnomaly(type=AnomalyType.FREQUENCY_SHIFT.value, description=f'Update frequency shifted by factor of {ratio:.1f}', severity=min(1.0, (ratio - 1) / 10), timestamp=timeline.snapshots[mid].timestamp, evidence=[f'First half avg gap: {first_freq:.1f} days', f'Second half avg gap: {second_freq:.1f} days'], affected_snapshots=[timeline.snapshots[mid].identifier]))
        return anomalies

    def _detect_identity_changes(self, snapshots: list[EntitySnapshot]) -> list[IdentityChange]:
        """Detect identity changes in snapshots."""
        changes = []
        for i in range(1, len(snapshots)):
            prev = snapshots[i - 1]
            curr = snapshots[i]
            if prev.identifier != curr.identifier:
                changes.append(IdentityChange(from_identifier=prev.identifier, to_identifier=curr.identifier, timestamp=curr.timestamp, change_type='url_redirect', confidence=0.8, evidence=['Identifier changed between snapshots']))
        return changes

    def _detect_temporal_gaps(self, snapshots: list[EntitySnapshot]) -> list[TemporalGap]:
        """Detect temporal gaps in snapshots."""
        gaps = []
        if len(snapshots) < 2:
            return gaps
        all_gaps = []
        for i in range(1, len(snapshots)):
            gap_days = (snapshots[i].timestamp - snapshots[i - 1].timestamp).days
            all_gaps.append(gap_days)
        if not all_gaps:
            return gaps
        median_gap = np.median(all_gaps)
        for i in range(1, len(snapshots)):
            gap_days = (snapshots[i].timestamp - snapshots[i - 1].timestamp).days
            if gap_days > median_gap * 3:
                gaps.append(TemporalGap(start_time=snapshots[i - 1].timestamp, end_time=snapshots[i].timestamp, duration_days=gap_days, gap_type='extended_silence', severity=min(1.0, gap_days / (median_gap * 10)) if median_gap > 0 else 0.5))
        return gaps

    def _calculate_timeline_confidence(self, snapshots: list[EntitySnapshot], gaps: list[TemporalGap]) -> float:
        """Calculate confidence score for timeline."""
        if not snapshots:
            return 0.0
        base_confidence = min(1.0, len(snapshots) / 10)
        gap_penalty = len(gaps) * 0.1
        return max(0.0, base_confidence - gap_penalty)

    def _content_similarity(self, content_a: str, content_b: str) -> float:
        """Calculate similarity between two content strings."""
        if not content_a or not content_b:
            return 0.0
        return SequenceMatcher(None, content_a, content_b).ratio()

    def _find_overlapping_periods(self, timeline_a: EntityTimeline, timeline_b: EntityTimeline) -> list[tuple[datetime, datetime]]:
        """Find overlapping time periods between two timelines."""
        overlaps = []
        if not timeline_a.snapshots or not timeline_b.snapshots:
            return overlaps
        a_start = timeline_a.first_seen
        a_end = timeline_a.last_seen
        b_start = timeline_b.first_seen
        b_end = timeline_b.last_seen
        if a_start and a_end and b_start and b_end:
            overlap_start = max(a_start, b_start)
            overlap_end = min(a_end, b_end)
            if overlap_start < overlap_end:
                overlaps.append((overlap_start, overlap_end))
        return overlaps

    def _calculate_correlation_score(self, timeline_a: EntityTimeline, timeline_b: EntityTimeline, overlapping_periods: list[tuple[datetime, datetime]]) -> float:
        """Calculate correlation score between two timelines."""
        if not overlapping_periods:
            return 0.0
        total_overlap = sum(((end - start).days for start, end in overlapping_periods))
        a_duration = timeline_a.lifespan_days
        b_duration = timeline_b.lifespan_days
        if a_duration == 0 or b_duration == 0:
            return 0.0
        return total_overlap / (a_duration + b_duration - total_overlap)

    def _find_shared_attributes(self, timeline_a: EntityTimeline, timeline_b: EntityTimeline) -> dict[str, Any]:
        """Find shared attributes between two timelines."""
        shared = {}
        for snap_a in timeline_a.snapshots:
            for snap_b in timeline_b.snapshots:
                for key in set(snap_a.metadata.keys()) & set(snap_b.metadata.keys()):
                    if snap_a.metadata[key] == snap_b.metadata[key]:
                        if key not in shared:
                            shared[key] = []
                        shared[key].append(snap_a.metadata[key])
        return shared

    def _find_temporal_proximity(self, timeline_a: EntityTimeline, timeline_b: EntityTimeline) -> list[dict[str, Any]]:
        """Find events that are temporally close."""
        proximity_events = []
        threshold_days = 7
        for snap_a in timeline_a.snapshots:
            for snap_b in timeline_b.snapshots:
                diff = abs((snap_a.timestamp - snap_b.timestamp).days)
                if diff <= threshold_days:
                    proximity_events.append({'entity_a_snapshot': snap_a.identifier, 'entity_b_snapshot': snap_b.identifier, 'time_difference_days': diff, 'timestamp_a': snap_a.timestamp.isoformat(), 'timestamp_b': snap_b.timestamp.isoformat()})
        return proximity_events

    def _group_similar_snapshots(self, snapshots: list[ArchivedVersion], threshold: float) -> list[list[ArchivedVersion]]:
        """Group similar snapshots using clustering.

        ISSUE-026 FIX #3: Uses Rust rayon-parallel trigram Jaccard grouping
        (text_similarity::group_similar_texts) when available — ~10-50× faster
        than the serial Python SequenceMatcher O(n²) approach for large batches.
        Falls back to pure-Python implementation if Rust extension unavailable.
        """
        if not snapshots:
            return []
        # Fast path: use Rust parallel implementation.
        try:
            from hledac_rust_extensions import group_similar_texts
            texts = [s.content or '' for s in snapshots]
            group_indices = group_similar_texts(texts, float(threshold))
            # Convert index groups back to ArchivedVersion groups.
            return [[snapshots[idx] for idx in group] for group in group_indices]
        except Exception:
            # Slow path: pure-Python serial fallback.
            groups: list[list[ArchivedVersion]] = []
            for snapshot in snapshots:
                added = False
                for group in groups:
                    similarity = self._content_similarity(snapshot.content or '', group[0].content or '')
                    if similarity >= threshold:
                        group.append(snapshot)
                        added = True
                        break
                if not added:
                    groups.append([snapshot])
            return groups

    def get_statistics(self) -> dict[str, Any]:
        """Get archaeologist statistics."""
        return {'queries_made': self._queries_made, 'versions_recovered': self._versions_recovered, 'anomalies_detected': self._anomalies_detected, 'cache_size': len(self._cache) if self.cache_enabled else 0}

    def clear_cache(self) -> None:
        """Clear internal cache."""
        self._cache.clear()
        logger.info('Cache cleared')

async def recover_deleted_content(url: str, **kwargs) -> RecoveryResult:
    """Quick function to recover deleted content."""
    async with TemporalArchaeologist() as archaeologist:
        return await archaeologist.recover_deleted_content(url, **kwargs)

async def reconstruct_timeline(identifier: str, **kwargs) -> EntityTimeline:
    """Quick function to reconstruct timeline."""
    async with TemporalArchaeologist() as archaeologist:
        return await archaeologist.reconstruct_version_history(identifier, **kwargs)

async def detect_anomalies(timeline: EntityTimeline) -> list[TemporalAnomaly]:
    """Quick function to detect anomalies."""
    archaeologist = TemporalArchaeologist()
    return archaeologist.detect_temporal_anomalies(timeline)

def create_temporal_archaeologist(**kwargs) -> TemporalArchaeologist:
    """Factory function for TemporalArchaeologist."""
    return TemporalArchaeologist(**kwargs)