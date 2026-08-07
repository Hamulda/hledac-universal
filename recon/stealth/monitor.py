"""
StreamingMonitor — Continuous monitoring system for web sources.

Rozděleno z původního stealth_crawler.py (ISSUE-028).


Features:
- RSS feed monitoring with selectolax (async-native)
- API polling (Twitter/X, Reddit, custom APIs)
- Scheduled URL crawling with change detection
- Content hash comparison for efficient change detection
- Entity extraction from changes
- Keyword matching with alert generation
- M1 8GB optimized: async loops, connection reuse, selective fetching
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, UTC
from typing import Any, cast

from ._models import (
    Alert,
    AlertRule,
    Change,
    ChangeType,
    MonitoredSource,
    SearchResult,
    Severity,
    SourceType,
    StreamEvent,
    TorProxyManager,
    _mark_surface_patched,
    _crawler_domain_allowed,
)

from hledac.universal.utils.async_helpers import parallel, safe_create_task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StreamingMonitor
# ---------------------------------------------------------------------------


class StreamingMonitor:
    """
    Continuous monitoring system for web sources.

    Features:
    - RSS feed monitoring with selectolax (async-native)
    - API polling (Twitter/X, Reddit, custom APIs)
    - Scheduled URL crawling with change detection
    - Content hash comparison for efficient change detection
    - Entity extraction from changes
    - Keyword matching with alert generation
    - M1 8GB optimized: async loops, connection reuse, selective fetching

    Example:
        >>> crawler = StealthCrawler()
        >>> monitor = StreamingMonitor(crawler)
        >>> source = MonitoredSource(
        ...     source_id="news_rss",
        ...     source_type="rss",
        ...     url="https://example.com/feed.xml",
        ...     check_interval_minutes=30,
        ...     keywords=["security", "breach"]
        ... )
        >>> await monitor.add_source(source)
        >>> await monitor.start_monitoring()
    """

    MAX_CONCURRENT_CHECKS = 3
    HEAD_CHECK_TIMEOUT = 5
    CONTENT_TIMEOUT = 30
    MEMORY_CLEANUP_INTERVAL = 50
    MAX_ALERT_HISTORY = 1000
    MAX_EVENT_HISTORY = 500

    __slots__ = tuple(
        (
            "_alert_rules",
            "_alerts",
            "_check_count",
            "_diff_match_patch_available",
            "_events",
            "_monitor_task",
            "_running",
            "_semaphore",
            "_session",
            "_sources",
            "_stats",
            "crawler",
        )
    )

    def __init__(self, crawler: Any):
        self.crawler = crawler
        self._sources: dict[str, MonitoredSource] = {}
        self._alert_rules: dict[str, AlertRule] = {}
        self._alerts: list[Alert] = []
        self._events: dict[str, list[StreamEvent]] = {}
        self._running = False
        self._monitor_task: asyncio.Task | None = None
        self._check_count = 0
        self._session: Any | None = None
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_CHECKS)
        self._stats = {
            "checks_performed": 0,
            "changes_detected": 0,
            "alerts_generated": 0,
            "errors": 0,
            "start_time": None,
        }
        self._diff_match_patch_available = False
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        """Check for optional dependencies."""
        try:
            import diff_match_patch

            self._diff_match_patch_available = True
            logger.info("✓ diff-match-patch available for diff generation")
        except ImportError:
            logger.warning("diff-match-patch not available, using simple diff")

    async def initialize(self) -> bool:
        """Initialize the monitor with HTTP session."""
        try:
            import httpx

            self._session = httpx.AsyncClient()
            logger.info("✅ StreamingMonitor initialized")
            return True
        except Exception as e:
            logger.error(f"❌ StreamingMonitor initialization failed: {e}")
            return False

    def _get_default_headers(self) -> dict[str, str]:
        """Get default HTTP headers for monitoring."""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    async def add_source(self, source: MonitoredSource) -> bool:
        """
        Add a source to monitor.

        Args:
            source: MonitoredSource configuration

        Returns:
            True if source was added successfully
        """
        try:
            if source.source_id in self._sources:
                logger.warning(
                    f"Source {source.source_id} already exists, updating"
                )
            source.session = self._session
            if source.source_id not in self._events:
                self._events[source.source_id] = []
            self._sources[source.source_id] = source
            logger.info(
                f"✅ Added source: {source.source_id} ({source.source_type})"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to add source {source.source_id}: {e}")
            return False

    async def remove_source(self, source_id: str) -> bool:
        """
        Remove a source from monitoring.

        Args:
            source_id: ID of source to remove

        Returns:
            True if source was removed
        """
        if source_id in self._sources:
            del self._sources[source_id]
            if source_id in self._events:
                del self._events[source_id]
            logger.info(f"✅ Removed source: {source_id}")
            return True
        return False

    async def start_monitoring(self) -> None:
        """Start the monitoring loop."""
        if self._running:
            logger.warning("Monitoring already running")
            return
        if not self._session:
            await self.initialize()
        self._running = True
        self._stats["start_time"] = datetime.now(UTC)
        self._monitor_task = safe_create_task(
            self._monitor_loop(), name="stealth_crawler:streaming_monitor"
        )
        logger.info("🚀 Streaming monitoring started")

    async def stop_monitoring(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:  # noqa: BLE001
                pass
            self._monitor_task = None
        self._session = None
        logger.info("🛑 Streaming monitoring stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop - M1 8GB optimized."""
        while self._running:
            try:
                now = datetime.now(UTC)
                sources_to_check = [
                    s
                    for s in self._sources.values()
                    if s.is_active
                    and (
                        s.last_check is None
                        or (now - s.last_check).total_seconds() / 60
                        >= s.check_interval_minutes
                    )
                ]
                if sources_to_check:
                    await parallel(
                        cast("list[Any]", [self._check_source_with_semaphore(source) for source in sources_to_check]),
                        policy="log",
                        ctx="streaming_monitor:check_sources",
                        logger_instance=logger,
                    )
                self._check_count += 1
                if self._check_count >= self.MEMORY_CLEANUP_INTERVAL:
                    await self._cleanup_memory()
                    self._check_count = 0
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                self._stats["errors"] += 1
                await asyncio.sleep(5)

    async def _check_source_with_semaphore(
        self, source: MonitoredSource
    ) -> None:
        """Check source with concurrency control."""
        async with self._semaphore:
            try:
                event = await self._check_source(source)
                if event:
                    await self._process_event(event)
            except Exception as e:
                logger.error(
                    f"Error checking source {source.source_id}: {e}"
                )
                self._stats["errors"] += 1

    async def _check_source(self, source: MonitoredSource) -> StreamEvent | None:
        """
        Check a single source for changes.

        M1 8GB Optimized:
        - Uses HEAD request first to check if content changed
        - Connection reuse via session
        - Minimal memory allocation
        """
        if not self._session:
            return None
        source.last_check = datetime.now(UTC)
        self._stats["checks_performed"] += 1
        try:
            if source.last_content_hash:
                head_changed = await self._head_check_changed(source)
                if not head_changed:
                    logger.debug(
                        f"Source {source.source_id} unchanged (HEAD check)"
                    )
                    return None
            if source.source_type == "rss":
                content = await self._fetch_rss(source)
            elif source.source_type == "api":
                content = await self._fetch_api(source)
            else:
                content = await self._fetch_url(source)
            if not content:
                return None
            content_hash = self._calculate_hash(content)
            if source.last_content_hash == content_hash:
                return None
            if source.last_content_hash is None:
                change_type = ChangeType.NEW
                changes = []
            else:
                change_type = ChangeType.UPDATED
                old_content = ""
                if (
                    source.source_id in self._events
                    and self._events[source.source_id]
                ):
                    old_content = self._events[source.source_id][-1].content
                changes = self._detect_changes(old_content, content)
            entities = self._extract_entities(content)
            matched_keywords = self._match_keywords(content, source.keywords)
            severity = self._determine_severity(
                change_type, matched_keywords, entities
            )
            source.last_content_hash = content_hash
            event = StreamEvent(
                event_id=self._generate_id(),
                source_id=source.source_id,
                timestamp=datetime.now(UTC),
                content=content[:10000],
                extracted_entities=entities,
                matched_keywords=matched_keywords,
                change_type=change_type.value,
                severity=severity.value,
                changes=changes[:10],
            )
            self._stats["changes_detected"] += 1
            logger.info(
                f"🔔 Change detected in {source.source_id}: {change_type.value}"
            )
            return event
        except Exception as e:
            logger.error(f"Error checking source {source.source_id}: {e}")
            self._stats["errors"] += 1
            return None

    async def _head_check_changed(self, source: MonitoredSource) -> bool:
        """
        Use HEAD request to check if content changed.

        M1 8GB Optimization: Avoids downloading full content if not needed.
        """
        allowed, reason = _crawler_domain_allowed(
            source.url, "StreamingMonitor._head_check_changed"
        )
        if not allowed:
            _mark_surface_patched("StreamingMonitor._head_check_changed")
            return False
        _mark_surface_patched("StreamingMonitor._head_check_changed")
        try:
            async with self._session.head(
                source.url,
                timeout=httpx.Timeout(self.HEAD_CHECK_TIMEOUT),
                allow_redirects=True,
            ) as response:
                etag = response.headers.get("ETag")
                if etag:
                    return etag != source.metadata.get("last_etag")
                last_modified = response.headers.get("Last-Modified")
                if last_modified:
                    return last_modified != source.metadata.get(
                        "last_modified"
                    )
                content_length = response.headers.get("Content-Length")
                if content_length:
                    return content_length != source.metadata.get(
                        "content_length"
                    )
                return True
        except Exception:
            return True

    async def _fetch_rss(self, source: MonitoredSource) -> str | None:
        """Fetch and parse RSS/Atom feed using selectolax (async-native)."""
        allowed, reason = _crawler_domain_allowed(
            source.url, "StreamingMonitor._fetch_rss"
        )
        if not allowed:
            _mark_surface_patched("StreamingMonitor._fetch_rss")
            return None
        _mark_surface_patched("StreamingMonitor._fetch_rss")
        try:
            from hledac.universal.parsing.feed_parser import parse_feed

            async with self._session.get(source.url) as response:
                content = await response.text()
            entries = parse_feed(content, feed_url=source.url)
            entries_text = []
            for entry in entries[:10]:
                entry_text = f"Title: {entry.title}\n"
                entry_text += f"Link: {entry.entry_url}\n"
                entry_text += f"Published: {entry.published_raw}\n"
                entry_text += f"Summary: {entry.description}\n"
                entries_text.append(entry_text)
            return "\n---\n".join(entries_text)
        except Exception as e:
            logger.error(f"RSS fetch failed for {source.source_id}: {e}")
            return None

    async def _fetch_api(self, source: MonitoredSource) -> str | None:
        """Fetch from API endpoint."""
        allowed, reason = _crawler_domain_allowed(
            source.url, "StreamingMonitor._fetch_api"
        )
        if not allowed:
            _mark_surface_patched("StreamingMonitor._fetch_api")
            return None
        _mark_surface_patched("StreamingMonitor._fetch_api")
        try:
            async with self._session.get(
                source.url, timeout=httpx.Timeout(self.CONTENT_TIMEOUT)
            ) as response:
                content = await response.text()
                return content
        except Exception as e:
            logger.error(f"API fetch failed for {source.source_id}: {e}")
            return None

    async def _fetch_url(self, source: MonitoredSource) -> str | None:
        """Fetch a general URL."""
        allowed, reason = _crawler_domain_allowed(
            source.url, "StreamingMonitor._fetch_url"
        )
        if not allowed:
            _mark_surface_patched("StreamingMonitor._fetch_url")
            return None
        _mark_surface_patched("StreamingMonitor._fetch_url")
        try:
            async with self._session.get(
                source.url,
                timeout=httpx.Timeout(self.CONTENT_TIMEOUT),
                headers=self._get_default_headers(),
            ) as response:
                content = await response.text()
                return content[:50000]  # Cap at 50KB
        except Exception as e:
            logger.error(f"URL fetch failed for {source.source_id}: {e}")
            return None

    async def _process_event(self, event: StreamEvent) -> None:
        """Process a stream event - generate alerts and store."""
        if event.source_id in self._events:
            self._events[event.source_id].append(event)
            # Trim history
            if len(self._events[event.source_id]) > self.MAX_EVENT_HISTORY:
                self._events[event.source_id] = self._events[event.source_id][
                    -self.MAX_EVENT_HISTORY :
                ]
        else:
            self._events[event.source_id] = [event]

        # Check alert rules
        for rule in self._alert_rules.values():
            if rule.enabled and event.source_id in rule.source_ids:
                if any(kw in event.content for kw in rule.keywords):
                    alert = Alert(
                        alert_id=self._generate_id(),
                        source_id=event.source_id,
                        timestamp=event.timestamp,
                        severity=rule.severity,
                        message=f"Alert triggered by rule '{rule.name}'",
                        event=event,
                    )
                    self._alerts.append(alert)
                    self._stats["alerts_generated"] += 1
                    if len(self._alerts) > self.MAX_ALERT_HISTORY:
                        self._alerts = self._alerts[-self.MAX_ALERT_HISTORY :]

    def _calculate_hash(self, content: str) -> str:
        """Calculate SHA256 hash of content."""
        import hashlib

        return hashlib.sha256(content.encode()).hexdigest()

    def _detect_changes(
        self, old_content: str, new_content: str
    ) -> list[Change]:
        """Detect changes between old and new content."""
        if self._diff_match_patch_available:
            try:
                import diff_match_patch

                dmp = diff_match_patch.diff_match_patch()
                diffs = dmp.diff_main(old_content, new_content)
                dmp.diff_cleanupSemantic(diffs)
                changes = []
                position = 0
                for op, text in diffs:
                    if op != 0:  # Not equal
                        change_type = (
                            ChangeType.UPDATED
                            if op == -1
                            else ChangeType.NEW
                        )
                        changes.append(
                            Change(
                                change_type=change_type,
                                position=position,
                                old_text=text if op == -1 else None,
                                new_text=text if op == 1 else None,
                            )
                        )
                    position += len(text)
                return changes
            except Exception:  # noqa: BLE001
                pass
        # Fallback: simple line-by-line comparison
        old_lines = old_content.split("\n")
        new_lines = new_content.split("\n")
        changes = []
        for i, (old, new) in enumerate(zip(old_lines, new_lines)):
            if old != new:
                changes.append(
                    Change(
                        change_type=ChangeType.UPDATED,
                        position=i,
                        old_text=old,
                        new_text=new,
                    )
                )
        return changes

    def _extract_entities(self, content: str) -> dict[str, list[str]]:
        """Extract entities from content using simple patterns."""
        import re

        entities: dict[str, list[str]] = {
            "urls": [],
            "emails": [],
            "ips": [],
            "domains": [],
        }
        # URLs
        url_pattern = re.compile(
            r"https?://[^\s<>\"]+", re.IGNORECASE
        )
        entities["urls"] = list(set(url_pattern.findall(content)))[:50]

        # Emails
        email_pattern = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
        entities["emails"] = list(set(email_pattern.findall(content)))[:20]

        # IPs
        ip_pattern = re.compile(
            r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b"
        )
        entities["ips"] = list(set(ip_pattern.findall(content)))[:20]

        # Domains
        domain_pattern = re.compile(
            r"\b(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b"
        )
        entities["domains"] = list(
            set(domain_pattern.findall(content))
        )[:20]

        return entities

    def _match_keywords(
        self, content: str, keywords: list[str]
    ) -> list[str]:
        """Match keywords in content."""
        content_lower = content.lower()
        matched = [kw for kw in keywords if kw.lower() in content_lower]
        return matched

    def _determine_severity(
        self,
        change_type: ChangeType,
        matched_keywords: list[str],
        entities: dict[str, list[str]],
    ) -> Severity:
        """Determine alert severity based on change characteristics."""
        if not matched_keywords:
            return Severity.INFO
        # High severity keywords
        high_keywords = [
            "breach",
            "leak",
            "stolen",
            "malware",
            "ransomware",
            "attack",
            "exploit",
            "vulnerability",
        ]
        if any(kw.lower() in high_keywords for kw in matched_keywords):
            return Severity.CRITICAL
        if len(matched_keywords) >= 3:
            return Severity.HIGH
        if len(entities.get("urls", [])) > 10:
            return Severity.MEDIUM
        return Severity.LOW

    async def _cleanup_memory(self) -> None:
        """Periodic memory cleanup for M1 8GB optimization."""
        # Trim events
        for source_id in list(self._events.keys()):
            if (
                source_id in self._sources
                and source_id not in self._sources[source_id].source_id
            ):
                del self._events[source_id]
        # Trim alerts
        if len(self._alerts) > self.MAX_ALERT_HISTORY:
            self._alerts = self._alerts[-self.MAX_ALERT_HISTORY :]
        logger.debug("StreamingMonitor memory cleanup completed")

    def _generate_id(self) -> str:
        """Generate a unique ID."""
        return str(uuid.uuid4())
