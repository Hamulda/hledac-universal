"""
Lightweight structured TI feed adapters for normalized threat-intel ingress.

Provides a simple adapter seam for structured threat-intel sources (NVD, CISA KEV)
that maps to the NormalizedEntry format compatible with the existing discovery
architecture.

No browser, no JS rendering, no auth-required APIs, no cloud-only dependencies.

Sprint 8BN — Structured TI Ingest V1
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import msgspec.json as _json
import os
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
from hledac.universal.utils.msgspec_json import loads as _msgspec_loads, dumps_str as _msgspec_dumps_str
import httpx
import msgspec
from hledac.universal.network.session_runtime import async_get_httpx_session
from hledac.universal.tools.discovery_replay import read_cassette, replay_enabled, replay_strict_enabled, write_cassette
from hledac.universal.transport.circuit_breaker import checked_httpx_get, checked_httpx_post
try:
    from selectolax.parser import HTMLParser as _SelectolaxHTMLParser
    SELECTOLAX_AVAILABLE = True
except ImportError:
    SELECTOLAX_AVAILABLE = False
if TYPE_CHECKING:
    from hledac.universal.fetching.public_fetcher import FetchResult
TIER_SURFACE = 'surface'
TIER_STRUCTURED_TI = 'structured_ti'
TIER_OVERLAY_READY = 'overlay_ready'

class NormalizedEntry(msgspec.Struct, frozen=True):
    """
    Lightweight normalized entry from any structured TI source.

    Compatible with the existing discovery architecture while providing
    richer identifier density than typical RSS feeds.

    Attributes
    ----------
    entry_hash:
        Deterministic hash of title|published_raw for dedup.
    source_url:
        Canonical URL for the entry (or empty string if N/A).
    title:
        Entry title.
    body_text:
        Extracted body/description text.
    published_at:
        Unix timestamp (UTC) or None.
    source_type:
        Adapter source type string (e.g. "nvd", "cisa_kev", "rss").
    raw_identifiers:
        Tuple of identifiers extracted from the entry (e.g. CVE IDs).
        Must contain at minimum the primary identifier if available.
    source_tier:
        Source tier classification (surface, structured_ti, overlay_ready).
    rich_content_available:
        Whether richer content (full advisory, exploit, etc.) is available.
    """
    entry_hash: str
    source_url: str
    title: str
    body_text: str
    published_at: float | None
    source_type: str
    raw_identifiers: tuple[str, ...]
    source_tier: str = TIER_SURFACE
    rich_content_available: bool = False

class SourceAdapter(ABC):
    """
    Abstract base for structured TI source adapters.

    Adapters must implement fetch_recent() which returns a list of
    NormalizedEntry objects.
    """

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Return the unique source type identifier."""
        ...

    @property
    @abstractmethod
    def source_tier(self) -> str:
        """Return the source tier classification."""
        ...

    @property
    def parseable(self) -> bool:
        """Whether the source format is parseable (default True)."""
        return True

    @property
    def stable_schema(self) -> bool:
        """Whether the source has a stable published schema (default True)."""
        return True

    @property
    def identifier_rich(self) -> bool:
        """
        Whether entries typically contain structured identifiers
        (CVE IDs, CPEs, etc.). Default True for structured TI sources.
        """
        return True

    @property
    def priority_score(self) -> int:
        """Computed priority score based on source quality attributes."""
        from hledac.universal.discovery.source_registry import source_quality_score
        return source_quality_score(self.parseable, self.stable_schema, self.identifier_rich, self.source_tier)

    @abstractmethod
    async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]:
        """
        Fetch recent entries from the source.

        Parameters
        ----------
        limit:
            Maximum number of entries to return.

        Returns
        -------
        tuple[NormalizedEntry, ...]
            Entries sorted newest-first if published_at is available,
            otherwise in discovery order. Empty tuple on failure.
        """
        ...

    @staticmethod
    def _hash_fields(*fields: str) -> str:
        """Compute deterministic xxhash over pipe-separated fields."""
        import xxhash
        return xxhash.xxh3_64('|'.join((f or '' for f in fields))).hexdigest()

    @staticmethod
    async def _fetch_text(url: str, timeout_s: float=30.0, max_bytes: int=5000000) -> tuple[str | None, str | None]:
        """
        Fetch text content via public_fetcher (async).

        Returns (text, error). One is always None.
        """
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text
        try:
            result: FetchResult = await async_fetch_public_text(url, timeout_s=timeout_s, max_bytes=max_bytes)
        except Exception as e:
            return (None, str(e))
        if result.error or result.text is None:
            return (None, result.error or 'fetch_returned_none')
        return (result.text, None)

class NvdApiAdapter(SourceAdapter):
    """
    NVD CVE API v2 recent CVE ingest.

    Public, no auth required. Bounded resultsPerPage.
    Maps CVE ID, description, score, and references to NormalizedEntry.

    API base: https://services.nvd.nist.gov/rest/json/cves/2.0
    """
    API_BASE = 'https://services.nvd.nist.gov/rest/json/cves/2.0'
    SOURCE_TYPE = 'nvd'
    SOURCE_TIER = TIER_STRUCTURED_TI
    MAX_PER_PAGE = 20
    HARD_LIMIT = 100

    @property
    def source_type(self) -> str:
        return self.SOURCE_TYPE

    @property
    def source_tier(self) -> str:
        return self.SOURCE_TIER

    @property
    def identifier_rich(self) -> bool:
        return True

    async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]:
        """
        Fetch recent CVEs from NVD API.

        Uses /cves/recent endpoint for latest CVEs.
        Results sorted by lastModified descending (NVD default).
        """
        limit = min(max(limit, 1), self.HARD_LIMIT)
        results_per_page = min(limit, self.MAX_PER_PAGE)
        url = f'{self.API_BASE}?resultsPerPage={results_per_page}&startIndex=0'
        text, error = await self._fetch_text(url, timeout_s=30.0, max_bytes=5000000)
        if error or text is None:
            return ()
        try:
            data = _msgspec_loads(text)
        except Exception as e:
            logger.debug(f'[NVD] JSON parse error for {url}: {e}')
            return ()
        vulnerabilities = data.get('vulnerabilities', [])
        if not isinstance(vulnerabilities, list):
            return ()
        entries: list[NormalizedEntry] = []
        time.time()
        for vuln in vulnerabilities[:limit]:
            cve_data = vuln.get('cve', {})
            cve_id = cve_data.get('id', '')
            descriptions = cve_data.get('descriptions', [])
            description = ''
            for desc in descriptions:
                if desc.get('lang', '').lower() == 'en':
                    description = desc.get('value', '')
                    break
            if not description and descriptions:
                description = descriptions[0].get('value', '')
            published_ts: float | None = None
            pub_str = cve_data.get('published')
            if pub_str:
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(pub_str.replace('Z', '+00:00'))
                    published_ts = dt.timestamp()
                except Exception as e:
                    logger.debug(f'[NVD] Timestamp parse error for {cve_id}: {e}')
            references = cve_data.get('references', [])[:5]
            source_url = references[0].get('url', '') if references else ''
            metrics = cve_data.get('metrics', {})
            score = None
            if 'cvssMetricV31' in metrics:
                cvss = metrics['cvssMetricV31'][0].get('cvssData', {})
                score = cvss.get('baseScore')
            elif 'cvssMetricV30' in metrics:
                cvss = metrics['cvssMetricV30'][0].get('cvssData', {})
                score = cvss.get('baseScore')
            elif 'cvssMetricV2' in metrics:
                cvss = metrics['cvssMetricV2'][0].get('cvssData', {})
                score = cvss.get('baseScore')
            body_parts = []
            if description:
                body_parts.append(description)
            if score is not None:
                body_parts.append(f'CVSS: {score}')
            body_text = ' '.join(body_parts)
            raw_identifiers = (cve_id,) if cve_id else ()
            entry_hash = self._hash_fields(cve_id, published_ts is not None and str(published_ts) or '')
            entries.append(NormalizedEntry(entry_hash=entry_hash, source_url=source_url, title=cve_id or '', body_text=body_text, published_at=published_ts, source_type=self.SOURCE_TYPE, raw_identifiers=raw_identifiers, source_tier=self.SOURCE_TIER, rich_content_available=bool(references)))
        return tuple(entries)

