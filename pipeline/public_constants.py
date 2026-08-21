"""Public pipeline constants, patterns, and helper functions.

Extracted from live_public_pipeline.py (originally Sprint 8AE).
Covers: quality tiers, fetch budgets, shopping noise filters, threat patterns,
        rescue sources, bootstrap URLs, CT/CC domain regexes.

No I/O, no async, no external dependencies except stdlib + urllib.parse.
"""

import re
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

MAX_EXTRACTED_TEXT_CHARS: int = 200_000
"""Hard cap on extracted text size per page."""

MAX_METADATA_PREPEND_CHARS: int = 500
"""Max chars of title+snippet prepended to extracted text for pattern scan context."""

_SOURCE_TYPE: str = "live_public_pipeline"
"""source_type value for all findings produced by this pipeline."""

_PUBLIC_SOURCE_TYPE: str = "public"
"""source_type value for public-surface findings from bootstrap/content-only pages (F226B)."""

_REPORT_SOURCE_TYPE: str = "report"
"""source_type value for generated OSINT reports."""

_DEFAULT_CONFIDENCE: float = 0.8
"""Confidence for pipeline findings — executed but unverified."""

# P6: Top results for report generation
_REPORT_TOP_N: int = 5
"""Number of top results to include in OSINT report."""

_FINDING_ID_CONTEXT_RADIUS: int = 100
"""Character radius around pattern hit for payload_text context window."""

# Sprint F150I: tier thresholds (additive, no new framework)
_QUALITY_TIER_VERY_GOOD = "very_good"
_QUALITY_TIER_GOOD = "good"
_QUALITY_TIER_OK = "ok"
_QUALITY_TIER_WEAK = "weak_low_signal"
_QUALITY_TIER_SKIP = "SKIP_WEAK"

# Sprint F161B: conversion truth consolidation
_DISCOVERY_SIGNAL_SCORE_THRESHOLD: float = 0.3

# Adaptive fetch budget tiers: multiplier on base fetch_timeout_s
_FETCH_BUDGET_STRONG: float = 1.25  # very_good or discovery_score >= 0.7
_FETCH_BUDGET_NORMAL: float = 1.0  # ok, good
_FETCH_BUDGET_WEAK: float = 0.65  # weak_low_signal, low discovery score
_FETCH_BUDGET_SKIP: float = 0.0  # SKIP_WEAK — dead until Fix A in F150J

# Sprint F161B: pre-fetch text-length gate — BEFORE budget is spent
_PRE_FETCH_TEXT_MIN_CHARS: int = 80  # F275: lowered from 150 to catch metadata-rich thin pages
"""Minimum extracted text chars to consider fetch worthwhile."""

# Sprint F163B: low-entropy gate — detect repetitive placeholder noise
_LOW_ENTROPY_UNIQUE_WORD_RATIO: float = 0.25

# Sprint F188B: CT winner slice — bounded CT subdomain injection
_CT_SUBDOMAIN_BOUND: int = 10
"""Max CT subdomains to inject as synthetic discovery hits."""
_CT_SUBDOMAIN_SCORE: float = 0.85
"""Discovery score assigned to CT-synthesized hits (high confidence)."""
_CT_QUERY_IS_DOMAIN_RE: re.Pattern = re.compile(r"^(?:\*\.)?[a-zA-Z0-9][a-zA-Z0-9.*-]*\.[a-zA-Z]{2,}$")
"""Regex to detect domain-like query strings suitable for CT subdomain lookup."""
_CC_QUERY_IS_DOMAIN_RE: re.Pattern = re.compile(
    r"^(?:\*\.)?[a-zA-Z0-9][a-zA-Z0-9.*-]*\.[a-zA-Z]{2,}$"
    r"|^(?:site|domain):"
)
"""Regex for CommonCrawl CDX lookup — supports wildcards and site:/domain: operators."""

# Sprint F161B: discovery false-positive band — legitimate signal but no conversion
_DISCOVERY_FALSE_POSITIVE_THRESHOLD: float = 0.5
"""Discovery score above this with zero patterns = false positive, not waste."""

# Sprint F150J: pre-fetch skip threshold — below this score with no strong signal → SKIP tier
_DISCOVERY_SKIP_THRESHOLD: float = 0.15
"""If discovery_score is below this AND no strong signal, skip fetch entirely."""

# Sprint F217C: Deterministic bootstrap URL generator
_MAX_BOOTSTRAP_URLS: int = 5
"""Max bootstrap URLs per query (domain-sourced)."""
_BOOTSTRAP_DEFAULT_URLS: list[str] = [
    "",  # https://domain/
    "/www.",  # https://www.domain/
    "/.well-known/security.txt",  # deterministic security policy endpoint
    "/robots.txt",  # robots directive
    "/sitemap.xml",  # sitemap reference
]
"""Ordered list of URL path templates for deterministic bootstrap."""

