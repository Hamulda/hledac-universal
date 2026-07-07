"""
Sprint 8AE: First live public OSINT pipeline wiring.

query -> discovery (8AC duckduckgo) -> fetch (8AD public_fetcher) ->
lightweight HTML extraction -> PatternMatcher (8X) -> quality gate (8W) ->
CanonicalFinding -> storage (8S/8R DuckDBShadowStore).

No LLM calls. No AO. No new storage schema.
All heavy I/O (HTML parsing, pattern scanning) offloaded via asyncio.to_thread().
"""
from __future__ import annotations



import asyncio
import hashlib
import html.parser
import msgspec.json as _json
import logging
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING, Any  # noqa: E402

import msgspec  # noqa: E402

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

# F206AB: discovery error taxonomy helper
from hledac.universal.discovery.duckduckgo_adapter import (  # noqa: E402
    DiscoveryHit,
    classify_discovery_error,
    search_multi_engine as _search_multi_engine_bootstrap,
)

# F206AC: fetch error taxonomy helper
from hledac.universal.fetching.public_fetcher import (  # noqa: E402
    classify_fetch_error,
)
from hledac.universal.utils.executors import CPU_EXECUTOR  # noqa: E402
from hledac.universal.utils.async_helpers import bounded_gather, safe_create_task  # noqa: E402

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

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

# P6: Top results for report generation
_REPORT_TOP_N: int = 5
"""Number of top results to include in OSINT report."""
"""Confidence for pipeline findings — executed but unverified."""

_FINDING_ID_CONTEXT_RADIUS: int = 100
"""Character radius around pattern hit for payload_text context window."""

# Sprint F150I: tier thresholds (additive, no new framework)
_QUALITY_TIER_VERY_GOOD = "very_good"
_QUALITY_TIER_GOOD = "good"
_QUALITY_TIER_OK = "ok"
_QUALITY_TIER_WEAK = "weak_low_signal"
_QUALITY_TIER_SKIP = "SKIP_WEAK"

# Sprint F161B: conversion truth consolidation
# Changes:
# - _compute_page_usable_fields: distinguish false-positive discovery from structural waste
# - _score_page_quality: pre-fetch skip for extremely low text BEFORE budget spent
# - New derived fields: discovery_false_positive, waste_category, structural_quality
# - Bounded: all additive, backward-compatible, M1-safe

_DISCOVERY_SIGNAL_SCORE_THRESHOLD: float = 0.3

# Adaptive fetch budget tiers: multiplier on base fetch_timeout_s
_FETCH_BUDGET_STRONG: float = 1.25   # very_good or discovery_score >= 0.7
_FETCH_BUDGET_NORMAL: float = 1.0    # ok, good
_FETCH_BUDGET_WEAK: float = 0.65     # weak_low_signal, low discovery score
_FETCH_BUDGET_SKIP: float = 0.0       # SKIP_WEAK — dead until Fix A in F150J

# Sprint F161B: pre-fetch text-length gate — BEFORE budget is spent
# Previously this check happened post-fetch in _score_page_quality (wasteful)
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
"""Regex to detect domain-like query strings suitable for CT subdomain lookup."""

# Sprint F161B: discovery false-positive band — legitimate signal but no conversion
_DISCOVERY_FALSE_POSITIVE_THRESHOLD: float = 0.5
"""Discovery score above this with zero patterns = false positive, not waste."""

# Sprint F150J: pre-fetch skip threshold — below this score with no strong signal → SKIP tier
_DISCOVERY_SKIP_THRESHOLD: float = 0.15
"""If discovery_score is below this AND no strong signal, skip fetch entirely."""

# Sprint F217C: Deterministic bootstrap URL generator
# Bounded, no brute force, no wordlists, no JS, no stealth.
_MAX_BOOTSTRAP_URLS: int = 5
"""Max bootstrap URLs per query (domain-sourced)."""
_BOOTSTRAP_DEFAULT_URLS: list[str] = [
    "",           # https://domain/
    "/www.",      # https://www.domain/
    "/.well-known/security.txt",   # deterministic security policy endpoint
    "/robots.txt",                  # robots directive
    "/sitemap.xml",                 # sitemap reference
]
"""Ordered list of URL path templates for deterministic bootstrap."""

# Sprint F220C: Public Provider Rescue for non-domain threat queries
# Known public CTI/news search URLs — lightweight, no new dependency.
# Mapped to (name, base_url_format) tuples. Max 10.
_RESGUE_SOURCE_CANDIDATES: list[tuple[str, str]] = [
    # F273: Expanded OSINT rescue sources (was 5, now 12)
    # Threat intelligence aggregators — open-access only (no login/API key required)
    ("ThreatFox", "https://threatfox.abuse.ch/browse.php?search="),
    # Ransomware-specific trackers — open-access
    # ("Ransomware Tracker", "https://ransomwaretracker.xyz/"),  # OFFLINE 2026-06 -- NS_ERROR_UNKNOWN_HOST
    ("ID Ransomware", "https://id-ransomware.malwarehunterteam.com/"),
    # General CTI/news — open-access
    ("BleepingComputer", "https://www.bleepingcomputer.com/search/?search="),
    ("The Hacker News", "https://thehackernews.com/search?q="),
    ("Krebs on Security", "https://krebsonsecurity.com/?s="),
    ("CISA KEV", "https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search="),
    # F273: Additional open-access OSINT sources
    ("URLhaus", "https://urlhaus.abuse.ch/"),  # Malware URL database
    ("AlienVault OTX", "https://otx.alienvault.com/api/v1/search?q="),  # OTX pulse search
    ("Maltiverse", "https://maltiverse.com/search?keyword="),  # Malware enrichment
    ("Onyphe", "https://www.onyphe.io/search/?query="),  # Cyber threat intelligence
    ("GreyNoise", "https://greynoise.io/viz/share/"),  # Internet noise scanner
    ("AbuseIPDB", "https://www.abuseipdb.com/check/"),  # IP abuse database
]
"""Static rescue source list for non-domain threat/malware/ransomware queries."""


# -----------------------------------------------------------------------------
# F221H: Public Discovery Relevance / Shopping Noise Filter
# -----------------------------------------------------------------------------

# Blocked domain patterns for shopping/e-commerce noise
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

# Blocked URL path patterns for e-commerce/shopping/category pages
# Used for non-threat queries (domain-only blocking for threat queries)
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

# Strict subset: only unambiguous e-commerce checkout/transaction paths
# Used for threat queries to avoid over-filtering legitimate CTI content
# that happens to have generic paths like /product/ or /category/
_SHOPPING_NOISE_PATHS_STRICT: tuple[str, ...] = (
    "/cart/",
    "/checkout/",
    "/buy/",
    "/sale/",
    "/offers/",
)