class CisaKevAdapter(SourceAdapter):
    """
    CISA Known Exploited Vulnerabilities (KEV) catalog JSON ingest.

    Public, no auth required. Single JSON endpoint.
    Maps CVE ID, vendor/project/product, and notes to NormalizedEntry.

    API: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json
    """
    API_URL = 'https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json'
    SOURCE_TYPE = 'cisa_kev'
    SOURCE_TIER = TIER_STRUCTURED_TI
    HARD_LIMIT = 200

    @property
    def source_type(self) -> str:
        return self.SOURCE_TYPE

    @property
    def source_tier(self) -> str:
        return self.SOURCE_TIER

    @property
    def identifier_rich(self) -> bool:
        return True

    @property
    def stable_schema(self) -> bool:
        return True

    async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]:
        """
        Fetch KEV catalog entries.

        Returns entries sorted by dateAdded descending (most recent first).
        """
        limit = min(max(limit, 1), self.HARD_LIMIT)
        text, error = await self._fetch_text(self.API_URL, timeout_s=45.0, max_bytes=10000000)
        if error or text is None:
            return ()
        try:
            data = _msgspec_loads(text)
        except Exception as e:
            logger.debug(f'[CISA KEV] JSON parse error for {self.API_URL}: {e}')
            return ()
        vulns = data.get('vulnerabilities', [])
        if not isinstance(vulns, list):
            return ()
        entries: list[NormalizedEntry] = []
        for vuln in vulns[:limit]:
            cve_id = vuln.get('cveID', '')
            body_parts = []
            for field in ('vendorProject', 'product', 'shortDescription', 'notes'):
                val = vuln.get(field, '')
                if val:
                    body_parts.append(str(val))
            body_text = ' '.join(body_parts)
            published_ts: float | None = None
            date_added = vuln.get('dateAdded', '')
            if date_added:
                try:
                    from datetime import datetime
                    dt = datetime.strptime(date_added, '%Y-%m-%d')
                    published_ts = dt.timestamp()
                except Exception as e:
                    logger.debug(f'[CISA KEV] Date parse error for {cve_id}: {e}')
            source_url = vuln.get('knownRansomwareCampaignUse', '')
            if not source_url:
                source_url = 'https://www.cisa.gov/known-exploited-vulnerabilities-catalog'
            raw_identifiers = (cve_id,) if cve_id else ()
            entry_hash = self._hash_fields(cve_id, date_added)
            entries.append(NormalizedEntry(entry_hash=entry_hash, source_url=source_url or '', title=cve_id or '', body_text=body_text, published_at=published_ts, source_type=self.SOURCE_TYPE, raw_identifiers=raw_identifiers, source_tier=self.SOURCE_TIER, rich_content_available=False))
        return tuple(entries)
logger = logging.getLogger(__name__)

async def fetch_urlhaus(max_items: int=100) -> list[dict]:
    """URLhaus — live malware URL feed, public API, no key required."""
    try:
        s = await async_get_httpx_session()
        data, status, err = await checked_httpx_get(s, 'https://urlhaus-api.abuse.ch/v1/urls/recent/', timeout=httpx.Timeout(15), failure_kind='urlhaus')
        if err:
            logger.debug(f'[URLhaus] {err}')
            return []
        return [{'ioc': e.get('url'), 'ioc_type': 'url', 'threat_type': e.get('threat'), 'title': f"URLhaus: {e.get('threat', 'malware')}", 'source': 'urlhaus'} for e in data.get('urls', [])[:max_items] if e.get('url_status') == 'online']
    except Exception as e:
        logger.debug(f'[URLhaus] {e}')
    return []

async def fetch_threatfox(days: int=1) -> list[dict]:
    """ThreatFox IOC feed — public API, no key required."""
    try:
        s = await async_get_httpx_session()
        data, status, err = await checked_httpx_post(s, 'https://threatfox-api.abuse.ch/api/v1/', json={'query': 'get_iocs', 'days': days}, timeout=httpx.Timeout(float(os.environ.get('HLEDAC_FEED_TIMEOUT', '45')), connect=10.0), failure_kind='threatfox')
        if err:
            logger.debug(f'[ThreatFox] {err}')
            return []
        return [{'ioc': i.get('ioc_value'), 'ioc_type': i.get('ioc_type'), 'malware': i.get('malware'), 'confidence': i.get('confidence_level', 50) / 100, 'title': f"ThreatFox: {i.get('malware', '?')}", 'source': 'threatfox'} for i in data.get('data', [])]
    except Exception as e:
        logger.debug(f'[ThreatFox] {e}')
    return []

async def fetch_sslbl() -> list[dict]:
    """SSLBL SSL Certificate Blacklist — SHA1 fingerprints of blacklisted SSL certs.

    Feed: https://sslbl.abuse.ch/blacklist/sslblacklist.csv
    No auth required. Updated every 5 min; do not fetch more often.
    """
    try:
        s = await async_get_httpx_session()
        timeout_cfg = httpx.Timeout(float(os.environ.get('HLEDAC_FEED_TIMEOUT', '45')), connect=10.0)
        resp = await s.get('https://sslbl.abuse.ch/blacklist/sslblacklist.csv', timeout=timeout_cfg)
        if resp.status_code != 200:
            logger.debug(f'[SSLBL] HTTP {resp.status_code}')
            return []
        text = resp.text
        findings = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split(',')
            if len(parts) < 3:
                continue
            listing_date, sha1, reason = (parts[0], parts[1], ','.join(parts[2:]))
            findings.append({'ioc': sha1, 'ioc_type': 'sha1', 'malware': reason, 'title': f'SSLBL: {reason}', 'source': 'sslbl.abuse.ch', 'listing_date': listing_date})
            if len(findings) >= 200:
                break
        return findings
    except Exception as e:
        logger.debug(f'[SSLBL] {e}')
    return []

async def fetch_feodo_c2() -> list[dict]:
    """Feodo Tracker C2 blocklist — public JSON, no key required."""
    try:
        s = await async_get_httpx_session()
        data, status, err = await checked_httpx_get(s, 'https://feodotracker.abuse.ch/downloads/ipblocklist.json', timeout=httpx.Timeout(15), failure_kind='feodo')
        if err:
            logger.debug(f'[Feodo] {err}')
            return []
        return [{'ioc': e.get('ip_address'), 'ioc_type': 'ip', 'malware': e.get('malware'), 'port': e.get('port'), 'title': f"Feodo C2: {e.get('ip_address')}", 'source': 'feodo_tracker'} for e in (data if isinstance(data, list) else [])]
    except Exception as e:
        logger.debug(f'[Feodo] {e}')
    return []

async def query_circl_pdns(domain: str, max_results: int=50) -> list[dict]:
    """CIRCL Passive DNS — community free tier, no authentication."""
    import json as _json
    try:
        s = await async_get_httpx_session()
        text, status, err = await checked_httpx_get(s, f'https://www.circl.lu/pdns/query/{domain}', timeout=httpx.Timeout(15), failure_kind='circl_pdns')
        if err:
            logger.debug(f'[CIRCL pDNS] {err}')
            return []
        if status != 200:
            return []
        results = []
        for line in str(text).strip().split('\n')[:max_results]:
            try:
                rec = __msgspec_loads(line)
                results.append({'ioc': rec.get('rrvalue', ''), 'ioc_type': rec.get('rrtype', 'A').lower(), 'domain': rec.get('rrname', ''), 'first_seen': rec.get('time_first', ''), 'last_seen': rec.get('time_last', ''), 'source': 'circl_pdns'})
            except Exception as e:
                logger.debug(f'[CIRCL pDNS] JSON parse error for line: {e}')
        return results
    except Exception as e:
        logger.debug(f'[CIRCL pDNS] {e}')
    return []