# Sprint F220C: Public Provider Rescue for non-domain threat queries
_RESGUE_SOURCE_CANDIDATES: list[tuple[str, str]] = [
    ("ThreatFox", "https://threatfox.abuse.ch/browse.php?search="),
    ("ID Ransomware", "https://id-ransomware.malwarehunterteam.com/"),
    ("BleepingComputer", "https://www.bleepingcomputer.com/search/?search="),
    ("The Hacker News", "https://thehackernews.com/search?q="),
    ("Krebs on Security", "https://krebsonsecurity.com/?s="),
    ("CISA KEV", "https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search="),
    ("URLhaus", "https://urlhaus.abuse.ch/"),
    ("AlienVault OTX", "https://otx.alienvault.com/api/v1/search?q="),
    ("Maltiverse", "https://maltiverse.com/search?keyword="),
    ("Onyphe", "https://www.onyphe.io/search/?query="),
    ("GreyNoise", "https://greynoise.io/viz/share/"),
    ("AbuseIPDB", "https://www.abuseipdb.com/check/"),
]
"""Static rescue source list for non-domain threat/malware/ransomware queries."""

_SHOPPING_NOISE_DOMAINS: tuple[str, ...] = (
    "trendyol.com",
    "pazarama.com",
    "amazon.com.tr",
    "n11.com",
    "hepsiburada.com",
    "gittigidiyor.com",
    "cimri.com",
    "akakce.com",
)

_SHOPPING_NOISE_PATHS: tuple[str, ...] = (
    "/gp/bestsellers/",
    "/gp/bestsellers",
    "/bestsellers/",
    "/best-seller",
    "/matkap",
    "/category/",
    "/product/",
    "/products/",
    "/shop/",
    "/shopping/",
    "/cart/",
    "/checkout/",
    "/buy/",
    "/sale/",
    "/offers/",
    "/home-improvement",
    "/home-and-garden",
)

_SHOPPING_NOISE_PATHS_STRICT: tuple[str, ...] = (
    "/cart/",
    "/checkout/",
    "/buy/",
    "/sale/",
    "/offers/",
)

_CTI_NEWS_ALLOWED_DOMAINS: tuple[str, ...] = (
    "cisa.gov",
    "krebsonsecurity.com",
    "bleepingcomputer.com",
    "thehackernews.com",
    "abuse.ch",
    "threatfox.abuse.ch",
    "id-ransomware.malwarehunterteam.com",
    "malwarehunterteam.com",
    "cyberscoop.com",
    "darkreading.com",
    "threatpost.com",
    "therecord.media",
    "securityweek.com",
    "inforisktoday.com",
    "helpnetsecurity.com",
    "ransomwarewiki.com",
    "cybercrime-tracker.net",
    "malware-traffic-analysis.net",
    "unit42.paloaltonetworks.com",
    "securityaffairs.com",
    "thecyberwire.com",
    "bleepinguid.com",
    "ransomware.live",
)


def _is_shopping_noise_url(url: str, is_threat_query: bool) -> tuple[bool, str]:
    """Detect if a URL is shopping/e-commerce noise.

    For threat queries: blocks obvious shopping/ecommerce/category pages.
    For non-threat queries: less strict, only blocks domain-level matches.

    Returns:
        Tuple of (is_noise, reason) where reason is one of:
        - "public_noise_shopping" — blocked shopping domain
        - "public_noise_unrelated_marketplace" — blocked marketplace
        - "public_relevance_pass" — URL is relevant

    """
    if not url:
        return False, "public_relevance_pass"

    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.lower()

    for allowed_domain in _CTI_NEWS_ALLOWED_DOMAINS:
        if netloc.endswith(allowed_domain) or netloc == allowed_domain:
            return False, "public_relevance_pass"

    for blocked_domain in _SHOPPING_NOISE_DOMAINS:
        if netloc.endswith(blocked_domain) or netloc == blocked_domain:
            return True, "public_noise_shopping"

    if is_threat_query:
        for blocked_path in _SHOPPING_NOISE_PATHS_STRICT:
            if blocked_path in path:
                return True, "public_noise_unrelated_marketplace"

    return False, "public_relevance_pass"


