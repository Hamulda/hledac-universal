"""
intelligence/social_identity_miner.py — F204I: Social Identity Surface Miner
============================================================================




Deterministic social identity facet miner. Extracts usernames, display names,
profile URLs, bio links, PGP/email hints from accepted findings without
invasive scraping.

GHOST_INVARIANTS enforced:
- asyncio.gather always with return_exceptions=True
- gather(return_exceptions=True) results are filtered inline and CancelledError is re-raised
- asyncio.CancelledError re-raised
- No blocking calls in event loop
- Canonical write path: async_ingest_findings_batch()
- Model lifecycle: NOT USED
- RAM guard: skip if RSS > high_water
- Bounds: MAX_SOCIAL_PROFILES, MAX_LINKS_PER_PROFILE, MAX_SOCIAL_TEXT_BYTES
- Fail-soft: malformed HTML/payload silently skipped
"""
import asyncio
import json
import re
import time as _time
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from hledac.universal.utils.async_helpers import parallel_ok
from .confidence_policy import compute_confidence as _compute_confidence
_AC_MATCHER: Any = None

def _get_ac_matcher() -> Any:
    """Lazy-init Aho-Corasick matcher for platform URL patterns."""
    global _AC_MATCHER
    if _AC_MATCHER is None:
        try:
            from hledac.universal.core.rust_backend import rust as _rust_backend
            if _rust_backend.is_available and _rust_backend.aho is not None:
                patterns = [p[1].pattern for p in _PLATFORM_PATTERNS]
                _AC_MATCHER = _rust_backend.aho.AhoCorasickMatcher(patterns, labels=[])
            else:
                _AC_MATCHER = None
        except Exception:  # noqa: BLE001
            pass
    return _AC_MATCHER

def _extract_platform_username(text: str, platform_idx: int) -> str:
    """Extract username from text using the given platform's username regex."""
    _, _, username_re, _ = _PLATFORM_PATTERNS[platform_idx]
    m = username_re.search(text)
    if m and m.group(1):
        return m.group(1)
    return ''
if TYPE_CHECKING:
    from ..project_types import CanonicalFinding
