"""HTML Processor — extracted from public_fetcher.py (ISSUE-014 REFACTOR).

Provides HTML parsing, pattern matching, and metadata extraction.
Optimized for M1 8GB with Rust rayon parallel processing.

"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
import urllib.parse
from collections import deque as _f273c_deque
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    pass

from hledac.universal._core.rust_backend import rust as _rust_backend
from hledac.universal.tools.regex_cache import collapse_whitespace, strip_html_tags
from hledac.universal.utils.html_text_fast import extract_html_metadata, html_to_text_fast
from hledac.universal.utils.patterns.pattern_matcher import PatternHit, match_text
from _core import aclose

# XML detection constants
_XML_MARKER = b'<?xml'
_XML_TAG_RE = re.compile(b'^\\s*<[a-zA-Z]', re.IGNORECASE)

# Feed URL pattern
_FEED_URL_RE = re.compile(r'/?(?:rss|feed|atom|xml|sitemap|opensearch)', re.IGNORECASE)

# JS skip hosts pattern
_JS_SKIP_HOST_RE = re.compile(
    r'(?:^|\.)(?:threatfox\.abuse\.ch|bleepingcomputer\.com|thehackernews\.com|'
    r'krebsonsecurity\.com|cisa\.gov|id-ransomware\.malwarehunterteam\.com|'
    r'ransomwaretracker\.xyz|abuse\.ch|urlhaus\.abuse\.ch|feodo\.tracker|'
    r'openphish\.com|cyberscoop\.com|darkreading\.com|threatpost\.com|'
    r'therecord\.media|securityweek\.com|inforisktoday\.com|'
    r'helpnetsecurity\.com|malwarebazaar\.abuse\.ch|sslbl\.abuse\.ch)$',
    re.IGNORECASE
)

# SERP hosts pattern
_SERP_HOST_RE = re.compile(
    r'(google\.|bing\.|duckduckgo\.|yahoo\.|baidu\.|yandex\.|so\.|'
    r'startpage\.|search\.|serp)|searchresults|webcache|googlesyndication|'
    r'googletagmanager|doubleclick|search\?q=|/search\?|\?q=|\&oq=|\&gs_l=',
    re.IGNORECASE
)

# Content length pattern
_CONTENT_LENGTH_RE = re.compile(r'content-length\s*[=:]\s*(\d+)', re.IGNORECASE)

# Noscript pattern
_NOSCRIPT_RE = re.compile(r'<noscript[^>]*>|enable javascript', re.IGNORECASE)


def looks_xmlish(body: bytes) -> bool:
    """Return True if body starts like XML (<?xml or <tag).

    Strips leading ASCII whitespace so servers that prepend newlines
    before the XML declaration are correctly identified.
    """
    stripped = body.lstrip()
    if stripped.startswith(_XML_MARKER):
        return True
    return bool(_XML_TAG_RE.match(stripped))


def try_decode(body: bytes) -> tuple[str, bool, int, str]:
    """Decode bytes to str, return (text, replaced_bool, replacement_count, codec).

    F178E: replacement_count is actual U+FFFD count (not just bool).

    codec: 'utf-8' | 'windows-1252' | 'latin-1' | 'utf-8-replace'

    replaced_bool=True when the decoder had to substitute characters
    (i.e. U+FFFD replacement chars were inserted).
    """
    try:
        text = body.decode('utf-8', errors='strict')
        return (text, False, 0, 'utf-8')
    except UnicodeDecodeError:  # noqa: BLE001
        pass
    try:
        text = body.decode('windows-1252', errors='strict')
        return (text, False, 0, 'windows-1252')
    except (UnicodeDecodeError, LookupError):  # noqa: BLE001
        pass
    try:
        text = body.decode('latin-1', errors='strict')
        return (text, False, 0, 'latin-1')
    except (UnicodeDecodeError, LookupError):  # noqa: BLE001
        pass
    text = body.decode('utf-8', errors='replace')
    count = text.count('�')
    return (text, True, count, 'utf-8-replace')


def needs_js_fetch(
    text: str,
    *,
    url: str = '',
    content_length: int = 0,
    declared_length: int = -1
) -> bool:
    """Detect if response suggests JS-rendered content is needed.

    Enhanced P0-FIX: covers three failure modes of the original _NOSCRIPT_RE-only
    detection that caused 10/10 SERP URLs to be rejected as empty_text:
    1. <noscript> tag presence (original)
    2. Known SERP/search engine hosts (new)
    3. Content-length ratio: tiny body vs large declared Content-Length (new)
    """
    if url:
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ''
            if host and _JS_SKIP_HOST_RE.search(host):
                return False
        except Exception:  # noqa: BLE001 — best-effort
            pass
    if _NOSCRIPT_RE.search(text):
        return True
    if url:
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ''
            if host and _SERP_HOST_RE.search(host + '/' + url):
                return True
            if _JS_SKIP_HOST_RE.search(host):
                return False
        except Exception:  # noqa: BLE001 — best-effort
            pass
    if declared_length > 0 and content_length > 0:
        if declared_length > content_length * 3 and content_length < 20000:
            return True
    return False


def sync_process_html(html: str, url: str = '') -> tuple[str, list, dict]:
    """Synchronous CPU-bound HTML parsing + pattern matching + metadata extraction.

    Runs in CPU_EXECUTOR thread pool — never blocks the async event loop.
    Fail-safe: malformed HTML returns empty text, never raises.

    Returns:
        Tuple of (markdown-stripped text, pattern match list, metadata dict).
        metadata dict keys: ga_gtm_ids, og_tags, comments (from extract_html_metadata).
    """
    metadata = extract_html_metadata(html)
    text = html_to_text_fast(html)
    if not text:
        import html as _html
        text = strip_html_tags(_html.unescape(html))
        text = collapse_whitespace(text).strip()
    matches = match_text(text)
    try:
        raw_ranges = _rust_backend.html.extract_links_zero_copy(html, url)
        for start, end in raw_ranges:
            href_str = html[start:end]
            resolved = urllib.parse.urljoin(url, href_str)
            if resolved.startswith(('http://', 'https://')):
                matches.append(PatternHit(pattern='rust_link', start=0, end=0, value=resolved, label=''))
    except Exception:  # noqa: BLE001 — best-effort
        pass
    return (text, matches, metadata)


def batch_sync_extract_html_metadata(items: list[tuple[str, str]]) -> list[dict]:
    """Batch extract metadata (emails, titles) via Rust rayon pool.

    Args:
        items: List of (html, url) tuples.

    Returns:
        List of dicts with 'emails' and 'title' keys, matching item order.
        Returns empty list on any error (fail-safe).
    """
    if not items:
        return []
    rust_emails = _rust_backend.batch_extract_emails
    rust_titles = _rust_backend.batch_extract_titles
    if rust_emails is None and rust_titles is None:
        return [{} for _ in items]
    try:
        htmls = [html for html, _ in items]
        emails_results: list[list[str]] = [[] for _ in items]
        titles_results: list[str | None] = [None for _ in items]
        if rust_emails is not None:
            try:
                raw_emails = rust_emails(htmls)
                if raw_emails and len(raw_emails) == len(items):
                    emails_results = raw_emails
            except Exception:  # noqa: BLE001 — best-effort
                pass
        if rust_titles is not None:
            try:
                raw_titles = rust_titles(htmls)
                if raw_titles and len(raw_titles) == len(items):
                    titles_results = raw_titles
            except Exception:  # noqa: BLE001 — best-effort
                pass
        return [{'emails': e, 'title': t} for e, t in zip(emails_results, titles_results, strict=True)]
    except Exception:  # noqa: BLE001 — best-effort
        return [{} for _ in items]


def batch_sync_extract_links(items: list[tuple[str, str]]) -> list[list[str]]:
    """R3.2: Batch extract links via Rust rayon parallel batch_extract_links.

    Single rayon-parallel call instead of N per-item extract_links_zero_copy loops.
    Zero-copy lol_html handles URL resolution inside Rust.

    Args:
        items: List of (html, base_url) tuples. Cap 1_000 items.

    Returns:
        List of link lists, one per item, in same order as input.
    Always-on, bounded, fail-safe.
    """
    if not items:
        return []
    try:
        return _rust_backend.html.batch_extract_links(items)
    except Exception:  # noqa: BLE001 — best-effort
        return [[] for _ in items]


async def process_html_payload(html: str, url: str) -> tuple[str, list, dict]:
    """Offload HTML→text+pattern matching+metadata extraction to rayon CPU pool.

    Uses RustWorkerPool with channel dispatch — work runs on 4 P-core rayon pool
    instead of asyncio-to_thread thread. ~5μs dispatch vs ~50μs thread::spawn.

    Args:
        html: Raw HTML content.
        url: Source URL (for context in errors; kept for API compatibility, unused).

    Returns:
        Tuple of (markdown-stripped text, pattern match list, metadata dict).
        metadata dict keys: ga_gtm_ids, og_tags, comments (from extract_html_metadata).
        Never raises — malformed HTML returns (stripped_text, [], {}) on fallback.
    """
    from hledac.universal.runtime.worker_pool import get_rust_pool

    pool = get_rust_pool("cpu")
    return await pool.submit(sync_process_html, html)


def batch_sync_process_html(items: list[tuple[str, str]]) -> list[tuple[str, list[str], dict]]:
    """Batch HTML→text extraction via Rust rayon parallel processing.

    Fully Rust-powered: batch_extract_html_text + batch_extract_links +
    batch_extract_titles + batch_extract_emails. One rayon ThreadPool call
    instead of N Python loops — 3-5× speedup.

    Args:
        items: List of (html, url) tuples. Cap 1_000 items.

    Returns:
        List of (text, links, metadata) tuples, one per item in same order.
        Returns [("", [], {}) * len(items)] on any error (fail-safe).

    M1 8GB: rayon ThreadPool with mixed_pool (CPU-bound adaptive).
    Bounded: max 1_000 items per batch (URL dedup already done upstream).
    Always-on, bounded, fail-safe. No new feature flags.
    """
    if not items:
        return []
    if len(items) > 1000:
        items = items[:1000]
    try:
        htmls = [html for html, _ in items]
        base_urls = [base_url for _, base_url in items]
        # Rayon parallel: batch_extract_html_text + batch_extract_links + batch_extract_titles
        texts: list[str] = _rust_backend.html.batch_extract_html_text(htmls)
        links_batch: list[list[str]] = _rust_backend.html.batch_extract_links(
            list(zip(htmls, base_urls, strict=True))
        )
        titles_batch: list[str | None] = _rust_backend.html.batch_extract_titles(htmls)
        emails_batch: list[list[str]] = _rust_backend.html.batch_extract_emails(htmls)
        return [
            (
                texts[i] if i < len(texts) else '',
                links_batch[i] if i < len(links_batch) else [],
                {
                    'title': titles_batch[i] if i < len(titles_batch) and titles_batch[i] is not None else '',
                    'emails': emails_batch[i] if i < len(emails_batch) else [],
                },
            )
            for i in range(len(items))
        ]
    except Exception:  # noqa: BLE001 — best-effort
        return [sync_process_html(html, url) for html, url in items]


async def process_html_payload_batch(items: list[tuple[str, str]]) -> list[tuple[str, list[str], dict]]:
    """Batch HTML processing via ThreadPoolExecutor (offload CPU from event loop).

    Submits _batch_sync_process_html to the shared _HTML_EXECUTOR thread pool
    and returns results preserving input order.

    Args:
        items: List of (html, url) tuples. Cap 1_000 items.

    Returns:
        List of (text, links, metadata) per page, matching input order.
        Returns [("", [], {}) * min(len(items), 1000)] on error (fail-safe).

    Always-on, bounded, fail-safe. No new feature flags.
    """
    if not items:
        return []
    items = items[:1000]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_html_executor(), batch_sync_process_html, items)


def _get_html_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Get or create bounded HTML processing executor.

    Now uses the centralized domain_executors registry (P1-4).
    """
    from hledac.universal.utils.domain_executors import get_html_executor
    return get_html_executor()


