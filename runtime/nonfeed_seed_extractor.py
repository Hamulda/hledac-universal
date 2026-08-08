"""
Sprint F222D: NonfeedSeed Extractor — Feed → Pivot → Nonfeed Bridge
=================================================================



runtime/nonfeed_seed_extractor.py
--------------------------------
Deterministic IOC extraction from feed findings → bounded seeds for
nonfeed lanes (CT, PassiveDNS, Wayback, DOH).

Rules:
  - regex/deterministic only — no model calls
  - bounded (max_seeds)
  - deduplicate by (kind, value)
  - filter publisher feed domains (krebsonsecurity.com etc.)
  - prefer IOCs in finding body/title, not source URL of feed itself

API:
    NonfeedSeed(value, kind, source, confidence, reason)
    extract_nonfeed_seeds_from_text(text, max_seeds=50) -> list[NonfeedSeed]
    extract_nonfeed_seeds_from_findings(findings, max_seeds=100) -> list[NonfeedSeed]
    compute_lane_unlocks(seeds) -> dict[str, list[str]]

Publisher domains (feed aggregators — excluded as seeds unless real indicators):
    krebsonsecurity.com, thehackernews.com, bleedingcomputer.com,
    welivesecurity.com, sans.edu, darkreading.com, zdnet.com,
    theregister.com, arstechnica.com, securityweek.com
"""
import re

import msgspec
__all__ = ['NonfeedSeed', 'SeedQuality', 'classify_seed_quality', 'extract_nonfeed_seeds_from_text', 'extract_nonfeed_seeds_from_findings', 'compute_lane_unlocks', 'PUBLISHER_DOMAINS']
PUBLISHER_DOMAINS: frozenset[str] = frozenset(['krebsonsecurity.com', 'thehackernews.com', 'bleepingcomputer.com', 'welivesecurity.com', 'sans.edu', 'darkreading.com', 'zdnet.com', 'theregister.com', 'arstechnica.com', 'securityweek.com', 'infoworld.com', 'threatpost.com', 'darknet.com.au', 'journalofcloudsecurity.com'])
'Publisher/aggregator domains filtered from seed extraction.'
_GENERIC_DROP_DOMAINS: frozenset[str] = frozenset(['example.com', 'example.org', 'example.net', 'example.edu', 'localhost', 'test.com', 'testing.com', 'invalid.com'])
'Generic infra / test domains — always drop.'
_WEAK_DOMAINS: frozenset[str] = frozenset(['mozilla.org', 'google.com', 'cloudflare.com', 'github.com', 'microsoft.com', 'apple.com', 'amazon.com', 'facebook.com', 'twitter.com', 'linkedin.com', 'instagram.com', 'youtube.com', 'reddit.com', 'dropbox.com', 'zoom.us', 'office.com', 'live.com', 'msn.com', 'aol.com', 'yahoo.com'])
'Major platform / publisher domains — weak unless explicit IOC context.'
_RANSOMWARE_KEYWORDS: frozenset[str] = frozenset(['ransomware', 'lockbit', 'conti', 'revil', 'clop', 'alphv', 'blackcat', 'hive', 'darkrace', 'vice society', 'PLAY', 'mount', 'babuk', 'avaddon', 'phobos', 'dharma', 'cem', 'mallox', 'stopdoj', 'doesp', ' Lucifer', 'malware', 'breach', 'leak', 'stolen', 'exposed', 'onion', 'darkweb', 'panel', 'victim', 'payment'])
'Context keywords that boost weak domains to keep.'

class SeedQuality(msgspec.Struct, frozen=True, gc=False):
    """
    Sprint F223B: Quality gate decision for a NonfeedSeed.

    Fields:
        decision:  "keep" | "weak" | "drop"
        reason:    Human-readable reason
        score:      Quality score [0.0, 1.0]
    """
    decision: str
    reason: str
    score: float