MAX_SOCIAL_PROFILES: int = 200
MAX_LINKS_PER_PROFILE: int = 20
MAX_SOCIAL_TEXT_BYTES: int = 4096
SOCIAL_MIN_CONFIDENCE: float = 0.35
_SOCIAL_BLOOM_PATH_A: str = '~/.cache/hledac/social_identity_bloom_a.bin'
_SOCIAL_BLOOM_PATH_B: str = '~/.cache/hledac/social_identity_bloom_b.bin'
_SOCIAL_BLOOM_CAPACITY: int = 50000
_PLATFORM_PATTERNS: list[tuple[str, re.Pattern[str], re.Pattern[str], bool]] = [('github', re.compile('https?://(?:www\\.)?github\\.com/([^/]+)?'), re.compile('(?:github\\.com/|@)([a-zA-Z0-9][a-zA-Z0-9_-]{0,38})'), False), ('twitter', re.compile('https?://(?:www\\.)?(?:twitter\\.com|x\\.com)/([^/]+)?'), re.compile('(?:twitter\\.com/|@)([a-zA-Z0-9_]{1,15})'), False), ('linkedin', re.compile('https?://(?:www\\.)?linkedin\\.com/in/([^/]+)?'), re.compile('linkedin\\.com/in/([a-zA-Z0-9_-]{3,100})'), False), ('mastodon', re.compile('https?://(?:www\\.)?mastodon\\.social/@([^/]+)?'), re.compile('@(?:[a-zA-Z0-9_]+@)?([a-zA-Z0-9_]{1,30})'), False), ('keybase', re.compile('https?://(?:www\\.)?keybase\\.io/([^/]+)?'), re.compile('(?:keybase\\.io/|@)([a-zA-Z0-9][a-zA-Z0-9_-]{0,38})'), False), ('gitlab', re.compile('https?://(?:www\\.)?gitlab\\.com/([^/]+)?'), re.compile('(?:gitlab\\.com/|@)([a-zA-Z0-9][a-zA-Z0-9_-]{0,38})'), False), ('hackernews', re.compile('https?://news\\.ycombinator\\.com/user\\?id=([^&]+)?'), re.compile('(?:news\\.ycombinator\\.com/user\\?id=|@)([a-zA-Z0-9_-]{1,30})'), False), ('reddit', re.compile('https?://(?:www\\.)?reddit\\.com/user/([^/]+)?'), re.compile('(?:reddit\\.com/user/|u/)([a-zA-Z0-9_-]{3,20})'), False), ('youtube', re.compile('https?://(?:www\\.)?youtube\\.com/@([^/]+)?'), re.compile('(?:youtube\\.com/@|@)([a-zA-Z0-9_-]{3,30})'), False), ('facebook', re.compile('https?://(?:www\\.)?facebook\\.com/([^/]+)?'), re.compile('(?:facebook\\.com/|@)([a-zA-Z0-9\\.]{5,50})'), False), ('telegram', re.compile('https?://(?:www\\.)?(?:t\\.me|telegram\\.me)/([^/]+)?'), re.compile('(?:t\\.me|telegram\\.me)/([a-zA-Z0-9_-]{3,50})'), False), ('matrix', re.compile('https?://(?:www\\.)?matrix\\.to/#[^/]+/?$'), re.compile('matrix\\.to/#@?([^/]+)'), False), ('medium', re.compile('https?://(?:www\\.)?medium\\.com/@([^/]+)?'), re.compile('medium\\.com/@([a-zA-Z0-9_-]{3,50})'), False), ('substack', re.compile('https?://(?:www\\.)?([a-zA-Z0-9][a-zA-Z0-9_-]{0,48})\\.substack\\.com/?'), re.compile('substack\\.com/@([a-zA-Z0-9_-]{3,50})'), False), ('npmjs', re.compile('https?://(?:www\\.)?npmjs\\.com/~([^/]+)?'), re.compile('npmjs\\.com/~([a-zA-Z0-9_-]{3,50})'), False), ('pypi', re.compile('https?://(?:www\\.)?pypi\\.org/user/([^/]+)?'), re.compile('pypi\\.org/user/([a-zA-Z0-9_-]{3,50})'), False), ('huggingface', re.compile('https?://(?:www\\.)?huggingface\\.co/([^/]+)?'), re.compile('huggingface\\.co/([a-zA-Z0-9_-]{3,50})'), False), ('github_gist', re.compile('https?://(?:www\\.)?gist\\.github\\.com/([^/]+)?'), re.compile('gist\\.github\\.com/([a-zA-Z0-9_-]{3,50})'), False), ('gitlab_selfhosted', re.compile('https?://[^/]+/u/([^/]+)?'), re.compile('/u/([a-zA-Z0-9][a-zA-Z0-9_-]{0,38})'), False), ('discord_invite', re.compile('https?://(?:www\\.)?discord(?:(?:app)?\\.com/invite|\\.gg)/([^/]+)?'), re.compile('discord(?:app)?\\.com/invite/([a-zA-Z0-9_-]{3,20})'), True)]
_BIO_LINK_PATTERNS: list[re.Pattern[str]] = [re.compile('(?:https?://)?(?:www\\.)?([a-zA-Z0-9-]+\\.[a-zA-Z]{2,})/[~@]?[a-zA-Z0-9_-]+', re.IGNORECASE), re.compile('@([a-zA-Z0-9_-]{1,30})\\.(?:io|dev|com|org|net)', re.IGNORECASE)]
_EMAIL_PATTERNS: re.Pattern[str] = re.compile('[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}')
_SOCIAL_URL_RE: re.Pattern[str] = re.compile('https?://[a-zA-Z0-9][a-zA-Z0-9-]*(?:\\.[a-zA-Z]{2,})+(?:/[^\\s<>\\"\')\\]]*)?', re.IGNORECASE)
_SOCIAL_DOMAIN_RE: re.Pattern[str] = re.compile('[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\\.(?:[a-zA-Z]{2,})')
_PGP_PATTERNS: re.Pattern[str] = re.compile('\\b(?:PGP|GPG)[:\\s]*(?:0x)?([A-F0-9]{8,40})\\b', re.IGNORECASE)
_BLOOM: Any = None