async def search_crtsh(domain: str, max_results: int=100) -> list[dict]:
    """crt.sh Certificate Transparency search — no key required."""
    try:
        s = await async_get_httpx_session()
        data, status, err = await checked_httpx_get(s, 'https://crt.sh/', params={'q': f'%.{domain}', 'output': 'json'}, timeout=httpx.Timeout(20), failure_kind='crtsh')
        if err:
            logger.warning(f'[crt.sh] {err}')
            return []
        if status != 200:
            return []
        results: list[dict] = []
        seen: set[str] = set()
        for cert in data[:max_results]:
            for sub in cert.get('name_value', '').split('\n'):
                sub = sub.strip()
                if sub and sub not in seen:
                    seen.add(sub)
                    results.append({'ioc': sub, 'ioc_type': 'domain', 'issuer': cert.get('issuer_name', ''), 'title': f'CT cert: {sub}', 'source': 'crtsh'})
        return results
    except Exception as e:
        logger.warning(f'[crt.sh] {e}')
    return []

async def certstream_monitor(keyword: str, duration_s: int=60, max_certs: int=200) -> list[dict]:
    """
    Certstream WebSocket — live CT certificate monitoring.
    Captures new certificates containing keyword in domain.
    Requires: pip install websockets
    FIXED: uses get_running_loop() — no race condition.
    """
    try:
        import websockets
    except ImportError:
        logger.debug('[Certstream] websockets not installed')
        return []
    import json as _json
    results: list[dict] = []
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + duration_s
        async with websockets.connect('wss://certstream.calidog.io', ping_interval=10, close_timeout=5) as ws:
            while loop.time() < deadline:
                if len(results) >= max_certs:
                    break
                try:
                    async with asyncio.timeout(5.0):
                        msg = await ws.recv()
                    data = __msgspec_loads(msg)
                    if data.get('message_type') != 'certificate_update':
                        continue
                    for d in data['data']['leaf_cert']['all_domains']:
                        if keyword.lower() in d.lower():
                            results.append({'ioc': d, 'ioc_type': 'domain', 'title': f'Certstream: {d}', 'source': 'certstream_live'})
                except TimeoutError:
                    continue
    except Exception as e:
        logger.warning(f'[Certstream] {e}')
    return results

async def enrich_ip_internetdb(ip: str) -> dict:
    """
    Shodan InternetDB — open ports, CVEs, hostnames.
    Free, no API key, ARM64 native. ~1MB RAM.
    """
    try:
        s = await async_get_httpx_session()
        data, status, err = await checked_httpx_get(s, f'https://internetdb.shodan.io/{ip}', timeout=httpx.Timeout(8), failure_kind='shodan_internetdb')
        if err:
            logger.debug(f'[ShodanInternetDB] {err}')
            return {}
        if status == 200:
            return {'ip': ip, 'ports': data.get('ports', []) if isinstance(data, dict) else [], 'cves': data.get('cves', []) if isinstance(data, dict) else [], 'hostnames': data.get('hostnames', []) if isinstance(data, dict) else [], 'tags': data.get('tags', []) if isinstance(data, dict) else [], 'source': 'shodan_internetdb'}
    except Exception as e:
        logger.debug(f'[ShodanInternetDB] {e}')
    return {}
COMMUNITY_URL = 'https://api.greynoise.io/v3/community/{ip}'

async def enrich_ip_greynoise_community(session: httpx.AsyncClient, ip: str) -> dict | None:
    """
    Single IP lookup via GreyNoise Community API.
    Returns dict with classification/noise/riot or None on miss/error.
    No API key required. Rate limit ~50/day — use sparingly.
    """
    url = COMMUNITY_URL.format(ip=ip)
    try:
        resp = await session.get(url, timeout=httpx.Timeout(10, connect=5.0), headers={'Accept': 'application/json'})
        if resp.status_code == 404:
            data = resp.json()
            return {'classification': data.get('classification', 'unknown'), 'noise': data.get('noise', False), 'riot': data.get('riot', False), 'name': data.get('name', ''), 'link': data.get('link', '')}
        if resp.status_code == 429:
            logger.warning('[GreyNoise/community] rate limit hit')
            return None
        resp.raise_for_status()
        data = resp.json()
        return {'classification': data.get('classification'), 'noise': data.get('noise', False), 'riot': data.get('riot', False), 'name': data.get('name', ''), 'link': data.get('link', '')}
    except Exception as e:
        logger.debug(f'[GreyNoise/community] {ip}: {e}')
        return None

async def enrich_findings_greynoise_community(session: httpx.AsyncClient, findings: list[dict], max_lookups: int=40) -> list[dict]:
    """
    Enrich IP findings with GreyNoise Community classification.
    Only enriches findings where ioc_type in ('ip', 'ipv4').
    Caps at max_lookups to respect daily rate limit.
    """
    ip_findings = [f for f in findings if f.get('ioc_type') in ('ip', 'ipv4') and f.get('ioc')][:max_lookups]
    if not ip_findings:
        return findings
    logger.info(f'[GreyNoise/community] enriching {len(ip_findings)} IPs (cap={max_lookups})')
    for finding in ip_findings:
        result = await enrich_ip_greynoise_community(session, finding['ioc'])
        if result:
            finding['greynoise'] = result
            if result['classification'] == 'malicious':
                finding['confidence'] = min(finding.get('confidence', 0.5) + 0.15, 1.0)
            if result['riot']:
                finding['confidence'] = min(finding.get('confidence', 0.5) * 0.5, 0.3)
        await asyncio.sleep(0.3)
    enriched = sum((1 for f in ip_findings if 'greynoise' in f))
    logger.info(f'[GreyNoise/community] enriched {enriched}/{len(ip_findings)} IPs')
    return findings

async def scrape_pastebin_for_keyword(keyword: str, max_pastes: int=10) -> list[dict]:
    """
    Pastebin archive scraping — public, no key required.
    FIXED: await asyncio.sleep() (previous bug was sync sleep).
    """
    results: list[dict] = []
    _UA = 'Mozilla/5.0 (Macintosh; ARM Mac OS X 14_0) AppleWebKit/605.1.15'
    try:
        s = await async_get_httpx_session()
        text, status, err = await checked_httpx_get(s, 'https://pastebin.com/archive', timeout=httpx.Timeout(10), failure_kind='pastebin_archive')
        if err:
            logger.debug(f'[Pastebin archive] {err}')
            return []
        if status != 200:
            return []
        html_text = str(text)
        paste_urls: list[str] = []
        if SELECTOLAX_AVAILABLE:
            try:
                tree = _SelectolaxHTMLParser(html_text)
                for tr in tree.css('table.maintable tr')[1:21]:
                    for a in tr.css('td a')[:1]:
                        href = a.attributes.get('href', '')
                        if href:
                            paste_urls.append(f'https://pastebin.com/raw{href}')
            except Exception as e:
                logger.debug('[Pastebin] selectolax parse failed, falling back to bs4: %s', e)
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_text, 'html.parser')
                paste_urls = [f"https://pastebin.com/raw{a['href']}" for tr in soup.select('table.maintable tr')[1:21] for a in tr.select('td a')[:1] if a.get('href')]
        else:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_text, 'html.parser')
            paste_urls = [f"https://pastebin.com/raw{a['href']}" for tr in soup.select('table.maintable tr')[1:21] for a in tr.select('td a')[:1] if a.get('href')]
        paste_urls = paste_urls[:max_pastes]
        # F350M-R: Parallel batch fetch — replaces sequential await loop.
        # Original: 1 URL × 1s sleep = N seconds serial. New: N URLs in one
        # asyncio.gather with concurrency=None (UMA-aware via ConcurrencyBudgetRegistry).
        # F1 FIX: concurrency=None → dynamic limit adapts to Pastebin WARN/CRITICAL
        # states automatically (PASTE_SCRAPE category: OK=4, WARN=2, CRITICAL=1).
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text_batch

        batch_results = await async_fetch_public_text_batch(
            paste_urls,
            timeout_s=8.0,
            max_bytes=200_000,
            use_stealth=False,
            use_js=False,
            use_doh=False,
            js_confidence=0.0,
            priority=5,
            concurrency=None,  # F1 FIX: UMA-aware via ConcurrencyBudgetRegistry
        )
        for raw_url, fetch_result in zip(paste_urls, batch_results):
            try:
                if fetch_result.error or fetch_result.text is None:
                    logger.debug(f'[Pastebin] Paste fetch error for {raw_url}: {fetch_result.error}')
                    continue
                content_str = fetch_result.text
                if keyword.lower() in content_str.lower():
                    results.append({
                        'url': raw_url,
                        'content': content_str[:2000],
                        'content_hash': hashlib.sha256(content_str.encode()).hexdigest()[:16],
                        'title': f'Pastebin hit: {keyword}',
                        'source': 'pastebin_scrape',
                    })
            except Exception as e:
                logger.debug(f'[Pastebin] Paste processing error for {raw_url}: {e}')
    except Exception as e:
        logger.debug(f'[Pastebin] {e}')
    return results

