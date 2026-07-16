"""
ThreatIntelligence — IOC lookup and threat analysis for OSINT findings.

Provides:
- IOC (Indicator of Compromise) lookup against local threat feeds
- Threat level assessment for analyzed entities
- Graceful degradation: returns empty results if no feeds available

Interface expected by security_coordinator.py:
- __init__(*args, **kwargs)
- async initialize()
- async analyze_threats(context, priority_level, security_level) -> dict
- async cleanup()
"""
import logging
import os
import re
from pathlib import Path
from typing import Any
import httpx
logger = logging.getLogger(__name__)

def _looks_like_ip(s: str) -> bool:
    return bool(re.match('^\\d{1,3}(\\.\\d{1,3}){3}$', s.strip()))

def _ioc_type_from_value(val: str) -> str:
    """Classify IOC type from value string."""
    if _looks_like_ip(val):
        return 'ip'
    if re.match('^[a-f0-9]{32,}$', val, re.IGNORECASE):
        return 'hash'
    if '://' in val or val.startswith('http'):
        return 'url'
    if '.' in val:
        return 'domain'
    return 'unknown'
_STATIC_IOC_PATTERNS: dict[str, list[str]] = {'domain': ['malware-c2.net', 'phishing-site.io', 'suspicious-cdn.com'], 'ip': ['185.220.101.0/24', '192.0.2.0/24'], 'hash': [], 'url': ['http://malware-download.com/payload.exe', 'https://phishing-site.io/steal']}