# CTI/news domains that are always allowed (override noise filter for threat queries)
_CTI_NEWS_ALLOWED_DOMAINS: tuple[str, ...] = (
    "cisa.gov",
    "krebsonsecurity.com",
    "bleepingcomputer.com",
    "thehackernews.com",
    "abuse.ch",
    "threatfox.abuse.ch",
    # "ransomwaretracker.xyz",  # OFFLINE 2026-06 -- NS_ERROR_UNKNOWN_HOST
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
    """
    Detect if a URL is shopping/e-commerce noise.

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

    # F221H: CTI/news domains always pass (override noise filter)
    for allowed_domain in _CTI_NEWS_ALLOWED_DOMAINS:
        if netloc.endswith(allowed_domain) or netloc == allowed_domain:
            return False, "public_relevance_pass"

    # Check if domain is in blocked shopping domains
    for blocked_domain in _SHOPPING_NOISE_DOMAINS:
        if netloc.endswith(blocked_domain) or netloc == blocked_domain:
            return True, "public_noise_shopping"

    # For threat queries, only block strict checkout/transaction paths
    # to avoid over-filtering legitimate CTI content with generic paths
    if is_threat_query:
        for blocked_path in _SHOPPING_NOISE_PATHS_STRICT:
            if blocked_path in path:
                return True, "public_noise_unrelated_marketplace"
    # Non-threat queries: no path-based blocking (only domain-level)

    return False, "public_relevance_pass"


def _filter_public_noise(
    hits: list | tuple, is_threat_query: bool
) -> tuple[list, list[tuple[str, str]]]:
    """
    Filter shopping/e-commerce noise from public discovery hits.

    For threat queries: blocks shopping domains AND path patterns.
    For non-threat queries: only blocks known shopping domains.

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


def _is_threat_query(query: str) -> bool:
    """
    Detect if query is a non-domain threat/malware/ransomware/entity query.

    Returns True for queries that look like OSINT entity searches where
    bootstrap would return no URLs but a rescue search URL may help.

    Covers: ransomware names, malware family names, threat actor names,
    CVE-like patterns, IP addresses (which domain bootstrap can't handle).
    """
    if not query or not query.strip():
        return False

    q = query.strip()

    # Strip prefix operators
    for prefix in ("site:", "domain:", "url:", "asn:", "ip:", "vpn:", "tor:"):
        if q.lower().startswith(prefix):
            q = q[len(prefix):].strip()
            break

    # IP address check — domain bootstrap can't help
    import re as _re
    IP_PAT = _re.compile(  # noqa: N806
        r"^\d{1,3}(?:\.\d{1,3}){3}(?:\/\d{1,2})?$|^"
        r"[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7}(?::\d{1,3})?(?:\/\d{1,2})?$"
    )
    if IP_PAT.match(q):
        return True

    # CVE pattern
    CVE_PAT = _re.compile(r"^CVE-\d{4}-\d{4,}$", _re.IGNORECASE)  # noqa: N806
    if CVE_PAT.match(q):
        return True

    # Ransomware/malware/threat actor name patterns
    THREAT_PAT = _re.compile(  # noqa: N806
        r"^(?:"
        r"lockbit|conti|revil|clop|darkside|blackcat|alphv|ransomware|"
        r"apt[_\s]?\d+|apt[_-]\w+|sidecopy|callback|triangle|temp"
        r"|wanna[_\s]?cry|wannacry|petya|notpetya|badrabbit|"
        r"emotet|trickbot|cobalt[_\s]?strike|koadic|metasploit|"
        r"fin7|carbanak|finacrypt|prodaft|labyrinth|zCrypt|"
        r"poisonivy|plugx|gh0st|gain|wellmess|whispergate|hermetic"
        r")$",
        _re.IGNORECASE,
    )
    if THREAT_PAT.match(q):
        return True

    # Also check first token (for multi-word queries like "LockBit ransomware")
    first_token = q.split()[0] if q else ""
    if first_token and THREAT_PAT.match(first_token):
        return True

    # Check any token in the query (for multi-word threat references, split on -, _, space)
    for token in re.split(r"[\s\-_]+", q):
        if len(token) >= 4 and THREAT_PAT.match(token):
            return True

    # Extended patterns: check bare tokens that are known threat names
    _EXTENDED_PAT = _re.compile(  # noqa: N806
        r"^(?:"
        r"meterpreter|sandworm|lazarus|log4shell|finacrypt|prodaft|labyrinth|"
        r"zcrypt|poisonivy|plugx|gh0st|gain|wellmess|whispergate|hermetic|"
        r"sidecopy|callback|triangle|temp|sofacy|平原"
        r")$",
        _re.IGNORECASE,
    )
    for token in re.split(r"[\s\-_]+", q):
        if len(token) >= 3 and _EXTENDED_PAT.match(token):
            return True

    # Generic keywords (must be stand-alone, not part of a sentence)
    THREAT_KW_PAT = _re.compile(  # noqa: N806
        r"^(?:"
        r"ransomware|malware|threat[_-]?actor|cobalt[_\s]?strike|"
        r"breach|exploit|0day|zero[_\s]?day|vulnerability|"
        r"phishing|spam|botnet|trojan|rootkit|keylogger|"
        r"Ransomware|Malware|ThreatActor|CVE|APT"
        r")$",
        _re.IGNORECASE,
    )
    if THREAT_KW_PAT.match(q):
        return True

    # P0-2: OSINT-related keywords — broad threat/discovery context queries
    # that lack a domain but have rich search-term seeds for rescue URLs.
    OSINT_KW_PAT = _re.compile(  # noqa: N806
        r"^(?:"
        r"osint|osint infrstructure|infrastructure|telemetry|leak|"
        r"dark[_\s]?web|exposure|credential|breach|"
        r"darkweb|onion|leakdb|intel|threat|hunting|"
        r"recon|scanning|fingerprint|iot|ics|scada"
        r")$",
        _re.IGNORECASE,
    )
    if OSINT_KW_PAT.match(q):
        return True
    # Also check first token for OSINT keywords
    if first_token and OSINT_KW_PAT.match(first_token):
        return True

    # F273: Multi-word OSINT/threat compound patterns
    # These detect complex queries like "ransomware threat intelligence leak"
    _OSINT_MULTI_PAT = _re.compile(  # noqa: N816
        r"(?:"
        r"ransomware\s+(?:threat|intelligence|leak|attack|group|operation)|"
        r"threat\s+(?:intelligence|actor|actor\s+group|intel)|"
        r"malware\s+(?:analysis|sample|family|variant)|"
        r"data\s+(?:breach|leak|exposure|dump)|"
        r"dark\s+web|deep\s+web|surface\s+web|"
        r"credential\s+(?:dump|leak|breach|stuffing)|"
        r"osint\s+(?:reconnaissance|recon|reconnaissance|automation)|"
        r"vulnerability\s+(?:scan|scanner|assessment|intelligence)|"
        r"threat\s+hunting|incident\s+response|digital\s+forensics|"
        r"infosec|cybersecurity\s+intelligence|"
        r"iosint|geoint|fintech\s+threat|"
        r"bloc\s+threat|apts|advanced\s+persistent|"
        r"supply\s+chain\s+(?:attack|threat)|"
        r"zero\s+day|zero-day|exploit\s+kit|"
        r"phishing\s+(?:campaign|kit|template)|"
        r"botnet\s+(?:infection|command|控|controller)|"
        r"ransomware\s+as\s+a\s+service|raas|ransomware\s+gang|"
        r"cyber\s+(?:attack|threat|crime|criminal|espionage)|"
        r"nation[\s_-]state\s+(?:threat|apt|actor|hacker)|"
        r"state[\s_-]sponsored|apt[\s_-]\w+"
        r")",
        _re.IGNORECASE,
    )
    if _OSINT_MULTI_PAT.search(q):
        return True

    return False


def generate_rescue_urls(query: str, max_urls: int = 8) -> list[DiscoveryHit]:
    """
    Generate lightweight rescue DiscoveryHits for non-domain threat queries.

    Sprint F220C: When bootstrap generates zero URLs (non-domain query),
    and the query appears to be a threat/malware/ransomware/entity search,
    generate rescue candidate hits from static CTI/news search URLs.

    Behavior:
      - Returns up to max_urls DiscoveryHit from static source list
      - Each hit has source="rescue", score=0.7, reason="rescue_candidate"
      - Does NOT perform network I/O — pure synchronous URL construction
      - Fail-safe: returns empty list for domain-like queries

    Args:
        query: The original OSINT query string.
        max_urls: Maximum number of rescue hits to return (default 5).

    Returns:
        List of DiscoveryHit objects from rescue sources. Empty if
        query looks like a domain or rescue sources exhausted.
    """
    if not query or max_urls < 1:
        return []
    # P0-2: Also trigger rescue for OSINT threat/discovery queries (non-domain but
    # rich search terms) — _is_threat_query now covers OSINT keywords.
    if not _is_threat_query(query):
        return []

    hits: list[DiscoveryHit] = []
    for name, base_url in _RESGUE_SOURCE_CANDIDATES[:max_urls]:
        url = f"{base_url}{urllib.parse.quote(query.strip())}"
        hits.append(DiscoveryHit(
            query=query,
            title=f"Rescue: {name}",
            url=url,
            snippet=f"Rescue search via {name}: {query}",
            score=0.70,
            reason="rescue_candidate",
            rank=-1,
            source="rescue",
            retrieved_ts=0.0,
        ))
    return hits


def generate_bootstrap_urls(query: str, max_urls: int = _MAX_BOOTSTRAP_URLS) -> list[str]:
    """
    Generate deterministic bootstrap URLs for domain/URL queries.

    Bounded: at most max_urls URLs returned.
    Fail-safe: returns empty list for non-domain queries or parse errors.
    No network I/O — pure synchronous URL construction.

    Bootstrap targets (in order):
      1. https://domain/
      2. https://www.domain/
      3. https://domain/.well-known/security.txt
      4. https://domain/robots.txt
      5. https://domain/sitemap.xml

    Args:
        query: The original OSINT query string.
        max_urls: Maximum number of bootstrap URLs to return (default 5).

    Returns:
        List of absolute URL strings (max max_urls). Empty list if query
        is not a domain or URL cannot be parsed.
    """
    if not query or max_urls < 1:
        return []

    # Strip common prefix operators used in OSINT queries
    clean_query = query.strip()
    for prefix in ("site:", "domain:", "url:"):
        if clean_query.lower().startswith(prefix):
            clean_query = clean_query[len(prefix):].strip()
            break

    # Attempt to extract a domain from the query
    domain = _extract_domain_from_query(clean_query)
    if not domain:
        return []

    # Build bootstrap URL list (paths in order of priority)
    paths = _BOOTSTRAP_DEFAULT_URLS[:max_urls]
    urls: list[str] = [
        f"https://www.{domain}" if path == "/www."
        else f"https://{domain}{path}" if path
        else f"https://{domain}"
        for path in paths
    ]
    return urls


# Sprint F223C: Bounded seed_context bootstrap for nonfeed_diagnostic profile
_MAX_SEED_CONTEXT_BOOTSTRAP: int = 10  # hard cap


def generate_seed_context_bootstrap_urls(seed_context: Any, max_candidates: int = _MAX_SEED_CONTEXT_BOOTSTRAP) -> list[str]:  # noqa: E501
    """
    Generate deterministic bootstrap URLs from NonfeedSeedContext.

    Bounded: at most max_candidates URLs returned.
    Fail-safe: returns empty list for None seed_context or parse errors.
    No network I/O — pure synchronous URL construction.
    No browser, no recursive crawl.

    Bootstrap sources (in priority order):
      1. seed_context.domains → https://domain/ (top 5 only)
      2. seed_context.urls → as-is (top 5 only)

    Args:
        seed_context: NonfeedSeedContext with domains/urls tuples.
        max_candidates: Maximum number of URLs to return (default 10).

    Returns:
        List of absolute URL strings (max max_candidates). Empty list if
        seed_context is None or has no domains/urls.
    """
    if not seed_context or max_candidates < 1:
        return []

    urls: list[str] = []
    _has_domains = bool(getattr(seed_context, 'domains', ()))
    _has_urls = bool(getattr(seed_context, 'urls', ()))
    _both_sources = _has_domains and _has_urls

    # Split budget: if both sources present, split evenly (5+5 for max=10)
    # If only one source, use full budget for that source
    if _both_sources:
        _max_per_source = (max_candidates + 1) // 2
    else:
        _max_per_source = max_candidates

    # Domains: construct root URL for each domain (top N)
    if _has_domains:
        for domain in list(getattr(seed_context, 'domains', ()))[:_max_per_source]:
            if len(urls) >= max_candidates:
                break
            # Basic domain validation — skip IPs and obvious noise
            if not domain or "." not in domain:
                continue
            try:
                # Ensure proper URL form
                domain = domain.lower().strip()
                if not domain.startswith(("http://", "https://")):
                    urls.append(f"https://{domain}")
                else:
                    urls.append(domain)
            except Exception:
                continue

    # URLs: use as-is (top N)
    if _has_urls:
        for url in list(getattr(seed_context, 'urls', ()))[:_max_per_source]:
            if len(urls) >= max_candidates:
                break
            if not url:
                continue
            try:
                url_str = str(url).strip()
                if not url_str.startswith(("http://", "https://")):
                    continue  # skip bare domains that would duplicate domain entries
                urls.append(url_str)
            except Exception:
                continue

    return urls[:max_candidates]


# =============================================================================
# 3.3 Public Discovery Bootstrap — Keyword-based search engine fallback
# Triggered when no URLs discovered from query (bootstrap + rescue both empty)
# =============================================================================

_PUBLIC_BOOTSTRAP_SEARCH_ENGINES: tuple[str, ...] = ("duckduckgo", "yahoo", "bing", "startpage")
"""Fallback search engine order for keyword-based discovery bootstrap."""

_MAX_KEYWORD_BOOTSTRAP_URLS: int = 10  # hard cap per engine


async def generate_keyword_bootstrap_urls(
    query: str,
    max_urls: int = _MAX_KEYWORD_BOOTSTRAP_URLS,
) -> list[DiscoveryHit]:
    """
    Keyword-based search engine bootstrap — falls back through multiple engines.

    3.3 Public Discovery Bootstrap:
      Triggered when bootstrap + rescue + seed_context all returned zero URLs.
      Runs the original query against DuckDuckGo → Yahoo → Bing → Startpage
      in order, returning hits from the first engine that returns results.

    Bounded: at most max_urls DiscoveryHit per successful engine.
    Fail-safe: returns empty list for any error (network, import, timeout).
    Always-on: no feature flag — this is the final fallback before empty result.

    Args:
        query: The original OSINT query string.
        max_urls: Maximum hits to return (default 10, hard cap per engine).

    Returns:
        List of DiscoveryHit objects from first responding search engine.
        Empty list if all engines fail or return no hits.
    """
    if not query or not query.strip():
        return []

    for engine in _PUBLIC_BOOTSTRAP_SEARCH_ENGINES:
        try:
            raw_results = await _search_multi_engine_bootstrap(
                query,
                max_results=max_urls,
            )
            if not raw_results:
                continue

            hits: list[DiscoveryHit] = []
            for i, item in enumerate(raw_results[:max_urls]):
                url = item.get("url", "") if isinstance(item, dict) else getattr(item, "url", "")
                title = item.get("title", "") if isinstance(item, dict) else getattr(item, "title", "")
                snippet = item.get("snippet", "") if isinstance(item, dict) else getattr(item, "snippet", "")
                if not url:
                    continue
                hits.append(DiscoveryHit(
                    query=query,
                    title=title or f"{engine.capitalize()} result {i+1}",
                    url=url,
                    snippet=snippet or f"Keyword bootstrap via {engine}: {query}",
                    score=0.75,
                    reason=f"keyword_bootstrap_{engine}",
                    rank=i,
                    source=engine,
                    retrieved_ts=time.time(),
                ))

            if hits:
                return hits

        except Exception:
            # Fail-safe: try next engine
            continue

    return []


def _extract_domain_from_query(query: str) -> str | None:
    """
    Handles:
      - Plain domains: example.com, www.example.com, *.example.com
      - URLs: https://example.com/path, https://www.example.com/path
      - IP addresses: ignored (no domain bootstrap for IPs)
      - Mixed OSINT queries with domain as first token: "mozilla.org certificate transparency"
        (F233E: split on whitespace, try first token as domain)
      - Non-domain strings: returns None

    Returns:
        Lower-case domain string suitable for bootstrap URL construction,
        or None if no domain pattern found.
    """
    if not query:
        return None

    # Sprint F233E: Try to extract domain from mixed OSINT query.
    # Strategy: try the query as-is first (pure domain or URL), then try
    # the first whitespace-delimited token (for "mozilla.org certificate..." cases).
    candidates = [query]
    # Also add first token if query has whitespace
    if " " in query or "\t" in query:
        first_token = query.strip().split()[0]
        if first_token and first_token != query:
            candidates.append(first_token)

    for candidate in candidates:
        q = candidate
        # Strip common prefix operators used in OSINT queries
        for prefix in ("site:", "domain:", "url:"):
            if q.lower().startswith(prefix):
                q = q[len(prefix):]
                break

        # Strip trailing slashes and path components from URL
        q = q.rstrip("/")
        if "/" in q and "://" in q:
            # It's a full URL — extract just the host part
            try:
                import urllib.parse
                parsed = urllib.parse.urlparse(q)
                host = parsed.netloc or parsed.path.split("/")[0]
            except Exception:
                host = None
            if host:
                q = host

        # Remove common port suffix
        if ":" in q:
            q = q.rsplit(":", 1)[0]

        # Strip www. prefix for base domain
        if q.lower().startswith("www."):
            q = q[4:]

        # Remove wildcard prefix
        if q.startswith("*."):
            q = q[2:]

        # Validate: must look like a domain (has TLD with 2+ chars)
        # Must have at least one dot and a plausible TLD
        if not q or "." not in q:
            continue

        # Reject if it looks like an IP address
        import re as _re
        if _re.match(r"^\d{1,3}(\.\d{1,3}){3}$", q):
            continue

        # Reject if contains path-like characters (more than one / or unusual chars)
        # Domain should only contain letters, digits, hyphens, dots
        if not _re.match(r"^[a-zA-Z0-9.\-]+$", q):
            continue

        # Reject single-char TLDs or obviously invalid
        tld = q.rsplit(".", 1)[-1] if "." in q else ""
        if len(tld) < 2:
            continue

        return q.lower()

    return None


# -----------------------------------------------------------------------------
# DTOs
# -----------------------------------------------------------------------------


# Sprint F193B: Explicit fetch policy — policy-driven JS/DoH/stealth, not dormant defaults
from dataclasses import dataclass  # noqa: E402


@dataclass(frozen=True, slots=True)
class FetchPolicy:
    """Bounded fetch policy for canonical public sprint."""
    use_js: bool = False
    use_doh: bool = False
    use_stealth: bool = False

    @classmethod
    def default(cls) -> FetchPolicy:
        return cls()


    @classmethod
    def js_capable(cls) -> FetchPolicy:
        return cls(use_js=True)

    @classmethod
    def tor_like(cls) -> FetchPolicy:
        return cls(use_doh=True, use_stealth=True)




def _compute_fetch_policy(
    url: str,
    discovery_score: float | None,
    discovery_reason: str | None,
    strong_signal: bool,
) -> FetchPolicy:
    """
    Sprint F193B: Policy-driven fetch policy — JS/DoH/stealth driven by signal
    strength and URL class, not just dormant defaults.

    Policy rules:
    - discovery_score >= 0.7 OR strong_signal → use_js (JS-heavy page likely)
    - Onion/I2P/Freenet → tor_like policy (use_doh + use_stealth)
    - discovery_reason contains 'ct_' → DoH (accuracy for CT-log sources)
    - discovery_score >= 0.5 with moderate signal → use_doh only
    - everything else → default (plain fetch)

    Bounded: no network calls, no external state.
    """
    if ".onion" in url or ".i2p" in url or ".b32.i2p" in url or ".freenet" in url:
        return FetchPolicy.tor_like()

    if discovery_score is not None and discovery_score >= 0.7:
        return FetchPolicy.js_capable()
    if strong_signal:
        return FetchPolicy.js_capable()
    if discovery_reason and "ct_" in discovery_reason:
        return FetchPolicy(use_doh=True)
    if discovery_score is not None and discovery_score >= 0.5:
        return FetchPolicy(use_doh=True)
    return FetchPolicy.default()


# ---------------------------------------------------------------------------
# F232: Provider surface telemetry extraction
# ---------------------------------------------------------------------------


def _extract_provider_surface(
    discovery_result,
    selected_out: list,
    skipped_out: list,
    stub_out: list,
    errors_out: list,
    timeout_count_out: list,
    import_error_count_out: list,
    empty_reason_out: list,
) -> None:
    """
    Extract provider surface telemetry from a DiscoveryBatchResult (or mock).

    Writes into the provided mutable list arguments to avoid nonlocal issues
    in the enclosing pipeline function.
    Populates:
      - selected_out: providers with selected=True
      - skipped_out: [{provider, reason}] with selected=False
      - stub_out: providers in ADVISORY_STUB state
      - errors_out: [{provider, error, error_type}] provider-level errors
      - timeout_count_out[0]: incremented on timeout errors
      - import_error_count_out[0]: incremented on import/availability errors
      - empty_reason_out[0]: set to refined discovery_empty subtype
    """
    # discovery_result may be a real DiscoveryBatchResult or a mock with .hits/.error
    result_error = getattr(discovery_result, "error", None) or (discovery_result.get("error") if isinstance(discovery_result, dict) else None)  # noqa: E501
    error_str = str(result_error) if result_error else ""

    # F265C+P0-C: Handle both single DiscoveryBatchResult and list (cascade returns list)
    # Also handle CascadeResult wrapper that has .result attribute
    results_to_process: list = []
    if isinstance(discovery_result, list):
        # Cascade returns [ddg_result, hf_result, wb_result] — process each
        results_to_process = discovery_result
    else:
        # Single result — may be CascadeResult with .result wrapper
        _result = getattr(discovery_result, "result", discovery_result)
        results_to_process = [_result] if _result is not None else []

    psd: list | None = None
    for _res in results_to_process:
        _psd = getattr(_res, "provider_status_debug", None)
        if _psd is None and isinstance(_res, dict):
            _psd = _res.get("provider_status_debug")
        if _psd and isinstance(_psd, list):
            psd = _psd
            break

    if psd and isinstance(psd, list):
        for entry in psd:
            p = entry.get("provider", "") if isinstance(entry, dict) else getattr(entry, "provider", "")
            state = entry.get("state") if isinstance(entry, dict) else getattr(entry, "state", None)
            if hasattr(state, "value"):
                state = state.value
            state_str = str(state) if state is not None else ""

            if entry.get("selected"):
                selected_out.append(p)
            else:
                reason = entry.get("reason", "") if isinstance(entry, dict) else ""
                skipped_out.append({"provider": p, "reason": reason})

            if state_str == "advisory_stub":
                stub_out.append(p)

        # Extract query variants if present
        variants = []
        if isinstance(psd, list) and psd:
            first = psd[0] if psd else {}
            if isinstance(first, dict):
                variants = first.get("query_variants", [])
            elif hasattr(psd[0], "query_variants"):
                variants = psd[0].query_variants
        # variants populated via duckduckgo_adapter._build_query_variants
        # For DDG single-call path, record via hits query if available
        _hits_target = getattr(discovery_result, "hits", None) or (discovery_result.get("hits") if isinstance(discovery_result, dict) else None)
        if _hits_target and _hits_target:
            # derive from first hit query
            first_hit = _hits_target[0]
            q = getattr(first_hit, "query", "") or ""
            if q:
                variants.append(q)

    # Provider-level errors from DiscoveryBatchResult fields
    # P0-C: For list (cascade), use first result's error_type; for single result, use it directly
    error_type = ""
    provider_name_for_error = ""
    if isinstance(discovery_result, list) and results_to_process:
        # Cascade path: use first result that has an error
        for _res in results_to_process:
            _et = getattr(_res, "error_type", None) or ""
            _pn = getattr(_res, "provider_name", None) or ""
            _err = getattr(_res, "error", None)
            if _err:
                error_type = _et
                provider_name_for_error = _pn
                break
    else:
        # Single result path (original behavior)
        error_type = getattr(discovery_result, "error_type", None) or ""
        provider_name_for_error = getattr(discovery_result, "provider_name", None) or ""

    if error_str:
        errors_out.append({"provider": provider_name_for_error or "", "error": error_str, "error_type": error_type})
        if error_type == "timeout" or "timeout" in error_str.lower():
            timeout_count_out[0] += 1
            if not empty_reason_out:
                empty_reason_out.append("provider_timeout")
        elif error_type == "provider_exception" or "exception" in error_str.lower():
            import_error_count_out[0] += 1
            if not empty_reason_out:
                empty_reason_out.append("provider_unavailable")
        elif error_str == "empty_query":
            if not empty_reason_out:
                empty_reason_out.append("query_builder_empty")
        elif not hits_from_result(discovery_result):
            if not empty_reason_out:
                empty_reason_out.append("provider_returned_zero")

    # If no providers selected at all — F234-FIX: preserve specific reason if already set
    # Previously this would overwrite "provider_returned_zero" / "provider_timeout" etc.
    if not selected_out and not psd:
        if not empty_reason_out:
            empty_reason_out.append("no_provider_selected")
        else:
            # A specific reason (provider_timeout, provider_returned_zero, etc.) was
            # already set by the error-handling above. Preserve it instead of overwriting
            # with the generic "no_provider_selected". This provides better diagnostics.
            pass

    # F232: When hits are empty and no specific reason set yet, set provider_returned_zero
    # This handles the case where provider returned zero without an error string
    if not hits_from_result(discovery_result) and not empty_reason_out:
        empty_reason_out.append("provider_returned_zero")


def hits_from_result(discovery_result) -> tuple:
    """Extract hits from DiscoveryBatchResult or dict."""
    if hasattr(discovery_result, "hits"):
        return discovery_result.hits
    if isinstance(discovery_result, dict):
        return discovery_result.get("hits", ())
    return ()


class PipelinePageResult(msgspec.Struct, frozen=True, gc=False):
    """Result of processing a single discovered page."""

    url: str
    fetched: bool
    matched_patterns: int
    accepted_findings: int
    stored_findings: int
    error: str | None = None
    quality_reason: str | None = None  # why page was good/weak/skipped
    discovery_score: float | None = None  # signal strength from discovery hit
    discovery_reason: str | None = None  # reason from discovery hit
    discovery_signal: bool = False  # True if hit had score >= 0.3 or reason
    # Sprint F150L: usable-value layer — conversion story per page
    usable_signal: bool = False  # True if page converted to usable value
    value_tier: str = "none"  # high | medium | low | waste
    resolution_reason: str = ""  # why this page resolved the way it did
    # Sprint F161B: conversion truth surfaces
    discovery_false_positive: bool = False  # True if discovery signal was legitimate but page converted to waste
    waste_category: str = ""  # "" | "structural" | "signalless" | "false_positive" | "error"
    structural_quality: str = ""  # "" | "healthy" | "thin" | "dead"
    # Sprint F170D: fetch accessibility truth — failure_stage from FetchResult
    failure_stage: str | None = None  # validation | connection | tls | http | body | size
    # Sprint F171A: redirect truth surfaces — redirect-induced non-content vs weak conversion
    redirected: bool = False  # True when page was redirected (final_url != original_url)
    redirect_target: str | None = None  # redirect destination URL when redirected=True
    # F207F: PUBLIC Yield — per-page JS/feed skip telemetry
    js_renderer_skipped_reason: str | None = None  # xml_or_feed_url | xml_recovered | browser_unavailable
    fetch_blocked_reason: str | None = None  # uma_memory | quality_skip (page not fetched due to gate)
    # F207J-C: PUBLIC Acceptance — per-page acceptance rejection reason
    # None = accepted | rejection reason string
    rejection_reason: str | None = None
    # F208G-A: PUBLIC Yield Taxonomy — canonical terminal classification per URL
    # None = still processing | "accepted" | "skipped_*" | "rejected_*"
    terminal_reason: str | None = None
    # F226B: PUBLIC acceptance uplift — per-page duplicate signal for public_surface findings
    public_surface_dup: bool = False
    # F231A: PUBLIC Candidate Ledger — stage progression per URL
    # build_attempted: page passed quality gate and entered finding-build phase
    build_attempted: bool = False


class PipelineRunResult(msgspec.Struct, frozen=True, gc=False):
    """Top-level result of a full pipeline run."""

    query: str
    discovered: int
    fetched: int
    matched_patterns: int
    accepted_findings: int
    stored_findings: int
    patterns_configured: int
    pages: tuple[PipelinePageResult, ...]
    error: str | None = None
    # Sprint F150I: branch economics observability (additive)
    strong_pages: int = 0  # very_good tier, high yield
    weak_pages_skipped: int = 0  # SKIP_WEAK early exits (Fix B: was error-based, now quality_reason-based)
    low_value_fetches: int = 0  # fetched but matched nothing + poor quality
    # Sprint F150J: derived value counters
    discovery_strong_content_weak: int = 0  # discovery signal but zero pattern yield
    discovery_and_content_strong: int = 0  # both discovery signal and pattern yield
    # Sprint F150K: additional derived economics signals (additive)
    discovery_squandered: int = 0  # strong discovery hit but page quality weak
    noise_fetch_ratio: float = 0.0  # ratio of fetched pages that yielded zero patterns
    corroboration_vs_burn: float = 0.0  # corroboration signal vs pure budget burn
    public_next_action: str = ""  # operator-facing one-liner next action hint
    public_confidence_note: str = ""  # operator-facing confidence note
    # Sprint F150J: condensed public-branch verdict (additive dict)
    public_branch_verdict: dict = {}
    # Sprint F150L: usable-value run-level aggregates
    usable_findings_ratio: float = 0.0  # stored_findings / max(discovered, 1)
    discovery_to_findings_efficiency: float = 0.0  # discovery_and_content_strong / max(discovered, 1)
    quality_mix: str = ""  # high|medium|low|waste composition summary
    public_proof_grade: str = ""  # proof quality of the public branch run
    public_value_density: float = 0.0  # stored_findings / max(fetched, 1)
    top_waste_pattern: str = ""  # dominant reason pages went to waste (heuristic)
    # Sprint F161B: conversion truth run-level aggregates
    discovery_false_positive_count: int = 0  # pages with discovery signal but no conversion
    waste_category_counts: dict = {}  # {"structural": N, "signalless": N, "false_positive": N, "error": N}
    structural_health_ratio: float = 0.0  # fraction of fetched pages with structural_quality=healthy
    # Sprint F162B: factual value density + clean waste code
    factual_value_density: float = 0.0  # stored / fetched (real conversion density)
    run_waste_pattern_code: str = ""   # dominant waste category clean code
    waste_reason_breakdown: str = ""   # waste category distribution
    # Sprint F163B: backend degradation flag — true when fetch errors dominate discovery output
    backend_degraded: bool = False
    # Sprint F170D: lower-layer truth consumption — discovery block / fetch accessibility
    # None | "uma_emergency_abort" | "backend_error_no_fallback" | "backend_error_fallback_failed"
    public_discovery_blocker: str | None = None
    # True when any page had fetch accessibility failure (DNS/TLS/connection/timeout)
    public_fetch_accessibility_blocker: bool = False
    # None | "primary_failed_fallback_succeeded" | "primary_failed_fallback_failed" | "no_fallback_needed"
    public_discovery_fallback_state: str | None = None
    # Dominant failure mode across all pages and discovery
    dominant_public_failure_mode: str | None = None
    # Sprint F213B: PUBLIC stage accounting — actionable failure classification
    public_stage_failure: str | None = None  # discovery_empty | fetch_zero | None
    public_stage_failure_reason: str | None = None  # human-readable reason
    # Sprint F213B: PUBLIC discovery stage counters
    public_discovery_attempted: bool = False  # discovery was called
    public_discovery_raw_count: int = 0  # raw URLs from discovery (before dedup)
    public_discovery_deduped_count: int = 0  # URLs after dedup (candidates for fetch)
    # Sprint F213B: PUBLIC page/finding acceptance counters
    public_pages_fetched: int = 0  # pages where fetch was called
    public_pages_accepted: int = 0  # pages with accepted_findings > 0
    public_pages_rejected: int = 0  # pages with accepted_findings == 0
    public_findings_accepted: int = 0  # total findings accepted from public lane
    # Sprint F173C: zero-hit evidence — bounded surfaces for next gate
    # zero_hit_accessible_fetch_count: pages that were fetched (fetched=True) with 0 pattern matches
    # (distinct from discovery_strong_content_weak which includes SKIP-tier pages)
    zero_hit_accessible_fetch_count: int = 0
    # Sprint F188B: CT winner slice — bounded CT-discovered subdomain count (additive)
    ct_subdomain_injected: int = 0
    # F192E: CommonCrawl CDX — bounded CC-discovered archive URL count (additive)
    cc_archive_injected: int = 0
    # F193B: Academic discovery persisted findings count (additive)
    academic_findings_count: int = 0
    # P20: PastebinMonitor + GitHubSecretScanner telemetry (additive)
    pastebin_findings_count: int = 0
    github_secrets_count: int = 0
    # Sprint F217C: Deterministic bootstrap telemetry
    public_bootstrap_enabled: bool = False  # True when bootstrap URLs were generated
    public_bootstrap_candidates_count: int = 0  # bootstrap URLs generated from query
    public_bootstrap_fetch_attempted: int = 0  # bootstrap URLs sent to fetch
    public_bootstrap_fetch_success: int = 0  # bootstrap URLs that fetched successfully
    public_bootstrap_accepted_findings: int = 0  # findings accepted from bootstrap hits
    public_bootstrap_errors: int = 0  # bootstrap-specific errors (parse, dedup, etc.)
    # Sprint F229A: Bootstrap ordering telemetry
    public_bootstrap_order: str = "disabled"  # "before_discovery" | "after_discovery" | "disabled"
    public_bootstrap_prevented_discovery_timeout: bool = False  # True when bootstrap produced candidates but discovery would have returned zero  # noqa: E501
    public_bootstrap_first_fetch_attempted: bool = False  # True when bootstrap hits were added to hits before fetch
    # Sprint F220C: Public Provider Rescue telemetry
    public_rescue_candidates_count: int = 0  # rescue URLs generated from threat query
    public_rescue_fetch_attempted: int = 0  # rescue URLs sent to fetch
    public_rescue_fetch_success: int = 0  # rescue URLs that fetched successfully
    public_rescue_accepted_findings: int = 0  # findings accepted from rescue hits
    public_rescue_errors: int = 0  # rescue-specific errors
    public_rescue_order: str = "disabled"  # "rescue_fallback" | "keyword_seed_fallback" | "disabled"
    # F1-3: keyword_seed_fallback — True when rescue URLs generated for threat query with disabled bootstrap
    keyword_seed_fallback_triggered: bool = False
    # zero_hit_quality_reason_counts: breakdown of WHY zero-hit pages failed
    # keys are the specific quality_reason values from PipelinePageResult
    zero_hit_quality_reason_counts: dict = {}
    # zero_hit_title_samples: bounded title+URL sample for zero-hit pages (max 5, no raw text)
    zero_hit_title_samples: tuple = ()
    # public_zero_hit_summary: run-level structured summary for gate review
    public_zero_hit_summary: dict = {}
    # F207F: PUBLIC Yield — discovered→fetched gap telemetry
    public_discovered: int = 0  # URLs discovered in public lane
    public_fetch_attempted: int = 0  # fetch() called for public URLs
    public_fetch_skipped: int = 0  # fetch skipped (UMA, quality gate, etc.)
    public_fetch_skip_reason: str | None = None  # uma_memory | quality_skip | error
    public_js_renderer_unavailable: int = 0  # JS renderer skipped due to browser unavailable
    public_xml_or_rss_detected: int = 0  # JS renderer skipped due to XML/feed URL
    public_fetch_timeout_count: int = 0  # fetch timeouts in public lane
    public_fetch_blocked_by_memory: int = 0  # skipped due to UMA critical
    # F207I-A: PUBLIC Yield — discovery→fetch transition invariants + telemetry
    public_discovery_cache_hit: int = 0  # DDG queries served from per-run cache
    public_discovery_query_count: int = 0  # total DDG queries issued this run
    public_fetch_candidate_count: int = 0  # URLs queued for fetch
    public_fetch_gate: str = "none"  # memory gate verdict: ok | critical_limited | emergency_blocked
    public_fetch_attempted_urls_sample: tuple[str, ...] = ()  # first 5 fetched URLs
    # F207J-C: PUBLIC Acceptance — post-fetch acceptance/rejection telemetry
    public_acceptance_attempted: int = 0  # pages where fetch succeeded (fetched=True)
    public_acceptance_accepted: int = 0  # pages with accepted_findings > 0
    public_acceptance_rejected: int = 0  # pages with accepted_findings == 0 (post-fetch rejection)
    # rejection reason breakdown: {reason: count}
    public_acceptance_reject_reasons: dict = {}
    # bounded URL samples (max 5 each)
    public_accepted_url_sample: tuple[str, ...] = ()
    public_rejected_url_sample: tuple[str, ...] = ()
    # F208G-A: PUBLIC Yield Taxonomy — run-level terminal classification
    # URL-level counts
    public_terminal_classified_count: int = 0  # URLs with terminal_reason != None
    public_unclassified_count: int = 0  # URLs with terminal_reason == None
    public_terminal_reason_counts: dict = {}  # {terminal_reason: count} for all classified URLs
    # Fetch outcome counts
    public_fetch_success: int = 0  # fetched=True with text available
    public_fetch_failed: int = 0  # fetched=False (all skip/error reasons)
    # Skipped reason breakdown
    public_skipped_duplicate: int = 0  # dedup bloom filter hit
    public_skipped_unsupported_scheme: int = 0  # non-http(s) URL
    public_skipped_memory_gate: int = 0  # UMA emergency/critical blocked
    public_skipped_quality_gate: int = 0  # discovery score too low
    public_skipped_browser_unavailable: int = 0  # JS renderer unavailable
    public_skipped_xml_or_feed: int = 0  # XML/feed URL detected
    public_skipped_timeout: int = 0  # fetch timed out
    public_skipped_fetch_error: int = 0  # fetch exception/error
    # Rejected reason breakdown (fetched but not accepted)
    public_rejected_no_pattern_match: int = 0  # fetched text had no pattern matches
    public_rejected_low_information: int = 0  # page quality too low (SKIP_WEAK)
    public_rejected_duplicate: int = 0  # per-page dedup exhausted
    public_rejected_storage_rejected: int = 0  # DuckDB storage rejected findings
    # F226B: PUBLIC acceptance uplift diagnostics
    public_build_success_count: int = 0  # public_surface findings built (pattern-miss pages)
    public_build_failure_count: int = 0  # public_surface build attempts that returned empty
    public_duplicate_count: int = 0  # public_surface findings rejected as duplicate
    public_acceptance_ratio: float = 0.0  # build_success / max(build_success+build_failure, 1)
    # Bounded URL samples (max 5 each)
    public_skipped_url_sample: tuple[str, ...] = ()  # skipped URL samples
    public_rejected_url_samples: tuple[str, ...] = ()  # rejected URL samples

    # F231A: PUBLIC Candidate Ledger — stage progression summary
    # discovery → fetch_attempted → fetch_success → parse_success → pattern_matched → built → store_attempted → stored/rejected  # noqa: E501
    public_candidates_discovered: int = 0
    public_candidates_fetch_attempted: int = 0
    public_candidates_fetch_success: int = 0
    public_candidates_parse_success: int = 0
    public_candidates_pattern_matched: int = 0
    public_candidates_built: int = 0
    public_candidates_store_attempted: int = 0
    public_candidates_stored: int = 0
    public_candidates_rejected: int = 0
    public_rejection_summary: dict = {}  # {stage: count} where candidates were lost
    # F231A: Canonical terminal stage — where PUBLIC evidence stream terminated
    public_terminal_stage: str = ""  # discovery_empty | fetch_zero | parse_zero | match_zero | build_zero | store_zero | accepted  # noqa: E501
    # F232: Provider surface telemetry — discovery provider selection and outcome truth
    # NOTE: msgspec.Struct does NOT support dataclasses.field(default_factory=...);
    # using mutable default=[] is safe here because PipelineRunResult is frozen=True,
    # so mutation is blocked at the struct level.
    public_provider_selected: list[str] = []  # providers with selected=True
    public_provider_skipped: list[dict] = []  # [{provider, reason}] with selected=False
    public_provider_stub: list[str] = []  # providers in ADVISORY_STUB state
    public_provider_errors: list[dict] = []  # [{provider, error, error_type}] provider-level errors
    public_query_variants: list[str] = []  # query variants emitted to providers
    public_provider_timeout_count: int = 0  # providers that timed out
    public_provider_import_error_count: int = 0  # providers that failed to import/initialize
    # F232: Refined discovery_empty subtypes — explicit reason when discovery returns zero
    public_discovery_empty_reason: str = ""  # no_provider_selected | provider_unavailable | provider_timeout | provider_returned_zero | query_builder_empty  # noqa: E501


# -----------------------------------------------------------------------------
# UMA helpers
# -----------------------------------------------------------------------------


async def _get_uma_state() -> tuple[str, bool]:
    """
    Read UMA status via 8AB surface.
    Returns (state_str, io_only_hint).
    Raises: propagates any exception from resource_governor.

    Sprint 8AK: Uses SSOT labels from resource_governor — no localUMA interpretation.
    ISSUE-003 FIX: Uses sample_uma_status_async() instead of sample_uma_status()
    to avoid blocking the event loop with threading.RLock in _record_transition().
    """
    # Sprint 8AB surface — lazy import to avoid module-level side effects
    from hledac.universal.core.resource_governor import (
        evaluate_uma_state,
        sample_uma_status_async,
    )

    status = await sample_uma_status_async()
    state = evaluate_uma_state(status.system_used_gib)
    io_only = status.io_only
    return state, io_only


# -----------------------------------------------------------------------------
# HTML extraction helpers
# -----------------------------------------------------------------------------


class _HTMLTextExtractor(html.parser.HTMLParser):
    """
    Lightweight HTMLParser that collects only text from body-level tags
    and collapses whitespace. Fail-soft: never raises on malformed HTML.
    """

    __slots__ = ("_in_body", "_chunks", "_last_end")

    def __init__(self) -> None:
        super().__init__()
        self._in_body = False
        self._chunks: list[str] = []
        self._last_end = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]  # noqa: ARG002
    ) -> None:
        if tag in ("body", "div", "p", "tr", "li", "article", "section", "main"):
            if not self._chunks or self._chunks[-1] != " ":
                self._chunks.append(" ")
        elif tag in ("br", "hr"):
            if self._chunks and self._chunks[-1] != " ":
                self._chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in (
            "body", "div", "p", "tr", "li", "article", "section", "main", "h1",
            "h2", "h3", "h4", "h5", "h6", "ul", "ol",
        ):
            if self._chunks and self._chunks[-1] != " ":
                self._chunks.append(" ")

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._chunks.append(stripped)
            if self._chunks[-1] != " ":
                self._chunks.append(" ")

    def get_text(self) -> str:
        result = "".join(self._chunks)
        # Collapse any runs of whitespace to single space
        result = re.sub(r"\s+", " ", result).strip()
        return result