async def search_github_gists(keyword: str, max_results: int=10) -> list[dict]:
    """GitHub Gist public search — free, no key required."""
    results: list[dict] = []
    try:
        s = await async_get_httpx_session()
        text, status, err = await checked_httpx_get(s, 'https://gist.github.com/search', params={'q': keyword, 's': 'updated'}, timeout=httpx.Timeout(12), failure_kind='github_gist')
        if err:
            logger.debug(f'[GitHub Gist] {err}')
            return []
        if status != 200:
            return []
        html_text = str(text)
        if SELECTOLAX_AVAILABLE:
            try:
                tree = _SelectolaxHTMLParser(html_text)
                for item in tree.css('.gist-snippet')[:max_results]:
                    meta_a = item.css_first('.gist-snippet-meta a')
                    meta_url = meta_a.attributes.get('href', '') if meta_a else ''
                    body_el = item.css_first('.gist-snippet-body')
                    snippet_text = body_el.text(strip=True)[:200] if body_el else ''
                    if meta_url:
                        results.append({'url': f'https://gist.github.com{meta_url}', 'title': meta_a.text(strip=True) if meta_a else '', 'snippet': snippet_text, 'source': 'github_gist_search'})
            except Exception:
                pass
        if not results:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_text, 'html.parser')
            for item in soup.select('.gist-snippet')[:max_results]:
                a = item.select_one('.gist-snippet-meta a')
                p = item.select_one('.gist-snippet-body')
                if a and a.get('href'):
                    results.append({'url': f"https://gist.github.com{a['href']}", 'title': a.get_text(strip=True), 'snippet': p.get_text(strip=True)[:200] if p else '', 'source': 'github_gist_search'})
    except Exception as e:
        logger.debug(f'[GitHub Gist] {e}')
    return results