def _get_bloom_filter() -> Any:
    """Lazy-open the rotating mmap Bloom filter, falling back to in-memory."""
    global _BLOOM
    if _BLOOM is None:
        try:
            from rust_extensions import RotatingMmapBloomFilter
            import os, pathlib
            path_a = os.path.expanduser(_SOCIAL_BLOOM_PATH_A)
            path_b = os.path.expanduser(_SOCIAL_BLOOM_PATH_B)
            pathlib.Path(path_a).parent.mkdir(parents=True, exist_ok=True)
            _BLOOM = RotatingMmapBloomFilter(path_a, path_b, capacity=_SOCIAL_BLOOM_CAPACITY, fp_rate=0.01)
        except Exception:
            from rust_extensions import BloomFilter
            _BLOOM = BloomFilter(capacity=_SOCIAL_BLOOM_CAPACITY, fp_rate=0.01)
    return _BLOOM

class SocialIdentityFacet(msgspec.Struct, frozen=True, gc=False):
    """A single social identity profile extracted from findings."""
    finding_id: str
    platform: str
    username: str
    display_name: str
    profile_url: str
    linked_domains: tuple[str, ...]
    linked_emails: tuple[str, ...]
    confidence: float
    evidence_kind: str = 'url_in_payload'

class SocialIdentityResult(msgspec.Struct, frozen=True, gc=False):
    """Outcome of a social identity mining scan."""
    facets: tuple[SocialIdentityFacet, ...]
    scanned_count: int
    skipped_count: int
    elapsed_ms: float

def _is_url(text: str) -> bool:
    """Check if text looks like a URL."""
    if not text or len(text) > 200:
        return False
    return bool(re.match('https?://', text, re.IGNORECASE))

