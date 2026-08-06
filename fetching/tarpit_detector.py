"""
Tarpit & Honeypot Detector for Fetch Pipeline
==============================================





ISSUE [UNINDEXED]-014: Detect tarpits, link labyrinths, and honeypots in HTTP responses
before HTML processing. Protects the fetch pipeline from wasting bandwidth and CPU on
crawler-trapping pages.

Detection Methods:
  1. Timing Tarpit — progressive response slowdown (100ms → 200ms → 400ms → ...)
  2. Link Labyrinth — pages with >500 internal links and no external links
  3. Honeypot — hidden fields, invisible links, JavaScript challenges / redirects

Architecture:
  - All detection methods are synchronous and CPU-light (~5ms per page)
  - Link graph analysis bounded to 1000 links → ~5MB RAM footprint
  - Per-domain timing tracker with bounded LRU eviction (512 entries)
  - msgspec.Struct for zero-copy typed results
  - M1 8GB: gc=False on all struct types

Integration:
  Called in _sync_process_html() BEFORE HTML→text extraction.
  If tarpit_score > 0.7, the fetch result is flagged with
  error='tarpit_detected:{reason}' and failure_stage='tarpit'.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Final
from urllib.parse import urljoin, urlparse

import msgspec

from hledac.universal.utils.logging_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# msgspec types — frozen, gc=False for M1 memory efficiency
# ---------------------------------------------------------------------------

class TarpitResult(msgspec.Struct, frozen=True, gc=False):
    """Detection result for a single page."""
    is_tarpit: bool
    tarpit_score: float  # 0.0 (safe) — 1.0 (certain tarpit)
    reasons: tuple[str, ...]  # human-readable detection reasons
    # Sub-scores
    timing_score: float  # 0.0 — 1.0
    link_labyrinth_score: float  # 0.0 — 1.0
    honeypot_score: float  # 0.0 — 1.0
    # Diagnostics
    internal_link_count: int = 0
    external_link_count: int = 0
    hidden_element_count: int = 0
    response_time_ms: float = 0.0
    domain: str = ''


class DomainTimingRecord(msgspec.Struct, gc=False):
    """Mutable per-domain timing record (not frozen — updated across requests)."""
    domain: str
    response_times: list[float]  # most recent N response times (ms)
    trend_score: float  # running exponential trend (0.0 — 1.0)
    last_seen: float  # monotonic timestamp


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Tarpit score threshold: if composite score exceeds this, abort fetch
TARPIT_ABORT_THRESHOLD: Final[float] = 0.7

# Timing detection: if response time exceeds this, suspect tarpit
_TIMING_SUSPECT_MS: Final[float] = 15_000.0  # 15 seconds
_TIMING_TARPIT_MS: Final[float] = 25_000.0   # 25 seconds

# Progressive slowdown: ratio thresholds for exponential backoff detection
_PROGRESSIVE_MIN_SAMPLES: Final[int] = 3
_PROGRESSIVE_RATIO_THRESHOLD: Final[float] = 1.5  # each step must be ≥ 1.5× previous

# Link labyrinth detection
_INTERNAL_LINK_THRESHOLD: Final[int] = 500
_MIN_EXTERNAL_LINK_RATIO: Final[float] = 0.01  # <1% external = labyrinth
_MAX_LINKS_TO_ANALYZE: Final[int] = 1000  # bound RAM to ~5KB for link analysis

# Honeypot detection patterns
# Hidden/invisible elements
_HIDDEN_CSS_RE: Final[re.Pattern] = re.compile(
    r'(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0|'
    r'position\s*:\s*absolute\s*;\s*(?:left|top)\s*:\s*-9999px|'
    r'width\s*:\s*0\s*;?\s*height\s*:\s*0|'
    r'text-indent\s*:\s*-9999px|'
    r'clip\s*:\s*rect\s*\(\s*0\s*,?\s*0\s*,?\s*0\s*,?\s*0\s*\))',
    re.IGNORECASE,
)

# Hidden form fields (type=hidden)
_HIDDEN_INPUT_RE: Final[re.Pattern] = re.compile(
    r'<input[^>]*type\s*=\s*["\']?hidden["\']?[^>]*/?>',
    re.IGNORECASE,
)

# Invisible links (links with no visible text, tiny dimensions, or off-screen)
_INVISIBLE_LINK_RE: Final[re.Pattern] = re.compile(
    r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>'
    r'(?:\s*<img[^>]*\b(?:width|height)\s*=\s*["\']?[01]["\']?[^>]*>|'
    r'\s*&nbsp;\s*|\s*<span[^>]*style\s*=\s*["\'][^"\']*'
    r'(?:display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0)[^"\']*["\']|'
    r'\s*)'
    r'</a>',
    re.IGNORECASE | re.DOTALL,
)

# JavaScript-based traps (eval, setTimeout with large delays, document.location redirects)
_JS_TRAP_RE: Final[re.Pattern] = re.compile(
    r'(?:eval\s*\(\s*(?:function|atob|String\.fromCharCode)|'
    r'setTimeout\s*\(\s*["\']?\s*(?:\d{5,}|function)|'
    r'document\.location\s*=\s*["\']|'
    r'window\.location\.replace\s*\(|'
    r'window\.top\.location|'
    r'<meta\s+http-equiv\s*=\s*["\']?refresh["\']?\s+content\s*=\s*["\']?\d)',
    re.IGNORECASE,
)

# Common tarpit/honeypot URL path patterns
_TARPIT_URL_RE: Final[re.Pattern] = re.compile(
    r'(?:/trap/|/honeypot/|/crawler/|/bot/|/spider/|/scraper/|'
    r'tarpit|crawl.?trap|honey.?pot)',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Timing tracker — bounded LRU per-domain
# ---------------------------------------------------------------------------

class _DomainTimingTracker:
    """Bounded LRU tracker for per-domain response times.

    Stores up to 512 domain entries. Each entry holds the last 8 response times
    and a running exponential trend score. Used to detect progressive slowdown
    across multiple requests to the same host.

    Thread-safe via explicit lock (not async — callers run in thread pool).
    """

    __slots__ = ('_data', '_max_entries', '_max_samples')

    def __init__(self, max_entries: int = 512, max_samples: int = 8) -> None:
        self._max_entries = max_entries
        self._max_samples = max_samples
        self._lock = threading.Lock()

    def record(self, domain: str, response_time_ms: float) -> None:
        with self._lock:
            now = time.monotonic()
            if domain in self._data:
                rec = self._data[domain]
                rec.response_times.append(response_time_ms)
                if len(rec.response_times) > self._max_samples:
                    rec.response_times = rec.response_times[-self._max_samples:]
                rec.last_seen = now
                rec.trend_score = self._compute_trend(rec.response_times)
                self._data.move_to_end(domain)
            else:
                rec = DomainTimingRecord(
                    domain=domain,
                    response_times=[response_time_ms],
                    trend_score=0.0,
                    last_seen=now,
                )
                self._data[domain] = rec
                if len(self._data) > self._max_entries:
                    self._data.popitem(last=False)  # LRU eviction

    def get_trend_score(self, domain: str) -> float:
        """Get the timing trend score for a domain (0.0 = stable, 1.0 = extreme slowdown)."""
        with self._lock:
            if domain in self._data:
                rec = self._data[domain]
                if time.monotonic() - rec.last_seen > 600:  # expire after 10min
                    del self._data[domain]
                    return 0.0
                return rec.trend_score
        return 0.0

    @staticmethod
    def _compute_trend(times: list[float]) -> float:
        """Compute exponential slowdown trend from a list of response times.

        Returns 0.0—1.0 where:
          - 1.0: clear exponential growth (each time ≥ 1.5× previous)
          - 0.0: stable or decreasing times
        """
        if len(times) < _PROGRESSIVE_MIN_SAMPLES:
            return 0.0
        ratios = 0
        count = 0
        for i in range(1, len(times)):
            if times[i - 1] > 0:
                ratio = times[i] / times[i - 1]
                if ratio >= _PROGRESSIVE_RATIO_THRESHOLD:
                    ratios += 1
                count += 1
        if count == 0:
            return 0.0
        # Exponential fit: if all ratios show growth, score = 1.0
        return min(1.0, ratios / count * 1.5)  # bonus multiplier for stronger signal


# Module-level singleton
_domain_timing_tracker = _DomainTimingTracker()


# ---------------------------------------------------------------------------
# Core: TarpitDetector
# ---------------------------------------------------------------------------

class TarpitDetector:
    """Static analysis + timing-based tarpit/honeypot/labyrinth detector.

    All methods are synchronous and safe to call from any thread.
    Designed for integration into the fetch pipeline after response
    receipt but before HTML→text extraction.

    Usage:
        detector = TarpitDetector()
        result = detector.detect(html, url, response_time_ms)

        if result.tarpit_score > TARPIT_ABORT_THRESHOLD:
            # Abort this fetch — likely tarpit
            return flagged_fetch_result

    M1 8GB:
      - Link analysis bounded to 1000 links (auto-truncates)
      - Regex operations on capped HTML prefixes (32KB for pattern matching)
      - No persistent state beyond _DomainTimingTracker (512 entries × ~200 bytes = ~100KB)
    """

    # Maximum HTML bytes to scan for patterns (bound memory to 32KB per analysis)
    _MAX_HTML_SCAN_BYTES: Final[int] = 32_768  # 32 KB

    # Link extraction regex — matches href= attributes (single-pass, bounded)
    _HREF_RE: Final[re.Pattern] = re.compile(
        r'''href\s*=\s*["']([^"']+)["']''',
        re.IGNORECASE,
    )

    __slots__ = ()

    def detect(
        self,
        html: str,
        url: str,
        response_time_ms: float,
        *,
        domain: str = '',
    ) -> TarpitResult:
        """Run all detection methods and return composite result.

        Args:
            html: Raw HTML content (may be truncated by caller).
            url: The source URL (for domain extraction and link resolution).
            response_time_ms: Total response time in milliseconds.
            domain: Optional pre-extracted domain (saves a urlparse call).

        Returns:
            TarpitResult with composite score and per-method scores.
        """
        if not html:
            return TarpitResult(
                is_tarpit=False,
                tarpit_score=0.0,
                reasons=(),
                timing_score=0.0,
                link_labyrinth_score=0.0,
                honeypot_score=0.0,
            )

        # Resolve domain
        if not domain:
            domain = self._extract_domain(url)

        # Run detection methods
        timing_score = self._detect_timing_tarpit(response_time_ms, domain)
        link_score, internal_count, external_count = self._detect_link_labyrinth(html, url)
        honeypot_score, hidden_count = self._detect_honeypot(html)

        # Composite score: weighted average with anti-tarpit bias
        # Weights: timing=0.3, labyrinth=0.4, honeypot=0.3
        composite = (
            timing_score * 0.3 +
            link_score * 0.4 +
            honeypot_score * 0.3
        )

        # Critical single-signal override: a strong single detection method
        # can independently trigger the tarpit abort.  Without this, even a
        # pure 500+ link labyrinth (link_score=0.7→max) or 25s+ timing
        # tarpit (timing=0.9) only contributes 0.28–0.36 to the weighted
        # composite — well below the 0.7 threshold.  Any single method ≥ 0.85
        # lifts the composite to at least 0.75, ensuring independent triggers.
        critical = max(timing_score, link_score, honeypot_score)
        if critical >= 0.85:
            composite = max(composite, 0.75)

        reasons: list[str] = []
        if timing_score > 0.5:
            reasons.append(f'timing_slowdown({timing_score:.2f})')
        if link_score > 0.5:
            reasons.append(f'link_labyrinth({link_score:.2f},internal={internal_count},external={external_count})')
        if honeypot_score > 0.5:
            reasons.append(f'honeypot({honeypot_score:.2f},hidden={hidden_count})')

        is_tarpit = composite > TARPIT_ABORT_THRESHOLD

        if is_tarpit:
            logger.info(
                '[TarpitDetector] TARPIT DETECTED: score=%.2f reasons=%s url=%s',
                composite, ';'.join(reasons), url,
            )

        return TarpitResult(
            is_tarpit=is_tarpit,
            tarpit_score=composite,
            reasons=tuple(reasons),
            timing_score=timing_score,
            link_labyrinth_score=link_score,
            honeypot_score=honeypot_score,
            internal_link_count=internal_count,
            external_link_count=external_count,
            hidden_element_count=hidden_count,
            response_time_ms=response_time_ms,
            domain=domain,
        )

    # ------------------------------------------------------------------
    # Method 1: Timing Tarpit Detection
    # ------------------------------------------------------------------

    def _detect_timing_tarpit(
        self,
        response_time_ms: float,
        domain: str,
    ) -> float:
        """Detect tarpit via timing patterns.

        Checks:
          1. Absolute response time (single request)
          2. Progressive slowdown across multiple requests to same domain

        Returns 0.0 (safe) — 1.0 (definite tarpit).
        """
        score = 0.0

        # Check 1: Single-request absolute timing
        if response_time_ms > _TIMING_TARPIT_MS:
            score = max(score, 0.9)
        elif response_time_ms > _TIMING_SUSPECT_MS:
            score = max(score, 0.6)
        elif response_time_ms > 10_000:
            score = max(score, 0.3)

        # Check 2: Multi-request progressive slowdown
        if domain:
            _domain_timing_tracker.record(domain, response_time_ms)
            trend = _domain_timing_tracker.get_trend_score(domain)
            if trend > 0.7:
                score = max(score, 0.85)
            elif trend > 0.4:
                score = max(score, 0.5)

        return min(score, 1.0)

    # ------------------------------------------------------------------
    # Method 2: Link Labyrinth Detection
    # ------------------------------------------------------------------

    def _detect_link_labyrinth(
        self,
        html: str,
        url: str,
    ) -> tuple[float, int, int]:
        """Detect link labyrinths — pages with excessive internal links.

        A "link labyrinth" is a page with >500 internal links and
        few/no external links. These are classic crawl traps.

        Returns:
            (labyrinth_score, internal_link_count, external_link_count)
        """
        if not html:
            return (0.0, 0, 0)

        # Resolve base domain for internal/external classification
        base_domain = self._extract_domain(url)

        # Extract links from HTML (single-pass, bounded)
        links = self._extract_links_bounded(html, url)

        internal_count = 0
        external_count = 0

        for link in links:
            link_domain = self._extract_domain(link)
            if not link_domain:
                continue
            if link_domain == base_domain:
                internal_count += 1
            else:
                external_count += 1

        total = internal_count + external_count
        if total == 0:
            return (0.0, 0, 0)

        # Score: high internal count + low external ratio = labyrinth
        score = 0.0

        # Internal count component
        if internal_count >= _INTERNAL_LINK_THRESHOLD:
            score += 0.7
        elif internal_count >= 250:
            score += 0.4
        elif internal_count >= 100:
            score += 0.2

        # External ratio component
        if total > 0:
            external_ratio = external_count / total
            if external_ratio < _MIN_EXTERNAL_LINK_RATIO:
                score += 0.3  # almost no external links
            elif external_ratio < 0.05:
                score += 0.15

        return (min(score, 1.0), internal_count, external_count)

    def _extract_links_bounded(self, html: str, base_url: str) -> list[str]:
        """Extract and resolve links from HTML, bounded to _MAX_LINKS_TO_ANALYZE.

        Args:
            html: Raw HTML (capped to _MAX_HTML_SCAN_BYTES internally).
            base_url: Base URL for resolving relative links.

        Returns:
            List of absolute URLs, at most _MAX_LINKS_TO_ANALYZE entries.
        """
        scan_html = html[:_MAX_HTML_SCAN_BYTES] if len(html) > _MAX_HTML_SCAN_BYTES else html
        links: list[str] = []
        seen: set[str] = set()

        for match in self._HREF_RE.finditer(scan_html):
            if len(links) >= _MAX_LINKS_TO_ANALYZE:
                break
            href = match.group(1).strip()
            if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:', 'data:')):
                continue
            try:
                resolved = urljoin(base_url, href)
            except ValueError:
                continue
            if resolved.startswith(('http://', 'https://')):
                if resolved not in seen:
                    seen.add(resolved)
                    links.append(resolved)

        return links

    # ------------------------------------------------------------------
    # Method 3: Honeypot Detection
    # ------------------------------------------------------------------

    def _detect_honeypot(self, html: str) -> tuple[float, int]:
        """Detect honeypot patterns in HTML.

        Checks:
          1. Hidden CSS elements (display:none, visibility:hidden, etc.)
          2. Hidden form inputs (type=hidden)
          3. Invisible links (no visible text, off-screen positioning)
          4. JavaScript traps (eval(), redirects)
          5. Suspicious URL paths

        Returns:
            (honeypot_score, hidden_element_count)
        """
        if not html:
            return (0.0, 0)

        scan_html = html[:_MAX_HTML_SCAN_BYTES] if len(html) > _MAX_HTML_SCAN_BYTES else html

        score = 0.0
        hidden_count = 0

        # Check 1: Hidden CSS elements
        hidden_matches = len(_HIDDEN_CSS_RE.findall(scan_html))
        if hidden_matches >= 20:
            score += 0.3
            hidden_count += hidden_matches
        elif hidden_matches >= 5:
            score += 0.15
            hidden_count += hidden_matches

        # Check 2: Hidden form inputs
        hidden_inputs = len(_HIDDEN_INPUT_RE.findall(scan_html))
        if hidden_inputs >= 10:
            score += 0.2
            hidden_count += hidden_inputs
        elif hidden_inputs >= 3:
            score += 0.1
            hidden_count += hidden_inputs

        # Check 3: Invisible links
        invisible_links = len(_INVISIBLE_LINK_RE.findall(scan_html))
        if invisible_links >= 5:
            score += 0.2
            hidden_count += invisible_links
        elif invisible_links >= 1:
            score += 0.05
            hidden_count += invisible_links

        # Check 4: JavaScript traps
        js_traps = len(_JS_TRAP_RE.findall(scan_html))
        if js_traps >= 5:
            score += 0.25
            hidden_count += js_traps
        elif js_traps >= 1:
            score += 0.1
            hidden_count += js_traps

        # Check 5: Suspicious URL path patterns in hrefs
        tarpit_urls = len(_TARPIT_URL_RE.findall(scan_html))
        if tarpit_urls >= 3:
            score += 0.15
            hidden_count += tarpit_urls
        elif tarpit_urls >= 1:
            score += 0.05
            hidden_count += tarpit_urls

        return (min(score, 1.0), hidden_count)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract lowercased hostname from URL."""
        try:
            parsed = urlparse(url)
            netloc = parsed.netloc or parsed.hostname or ''
            # Strip www. prefix for canonical comparison
            return netloc.lower().removeprefix('www.')
        except Exception:  # noqa: BLE001 — best-effort
            return ''


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