_GH_DORK_TEMPLATES = {'ioc_in_code': '"{v}" filename:iocs.txt OR filename:indicators', 'credential': '"{v}" password OR token OR secret', 'config_leak': '"{v}" filename:config.yml OR filename:.env', 'malware_sample': '"{v}" malware OR implant OR backdoor'}
_GH_HEADERS_BASE = {'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'hledac-osint/1.0'}

async def github_dork(value: str, dork_type: str='ioc_in_code', max_results: int=20) -> list[dict]:
    """
    GitHub code search dorking.
    Without token: 60 req/h (public unauthenticated).
    With GITHUB_TOKEN env var: 5000 req/h.
    Token is optional — function works without it.
    """
    import os
    headers = dict(_GH_HEADERS_BASE)
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = f'token {token}'
    query = _GH_DORK_TEMPLATES.get(dork_type, _GH_DORK_TEMPLATES['ioc_in_code']).format(v=value)
    try:
        s = await async_get_httpx_session()
        data, status, err = await checked_httpx_get(s, 'https://api.github.com/search/code', params={'q': query, 'per_page': min(max_results, 30)}, headers=headers, timeout=httpx.Timeout(15), failure_kind='github_dork')
        if err:
            logger.debug(f'[GitHub dork] {err}')
            return []
        if status == 200 and isinstance(data, dict):
            return [{'title': i['name'], 'url': i['html_url'], 'snippet': i['repository']['full_name'], 'source': 'github_dork'} for i in data.get('items', [])]
        elif status == 403:
            logger.debug('[GitHub dork] rate limited — set GITHUB_TOKEN')
    except Exception as e:
        logger.debug(f'[GitHub dork] {e}')
    return []
AHMIA_CLEARNET = 'https://ahmia.fi/search/'
AHMIA_ONION = 'http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/search/'

async def search_ahmia(query: str, max_results: int=20, use_onion: bool=False) -> list[dict]:
    """
    Ahmia dark web index search.
    use_onion=True → via tor_transport.
    """
    base = AHMIA_ONION if use_onion else AHMIA_CLEARNET
    html = ''
    try:
        s = await async_get_httpx_session()
        url = f'{base}?q={query}' if use_onion else base
        params = None if use_onion else {'q': query}
        text, status, err = await checked_httpx_get(s, url, params=params, headers={'User-Agent': 'Mozilla/5.0'}, timeout=httpx.Timeout(15), failure_kind='ahmia_onion' if use_onion else 'ahmia_clearnet')
        if err:
            logger.debug(f'[Ahmia] fetch failed: {err}')
            return []
        html = str(text)
        if not html:
            return []
        results: list[dict] = []
        if SELECTOLAX_AVAILABLE:
            try:
                tree = _SelectolaxHTMLParser(html)
                for li in tree.css('li.result')[:max_results]:
                    a = li.css_first('h4 a')
                    p = li.css_first('p')
                    if a:
                        href = a.attributes.get('href', '')
                        results.append({'title': a.text(strip=True), 'url': href, 'snippet': p.text(strip=True) if p else '', 'source': 'ahmia_onion' if use_onion else 'ahmia_clearnet'})
            except Exception:
                pass
        if not results:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            results = [{'title': a.get_text(strip=True), 'url': a['href'], 'snippet': p.get_text(strip=True) if p else '', 'source': 'ahmia_onion' if use_onion else 'ahmia_clearnet'} for li in soup.select('li.result')[:max_results] for a in [li.select_one('h4 a')] for p in [li.select_one('p')] if a and a.get('href')]
        return results
    except Exception as e:
        logger.warning(f'[Ahmia] {e}')
    return []

async def query_rdap(target: str) -> dict:
    """
    RDAP — WHOIS successor, structured REST API, no key required.
    Automatically detects domain vs IP.

    F239A: Discovery replay — read from cassette if available, write on success.
    """
    if replay_enabled():
        cached = read_cassette('rdap_org', target)
        if cached is not None:
            logger.debug(f'[RDAP] replay hit for {target}')
            return cached
        elif replay_strict_enabled():
            logger.debug(f'[RDAP] replay miss for {target}, strict mode')
            return {'error': 'replay_miss', 'error_type': 'replay_miss', 'target': target, 'rdap': None, 'source': 'rdap_org'}
    is_ip = target.replace('.', '').isdigit() or ':' in target
    base = 'https://rdap.org'
    endpoint = f'{base}/ip/{target}' if is_ip else f'{base}/domain/{target}'
    try:
        s = await async_get_httpx_session()
        data, status, err = await checked_httpx_get(s, endpoint, timeout=httpx.Timeout(10), failure_kind='rdap')
        if err:
            logger.debug(f'[RDAP] {err}')
            return {}
        result = {'target': target, 'rdap': data if isinstance(data, dict) else {}, 'source': 'rdap_org'}
        if replay_enabled():
            write_cassette('rdap_org', target, result)
        return result
    except Exception as e:
        logger.debug(f'[RDAP] {e}')
    return {}

class WaybackArchiveAdapter(SourceAdapter):
    """
    Wayback Machine archive discovery adapter.

    Uses ArchiveDiscovery.search_url() to find archived versions of URLs.
    Maps ArchiveResult to NormalizedEntry format.

    Bounded: max 20 results, 10s timeout per source.
    source_type = "wayback_archive", source_tier = TIER_OVERLAY_READY

    Note: This adapter requires a target URL to search archives for.
    Set self.target_url before calling fetch_recent(), or use the
    fetch_archives_for_url() convenience method.
    """
    SOURCE_TYPE = 'wayback_archive'
    SOURCE_TIER = TIER_OVERLAY_READY
    HARD_LIMIT = 20
    TIMEOUT_PER_SOURCE = 10.0
    __slots__ = tuple(('target_url',))

    def __init__(self) -> None:
        self.target_url: str = ''

    @property
    def source_type(self) -> str:
        return self.SOURCE_TYPE

    @property
    def source_tier(self) -> str:
        return self.SOURCE_TIER

    async def fetch_recent(self, limit: int) -> tuple[NormalizedEntry, ...]:
        """
        Fetch archive snapshots for self.target_url.

        Returns empty tuple if no target_url is set or on error.
        """
        if not self.target_url:
            return ()
        return await self.fetch_archives_for_url(self.target_url, limit)

    async def fetch_archives_for_url(self, url: str, limit: int | None=None) -> tuple[NormalizedEntry, ...]:
        """
        Fetch archive snapshots for a specific URL.

        This is the main entry point for archive discovery.
        Use this method directly instead of fetch_recent() when
        you have a specific URL to check.
        """
        if limit is None:
            limit = self.HARD_LIMIT
        limit = min(max(limit, 1), self.HARD_LIMIT)
        from hledac.universal.intel.archive_discovery import ArchiveDiscovery, ArchiveResult
        entries: list[NormalizedEntry] = []
        try:
            discovery = ArchiveDiscovery()
            async with asyncio.timeout(self.TIMEOUT_PER_SOURCE):
                results_dict: dict[str, list[ArchiveResult]] = (await discovery.search_url(url, sources=['wayback', 'archive_today'], limit_per_source=limit),)
            for _source_name, archive_results in results_dict.items():
                for ar in archive_results[:limit]:
                    if ar.available:
                        entry_hash = self._hash_fields(ar.url or '', str(ar.timestamp) if ar.timestamp else '')
                        published_ts: float | None = None
                        if ar.timestamp:
                            try:
                                published_ts = ar.timestamp.timestamp()
                            except Exception as e:
                                logger.debug(f'[WaybackArchive] Timestamp parse error: {e}')
                        entries.append(NormalizedEntry(entry_hash=entry_hash, source_url=ar.url or '', title=ar.title or f'Archive: {ar.url}', body_text=ar.content[:500] if ar.content else '', published_at=published_ts, source_type=self.SOURCE_TYPE, raw_identifiers=(), source_tier=self.SOURCE_TIER, rich_content_available=False))
                        if len(entries) >= limit:
                            return tuple(entries)
        except (TimeoutError, Exception):
            pass
        return tuple(entries)
from hledac.universal.tool_registry import register_task

@register_task('domain_to_pdns')
async def _handle_domain_to_pdns(task, scheduler):
    from hledac.universal.discovery.ti_feed_adapter import query_circl_pdns
    results = await query_circl_pdns(task.ioc_value)
    if not results:
        return
    for r in results:
        await scheduler._buffer_ioc_pivot(r.get('ioc_type', 'domain'), r.get('ioc', ''), 0.75)
    if scheduler._duckdb_store is not None:
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
        findings = []
        ts_now = time.time()
        for r in results:
            finding = CanonicalFinding(finding_id=f"pdns_{r.get('ioc', '')}_{int(ts_now * 1000)}", query=f'passive_dns:{task.ioc_value}', source_type='circl_pdns', confidence=0.75, ts=ts_now, provenance=('circl_pdns', task.ioc_value, r.get('ioc', '')), payload_text=f"{r.get('rrtype', '')} {r.get('rrname', '')} first={r.get('time_first', '')} last={r.get('time_last', '')}")
            findings.append(finding)
        if findings:
            await scheduler._duckdb_store.async_ingest_findings_batch(findings)

@register_task('domain_to_ct')
async def _handle_domain_to_ct(task, scheduler):
    from hledac.universal.discovery.ti_feed_adapter import search_crtsh
    results = await search_crtsh(task.ioc_value)
    sem = asyncio.Semaphore(4)

    async def _buffer_one(r) -> None:
        async with sem:
            await scheduler._buffer_ioc_pivot('domain', r.get('ioc', ''), 0.7)
    if results:
        await safe_gather_ok(*[_buffer_one(r) for r in results], label='ti_feed_adapter:domain_to_ct')

@register_task('ct_live_monitor')
async def _handle_ct_live_monitor(task, scheduler):
    from hledac.universal.discovery.ti_feed_adapter import certstream_monitor
    results = await certstream_monitor(task.ioc_value, duration_s=120)
    sem = asyncio.Semaphore(4)

    async def _buffer_one(r) -> None:
        async with sem:
            await scheduler._buffer_ioc_pivot('domain', r.get('ioc', ''), 0.65)
    if results:
        await safe_gather_ok(*[_buffer_one(r) for r in results], label='ti_feed_adapter:ct_live_monitor')

@register_task('multi_engine_search')
async def _handle_multi_engine_search(task, scheduler):
    from hledac.universal.discovery.duckduckgo_adapter import search_multi_engine
    results = await search_multi_engine(task.ioc_value)
    sem = asyncio.Semaphore(4)

    async def _buffer_one(r) -> None:
        async with sem:
            await scheduler._buffer_ioc_pivot('url', r.get('url', ''), 0.7)
    if results:
        await safe_gather_ok(*[_buffer_one(r) for r in results], label='ti_feed_adapter:multi_engine_search')

@register_task('github_dork')
async def _handle_github_dork(task, scheduler):
    from hledac.universal.discovery.ti_feed_adapter import github_dork
    results = await github_dork(task.ioc_value)
    sem = asyncio.Semaphore(4)

    async def _buffer_one(r) -> None:
        async with sem:
            await scheduler._buffer_ioc_pivot('url', r.get('url', ''), 0.7)
    if results:
        await safe_gather_ok(*[_buffer_one(r) for r in results], label='ti_feed_adapter:github_dork')

@register_task('shodan_enrich')
async def _handle_shodan_enrich(task, scheduler):
    from hledac.universal.intel.shodan_wrapper import search_shodan_to_findings
    findings, raw_results = await search_shodan_to_findings(query=task.ioc_value, limit=10)
    if raw_results:
        await scheduler._buffer_ioc_pivot('ipv4', task.ioc_value, 0.8)
    if findings and scheduler._duckdb_store is not None:
        await scheduler._duckdb_store.async_ingest_findings_batch(findings)

@register_task('rdap_lookup')
async def _handle_rdap_lookup(task, scheduler):
    from hledac.universal.discovery.ti_feed_adapter import query_rdap
    from hledac.universal.runtime.source_finding_bridge import rdap_result_to_findings
    rdap_telemetry: dict[str, Any] = {}
    try:
        r = await query_rdap(task.ioc_value)
        if r:
            findings, rejections, telemetry = rdap_result_to_findings(target=task.ioc_value, rdap_result=r, trigger_confidence=getattr(task, 'confidence', None), max_findings=32)
            rdap_telemetry = telemetry
            rdap_telemetry['rdap_enrichment_attempted'] = True
            rdap_telemetry['rdap_enrichment_findings_built'] = len(findings)
            rdap_telemetry['rdap_enrichment_rejections'] = len(rejections)
            if findings and scheduler._duckdb_store is not None:
                try:
                    stored = await scheduler._duckdb_store.async_ingest_findings_batch(list(findings))
                    rdap_telemetry['rdap_enrichment_findings_stored'] = stored
                except Exception as exc:
                    rdap_telemetry['rdap_enrichment_error'] = str(exc)
            else:
                rdap_telemetry['rdap_enrichment_findings_stored'] = 0
            await scheduler._buffer_ioc_pivot('domain', task.ioc_value, 0.75)
        else:
            rdap_telemetry['rdap_enrichment_attempted'] = True
            rdap_telemetry['rdap_enrichment_findings_built'] = 0
            rdap_telemetry['rdap_enrichment_findings_stored'] = 0
            rdap_telemetry['rdap_enrichment_rejections'] = 1
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        rdap_telemetry.setdefault('rdap_enrichment_error', str(exc))
        rdap_telemetry['rdap_enrichment_attempted'] = True
        rdap_telemetry['rdap_enrichment_findings_built'] = 0
        rdap_telemetry['rdap_enrichment_findings_stored'] = 0
        rdap_telemetry['rdap_enrichment_rejections'] = 0
    if rdap_telemetry and hasattr(scheduler, '_result') and (scheduler._result is not None):
        if not hasattr(scheduler._result, 'rdap_enrichment_attempted'):
            scheduler._result.rdap_enrichment_attempted = 0
            scheduler._result.rdap_enrichment_findings_built = 0
            scheduler._result.rdap_enrichment_findings_stored = 0
            scheduler._result.rdap_enrichment_rejections = 0
            scheduler._result.rdap_enrichment_error = None
        scheduler._result.rdap_enrichment_attempted += rdap_telemetry.get('rdap_enrichment_attempted', 0)
        scheduler._result.rdap_enrichment_findings_built += rdap_telemetry.get('rdap_enrichment_findings_built', 0)
        scheduler._result.rdap_enrichment_findings_stored += rdap_telemetry.get('rdap_enrichment_findings_stored', 0)
        scheduler._result.rdap_enrichment_rejections += rdap_telemetry.get('rdap_enrichment_rejections', 0)
        if rdap_telemetry.get('rdap_enrichment_error'):
            scheduler._result.rdap_enrichment_error = rdap_telemetry['rdap_enrichment_error']

@register_task('ahmia_search')
async def _handle_ahmia_search(task, scheduler):
    from hledac.universal.discovery.ti_feed_adapter import search_ahmia
    results = await search_ahmia(task.ioc_value, use_onion=False)
    sem = asyncio.Semaphore(4)

    async def _buffer_one(r) -> None:
        async with sem:
            await scheduler._buffer_ioc_pivot('url', r.get('url', ''), 0.65)
    if results:
        await safe_gather_ok(*[_buffer_one(r) for r in results], label='ti_feed_adapter:ahmia_search')

@register_task('paste_keyword_search')
async def _handle_paste_keyword_search(task, scheduler):
    from hledac.universal.discovery.ti_feed_adapter import scrape_pastebin_for_keyword
    results = await scrape_pastebin_for_keyword(task.ioc_value)
    sem = asyncio.Semaphore(4)

    async def _buffer_one(r) -> None:
        async with sem:
            await scheduler._buffer_ioc_pivot('url', r.get('url', ''), 0.6)
    if results:
        await safe_gather_ok(*[_buffer_one(r) for r in results], label='ti_feed_adapter:paste_keyword_search')

@register_task('wayback_search')
async def _handle_wayback_search(task, scheduler):
    from hledac.universal.discovery.duckduckgo_adapter import _search_wayback_cdx
    results = await _search_wayback_cdx(task.ioc_value)
    sem = asyncio.Semaphore(4)

    async def _buffer_one(r) -> None:
        async with sem:
            await scheduler._buffer_ioc_pivot('url', r.get('url', ''), 0.65)
    if results:
        await safe_gather_ok(*[_buffer_one(r) for r in results], label='ti_feed_adapter:wayback_search')

@register_task('commoncrawl_search')
async def _handle_commoncrawl_search(task, scheduler):
    from hledac.universal.discovery.duckduckgo_adapter import _search_commoncrawl_cdx
    results = await _search_commoncrawl_cdx(task.ioc_value)
    sem = asyncio.Semaphore(4)

    async def _buffer_one(r) -> None:
        async with sem:
            await scheduler._buffer_ioc_pivot('url', r.get('url', ''), 0.65)
    if results:
        await safe_gather_ok(*[_buffer_one(r) for r in results], label='ti_feed_adapter:commoncrawl_search')

def _register_structured_adapters() -> None:
    """Register the structured TI adapters. Called once at module load."""
    from hledac.universal.discovery.source_registry import register_source_adapter
    try:
        register_source_adapter(NvdApiAdapter.SOURCE_TYPE, NvdApiAdapter)
    except ValueError:
        pass
    try:
        register_source_adapter(CisaKevAdapter.SOURCE_TYPE, CisaKevAdapter)
    except ValueError:
        pass
_register_structured_adapters()

async def fetch_i2p_eepsite(url: str, proxy_url: str='http://127.0.0.1:4444') -> dict:
    """
    Fetch I2P eepsite přes lokální HTTP proxy (port 4444).
    Graceful fallback — pokud proxy neběží, vrátí error dict (nekrachne).
    Timeout 60s — I2P je inherentně pomalé.
    M1 cap: content ořezán na 50KB.

    RC-5: Uses shared session from session_runtime (connection pooling).
    """
    try:
        session = await async_get_httpx_session()
        resp = await session.get(url, timeout=httpx.Timeout(60), headers={'User-Agent': 'Mozilla/5.0 (compatible; research)'})
        content = resp.text
        return {'url': url, 'status': resp.status_code, 'content': content[:50000], 'source': 'i2p_eepsite', 'error': None}
    except Exception as e:
        logger.debug(f'[I2P] {e}')
        return {'url': url, 'status': 0, 'content': '', 'source': 'i2p_eepsite', 'error': str(e)}

async def search_i2p_directory(query: str, max_results: int=20) -> list[dict]:
    """
    I2P eepsite discovery přes stats.i2p directory.
    Vrátí seznam {url, title, source} dostupných eepsites.
    Pokud proxy neběží → vrátí [] bez výjimky.
    """
    import re as _re
    page = await fetch_i2p_eepsite('http://stats.i2p/cgi-bin/netstats.cgi')
    if page['error'] or not page['content']:
        return []
    links = _re.findall('href="(http://[^\\s"]+\\.i2p[^"]*)"', page['content'])
    return [{'url': link, 'title': link, 'source': 'i2p_directory'} for link in links[:max_results]]

@register_task('i2p_eepsite_fetch')
async def _handle_i2p_eepsite_fetch(task, scheduler):
    """Fetch I2P eepsite nebo search I2P directory."""
    ioc = task.ioc_value
    if '.i2p' in ioc:
        url = ioc if ioc.startswith('http') else f'http://{ioc}'
        result = await fetch_i2p_eepsite(url)
        if result['status'] > 0:
            await scheduler._buffer_ioc_pivot('url', url, 0.6)
    else:
        results = await search_i2p_directory(ioc)
        sem = asyncio.Semaphore(4)

        async def _buffer_one(r) -> None:
            async with sem:
                await scheduler._buffer_ioc_pivot('url', r['url'], 0.55)
        if results:
            await safe_gather_ok(*[_buffer_one(r) for r in results], label='ti_feed_adapter:i2p_eepsite_fetch')
import re as _cid_re_mod
_CID_PATTERN = _cid_re_mod.compile('\\b(Qm[1-9A-HJ-NP-Za-km-z]{44}|b[a-z2-7]{58})\\b')
_IPFS_GATEWAYS = ['https://ipfs.io/ipfs/', 'https://cloudflare-ipfs.com/ipfs/', 'https://gateway.pinata.cloud/ipfs/']

async def fetch_ipfs_cid(cid: str) -> dict:
    """
    Fetch IPFS content přes CID.
    Pokus 1: lokální daemon (127.0.0.1:5001/api/v0/cat).
    Pokus 2: public gateways (ipfs.io, cloudflare, pinata).
    M1 cap: content ořezán na 100KB.

    RC-5: Uses shared session from session_runtime (connection pooling).
    """
    session = await async_get_httpx_session()
    try:
        resp = await session.post('http://127.0.0.1:5001/api/v0/cat', params={'arg': cid}, timeout=httpx.Timeout(5))
        if resp.status_code == 200:
            data = resp.content
            return {'cid': cid, 'source': 'ipfs_local_daemon', 'content': data[:100000].decode('utf-8', errors='replace'), 'size': len(data), 'error': None}
    except Exception as e:
        logger.debug(f'[IPFS] Local daemon fetch failed for CID {cid}: {e}')
    for gw in _IPFS_GATEWAYS:
        try:
            resp = await session.get(f'{gw}{cid}', timeout=httpx.Timeout(30))
            if resp.status_code == 200:
                data = resp.content
                return {'cid': cid, 'source': gw, 'content': data[:100000].decode('utf-8', errors='replace'), 'size': len(data), 'error': None}
        except Exception as e:
            logger.debug(f'[IPFS] Gateway fetch failed for CID {cid} via {gw}: {e}')
    return {'cid': cid, 'source': None, 'content': '', 'size': 0, 'error': 'IPFS nedostupný (daemon + všechny gateways selhaly)'}

async def search_ipfs(query: str, max_results: int=10) -> list[dict]:
    """ipfs-search.com REST API — index veřejného IPFS obsahu."""
    results = []
    try:
        s = await async_get_httpx_session()
        resp = await s.get('https://api.ipfs-search.com/v1/search', params={'q': query, 'type': 'any'}, timeout=httpx.Timeout(15))
        if resp.status_code == 200:
            data = resp.json()
            for hit in data.get('hits', {}).get('hits', [])[:max_results]:
                results.append({'cid': hit.get('_id', ''), 'title': hit.get('_source', {}).get('title', ''), 'score': hit.get('_score', 0), 'source': 'ipfs_search'})
    except Exception as e:
        logger.debug(f'[IPFS search] {e}')
    return results

@register_task('ipfs_fetch')
async def _handle_ipfs_fetch(task, scheduler):
    """Fetch IPFS content — CID nebo keyword search.

    Canonical persistence: IPFS content is persisted as CanonicalFinding
    with source_type='ipfs'. Pivoting remains as optional side effect.

    Provenance tuple: (cid, gateway, query) for CID fetches,
                      (cid, 'ipfs_search', query) for keyword searches.
    """
    ioc = task.ioc_value
    m = _CID_PATTERN.search(ioc)
    ts_now = time.time()
    findings = []
    if m:
        cid = m.group(1)
        result = await fetch_ipfs_cid(cid)
        content = result.get('content', '')
        if content:
            await scheduler._buffer_ioc_pivot('url', f'ipfs://{cid}', 0.65)
        if scheduler._duckdb_store is not None and content:
            from hledac.universal.knowledge.duckdb_store import CanonicalFinding
            finding = CanonicalFinding(finding_id=f'ipfs_{cid}_{int(ts_now * 1000)}', query=f'ipfs_fetch:{ioc}', source_type='ipfs', confidence=0.75, ts=ts_now, provenance=(cid, result.get('source', 'unknown'), ioc), payload_text=content[:2000] if content else None)
            findings.append(finding)
    else:
        search_results = await search_ipfs(ioc)
        sem = asyncio.Semaphore(4)

        async def _process_one(r) -> None:
            cid = r.get('cid', '')
            if not cid:
                return
            await scheduler._buffer_ioc_pivot('url', f'ipfs://{cid}', 0.55)
            if scheduler._duckdb_store is not None:
                from hledac.universal.knowledge.duckdb_store import CanonicalFinding
                finding = CanonicalFinding(finding_id=f'ipfs_search_{cid}_{int(ts_now * 1000)}', query=f'ipfs_search:{ioc}', source_type='ipfs', confidence=0.65, ts=ts_now, provenance=(cid, 'ipfs_search', ioc), payload_text=r.get('title', '')[:500] if r.get('title') else None)
                findings.append(finding)
        if search_results:
            await safe_gather_ok(*[_process_one(r) for r in search_results], label='ti_feed_adapter:ipfs_fetch')
    if findings and scheduler._duckdb_store is not None:
        try:
            await scheduler._duckdb_store.async_ingest_findings_batch(findings)
        except Exception as e:
            logger.debug(f'IPFS canonical persist failed: {e}')

async def fetch_gopher(host: str, selector: str='/', port: int=70) -> dict:
    """
    Gopher protocol client — RFC 1436, raw async TCP.
    Zero extra deps — asyncio.open_connection nativně na M1.
    M1 cap: content ořezán na 500KB (Gopher nemá binární payload limit).
    Timeout 15s.
    """
    try:
        async with asyncio.timeout(15.0):
            reader, writer = await asyncio.open_connection(host, port)
        writer.write(f'{selector}\r\n'.encode())
        await writer.drain()
        data = bytearray()
        while True:
            async with asyncio.timeout(15.0):
                chunk = await reader.read(8192)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 500000:
                break
        writer.close()
        try:
            async with asyncio.timeout(2.0):
                await writer.wait_closed()
        except Exception as e:
            logger.debug(f'[Gopher] Wait closed failed for {host}{selector}: {e}')
        content = data.decode('utf-8', errors='replace')
        return {'host': host, 'selector': selector, 'content': content[:10000], 'items': _parse_gophermap(content), 'source': 'gopher', 'error': None}
    except TimeoutError:
        return {'host': host, 'selector': selector, 'content': '', 'items': [], 'source': 'gopher', 'error': 'timeout'}
    except Exception as e:
        logger.debug(f'[Gopher] {host}{selector}: {e}')
        return {'host': host, 'selector': selector, 'content': '', 'items': [], 'source': 'gopher', 'error': str(e)}

def _parse_gophermap(content: str) -> list[dict]:
    """Parsuje Gopher menu (tab-separated RFC 1436 format)."""
    items = []
    for line in content.split('\n'):
        line = line.rstrip('\r')
        if not line.strip() or line.strip() == '.':
            continue
        item_type = line[0]
        parts = line[1:].split('\t')
        if len(parts) >= 3:
            items.append({'type': item_type, 'text': parts[0].strip(), 'selector': parts[1] if len(parts) > 1 else '/', 'host': parts[2] if len(parts) > 2 else '', 'port': int(parts[3]) if len(parts) > 3 and parts[3].strip().isdigit() else 70})
    return items

@register_task('gopher_fetch')
async def _handle_gopher_fetch(task, scheduler):
    """Gopher fetch — floodgap.com Veronica-2 search nebo přímý selector."""
    from urllib.parse import urlparse
    ioc = task.ioc_value
    if ioc.startswith('gopher://'):
        p = urlparse(ioc)
        result = await fetch_gopher(p.hostname or 'gopher.floodgap.com', p.path or '/', p.port or 70)
    else:
        result = await fetch_gopher('gopher.floodgap.com', f"/v2/vs?query={ioc.replace(' ', '+')}")
    sem = asyncio.Semaphore(4)

    async def _buffer_one(item) -> None:
        if item.get('host') and item.get('type') in ('1', '0', '7'):
            gopher_url = f"gopher://{item['host']}:{item['port']}{item['selector']}"
            async with sem:
                await scheduler._buffer_ioc_pivot('url', gopher_url, 0.5)
    items = result.get('items', [])
    if items:
        await safe_gather_ok(*[_buffer_one(item) for item in items], label='ti_feed_adapter:gopher_fetch')
import re as _ip_re_mod
from hledac.universal.utils.async_helpers import safe_gather_ok
_IP_PATTERN = _ip_re_mod.compile('^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$')

def _is_valid_ip(s: str) -> bool:
    return bool(_IP_PATTERN.match(s))

async def query_ripe_stat_asn(ip: str) -> dict:
    """
    RIPE Stat REST API — ASN a prefix pro IP adresu.
    Free, no API key, M1 native.
    """
    try:
        s = await async_get_httpx_session()
        resp = await s.get('https://stat.ripe.net/data/prefix-overview/data.json', params={'resource': ip}, timeout=httpx.Timeout(15))
        if resp.status_code == 200:
            data = (await resp.json()).get('data', {})
            asns = data.get('asns', [])
            return {'ip': ip, 'asn': asns[0].get('asn') if asns else None, 'holder': asns[0].get('holder') if asns else None, 'prefix': data.get('resource', ip), 'source': 'ripe_stat'}
    except Exception as e:
        logger.debug(f'[RIPE Stat] {e}')
    return {'ip': ip, 'asn': None, 'holder': None, 'source': 'ripe_stat', 'error': 'RIPE Stat nedostupný'}

async def query_team_cymru_asn(ip: str) -> dict:
    """
    Team Cymru ASN lookup přes DNS TXT record.
    Pokus 1: aiodns (pokud nainstalován).
    Pokus 2: nslookup subprocess — vždy dostupný na macOS.
    Free, no API key.
    """
    import re as _re
    reversed_ip = '.'.join(reversed(ip.split('.')))
    query_name = f'{reversed_ip}.origin.asn.cymru.com'
    try:
        import aiodns
        resolver = aiodns.DNSResolver()
        result = await resolver.query(query_name, 'TXT')
        txt = result[0].text if result else ''
        parts = txt.split('|')
        return {'ip': ip, 'asn': parts[0].strip() if parts else None, 'country': parts[2].strip() if len(parts) > 2 else None, 'registry': parts[3].strip() if len(parts) > 3 else None, 'source': 'team_cymru_aiodns'}
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f'[Cymru aiodns] {e}')
    try:
        proc = await asyncio.create_subprocess_exec('nslookup', '-type=TXT', query_name, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        async with asyncio.timeout(5.0):
            stdout, _ = await proc.communicate()
        output = stdout.decode()
        asn_match = _re.search('"(\\d+)\\s*\\|', output)
        return {'ip': ip, 'asn': f'AS{asn_match.group(1)}' if asn_match else None, 'source': 'team_cymru_nslookup'}
    except Exception as e:
        logger.debug(f'[Cymru nslookup] {e}')
    return {'ip': ip, 'asn': None, 'source': 'team_cymru', 'error': 'lookup failed'}

@register_task('bgp_asn_lookup')
async def _handle_bgp_asn_lookup(task, scheduler):
    """BGP ASN lookup pro IP — RIPE Stat + Team Cymru."""
    ioc = task.ioc_value
    if not _is_valid_ip(ioc):
        return
    ripe, cymru = await safe_gather_ok(query_ripe_stat_asn(ioc), query_team_cymru_asn(ioc), label='ti_feed_adapter:1871')
    if ripe.get('asn') or cymru.get('asn'):
        await scheduler._buffer_ioc_pivot('ipv4', ioc, 0.8)

async def query_bgp_routing_history(resource: str, max_rows: int=20) -> dict:
    """
    RIPE Stat BGP routing history — prefix nebo ASN.
    Ukazuje historické routing changes — užitečné pro infrastructure tracking.
    """
    try:
        s = await async_get_httpx_session()
        resp = await s.get('https://stat.ripe.net/data/routing-history/data.json', params={'resource': resource, 'max_rows': max_rows}, timeout=httpx.Timeout(15))
        if resp.status_code == 200:
            data = resp.json()
            return {'resource': resource, 'history': data.get('data', {}).get('by_origin', [])[:max_rows], 'source': 'ripe_bgp_history', 'error': None}
    except Exception as e:
        logger.debug(f'[BGP history] {e}')
    return {'resource': resource, 'history': [], 'source': 'ripe_bgp_history', 'error': 'RIPE BGP history nedostupná'}

@register_task('bgp_routing_history')
async def _handle_bgp_routing_history(task, scheduler):
    """BGP routing history pro prefix nebo ASN číslo."""
    result = await query_bgp_routing_history(task.ioc_value)
    if not result.get('history'):
        return
    await scheduler._buffer_ioc_pivot('ipv4', task.ioc_value, 0.7)
    try:
        from hledac.universal.network.bgp_monitor import monitor_bgp
    except ImportError:
        return
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    findings: list[CanonicalFinding] = []
    ts_now = time.time()

    def bgp_callback(timestamp: float, prefix: str, as_path: str, event_type: str):
        pass
    try:
        events = await monitor_bgp(prefixes=[task.ioc_value], callback=bgp_callback, duration_seconds=30)
    except Exception as e:
        logger.debug(f'[BGP routing history] monitor_bgp failed for {task.ioc_value}: {e}')
        return
    for event in events:
        finding = CanonicalFinding(finding_id=f"bgp_{event['prefix']}_{event['timestamp']}_{int(ts_now * 1000)}", query=f'bgp_monitor:{task.ioc_value}', source_type='bgp_monitor', confidence=0.75, ts=event['timestamp'], provenance=('bgp_monitor', task.ioc_value, event['prefix'], event['as_path'], event['event_type']), payload_text=f"prefix={event['prefix']} as_path={event['as_path']} event={event['event_type']}")
        findings.append(finding)
    if findings and scheduler._duckdb_store is not None:
        await scheduler._duckdb_store.async_ingest_findings_batch(findings)

async def fetch_malwarebazaar_recent(tag: str | None=None, max_items: int=25) -> list[dict]:
    """
    MalwareBazaar — recent malware sample feed.
    Public API, no key required. abuse.ch infrastruktura.
    Vrátí hash, malware family, tags, first_seen.
    """
    payload: dict = {'query': 'get_recent', 'selector': 'time'}
    if tag:
        payload = {'query': 'get_taginfo', 'tag': tag, 'limit': max_items}
    try:
        s = await async_get_httpx_session()
        resp, err = await checked_httpx_post(s, 'https://mb-api.abuse.ch/api/v1/', json=payload, timeout=httpx.Timeout(20), failure_kind='malwarebazaar_recent')
        if err:
            logger.debug(f'[MalwareBazaar] {err}')
            return []
        data = await resp.json()
        return [{'sha256': e.get('sha256_hash', ''), 'malware_family': e.get('signature', ''), 'file_type': e.get('file_type', ''), 'first_seen': e.get('first_seen', ''), 'tags': e.get('tags', []), 'ioc': e.get('sha256_hash', ''), 'ioc_type': 'sha256', 'title': f"MalwareBazaar: {e.get('signature', '?')}", 'source': 'malwarebazaar'} for e in data.get('data', [])[:max_items]]
    except Exception as e:
        logger.debug(f'[MalwareBazaar] {e}')
    return []

@register_task('malwarebazaar_search')
async def _handle_malwarebazaar_search(task, scheduler):
    """MalwareBazaar malware sample lookup — hash nebo tag."""
    ioc = task.ioc_value
    if len(ioc) == 64 and all((c in '0123456789abcdefABCDEF' for c in ioc)):
        try:
            s = await async_get_httpx_session()
            resp, err = await checked_httpx_post(s, 'https://mb-api.abuse.ch/api/v1/', json={'query': 'get_info', 'hash': ioc}, timeout=httpx.Timeout(15), failure_kind='malwarebazaar_info')
            if err:
                logger.debug(f'[MalwareBazaar hash] {err}')
                return
            data = await resp.json()
            if data.get('data'):
                await scheduler._buffer_ioc_pivot('sha256', ioc, 0.85)
        except Exception as e:
            logger.debug(f'[MalwareBazaar hash] {e}')
    else:
        results = await fetch_malwarebazaar_recent(tag=ioc)
        sem = asyncio.Semaphore(4)

        async def _buffer_one(item) -> None:
            async with sem:
                await scheduler._buffer_ioc_pivot('sha256', item['sha256'], 0.75)
        if results:
            await safe_gather_ok(*[_buffer_one(item) for item in results], label='ti_feed_adapter:malwarebazaar_search')

@register_task('cve_to_github')
async def _handle_cve_to_github(task, scheduler):
    """
    Sprint 8TB-FIX: GitHub CVE PoC search via GitHubCodeSearchClient.

    Searches GitHub code for CVE proof-of-concept / exploit samples.
    Uses the dedicated GitHubCodeSearchClient.search_cve() which is different
    from github_dork (generic code search) — search_cve uses the
    `language:Python OR language:C exploit OR poc` query template scoped to a CVE.
    """
    from hledac.universal.intel.exposure_clients import GitHubCodeSearchClient
    from hledac.universal.paths import CACHE_ROOT
    try:
        cache_dir = CACHE_ROOT / 'github_cve'
        cache_dir.mkdir(parents=True, exist_ok=True)
        client = GitHubCodeSearchClient(cache_dir=cache_dir)
    except Exception:
        return
    try:
        s = await async_get_httpx_session()
        results = await client.search_cve(task.ioc_value, s)
        await client.close()
        sem = asyncio.Semaphore(4)

        async def _buffer_one(r) -> None:
            async with sem:
                await scheduler._buffer_ioc_pivot('url', r.get('url', ''), 0.7)
        if results:
            await safe_gather_ok(*[_buffer_one(r) for r in results], label='ti_feed_adapter:cve_to_github')
    except Exception:
        pass