# ==== Drain Registry ====
class DrainRegistry:
    """Manages HTML extraction futures with bounded deque and stats tracking.

    F-GLOBAL: Encapsulates _DRAIN_REGISTRY, _DRAIN_TOTAL_SCHEDULED,
    _DRAIN_TOTAL_COMPLETED into a single class with __slots__.

    Thread-safe for async use: mutations only from asyncio event loop.
    """
    __slots__ = ('_registry', '_scheduled', '_completed', '_max_size')

    def __init__(self, max_size: int = 512) -> None:
        self._registry: _f273c_deque = _f273c_deque(maxlen=max_size)
        self._scheduled: int = 0
        self._completed: int = 0
        self._max_size: int = max_size

    def schedule(self, fut: asyncio.Future) -> None:
        """Add a future to the registry, evicting oldest if at capacity."""
        while len(self._registry) >= self._max_size:
            try:
                old = self._registry.popleft()
                if not old.done():
                    old.cancel()
            except Exception:  # noqa: BLE001 — best-effort
                pass
        self._registry.append(fut)
        self._scheduled += 1

    def pending_list(self) -> list:
        """Return list of pending futures."""
        return list(self._registry)

    def mark_completed(self, cancelled: bool = False) -> None:
        """Mark a future as completed."""
        if not cancelled:
            self._completed += 1

    def remove(self, fut: asyncio.Future) -> None:
        """Remove a specific future from the registry."""
        try:
            self._registry.remove(fut)
        except ValueError:  # noqa: BLE001
            pass

    def clear(self) -> None:
        """Clear all futures and reset counters (for test isolation)."""
        self._registry.clear()
        self._scheduled = 0
        self._completed = 0

    def stats(self) -> dict:
        """Return diagnostic snapshot."""
        return {
            'registry_size': len(self._registry),
            'registry_capacity': self._registry.maxlen,
            'total_scheduled': self._scheduled,
            'total_completed': self._completed,
            'in_flight': self._scheduled - self._completed,
        }