def _filter_public_noise(hits: list | tuple, is_threat_query: bool) -> tuple[list, list[tuple[str, str]]]:
    """Filter shopping/e-commerce noise from public discovery hits.

    Returns:
        Tuple of (filtered_hits, rejected_reasons) where rejected_reasons
        is list of (url, reason) for each rejected hit.

    """
    filtered: list = []
    rejected: list[tuple[str, str]] = []

    for hit in hits:
        url = getattr(hit, "url", None) or (str(hit[2]) if len(hit) > 2 else "")
        if not url:
            filtered.append(hit)
            continue

        is_noise, reason = _is_shopping_noise_url(url, is_threat_query)
        if is_noise:
            rejected.append((url, reason))
        else:
            filtered.append(hit)

    return filtered, rejected


# Compiled threat patterns (module-level for performance)
_IP_PAT = re.compile(
    r"^\d{1,3}(?:\.\d{1,3}){3}(?:\/\d{1,2})?$|^"
    r"[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7}(?::\d{1,3})?(?:\/\d{1,2})?$"
)
_CVE_PAT = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)
_THREAT_PAT = re.compile(
    r"^(?:"
    r"lockbit|conti|revil|clop|darkside|blackcat|alphv|ransomware|"
    r"apt[_\s]?\d+|apt[_-]\w+|sidecopy|callback|triangle|temp"
    r"|wanna[_\s]?cry|wannacry|petya|notpetya|badrabbit|"
    r"emotet|trickbot|cobalt[_\s]?strike|koadic|metasploit|"
    r"fin7|carbanak|finacrypt|prodaft|labyrinth|zCrypt|"
    r"poisonivy|plugx|gh0st|gain|wellmess|whispergate|hermetic"
    r")$",
    re.IGNORECASE,
)
_EXTENDED_PAT = re.compile(
    r"^(?:"
    r"meterpreter|sandworm|lazarus|log4shell|finacrypt|prodaft|labyrinth|"
    r"zcrypt|poisonivy|plugx|gh0st|gain|wellmess|whispergate|hermetic|"
    r"sidecopy|callback|triangle|temp|sofacy|平原"
    r")$",
    re.IGNORECASE,
)
_THREAT_KW_PAT = re.compile(
    r"^(?:"
    r"ransomware|malware|threat[_-]?actor|cobalt[_\s]?strike|"
    r"breach|exploit|0day|zero[_\s]?day|vulnerability|"
    r"phishing|spam|botnet|trojan|rootkit|keylogger|"
    r"Ransomware|Malware|ThreatActor|CVE|APT"
    r")$",
    re.IGNORECASE,
)
_OSINT_KW_PAT = re.compile(
    r"^(?:"
    r"osint|osint infrastructure|infrastructure|telemetry|leak|"
    r"dark[_\s]?web|exposure|credential|breach|"
    r"darkweb|onion|leakdb|intel|threat|hunting|"
    r"recon|scanning|fingerprint|iot|ics|scada"
    r")$",
    re.IGNORECASE,
)
_OSINT_MULTI_PAT = re.compile(
    r"^(?:"
    r"osint[_\s]?infrastructure|infrastructure[_\s]?osint|"
    r"dark[_\s]?web[_\s]?leak|credential[_\s]?leak|"
    r"threat[_\s]?intel|threat[_\s]?hunting"
    r")$",
    re.IGNORECASE,
)


def _is_threat_query(query: str) -> bool:
    """Detect if query is a non-domain threat/malware/ransomware/entity query.

    Returns True for queries that look like OSINT entity searches where
    bootstrap would return no URLs but a rescue search URL may help.
    """
    if not query or not query.strip():
        return False

    q = query.strip()

    # Strip prefix operators
    for prefix in ("site:", "domain:", "url:", "asn:", "ip:", "vpn:", "tor:"):
        if q.lower().startswith(prefix):
            q = q[len(prefix) :].strip()
            break

    # IP address check
    if _IP_PAT.match(q):
        return True

    # CVE pattern
    if _CVE_PAT.match(q):
        return True

    # Ransomware/malware/threat actor name patterns
    if _THREAT_PAT.match(q):
        return True

    # Also check first token (for multi-word queries like "LockBit ransomware")
    first_token = q.split()[0] if q else ""
    if first_token and _THREAT_PAT.match(first_token):
        return True

    for token in re.split(r"[\s\-_]+", q):
        if len(token) >= 4 and _THREAT_PAT.match(token):
            return True

    # Extended patterns
    for token in re.split(r"[\s\-_]+", q):
        if len(token) >= 3 and _EXTENDED_PAT.match(token):
            return True

    # Generic keywords
    if _THREAT_KW_PAT.match(q):
        return True

    # OSINT keywords
    if _OSINT_KW_PAT.match(q):
        return True
    if first_token and _OSINT_KW_PAT.match(first_token):
        return True

    # Multi-word OSINT/threat compound patterns
    if _OSINT_MULTI_PAT.match(q):
        return True

    return False