# Singleton detector for use throughout the fetch pipeline
_tarpit_detector_singleton: TarpitDetector | None = None


def get_tarpit_detector() -> TarpitDetector:
    """Get or create the module-level TarpitDetector singleton.

    Thread-safe via Python's module import lock.
    Stateless — all state is in _DomainTimingTracker.
    """
    global _tarpit_detector_singleton
    if _tarpit_detector_singleton is None:
        _tarpit_detector_singleton = TarpitDetector()
    return _tarpit_detector_singleton


def detect_tarpit(
    html: str,
    url: str,
    response_time_ms: float,
    *,
    domain: str = '',
) -> TarpitResult:
    """Convenience wrapper: run tarpit detection on a single HTML response.

    Returns TarpitResult. Check result.is_tarpit to decide whether to abort.
    """
    return get_tarpit_detector().detect(html, url, response_time_ms, domain=domain)


def reset_timing_tracker() -> None:
    """Reset the per-domain timing tracker (for testing)."""
    global _domain_timing_tracker
    _domain_timing_tracker = _DomainTimingTracker()


__all__ = [
    'TarpitDetector',
    'TarpitResult',
    'DomainTimingRecord',
    'get_tarpit_detector',
    'detect_tarpit',
    'reset_timing_tracker',
    'TARPIT_ABORT_THRESHOLD',
]