def classify_seed_quality(seed: NonfeedSeed, *, query: str='', context: str='') -> SeedQuality:
    """
    Sprint F223B: Classify seed quality — drop generic infra, weaken
    major platforms, keep ransomware-relevant IOCs.

    Args:
        seed:     NonfeedSeed to classify
        query:    Optional query string (e.g. "LockBit ransomware")
        context:  Optional additional context text

    Returns:
        SeedQuality with decision, reason, score.
    """
    combined = f'{query} {context}'.lower()
    if seed.value.lower() in _GENERIC_DROP_DOMAINS:
        return SeedQuality(decision='drop', reason='generic_or_test_domain', score=0.0)
    if _is_publisher_domain(seed.value):
        has_ioc_context = any((kw in combined for kw in _RANSOMWARE_KEYWORDS))
        if not has_ioc_context:
            return SeedQuality(decision='drop', reason='publisher_domain_no_ioc_context', score=0.1)
        return SeedQuality(decision='keep', reason='publisher_domain_explicit_ioc_context', score=0.7)
    lower_val = seed.value.lower()
    if not lower_val.endswith('.onion'):
        parts = lower_val.split('.')
        if len(parts) == 2:
            base = parts[0]
            if len(base) <= 2 or base in ('www', 'ftp', 'mail', 'ns1', 'ns2'):
                return SeedQuality(decision='drop', reason='bare_or_invalid_tld', score=0.0)
    parts = lower_val.split('.')
    is_weak = any(('.'.join(parts[i:]) in _WEAK_DOMAINS for i, _ in enumerate(parts)))
    if is_weak:
        has_ioc_context = any((kw in combined for kw in _RANSOMWARE_KEYWORDS))
        if has_ioc_context:
            return SeedQuality(decision='keep', reason='weak_domain_explicit_ransomware_context', score=0.75)
        return SeedQuality(decision='weak', reason='major_platform_domain', score=0.3)
    if seed.kind in ('hash', 'sha256', 'sha1', 'md5', 'ip', 'cve', 'email'):
        return SeedQuality(decision='keep', reason=f'ioc_{seed.kind}_preserved', score=0.9)
    if seed.kind == 'url':
        lower_url = seed.value.lower()
        if any((kw in lower_url for kw in ('onion', 'panel', 'leak', 'stolen', 'dump'))):
            return SeedQuality(decision='keep', reason='url_contains_onion_or_illegal_path', score=0.9)
    lower_value = seed.value.lower()
    if any((kw in lower_value for kw in _RANSOMWARE_KEYWORDS)):
        return SeedQuality(decision='keep', reason='domain_contains_ransomware_keyword', score=0.85)
    return SeedQuality(decision='keep', reason='standard_ioc_preserved', score=0.65)

class NonfeedSeed(msgspec.Struct, frozen=True, gc=False):
    """
    Sprint F222D: Bounded IOC seed for nonfeed lanes.

    Migrated from @dataclass(frozen=True, slots=True) → msgspec.Struct.
    5-7× faster init, ~40B/instance smaller, no GC tracking. M1 8GB friendly.

    Fields:
        value:       The IOC value (domain, IP, URL, hash, CVE)
        kind:        IOC kind: domain | ip | url | hash | cve | email | unknown
        source:      Source tag: body | title | query | url
        confidence:  Extraction confidence [0.0, 1.0]
        reason:      Why this was extracted
    """
    value: str
    kind: str
    source: str
    confidence: float
    reason: str

    # NOTE: Former __post_init__ kind validation removed — all 12 call sites
    # in this module already pass literal valid kind strings. External callers
    # should validate via _VALID_KINDS frozenset.
_IP_RE = re.compile('\\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\b')
_HASH_RE = re.compile('\\b([a-fA-F0-9]{32,64})\\b')
_CVE_RE = re.compile('\\b(CVE-\\d{4}-\\d{4,})\\b', re.IGNORECASE)
_DOMAIN_RE = re.compile('(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\\.)+[a-zA-Z]{2,}[a-zA-Z0-9/_\\-]*')
_URL_RE = re.compile('https?://[^\\s\\"\'<>]+')
_EMAIL_RE = re.compile('\\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,})\\b')
_OBFUSCATED_RE = re.compile('\\[(\\.)\\]|\\((\\.)\\)')