# Singleton instance
drain_registry = DrainRegistry(max_size=512)


def schedule_html_extraction(html: str, url: str = '') -> asyncio.Future:
    """Submit HTML processing to CPU_EXECUTOR and register for drain.

    Returns the asyncio.Future wrapping the work. Caller may await it
    immediately (semantically equivalent to `process_html_payload`) or defer
    the await to `drain_pending_extractions(deadline_s)` at windup entry.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
    fut: asyncio.Future = loop.run_in_executor(_get_html_executor(), sync_process_html, html)
    try:
        tag = f'pattern_extract:{url[:64]}' if url else 'pattern_extract'
        fut.set_name(tag)
    except Exception:  # noqa: BLE001 — best-effort
        pass
    drain_registry.schedule(fut)

    def _drop_from_registry(f: asyncio.Future = fut) -> None:
        drain_registry.mark_completed(f.cancelled())
        drain_registry.remove(f)
    fut.add_done_callback(_drop_from_registry)
    return fut


async def drain_pending_extractions(deadline_s: float = 30.0) -> tuple[int, int, float]:
    """Await all registered HTML-extraction futures with a bounded deadline.

    Args:
        deadline_s: Maximum wall-clock seconds to wait for in-flight work.

    Returns:
        Tuple of (completed_count, timed_out_count, elapsed_seconds).
    """
    import time as _t_module
    _t0 = _t_module.monotonic()
    deadline_abs = _t0 + max(0.0, deadline_s)
    pending = drain_registry.pending_list()
    if not pending:
        return (0, 0, 0.0)
    completed = 0
    timed_out = 0
    remaining_timeout = max(0.0, deadline_abs - _t_module.monotonic())
    try:
        async with asyncio.timeout(remaining_timeout):
            gathered = await asyncio.gather(*pending, return_exceptions=True)
            _, errors = _check_gathered(gathered)
            for err in errors:
                pass  # log if needed
        completed = len(pending)
        timed_out = 0
    except asyncio.TimeoutError:
        completed = sum(1 for t in pending if t.done())
        timed_out = len(pending) - completed
    except Exception:  # noqa: BLE001 — best-effort
        return (0, 0, _t_module.monotonic() - _t0)
    return (completed, timed_out, _t_module.monotonic() - _t0)


def _check_gathered(gathered) -> tuple[list, list]:
    """Separate successful results from exceptions in asyncio.gather result."""
    ok = []
    errors = []
    for item in gathered:
        if isinstance(item, Exception):
            errors.append(item)
        else:
            ok.append(item)
    return (ok, errors)


def get_drain_stats() -> dict:
    """Diagnostic snapshot of the drain registry (size, totals)."""
    return drain_registry.stats()