def _html_to_text(html_content: str) -> str:
    """
    Convert HTML to plain text using stdlib HTMLParser.
    Runs in calling thread (caller is responsible for asyncio.to_thread).
    """
    try:
        parser = _HTMLTextExtractor()
        parser.feed(html_content)
        text = parser.get_text()
    except Exception:
        # Defensive: fall back to stripping tags via regex
        text = re.sub(r"<[^>]+>", " ", html_content)
        text = re.sub(r"\s+", " ", text).strip()
    return text


# -----------------------------------------------------------------------------
# Finding ID helper
# -----------------------------------------------------------------------------

def _make_finding_id(
    query: str, url: str, label: str, pattern: str, value: str
) -> str:
    """
    Deterministic finding ID via SHA-256 hash of pipeline inputs.
    hash() is forbidden (non-deterministic across processes).
    """
    key = f"{query}\x00{url}\x00{label}\x00{pattern}\x00{value}"
    # xxhash — non-cryptographic, 10-20× faster than sha256 for dedup keys
    # F265C: Use centralized rust backend
    try:
        from core.rust_backend import rust as _rust_backend

        if _rust_backend.is_available and _rust_backend.hash is not None:
            return _rust_backend.hash.content_hash_hex(key)
        raise ImportError("Rust hash not available")
    except Exception:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# -----------------------------------------------------------------------------
# Context window helper
# -----------------------------------------------------------------------------
# Sentinel: use a private module-level constant so the call site is self-explanatory
_NO_HIT_START = object()


def _pattern_context(
    text: str,
    start: int,
    end: int,
    radius: int = _FINDING_ID_CONTEXT_RADIUS,
) -> str:
    """
    Extract a context window around a pattern hit.
    Runs in calling thread (caller is responsible for asyncio.to_thread).
    """
    if start is _NO_HIT_START or end is _NO_HIT_START:
        return text[:MAX_EXTRACTED_TEXT_CHARS]
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    return text[lo:hi]


def _js_confidence_from_verdict(
    verdict: str,
    status_code: int | None = None,
    content_length: int | None = None,
) -> float:
    """Derive js_confidence from verdict string and response signals."""
    if "RETRY_JS:thin_text_strong_signal" in verdict:
        return 0.85
    if "RETRY_JS" in verdict:
        return 0.70
    if status_code in (403, 429):
        return 0.45
    if content_length is not None and content_length < 500:
        return 0.55
    return 0.30


# -----------------------------------------------------------------------------
# Text enrichment with discovery metadata (Sprint F150I)
# Prepend title/snippet to extracted text so pattern scanner gets better signal.
# Hard-capped, M1-safe, no new dependency.
# -----------------------------------------------------------------------------


def _enrich_text_with_metadata(
    title: str,
    snippet: str,
    extracted_text: str,
) -> str:
    """
    Build a bounded scan text from: [title] [snippet] [extracted_content].

    Rationale: title + snippet contain query-aware signal that raw HTML→text
    loses (e.g. search engine bolded terms). Prepending them gives pattern
    matcher better context without any LLM or external call.

    The result is hard-capped at MAX_EXTRACTED_TEXT_CHARS.

    FIX (F300): HTML-strip title and snippet before concatenation.
    Discovery providers return raw HTML in title/snippet (e.g. <b>bold</b> terms).
    Without stripping, HTML tag characters (<, >, /) create false word boundaries
    in PatternMatcher's boundary_policy="word" check, causing zero matches.
    """
    # FIX F300: Strip HTML from title and snippet before enrichment
    # Uses the same proven function as the feed pipeline (pipeline/scoring.py)
    try:
        from hledac.universal.pipeline.scoring import _strip_html_tags_from_text
    except ImportError:
        def _strip_html_tags_from_text(text: str) -> str:
            """Minimal fallback: strip <...> tags naively."""
            if not text:
                return ""
            import re as _re
            return _re.sub(r'<[^>]+>', ' ', text).strip()
    title_clean = _strip_html_tags_from_text(title) if title else ""
    snippet_clean = _strip_html_tags_from_text(snippet) if snippet else ""

    # Build metadata prefix bounded to MAX_METADATA_PREPEND_CHARS
    meta_parts: list[str] = []
    remaining_meta = MAX_METADATA_PREPEND_CHARS

    if title_clean:
        title_trunc = title_clean[:remaining_meta]
        meta_parts.append(title_trunc)
        remaining_meta -= len(title_trunc)

    if snippet_clean and remaining_meta > 20:
        snippet_trunc = snippet_clean[:remaining_meta]
        meta_parts.append(snippet_trunc)

    meta_prefix = "\n".join(meta_parts) + "\n---\n"

    # Hard cap: meta_prefix + extracted_text capped at MAX_EXTRACTED_TEXT_CHARS
    max_content = MAX_EXTRACTED_TEXT_CHARS - len(meta_prefix)
    if max_content < 0:
        # meta_prefix alone exceeds cap — truncate it
        meta_prefix = meta_prefix[:MAX_EXTRACTED_TEXT_CHARS]
        max_content = 0

    content = extracted_text[:max_content] if max_content > 0 else ""

    return meta_prefix + content


# -----------------------------------------------------------------------------
# Page quality scoring (Sprint F150I)
# Query-aware heuristic for fetch budget prioritization.
# Bounded, no ML, no external calls.
# -----------------------------------------------------------------------------


def _score_page_quality(
    *,
    hit_url: str,
    hit_title: str,
    hit_snippet: str,
    hit_rank: int,
    query: str,
    extracted_text: str,
    discovery_score: float | None = None,
    discovery_reason: str | None = None,
) -> str:
    """
    Return a short quality tier string for a discovered page.

    Signals (compositional, no ML):
    - query-term density in title/snippet
    - URL structural depth
    - text richness (avg word len + word count)
    - discovery hit score / reason (if present)
    - rank priority (top-5 benefit of doubt)
    - pre-filter: skip extremely thin pages

    Returns one of:
      SKIP_WEAK: below minimum — skip immediately
      RETRY_JS: thin text but strong discovery signal — retry with JS rendering (F275)
      weak_low_signal: poor signals even after fetch
      ok: acceptable but not exceptional
      good: strong multi-dimensional signals
      very_good: exceptional signals, full investment warranted
    """
    # --- Discovery signal blend (additive, fail-soft) ------------
    has_discovery_signal = (
        (discovery_score is not None and discovery_score >= _DISCOVERY_SIGNAL_SCORE_THRESHOLD)
        or (discovery_reason is not None and discovery_reason.strip() != "")
    )
    strong_discovery = (
        discovery_score is not None and discovery_score >= 0.7
    )

    query_lower = query.lower()
    query_terms = frozenset(query_lower.split())

    # --- Title query-term density FIRST (F275) ---
    # Moved before text-length gate so title-rich pages with thin body bypass the gate
    title_words = frozenset(hit_title.lower().split())
    title_query_hits = len(query_terms & title_words)
    title_has_query = title_query_hits > 0

    # --- Snippet query-term density ---
    snippet_words = frozenset(hit_snippet.lower().split())
    snippet_query_hits = len(query_terms & snippet_words)
    snippet_has_query = snippet_query_hits > 0

    # --- Pre-filter: skip pages with almost no content BEFORE signal scoring ---
    # Sprint F163B: apply text-length gate first — avoids wasting compute on dead pages
    # F275: Relaxed gate — title-rich pages (query terms in title) bypass text gate
    title_rich = title_has_query and title_query_hits >= 1
    snippet_rich = snippet_has_query and snippet_query_hits >= 2
    if len(extracted_text) < _PRE_FETCH_TEXT_MIN_CHARS:
        # F275: title/snippet-rich pages survive even with thin body (metadata pages)
        if title_rich or snippet_rich:
            pass  # proceed to scoring
        # F275: RETRY_JS — thin page but strong discovery signal → try JS rendering
        elif strong_discovery:
            return "RETRY_JS:thin_text_strong_signal"
        else:
            return "SKIP_WEAK:very_low_text"

    # --- Signalless gate: very low word-level entropy = spam/placeholder ---
    # Sprint F163B: detect "lorem ipsum" / repetitive filler / template noise
    # This is orthogonal to text length — catches thin-but-long pages
    words = extracted_text.split()
    if len(words) >= 10:
        unique_ratio = len(frozenset(w.lower() for w in words)) / len(words)
        if unique_ratio < 0.25:
            return "SKIP_WEAK:low_entropy"

    # --- URL structural signal -----------------------------------
    url_has_path = "/" in hit_url and len(hit_url.split("/")) > 3

    # --- Text richness -----------------------------------------
    text_len = len(extracted_text)
    word_count = len(extracted_text.split())
    avg_word_len = text_len / max(word_count, 1)
    text_is_meaningful = avg_word_len >= 3.5 and word_count >= 50

    # --- Composite scoring --------------------------------------
    signals_good = sum([
        title_has_query,
        snippet_has_query,
        url_has_path,
        text_is_meaningful,
    ])
    if strong_discovery:
        signals_good += 1  # discovery bonus

    # P2.1: If URL was discovered via bootstrap and is highly relevant, lower pattern match threshold.
    # Bootstrap sources (deterministic, seed_context, rescue, keyword_bootstrap) have synthetic
    # titles/snippets with no query terms but the URL itself is directly related to the query
    # (domain/URL query), so the bootstrap bonus compensates for the lack of title/snippet signal
    # while preserving quality filtering for non-bootstrap URLs.
    _is_bootstrap = (
        discovery_reason in ("deterministic_bootstrap", "seed_context_bootstrap", "rescue")
        or (discovery_reason or "").startswith("keyword_bootstrap_")
    )
    if _is_bootstrap:
        signals_good += 1

    rank_bonus = hit_rank < 5

    # --- Tier determination -------------------------------------
    if signals_good >= 4 or (signals_good >= 3 and (rank_bonus or strong_discovery)):
        return "very_good"
    elif signals_good >= 3:
        return "good"
    elif signals_good >= 2:
        return "ok"
    elif signals_good >= 1:
        return "ok"
    elif has_discovery_signal and text_is_meaningful and text_len > 1000:
        return "ok:no_query_signal"
    else:
        return "weak_low_signal"


# -----------------------------------------------------------------------------
# Per-page usable-value computation (Sprint F150L)
# Bounded heuristic — no new analysis, purely derived from existing buckets.
# -----------------------------------------------------------------------------


def _compute_page_usable_fields(
    *,
    fetched: bool,
    matched_patterns: int,
    stored_findings: int,
    quality_reason: str | None,
    discovery_signal: bool,
    discovery_score: float | None,
    error: str | None,
    extracted_text_len: int = 0,
) -> tuple[bool, str, str, bool, str, str]:
    """
    Derive usable_signal, value_tier, resolution_reason, discovery_false_positive,
    waste_category, structural_quality from existing page data.

    usable_signal: page contributed to real output (stored findings or strong signal).
    value_tier: conversion quality — high/medium/low/waste.
    resolution_reason: human-readable why the page resolved as it did.
    discovery_false_positive: True if discovery signal was legitimate but page wasted.
    waste_category: "" | "structural" | "signalless" | "false_positive" | "error"
    structural_quality: "" | "healthy" | "thin" | "dead"

    All derived from existing fields — no new heavy analysis.
    """
    if not fetched or error is not None:
        tier = "waste"
        reason = f"unfetched_or_error:{error or 'none'}"
        false_pos = False
        waste_cat = "error"
        structural = "dead"
        return False, tier, reason, false_pos, waste_cat, structural

    if stored_findings > 0:
        tier = "high"
        reason = "stored_findings"
        false_pos = False
        waste_cat = ""
        structural = "healthy"
        return True, tier, reason, false_pos, waste_cat, structural

    if matched_patterns > 0 and discovery_signal:
        tier = "medium"
        reason = "patterns_found_discovery_signal"
        false_pos = False
        waste_cat = ""
        structural = "healthy"
        return True, tier, reason, false_pos, waste_cat, structural

    if matched_patterns > 0:
        tier = "medium"
        reason = "patterns_found_no_discovery"
        false_pos = False
        waste_cat = ""
        structural = "healthy"
        return True, tier, reason, false_pos, waste_cat, structural

    # Fetched but nothing matched — distinguish waste categories
    # Sprint F163B: signalless detection BEFORE SKIP_WEAK — signalless is a real category
    if not discovery_signal:
        # No discovery signal at all — signalless waste (not structural)
        tier = "waste"
        reason = quality_reason or "no_discovery_signal"
        false_pos = False
        waste_cat = "signalless"
        structural = "thin" if extracted_text_len < _PRE_FETCH_TEXT_MIN_CHARS else "healthy"
        return False, tier, reason, false_pos, waste_cat, structural

    if discovery_score is not None and discovery_score >= _DISCOVERY_FALSE_POSITIVE_THRESHOLD:
        # Sprint F161B: legitimate discovery signal, no pattern yield = false positive
        tier = "low"
        reason = "discovery_signal_no_patterns"
        false_pos = True
        waste_cat = "false_positive"
        structural = "healthy" if extracted_text_len >= _PRE_FETCH_TEXT_MIN_CHARS else "thin"
        return False, tier, reason, false_pos, waste_cat, structural

    if quality_reason is not None and quality_reason.startswith("SKIP_WEAK"):
        tier = "waste"
        reason = f"quality_skip:{quality_reason}"
        false_pos = False
        waste_cat = "structural"
        structural = "thin"
        return False, tier, reason, false_pos, waste_cat, structural

    # F275: RETRY_JS verdict — in-flight JS retry attempt, not yet resolved
    if quality_reason is not None and quality_reason.startswith("RETRY_JS"):
        tier = "medium"
        reason = f"js_retry_pending:{quality_reason}"
        false_pos = False
        waste_cat = ""
        structural = "thin" if extracted_text_len < _PRE_FETCH_TEXT_MIN_CHARS else "healthy"
        return False, tier, reason, false_pos, waste_cat, structural

    # Final fallback
    tier = "waste"
    reason = quality_reason or "no_match_no_signal"
    false_pos = False
    waste_cat = "signalless"
    structural = "thin" if extracted_text_len < _PRE_FETCH_TEXT_MIN_CHARS else "healthy"
    return False, tier, reason, false_pos, waste_cat, structural


# -----------------------------------------------------------------------------
# PatternMatcher helpers
# -----------------------------------------------------------------------------


def _get_patterns_configured_count() -> int:
    """Return current pattern count from singleton registry (0 if dirty/empty)."""
    state = sys.modules["hledac.universal.patterns.pattern_matcher"]._matcher_state
    return len(state._registry_snapshot) if state._registry_snapshot else 0


# -----------------------------------------------------------------------------
# Per-page finding extraction
# -----------------------------------------------------------------------------