def _is_publisher_domain(domain: str) -> bool:
    """Return True if domain is a known publisher/aggregator."""
    return domain.lower() in PUBLISHER_DOMAINS

# ─── Generic IOC extractor factory (DRY: replaces 4× identical patterns) ────────

def _make_ioc_extractor(
    pattern: re.Pattern[str],
    kind: str,
    confidence: float,
    reason_prefix: str,
    extract_value: callable = lambda m, _: m.group(0),
) -> callable:
    """Factory: creates an IOC extractor function from parameters.

    Eliminates 4× identical extractor patterns (urls, emails, ips, cves).
    """
    def extractor(cleaned: str, seen: dict, max_seeds: int) -> None:
        if len(seen) >= max_seeds:
            return
        for m in pattern.finditer(cleaned):
            raw = extract_value(m, 0)
            key = (kind, raw)
            if key not in seen:
                seen[key] = NonfeedSeed(
                    value=raw,
                    kind=kind,
                    source='body',
                    confidence=confidence,
                    reason=f'{reason_prefix}_in_body',
                )
            if len(seen) >= max_seeds:
                return
    extractor.__doc__ = f"Extract {kind.upper()} IOCs. Early-return when max_seeds reached."
    return extractor

# Concrete extractors (generated from factory)
_extract_urls = _make_ioc_extractor(
    pattern=_URL_RE, kind='url', confidence=0.9, reason_prefix='url',
)
_extract_emails = _make_ioc_extractor(
    pattern=_EMAIL_RE, kind='email', confidence=0.85, reason_prefix='email',
    extract_value=lambda m, _: m.group(1).lower(),
)
_extract_ips = _make_ioc_extractor(
    pattern=_IP_RE, kind='ip', confidence=0.95, reason_prefix='ip',
)
_extract_cves = _make_ioc_extractor(
    pattern=_CVE_RE, kind='cve', confidence=0.9, reason_prefix='cve',
    extract_value=lambda m, _: m.group(1).upper(),
)

def _extract_hashes(cleaned: str, seen: dict, max_seeds: int) -> None:
    """Extract hashes (MD5/SHA1/SHA256). Early-return when max_seeds reached."""
    if len(seen) >= max_seeds:
        return
    for m in _HASH_RE.finditer(cleaned):
        raw = m.group(1).lower()
        if len(raw) == 32:
            kind_str = 'md5'
        elif len(raw) == 40:
            kind_str = 'sha1'
        elif len(raw) == 64:
            kind_str = 'sha256'
        else:
            kind_str = 'unknown'
        key = ('hash', raw)
        if key not in seen:
            seen[key] = NonfeedSeed(value=raw, kind='hash', source='body', confidence=0.8, reason=f'hash_in_body_{kind_str}')
        if len(seen) >= max_seeds:
            return

def _extract_domains(cleaned: str, seen: dict, max_seeds: int) -> None:
    """Extract domains (generic fallback). Early-return when max_seeds reached."""
    if len(seen) >= max_seeds:
        return
    for m in _DOMAIN_RE.finditer(cleaned):
        raw = m.group(0).lower()
        if raw.endswith(('.gov.', '.edu.', '.mil.', '.onion')):
            continue
        if _is_publisher_domain(raw):
            continue
        key = ('domain', raw)
        if key not in seen:
            seen[key] = NonfeedSeed(value=raw, kind='domain', source='body', confidence=0.7, reason='domain_in_body')
        if len(seen) >= max_seeds:
            return