class ThreatIntelligence:
    """
    Threat intelligence analysis for OSINT findings.

    Uses local IOC feeds for threat detection:
    - Loads from config/feeds/threat_feeds.json if available
    - Falls back to static IOC patterns
    - Returns typed empty results (not exceptions) for graceful degradation

    Attributes:
        _initialized: Whether async initialize() completed
        _iocs: Dict of loaded IOCs by type
        _feed_source: Source of loaded IOCs ("file", "static")
    """
    __slots__ = tuple(('_feed_source', '_initialized', '_iocs', '_threat_count'))

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        """Initialize without parameters — async initialize() loads feeds."""
        self._initialized = False
        self._iocs: dict[str, set[str]] = {'domain': set(), 'ip': set(), 'hash': set(), 'url': set()}
        self._feed_source: str = 'none'
        self._threat_count: int = 0

    async def initialize(self) -> None:
        """
        Load threat intelligence feeds.

        Attempts to load from:
        1. config/feeds/threat_feeds.json
        2. Falls back to static IOC patterns

        Logs WARNING if no feeds loaded (not error — graceful degradation).
        """
        feed_path = Path('config/feeds/threat_feeds.json')
        if not feed_path.is_absolute():
            feed_path = Path(__file__).parent.parent.parent / feed_path
        if feed_path.exists():
            try:
                import json
                with open(feed_path) as f:
                    data = json.load(f)
                for ioc_type, iocs in data.get('iocs', {}).items():
                    if ioc_type in self._iocs:
                        self._iocs[ioc_type] = set(iocs)
                self._feed_source = 'file'
                logger.info(f'ThreatIntelligence: Loaded IOCs from {feed_path}')
            except Exception as e:
                logger.warning(f'ThreatIntelligence: Failed to load feeds: {e}')
                self._load_static_iocs()
        else:
            self._load_static_iocs()
        self._initialized = True

    def _load_static_iocs(self) -> None:
        """Load static IOC patterns as fallback."""
        for ioc_type, patterns in _STATIC_IOC_PATTERNS.items():
            self._iocs[ioc_type] = set(patterns)
        self._feed_source = 'static'
        total = sum((len(v) for v in self._iocs.values()))
        logger.warning(f'ThreatIntelligence: Using static IOCs ({total} patterns)')

    async def analyze_threats(self, context: dict[str, Any], priority_level: int=5, security_level: int=3) -> dict[str, Any]:
        """
        Analyze context for threat indicators.

        Args:
            context: Dict with keys like 'query', 'findings', 'entities'
            priority_level: 1-10 priority (higher = more urgent)
            security_level: 1-4 security level (higher = more thorough)

        Returns:
            dict with keys:
                - threats: list of detected threat dicts
                - threat_level: float 0.0-1.0
                - analyzed_count: int
                - ioc_matches: int
        """
        if not self._initialized:
            await self.initialize()
        from intelligence.kill_chain_tagger import ioc_to_technique_ids
        threats: list[dict[str, Any]] = []
        stats = {'total': 0, 'high': 0, 'medium': 0, 'low': 0}
        findings = context.get('findings', [])
        if isinstance(findings, str):
            findings = [findings]
        for finding in findings:
            if not finding:
                continue
            iocs = finding.get('iocs') or []
            for ioc in iocs:
                ioc_val = str(ioc.get('value', ioc) if isinstance(ioc, dict) else ioc)
                ioc_type = _ioc_type_from_value(ioc_val)
                techniques = ioc_to_technique_ids(ioc_type, ioc_val)
                if techniques:
                    threats.append({'type': 'kill_chain_match', 'ioc': ioc_val, 'ioc_type': ioc_type, 'techniques': techniques, 'source': 'kill_chain_tagger', 'severity': 'medium'})
            if os.getenv('HLEDAC_ENABLE_GREYNOISE'):
                try:
                    from intelligence.greynoise_lane import query_greynoise_ip
                    for ioc in iocs:
                        ioc_val = str(ioc.get('value', ioc) if isinstance(ioc, dict) else ioc)
                        if _looks_like_ip(ioc_val):
                            _, raw = await query_greynoise_ip(ioc_val, use_community=True)
                            if raw.get('classification') == 'malicious':
                                threats.append({'type': 'greynoise_malicious_ip', 'ioc': ioc_val, 'source': 'greynoise', 'severity': 'high', 'detail': raw})
                except Exception:
                    pass
        for t in threats:
            stats['total'] += 1
            stats[t.get('severity', 'low')] += 1
        analyzed = len(findings)
        threat_level = min(1.0, stats['total'] / max(analyzed, 1) * (priority_level / 5) * (security_level / 3))
        return {'threats': threats, 'threat_level': threat_level, 'analyzed_count': analyzed, 'ioc_matches': stats['total'], 'stats': stats, 'feed_source': self._feed_source}

    def _classify_entity(self, entity: str) -> str:
        """Classify entity type based on pattern."""
        if re.match('^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$', entity):
            return 'ip'
        if '://' in entity or entity.startswith('http'):
            return 'url'
        if re.match('^[a-f0-9]{32,}$', entity, re.IGNORECASE):
            return 'hash'
        if '.' in entity and (not entity.startswith('http')):
            return 'domain'
        return 'unknown'

    def _check_ioc_match(self, entity: str, ioc_type: str) -> bool:
        """Check if entity matches any IOC of given type."""
        if ioc_type not in self._iocs:
            return False
        iocs = self._iocs[ioc_type]
        if not iocs:
            return False
        if entity in iocs:
            return True
        if ioc_type == 'domain':
            for ioc in iocs:
                if entity.endswith(ioc) or ioc in entity:
                    return True
        return False

    @staticmethod
    def _rdap_find_base(ip: str, bootstrap: dict) -> str:
        """Find correct RDAP base URL from IANA bootstrap data."""
        import ipaddress
        try:
            addr = ipaddress.ip_address(ip)
            for service in bootstrap.get('services', []):
                cidrs, urls = (service[0], service[1])
                for cidr in cidrs:
                    if addr in ipaddress.ip_network(cidr, strict=False):
                        return urls[0].rstrip('/')
        except Exception:
            pass
        return 'https://rdap.arin.net/registry'

    async def lookup_ioc(self, ioc: str) -> dict[str, Any]:
        """
        Direct IOC lookup.

        Args:
            ioc: Indicator to look up (domain, IP, hash, URL)

        Returns:
            dict with keys:
                - found: bool
                - type: str
                - severity: str or None
                - source: str
        """
        if not self._initialized:
            await self.initialize()
        from intelligence.kill_chain_tagger import ioc_to_technique_ids
        ioc_str = str(ioc).strip()
        ioc_type = _ioc_type_from_value(ioc_str)
        techniques = ioc_to_technique_ids(ioc_type, ioc_str)
        result: dict[str, Any] = {'found': False, 'type': ioc_type, 'severity': 'low', 'sources': []}
        if techniques:
            result.update({'found': True, 'severity': 'medium', 'techniques': techniques, 'sources': ['kill_chain_tagger']})
        if os.getenv('HLEDAC_ENABLE_GREYNOISE') and _looks_like_ip(ioc_str):
            try:
                from intelligence.greynoise_lane import query_greynoise_ip
                _, raw = await query_greynoise_ip(ioc_str, use_community=True)
                if raw.get('classification') in ('malicious', 'suspicious'):
                    result.update({'found': True, 'severity': 'high', 'classification': raw['classification'], 'sources': result['sources'] + ['greynoise']})
            except Exception:
                pass
        if _looks_like_ip(ioc_str):
            try:
                from network.session_runtime import async_get_httpx_session
                session = await async_get_httpx_session()
                async with session.get('https://data.iana.org/rdap/ipv4.json', timeout=httpx.Timeout(total=4)) as boot_resp:
                    if boot_resp.status_code == 200:
                        bootstrap = boot_resp.json()
                        rdap_base = self._rdap_find_base(ioc_str, bootstrap)
                    else:
                        rdap_base = 'https://rdap.arin.net/registry'
                async with session.get(f'{rdap_base}/ip/{ioc_str}', timeout=httpx.Timeout(total=5)) as resp:
                    if resp.status_code == 200:
                        data = resp.json()
                        org = data.get('name', '')
                        country = data.get('country', '')
                        asn_info = [e.get('handle', '') for e in data.get('entities', [])]
                        result.update({'found': True, 'sources': result['sources'] + ['rdap'], 'org': org, 'country': country, 'asn_entities': asn_info[:3]})
            except Exception:
                pass
        if _looks_like_ip(ioc_str) and os.getenv('HLEDAC_ENABLE_BGPTOOLS', '1') != '0':
            try:
                from network.session_runtime import async_get_httpx_session
                session = await async_get_httpx_session()
                async with session.get(f'https://bgp.tools/prefix/{ioc_str}/json', headers={'User-Agent': 'hledac-security-research/1.0'}, timeout=httpx.Timeout(total=4)) as resp:
                    if resp.status_code == 200:
                        data = resp.json()
                        asn = data.get('asn', '')
                        pfx = data.get('prefix', '')
                        name = data.get('name', '')
                        result.update({'sources': result['sources'] + ['bgptools'], 'asn': asn, 'prefix': pfx, 'asn_name': name})
            except Exception:
                pass
        return result

    async def cleanup(self) -> None:
        """Cleanup resources — no-op for local IOC lookup."""
        self._iocs = {k: set() for k in self._iocs}
        self._initialized = False
        logger.debug('ThreatIntelligence: Cleanup complete')
__all__ = ['ThreatIntelligence']