class SocialIdentityMiner:
    """
    Deterministic social identity facet miner.

    Extracts social profile facets (GitHub, Twitter, LinkedIn, etc.) from
    accepted findings by scanning URLs, text content, and bio links.
    No invasive scraping — only surface-level extraction from existing data.

    Fail-soft: malformed input silently skipped, partial results returned.
    """
    __slots__ = ('_bloom_seen', '_semaphore', '_stats')

    def __init__(self) -> None:
        self._bloom_seen: bool = False
        from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
        self._semaphore: asyncio.Semaphore = get_semaphore(ConcurrencyCategory.SOCIAL_MINE)
        self._stats: dict[str, int] = {'scanned': 0, 'skipped': 0, 'facets_found': 0}

    def reset(self) -> None:
        """Reset state between sprints. Bloom filter survives (mmap persists)."""
        self._bloom_seen = False
        self._stats = {'scanned': 0, 'skipped': 0, 'facets_found': 0}

    async def mine(self, findings: list[Any], store: Any, query: str) -> SocialIdentityResult:
        """
        Scan accepted findings for social identity facets.

        Args:
            findings: Accepted CanonicalFinding list from sprint
            store: DuckDBShadowStore for canonical write
            query: Sprint query (used for context)

        Returns:
            SocialIdentityResult with extracted facets and stats
        """
        start_ms = _time.monotonic() * 1000
        facets: list[SocialIdentityFacet] = []
        try:
            from ..utils.uma_budget import get_uma_snapshot
            snap = get_uma_snapshot()
            if snap.get('high_water') and snap.get('rss_mb', 0) > snap['high_water'] * 0.85:
                return SocialIdentityResult(facets=(), scanned_count=0, skipped_count=len(findings), elapsed_ms=_time.monotonic() * 1000 - start_ms)
        except Exception:  # noqa: BLE001
            pass
        all_urls: list[tuple[str, str, str, str]] = []
        for finding in findings:
            if len(all_urls) >= MAX_SOCIAL_PROFILES:
                break
            self._stats['scanned'] += 1
            urls_from_payload = self._extract_urls_from_payload(finding)
            for url in urls_from_payload[:MAX_LINKS_PER_PROFILE]:
                all_urls.append((url, getattr(finding, 'finding_id', 'unknown'), '', 'url_in_payload'))
            ioc_val = getattr(finding, 'ioc_value', '')
            if ioc_val and isinstance(ioc_val, str) and (len(ioc_val) < 2048):
                if _is_url(ioc_val):
                    all_urls.append((ioc_val, getattr(finding, 'finding_id', 'unknown'), '', 'ioc_value'))
            source_type = getattr(finding, 'source_type', '')
            if source_type in ('ct', 'certificate_transparency'):
                domains = self._extract_domains_from_cert_text(getattr(finding, 'payload_text', '') or '')
                for domain in domains[:5]:
                    all_urls.append((f'https://{domain}', getattr(finding, 'finding_id', 'unknown'), '', 'provenance'))
        self._stats['scanned'] = len(findings)
        if not all_urls:
            return SocialIdentityResult(facets=(), scanned_count=self._stats['scanned'], skipped_count=self._stats['skipped'], elapsed_ms=_time.monotonic() * 1000 - start_ms)
        tasks = [self._process_url(url, finding_id, text_sample, evidence_kind) for url, finding_id, text_sample, evidence_kind in all_urls]
        gathered: list[Any] = []
        try:
            async with asyncio.timeout(30.0):
                gathered = await parallel_ok(*tasks, label='social_identity_miner:331')
        except TimeoutError:
            for t in tasks:
                try:
                    t.close()
                except Exception:  # noqa: BLE001
                    pass
            gathered = []
        for result in gathered:
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, Exception):
                self._stats['skipped'] += 1
                continue
            if isinstance(result, SocialIdentityFacet):
                facets.append(result)
                self._stats['facets_found'] += 1
        unique_facets = self._deduplicate_facets(facets)
        if unique_facets:
            await self._write_findings(unique_facets, store, query)
        return SocialIdentityResult(facets=tuple(unique_facets), scanned_count=self._stats['scanned'], skipped_count=self._stats['skipped'], elapsed_ms=_time.monotonic() * 1000 - start_ms)

    async def _process_url(self, url: str, finding_id: str, text_sample: str, source: str='url_in_payload') -> SocialIdentityFacet | None:
        """Extract social identity from a single URL.

        Fast path: Aho-Corasick single-pass platform detection.
        Fallback: sequential pattern matching (same logic, same results).
        """
        async with self._semaphore:
            try:
                parsed = urlparse(url)
                path = parsed.path.strip('/')
                ac = _get_ac_matcher()
                if ac is not None:
                    matches = ac.scan(url)
                    if matches:
                        _, _, pattern_str = matches[0]
                        matched_idx = None
                        for idx, (_plat, url_re, _, _) in enumerate(_PLATFORM_PATTERNS):
                            if url_re.pattern == pattern_str:
                                matched_idx = idx
                                break
                        if matched_idx is None:
                            return None
                        platform, _url_re, _username_re, is_invite_only = _PLATFORM_PATTERNS[matched_idx]
                        if is_invite_only:
                            return None
                        username = _extract_platform_username(url, matched_idx)
                        if not username or len(username) < 2:
                            return None
                        profile_url = self._build_profile_url(platform, username, parsed.netloc)
                        linked_domains = self._extract_linked_domains(text_sample)
                        linked_emails = self._extract_linked_emails(text_sample)
                        confidence = self._compute_confidence(platform, username, linked_domains, linked_emails)
                        if confidence < SOCIAL_MIN_CONFIDENCE:
                            return None
                        return SocialIdentityFacet(finding_id=finding_id, platform=platform, username=username, display_name=username, profile_url=profile_url, linked_domains=tuple(linked_domains), linked_emails=tuple(linked_emails), confidence=confidence, evidence_kind=source)
                for platform, url_re, username_re, is_invite_only in _PLATFORM_PATTERNS:
                    url_match = url_re.match(url)
                    if not url_match:
                        host_match = re.match('https?://(?:www\\.)?' + re.escape(parsed.netloc) + '/?', url)
                        if host_match and platform in parsed.netloc:
                            url_match = True
                    if not url_match and platform not in parsed.netloc:
                        continue
                    if is_invite_only:
                        return None
                    username = ''
                    if path:
                        username = path.split('/')[0]
                        if username_re.search(url):
                            m = username_re.search(url)
                            if m and m.group(1):
                                username = m.group(1)
                    if not username or len(username) < 2:
                        continue
                    profile_url = self._build_profile_url(platform, username, parsed.netloc)
                    linked_domains = self._extract_linked_domains(text_sample)
                    linked_emails = self._extract_linked_emails(text_sample)
                    confidence = self._compute_confidence(platform, username, linked_domains, linked_emails)
                    if confidence < SOCIAL_MIN_CONFIDENCE:
                        continue
                    return SocialIdentityFacet(finding_id=finding_id, platform=platform, username=username, display_name=username, profile_url=profile_url, linked_domains=tuple(linked_domains), linked_emails=tuple(linked_emails), confidence=confidence, evidence_kind=source)
                return None
            except Exception:
                return None

    def _extract_urls_from_payload(self, finding: Any) -> list[str]:
        """Extract URLs from finding payload_text."""
        urls: list[str] = []
        try:
            payload = getattr(finding, 'payload_text', '') or ''
            if not payload:
                return []
            try:
                import orjson as _orjson
                env = _orjson.loads(payload)
                for key in ('urls', 'links', 'extracted_urls', 'url_list'):
                    if key in env and isinstance(env[key], list):
                        urls.extend((str(u) for u in env[key] if isinstance(u, str)))
                if 'raw_text' in env:
                    urls.extend(self._scan_text_for_urls(env['raw_text']))
                elif 'text' in env:
                    urls.extend(self._scan_text_for_urls(env['text']))
            except (json.JSONDecodeError, TypeError):
                urls.extend(self._scan_text_for_urls(payload))
            finding_str = str(finding)
            urls.extend(self._scan_text_for_urls(finding_str))
        except Exception:  # noqa: BLE001
            pass
        return urls[:MAX_LINKS_PER_PROFILE]

    def _scan_text_for_urls(self, text: str) -> list[str]:
        """Scan text for URL patterns."""
        if not text or len(text) > MAX_SOCIAL_TEXT_BYTES:
            return []
        urls: list[str] = []
        for m in _SOCIAL_URL_RE.finditer(text):
            url = m.group(0)
            if len(url) < 200:
                urls.append(url)
        return urls[:MAX_LINKS_PER_PROFILE]

    def _extract_domains_from_cert_text(self, text: str) -> list[str]:
        """Extract domains from certificate transparency text."""
        if not text:
            return []
        domains = []
        for m in _SOCIAL_DOMAIN_RE.finditer(text):
            d = m.group(0)
            if len(d) > 4 and '.' in d and (d.count('.') < 4):
                domains.append(d)
        return domains[:10]

    def _extract_linked_domains(self, text: str) -> list[str]:
        """Extract domain mentions from text (bio links)."""
        if not text:
            return []
        domains: list[str] = []
        for pattern in _BIO_LINK_PATTERNS:
            for m in pattern.finditer(text):
                if m.group(1):
                    domains.append(m.group(1).lower())
        return list(set(domains))[:5]

    def _extract_linked_emails(self, text: str) -> list[str]:
        """Extract email addresses from text."""
        if not text:
            return []
        emails = _EMAIL_PATTERNS.findall(text)
        return list(set(emails))[:5]

    def _compute_confidence(self, _platform: str, _username: str, linked_domains: list[str], linked_emails: list[str]) -> float:
        """Compute confidence using canonical confidence policy."""
        has_provenance = True
        has_ioc = bool(linked_emails or linked_domains)
        corroboration_count = min(len(linked_domains) + len(linked_emails), 4)
        confidence = _compute_confidence(source_family='SOCIAL', has_provenance=has_provenance, has_ioc=has_ioc, corroboration_count=corroboration_count, model_score=None)
        _platform_upper = _platform.upper()
        if _platform_upper == 'GITHUB':
            confidence += 0.1
        elif _platform_upper in ('TWITTER', 'LINKEDIN', 'MASTODON', 'KEYBASE', 'GITLAB'):
            confidence += 0.05
        if len(_username) > 5:
            confidence += 0.05
        return max(min(confidence, 0.95), SOCIAL_MIN_CONFIDENCE)

    def _deduplicate_facets(self, facets: list[SocialIdentityFacet]) -> list[SocialIdentityFacet]:
        """Deduplicate facets by profile URL using Rust MmapBloomFilter (F320).

        Bloom filter is opened lazily and survives sprint restarts (mmap persist).
        Rotating filter avoids file deletion race. FPR ≤ 1% is acceptable for
        social identity dedup (dupe social profiles → mild noise, not data loss).
        """
        bloom = _get_bloom_filter()
        if not self._bloom_seen:
            self._bloom_seen = True
            try:
                if len(bloom) >= _SOCIAL_BLOOM_CAPACITY * 0.9:
                    bloom.rotate()
            except Exception:  # noqa: BLE001
                pass
        unique: list[SocialIdentityFacet] = []
        for facet in facets:
            key = f'{facet.platform}:{facet.username}'
            if not bloom.contains(key):
                bloom.add(key)
                unique.append(facet)
            elif not any((f'{f.platform}:{f.username}' == key for f in unique)):
                unique.append(facet)
        return unique[:MAX_SOCIAL_PROFILES]

    async def _write_findings(self, facets: list[SocialIdentityFacet], store: Any, query: str) -> None:
        """Write social identity facets via canonical path."""
        try:
            from hledac.universal.knowledge.duckdb_store import CanonicalFinding
            findings: list[CanonicalFinding] = []
            for facet in facets:
                payload = json.dumps({'platform': facet.platform, 'username': facet.username, 'display_name': facet.display_name, 'profile_url': facet.profile_url, 'linked_domains': list(facet.linked_domains), 'linked_emails': list(facet.linked_emails), 'confidence': facet.confidence, 'source_finding_id': facet.finding_id, 'evidence_kind': facet.evidence_kind if hasattr(facet, 'evidence_kind') else 'url_in_payload'})
                finding = CanonicalFinding(finding_id=f'social:{facet.platform}:{facet.username[:32]}', source_type='social_identity_surface', query=query, confidence=facet.confidence, ts=_time.time(), provenance=('social_identity_miner', facet.platform), payload_text=payload)
                findings.append(finding)
            if hasattr(store, 'submit_findings'):
                await store.submit_findings(findings)
            elif hasattr(store, 'async_ingest_findings_batch'):
                await store.async_ingest_findings_batch(findings)
            elif hasattr(store, 'ingest_findings'):
                await store.ingest_findings(findings)
        except Exception:  # noqa: BLE001
            pass

    def _build_profile_url(self, platform: str, username: str, platform_host: str='') -> str:
        """Build canonical profile URL for a platform."""
        platform_url_map = {'github': f'https://github.com/{username}', 'twitter': f'https://twitter.com/{username}', 'linkedin': f'https://linkedin.com/in/{username}', 'mastodon': f'https://mastodon.social/@{username}', 'keybase': f'https://keybase.io/{username}', 'gitlab': f'https://gitlab.com/{username}', 'hackernews': f'https://news.ycombinator.com/user?id={username}', 'reddit': f'https://www.reddit.com/user/{username}', 'youtube': f'https://youtube.com/@{username}', 'facebook': f'https://www.facebook.com/{username}', 'telegram': f'https://t.me/{username}', 'matrix': f'https://matrix.to/#/{username}', 'medium': f'https://medium.com/@{username}', 'substack': f'https://{username}.substack.com/', 'npmjs': f'https://www.npmjs.com/~{username}', 'pypi': f'https://pypi.org/user/{username}', 'huggingface': f'https://huggingface.co/{username}', 'github_gist': f'https://gist.github.com/{username}'}
        if platform == 'gitlab_selfhosted' and platform_host:
            return f'https://{platform_host}/u/{username}'
        if platform == 'discord_invite':
            return ''
        return platform_url_map.get(platform, f'https://{platform}.com/{username}')

    def get_stats(self) -> dict[str, int]:
        """Return current mining statistics."""
        return dict(self._stats)

def create_social_identity_miner_adapter() -> SocialIdentityMiner:
    """Create a SocialIdentityMiner instance."""
    return SocialIdentityMiner()