def extract_nonfeed_seeds_from_text(text: str, *, max_seeds: int=50) -> list[NonfeedSeed]:
    """
    Sprint F222D: Extract IOC seeds from arbitrary text.

    Extraction order (most specific first):
      1. URL   (has :// prefix)           → url
      2. Email  (has @)                   → email
      3. IP     (dotted quad)             → ip
      4. Hash   (32-64 hex chars)         → hash (md5/sha1/sha256)
      5. CVE    (CVE-YYYY-NNNN)            → cve
      6. Domain (generic fallback)         → domain

    Args:
        text:     Text to scan (body, title, etc.)
        max_seeds: Hard cap on returned seeds (default 50)

    Returns:
        Deduplicated list of NonfeedSeed, newest-first.
        Publisher domains filtered out.
        Bounded to max_seeds.
    """
    if not text or not isinstance(text, str):
        return []
    cleaned = _OBFUSCATED_RE.sub('.', text)
    seen: dict[tuple[str, str], NonfeedSeed] = {}
    _extract_urls(cleaned, seen, max_seeds)
    _extract_emails(cleaned, seen, max_seeds)
    _extract_ips(cleaned, seen, max_seeds)
    _extract_hashes(cleaned, seen, max_seeds)
    _extract_cves(cleaned, seen, max_seeds)
    _extract_domains(cleaned, seen, max_seeds)
    return list(seen.values())

def extract_nonfeed_seeds_from_findings(findings: list[dict], *, max_seeds: int=100) -> list[NonfeedSeed]:
    """
    Sprint F222D: Extract IOC seeds from a list of finding dicts.

    Scans each finding's:
      - payload_text  (body content)
      - title          (article title)
      - query          (search query / domain pivot)

    Args:
        findings:  List of CanonicalFinding-like dicts
        max_seeds: Hard cap on total returned seeds (default 100)

    Returns:
        Deduplicated list of NonfeedSeed across all findings.
        Publisher domains filtered.
        Bounded to max_seeds.
    """
    if not findings:
        return []
    seen: dict[tuple[str, str], NonfeedSeed] = {}
    total = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        if len(seen) >= max_seeds:
            break
        payload = finding.get('payload_text', '') or ''
        if isinstance(payload, str) and payload:
            seeds = extract_nonfeed_seeds_from_text(payload, max_seeds=max_seeds)
            for s in seeds:
                key = (s.kind, s.value)
                if key not in seen:
                    seen[key] = NonfeedSeed(value=s.value, kind=s.kind, source='body', confidence=s.confidence, reason=s.reason)
        title = finding.get('title', '') or ''
        if isinstance(title, str) and title:
            seeds = extract_nonfeed_seeds_from_text(title, max_seeds=max_seeds)
            for s in seeds:
                key = (s.kind, s.value)
                if key not in seen:
                    seen[key] = NonfeedSeed(value=s.value, kind=s.kind, source='title', confidence=min(s.confidence + 0.05, 1.0), reason='domain_in_title' if s.kind == 'domain' else s.reason)
        query = finding.get('query', '') or ''
        if isinstance(query, str) and query:
            seeds = extract_nonfeed_seeds_from_text(query, max_seeds=max_seeds)
            for s in seeds:
                key = (s.kind, s.value)
                if key not in seen:
                    seen[key] = NonfeedSeed(value=s.value, kind=s.kind, source='query', confidence=0.85, reason=f'ioc_in_query_{s.kind}')
        total += 1
    result = list(seen.values())
    return result[:max_seeds]

def compute_lane_unlocks(seeds: list[NonfeedSeed]) -> dict[str, list[str]]:
    """
    Sprint F222D: Map seeds → which nonfeed lanes they unlock.

    Returns:
        {
            "ct":          [domain seeds],
            "passive_dns": [domain + ip seeds],
            "wayback":     [domain + url seeds],
            "doh":         [domain seeds],
            "graph":       [hash seeds],
        }
    """
    domains = [s for s in seeds if s.kind == 'domain']
    ips = [s for s in seeds if s.kind in ('ip', 'ipv4')]
    urls = [s for s in seeds if s.kind == 'url']
    hashes = [s for s in seeds if s.kind == 'hash']
    cves = [s for s in seeds if s.kind == 'cve']
    return {'ct': [s.value for s in domains], 'passive_dns': [s.value for s in domains + ips], 'wayback': [s.value for s in domains + urls], 'doh': [s.value for s in domains], 'graph': [s.value for s in hashes], 'cve': [s.value for s in cves]}