async def _build_public_finding(
    *,
    query: str,
    url: str,
    page_text: str,
    hit_title: str,
    hit_snippet: str,
    discovery_score: float | None,
    discovery_reason: str | None,
    http_status_code: int = 0,
) -> tuple:
    """
    F226B: Build a public-surface CanonicalFinding from a non-pattern-maching page.

    Called when a page fetches successfully, extracts text, but has zero pattern
    matches AND is NOT skipped by quality gate (SKIP_WEAK) — i.e. a "content-only" page
    that provides public surface evidence.

    Also called for bootstrap pages (robots.txt, security.txt, sitemap.xml) that
    have meaningful content even without pattern matches.

    Does NOT bypass quality gate — SKIP_WEAK pages still return empty tuple.

    Returns:
        Tuple of (CanonicalFinding,) or () if page provides no actionable signal.
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    # P0-FIX (F290): Accept title+snippet even without body text.
    # SERP pages often have no body content but meaningful title/snippet.
    if not page_text or not page_text.strip():
        # Only return () if we have NEITHER title NOR snippet
        if not hit_title and not hit_snippet:
            return ()
        # Fall through with empty page_text — title/snippet will still be used

    # Bounded payload from title + snippet + first chars of body + status
    payload_parts: list[str] = []
    if hit_title:
        payload_parts.append(f"title: {hit_title[:200]}")
    if hit_snippet:
        payload_parts.append(f"snippet: {hit_snippet[:300]}")
    # Include first 500 chars of body as surface evidence (may be empty)
    body_preview = page_text[:500].strip() if page_text else ""
    if body_preview:
        payload_parts.append(f"body: {body_preview}")
    if http_status_code > 0:
        payload_parts.append(f"status: {http_status_code}")
    if not payload_parts:
        return ()

    payload_text = "\n".join(payload_parts)
    # Hard cap
    if len(payload_text) > 2000:
        payload_text = payload_text[:2000]

    # Provenance tags
    provenance_parts = [
        "source_family:public",
        f"url:{url[:300]}",
        "label:public_surface",
    ]
    if discovery_score is not None:
        provenance_parts.append(f"score:{discovery_score:.2f}")
    if discovery_reason:
        provenance_parts.append(f"reason:{discovery_reason[:100]}")
    provenance: tuple[str, ...] = tuple(provenance_parts)

    # Deterministic finding_id using same scheme as pattern findings
    finding_id = _make_finding_id(
        query=query,
        url=url,
        label="public_surface",
        pattern="content_only",
        value=payload_text[:100],
    )

    try:
        finding = CanonicalFinding(
            finding_id=finding_id,
            query=query[:500],
            source_type=_PUBLIC_SOURCE_TYPE,
            confidence=0.65,  # P0-B FIX: Raised from 0.55 — bootstrap SERP pages are valid discovery
            ts=time.time(),
            provenance=provenance,
            payload_text=payload_text,
        )
        return (finding,)
    except Exception:
        return ()


async def _extract_live_public_findings_from_page(
    *,
    query: str,
    url: str,
    hit_label: str,
    hit_pattern: str,
    hit_value: str,
    hit_start: int,
    hit_end: int,
    page_text: str,
    discovery_score: float | None = None,
) -> tuple:  # CanonicalFinding — imported lazily to satisfy runtime
    """
    Construct CanonicalFinding for a single PatternHit.
    All heavy work (context extraction) offloaded to thread executor.
    """
    # Lazy import to avoid TYPE_CHECKING-only circular issues at runtime
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    from utils.rayon_pool import run_in_cpu_pool_async

    # Extract context in thread to avoid blocking event loop
    context: str = await run_in_cpu_pool_async(_pattern_context, page_text, hit_start, hit_end)

    # Truncate to hard cap (double-check since context is already bounded)
    if len(context) > MAX_EXTRACTED_TEXT_CHARS:
        context = context[:MAX_EXTRACTED_TEXT_CHARS]

    finding_id = _make_finding_id(query, url, hit_label, hit_pattern, hit_value)

    # provenance: (source_family, source, url, hit_label, hit_pattern)
    provenance: tuple[str, ...] = ("source_family:public", "duckduckgo", url, hit_label or "", hit_pattern)

    # F234: propagate discovery_score as finding confidence if available
    if discovery_score is not None:
        confidence = float(max(0.0, min(1.0, discovery_score)))
    else:
        confidence = _DEFAULT_CONFIDENCE

    finding = CanonicalFinding(
        finding_id=finding_id,
        query=query,
        source_type=_SOURCE_TYPE,
        confidence=confidence,
        ts=time.time(),
        provenance=provenance,
        payload_text=context,
    )
    return (finding,)


# -----------------------------------------------------------------------------
# Single-page fetch + extract + match + store
# Extracted to pipeline/public_fetch.py — this module re-exports for compatibility
# -----------------------------------------------------------------------------


async def _fetch_and_process_page(
    *,
    semaphore: asyncio.Semaphore,
    query: str,
    hit_url: str,
    hit_title: str,
    hit_snippet: str,
    hit_rank: int,
    fetch_timeout_s: float,
    fetch_max_bytes: int,
    store: Any | None,
    memory_manager: Any | None = None,
    session_id: str | None = None,
    discovery_score: float | None = None,
    discovery_reason: str | None = None,
    vector_store: Any | None = None,
    graph: Any | None = None,
) -> PipelinePageResult:
    """Delegate to public_fetch module (extracted from this file)."""
    from .public_fetch import _fetch_and_process_page as _impl

    return await _impl(
        semaphore=semaphore,
        query=query,
        hit_url=hit_url,
        hit_title=hit_title,
        hit_snippet=hit_snippet,
        hit_rank=hit_rank,
        fetch_timeout_s=fetch_timeout_s,
        fetch_max_bytes=fetch_max_bytes,
        store=store,
        memory_manager=memory_manager,
        session_id=session_id,
        discovery_score=discovery_score,
        discovery_reason=discovery_reason,
        vector_store=vector_store,
        graph=graph,
    )


# ---- Legacy fetch/match imports (delegated to public_fetch module) ------------------


def _patch_fetcher_and_matcher(fetch_fn: Any, match_fn: Any) -> None:
    """Legacy compatibility: delegate to public_fetch module."""
    from . import public_fetch
    public_fetch._patch_fetcher_and_matcher(fetch_fn, match_fn)


def _ensure_patched() -> None:
    """Legacy compatibility: delegate to public_fetch module."""
    from . import public_fetch
    public_fetch._ensure_patched()


# -----------------------------------------------------------------------------
# P6: OSINT Report Generation
# -----------------------------------------------------------------------------


def _make_finding_id(
    query: str, url: str, label: str, pattern: str, value: str
) -> str:
    """
    Deterministic finding ID via SHA-256 hash of pipeline inputs.
    hash() is forbidden (non-deterministic across processes).
    """
    key = f"{query}\x00{url}\x00{label}\x00{pattern}\x00{value}"
    # xxhash — non-cryptographic, 10-20× faster than sha256 for dedup keys
    # F265C: Use centralized rust backend
    try:
        from core.rust_backend import rust as _rust_backend

        if _rust_backend.is_available and _rust_backend.hash is not None:
            return _rust_backend.hash.content_hash_hex(key)
        raise ImportError("Rust hash not available")
    except Exception:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


async def _generate_and_store_report(
    query: str,
    pages: tuple,
    store: Any | None,
    hermes_engine: Any | None,
    vector_store: Any | None = None,
) -> str:
    """
    P6: Generate OSINT report from top findings and store in DuckDB.
    P13: Integrate vector search, MMR reranking, and RRF fusion for RAG context.

    Collects top 5 pages by matched_patterns count, generates report via Hermes
    (if available), and stores with source_type='report'.

    Fail-soft: returns empty string on any error. Pipeline continues regardless.

    Args:
        query: Research query
        pages: Tuple of PipelinePageResult
        store: Optional DuckDBShadowStore instance
        hermes_engine: Optional Hermes3Engine instance (if None, report generation skipped)
        vector_store: Optional VectorStore instance for semantic search

    Returns:
        Generated report text, or empty string if skipped/failed
    """
    if hermes_engine is None:
        return ""  # No Hermes, skip report generation

    # P13: Vector search for RAG context with MMR reranking
    vector_candidates: list[tuple[str, float]] = []
    if vector_store is not None:
        try:
            from hledac.universal.brain.model_manager import get_model_manager
            from hledac.universal.embedding_pipeline import embed_query_async
            from utils.ranking import rrf_fuse

            # Generate query embedding with proper lifecycle management
            model_manager = get_model_manager()
            async with model_manager.embedding_lifecycle():
                query_vec = await embed_query_async(query)

                # Query vector store for similar documents
                raw_similar = vector_store.query(query_vec, k=10, index_type="text")
                if raw_similar:
                    logger.info(f"[P13] Vector search found {len(raw_similar)} similar docs")
                    vector_candidates = raw_similar

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[P13] Vector search failed: {e}")
            vector_candidates = []

    # Collect top N pages by matched_patterns (proxy for IOC density)
    sorted_pages = sorted(
        pages,
        key=lambda p: (p.matched_patterns or 0, p.accepted_findings or 0),
        reverse=True
    )
    top_pages = sorted_pages[:_REPORT_TOP_N]

    if not top_pages:
        return ""  # No findings to report on

    # P13: Build pattern_matcher ranked list for RRF fusion
    pattern_ranked: list[tuple[str, float]] = []
    for p in top_pages:
        url = getattr(p, 'url', '') or ''
        score = (p.matched_patterns or 0) + (p.accepted_findings or 0) * 0.5
        if url:
            pattern_ranked.append((url, score))

    # P13: Fuse vector search results with pattern matcher results using RRF
    if vector_candidates and pattern_ranked:
        try:
            fused_ids = rrf_fuse([vector_candidates, pattern_ranked], k=60)
            logger.info(f"[P13] RRF fused {len(fused_ids)} results")
            # Use fused order for context building
            fused_url_order = fused_ids[:_REPORT_TOP_N]
        except Exception:
            # Fallback to pattern matcher order if RRF fails
            fused_url_order = [url for url, _ in pattern_ranked[:_REPORT_TOP_N]]
    else:
        fused_url_order = [url for url, _ in pattern_ranked[:_REPORT_TOP_N]]

    # Build context from fused/ranked pages
    context_items: list[str] = []
    url_to_page = {getattr(p, 'url', ''): p for p in pages}

    for url in fused_url_order:
        page = url_to_page.get(url)
        if page is None:
            continue
        # Format page info as context item
        ioc_count = page.matched_patterns or 0
        accepted = page.accepted_findings or 0
        title = getattr(page, 'discovery_reason', '') or getattr(page, 'quality_reason', '') or url

        context_items.append(
            f"URL: {url}\n"
            f"Title/Reason: {title}\n"
            f"IOC count: {ioc_count}, Accepted findings: {accepted}"
        )

    # If no context from fusion, fall back to top_pages
    if not context_items:
        for p in top_pages:
            ioc_count = p.matched_patterns or 0
            accepted = p.accepted_findings or 0
            url = getattr(p, 'url', '') or ''
            title = getattr(p, 'discovery_reason', '') or getattr(p, 'quality_reason', '') or url

            context_items.append(
                f"URL: {url}\n"
                f"Title/Reason: {title}\n"
                f"IOC count: {ioc_count}, Accepted findings: {accepted}"
            )

    # FÁZE P14: Build routing context and determine best model
    route_context: dict = {
        "urls": [getattr(p, 'url', '') for p in top_pages if hasattr(p, 'url')],
        "content_type": "html",  # Default content type
    }

    # Check for images in page data (vision routing)
    has_images = any(
        getattr(p, 'redirected', False) and 'image' in (getattr(p, 'redirect_target', '') or '').lower()
        for p in top_pages
    )
    if has_images:
        route_context["has_images"] = True

    # P16: Route via MoERouter.route() to get expert IDs for generator selection
    _expert_ids: list[str] = []  # noqa: F841  # P16: reserved for future MoE expert routing
    try:
        from hledac.universal.brain.moe_router import create_moe_router
        router = await create_moe_router()
        if router is not None:
            _expert_ids = await router.route(query, context_items)
            logger.info(f"[P16] MoE experts: {_expert_ids} for query: {query[:50]}")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[P16] MoE routing failed: {e}")
        _expert_ids = []

    # FÁZE P14: Route to appropriate model (legacy fallback)
    from hledac.universal.brain.moe_router import route as moe_route
    model_choice = moe_route(query, route_context)
    logger.info(f"[P14] MoE route: {model_choice} for query: {query[:50]}")

    # Generate report based on routed model
    report_text = ""
    try:
        match model_choice:
            case "vision":
                report_text = "[image description] " + "\n".join(context_items[:3])
                logger.info("[P14] Using vision encoder placeholder")
            case "modernbert":
                try:
                    from hledac.universal.brain.modernbert_engine import ModernBertEngine
                    modernbert = ModernBertEngine()
                    report_text = await modernbert.summarize(context_items)
                    logger.info("[P14] Using ModernBERT summarizer")
                except Exception as e:
                    logger.warning(f"[P14] ModernBERT failed, falling back to Hermes: {e}")
                    report_text = await hermes_engine.generate_report(query, context_items)
            case _:
                report_text = await hermes_engine.generate_report(query, context_items)

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[REPORT] Generation failed: {e}")
        return ""

    if not report_text:
        return ""  # Report generation returned empty

    # Store report as CanonicalFinding with source_type='report'
    if store is not None:
        try:
            from hledac.universal.knowledge.duckdb_store import CanonicalFinding

            report_id = _make_finding_id(
                query=query,
                url="synthetic://report",
                label="osint_report",
                pattern="synthetic",
                value=report_text[:200]  # Use first 200 chars as value for ID
            )

            report_finding = CanonicalFinding(
                finding_id=report_id,
                query=query,
                source_type=_REPORT_SOURCE_TYPE,
                confidence=0.7,  # Moderate confidence for generated content
                ts=time.time(),
                provenance=("source_family:public", "report_generation", hermes_engine.__class__.__name__),
                payload_text=report_text,
            )

            # Store using existing async API (coalescer path — fire-and-forget)
            await store.submit_findings([report_finding])
            import logging
            logging.getLogger(__name__).info(f"[REPORT] Stored report {report_id[:8]} for query: {query[:50]}")

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"[REPORT] Storage failed: {e}")
            # Fail-soft: report was generated but not stored - still return it

        # F256: Also produce HermesInferenceOutput for pivot planning
        # (stored alongside report finding for query at advisory time)
        # Guard: need store (for CanonicalFinding) + hermes_engine + non-empty report_text
        if store is not None and hermes_engine is not None and report_text:
            try:
                from hledac.universal.brain.ner_engine import extract_iocs_from_text
                from hledac.universal.runtime.hermes_pivot_contract import HermesInferenceOutput

                # F256K: Try structured IOC_JSON block first, fall back to NER extraction
                key_iocs: list[str] = []
                key_entities: list[str] = []

                ioc_json_block = re.search(r'<IOC_JSON>\s*(\{.*?\})\s*</IOC_JSON>', report_text, re.DOTALL)
                if ioc_json_block:
                    try:
                        ioc_data = _json.decode(ioc_json_block.group(1))
                        key_iocs = list(ioc_data.get("iocs", [])[:20])
                        key_entities = list(ioc_data.get("entities", [])[:20])
                    except (ValueError, KeyError) as _:
                        pass  # Fall back to NER extraction

                if not key_iocs and not key_entities:
                    # Fallback: use NER extraction
                    ioc_results = extract_iocs_from_text(report_text)
                    key_iocs = list(
                        r["value"] for r in ioc_results
                        if r.get("value") and len(r["value"]) > 3
                    )[:20]
                    key_entities = list(
                        r["value"] for r in ioc_results
                        if r.get("ioc_type") in ("org", "person", "gpe", "product")
                    )[:20]

                pivot_suggestions = key_iocs[:10]

                hermes_output = HermesInferenceOutput(
                    output_id=report_id,
                    source_finding_id=report_id,
                    inference_type="report_synthesis",
                    timestamp=time.time(),
                    primary_text=report_text,
                    confidence=0.7,
                    key_iocs=key_iocs,
                    key_entities=key_entities,
                    pivot_suggestions=pivot_suggestions,
                    bounded=False,
                    tokens_used=0,
                    model_name=hermes_engine.__class__.__name__,
                    source_hints=("public",),
                )

                # Store hermes_inference as CanonicalFinding for advisory retrieval
                hermes_finding = CanonicalFinding(
                    finding_id=hermes_output.output_id,
                    query=query,
                    source_type="hermes_inference",
                    confidence=hermes_output.confidence,
                    ts=hermes_output.timestamp,
                    provenance=("source_family:public", "hermes_inference", hermes_engine.__class__.__name__),
                    payload_text=_json.encode(hermes_output.to_dict()).decode("utf-8")[:4096],
                )
                await store.submit_findings([hermes_finding])
                import logging as _log
                _log.getLogger(__name__).info(f"[F256] Stored hermes_inference {hermes_output.output_id[:8]}")
            except Exception as _e:
                import logging as _log
                _log.getLogger(__name__).warning(f"[F256] HermesInferenceOutput failed: {_e}")
                # fail-soft: report still returned

    return report_text


# -----------------------------------------------------------------------------
# Main pipeline
# -----------------------------------------------------------------------------


def _query_looks_like_domain(query: str) -> bool:
    """
    Sprint F188B: Detect if query is a domain name suitable for CT subdomain lookup.

    Returns True for "example.com", "api.example.com", "*.example.com".
    Returns False for "apple inc", "what is DNS", "site:example.com".

    F233E: Also try token with a dot for mixed OSINT queries like
    "certificate transparency subdomains of mozilla.org" — the token
    "mozilla.org" has a dot and is the domain candidate.
    """
    q = query.strip()
    if not q or len(q) > 253:
        return False
    # F233E: also try token with a dot (handles "domain at end" queries like
    # "certificate transparency subdomains of mozilla.org" where domain is last token)
    candidates = [q]
    for token in q.split():
        if "." in token and token != q:
            candidates.append(token)
    return any(_CT_QUERY_IS_DOMAIN_RE.match(c) for c in candidates)


def _extract_base_domain(domain: str) -> str:
    """
    Sprint F188B: Extract base domain from a domain string for CT scanner input.

    "www.example.com" -> "example.com"
    "api.example.com" -> "example.com"
    "example.com"     -> "example.com"
    "*.example.com"   -> "example.com"

    Returns the input unchanged if it can't be parsed.
    """
    # Remove wildcard prefix
    if domain.startswith("*."):
        domain = domain[2:]
    parts = domain.split(".")
    if len(parts) >= 3:
        # Heuristic: last two parts are the registered domain
        return ".".join(parts[-2:])
    return domain


async def _inject_ct_subdomain_hits(
    hits: tuple,
    query: str,
) -> tuple:
    """
    Sprint F188B: Thin CT winner-slice adapter.

    If query looks like a domain, call the CT scanner to get subdomains,
    synthesize them as high-confidence discovery hits, and prepend to the
    existing hits tuple.

    Fail-soft: scanner errors or non-domain queries return hits unchanged.
    Bounded: at most _CT_SUBDOMAIN_BOUND subdomains injected.
    M1-safe: CT scanner owns its cache; shared session reuse via async_session.

    This is NOT a new discovery world — it augments existing discovery hits
    with CT-sourced subdomains within the same fetch batch.
    """
    global _CT_SCANNER_GET_SUBDOMAINS

    if not hits or not _query_looks_like_domain(query):
        return hits

    _ensure_ct_scanner_patched()
    if _CT_SCANNER_GET_SUBDOMAINS is None:
        return hits

    base_domain = _extract_base_domain(query)

    # Sprint F188B: use shared aiohttp session for connection pooling
    shared_session = None
    try:
        from hledac.universal.network.session_runtime import async_get_aiohttp_session
        shared_session = await async_get_aiohttp_session()
    except Exception:  # noqa: BLE001
        pass

    try:
        subdomains: list[str] = await _CT_SCANNER_GET_SUBDOMAINS(
            base_domain, async_session=shared_session
        )
    except Exception:
        subdomains = []

    if not subdomains:
        return hits

    subdomains = subdomains[:_CT_SUBDOMAIN_BOUND]

    # Sprint F188B: synthesize CT hits as simple structs with the same
    # attribute interface that _fetch_and_process_page expects.
    # Attribute-based access: hit.url, hit.title, hit.snippet, hit.rank, hit.score, hit.reason
    class _CTHit:
        __slots__ = ("url", "title", "snippet", "rank", "score", "reason")
        def __init__(self, url: str, rank: int):
            self.url = url
            self.title = f"[CT] {url}"
            self.snippet = f"Certificate Transparency subdomain of {base_domain}"
            self.rank = rank
            self.score = _CT_SUBDOMAIN_SCORE
            self.reason = "ct_subdomain"

    ct_hits = tuple(
        _CTHit(f"https://{subdomain}", idx) for idx, subdomain in enumerate(subdomains)
    )
    return ct_hits + hits


# F192E: CommonCrawl domain discovery injection
_CC_SCANNER_LOOKUP: Any = None


def _query_looks_like_domain_for_cc(query: str) -> bool:
    """
    F192E: Detect if query is a domain name suitable for CommonCrawl CDX lookup.

    Returns True for "example.com", "*.example.com", "site:example.com".
    Returns False for "apple inc", "what is DNS", etc.

    F233E: Also try token with a dot for mixed OSINT queries.
    """
    q = query.strip()
    if not q or len(q) > 253:
        return False
    # F233E: also try token with a dot for mixed OSINT queries
    candidates = [q]
    for token in q.split():
        if "." in token and token != q:
            candidates.append(token)
    return any(_CC_QUERY_IS_DOMAIN_RE.match(c) for c in candidates)


async def _inject_commoncrawl_hits(
    hits: tuple,
    query: str,
) -> tuple:
    """
    F192E: Thin CommonCrawl CDX injection as discovery augmentation.

    CommonCrawl CDX API is a domain index (historical URL archive), not a
    general search engine. It only activates for domain-like queries.

    This is NOT a new discovery world — it augments existing discovery hits
    with CC-sourced archived URLs within the same fetch batch.

    Fail-soft: CC errors or non-domain queries return hits unchanged.
    Bounded: at most 20 CC results injected.
    M1-safe: adapter owns its HTTP calls, shared session reuse.
    """
    global _CC_SCANNER_LOOKUP

    if not hits or not _query_looks_like_domain_for_cc(query):
        return hits

    # Lazy-patch CommonCrawl scanner
    if _CC_SCANNER_LOOKUP is None:
        try:
            from hledac.universal.tools.commoncrawl_adapter import CommonCrawlAdapter

            class _MinimalStealth:
                async def get(self, url: str) -> str:
                    from hledac.universal.network.session_runtime import async_get_aiohttp_session
                    s = await async_get_aiohttp_session()
                    async with s.get(url) as r:
                        return await r.text()

            _CC_SCANNER_LOOKUP = CommonCrawlAdapter(stealth=_MinimalStealth())
        except Exception:
            return hits

    # Extract domain from query (strip site:/domain: prefix)
    import re
    clean_domain = re.sub(r"^(site|domain):", "", query.strip(), flags=re.IGNORECASE).strip()
    if not clean_domain:
        return hits

    try:
        cc_results: list = await _CC_SCANNER_LOOKUP.search(clean_domain, max_results=20)
    except Exception:
        return hits

    if not cc_results:
        return hits

    # Synthesize CC hits as simple attribute-based objects (same interface as CT hits)
    class _CCHit:
        __slots__ = ("url", "title", "snippet", "rank", "score", "reason")
        def __init__(self, url: str, title: str, snippet: str, rank: int):
            self.url = url
            self.title = title
            self.snippet = snippet
            self.rank = rank
            self.score = 0.75  # F192E: CC hits get strong baseline score
            self.reason = "commoncrawl_archive"

    cc_hits = tuple(
        _CCHit(
            url=r.get("url", ""),
            title=r.get("title", ""),
            snippet=r.get("snippet", ""),
            rank=idx,
        )
        for idx, r in enumerate(cc_results[:20])
    )
    # Prepend CC hits to give them priority in the fetch batch
    return cc_hits + hits


# Sprint F193A: Onion discovery + scraping block
_ONION_HIT_MAX = 5
_ONION_CIRCUIT_FAIL_LIMIT = 3
_onion_circuit_state = {"failures": 0, "opened_at": 0.0}
_onion_circuit_lock = asyncio.Lock()


def _onion_circuit_is_open() -> bool:
    """Check if onion circuit breaker is open."""
    if _onion_circuit_state["failures"] < _ONION_CIRCUIT_FAIL_LIMIT:
        return False
    import time
    if time.time() - _onion_circuit_state["opened_at"] >= 60.0:
        _onion_circuit_state["failures"] = 0
        _onion_circuit_state["opened_at"] = 0.0
        return False
    return True


def _onion_circuit_record_failure() -> None:
    """Record a failure in the onion circuit breaker."""
    import time
    _onion_circuit_state["failures"] += 1
    if _onion_circuit_state["failures"] >= _ONION_CIRCUIT_FAIL_LIMIT:
        _onion_circuit_state["opened_at"] = time.time()
        logger.warning("[F193A] Onion circuit breaker OPEN — pausing 60s")


async def _inject_onion_hits(
    hits: tuple,
    query: str,
    store: DuckDBShadowStore,
) -> int:
    """
    Sprint F193A: Onion discovery + scraping via Tor.

    Discovers .onion URLs via Ahmia search and scrapes them using
    Tor-capable async_fetch_public_text(). Converts results to CanonicalFinding
    and stores via duckdb_store.

    Bounded: max 5 onion hits, circuit breaker after 3 failures, fail-soft.
    Returns number of onion findings stored.
    """
    from hledac.universal.fetching.public_fetcher import async_fetch_public_text
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    from hledac.universal.utils.async_helpers import safe_gather

    # Quick check: skip if circuit is open
    if _onion_circuit_is_open():
        return 0

    # Detect .onion URLs in existing hits (already discovered)
    onion_urls: list[str] = []
    for hit in hits:
        url = getattr(hit, "url", None) or (str(hit[2]) if len(hit) > 2 else None)
        if url and ".onion" in url.lower():
            onion_urls.append(url if url.startswith("http") else f"http://{url}")

    if not onion_urls:
        return 0

    onion_urls = onion_urls[:_ONION_HIT_MAX]

    findings: list[CanonicalFinding] = []
    ts_now = time.time()
    failure_count = 0

    # F320: Parallel fetch — replaced sequential await loop with safe_gather.
    # Each coroutine fetches one .onion URL concurrently. Tor is already
    # serialized by its own circuit semaphore, so this parallelizes across
    # multiple .onion targets (typically 2-5) rather than within Tor itself.
    async def _fetch_one_onion(onion_url: str) -> CanonicalFinding | None:
        try:
            result = await async_fetch_public_text(
                onion_url,
                timeout_s=30.0,
                max_bytes=200_000,
            )
            if result.error or result.text is None:
                return None

            content = result.text
            pf_id = hashlib.sha256(
                f"{query}\x00{onion_url}\x00onion_discovery".encode()
            ).hexdigest()[:16]

            return CanonicalFinding(
                finding_id=pf_id,
                query=query,
                source_type="onion_discovery",
                confidence=0.55,
                ts=ts_now,
                provenance=("onion_discovery", onion_url),
                payload_text=content[:500] if content else None,
            )
        except Exception as e:
            logger.debug(f"[F193A] Onion fetch {onion_url}: {e}")
            return None

    result_obj = await safe_gather(
        *[_fetch_one_onion(url) for url in onion_urls],
        label="onion_hits",
    )
    for finding in result_obj.ok:
        if finding is not None:
            findings.append(finding)

    successful_urls = {f.provenance[1] for f in findings}
    failure_count = sum(1 for url in onion_urls if url not in successful_urls)
    if failure_count >= _ONION_CIRCUIT_FAIL_LIMIT:
        _onion_circuit_record_failure()

    if findings and store is not None:
        try:
            await store.submit_findings(findings)
            logger.info(f"[F193A] Stored {len(findings)} onion findings")
        except Exception as e:
            logger.debug(f"[F193A] Onion findings persist failed: {e}")

    return len(findings)


async def async_run_live_public_pipeline(
    query: str,
    store: DuckDBShadowStore | None = None,
    max_results: int = 10,
    fetch_timeout_s: float = 35.0,
    fetch_max_bytes: int = 2_000_000,
    fetch_concurrency: int = 8,  # F290: 5→8, M1 8GB RAM budget allows 8 concurrent HTTP
    hermes_engine: Any | None = None,
    graph: Any | None = None,
    memory_manager: Any | None = None,
    session_id: str | None = None,
    vector_store: Any | None = None,
    run_loop: bool = False,  # P16: If True, run ResearchLoop after pipeline
    rl_steps: int = 0,  # P17: Number of RL steps (0 = use time limit)
    enqueue_hypothesis_pivot: Any | None = None,  # Sprint F193B: bounded feedback seam
    # Sprint F217C: Deterministic bootstrap — if True, prepend bootstrap URLs before discovery
    public_bootstrap_enabled: bool = False,
    # Sprint F223C: Bounded seed_context bootstrap for nonfeed_diagnostic profile
    seed_context: Any | None = None,
    # DI F226: explicit dependency injection for testable seams
    fetch_fn: Any | None = None,  # async_fetch_public_text replacement
    match_fn: Any | None = None,  # match_text replacement
    discovery_fn: Any | None = None,  # async_search_public_web replacement
    ct_subdomains_fn: Any | None = None,  # CT scanner get_subdomains replacement
    clear_query_cache_fn: Any | None = None,  # _clear_query_cache replacement
    # F271E: Export directory for P18 in-pipeline markdown/graph export.
    # When None, the singleton ExportManager falls back to ~/hledac_outputs/.
    # Threaded from __main__.py dispatcher so ``--export-dir`` is honoured
    # by the in-pipeline Obsidian export as well as the post-sprint export.
    export_dir: str | None = None,
    _sprint_id: str = "",  # noqa: F841  # F268: reserved for graph accumulation context
) -> PipelineRunResult:
    """
    Sprint 8AE: Live public OSINT pipeline.

    Orchestration-only: wires existing 8AC/8AD/8X/8W/8S components.
    P6: Optional Hermes3Engine for OSINT report generation.
    P11: Optional MemoryManager for persistent RAG history.

    Parameters
    ----------
    query:
        Research query string (passed to CanonicalFinding.query).
    store:
        Optional DuckDBShadowStore instance. If None, storage is a no-op
        and only counting happens.
    max_results:
        Maximum discovery hits to process (default 10).
    fetch_timeout_s:
        Per-fetch operation timeout in seconds (applied per-page via 8AD API).
    fetch_max_bytes:
        Maximum bytes to fetch per page.
    fetch_concurrency:
        Maximum concurrent fetches in the batch.
    memory_manager:
        Optional MemoryManager instance for persistent RAG history.
    session_id:
        Optional session ID for memory manager. If None, uses query hash.
    enqueue_hypothesis_pivot:
        Optional callback for bounded hypothesis pivot feedback (Sprint F193B).
    public_bootstrap_enabled:
        If True, prepend bootstrap URLs before discovery (Sprint F217C).
    seed_context:
        Optional seed context for nonfeed_diagnostic profile bootstrap (Sprint F223C).
    fetch_fn:
        DI F226: explicit async_fetch_public_text replacement. If None,
        falls back to _ensure_patched() → async_fetch_public_text from 8AD.
    match_fn:
        DI F226: explicit match_text replacement. If None, falls back to
        _ensure_patched() → match_text from 8X.
    discovery_fn:
        DI F226: explicit async_search_public_web replacement. If None,
        falls back to _ensure_discovery_patched() (providerless cascade or DDG).
    ct_subdomains_fn:
        DI F226: explicit CT scanner get_subdomains(domain, async_session)
        replacement. If None, falls back to _ensure_ct_scanner_patched().
    clear_query_cache_fn:
        DI F226: explicit _clear_query_cache replacement. If None,
        imports and calls duckduckgo_adapter._clear_query_cache.

    Returns
    -------
    PipelineRunResult with typed counts and per-page error breakdown.
    """
    # Sprint F206P: Reset temporal signal layer at run start
    from hledac.universal.layers import reset_temporal_signal_layer
    reset_temporal_signal_layer()

    # F207I-A: Clear per-run DDG query cache at pipeline run start
    # DI F226: explicit dependency injection — clear_query_cache_fn
    _resolved_clear_cache: Any = clear_query_cache_fn
    if _resolved_clear_cache is None:
        from hledac.universal.discovery.duckduckgo_adapter import _clear_query_cache
        _resolved_clear_cache = _clear_query_cache
    _resolved_clear_cache()

    # Sprint F206Q: Restore from persistent snapshot if store is enabled
    persistence_enabled = False
    persistence_restored = False
    try:
        from hledac.universal.layers import (
            is_temporal_store_enabled,
            load_temporal_signal_snapshot,
        )
        persistence_enabled = is_temporal_store_enabled()
        if persistence_enabled:
            persistence_restored = load_temporal_signal_snapshot()
    except Exception:  # noqa: BLE001
        pass

    # DI F226: explicit dependency injection — resolve all seams before use
    # fetch_fn / match_fn override globals; otherwise _ensure_patched() sets them
    if fetch_fn is not None:
        from . import public_fetch as _pf
        _pf._ASYNC_FETCH_PUBLIC_TEXT = fetch_fn
    if match_fn is not None:
        from . import public_fetch as _pf
        _pf._SYNC_MATCH_TEXT = match_fn
    # discovery_fn / ct_subdomains_fn override globals
    if discovery_fn is not None:
        global _ASYNC_DISCOVERY_SEARCH
        _ASYNC_DISCOVERY_SEARCH = discovery_fn
    if ct_subdomains_fn is not None:
        global _CT_SCANNER_GET_SUBDOMAINS
        _CT_SCANNER_GET_SUBDOMAINS = ct_subdomains_fn

    # Ensure hot-path imports are resolved
    _ensure_patched()

    # P11: Initialize session ID for memory manager
    if session_id is None:
        import hashlib
        session_id = hashlib.sha256(query.encode()).hexdigest()[:16]

    # P11: Load relevant RAG history from memory manager (if available)
    # NOTE: rag_context is populated but not yet wired to hermes_engine.generate_report().
    # Reserved for future RAG context injection in synthesis phase.
    _rag_context: list[dict] = []  # noqa: F841
    if memory_manager is not None:
        try:
            history = await memory_manager.get_session_history(session_id, limit=50)
            # Extract payload_text from past findings for RAG context
            for entry in history:
                value = entry.get("value", {})
                if isinstance(value, dict):
                    payload = value.get("payload_text", "")
                    if payload:
                        _rag_context.append({
                            "query": value.get("query", ""),
                            "payload": payload[:500],  # Truncate for context
                            "timestamp": value.get("timestamp", 0),
                        })
        except Exception:
            _rag_context = []  # Fail-soft: memory errors don't fail pipeline

    # ---- Engines -----------------------------------------------------------
    # Sprint F214: Refactored into focused engine classes for maintainability.
    # Each engine is a dataclass with async run() method that encapsulates a
    # logical phase of the pipeline. Backward compatible — same inputs/outputs.

    @dataclass(slots=True)
    class _DiscoveryEngine:
        """
        Engine 1: Handles all discovery-related logic.

        Input state: query, store, max_results, public_bootstrap_enabled, seed_context
        Output state: enriched hits tuple + all discovery telemetry accumulators
        """
        query: str
        store: Any
        max_results: int
        public_bootstrap_enabled: bool
        seed_context: Any | None  # Sprint F223C: NonfeedSeedContext for bounded bootstrap

        async def run(
            self,
            uma_state: str,
        ) -> tuple[
            tuple,  # hits
            str | None,  # discovery_result
            str | None,  # discovery_error
            str | None,  # discovery_error_type
            float | None,  # discovery_elapsed_s
            bool,  # discovery_attempted
            dict,  # discovery_telemetry
            int,  # academic_findings_count
            int,  # ct_injected
            int,  # cc_injected
            int,  # onion_findings_count
            int,  # pastebin_findings_count
            int,  # github_secrets_count
        ]:
            # ---- Discovery (8AC) -----------------------------------------------------
            discovery_error: str | None = None
            discovery_error_type: str | None = None
            discovery_elapsed_s: float | None = None
            discovery_attempted: bool = False
            hits: tuple = ()
            # Sprint F213B: stage failure accounting
            public_stage_failure: str | None = None
            public_stage_failure_reason: str | None = None
            public_discovery_deduped_count: int = 0
            _discovery_start: float | None = None
            # F207I-A: discovery telemetry counters (initialized before try block)
            public_discovery_cache_hit: int = 0
            public_discovery_query_count: int = 0

            # Sprint F217C: Deterministic bootstrap telemetry (initialized before try block)
            _pub_bootstrap_candidates_count: int = 0
            _pub_bootstrap_fetch_attempted: int = 0
            _pub_bootstrap_fetch_success: int = 0
            _pub_bootstrap_accepted_findings: int = 0
            _pub_bootstrap_errors: int = 0
            _pub_bootstrap_order: str = "disabled"
            _pub_bootstrap_prevented_discovery_timeout: bool = False
            _pub_bootstrap_first_fetch_attempted: bool = False

            # 3.3: Keyword-based search engine bootstrap fallback telemetry
            _pub_keyword_bootstrap_candidates_count: int = 0
            _pub_keyword_bootstrap_fetch_attempted: int = 0
            _pub_keyword_bootstrap_fetch_success: int = 0
            _pub_keyword_bootstrap_accepted_findings: int = 0
            _pub_keyword_bootstrap_errors: int = 0
            _pub_keyword_bootstrap_order: str = "disabled"

            # F226B: PUBLIC acceptance uplift telemetry (initialized before try block)
            _pub_build_success_count: int = 0
            _pub_build_failure_count: int = 0
            _pub_duplicate_count: int = 0

            # F232: Provider surface telemetry — local accumulators (reset each run)
            _pub_provider_selected: list[str] = []
            _pub_provider_skipped: list[dict] = []
            _pub_provider_stub: list[str] = []
            _pub_provider_errors: list[dict] = []
            _pub_query_variants: list[str] = []
            _pub_provider_timeout_count: list[int] = [0]
            _pub_provider_import_error_count: list[int] = [0]
            _pub_discovery_empty_reason: list[str] = []

            # F231A: PUBLIC Candidate Ledger — stage counters
            _public_candidates_discovered: int = 0
            _public_candidates_fetch_attempted: int = 0
            _public_candidates_fetch_success: int = 0
            _public_candidates_parse_success: int = 0
            _public_candidates_pattern_matched: int = 0
            _public_candidates_built: int = 0
            _public_candidates_store_attempted: int = 0
            _public_candidates_stored: int = 0
            _public_candidates_rejected: int = 0

            # Sprint F217C: Deterministic bootstrap — generate before discovery attempt
            bootstrap_hits: list[DiscoveryHit] = []
            rescue_hits: list[DiscoveryHit] = []
            _pub_rescue_candidates_count: int = 0
            _pub_rescue_fetch_attempted: int = 0
            _pub_rescue_fetch_success: int = 0
            _pub_rescue_accepted_findings: int = 0
            _pub_rescue_errors: int = 0
            _pub_rescue_order: str = "disabled"
            _keyword_seed_fallback_triggered: bool = False

            # F1-3: keyword_seed_fallback — runs INDEPENDENTLY of bootstrap_enabled
            # so non-domain queries (ransomware, leak, breach, APT, malware, darkweb)
            # always get candidates even when bootstrap is disabled
            try:
                rescue_hits = generate_rescue_urls(self.query, max_urls=5)
                _pub_rescue_candidates_count = len(rescue_hits)
                if rescue_hits:
                    _pub_rescue_order = "keyword_seed_fallback"
                    _keyword_seed_fallback_triggered = True
                    bootstrap_hits = rescue_hits
                    rescue_hits = []
            except Exception:
                _pub_rescue_candidates_count = 0

            if self.public_bootstrap_enabled:
                try:
                    bootstrap_urls = generate_bootstrap_urls(self.query, max_urls=_MAX_BOOTSTRAP_URLS)
                    _pub_bootstrap_candidates_count = len(bootstrap_urls)
                    for idx, url in enumerate(bootstrap_urls):
                        bootstrap_hits.append(DiscoveryHit(
                            query=self.query,
                            title=f"Bootstrap {idx+1}",
                            url=url,
                            snippet=f"Deterministic bootstrap URL: {url}",
                            score=0.85,
                            reason="deterministic_bootstrap",
                            rank=-1,
                            source="bootstrap",
                            retrieved_ts=0.0,
                        ))
                except Exception:
                    _pub_bootstrap_candidates_count = 0

                # Sprint F220C: Rescue for non-domain threat queries
                # When bootstrap generated zero candidates (non-domain query),
                # generate rescue hits from static CTI/news search URLs.
                if _pub_bootstrap_candidates_count == 0 and self.public_bootstrap_enabled:
                    try:
                        rescue_hits = generate_rescue_urls(self.query, max_urls=8)
                        _pub_rescue_candidates_count = len(rescue_hits)
                        if rescue_hits:
                            _pub_rescue_order = "rescue_fallback"
                            # F251B: Prepend rescue hits immediately so discovery stage has candidates
                            bootstrap_hits = rescue_hits
                            rescue_hits = []
                    except Exception:
                        _pub_rescue_candidates_count = 0

                # Sprint F223C: Seed context bootstrap fallback
                # When query-based bootstrap + rescue both returned zero AND seed_context is available,
                # use bounded static URLs from seed_context.domains/urls.
                # Enabled only in nonfeed_diagnostic profile with seed_context (propagated from scheduler).
                if _pub_bootstrap_candidates_count == 0 and _pub_rescue_candidates_count == 0 and self.seed_context is not None:  # noqa: E501
                    try:
                        seed_bootstrap_urls = generate_seed_context_bootstrap_urls(
                            self.seed_context, max_candidates=_MAX_SEED_CONTEXT_BOOTSTRAP
                        )
                        _pub_bootstrap_candidates_count = len(seed_bootstrap_urls)
                        for idx, url in enumerate(seed_bootstrap_urls):
                            bootstrap_hits.append(DiscoveryHit(
                                query=self.query,
                                title=f"SeedBootstrap {idx+1}",
                                url=url,
                                snippet=f"Seed context bootstrap URL: {url}",
                                score=0.80,
                                reason="seed_context_bootstrap",
                                rank=-1,
                                source="seed_bootstrap",
                                retrieved_ts=0.0,
                            ))
                    except Exception:
                        _pub_bootstrap_candidates_count = 0

            try:
                _discovery_start = time.monotonic()
                discovery_attempted = True
                # Sprint F271B: bound the discovery coroutine with asyncio.wait_for so
                # any internal sub-coroutines spawned by _ASYNC_DISCOVERY_SEARCH
                # (e.g. _run_wayback_cdx / _run_historical_frontier / _run_ddg
                #  inside the cascade provider) are cancelled at timeout. Without
                # this guard, slow discovery paths produce
                # `RuntimeWarning: coroutine ... was never awaited` when the
                # outer `await` returns and the coroutine reference is GC'd
                # mid-flight. Timeout 35.0 matches the existing
                # `classify_discovery_error(..., timeout_s=35.0)` contract.
                discovery_result = await asyncio.wait_for(
                    _ASYNC_DISCOVERY_SEARCH(self.query, self.max_results),
                    timeout=35.0,
                )
                discovery_elapsed_s = time.monotonic() - _discovery_start

                cache_hit = getattr(discovery_result, "cache_hit", False) if hasattr(discovery_result, "cache_hit") else False  # noqa: E501
                public_discovery_cache_hit += int(cache_hit)
                public_discovery_query_count += 1

                _extract_provider_surface(discovery_result, _pub_provider_selected, _pub_provider_skipped,
                                          _pub_provider_stub, _pub_provider_errors,
                                          _pub_provider_timeout_count, _pub_provider_import_error_count,
                                          _pub_discovery_empty_reason)

                if hasattr(discovery_result, "hits"):
                    hits = discovery_result.hits
                elif isinstance(discovery_result, dict):
                    hits = discovery_result.get("hits", ())

                if bootstrap_hits:
                    hits = tuple(bootstrap_hits) + tuple(hits)
                    _pub_bootstrap_fetch_attempted = len(bootstrap_hits)
                    _pub_bootstrap_order = "before_discovery"
                    _pub_bootstrap_first_fetch_attempted = True
                    _disc_hits = discovery_result.hits if hasattr(discovery_result, "hits") else ()
                    if not _disc_hits:
                        _pub_bootstrap_prevented_discovery_timeout = True

                # Sprint F220C: Append rescue hits if no bootstrap candidates
                if rescue_hits:
                    hits = tuple(rescue_hits) + tuple(hits)
                    _pub_rescue_fetch_attempted = len(rescue_hits)

                # F251B: Track bootstrap order — rescue_fallback if rescue candidates used
                if bootstrap_hits:
                    if _pub_rescue_order == "rescue_fallback":
                        _pub_bootstrap_order = "rescue_fallback"
                    else:
                        _pub_bootstrap_order = "before_discovery"
                    _pub_bootstrap_fetch_attempted = len(bootstrap_hits)
                    _pub_bootstrap_first_fetch_attempted = True
                    _disc_hits = discovery_result.hits if hasattr(discovery_result, "hits") else ()
                    if len(_disc_hits) == 0:
                        _pub_bootstrap_prevented_discovery_timeout = True

                err_val = discovery_result.get("error") if isinstance(discovery_result, dict) else getattr(discovery_result, "error", None)  # noqa: E501
                if err_val:
                    discovery_error = str(err_val)

                discovery_error_type = classify_discovery_error(
                    discovery_error,
                    elapsed_s=discovery_elapsed_s,
                    timeout_s=35.0,
                    hits_count=len(hits),
                )
            except asyncio.CancelledError:
                discovery_elapsed_s = time.monotonic() - _discovery_start if _discovery_start else None
                discovery_error_type = classify_discovery_error(
                    asyncio.CancelledError("cancelled"),
                    elapsed_s=discovery_elapsed_s,
                    hits_count=0,
                )
                raise  # [I6]
            except Exception as exc:
                discovery_elapsed_s = time.monotonic() - _discovery_start if _discovery_start else None
                discovery_error = f"discovery_exception:{type(exc).__name__}:{exc}"
                discovery_error_type = classify_discovery_error(
                    discovery_error,
                    elapsed_s=discovery_elapsed_s,
                    hits_count=0,
                )
                hits = ()

            # Sprint F229A: Check for hits AFTER bootstrap prepend
            # 3.3: Keyword-based search engine bootstrap fallback
            if not hits:
                try:
                    keyword_hits = await generate_keyword_bootstrap_urls(
                        self.query,
                        max_urls=_MAX_KEYWORD_BOOTSTRAP_URLS,
                    )
                    _pub_keyword_bootstrap_candidates_count = len(keyword_hits)
                    if keyword_hits:
                        hits = tuple(keyword_hits)
                        _pub_keyword_bootstrap_order = "keyword_bootstrap"
                        _pub_keyword_bootstrap_fetch_attempted = len(keyword_hits)
                        _pub_keyword_bootstrap_fetch_success = len(keyword_hits)
                except Exception:
                    _pub_keyword_bootstrap_errors = 1
                    _pub_keyword_bootstrap_candidates_count = 0

            # Final check after keyword bootstrap fallback
            if not hits:
                discovery_telemetry = {
                    'discovery_result': None,
                    'public_stage_failure': 'discovery_empty',
                    'public_stage_failure_reason': discovery_error if discovery_error else 'no URLs returned from discovery',  # noqa: E501
                    'public_discovery_raw_count': 0,
                    'public_discovery_deduped_count': 0,
                    'public_discovery_attempted': discovery_attempted,
                    'public_discovery_cache_hit': public_discovery_cache_hit,
                    'public_discovery_query_count': public_discovery_query_count,
                    'public_bootstrap_order': _pub_bootstrap_order if _pub_bootstrap_order else 'disabled',
                    'public_bootstrap_prevented_discovery_timeout': _pub_bootstrap_prevented_discovery_timeout,
                    'public_bootstrap_first_fetch_attempted': _pub_bootstrap_first_fetch_attempted,
                    'public_bootstrap_candidates_count': _pub_bootstrap_candidates_count,
                    'public_bootstrap_fetch_attempted': _pub_bootstrap_fetch_attempted,
                    # Sprint F220C: Rescue telemetry
                    'public_rescue_candidates_count': _pub_rescue_candidates_count,
                    'public_rescue_fetch_attempted': _pub_rescue_fetch_attempted,
                    'public_rescue_order': _pub_rescue_order,
                    # F1-3: keyword_seed_fallback telemetry
                    'keyword_seed_fallback_triggered': _keyword_seed_fallback_triggered,
                    # 3.3: Keyword-based search engine bootstrap telemetry
                    'public_keyword_bootstrap_candidates_count': _pub_keyword_bootstrap_candidates_count,
                    'public_keyword_bootstrap_fetch_attempted': _pub_keyword_bootstrap_fetch_attempted,
                    'public_keyword_bootstrap_fetch_success': _pub_keyword_bootstrap_fetch_success,
                    'public_keyword_bootstrap_order': _pub_keyword_bootstrap_order,
                    'public_keyword_bootstrap_errors': _pub_keyword_bootstrap_errors,
                    'public_build_success_count': 0,
                    'public_build_failure_count': 0,
                    'public_duplicate_count': 0,
                    'public_provider_selected': list(_pub_provider_selected),
                    'public_provider_skipped': list(_pub_provider_skipped),
                    'public_provider_stub': list(_pub_provider_stub),
                    'public_provider_errors': list(_pub_provider_errors),
                    'public_query_variants': list(_pub_query_variants),
                    'public_provider_timeout_count': _pub_provider_timeout_count[0],
                    'public_provider_import_error_count': _pub_provider_import_error_count[0],
                    'public_discovery_empty_reason': _pub_discovery_empty_reason[0] if _pub_discovery_empty_reason else '',  # noqa: E501
                    'discovery_error_type': discovery_error_type or '',
                    'discovery_elapsed_s': round(discovery_elapsed_s, 3) if discovery_elapsed_s else None,
                    'public_candidates_discovered': 0,
                    'public_candidates_fetch_attempted': 0,
                    'public_candidates_fetch_success': 0,
                    'public_candidates_parse_success': 0,
                    'public_candidates_pattern_matched': 0,
                    'public_candidates_built': 0,
                    'public_candidates_store_attempted': 0,
                    'public_candidates_stored': 0,
                    'public_candidates_rejected': 0,
                }
                return (
                    (), None, discovery_error, discovery_error_type, discovery_elapsed_s, discovery_attempted,
                    discovery_telemetry, 0, 0, 0, 0, 0, 0
                )

            # F259: Academic research lane via discovery/academic adapters
            academic_findings_count = 0
            if self.store is not None:
                try:
                    # Check env gate and academic keywords
                    academic_enabled = os.environ.get("HLEDAC_ENABLE_ACADEMIC", "1").strip().lower() in ("1", "true", "yes", "on")  # noqa: E501
                    query_lower = self.query.lower()
                    academic_keywords = ["paper", "research", "academic", "scholar", "study", "journal", "citation", "doi", "arxiv", "publication", "conference", "thesis"]  # noqa: E501
                    has_academic_keywords = any(kw in query_lower for kw in academic_keywords)

                    # Also check for --deep-research flag via query or env
                    deep_research = os.environ.get("HLEDAC_DEEP_RESEARCH", "0").strip().lower() in ("1", "true", "yes", "on")  # noqa: E501

                    if academic_enabled or has_academic_keywords or deep_research:
                        from hledac.universal.discovery.academic import ACADEMIC_ENABLED, search_all_academic
                        if ACADEMIC_ENABLED:
                            from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
                            academic_semaphore = get_semaphore_for_testing(ConcurrencyCategory.ACADEMIC_SEARCH)
                            async def limited_academic_search():
                                async with academic_semaphore:
                                    return await search_all_academic(self.query, max_results_per_source=10)
                            academic_results = await limited_academic_search()

                            # Collect all findings from new adapters
                            all_findings = []
                            for _source, findings in academic_results.items():
                                all_findings.extend(findings)

                            if all_findings:
                                # Ingest via coalescer (fire-and-forget)
                                await self.store.submit_findings(all_findings)
                                academic_findings_count = len(all_findings)
                                logger.info(f"[F259] Academic lane: {academic_findings_count} findings from {len(academic_results)} sources")  # noqa: E501
                except Exception as e:
                    logger.warning(f"[F259] Academic research lane failed: {e}")

            # ISSUE #32 FIX: Parallelize independent discovery sources.
            # Phase 1 (parallel): CT + CC — both return augmented hits tuples
            # Phase 2 (parallel): Onion + Pastebin/GitHub — both run on CT/CC-augmented hits
            #
            # M1 8GB: bounded concurrency=4 keeps RAM bounded.
            # Fail-soft: each source has its own try/except wrapper, one failure doesn't block others.
            _original_hit_count = len(hits)

            async def _ct_wrapper() -> tuple:
                try:
                    return await _inject_ct_subdomain_hits(hits, self.query)
                except Exception:
                    return hits

            async def _cc_wrapper() -> tuple:
                try:
                    return await _inject_commoncrawl_hits(hits, self.query)
                except Exception:
                    return hits

            async def _pastebin_github_wrapper() -> tuple[int, int]:
                if self.store is None:
                    return 0, 0
                import re as _re

                from hledac.universal.knowledge.duckdb_store import CanonicalFinding
                _DOMAIN_ORG_RE = _re.compile(  # noqa: N806
                    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}"
                )
                try:
                    _match = _DOMAIN_ORG_RE.search(self.query)
                    if not _match:
                        return 0, 0
                    _target = _match.group()
                    logger.info(f"[P20] PastebinMonitor targeting: {_target}")
                    from hledac.universal.intelligence.pastebin_monitor import run as _pastebin_run
                    _paste_findings = await _pastebin_run(_target)
                    _pastebin_count = 0
                    if _paste_findings:
                        _p20_findings = []
                        for _pf in _paste_findings:
                            _pf_id = hashlib.sha256(
                                f"{self.query}\x00{_pf.uri}\x00pastebin".encode()
                            ).hexdigest()[:16]
                            _p20_findings.append(CanonicalFinding(
                                finding_id=_pf_id,
                                query=self.query,
                                source_type="pastebin_monitor",
                                confidence=0.6,
                                ts=time.time(),
                                provenance=("pastebin", _pf.source, _target),
                                payload_text=(
                                    f"uri={_pf.uri}\n"
                                    f"emails={_pf.emails}\n"
                                    f"ips={_pf.ip_addresses}\n"
                                    f"masked_secrets={_pf.masked_secrets()}\n"
                                    f"snippet={_pf.context_snippet[:300]}"
                                ),
                            ))
                        await self.store.submit_findings(_p20_findings)
                        _pastebin_count = len(_p20_findings)

                    _org = _match.group().rsplit(".", 1)[0]
                    from hledac.universal.intelligence.github_secret_scanner import search_org_secrets
                    _gh_count = 0
                    try:
                        _gh_results = await search_org_secrets(_org)
                    except Exception:
                        _gh_results = []
                    if _gh_results:
                        _gh_findings = []
                        for _gf in _gh_results:
                            _gf_id = hashlib.sha256(
                                f"{self.query}\x00{_gf.file_path}\x00{_gf.pattern}\x00github".encode()
                            ).hexdigest()[:16]
                            _gh_findings.append(CanonicalFinding(
                                finding_id=_gf_id,
                                query=self.query,
                                source_type="github_secret_scanner",
                                confidence=0.55,
                                ts=time.time(),
                                provenance=("github", _gf.pattern, _org),
                                payload_text=(
                                    f"pattern={_gf.pattern}\n"
                                    f"file={_gf.file_path}\n"
                                    f"line={_gf.line}\n"
                                    f"context={_gf.context[:300]}"
                                ),
                            ))
                        await self.store.submit_findings(_gh_findings)
                        _gh_count = len(_gh_findings)
                    return _pastebin_count, _gh_count
                except Exception as e:
                    logging.getLogger("hledac.universal.pipeline.live_public_pipeline").warning(
                        "[P20] Pastebin/GitHub scan failed: %s", e
                    )
                    return 0, 0

            async def _onion_wrapper(_augmented_hits: tuple) -> int:
                if self.store is None:
                    return 0
                try:
                    return await _inject_onion_hits(_augmented_hits, self.query, self.store)
                except Exception as e:
                    logger.debug(f"[F193A] Onion discovery failed: {e}")
                    return 0

            # Phase 1: CT + CC in parallel
            ct_augmented, cc_augmented = await bounded_gather(
                [_ct_wrapper(), _cc_wrapper()],
                concurrency=2,
                ctx="live_public_pipeline:issue32_phase1",
            )
            ct_injected = len(ct_augmented) - _original_hit_count
            cc_injected = len(cc_augmented) - len(ct_augmented)

            # Merge: CC builds on CT result
            hits = cc_augmented

            # Phase 2: Onion + Pastebin/GitHub in parallel
            # bounded_gather returns tuple[list[T], list[BaseException]]
            p20_onion_results, _p20_onion_errors = await bounded_gather(
                [_pastebin_github_wrapper(), _onion_wrapper(hits)],
                concurrency=2,
                ctx="live_public_pipeline:issue32_phase2",
            )
            pastebin_findings_count, github_secrets_count = p20_onion_results[0]
            onion_findings_count = p20_onion_results[1]

            discovery_telemetry = {
                'discovery_result': discovery_result,
                'public_stage_failure': public_stage_failure,
                'public_stage_failure_reason': public_stage_failure_reason,
                'public_discovery_raw_count': len(hits),
                'public_discovery_deduped_count': public_discovery_deduped_count,
                'public_discovery_attempted': discovery_attempted,
                'public_discovery_cache_hit': public_discovery_cache_hit,
                'public_discovery_query_count': public_discovery_query_count,
                'public_bootstrap_order': _pub_bootstrap_order,
                'public_bootstrap_prevented_discovery_timeout': _pub_bootstrap_prevented_discovery_timeout,
                'public_bootstrap_first_fetch_attempted': _pub_bootstrap_first_fetch_attempted,
                'public_bootstrap_candidates_count': _pub_bootstrap_candidates_count,
                'public_bootstrap_fetch_attempted': _pub_bootstrap_fetch_attempted,
                'public_bootstrap_fetch_success': _pub_bootstrap_fetch_success,
                'public_bootstrap_accepted_findings': _pub_bootstrap_accepted_findings,
                'public_bootstrap_errors': _pub_bootstrap_errors,
                # Sprint F220C: Rescue telemetry
                'public_rescue_candidates_count': _pub_rescue_candidates_count,
                'public_rescue_fetch_attempted': _pub_rescue_fetch_attempted,
                'public_rescue_fetch_success': _pub_rescue_fetch_success,
                'public_rescue_accepted_findings': _pub_rescue_accepted_findings,
                'public_rescue_errors': _pub_rescue_errors,
                'public_rescue_order': _pub_rescue_order,
                # F1-3: keyword_seed_fallback telemetry
                'keyword_seed_fallback_triggered': _keyword_seed_fallback_triggered,
                'public_build_success_count': _pub_build_success_count,
                'public_build_failure_count': _pub_build_failure_count,
                'public_duplicate_count': _pub_duplicate_count,
                'public_provider_selected': list(_pub_provider_selected),
                'public_provider_skipped': list(_pub_provider_skipped),
                'public_provider_stub': list(_pub_provider_stub),
                'public_provider_errors': list(_pub_provider_errors),
                'public_query_variants': list(_pub_query_variants),
                'public_provider_timeout_count': _pub_provider_timeout_count[0],
                'public_provider_import_error_count': _pub_provider_import_error_count[0],
                'public_discovery_empty_reason': _pub_discovery_empty_reason[0] if _pub_discovery_empty_reason else '',
                'public_candidates_discovered': _public_candidates_discovered,
                'public_candidates_fetch_attempted': _public_candidates_fetch_attempted,
                'public_candidates_fetch_success': _public_candidates_fetch_success,
                'public_candidates_parse_success': _public_candidates_parse_success,
                'public_candidates_pattern_matched': _public_candidates_pattern_matched,
                'public_candidates_built': _public_candidates_built,
                'public_candidates_store_attempted': _public_candidates_store_attempted,
                'public_candidates_stored': _public_candidates_stored,
                'public_candidates_rejected': _public_candidates_rejected,
            }

            return (
                hits, discovery_result, discovery_error, discovery_error_type, discovery_elapsed_s, discovery_attempted,
                discovery_telemetry, academic_findings_count, ct_injected, cc_injected,
                onion_findings_count, pastebin_findings_count, github_secrets_count
            )

    # ---- UMA check -----------------------------------------------------------
    # Sprint 8AK: SSOT labels from resource_governor — no local string literals
    from hledac.universal.core.resource_governor import (
        UMA_STATE_CRITICAL,
        UMA_STATE_EMERGENCY,
        UMA_STATE_OK,
    )

    uma_state = UMA_STATE_OK
    try:
        uma_state, _ = await _get_uma_state()
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001  # Defensive: proceed with ok state

    if uma_state == UMA_STATE_EMERGENCY:
        return PipelineRunResult(
            query=query,
            discovered=0,
            fetched=0,
            matched_patterns=0,
            accepted_findings=0,
            stored_findings=0,
            patterns_configured=_get_patterns_configured_count(),
            pages=(),
            error="uma_emergency_abort",
            public_discovery_blocker="uma_emergency_abort",
            public_fetch_accessibility_blocker=False,
            public_discovery_fallback_state=None,
            dominant_public_failure_mode="uma_emergency_abort",
            # Sprint F213B: stage failure accounting
            public_stage_failure="uma_emergency",
            public_stage_failure_reason="UMA emergency state blocks all public lane processing",
            public_discovery_attempted=False,
            public_discovery_raw_count=0,
            public_discovery_deduped_count=0,
            public_pages_fetched=0,
            public_pages_accepted=0,
            public_pages_rejected=0,
            public_findings_accepted=0,
            # F207I-A: emergency gate + telemetry
            public_fetch_gate="emergency_blocked",
            public_discovered=0,
            public_fetch_attempted=0,
            public_fetch_skipped=0,
            public_fetch_candidate_count=0,
            public_fetch_attempted_urls_sample=(),
            # F207J-C: PUBLIC Acceptance — zeroed (UMA emergency abort before fetch)
            public_acceptance_attempted=0,
            public_acceptance_accepted=0,
            public_acceptance_rejected=0,
            public_acceptance_reject_reasons={},
            public_accepted_url_sample=(),
            public_rejected_url_sample=(),
            # F208G-A: PUBLIC Yield Taxonomy — zeros (no URLs reached terminal classification)
            public_terminal_classified_count=0,
            public_unclassified_count=0,
            public_terminal_reason_counts={},
            public_fetch_success=0,
            public_fetch_failed=0,
            public_skipped_duplicate=0,
            public_skipped_unsupported_scheme=0,
            public_skipped_memory_gate=0,
            public_skipped_quality_gate=0,
            public_skipped_browser_unavailable=0,
            public_skipped_xml_or_feed=0,
            public_skipped_timeout=0,
            public_skipped_fetch_error=0,
            public_rejected_no_pattern_match=0,
            public_rejected_low_information=0,
            public_rejected_duplicate=0,
            public_rejected_storage_rejected=0,
            public_build_success_count=0,
            public_build_failure_count=0,
            public_duplicate_count=0,
            public_acceptance_ratio=0.0,
            public_skipped_url_sample=(),
            public_rejected_url_samples=(),
            # F231A: PUBLIC Candidate Ledger — zeroed (UMA emergency abort)
            public_candidates_discovered=0,
            public_candidates_fetch_attempted=0,
            public_candidates_fetch_success=0,
            public_candidates_parse_success=0,
            public_candidates_pattern_matched=0,
            public_candidates_built=0,
            public_candidates_store_attempted=0,
            public_candidates_stored=0,
            public_candidates_rejected=0,
            public_rejection_summary={},
            # Sprint F220C: Rescue telemetry (UMA emergency abort)
            public_rescue_candidates_count=0,
            public_rescue_fetch_attempted=0,
            public_rescue_fetch_success=0,
            public_rescue_accepted_findings=0,
            public_rescue_errors=0,
            public_rescue_order="disabled",
            public_terminal_stage="uma_emergency",
        )

    effective_concurrency = fetch_concurrency
    if uma_state == UMA_STATE_CRITICAL or uma_state == UMA_STATE_EMERGENCY:
        effective_concurrency = 1

    semaphore = asyncio.Semaphore(effective_concurrency)

    # ---- Call Discovery Engine -----------------------------------------------
    # Sprint F214: Refactored — inline discovery replaced with _DiscoveryEngine.run()
    (
        hits,
        discovery_result,
        discovery_error,
        discovery_error_type,
        discovery_elapsed_s,
        discovery_attempted,
        discovery_telemetry,
        academic_findings_count,
        ct_injected,
        cc_injected,
        onion_findings_count,
        pastebin_findings_count,
        github_secrets_count,
    ) = await _DiscoveryEngine(
        query=query,
        store=store,
        max_results=max_results,
        public_bootstrap_enabled=public_bootstrap_enabled,
        seed_context=seed_context,  # Sprint F223C: bounded seed_context bootstrap
    ).run(uma_state=uma_state)

    # Unpack discovery telemetry into main-line state
    public_stage_failure = discovery_telemetry.get('public_stage_failure')
    public_stage_failure_reason = discovery_telemetry.get('public_stage_failure_reason')
    public_discovery_deduped_count = discovery_telemetry.get('public_discovery_deduped_count', 0)
    public_discovery_cache_hit = discovery_telemetry.get('public_discovery_cache_hit', 0)
    public_discovery_query_count = discovery_telemetry.get('public_discovery_query_count', 0)
    _pub_bootstrap_candidates_count = discovery_telemetry.get('public_bootstrap_candidates_count', 0)
    _pub_bootstrap_fetch_attempted = discovery_telemetry.get('public_bootstrap_fetch_attempted', 0)
    _pub_bootstrap_fetch_success = discovery_telemetry.get('public_bootstrap_fetch_success', 0)
    _pub_bootstrap_accepted_findings = discovery_telemetry.get('public_bootstrap_accepted_findings', 0)
    _pub_bootstrap_errors = discovery_telemetry.get('public_bootstrap_errors', 0)
    _pub_bootstrap_order = discovery_telemetry.get('public_bootstrap_order', 'disabled')
    _pub_bootstrap_prevented_discovery_timeout = discovery_telemetry.get('public_bootstrap_prevented_discovery_timeout', False)  # noqa: E501
    _pub_bootstrap_first_fetch_attempted = discovery_telemetry.get('public_bootstrap_first_fetch_attempted', False)
    _pub_build_success_count = discovery_telemetry.get('public_build_success_count', 0)
    _pub_build_failure_count = discovery_telemetry.get('public_build_failure_count', 0)
    _pub_duplicate_count = discovery_telemetry.get('public_duplicate_count', 0)
    _pub_provider_selected = discovery_telemetry.get('public_provider_selected', [])
    _pub_provider_skipped = discovery_telemetry.get('public_provider_skipped', [])
    _pub_provider_stub = discovery_telemetry.get('public_provider_stub', [])
    _pub_provider_errors = discovery_telemetry.get('public_provider_errors', [])
    _pub_query_variants = discovery_telemetry.get('public_query_variants', [])
    _pub_provider_timeout_count = [discovery_telemetry.get('public_provider_timeout_count', 0)]
    _pub_provider_import_error_count = [discovery_telemetry.get('public_provider_import_error_count', 0)]
    _pub_discovery_empty_reason = [discovery_telemetry.get('public_discovery_empty_reason', '')]
    _public_candidates_discovered = discovery_telemetry.get('public_candidates_discovered', 0)
    _public_candidates_fetch_attempted = discovery_telemetry.get('public_candidates_fetch_attempted', 0)
    _public_candidates_fetch_success = discovery_telemetry.get('public_candidates_fetch_success', 0)
    _public_candidates_parse_success = discovery_telemetry.get('public_candidates_parse_success', 0)
    _public_candidates_pattern_matched = discovery_telemetry.get('public_candidates_pattern_matched', 0)
    _public_candidates_built = discovery_telemetry.get('public_candidates_built', 0)
    _public_candidates_store_attempted = discovery_telemetry.get('public_candidates_store_attempted', 0)
    _public_candidates_stored = discovery_telemetry.get('public_candidates_stored', 0)
    _public_candidates_rejected = discovery_telemetry.get('public_candidates_rejected', 0)
    # Sprint F220C: Rescue telemetry unpacking
    _pub_rescue_candidates_count = discovery_telemetry.get('public_rescue_candidates_count', 0)
    _pub_rescue_fetch_attempted = discovery_telemetry.get('public_rescue_fetch_attempted', 0)
    _pub_rescue_fetch_success = discovery_telemetry.get('public_rescue_fetch_success', 0)
    _pub_rescue_accepted_findings = discovery_telemetry.get('public_rescue_accepted_findings', 0)
    _pub_rescue_errors = discovery_telemetry.get('public_rescue_errors', 0)
    _pub_rescue_order = discovery_telemetry.get('public_rescue_order', 'disabled')

    # F1-3: keyword_seed_fallback — unpack from discovery telemetry into outer scope
    keyword_seed_fallback_triggered = discovery_telemetry.get('keyword_seed_fallback_triggered', False)

    # F207J-C: PUBLIC Acceptance — local accumulator for rejection reasons
    public_acceptance_reject_reasons: dict[str, int] = {}

    # ---- Fetch batch ---------------------------------------------------------
    # Per-call semaphore, no global batch timeout
    # F208G-A: URL-level dedup — skip duplicate URLs before creating fetch tasks
    # Sprint F213B: track discovery stage counts before dedup
    # F221H: Public Discovery Relevance / Shopping Noise Filter
    is_threat = _is_threat_query(query)
    hits, noise_rejections = _filter_public_noise(hits, is_threat)
    # F265C: Debug logging for noise filter analysis — tracks why discovered_urls=0
    if noise_rejections:
        logger.debug(
            "[F265C] Noise filter rejected %d/%d hits for query='%s' (is_threat=%s): %s",
            len(noise_rejections),
            len(hits) + len(noise_rejections),
            query[:80],
            is_threat,
            [f"{r}:{u[:60]}" for u, r in noise_rejections[:5]],
        )
    # Track noise rejections separately (will merge into public_acceptance_reject_reasons later)
    public_noise_reject_reasons: dict[str, int] = {}
    for _noise_url, noise_reason in noise_rejections:
        if noise_reason not in public_noise_reject_reasons:
            public_noise_reject_reasons[noise_reason] = 0
        public_noise_reject_reasons[noise_reason] += 1
    public_discovery_raw_count = len(hits)  # raw URLs from discovery (includes CT/CC injection)
    public_discovery_attempted = discovery_attempted
    seen_urls: set[str] = set()
    tasks: list[asyncio.Task] = []
    for hit in hits:
        hit_url = hit.url if hasattr(hit, "url") else str(hit[2])
        if hit_url in seen_urls:
            continue
        seen_urls.add(hit_url)
        # Sprint F150I: extract discovery score/reason if present (additive, fail-soft)
        hit_score: float | None = getattr(hit, "score", None)
        if hit_score is None and hasattr(hit, "__getitem__"):
            try:
                hit_score = float(hit[4]) if len(hit) > 4 else None
            except (ValueError, TypeError):
                hit_score = None

        hit_reason: str | None = getattr(hit, "reason", None)
        if hit_reason is None and hasattr(hit, "__getitem__"):
            try:
                hit_reason = str(hit[5]) if len(hit) > 5 else None
            except (ValueError, TypeError):
                hit_reason = None

        task = safe_create_task(
            _fetch_and_process_page(
                semaphore=semaphore,
                query=query,
                hit_url=hit.url if hasattr(hit, "url") else str(hit[2]),
                hit_title=hit.title if hasattr(hit, "title") else str(hit[1] if len(hit) > 1 else ""),
                hit_snippet=hit.snippet if hasattr(hit, "snippet") else str(hit[3] if len(hit) > 3 else ""),
                hit_rank=hit.rank if hasattr(hit, "rank") else 0,
                fetch_timeout_s=fetch_timeout_s,
                fetch_max_bytes=fetch_max_bytes,
                store=store,
                memory_manager=memory_manager,
                session_id=session_id,
                discovery_score=hit_score,
                discovery_reason=hit_reason,
                vector_store=vector_store,
                graph=graph,
            ),
            name="fetch:public_page",
        )
        tasks.append(task)

    # F261: safe_gather centralizes [I6][I7][I8] invariants at the gather boundary.
    # Same return shape as before (ok_results + error_results) so downstream
    # code at 3911/4225/4227 keeps working unchanged.
    from hledac.universal.utils.async_helpers import safe_gather
    _result = await safe_gather(*tasks, label="live_public_page_fetch")
    ok_results, error_results = _result.ok, _result.errors

    # Assemble page results in discovery order (skipping exceptions)
    all_page_results: list[PipelinePageResult] = []
    for item in ok_results:
        if isinstance(item, PipelinePageResult):
            all_page_results.append(item)

    # ---- Aggregate -----------------------------------------------------------
    total_discovered = len(hits)
    total_fetched = sum(1 for p in all_page_results if p.fetched)
    total_matched = sum(p.matched_patterns for p in all_page_results)
    total_accepted = sum(p.accepted_findings for p in all_page_results)
    total_stored = sum(p.stored_findings for p in all_page_results)
    patterns_cfg = _get_patterns_configured_count()

    # F207F: PUBLIC Yield telemetry — aggregate from per-page telemetry
    public_discovered = total_discovered
    public_fetch_attempted = sum(1 for p in all_page_results if p.fetched)
    public_fetch_skipped = sum(1 for p in all_page_results if not p.fetched)
    public_fetch_skip_reason = None
    public_js_renderer_unavailable = sum(
        1 for p in all_page_results
        if p.fetched and p.js_renderer_skipped_reason == "browser_unavailable"
    )
    public_xml_or_rss_detected = sum(
        1 for p in all_page_results
        if p.fetched and p.js_renderer_skipped_reason in ("xml_or_feed_url", "xml_recovered")
    )
    public_fetch_timeout_count = sum(
        1 for p in all_page_results
        if not p.fetched and p.fetch_blocked_reason == "timeout"
    )
    public_fetch_blocked_by_memory = sum(
        1 for p in all_page_results
        if not p.fetched and p.fetch_blocked_reason == "uma_memory"
    )
    # Dominant skip reason for reporting
    skip_reasons = [p.fetch_blocked_reason for p in all_page_results if not p.fetched and p.fetch_blocked_reason]
    if skip_reasons:
        from collections import Counter
        public_fetch_skip_reason = Counter(skip_reasons).most_common(1)[0][0]

    # F207I-A: memory gate verdict
    if uma_state == UMA_STATE_EMERGENCY:
        public_fetch_gate = "emergency_blocked"
    elif uma_state == UMA_STATE_CRITICAL:
        public_fetch_gate = "critical_limited"
    else:
        public_fetch_gate = "ok"

    # F207I-A: new telemetry aggregation
    # F208G-A: len(seen_urls) = unique URLs after dedup (dedup skipped URLs excluded from all_page_results)
    public_fetch_candidate_count = len(seen_urls)
    public_skipped_duplicate = len(hits) - len(seen_urls)  # F208G-A: dedup gap
    fetched_urls_sample_list = [p.url for p in all_page_results if p.fetched][:5]
    public_fetch_attempted_urls_sample = tuple(fetched_urls_sample_list)

    # F207J-C: PUBLIC Acceptance — post-fetch acceptance/rejection aggregation
    # Only pages where fetch was attempted (fetched=True) enter acceptance classification
    _fetched_pages = [p for p in all_page_results if p.fetched]
    public_acceptance_attempted = len(_fetched_pages)
    public_acceptance_accepted: int = 0  # pages with accepted_findings > 0
    public_acceptance_rejected: int = 0  # pages with accepted_findings == 0 (post-fetch rejection)
    accepted_urls: list[str] = []
    rejected_urls: list[str] = []
    for p in _fetched_pages:
        rr = getattr(p, "rejection_reason", None)
        if rr is None:
            # Accepted: had pattern matches that passed storage gate
            public_acceptance_accepted += 1
            if len(accepted_urls) < 5:
                accepted_urls.append(p.url)
        else:
            # Rejected: reasons include empty_text, no_pattern_match, low_information, etc.
            public_acceptance_rejected += 1
            public_acceptance_reject_reasons[rr] = public_acceptance_reject_reasons.get(rr, 0) + 1
            if len(rejected_urls) < 5:
                rejected_urls.append(p.url)
    # F221H: Merge pre-fetch noise rejections into acceptance reject reasons
    for reason, count in public_noise_reject_reasons.items():
        public_acceptance_reject_reasons[reason] = public_acceptance_reject_reasons.get(reason, 0) + count
    public_accepted_url_sample = tuple(accepted_urls)
    public_rejected_url_sample = tuple(rejected_urls)

    # F208G-A: PUBLIC Yield Taxonomy — run-level terminal classification
    # Classify every URL by terminal_reason; accepted/skipped/rejected buckets
    from collections import Counter
    _tr_counter: Counter[str] = Counter()
    _skipped_samples: list[str] = []
    _rejected_samples: list[str] = []
    for p in all_page_results:
        tr = getattr(p, "terminal_reason", None)
        if tr is None:
            _tr_counter["accepted"] += 1
        else:
            _tr_counter[tr] += 1
            if tr.startswith("skipped_") and len(_skipped_samples) < 5:
                _skipped_samples.append(p.url)
            elif tr.startswith("rejected_") and len(_rejected_samples) < 5:
                _rejected_samples.append(p.url)

    # Run-level counts
    _classified = sum(v for k, v in _tr_counter.items() if k != "accepted")
    _accepted = _tr_counter.get("accepted", 0)
    public_terminal_classified_count = _classified
    public_unclassified_count = len(all_page_results) - _classified - _accepted
    public_terminal_reason_counts = dict(_tr_counter)

    # Fetch outcome
    public_fetch_success = sum(1 for p in all_page_results if p.fetched)
    public_fetch_failed = sum(1 for p in all_page_results if not p.fetched)

    # Sprint F213B: PUBLIC discovery stage counters
    public_discovery_deduped_count = len(seen_urls)  # unique URLs after dedup

    # Sprint F213B: PUBLIC page/finding acceptance counters
    public_pages_fetched = sum(1 for p in all_page_results if p.fetched)
    public_pages_accepted = sum(1 for p in all_page_results if p.accepted_findings > 0)
    public_pages_rejected = sum(1 for p in all_page_results if p.fetched and p.accepted_findings == 0)
    public_findings_accepted = sum(p.accepted_findings for p in all_page_results)

    # Sprint F213B: stage failure — discovery returned URLs but no findings accepted
    if public_discovery_deduped_count > 0 and public_findings_accepted == 0:
        public_stage_failure = "fetch_zero"
        public_stage_failure_reason = f"discovery returned {public_discovery_deduped_count} URLs but no findings were accepted"  # noqa: E501

    # F231A: PUBLIC Candidate Ledger — derive from page results
    # Tracks stage progression: discovery → fetch_attempted → fetch_success → parse_success → pattern_matched → built → store_attempted → stored/rejected  # noqa: E501
    # fetch_attempted = pages that passed quality gate and entered page processing
    public_candidates_discovered = total_discovered
    public_candidates_fetch_attempted = public_pages_fetched  # pages that entered fetch/parse
    public_candidates_fetch_success = sum(
        1 for p in all_page_results
        if p.fetched and p.error is not None and not p.error.startswith(("fetch_text_none_or_empty", "html_extract_failed"))  # noqa: E501
    )
    public_candidates_parse_success = sum(
        1 for p in all_page_results if p.fetched and not p.error  # noqa: E501
    )
    public_candidates_pattern_matched = sum(1 for p in all_page_results if p.fetched and p.matched_patterns > 0)
    public_candidates_built = sum(
        1 for p in all_page_results
        if p.fetched and (p.matched_patterns > 0 or p.accepted_findings > 0)
    )
    public_candidates_store_attempted = sum(1 for p in all_page_results if p.fetched and p.matched_patterns > 0)
    public_candidates_stored = sum(1 for p in all_page_results if p.stored_findings > 0)
    public_candidates_rejected = sum(
        1 for p in all_page_results
        if p.fetched and p.matched_patterns > 0 and p.stored_findings == 0
    )
    # Build rejection summary by stage
    _rej_sum: dict[str, int] = {}
    if public_candidates_fetch_attempted == 0 and public_candidates_discovered > 0:
        _rej_sum["fetch_zero"] = public_candidates_discovered - public_candidates_fetch_attempted
    if public_candidates_pattern_matched == 0 and public_candidates_fetch_success > 0:
        _rej_sum["match_zero"] = public_candidates_fetch_success - public_candidates_pattern_matched
    if public_candidates_store_attempted > 0 and public_candidates_stored == 0:
        _rej_sum["store_zero"] = public_candidates_store_attempted
    public_rejection_summary = _rej_sum

    # Sprint 300s BUILT_FAIL diagnostic: log per-URL rejection reasons when built_count=0
    _built_count = sum(
        1 for p in all_page_results
        if p.fetched and (p.matched_patterns > 0 or p.accepted_findings > 0)
    )
    if _built_count == 0 and public_candidates_fetch_success > 0:
        _logger = __import__("logging").getLogger(__name__)
        for p in all_page_results:
            if not p.fetched:
                continue
            _text_len = getattr(p, "extracted_text_len", 0) or 0
            _matched = p.matched_patterns or 0
            _accepted = p.accepted_findings or 0
            _err = p.error or ""
            _reason = p.quality_reason or ""
            if _matched == 0 and _accepted == 0:
                _logger.warning(
                    "[BUILT_FAIL] %s: no IoCs in %d chars, error=%r, quality_reason=%r",
                    (p.url or "")[:120], _text_len, _err, _reason,
                )
    # F231A: Derive canonical terminal stage
    if not public_candidates_discovered:
        public_terminal_stage = "discovery_empty"
    elif public_candidates_fetch_attempted == 0:
        public_terminal_stage = "fetch_zero"
    elif public_candidates_pattern_matched == 0:
        public_terminal_stage = "match_zero"
    elif public_candidates_stored == 0:
        public_terminal_stage = "store_zero"
    else:
        public_terminal_stage = "accepted"

    # F221G: Public discovery empty reason consistency
    # If public produced accepted findings, empty_reason contradicts the outcome.
    # Preserve original diagnostic in debug_reason, clear empty_reason.
    _accepted_findings = sum(p.accepted_findings for p in all_page_results) if all_page_results else 0
    if (
        public_terminal_stage == "accepted"
        or _accepted_findings > 0
        or public_candidates_stored > 0
    ) and _pub_discovery_empty_reason and _pub_discovery_empty_reason[0]:
        _original_empty_reason = _pub_discovery_empty_reason[0]
        _pub_discovery_empty_reason[0] = ""
        # Pass debug reason through discovery_telemetry for downstream consumption
        discovery_telemetry["public_discovery_debug_reason"] = _original_empty_reason

    # Skipped breakdown
    # F208G-A: public_skipped_duplicate already computed as len(hits)-len(seen_urls) at line 2575
    # Do NOT overwrite with _tr_counter lookup (duplicates never reach page processing)
    public_skipped_unsupported_scheme = _tr_counter.get("skipped_unsupported_scheme", 0)
    public_skipped_memory_gate = _tr_counter.get("skipped_memory_gate", 0)
    public_skipped_quality_gate = _tr_counter.get("skipped_quality_gate", 0)
    public_skipped_browser_unavailable = _tr_counter.get("skipped_browser_unavailable", 0)
    public_skipped_xml_or_feed = _tr_counter.get("skipped_xml_or_feed", 0)
    public_skipped_timeout = _tr_counter.get("skipped_timeout", 0)
    public_skipped_fetch_error = _tr_counter.get("skipped_fetch_error", 0)

    # Rejected breakdown
    public_rejected_no_pattern_match = _tr_counter.get("rejected_no_pattern_match", 0)
    public_rejected_low_information = _tr_counter.get("rejected_low_information", 0)
    public_rejected_duplicate = _tr_counter.get("rejected_duplicate", 0)
    public_rejected_storage_rejected = _tr_counter.get("rejected_storage_rejected", 0)

    # F226B: PUBLIC acceptance uplift — public_surface finding build outcomes
    # _pub_duplicate_count: public_surface findings already seen in same run (deduped at per-page level)
    _pub_dup_total = sum(
        1 for p in all_page_results
        if getattr(p, "public_surface_dup", False)
    )
    _pub_duplicate_count = _pub_dup_total
    # F230B: Compute bootstrap fetch success from page results
    # Bootstrap URLs were prepended to hits with source="bootstrap"
    _bootstrap_candidate_urls = {
        p.url for p in all_page_results
        if getattr(p, "url", "").startswith("http")
    }
    _pub_bootstrap_fetch_success = sum(
        1 for p in all_page_results
        if p.fetched and p.url in _bootstrap_candidate_urls
    )
    # Sprint F220C: Rescue fetch success from rescue source hits
    _rescue_candidate_urls = {
        p.url for p in all_page_results
        if getattr(p, "url", "").startswith("http")
    }
    _pub_rescue_fetch_success = sum(
        1 for p in all_page_results
        if p.fetched and p.url in _rescue_candidate_urls
    )
    # public_build_failure_count already accumulated during page processing for zero-match pages
    # that passed quality gate but produced no actionable finding
    public_build_success_count = _pub_build_success_count
    public_build_failure_count = _pub_build_failure_count
    public_duplicate_count = _pub_duplicate_count
    public_acceptance_ratio = _pub_build_success_count / max(_pub_build_success_count + _pub_build_failure_count, 1)

    # Bounded URL samples
    public_skipped_url_sample = tuple(_skipped_samples)
    public_rejected_url_samples = tuple(_rejected_samples)

    # Sprint F150J Fix B: branch economics counters
    # Fix weak_pages_skipped: SKIP_WEAK post-fetch pages have error=None (not error!=None)
    strong_pages = sum(
        1 for p in all_page_results
        if p.quality_reason == "very_good"
    )
    weak_pages_skipped = sum(
        1 for p in all_page_results
        if p.quality_reason is not None and p.quality_reason.startswith("SKIP_WEAK")
    )
    # low-value = fetched but poor quality + no matches
    low_value_fetches = sum(
        1 for p in all_page_results
        if p.fetched
        and p.matched_patterns == 0
        and p.quality_reason in ("weak_low_signal", "ok:no_query_signal")
    )
    # Sprint F150J: additive derived counters for public-branch value assessment
    # discovery_strong_content_weak: discovery signal but page yielded nothing
    discovery_strong_content_weak = sum(
        1 for p in all_page_results
        if (p.discovery_signal and p.matched_patterns == 0)
    )
    # discovery_and_content_strong: both discovery signal and pattern yield
    discovery_and_content_strong = sum(
        1 for p in all_page_results
        if p.discovery_signal and p.matched_patterns > 0
    )
    # Sprint F150K: discovery_squandered — strong discovery score but page quality weak
    # (promarněný strong discovery hit = high score but got SKIP_WEAK or weak_low_signal)
    # Sprint F162B: threshold aligned with _FETCH_BUDGET_STRONG = 0.85
    discovery_squandered = sum(
        1 for p in all_page_results
        if p.discovery_score is not None
        and p.discovery_score >= 0.85
        and p.quality_reason in ("weak_low_signal", "SKIP_WEAK:weak_discovery", "SKIP_WEAK:very_low_text")
    )
    # Sprint F150K: build derived value metrics
    fetched_pages = [p for p in all_page_results if p.fetched]
    fetched_count = len(fetched_pages)

    # noise_fetch_ratio: what fraction of fetched pages yielded zero patterns
    noise_fetch_ratio = (
        round(low_value_fetches / fetched_count, 3)
        if fetched_count > 0
        else 0.0
    )
    # waste_ratio = pages that consumed budget but yielded nothing
    waste_ratio = (
        round(low_value_fetches / fetched_count, 3)
        if fetched_count > 0
        else 0.0
    )
    # value_ratio = pages with actual pattern yield vs total discovered
    value_ratio = (
        round(discovery_and_content_strong / total_discovered, 3)
        if total_discovered > 0
        else 0.0
    )
    # public_branch_hint: one-liner signal quality label
    if strong_pages >= 2 and discovery_and_content_strong >= 2:
        public_branch_hint = "high_value"
    elif discovery_and_content_strong >= 1:
        public_branch_hint = "some_value"
    elif discovery_strong_content_weak >= 1:
        public_branch_hint = "weak_signal"
    elif weak_pages_skipped > 0 and fetched_count == 0:
        public_branch_hint = "skipped_low_quality"
    else:
        public_branch_hint = "low_value"

    # corroboration_vs_burn: strong signal corroboration vs pure budget drain
    # = (discovery_and_content_strong + strong_pages) / max(total_discovered, 1)
    corroboration_vs_burn = (
        round((discovery_and_content_strong + strong_pages) / max(total_discovered, 1), 3)
    )

    run_error: str | None = None
    if discovery_error:
        run_error = discovery_error
    elif error_results:
        # Surface first error
        err = error_results[0]
        run_error = f"batch_error:{type(err).__name__}:{err}"

    # Sprint F150K: operator-facing hints
    if strong_pages >= 2 and discovery_and_content_strong >= 2:
        public_next_action = "expand_public_branch"
        public_confidence_note = "high_yield_run"
    elif discovery_and_content_strong >= 1 and discovery_squandered == 0:
        public_next_action = "continue_public_branch"
        public_confidence_note = "positive_signal"
    elif discovery_squandered >= 1 and discovery_strong_content_weak >= 1:
        public_next_action = "review_discovery_quality"
        public_confidence_note = "squandered_hits_detected"
    elif noise_fetch_ratio >= 0.5:
        public_next_action = "drain_public_branch"
        public_confidence_note = "high_noise_ratio"
    elif weak_pages_skipped >= total_discovered * 0.5:
        public_next_action = "throttle_public_branch"
        public_confidence_note = "low_quality_majority"
    else:
        public_next_action = "hold_public_branch"
        public_confidence_note = "marginal_signal"

    # Sprint F206P: temporal signal summary (advisory, fail-soft)
    try:
        from hledac.universal.layers import get_temporal_signal_summary
        temporal_signal_summary = get_temporal_signal_summary(k=10)
    except Exception:
        temporal_signal_summary = {}

    # Sprint F206R: temporal priority hints (advisory, bounded top-10, fail-soft)
    try:
        from hledac.universal.layers import build_temporal_priority_hints
        temporal_priority_hints = build_temporal_priority_hints(k=10)
    except Exception:
        temporal_priority_hints = []

    # Sprint F206Q: save snapshot at pipeline end (fail-soft)
    persistence_saved = False
    try:
        from hledac.universal.layers import save_temporal_signal_snapshot
        persistence_saved = save_temporal_signal_snapshot()
    except Exception:  # noqa: BLE001
        pass

    public_branch_verdict = {
        "waste_ratio": waste_ratio,
        "value_ratio": value_ratio,
        "public_branch_hint": public_branch_hint,
        "strong_pages": strong_pages,
        "weak_pages_skipped": weak_pages_skipped,
        "discovery_strong_content_weak": discovery_strong_content_weak,
        "discovery_and_content_strong": discovery_and_content_strong,
        "low_value_fetches": low_value_fetches,
        "discovery_squandered": discovery_squandered,
        "noise_fetch_ratio": noise_fetch_ratio,
        "corroboration_vs_burn": corroboration_vs_burn,
        "public_next_action": public_next_action,
        "public_confidence_note": public_confidence_note,
        "temporal_signal_summary": temporal_signal_summary,
        # Sprint F206R: temporal priority hints (advisory, no scheduler mutation)
        "temporal_priority_hints": temporal_priority_hints,
        # Sprint F206Q: persistence flags
        "persistence_enabled": persistence_enabled,
        "persistence_restored": persistence_restored,
        "persistence_saved": persistence_saved,
    }

    # Sprint F150L: usable-value run-level aggregates
    usable_findings_ratio = round(total_stored / max(total_discovered, 1), 3)
    discovery_to_findings_efficiency = round(
        discovery_and_content_strong / max(total_discovered, 1), 3
    )
    public_value_density = round(total_stored / max(total_fetched, 1), 3)
    # Sprint F162B: factual_value_density uses fetched as denominator (real conversion density)
    factual_value_density = round(total_stored / max(total_fetched, 1), 3)

    # quality_mix: composition summary from per-page value_tiers
    tier_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "waste": 0, "none": 0}
    for p in all_page_results:
        tier = getattr(p, "value_tier", "none")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
    mix_parts = [f"{v}{k[0]}" for k, v in tier_counts.items() if v > 0]
    quality_mix = "|".join(mix_parts) if mix_parts else "empty"

    # top_waste_pattern: dominant waste reason from existing buckets
    waste_reasons: dict[str, int] = {}
    for p in all_page_results:
        if getattr(p, "value_tier", "none") == "waste":
            reason = getattr(p, "resolution_reason", "unknown") or "unknown"
            waste_reasons[reason] = waste_reasons.get(reason, 0) + 1
    top_waste_pattern = (
        max(waste_reasons, key=lambda r: waste_reasons[r]) if waste_reasons else ""
    )

    # Sprint F161B: conversion truth run-level aggregates
    fetched_pages = [p for p in all_page_results if p.fetched]
    fetched_count = len(fetched_pages)

    discovery_false_positive_count = sum(
        1 for p in all_page_results if getattr(p, "discovery_false_positive", False)
    )

    # waste_category_counts: aggregate from per-page waste_category
    waste_category_counts = {"structural": 0, "signalless": 0, "false_positive": 0, "error": 0}
    for p in all_page_results:
        cat = getattr(p, "waste_category", "")
        if cat in waste_category_counts:
            waste_category_counts[cat] += 1

    # structural_health_ratio: fraction of fetched pages that are structurally healthy
    structural_health_ratio = (
        round(sum(1 for p in fetched_pages if getattr(p, "structural_quality", "") == "healthy") / max(fetched_count, 1), 3)  # noqa: E501
        if fetched_count > 0 else 0.0
    )

    # Sprint F162B: run_waste_pattern_code — dominant clean waste category code
    run_waste_pattern_code = (
        max(waste_category_counts, key=lambda k: waste_category_counts[k])
        if any(v > 0 for v in waste_category_counts.values())
        else ""
    )

    # Sprint F162B: waste_reason_breakdown — distribution of waste categories
    waste_reason_breakdown = "|".join(
        f"{v}{k[:3]}" for k, v in sorted(waste_category_counts.items()) if v > 0
    ) if any(v > 0 for v in waste_category_counts.values()) else "none"

    # Sprint F163B: backend_degraded — fetch errors dominate discovery output
    # Not "low value" — true infrastructure failure that makes content inaccessible
    # Threshold: >60% of all pages had fetch errors OR discovery failed with zero fetches
    _error_page_count = sum(1 for p in all_page_results if p.error is not None and "fetch_exception" in p.error)
    _error_dominated = total_discovered > 0 and _error_page_count / total_discovered > 0.6
    _backend_degraded = bool(_error_dominated or (discovery_error is not None and total_fetched == 0))

    # Sprint F163B: enhanced public_proof_grade — decouple backend failure from weak content
    # "no_discovery" and "empty" are discovery problems, not content problems
    # "backend_degraded" overrides everything below it — the content was never even evaluated
    if _backend_degraded:
        _derived_proof_grade = "backend_degraded"
    elif factual_value_density >= 0.5 and structural_health_ratio >= 0.7 and noise_fetch_ratio <= 0.3:
        _derived_proof_grade = "strong"
    elif factual_value_density >= 0.3 and noise_fetch_ratio <= 0.5:
        _derived_proof_grade = "moderate"
    elif factual_value_density > 0 or total_stored > 0:
        _derived_proof_grade = "weak"
    elif total_discovered > 0:
        _derived_proof_grade = "empty"
    else:
        _derived_proof_grade = "no_discovery"

    # Sprint F163B: embed backend_degraded and public_proof_grade into verdict dict
    public_branch_verdict["backend_degraded"] = _backend_degraded
    public_branch_verdict["public_proof_grade"] = _derived_proof_grade

    # Sprint F206AB: discovery error taxonomy — concrete error reason preserved in verdict
    public_branch_verdict["discovery_error_detail"] = discovery_error  # None | "network_error" | "server_error" | etc.

    # Sprint F170D: lower-layer truth consumption
    # Read fallback_triggered from discovery_result
    fallback_triggered: str | None = getattr(discovery_result, "fallback_triggered", None)

    # F185A DF-3 FIX: replace hardcoded if/elif chain with explicit dictionary.
    # Key: duckduckgo_adapter.py fallback_triggered string → public pipeline enum string.
    # This eliminates the silent-fail risk when new fallback_triggered variants are added.
    _FALLBACK_STATE_MAP: dict[str, str] = {  # noqa: N806
        "primary_backend_failed_fallback_succeeded": "primary_failed_fallback_succeeded",
        "primary_backend_failed_fallback_failed": "primary_failed_fallback_failed",
    }
    public_discovery_fallback_state = _FALLBACK_STATE_MAP.get(fallback_triggered) or (
        "no_fallback_needed" if discovery_error is None else None
    )

    # Sprint F206AB: per-stage discovery counters (additive telemetry)
    public_branch_verdict["discovery_calls"] = 1  # always 1 in current single-discovery architecture
    public_branch_verdict["discovery_hits_total"] = len(hits)
    public_branch_verdict["discovery_error_count"] = 1 if discovery_error else 0
    public_branch_verdict["discovery_fallback_count"] = 1 if fallback_triggered else 0

    # Sprint F206AB: discovery error taxonomy — additive fields
    public_branch_verdict["discovery_attempted"] = discovery_attempted
    public_branch_verdict["discovery_elapsed_s"] = discovery_elapsed_s
    public_branch_verdict["discovery_error_type"] = discovery_error_type  # F206AB taxonomy string
    public_branch_verdict["discovery_fallback_triggered"] = fallback_triggered  # raw adapter string

    # Sprint F206AO: provider metadata from DiscoveryBatchResult
    _dbr_provider_name = getattr(discovery_result, "provider_name", None)
    _dbr_provider_chain = getattr(discovery_result, "provider_chain", None)
    _dbr_source_family = getattr(discovery_result, "source_family", None)
    _dbr_elapsed_s = getattr(discovery_result, "elapsed_s", None)
    _dbr_error_type = getattr(discovery_result, "error_type", None)
    if _dbr_provider_name is not None:
        public_branch_verdict["discovery_provider_name"] = _dbr_provider_name
    if _dbr_provider_chain is not None:
        public_branch_verdict["discovery_provider_chain"] = _dbr_provider_chain
    if _dbr_source_family is not None:
        public_branch_verdict["discovery_source_family"] = _dbr_source_family
    if _dbr_elapsed_s is not None:
        public_branch_verdict["discovery_provider_elapsed_s"] = _dbr_elapsed_s
    if _dbr_error_type is not None:
        public_branch_verdict["discovery_provider_error_type"] = _dbr_error_type

    # Sprint F206AB: fetch stage counters — collected from all_page_results
    # Success: p.fetched=True AND p.error=None (per PipelinePageResult construction pattern)
    _fetch_attempted = 0
    _fetch_success = 0
    _fetch_error = 0
    for p in all_page_results:
        _fetch_attempted += 1
        p_fetched = getattr(p, "fetched", False)
        p_error = getattr(p, "error", None)
        if p_fetched and p_error is None:
            _fetch_success += 1
        else:
            _fetch_error += 1
    public_branch_verdict["fetch_attempted"] = _fetch_attempted
    public_branch_verdict["fetch_success"] = _fetch_success
    public_branch_verdict["fetch_error"] = _fetch_error

    # Sprint F206AC: fetch error taxonomy — per-URL classification with bounded samples
    _fetch_error_types: dict[str, int] = {}
    _fetch_error_samples: list[dict] = []
    for p in all_page_results:
        pfr = getattr(p, "_fetch_result", None)
        err_type = classify_fetch_error(pfr) if pfr is not None else classify_fetch_error(p.error)
        _fetch_error_types[err_type] = _fetch_error_types.get(err_type, 0) + 1
        if err_type != "none" and len(_fetch_error_samples) < 5:
            sample: dict = {
                "url": p.url,
                "selected_transport": getattr(pfr, "selected_transport", None) if pfr is not None else None,
                "status_code": getattr(pfr, "status_code", None) if pfr is not None else None,
                "error_type": err_type,
                "error": p.error,
                "failure_stage": p.failure_stage,
                "network_error_kind": getattr(pfr, "network_error_kind", None) if pfr is not None else None,
                "transport_policy_reason": getattr(pfr, "transport_policy_reason", None) if pfr is not None else None,
                "transport_fallback_reason": getattr(pfr, "transport_fallback_reason", None) if pfr is not None else None,  # noqa: E501
                "content_type": getattr(pfr, "content_type", None) if pfr is not None else None,
            }
            _fetch_error_samples.append(sample)
    public_branch_verdict["fetch_error_types"] = _fetch_error_types
    public_branch_verdict["fetch_error_samples"] = _fetch_error_samples

    # Sprint F206AB: admission and pattern hit counters
    # admitted_urls: URL count after deduplication, before fetch
    public_branch_verdict["admitted_urls"] = len(hits) if hits else 0

    # pattern_hits: sum of matched_patterns across all fetched pages
    public_branch_verdict["pattern_hits"] = sum(p.matched_patterns for p in all_page_results)

    # F185A DF-3 FIX: same dictionary approach for public_discovery_blocker
    _BLOCKER_BY_BACKEND_ERROR: dict[str, str] = {  # noqa: N806
        "primary_backend_failed_fallback_failed": "backend_error_fallback_failed",
    }
    if uma_state == "UMA_STATE_EMERGENCY":
        public_discovery_blocker = "uma_emergency_abort"
    elif discovery_error is not None and fallback_triggered is None:
        public_discovery_blocker = "backend_error_no_fallback"
    else:
        public_discovery_blocker = _BLOCKER_BY_BACKEND_ERROR.get(fallback_triggered)

    # public_fetch_accessibility_blocker: True when any page had connectivity/TLS/timeout failure
    # failure_stage IN {connection, tls, http} OR network_error_kind signals accessibility issue
    _accessibility_failure_stages = {"connection", "tls", "http"}
    public_fetch_accessibility_blocker = any(
        p.failure_stage in _accessibility_failure_stages
        for p in all_page_results
    )

    # dominant_public_failure_mode: aggregate failure story
    # Priority: discovery blocker > fetch_accessibility_blocker > redirect_non_content > waste:*
    _failure_modes: list[str] = []
    if public_discovery_blocker:
        _failure_modes.append(public_discovery_blocker)
    if public_fetch_accessibility_blocker:
        _failure_modes.append("fetch_accessibility_blocker")
    # Sprint F171A: redirect-induced non-content — redirected AND ended as structural/signalless waste
    # Only triggers for pages that were actually fetched and found thin/dead content at redirect target
    _any_redirect_non_content = any(
        p.redirected and p.waste_category in ("structural", "signalless")
        for p in all_page_results
    )
    if _any_redirect_non_content:
        _failure_modes.append("redirect_non_content")
    # Add dominant waste category if present
    if run_waste_pattern_code and run_waste_pattern_code != "none":
        _failure_modes.append(f"waste:{run_waste_pattern_code}")
    dominant_public_failure_mode = _failure_modes[0] if _failure_modes else None

    # Sprint F173C: zero-hit evidence aggregation
    # zero_hit_accessible_fetch_count: pages that were fetched with 0 matches
    zero_hit_accessible_fetch_count = sum(
        1 for p in all_page_results
        if p.fetched and p.matched_patterns == 0
    )
    # zero_hit_quality_reason_counts: why zero-hit pages failed
    _zero_hit_reasons: dict[str, int] = {}
    _zero_hit_titles: list[tuple[str, str]] = []  # (title, url) pairs, bounded
    for p in all_page_results:
        if p.fetched and p.matched_patterns == 0 and p.quality_reason:
            _zero_hit_reasons[p.quality_reason] = _zero_hit_reasons.get(p.quality_reason, 0) + 1
        if p.fetched and p.matched_patterns == 0 and len(_zero_hit_titles) < 5:
            # Capture title+url for gate evidence (no raw text)
            p_title = getattr(p, "discovery_reason", "") or ""
            _zero_hit_titles.append((p_title, p.url))
    zero_hit_quality_reason_counts = _zero_hit_reasons
    zero_hit_title_samples = tuple(_zero_hit_titles)
    # public_zero_hit_summary: structured run-level summary
    public_zero_hit_summary = {
        "zero_hit_accessible_fetch_count": zero_hit_accessible_fetch_count,
        "zero_hit_unique_reasons": list(zero_hit_quality_reason_counts.keys()),
        "zero_hit_has_substantive_content": any(
            p.fetched and p.matched_patterns == 0
            and getattr(p, "structural_quality", "") == "healthy"
            for p in all_page_results
        ),
        "zero_hit_has_signalless": any(
            p.fetched and p.matched_patterns == 0
            and getattr(p, "waste_category", "") == "signalless"
            for p in all_page_results
        ),
        "zero_hit_has_false_positive": any(
            p.fetched and p.matched_patterns == 0
            and getattr(p, "discovery_false_positive", False)
            for p in all_page_results
        ),
        "zero_hit_has_redirect_non_content": any(
            p.fetched and p.matched_patterns == 0
            and p.redirected and p.waste_category in ("structural", "signalless")
            for p in all_page_results
        ),
    }

    # P6: Generate OSINT report from top findings (if Hermes available)
    # Fail-soft: report generation is optional, pipeline continues regardless
    generated_report = ""
    if hermes_engine is not None and all_page_results:
        try:
            generated_report = await _generate_and_store_report(
                query=query,
                pages=tuple(all_page_results),
                store=store,
                hermes_engine=hermes_engine,
                vector_store=vector_store,
            )
        except Exception:
            generated_report = ""  # Fail-soft: report generation errors don't fail the pipeline

    # FÁZE P9: Export graph after pipeline completes (legacy path)
    if graph is not None and graph.node_count() > 0:
        try:
            export_path = str(Path("~/new_hledac_graph.html").expanduser())
            graph.export_html(export_path)
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Fail-soft: graph export errors don't fail pipeline

    # P17: Run RL loop if --loop flag was set
    # Uses FederatedBridge (M1-safe) instead of loops.research_loop.ResearchLoop
    # which had M1 crash vectors (get_event_loop().run_until_complete() in async context)
    if run_loop and hermes_engine is not None:
        try:
            from hledac.universal.knowledge.duckdb_store import CanonicalFinding
            from hledac.universal.federated.bridge import FederatedBridge

            # P17: Default RL loop time limit (5 minutes)
            _RL_LOOP_TIME_LIMIT_S = 300.0  # noqa: N806
            # FederatedBridge uses lane-prefixed state; use "rl" as lane
            _RL_LANE = "rl"
            # Actions mirror loops.research_loop.ResearchLoop.ACTIONS
            _RL_ACTIONS = [
                "hypothesis_generation",
                "tot_reasoning",
                "discovery",
                "fetch",
                "graph_update",
                "evaluate",
                "done",
            ]

            bridge = FederatedBridge()
            # P17: Run either N steps or until time limit
            rl_start_time = time.monotonic()
            step_count = 0
            total_reward = 0.0
            # Build initial state for Q-learning
            rl_state: tuple = (query[:20] if len(query) > 20 else query, 0, 0, 6, False)
            rl_next_state: tuple = rl_state

            while True:
                # Check step limit first
                if rl_steps > 0 and step_count >= rl_steps:
                    break

                # Check time limit
                elapsed = time.monotonic() - rl_start_time
                if elapsed >= _RL_LOOP_TIME_LIMIT_S:
                    logger.info(f"[P17] RL loop time limit reached ({elapsed:.1f}s)")
                    break

                # P17: Get action from FederatedBridge Q-table (M1-safe, no event loop)
                action = bridge.get_best_action(_RL_LANE, rl_state, _RL_ACTIONS)
                if action == "done":
                    # Persist and break
                    await bridge.persist_if_due()
                    break

                # P17: Execute action via hermes_engine (simplified RL loop)
                reward = 0.0
                action_findings: list = []
                try:
                    # Generate hypotheses if that action
                    if action == "hypothesis_generation" and hermes_engine is not None:
                        ctx: dict[str, Any] = {"query": query, "source": "rl_loop"}
                        if hasattr(hermes_engine, "generate_hypotheses_async"):
                            hyp_strings = await hermes_engine.generate_hypotheses_async(
                                context=ctx,
                                hermes_engine=getattr(hermes_engine, "_inference_engine", None),
                            )
                            for h in (hyp_strings or [])[:10]:
                                action_findings.append({"type": "hypothesis", "content": h, "source": "rl_hypothesis"})
                            reward = len(action_findings) * 0.1
                        elif hasattr(hermes_engine, "generate_hypotheses"):
                            import inspect
                            if inspect.iscoroutinefunction(hermes_engine.generate_hypotheses):
                                hyp_strings = await hermes_engine.generate_hypotheses(ctx)
                            else:
                                hyp_strings = hermes_engine.generate_hypotheses(ctx)
                            for h in (hyp_strings or [])[:10]:
                                action_findings.append({"type": "hypothesis", "content": h, "source": "rl_hypothesis"})
                            reward = len(action_findings) * 0.1
                    elif action == "tot_reasoning":
                        action_findings.append({"type": "tot", "content": f"ToT reasoning for: {query[:50]}", "source": "rl_tot"})
                        reward = 0.3
                    elif action == "discovery":
                        action_findings.append({"type": "discovery", "content": f"Discovery: {query}", "source": "rl_discovery"})
                        reward = 0.2
                    elif action == "fetch":
                        action_findings.append({"type": "fetch", "content": f"Fetch: {query}", "source": "rl_fetch"})
                        reward = 0.1
                    elif action == "evaluate":
                        action_findings.append({"type": "evaluation", "content": f"Evaluation: {query}", "source": "rl_evaluate"})
                        reward = 0.15
                    # graph_update is no-op for findings
                except Exception as e:
                    logger.debug(f"[P17] RL action '{action}' failed: {e}")

                # P17: Update Q-table via FederatedBridge
                new_findings_count = len(action_findings)
                rl_next_state = (
                    query[:20] if len(query) > 20 else query,
                    min(step_count // 2, 5),
                    min(new_findings_count // 10, 10),
                    6,
                    action == "tot_reasoning",
                )
                bridge.update(_RL_LANE, rl_state, action, reward, rl_next_state)
                total_reward += reward

                # P17: Store findings to DuckDB if available — batched (F265B)
                if store is not None and action_findings:
                    try:
                        rl_finding_buffer: list[CanonicalFinding] = []
                        for finding_data in action_findings:
                            finding_id = hashlib.sha256(
                                f"{query}\x00{str(finding_data)}\x00rl".encode()
                            ).hexdigest()[:16]
                            rl_finding_buffer.append(CanonicalFinding(
                                finding_id=finding_id,
                                query=query,
                                source_type="rl_research",
                                confidence=0.7,
                                ts=time.time(),
                                provenance=("rl", action),
                                payload_text=str(finding_data)[:500],
                            ))
                        if rl_finding_buffer:
                            await store.submit_findings(rl_finding_buffer)
                    except Exception as e:
                        logger.warning(f"[P17] Failed to store RL findings: {e}")

                # P17: Store RL result to memory manager
                if memory_manager is not None and session_id is not None:
                    try:
                        await memory_manager.put(
                            session_id,
                            f"rl_result:{step_count}",
                            {
                                "action": action,
                                "reward": reward,
                                "findings_count": len(action_findings),
                                "timestamp": time.time(),
                            }
                        )
                    except Exception:  # noqa: BLE001
                        pass  # noqa: BLE001  # Fail-soft

                rl_state = rl_next_state
                step_count += 1

                logger.info(
                    f"[P17] RL step {step_count}: action={action}, "
                    f"reward={reward:.3f}, findings={len(action_findings)}"
                )

                # P17: Debounced persist
                await bridge.persist_if_due()

            logger.info(f"[P17] RL loop completed {step_count} steps, total_reward={total_reward:.3f}")

        except Exception as e:
            logger.warning(f"[P17] RL loop failed: {e}")

    # FÁZE P18: Export to Obsidian Markdown and interactive HTML graph
    # Only export on successful pipeline completion (run_error is None)
    if run_error is None:
        try:
            # F271E: honour --export-dir (or GHOST_EXPORT_DIR) for the
            # in-pipeline P18 export. When unset, the singleton falls
            # back to ~/hledac_outputs/ for backwards compatibility.
            import os as _os

            from hledac.universal.export.export_manager import get_export_manager
            from hledac.universal.memory.memory_manager import export_session
            _resolved_export_dir = (
                export_dir
                or _os.environ.get("GHOST_EXPORT_DIR")
            )
            export_mgr = get_export_manager(_resolved_export_dir)

            # Build sources list from pages
            sources = [
                p.url for p in all_page_results
                if hasattr(p, 'url') and p.url
            ][:20]

            # Get findings from memory manager
            session_findings = []
            if memory_manager is not None and session_id is not None:
                try:
                    session_data = await export_session(session_id)
                    session_findings = session_data.get("findings", [])
                except Exception:
                    session_findings = []

            # Export metadata for YAML front matter
            export_metadata = {
                "query": query,
                "sources": sources,
                "tags": ["hledac", "osint", "public-pipeline"],
                "session_id": session_id,
                "stored_findings": str(total_stored),
                "discovered": str(total_discovered),
                "fetched": str(total_fetched),
            }

            # Export markdown report (Obsidian-compatible)
            try:
                md_path = export_mgr.export_markdown(
                    report=generated_report,
                    findings=session_findings,
                    file_path=None,  # Uses timestamp
                    metadata=export_metadata,
                )
                if md_path:
                    logger.info(f"[P18] Exported markdown to {md_path}")
            except Exception as e:
                logger.warning(f"[P18] Markdown export failed: {e}")

            # Export graph HTML (interactive pyvis)
            if graph is not None and graph.node_count() > 0:
                try:
                    html_path = export_mgr.export_graph_html(
                        graph_manager=graph,
                        file_path=None,  # Uses timestamp
                        title=f"Hledac Graph - {query[:50]}",
                    )
                    if html_path:
                        logger.info(f"[P18] Exported graph HTML to {html_path}")
                except Exception as e:
                    logger.warning(f"[P18] Graph HTML export failed: {e}")

        except Exception as e:
            logger.warning(f"[P18] Export failed: {e}")

    # P12: Hypothesis generation and ToT evaluation — POST-STORAGE variant
    # Runs AFTER findings are stored (real persisted evidence), not before fetch.
    # Canonical sprint: gated on store+hermes_engine (not memory_manager alone).
    # M1 8GB: bounded to 5 hypotheses, fail-soft, no ToT in hot path.
    # NOTE: This block executes BEFORE the return so it is always reachable.
    # Downstream: enqueue_hypothesis_pivot (scheduler) consumes first 3 ToT results
    # via asyncio.as_completed — pivot enqueue is fail-soft and bounded.
    tot_solution_count = 0
    if store is not None and hermes_engine is not None and total_stored > 0:
        try:
            from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine
            from hledac.universal.tot_integration import TotIntegrationLayer

            hypo_engine = HypothesisEngine()
            tot_layer = TotIntegrationLayer()
            tot_layer.attach_hypothesis_engine(hypo_engine)  # wire epistemic engine

            # Query real persisted findings as hypothesis input
            recent_findings = await store.async_get_recent_findings(limit=20)
            if not recent_findings:
                logger.debug("[P12] No stored findings — hypothesis layer skipped")
            else:
                # Build context from real findings, not placeholder RAG/graph summary
                hypo_context = {
                    "query": query,
                    "stored_findings_count": total_stored,
                    "findings": [
                        {
                            "finding_id": f.finding_id if hasattr(f, "finding_id") else str(f.get("finding_id", "")),
                            "source_type": f.source_type if hasattr(f, "source_type") else str(f.get("source_type", "")),  # noqa: E501
                            "confidence": f.confidence if hasattr(f, "confidence") else float(f.get("confidence", 0.0)),
                            "provenance": f.provenance if hasattr(f, "provenance") else f.get("provenance", ""),
                        }
                        for f in recent_findings[:20]
                    ],
                }

                # Generate hypotheses from real stored findings
                hypotheses = await hypo_engine.generate_hypotheses_async(
                    context=hypo_context,
                    hermes_engine=hermes_engine
                )

                # Evaluate each hypothesis via ToT if complex — bounded to 5
                # Concurrent evaluation: fire up to 5 tasks, 15s timeout each,
                # first 3 completed results immediately feed pivot enqueue (scheduler caps handle the rest)
                hypotheses_to_eval = hypotheses[:5]
                if hypotheses_to_eval:
                    async def run_tot_with_timeout(hypo: str, timeout_s: float = 15.0) -> str:
                        """Run ToT solve with per-hypothesis timeout. Fail-soft: returns empty string on timeout/error."""  # noqa: E501
                        try:
                            # Primary path: asyncio.timeout ctx (P12 invariant — bounded per-task timeout)
                            async with asyncio.timeout(timeout_s):
                                result = await tot_layer.solve_with_tot(hypo)
                            return result
                        except TimeoutError:
                            logger.debug(f"[P12] ToT timed out after {timeout_s}s for hypothesis: {hypo[:50]}...")
                            return ""
                        except Exception as e:
                            logger.debug(f"[P12] ToT failed for hypothesis: {hypo[:50]}... — {e}")
                            return ""

                    # Fire all 5 ToT tasks concurrently
                    # F320: asyncio.create_task -> safe_create_task (eager_start, loop probe)
                    tasks = [safe_create_task(run_tot_with_timeout(hypo), name=f"tot:hypo_{i}") for i, hypo in enumerate(hypotheses_to_eval)]

                    # Process results as they complete — first 3 successful results
                    # trigger immediate pivot enqueue (scheduler caps naturally limit to 3)
                    tot_finding_buffer: list[CanonicalFinding] = []  # F265B: buffer batch
                    for idx, coro in enumerate(asyncio.as_completed(tasks)):
                        tot_result = await coro
                        if tot_result:
                            tot_solution_count += 1
                            _hypo = hypotheses_to_eval[idx]
                            try:
                                from hledac.universal.knowledge.duckdb_store import CanonicalFinding
                                tot_finding_buffer.append(CanonicalFinding(
                                    finding_id=f"tot_{hashlib.sha256(tot_result.encode()).hexdigest()[:16]}",
                                    query=query,
                                    source_type="tot_synthesis",
                                    confidence=0.7,
                                    ts=time.time(),
                                    provenance=("tot", _hypo[:100]),
                                    payload_text=tot_result[:1000],
                                ))
                            except Exception:  # noqa: BLE001
                                pass  # noqa: BLE001  # Fail-soft

                            # Sprint F193B: Bounded hypothesis → finding feedback loop
                            if enqueue_hypothesis_pivot is not None:
                                try:
                                    pivot_seed = tot_result[:200].split()[:5]
                                    for _i, term in enumerate(pivot_seed):
                                        enqueue_hypothesis_pivot(
                                            ioc_value=term.lower(),
                                            ioc_type="hypothesis",
                                            confidence=0.6,
                                            depth=1,
                                        )
                                except Exception:  # noqa: BLE001
                                    pass  # noqa: BLE001  # Fail-soft
                    # F265B: flush buffered ToT findings after loop completes
                    if tot_finding_buffer and store is not None:
                        await store.submit_findings(tot_finding_buffer)

        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # P12: fail-soft, hypothesis generation is optional

        # Sprint F217E: wire ToT epistemic branches into NonfeedCandidateLedger
        # NOTE: _nonfeed_ledger access removed — async_run_live_public_pipeline is a standalone
        # async function (not a method), so self._nonfeed_ledger was unreachable dead code.

    # Sprint F198C: Document discovery — extract text from PDF/image files
    # Produces CanonicalFinding(source_type="document") findings.
    # Bounded: max 10 files, RAM guard check, fail-soft.
    if store is not None:
        try:
            # Import DocumentExtractor lazily to avoid import-time side effects
            from hledac.universal.multimodal.analyzer import DocumentExtractor

            extractor = DocumentExtractor(governor=None)
            await extractor.initialize()

            # Document discovery looks for file paths in payload_text of existing findings
            # This is a passive enrichment path — documents are discovered via other pipelines
            # For now: no active document discovery in public pipeline
            # (Documents are typically uploaded or discovered via specialized channels)
            await extractor.close()
        except Exception as e:
            logger.debug(f"[F198C] Document discovery failed: {e}")

    # ── Part B: Sprint Synthesis Activation (Sprint HERMES3_WIRING)
    # Gate: len(findings) >= 5 AND HLEDAC_ENABLE_SYNTHESIS=1
    # Cap: max 50 findings for M1 8GB RAM safety
    # Timeout: 90 seconds max
    synthesis_finding = None
    if total_accepted >= 5 and os.environ.get("HLEDAC_ENABLE_SYNTHESIS", "1") == "1":
        try:
            # Check RAM constraint: skip if RSS > 5.5GB
            try:
                import psutil
                rss_gib = psutil.Process().memory_info().rss / (1024**3)
                if rss_gib > 5.5:
                    logger.debug("[SYNTHESIS] Skipped: RSS %.1fGiB > 5.5GiB", rss_gib)
                else:
                    from hledac.universal.brain.model_lifecycle import ModelLifecycle
                    from hledac.universal.brain.synthesis_runner import SynthesisRunner

                    # Build findings list from all_page_results
                    findings_for_synth = []
                    for pr in all_page_results:
                        # P2.3-fix: use accepted_findings (int) not accepted (bool)
                        # PipelinePageResult has no payload_text/title/confidence —
                        # use url + quality_reason as proxy content for synthesis
                        if pr.accepted_findings > 0:
                            finding = {
                                "content": (pr.quality_reason or "")[:500],
                                "title": pr.url or "",
                                "source_type": _SOURCE_TYPE,
                                "confidence": 0.5,
                                "url": pr.url or "",
                            }
                            findings_for_synth.append(finding)

                    if len(findings_for_synth) >= 5:
                        # Limit to 50 findings for M1 RAM safety
                        findings_for_synth = findings_for_synth[:50]

                        # Initialize lifecycle for synthesis
                        lifecycle = ModelLifecycle()
                        runner = SynthesisRunner(lifecycle)
                        # F234: Enable MLX-first context compression for M1 8GB safety
                        runner.set_compression_threshold(4000)

                        # Run synthesis with 90s timeout
                        # NOTE: `asyncio` is module-scoped (line 14). Do NOT add a local
                        # `import asyncio` here — Python scoping would make `asyncio` a
                        # local name throughout `async_run_live_public_pipeline` and
                        # every earlier `asyncio.X` reference (e.g. line 3770's
                        # `asyncio.Semaphore(...)`) would raise UnboundLocalError.
                        # Regression test: tests/test_f_pipeline_asyncio_shadowing.py
                        async with asyncio.timeout(90.0):
                            report = await runner.synthesize_findings(
                                query=query,
                                findings=findings_for_synth,
                                max_findings=10,
                                force_synthesis=False,
                            )

                        # Unload model after synthesis
                        await runner.close()

                        if report is not None:
                            # Add synthesis result as CanonicalFinding
                            import hashlib
                            import time as _time

                            from hledac.universal.knowledge.duckdb_store import CanonicalFinding

                            report_id = f"synth_{hashlib.md5(query.encode()).hexdigest()[:12]}"
                            synthesis_finding = CanonicalFinding(
                                finding_id=report_id,
                                query=query,
                                source_type="llm_synthesis",
                                confidence=getattr(report, 'confidence', 0.7) or 0.7,
                                ts=_time.time(),
                                payload_text=f"Threat actors: {', '.join(getattr(report, 'threat_actors', []) or [])} | {getattr(report, 'threat_summary', '')[:500]}",
                                provenance=("synthesis", getattr(report, 'query', query)[:50]),
                            )
                            logger.info("[SYNTHESIS] Report produced: confidence=%.3f", synthesis_finding.confidence)

                        # Also run DSPy query expansion for next sprint seeds
                        if os.environ.get("HLEDAC_ENABLE_DSPY") == "1":
                            try:
                                from hledac.universal.brain import dspy_service
                                expanded = await dspy_service.expand_query(query)
                                if expanded:
                                    logger.debug("[SYNTHESIS] DSPy expanded %d queries", len(expanded))
                                    # Store expanded queries for next sprint
                                    # (would write to SprintSchedulerConfig.next_seeds or sprint_seeds table)
                            except Exception as e:
                                logger.debug("[SYNTHESIS] DSPy expand_query failed: %s", e)
            except Exception as e:
                logger.debug("[SYNTHESIS] RAM check failed: %s", e)
        except Exception as e:
            logger.warning("[SYNTHESIS] Synthesis failed: %s", e)

    return PipelineRunResult(
        query=query,
        discovered=total_discovered,
        fetched=total_fetched,
        matched_patterns=total_matched,
        accepted_findings=total_accepted,
        stored_findings=total_stored,
        patterns_configured=patterns_cfg,
        pages=tuple(all_page_results),
        error=run_error,
        strong_pages=strong_pages,
        weak_pages_skipped=weak_pages_skipped,
        low_value_fetches=low_value_fetches,
        discovery_strong_content_weak=discovery_strong_content_weak,
        discovery_and_content_strong=discovery_and_content_strong,
        discovery_squandered=discovery_squandered,
        noise_fetch_ratio=noise_fetch_ratio,
        corroboration_vs_burn=corroboration_vs_burn,
        public_next_action=public_next_action,
        public_confidence_note=public_confidence_note,
        public_branch_verdict=public_branch_verdict,
        usable_findings_ratio=usable_findings_ratio,
        discovery_to_findings_efficiency=discovery_to_findings_efficiency,
        quality_mix=quality_mix,
        public_proof_grade=_derived_proof_grade,
        public_value_density=public_value_density,
        top_waste_pattern=top_waste_pattern,
        discovery_false_positive_count=discovery_false_positive_count,
        waste_category_counts=waste_category_counts,
        structural_health_ratio=structural_health_ratio,
        factual_value_density=factual_value_density,
        run_waste_pattern_code=run_waste_pattern_code,
        waste_reason_breakdown=waste_reason_breakdown,
        backend_degraded=_backend_degraded,
        public_discovery_blocker=public_discovery_blocker,
        public_fetch_accessibility_blocker=public_fetch_accessibility_blocker,
        public_discovery_fallback_state=public_discovery_fallback_state,
        dominant_public_failure_mode=dominant_public_failure_mode,
        # Sprint F213B: PUBLIC stage accounting
        public_stage_failure=public_stage_failure,
        public_stage_failure_reason=public_stage_failure_reason,
        public_discovery_attempted=public_discovery_attempted,
        public_discovery_raw_count=public_discovery_raw_count,
        public_discovery_deduped_count=public_discovery_deduped_count,
        public_pages_fetched=public_pages_fetched,
        public_pages_accepted=public_pages_accepted,
        public_pages_rejected=public_pages_rejected,
        public_findings_accepted=public_findings_accepted,
        zero_hit_accessible_fetch_count=zero_hit_accessible_fetch_count,
        zero_hit_quality_reason_counts=zero_hit_quality_reason_counts,
        zero_hit_title_samples=zero_hit_title_samples,
        public_zero_hit_summary=public_zero_hit_summary,
        # Sprint F188B: CT winner-slice telemetry
        ct_subdomain_injected=ct_injected,
        cc_archive_injected=cc_injected,
        # F193B: Academic discovery telemetry
        academic_findings_count=academic_findings_count,
        # P20: PastebinMonitor + GitHubSecretScanner telemetry
        pastebin_findings_count=pastebin_findings_count,
        github_secrets_count=github_secrets_count,
        # Sprint F217C: Deterministic bootstrap telemetry
        public_bootstrap_enabled=public_bootstrap_enabled,
        public_bootstrap_candidates_count=_pub_bootstrap_candidates_count,
        public_bootstrap_fetch_attempted=_pub_bootstrap_fetch_attempted,
        public_bootstrap_fetch_success=_pub_bootstrap_fetch_success,
        public_bootstrap_accepted_findings=_pub_bootstrap_accepted_findings,
        public_bootstrap_errors=_pub_bootstrap_errors,
        # Sprint F229A: Bootstrap ordering telemetry
        public_bootstrap_order=_pub_bootstrap_order,
        public_bootstrap_prevented_discovery_timeout=_pub_bootstrap_prevented_discovery_timeout,
        public_bootstrap_first_fetch_attempted=_pub_bootstrap_first_fetch_attempted,
        # Sprint F220C: Public Provider Rescue telemetry
        public_rescue_candidates_count=_pub_rescue_candidates_count,
        public_rescue_fetch_attempted=_pub_rescue_fetch_attempted,
        public_rescue_fetch_success=_pub_rescue_fetch_success,
        public_rescue_accepted_findings=_pub_rescue_accepted_findings,
        public_rescue_errors=_pub_rescue_errors,
        public_rescue_order=_pub_rescue_order,
        # F1-3: keyword_seed_fallback telemetry
        keyword_seed_fallback_triggered=keyword_seed_fallback_triggered,
        # F207F: PUBLIC Yield telemetry
        public_discovered=public_discovered,
        public_fetch_attempted=public_fetch_attempted,
        public_fetch_skipped=public_fetch_skipped,
        public_fetch_skip_reason=public_fetch_skip_reason,
        public_js_renderer_unavailable=public_js_renderer_unavailable,
        public_xml_or_rss_detected=public_xml_or_rss_detected,
        public_fetch_timeout_count=public_fetch_timeout_count,
        public_fetch_blocked_by_memory=public_fetch_blocked_by_memory,
        # F207I-A: new telemetry
        public_discovery_cache_hit=public_discovery_cache_hit,
        public_discovery_query_count=public_discovery_query_count,
        public_fetch_candidate_count=public_fetch_candidate_count,
        public_fetch_gate=public_fetch_gate,
        public_fetch_attempted_urls_sample=public_fetch_attempted_urls_sample,
        # F207J-C: PUBLIC Acceptance — post-fetch acceptance/rejection telemetry
        public_acceptance_attempted=public_acceptance_attempted,
        public_acceptance_accepted=public_acceptance_accepted,
        public_acceptance_rejected=public_acceptance_rejected,
        public_acceptance_reject_reasons=public_acceptance_reject_reasons,
        public_accepted_url_sample=public_accepted_url_sample,
        public_rejected_url_sample=public_rejected_url_sample,
        # F226B: PUBLIC acceptance uplift diagnostics
        public_build_success_count=public_build_success_count,
        public_build_failure_count=public_build_failure_count,
        public_duplicate_count=public_duplicate_count,
        public_acceptance_ratio=public_acceptance_ratio,
        # F208G-A: PUBLIC Yield Taxonomy — run-level terminal classification
        public_terminal_classified_count=public_terminal_classified_count,
        public_unclassified_count=public_unclassified_count,
        public_terminal_reason_counts=public_terminal_reason_counts,
        public_fetch_success=public_fetch_success,
        public_fetch_failed=public_fetch_failed,
        public_skipped_duplicate=public_skipped_duplicate,
        public_skipped_unsupported_scheme=public_skipped_unsupported_scheme,
        public_skipped_memory_gate=public_skipped_memory_gate,
        public_skipped_quality_gate=public_skipped_quality_gate,
        public_skipped_browser_unavailable=public_skipped_browser_unavailable,
        public_skipped_xml_or_feed=public_skipped_xml_or_feed,
        public_skipped_timeout=public_skipped_timeout,
        public_skipped_fetch_error=public_skipped_fetch_error,
        public_rejected_no_pattern_match=public_rejected_no_pattern_match,
        public_rejected_low_information=public_rejected_low_information,
        public_rejected_duplicate=public_rejected_duplicate,
        public_rejected_storage_rejected=public_rejected_storage_rejected,
        public_skipped_url_sample=public_skipped_url_sample,
        public_rejected_url_samples=public_rejected_url_samples,
        # F231A: PUBLIC Candidate Ledger — stage progression
        public_candidates_discovered=public_candidates_discovered,
        public_candidates_fetch_attempted=public_candidates_fetch_attempted,
        public_candidates_fetch_success=public_candidates_fetch_success,
        public_candidates_parse_success=public_candidates_parse_success,
        public_candidates_pattern_matched=public_candidates_pattern_matched,
        public_candidates_built=public_candidates_built,
        public_candidates_store_attempted=public_candidates_store_attempted,
        public_candidates_stored=public_candidates_stored,
        public_candidates_rejected=public_candidates_rejected,
        public_rejection_summary=public_rejection_summary,
        public_terminal_stage=public_terminal_stage,
        # F232: Provider surface — discovery_empty subtype
        public_discovery_empty_reason=_pub_discovery_empty_reason[0] if _pub_discovery_empty_reason else "",
    )


# Placeholder for discovery (patched in tests)
_ASYNC_DISCOVERY_SEARCH: Any = None

# Sprint F188B: CT winner slice — optional scanner seam (patched in tests)
_CT_SCANNER_GET_SUBDOMAINS: Any = None


def _patch_discovery(search_fn: Any) -> None:
    global _ASYNC_DISCOVERY_SEARCH
    _ASYNC_DISCOVERY_SEARCH = search_fn


def _ensure_discovery_patched() -> None:
    global _ASYNC_DISCOVERY_SEARCH
    if _ASYNC_DISCOVERY_SEARCH is None:
        # Sprint F206AO: env-gated providerless cascade wiring
        # HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1 → use cascade (DDG→Historical→Wayback)
        # Default (0/false/off) → use direct DDG (unchanged behavior)
        _env = os.environ.get("HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY", "1").strip().lower()
        _providerless = _env in ("1", "true", "yes", "on")
        if _providerless:
            from hledac.universal.discovery.cascade import (
                async_search_providerless,
            )
            _ASYNC_DISCOVERY_SEARCH = async_search_providerless
        else:
            from hledac.universal.discovery.duckduckgo_adapter import (
                async_search_public_web,
            )
            _ASYNC_DISCOVERY_SEARCH = async_search_public_web


# Ensure discovery is patched on module import
_ensure_discovery_patched()


def _patch_ct_scanner(get_subdomains_fn: Any) -> None:
    """Patch in a CT scanner get_subdomains(domain, async_session) -> list[str]."""
    global _CT_SCANNER_GET_SUBDOMAINS
    _CT_SCANNER_GET_SUBDOMAINS = get_subdomains_fn


def _ensure_ct_scanner_patched() -> None:
    """Lazily patch the CT scanner from network.ct_log_scanner."""
    global _CT_SCANNER_GET_SUBDOMAINS
    if _CT_SCANNER_GET_SUBDOMAINS is not None:
        return
    try:
        from hledac.universal.network.ct_log_scanner import _CTLogScanner

        _scanner = _CTLogScanner(allow_external=True, cache_ttl_days=30)

        async def _get_subdomains(
            domain: str, async_session: Any = None
        ) -> list[str]:
            return await _scanner.get_subdomains(domain, async_session=async_session)

        _CT_SCANNER_GET_SUBDOMAINS = _get_subdomains
    except Exception:
        # Fail-soft: CT scanner unavailable
        _CT_SCANNER_GET_SUBDOMAINS = None

