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
import json
import logging
import os
import re
import sys
import time
import urllib.parse

logger = logging.getLogger(__name__)
from typing import TYPE_CHECKING, Any

import msgspec

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

# F206AB: discovery error taxonomy helper
from hledac.universal.discovery.duckduckgo_adapter import (  # noqa: E402
    DiscoveryHit,
    classify_discovery_error,
)

# F206AC: fetch error taxonomy helper
from hledac.universal.fetching.public_fetcher import (  # noqa: E402
    classify_fetch_error,
)

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
_PRE_FETCH_TEXT_MIN_CHARS: int = 150
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
    # Threat intelligence aggregators — open-access only (no login/API key required)
    ("ThreatFox", "https://threatfox.abuse.ch/browse.php?search="),
    # Ransomware-specific trackers — open-access
    ("Ransomware Tracker", "https://ransomwaretracker.xyz/"),
    ("ID Ransomware", "https://id-ransomware.malwarehunterteam.com/"),
    # General CTI/news — open-access
    ("BleepingComputer", "https://www.bleepingcomputer.com/search/?search="),
    ("The Hacker News", "https://thehackernews.com/search?q="),
    ("Krebs on Security", "https://krebsonsecurity.com/?s="),
    ("CISA KEV", "https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search="),
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

# CTI/news domains that are always allowed (override noise filter for threat queries)
_CTI_NEWS_ALLOWED_DOMAINS: tuple[str, ...] = (
    "cisa.gov",
    "krebsonsecurity.com",
    "bleepingcomputer.com",
    "thehackernews.com",
    "abuse.ch",
    "threatfox.abuse.ch",
    "ransomwaretracker.xyz",
    "id-ransomware.malwarehunterteam.com",
    "malwarehunterteam.com",
    "cyberscoop.com",
    "darkreading.com",
    "threatpost.com",
    "therecord.media",
    "securityweek.com",
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

    # For threat queries, also check path patterns
    if is_threat_query:
        for blocked_path in _SHOPPING_NOISE_PATHS:
            if blocked_path in path:
                return True, "public_noise_unrelated_marketplace"

    return False, "public_relevance_pass"


def _filter_public_noise(
    hits: list, is_threat_query: bool
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
    IP_PAT = _re.compile(
        r"^\d{1,3}(?:\.\d{1,3}){3}(?:\/\d{1,2})?$|^"
        r"[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){2,7}(?::\d{1,3})?(?:\/\d{1,2})?$"
    )
    if IP_PAT.match(q):
        return True

    # CVE pattern
    CVE_PAT = _re.compile(r"^CVE-\d{4}-\d{4,}$", _re.IGNORECASE)
    if CVE_PAT.match(q):
        return True

    # Ransomware/malware/threat actor name patterns
    THREAT_PAT = _re.compile(
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
    _EXTENDED_PAT = _re.compile(
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
    THREAT_KW_PAT = _re.compile(
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

    return False


def generate_rescue_urls(query: str, max_urls: int = 5) -> list[DiscoveryHit]:
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
    urls: list[str] = []
    for path in paths:
        if path == "/www.":
            # https://www.domain/
            urls.append(f"https://www.{domain}")
        elif path:
            # https://domain/<path>
            urls.append(f"https://{domain}{path}")
        else:
            # https://domain/
            urls.append(f"https://{domain}")

    return urls


# Sprint F223C: Bounded seed_context bootstrap for nonfeed_diagnostic profile
_MAX_SEED_CONTEXT_BOOTSTRAP: int = 10  # hard cap


def generate_seed_context_bootstrap_urls(seed_context: Any, max_candidates: int = _MAX_SEED_CONTEXT_BOOTSTRAP) -> list[str]:
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
from dataclasses import dataclass, field


@dataclass(frozen=True)
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
    result_error = getattr(discovery_result, "error", None) or (discovery_result.get("error") if isinstance(discovery_result, dict) else None)
    error_str = str(result_error) if result_error else ""

    # provider_status_debug may be attached as attribute or in dict
    psd = getattr(discovery_result, "provider_status_debug", None)
    if psd is None and isinstance(discovery_result, dict):
        psd = discovery_result.get("provider_status_debug")

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
        if hasattr(discovery_result, "hits") and discovery_result.hits:
            # derive from first hit query
            first_hit = discovery_result.hits[0]
            q = getattr(first_hit, "query", "") or ""
            if q:
                variants.append(q)

    # Provider-level errors from DiscoveryBatchResult fields
    error_type = getattr(discovery_result, "error_type", None) or ""
    getattr(discovery_result, "provider_name", None) or ""

    if error_str:
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
    public_bootstrap_prevented_discovery_timeout: bool = False  # True when bootstrap produced candidates but discovery would have returned zero
    public_bootstrap_first_fetch_attempted: bool = False  # True when bootstrap hits were added to hits before fetch
    # Sprint F220C: Public Provider Rescue telemetry
    public_rescue_candidates_count: int = 0  # rescue URLs generated from threat query
    public_rescue_fetch_attempted: int = 0  # rescue URLs sent to fetch
    public_rescue_fetch_success: int = 0  # rescue URLs that fetched successfully
    public_rescue_accepted_findings: int = 0  # findings accepted from rescue hits
    public_rescue_errors: int = 0  # rescue-specific errors
    public_rescue_order: str = "disabled"  # "rescue_fallback" | "disabled"
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
    # discovery → fetch_attempted → fetch_success → parse_success → pattern_matched → built → store_attempted → stored/rejected
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
    public_terminal_stage: str = ""  # discovery_empty | fetch_zero | parse_zero | match_zero | build_zero | store_zero | accepted
    # F232: Provider surface telemetry — discovery provider selection and outcome truth
    public_provider_selected: list[str] = field(default_factory=list)  # providers with selected=True
    public_provider_skipped: list[dict] = field(default_factory=list)  # [{provider, reason}] with selected=False
    public_provider_stub: list[str] = field(default_factory=list)  # providers in ADVISORY_STUB state
    public_provider_errors: list[dict] = field(default_factory=list)  # [{provider, error, error_type}] provider-level errors
    public_query_variants: list[str] = field(default_factory=list)  # query variants emitted to providers
    public_provider_timeout_count: int = 0  # providers that timed out
    public_provider_import_error_count: int = 0  # providers that failed to import/initialize
    # F232: Refined discovery_empty subtypes — explicit reason when discovery returns zero
    public_discovery_empty_reason: str = ""  # no_provider_selected | provider_unavailable | provider_timeout | provider_returned_zero | query_builder_empty


# -----------------------------------------------------------------------------
# UMA helpers
# -----------------------------------------------------------------------------


def _get_uma_state() -> tuple[str, bool]:
    """
    Read UMA status via 8AB surface.
    Returns (state_str, io_only_hint).
    Raises: propagates any exception from resource_governor.

    Sprint 8AK: Uses SSOT labels from resource_governor — no localUMA interpretation.
    """
    # Sprint 8AB surface — lazy import to avoid module-level side effects
    from hledac.universal.core.resource_governor import (
        evaluate_uma_state,
        sample_uma_status,
    )

    status = sample_uma_status()
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
    try:
        from hledac_rust_extensions import content_hash_hex as _xxh
        return _xxh(key)
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
    """
    # Build metadata prefix bounded to MAX_METADATA_PREPEND_CHARS
    meta_parts: list[str] = []
    remaining_meta = MAX_METADATA_PREPEND_CHARS

    if title:
        title_trunc = title[:remaining_meta]
        meta_parts.append(title_trunc)
        remaining_meta -= len(title_trunc)

    if snippet and remaining_meta > 20:
        snippet_trunc = snippet[:remaining_meta]
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

    # --- Pre-filter: skip pages with almost no content BEFORE signal scoring ---
    # Sprint F163B: apply text-length gate first — avoids wasting compute on dead pages
    if len(extracted_text) < _PRE_FETCH_TEXT_MIN_CHARS:
        return "SKIP_WEAK:very_low_text"

    # --- Signalless gate: very low word-level entropy = spam/placeholder ---
    # Sprint F163B: detect "lorem ipsum" / repetitive filler / template noise
    # This is orthogonal to text length — catches thin-but-long pages
    words = extracted_text.split()
    if len(words) >= 10:
        unique_ratio = len(frozenset(w.lower() for w in words)) / len(words)
        if unique_ratio < 0.25:
            return "SKIP_WEAK:low_entropy"

    # --- Title query-term density --------------------------------
    title_words = frozenset(hit_title.lower().split())
    title_query_hits = len(query_terms & title_words)
    title_has_query = title_query_hits > 0

    # --- Snippet query-term density -----------------------------
    snippet_words = frozenset(hit_snippet.lower().split())
    snippet_query_hits = len(query_terms & snippet_words)
    snippet_has_query = snippet_query_hits > 0

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

    if not page_text or not page_text.strip():
        return ()

    # Bounded payload from title + snippet + first chars of body + status
    payload_parts: list[str] = []
    if hit_title:
        payload_parts.append(f"title: {hit_title[:200]}")
    if hit_snippet:
        payload_parts.append(f"snippet: {hit_snippet[:300]}")
    # Include first 500 chars of body as surface evidence
    body_preview = page_text[:500].strip()
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
            confidence=0.55,  # Lower than pattern-matched (0.8) — corroborating signal
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

    loop = asyncio.get_running_loop()

    # Extract context in thread to avoid blocking event loop
    context: str = await loop.run_in_executor(
        None, _pattern_context, page_text, hit_start, hit_end
    )

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
    """
    Single-page fetch + extract + match + store.

    F226B: PUBLIC acceptance uplift telemetry (local accumulators for this page).
    These are initialized here because _fetch_and_process_page runs as a parallel
    task via asyncio.create_task — each task needs its own counters, not shared ones.
    """
    _pub_build_success_count: int = 0
    _pub_build_failure_count: int = 0
    _pub_duplicate_count: int = 0
    _pub_bootstrap_accepted_findings: int = 0  # F230B: bootstrap-sourced accepted findings
    _pub_dup_found: bool = False  # F226B: duplicate signal — initialized before conditional branches
    # --- Adaptive budget tier ----------------------------------------
    has_signal = (
        (discovery_score is not None and discovery_score >= _DISCOVERY_SIGNAL_SCORE_THRESHOLD)
        or (discovery_reason is not None and discovery_reason.strip() != "")
    )
    strong_signal = discovery_score is not None and discovery_score >= 0.7

    # Sprint F150J Fix A: wire SKIP tier — was dead code before
    low_discovery = (
        discovery_score is not None
        and discovery_score < _DISCOVERY_SKIP_THRESHOLD
        and not strong_signal
    )
    if low_discovery:
        budget_mult = _FETCH_BUDGET_SKIP  # 0.0 → true skip
    elif discovery_score is not None and discovery_score >= 0.85:
        budget_mult = _FETCH_BUDGET_STRONG
    elif strong_signal or has_signal:
        budget_mult = _FETCH_BUDGET_NORMAL
    else:
        budget_mult = _FETCH_BUDGET_WEAK

    effective_timeout = fetch_timeout_s * budget_mult
    # Don't call fetch at all for SKIP tier (budget_mult == 0)
    skip_fetch = budget_mult <= 0

    async with semaphore:
        # ---- Fetch -----------------------------------------------------------
        if skip_fetch:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=False, matched_patterns=0, stored_findings=0,
                quality_reason="SKIP_WEAK:weak_discovery",
                discovery_signal=has_signal,
                discovery_score=discovery_score,
                error="skipped:weak_discovery",
                extracted_text_len=0,
            )
            ppr = PipelinePageResult(
                url=hit_url,
                fetched=False,
                matched_patterns=0,
                accepted_findings=0,
                stored_findings=0,
                error="skipped:weak_discovery",
                quality_reason="SKIP_WEAK:weak_discovery",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=None,
                redirected=False,
                redirect_target=None,
                fetch_blocked_reason="quality_skip",  # F207F
                rejection_reason="no_fetch_result",  # F207J-C: not fetched due to quality gate
                terminal_reason="skipped_quality_gate",  # F208G-A: canonical terminal classification
            )
            return ppr

        # F208G-A: Validate URL scheme before attempting fetch
        from urllib.parse import urlparse
        _parsed_url = urlparse(hit_url)
        if not _parsed_url.scheme or _parsed_url.scheme.lower() not in ("http", "https"):
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=False, matched_patterns=0, stored_findings=0,
                quality_reason=None, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=f"url_unsupported_scheme:{_parsed_url.scheme}",
                extracted_text_len=0,
            )
            ppr = PipelinePageResult(
                url=hit_url,
                fetched=False,
                matched_patterns=0,
                accepted_findings=0,
                stored_findings=0,
                error=f"url_unsupported_scheme:{_parsed_url.scheme}",
                quality_reason=None,
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=None,
                redirected=False,
                redirect_target=None,
                fetch_blocked_reason="unsupported_scheme",
                rejection_reason="fetch_error",
                terminal_reason="skipped_unsupported_scheme",  # F208G-A
            )
            return ppr

        # Sprint F193B: Policy-driven fetch — JS/DoH/stealth driven by signal, not dormant defaults
        policy = _compute_fetch_policy(hit_url, discovery_score, discovery_reason, strong_signal)

        try:
            result = await asyncio.wait_for(
                _ASYNC_FETCH_PUBLIC_TEXT(
                    hit_url, effective_timeout, fetch_max_bytes,
                    use_stealth=policy.use_stealth,
                    use_js=policy.use_js,
                    use_doh=policy.use_doh,
                ),
                timeout=effective_timeout + 5.0,
            )
        except TimeoutError:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=False, matched_patterns=0, stored_findings=0,
                quality_reason=None, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=f"fetch_timeout_after_{effective_timeout:.1f}s",
                extracted_text_len=0,
            )
            ppr = PipelinePageResult(
                url=hit_url, fetched=False, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                error=f"fetch_timeout_after_{effective_timeout:.1f}s",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage="connection",
                redirected=False,
                redirect_target=None,
                fetch_blocked_reason="timeout",  # F207F
                rejection_reason="fetch_error",  # F207J-C: fetch failed due to timeout
                terminal_reason="skipped_timeout",  # F208G-A
            )
            # [F207F] ppr._fetch_result removed — PipelinePageResult is frozen msgspec.Struct;
            # FetchResult is not needed in verdict telemetry; use p.error and p.failure_stage directly
            return ppr
        except asyncio.CancelledError:
            raise  # [I6] propagate, never swallow
        except Exception as exc:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=False, matched_patterns=0, stored_findings=0,
                quality_reason=None, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=f"fetch_exception:{type(exc).__name__}:{exc}",
                extracted_text_len=0,
            )
            ppr = PipelinePageResult(
                url=hit_url, fetched=False, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                error=f"fetch_exception:{type(exc).__name__}:{exc}",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage="connection",
                redirected=False,
                redirect_target=None,
                fetch_blocked_reason="exception",  # F207F
                rejection_reason="fetch_error",  # F207J-C: fetch failed due to exception
                terminal_reason="skipped_fetch_error",  # F208G-A
            )
            # [F207F] ppr._fetch_result removed — PipelinePageResult is frozen msgspec.Struct;
            # FetchResult is not needed in verdict telemetry; use p.error and p.failure_stage directly
            return ppr

        # Unpack fetch result (FetchResult frozen struct)
        # Sprint F170D: also read failure_stage for accessibility truth
        # Sprint F171A: also read redirected + redirect_target for redirect-induced non-content detection
        # F207F: also read js_renderer_skipped_reason for PUBLIC yield telemetry
        fetched_text: str | None
        fetched_failure_stage: str | None = None
        fetched_redirected: bool = False
        fetched_redirect_target: str | None = None
        fetched_js_skip_reason: str | None = None
        if hasattr(result, "text"):
            fetched_text = result.text
            fetched_failure_stage = getattr(result, "failure_stage", None)
            fetched_redirected = getattr(result, "redirected", False)
            fetched_redirect_target = getattr(result, "redirect_target", None)
            fetched_js_skip_reason = getattr(result, "js_renderer_skipped_reason", None)
        else:
            fetched_text = None

        if not fetched_text:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=True, matched_patterns=0, stored_findings=0,
                quality_reason=None, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error="fetch_text_none_or_empty",
                extracted_text_len=0,
            )
            ppr = PipelinePageResult(
                url=hit_url, fetched=True, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                error="fetch_text_none_or_empty",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=None,
                redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
                js_renderer_skipped_reason=fetched_js_skip_reason,  # F207F
                rejection_reason="empty_text",  # F207J-C: fetched but text extraction returned nothing
                terminal_reason="rejected_empty_text",  # F208G-A
            )
            return ppr

        # ---- Extract ---------------------------------------------------------
        loop = asyncio.get_running_loop()
        try:
            extracted_text: str = await loop.run_in_executor(
                None, _html_to_text, fetched_text
            )
        except Exception as exc:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=True, matched_patterns=0, stored_findings=0,
                quality_reason=None, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=f"html_extract_failed:{exc}",
                extracted_text_len=0,
            )
            ppr = PipelinePageResult(
                url=hit_url, fetched=True, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                error=f"html_extract_failed:{exc}",
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=fetched_failure_stage,
                redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
                js_renderer_skipped_reason=fetched_js_skip_reason,  # F207F
                rejection_reason="extraction_failed",  # F207J-C: HTML text extraction failed
                terminal_reason="rejected_extraction_failed",  # F208G-A
            )
            return ppr

        # Hard cap
        if len(extracted_text) > MAX_EXTRACTED_TEXT_CHARS:
            extracted_text = extracted_text[:MAX_EXTRACTED_TEXT_CHARS]

        # Build quality signal from discovery metadata + text metrics
        # Sprint F150I: query-aware page selection, bounded signal scoring
        quality_reason = _score_page_quality(
            hit_url=hit_url,
            hit_title=hit_title or "",
            hit_snippet=hit_snippet or "",
            hit_rank=hit_rank,
            query=query,
            extracted_text=extracted_text,
            discovery_score=discovery_score,
            discovery_reason=discovery_reason,
        )

        # Skip very-low-quality pages early — preserve fetch budget
        if quality_reason.startswith("SKIP_WEAK"):
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=True, matched_patterns=0, stored_findings=0,
                quality_reason=quality_reason, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=None,
                extracted_text_len=len(extracted_text),
            )
            # F208G-A: js_renderer_skip_reason takes precedence over low_information
            # when page quality is weak (browser_unavailable, xml_or_feed_url are
            # operational skips that explain the weak quality)
            _tr_skipped: str | None = None
            if fetched_js_skip_reason == "browser_unavailable":
                _tr_skipped = "skipped_browser_unavailable"
            elif fetched_js_skip_reason in ("xml_or_feed_url", "xml_recovered"):
                _tr_skipped = "skipped_xml_or_feed"
            _terminal_reason = _tr_skipped if _tr_skipped else "rejected_low_information"
            _rejection_reason = _tr_skipped if _tr_skipped else "low_information"
            ppr = PipelinePageResult(
                url=hit_url, fetched=True, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                error=None, quality_reason=quality_reason,
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=fetched_failure_stage,
                redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
                js_renderer_skipped_reason=fetched_js_skip_reason,  # F207F
                rejection_reason=_rejection_reason,  # F207J-C
                terminal_reason=_terminal_reason,  # F208G-A
            )
            return ppr

        # Sprint F150I: enrich extracted text with discovery metadata
        # This gives pattern scanner better signal (title/snippet hints present)
        scan_text = _enrich_text_with_metadata(
            hit_title or "", hit_snippet or "", extracted_text
        )

        # Free raw HTML reference early
        del fetched_text

        # ---- Pattern scan ----------------------------------------------------
        # 8X surface — run in thread executor; use enriched text
        try:
            loop = asyncio.get_running_loop()
            hits: list = await loop.run_in_executor(
                None, _SYNC_MATCH_TEXT, scan_text
            )
        except Exception:
            hits = []
        if hits is None:
            hits = []

        matched_count = len(hits)

        # FÁZE P9: Stream graph entities per-page (pattern scan results)
        if graph is not None and hits:
            _add_pattern_hits_to_graph(hits, graph)
        if matched_count == 0:
            usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                fetched=True, matched_patterns=0, stored_findings=0,
                quality_reason=quality_reason, discovery_signal=has_signal,
                discovery_score=discovery_score,
                error=None,
                extracted_text_len=len(extracted_text),
            )
            # F226B: Try public-surface finding from content-only page (bootstrap, security.txt, etc.)
            # Only attempt when page was successfully fetched with extractable text.
            # SKIP_WEAK pages (quality_reason.startswith("SKIP_WEAK")) are excluded — quality gate already decided.
            _public_findings: list = []
            if (
                extracted_text
                and quality_reason is not None
                and not quality_reason.startswith("SKIP_WEAK")
            ):
                try:
                    _pub_tuple = await _build_public_finding(
                        query=query,
                        url=hit_url,
                        page_text=extracted_text,
                        hit_title=hit_title or "",
                        hit_snippet=hit_snippet or "",
                        discovery_score=discovery_score,
                        discovery_reason=discovery_reason,
                        http_status_code=getattr(result, "status_code", 0) or 0,
                    )
                    if _pub_tuple:
                        _public_findings.append(_pub_tuple[0])
                except Exception:
                    _public_findings = []

            # F226B: If public_surface finding was built, store it (bypassing pattern match requirement)
            _pub_accepted = 0
            _pub_stored = 0
            if _public_findings and store is not None:
                try:
                    _pub_results = await store.async_ingest_findings_batch(_public_findings)
                    for _sr in _pub_results:
                        if isinstance(_sr, dict):
                            if _sr.get("accepted"):
                                _pub_accepted += 1
                            if _sr.get("lmdb_success"):
                                _pub_stored += 1
                        else:
                            if getattr(_sr, "accepted", False):
                                _pub_accepted += 1
                            if getattr(_sr, "lmdb_success", False):
                                _pub_stored += 1
                except Exception:
                    pass  # fail-soft

            # F226B: Track public finding build outcomes and detect duplicates
            if _pub_accepted > 0:
                _pub_build_success_count += 1
                # F230B: Bootstrap-sourced accepted findings tracked via _pub_bootstrap_accepted_findings.
                # Bootstrap hits have source="bootstrap" on the DiscoveryHit; we track them
                # by URL pattern since the hit object is not available in this scope.
                # Bootstrap URLs are deterministic and start with known prefixes.
                pass
            elif _public_findings or (extracted_text and quality_reason is not None and not quality_reason.startswith("SKIP_WEAK")):
                # Check if the finding was rejected as duplicate (stored but not accepted)
                if _public_findings and _pub_stored > 0 and _pub_accepted == 0:
                    # Duplicate: finding_id already existed in storage from this run
                    _pub_duplicate_count += 1
                    _pub_dup_found = True
                else:
                    _pub_dup_found = False
                _pub_build_failure_count += 1
            else:
                _pub_dup_found = False

            # F226B: If public finding was accepted, report it; otherwise fall through to rejection
            if _pub_accepted > 0:
                usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
                    fetched=True, matched_patterns=0, stored_findings=_pub_stored,
                    quality_reason=quality_reason, discovery_signal=has_signal,
                    discovery_score=discovery_score,
                    error=None,
                    extracted_text_len=len(extracted_text),
                )
                ppr = PipelinePageResult(
                    url=hit_url, fetched=True, matched_patterns=0,
                    accepted_findings=_pub_accepted, stored_findings=_pub_stored,
                    quality_reason=quality_reason,
                    discovery_score=discovery_score,
                    discovery_reason=discovery_reason,
                    discovery_signal=has_signal,
                    usable_signal=usable_signal,
                    value_tier=value_tier,
                    resolution_reason=resolution_reason,
                    discovery_false_positive=discovery_false_positive,
                    waste_category=waste_category,
                    structural_quality=structural_quality,
                    failure_stage=fetched_failure_stage,
                    redirected=fetched_redirected,
                    redirect_target=fetched_redirect_target,
                    js_renderer_skipped_reason=fetched_js_skip_reason,
                    rejection_reason=None,  # accepted via public_surface
                    terminal_reason=None,  # accepted
                    public_surface_dup=_pub_dup_found,  # F226B: duplicate signal if finding_id already existed
                )
                return ppr

            # Fall through to standard rejection when no public finding was produced
            _tr_skipped: str | None = None
            if fetched_js_skip_reason == "browser_unavailable":
                _tr_skipped = "skipped_browser_unavailable"
            elif fetched_js_skip_reason in ("xml_or_feed_url", "xml_recovered"):
                _tr_skipped = "skipped_xml_or_feed"
            _terminal_reason = _tr_skipped if _tr_skipped else "rejected_no_pattern_match"
            _rejection_reason = _tr_skipped if _tr_skipped else "no_pattern_match"
            ppr = PipelinePageResult(
                url=hit_url, fetched=True, matched_patterns=0,
                accepted_findings=0, stored_findings=0,
                quality_reason=quality_reason,
                discovery_score=discovery_score,
                discovery_reason=discovery_reason,
                discovery_signal=has_signal,
                usable_signal=usable_signal,
                value_tier=value_tier,
                resolution_reason=resolution_reason,
                discovery_false_positive=discovery_false_positive,
                waste_category=waste_category,
                structural_quality=structural_quality,
                failure_stage=fetched_failure_stage,
                redirected=fetched_redirected,
                redirect_target=fetched_redirect_target,
                js_renderer_skipped_reason=fetched_js_skip_reason,  # F207F
                rejection_reason=_rejection_reason,  # F207J-C
                terminal_reason=_terminal_reason,  # F208G-A
            )
            return ppr

        # ---- Per-page dedup: (label, pattern, value) exact dedup -----------
        # F182D: Order changed from (value,label,pattern) to match feed pipeline (label,pattern,value)
        seen: set[tuple[str, str, str]] = set()
        unique_findings: list = []

        for hit in hits:
            key = (hit.label or "", hit.pattern, hit.value)
            if key in seen:
                continue
            seen.add(key)

            findings_tuple = await _extract_live_public_findings_from_page(
                query=query,
                url=hit_url,
                hit_label=hit.label if hit.label else "",
                hit_pattern=hit.pattern,
                hit_value=hit.value,
                hit_start=hit.start,
                hit_end=hit.end,
                page_text=extracted_text,
                discovery_score=discovery_score,
            )
            unique_findings.append(findings_tuple[0])

        # F180B FIX: accepted_count = quality-gated count (before storage)
        # stored_count = actual storage success (lmdb_success)
        # These are SEPARATE — accepted does NOT imply stored (DuckDB may fail)
        accepted_count = 0
        stored_count = 0
        storage_error: bool = False  # F208G-A: track storage exceptions for terminal_reason
        quality_gate_rejected: bool = False  # F208G-A: track quality-gate rejections

        # ---- Storage ---------------------------------------------------------
        if store is not None and unique_findings:
            try:
                # DuckDBShadowStore quality-gated ingest surface (8W + 8S)
                store_results = await store.async_ingest_findings_batch(unique_findings)
                # F180B FIX: accepted_count from quality gate, stored_count from lmdb_success.
                # accepted_count = number that passed quality gate (may not all reach storage)
                # stored_count = number that actually reached LMDB WAL successfully
                for sr in store_results:
                    if isinstance(sr, dict):
                        # FindingQualityDecision: has "accepted" key
                        if sr.get("accepted"):
                            accepted_count += 1
                        # ActivationResult: has "lmdb_success" key
                        if sr.get("lmdb_success"):
                            stored_count += 1
                    else:
                        # msgspec struct
                        if getattr(sr, "accepted", False):
                            accepted_count += 1
                        if getattr(sr, "lmdb_success", False):
                            stored_count += 1
                # F208G-A: quality gate rejection — storage succeeded but no findings accepted
                if unique_findings and accepted_count == 0:
                    quality_gate_rejected = True

                # F208G-A: storage error — DuckDB/LMDB rejected write (stored_count=0
                # means lmdb_success=False despite no exception = storage layer rejection,
                # distinct from quality gate which is about accepted=False)
                if stored_count == 0 and unique_findings:
                    storage_error = True

                # P11: Write to memory manager after DuckDB storage succeeds
                # This enables RAG context for future queries
                if memory_manager is not None and session_id is not None:
                    for finding in unique_findings:
                        try:
                            finding_id = getattr(finding, "finding_id", None) or str(hash(hit_url))
                            memory_entry = {
                                "finding_id": finding_id,
                                "query": query,
                                "url": hit_url,
                                "timestamp": time.time(),
                                "payload_text": getattr(finding, "payload_text", ""),
                                "source_type": getattr(finding, "source_type", ""),
                                "confidence": getattr(finding, "confidence", 0.0),
                                "provenance": list(getattr(finding, "provenance", ())),
                            }
                            await memory_manager.put(
                                session_id,
                                f"finding:{finding_id}",
                                memory_entry
                            )
                        except Exception:
                            # Fail-soft: memory write errors don't fail the page
                            pass

            except asyncio.CancelledError:
                raise  # [I6]
            except Exception:
                # Fail-soft: storage error does not fail the page
                # accepted_count/stored_count already set to 0 (pre-loop init) on error
                storage_error = True  # F208G-A: mark storage failure for terminal_reason

            # F197C: Per-finding embeddings — stored AFTER DuckDB quality gate.
            # Embed only accepted findings (quality-gated payload_text).
            # Fail-soft: embedding failure never breaks the pipeline.
            # Uses model_manager.embedding_lifecycle() for M1 memory discipline.
            if vector_store is not None and unique_findings and accepted_count > 0:
                try:
                    from hledac.universal.brain.model_manager import get_model_manager
                    from hledac.universal.embedding_pipeline import generate_embeddings_async

                    # Build list of (finding_id, payload_text) for accepted findings only
                    # Sprint F206P: temporal signal observation (advisory only, fail-soft)
                    try:
                        from hledac.universal.layers import get_temporal_signal_layer
                        from hledac.universal.layers.temporal_signal_layer import event_from_finding_like
                        temporal_layer = get_temporal_signal_layer()
                    except Exception:
                        temporal_layer = None

                    accepted_ids: list[str] = []
                    accepted_texts: list[str] = []
                    for finding, sr in zip(unique_findings, store_results, strict=False):
                        is_accepted = False
                        if isinstance(sr, dict):
                            is_accepted = bool(sr.get("accepted"))
                        else:
                            is_accepted = bool(getattr(sr, "accepted", False))
                        if is_accepted:
                            # Sprint F206P: observe temporal event (advisory, fail-soft)
                            if temporal_layer is not None:
                                try:
                                    te = event_from_finding_like(finding)
                                    if te:
                                        temporal_layer.observe(te)
                                except asyncio.CancelledError:
                                    raise
                                except Exception:
                                    pass  # fail-soft: temporal scoring is advisory only
                            pt = getattr(finding, "payload_text", "") or ""
                            if len(pt) > 20:
                                fid = getattr(finding, "finding_id", None)
                                if fid:
                                    accepted_ids.append(fid)
                                    accepted_texts.append(pt)

                    if accepted_texts:
                        model_manager = get_model_manager()
                        async with model_manager.embedding_lifecycle():
                            embeddings = await generate_embeddings_async(accepted_texts, keep_loaded=True)
                        if embeddings is not None and len(embeddings) > 0:
                            import numpy as np
                            vec_array = np.asarray(embeddings, dtype=np.float32)
                            vector_store.add_vectors(
                                accepted_ids,
                                vec_array,
                                index_type="finding"
                            )
                            logger.debug(
                                f"[F197C] Stored {len(accepted_ids)} per-finding embeddings "
                                f"for {hit_url[:50]}"
                            )
                except Exception:
                    # Fail-soft: per-finding embedding errors never break the page
                    pass

            # P13: Store page text embedding in vector store
            # Only for html/text content, not binary
            if vector_store is not None and extracted_text and len(extracted_text) > 50:
                try:
                    from hledac.universal.brain.model_manager import get_model_manager
                    from hledac.universal.embedding_pipeline import generate_embeddings_async

                    # Use extracted_text (not enriched scan_text) for embedding
                    # P16: Wrap with embedding_lifecycle() for proper M1 memory management
                    model_manager = get_model_manager()
                    async with model_manager.embedding_lifecycle():
                        embeddings = await generate_embeddings_async([extracted_text], keep_loaded=True)
                    if embeddings is not None and len(embeddings) > 0:
                        # Use URL-based ID for vector lookup
                        finding_id_for_vec = _make_finding_id(
                            query=query,
                            url=hit_url,
                            label="page_text",
                            pattern="embedding",
                            value=extracted_text[:100]
                        )
                        # P16: Ensure embeddings are float32 numpy array with correct shape
                        import numpy as np
                        vec = np.asarray(embeddings[0], dtype=np.float32)
                        vector_store.add_vectors(
                            [finding_id_for_vec],
                            vec.reshape(1, -1),
                            index_type="text"
                        )
                        logger.debug(f"[P16] Stored embedding for {hit_url[:50]}")
                except Exception:
                    # Fail-soft: vector storage errors don't fail the page
                    pass

        usable_signal, value_tier, resolution_reason, discovery_false_positive, waste_category, structural_quality = _compute_page_usable_fields(
            fetched=True, matched_patterns=matched_count,
            stored_findings=stored_count,
            quality_reason=quality_reason,
            discovery_signal=has_signal,
            discovery_score=discovery_score,
            error=None,
            extracted_text_len=len(extracted_text),
        )
        # F208G-A: terminal_reason = None if accepted, else rejected_storage_rejected
        _terminal: str | None
        _rej_reason: str | None
        # F208G-A: js_renderer_skipped_reason takes precedence as terminal_reason
        # when set (browser_unavailable/xml_or_feed means renderer was unavailable
        # during fetch — this operational skip explains the page state regardless
        # of whether patterns were matched and accepted)
        if fetched_js_skip_reason == "browser_unavailable":
            _terminal = "skipped_browser_unavailable"
            _rej_reason = "browser_unavailable"
        elif fetched_js_skip_reason in ("xml_or_feed_url", "xml_recovered"):
            _terminal = "skipped_xml_or_feed"
            _rej_reason = "xml_or_feed"
        elif accepted_count > 0 and not storage_error:
            _terminal = None
            _rej_reason = None
        elif storage_error:
            _terminal = "rejected_storage_rejected"
            _rej_reason = "storage_rejected"
        elif quality_gate_rejected:
            # storage succeeded but quality gate rejected all findings
            _terminal = "rejected_quality_gate"
            _rej_reason = "quality_gate_rejected"
        else:
            # accepted_count == 0 but no storage error — fallback (shouldn't reach here)
            _terminal = "rejected_storage_rejected"
            _rej_reason = "storage_rejected"
        ppr = PipelinePageResult(
            url=hit_url,
            fetched=True,
            matched_patterns=matched_count,
            accepted_findings=accepted_count,
            stored_findings=stored_count,
            quality_reason=quality_reason,
            discovery_score=discovery_score,
            discovery_reason=discovery_reason,
            discovery_signal=has_signal,
            usable_signal=usable_signal,
            value_tier=value_tier,
            resolution_reason=resolution_reason,
            discovery_false_positive=discovery_false_positive,
            waste_category=waste_category,
            structural_quality=structural_quality,
            failure_stage=fetched_failure_stage,
            redirected=fetched_redirected,
            redirect_target=fetched_redirect_target,
            js_renderer_skipped_reason=fetched_js_skip_reason,  # F207F
            rejection_reason=_rej_reason,  # F208G-A
            terminal_reason=_terminal,  # F208G-A: None=accepted, else rejected
        )
        return ppr


# -----------------------------------------------------------------------------
# Placeholder fetch/match imports (patched in tests; real code uses 8AD/8X)
# -----------------------------------------------------------------------------

# Legacy module-level globals — backward compatibility only.
# DO NOT add new _ASYNC_* / _SYNC_* patch globals.
# Preferred test hook: explicit keyword arguments to async_run_live_public_pipeline.
_ASYNC_FETCH_PUBLIC_TEXT: Any = None  # legacy: patched by tests
_SYNC_MATCH_TEXT: Any = None  # legacy: patched by tests
_PATCHED_BY_ENSURE: bool = False  # guard: once _ensure_patched() runs, don't re-overwrite


def _patch_fetcher_and_matcher(
    fetch_fn: Any, match_fn: Any
) -> None:
    global _ASYNC_FETCH_PUBLIC_TEXT, _SYNC_MATCH_TEXT
    _ASYNC_FETCH_PUBLIC_TEXT = fetch_fn
    _SYNC_MATCH_TEXT = match_fn


def _ensure_patched() -> None:
    """Ensure runtime fetch/matcher are patched from 8AD/8X modules.

    Idempotent: once called (by production code), never re-runs.
    Tests patch _ASYNC_FETCH_PUBLIC_TEXT and _SYNC_MATCH_TEXT BEFORE calling
    the pipeline; this guard preserves those patches by skipping the real import
    once any code (tests or production) has triggered this function.
    """
    global _ASYNC_FETCH_PUBLIC_TEXT, _SYNC_MATCH_TEXT, _PATCHED_BY_ENSURE
    if _PATCHED_BY_ENSURE:
        return
    _PATCHED_BY_ENSURE = True
    if _ASYNC_FETCH_PUBLIC_TEXT is None:
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text
        _ASYNC_FETCH_PUBLIC_TEXT = async_fetch_public_text
    if _SYNC_MATCH_TEXT is None:
        from hledac.universal.patterns.pattern_matcher import match_text
        _SYNC_MATCH_TEXT = match_text


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
    try:
        from hledac_rust_extensions import content_hash_hex as _xxh
        return _xxh(key)
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
    expert_ids: list[str] = []
    try:
        from hledac.universal.brain.moe_router import create_moe_router
        router = await create_moe_router()
        if router is not None:
            expert_ids = await router.route(query, context_items)
            logger.info(f"[P16] MoE experts: {expert_ids} for query: {query[:50]}")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"[P16] MoE routing failed: {e}")
        expert_ids = []

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

            # Store using existing async API
            await store.async_ingest_findings_batch([report_finding])
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
                key_iocs: tuple[str, ...] = ()
                key_entities: tuple[str, ...] = ()

                ioc_json_block = re.search(r'<IOC_JSON>\s*(\{.*?\})\s*</IOC_JSON>', report_text, re.DOTALL)
                if ioc_json_block:
                    try:
                        ioc_data = json.loads(ioc_json_block.group(1))
                        key_iocs = tuple(ioc_data.get("iocs", [])[:20])
                        key_entities = tuple(ioc_data.get("entities", [])[:20])
                    except (json.JSONDecodeError, KeyError) as _:
                        pass  # Fall back to NER extraction

                if not key_iocs and not key_entities:
                    # Fallback: use NER extraction
                    ioc_results = extract_iocs_from_text(report_text)
                    key_iocs = tuple(
                        r["value"] for r in ioc_results
                        if r.get("value") and len(r["value"]) > 3
                    )[:20]
                    key_entities = tuple(
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
                    payload_text=json.dumps(hermes_output.to_dict(), ensure_ascii=False)[:4096],
                )
                await store.async_ingest_findings_batch([hermes_finding])
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


# =============================================================================
# FÁZE P9: GraphManager integration
# =============================================================================


def _add_pattern_hits_to_graph(hits: list, graph: Any) -> None:
    """
    FÁZE P9: Stream pattern hits into GraphManager.

    Called per-page after pattern scan — lightweight, no heavy ops.
    Max 1000 entries per page enforced (M1 8GB safe).
    """
    if graph is None or not hits:
        return
    try:
        seen: set[tuple[str, str]] = set()
        for hit in hits[:1000]:  # Hard cap per page
            entity_type = hit.label or "unknown"
            value = hit.value
            key = (entity_type, value)
            if key in seen:
                continue
            seen.add(key)
            graph.add_entity(entity_type, value)
    except Exception:
        pass  # Fail-soft: graph errors don't fail pipeline


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
    except Exception:
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

    for onion_url in onion_urls:
        try:
            result = await async_fetch_public_text(
                onion_url,
                timeout_s=30.0,
                max_bytes=200_000,
            )
            if result.error or result.text is None:
                failure_count += 1
                continue

            content = result.text
            pf_id = hashlib.sha256(
                f"{query}\x00{onion_url}\x00onion_discovery".encode()
            ).hexdigest()[:16]

            findings.append(CanonicalFinding(
                finding_id=pf_id,
                query=query,
                source_type="onion_discovery",
                confidence=0.55,
                ts=ts_now,
                provenance=("onion_discovery", onion_url),
                payload_text=content[:500] if content else None,
            ))

        except Exception as e:
            logger.debug(f"[F193A] Onion fetch {onion_url}: {e}")
            failure_count += 1
            if failure_count >= _ONION_CIRCUIT_FAIL_LIMIT:
                _onion_circuit_record_failure()
                break

    if failure_count >= _ONION_CIRCUIT_FAIL_LIMIT:
        _onion_circuit_record_failure()

    if findings and store is not None:
        try:
            await store.async_ingest_findings_batch(findings)
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
    fetch_concurrency: int = 5,
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
    except Exception:
        pass

    # DI F226: explicit dependency injection — resolve all seams before use
    # fetch_fn / match_fn override globals; otherwise _ensure_patched() sets them
    if fetch_fn is not None:
        global _ASYNC_FETCH_PUBLIC_TEXT
        _ASYNC_FETCH_PUBLIC_TEXT = fetch_fn
    if match_fn is not None:
        global _SYNC_MATCH_TEXT
        _SYNC_MATCH_TEXT = match_fn
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
    rag_context: list[dict] = []
    if memory_manager is not None:
        try:
            history = await memory_manager.get_session_history(session_id, limit=50)
            # Extract payload_text from past findings for RAG context
            for entry in history:
                value = entry.get("value", {})
                if isinstance(value, dict):
                    payload = value.get("payload_text", "")
                    if payload:
                        rag_context.append({
                            "query": value.get("query", ""),
                            "payload": payload[:500],  # Truncate for context
                            "timestamp": value.get("timestamp", 0),
                        })
        except Exception:
            rag_context = []  # Fail-soft: memory errors don't fail pipeline

    # ---- Engines -----------------------------------------------------------
    # Sprint F214: Refactored into focused engine classes for maintainability.
    # Each engine is a dataclass with async run() method that encapsulates a
    # logical phase of the pipeline. Backward compatible — same inputs/outputs.

    @dataclass
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
                        rescue_hits = generate_rescue_urls(self.query, max_urls=5)
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
                if _pub_bootstrap_candidates_count == 0 and _pub_rescue_candidates_count == 0 and self.seed_context is not None:
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
                discovery_result = await _ASYNC_DISCOVERY_SEARCH(self.query, self.max_results)
                discovery_elapsed_s = time.monotonic() - _discovery_start

                cache_hit = getattr(discovery_result, "cache_hit", False) if hasattr(discovery_result, "cache_hit") else False
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
                    if len(_disc_hits) == 0:
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

                err_val = discovery_result.get("error") if isinstance(discovery_result, dict) else getattr(discovery_result, "error", None)
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
            if not hits:
                discovery_telemetry = {
                    'discovery_result': None,
                    'public_stage_failure': 'discovery_empty',
                    'public_stage_failure_reason': discovery_error if discovery_error else 'no URLs returned from discovery',
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
                    'public_discovery_empty_reason': _pub_discovery_empty_reason[0] if _pub_discovery_empty_reason else '',
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

            # P16: Academic discovery integration
            academic_findings_count = 0
            if self.store is not None:
                try:
                    from hledac.universal.intelligence.academic_discovery import search_academic_all
                    academic_semaphore = asyncio.Semaphore(3)
                    async def limited_academic_search():
                        async with academic_semaphore:
                            return await search_academic_all(self.query, max_results=10, rate_limit=50)
                    academic_results = await limited_academic_search()
                    all_papers = []
                    for source, papers in academic_results.items():
                        for paper in papers:
                            all_papers.append(paper)
                    if all_papers:
                        academic_findings = []
                        for paper in all_papers[:20]:
                            paper_id = hashlib.sha256(
                                f"{self.query}\x00{paper.get('link', '')}\x00academic".encode()
                            ).hexdigest()[:16]
                            provenance = ("academic", source, paper.get('title', ''))
                            academic_finding = CanonicalFinding(
                                finding_id=paper_id,
                                query=self.query,
                                source_type="academic_discovery",
                                confidence=0.7,
                                ts=time.time(),
                                provenance=provenance,
                                payload_text=f"{paper.get('title', '')}\n{paper.get('abstract', '')}".strip()[:500],
                            )
                            academic_findings.append(academic_finding)
                        if academic_findings:
                            await self.store.async_ingest_findings_batch(academic_findings)
                            academic_findings_count = len(academic_findings)
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"[P16] Academic discovery failed: {e}")

            # Sprint F188B: CT winner-slice injection
            original_hit_count = len(hits)
            hits = await _inject_ct_subdomain_hits(hits, self.query)
            ct_injected = len(hits) - original_hit_count

            # F192E: CommonCrawl CDX domain injection
            original_hit_count = len(hits)
            hits = await _inject_commoncrawl_hits(hits, self.query)
            cc_injected = len(hits) - original_hit_count

            # Sprint F193A: Onion discovery
            onion_findings_count = 0
            if self.store is not None:
                try:
                    onion_findings_count = await _inject_onion_hits(hits, self.query, self.store)
                except Exception as e:
                    logger.debug(f"[F193A] Onion discovery failed: {e}")

            # P20: PastebinMonitor + GitHubSecretScanner
            pastebin_findings_count = 0
            github_secrets_count = 0
            if self.store is not None:
                try:
                    import re as _re
                    _DOMAIN_ORG_RE = _re.compile(
                        r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}"
                    )
                    _match = _DOMAIN_ORG_RE.search(self.query)
                    if _match:
                        target = _match.group()
                        logger.info(f"[P20] PastebinMonitor targeting: {target}")
                        from hledac.universal.intelligence.pastebin_monitor import run as pastebin_run
                        paste_findings = await pastebin_run(target)
                        if paste_findings:
                            p20_findings = []
                            for pf in paste_findings:
                                pf_id = hashlib.sha256(
                                    f"{self.query}\x00{pf.uri}\x00pastebin".encode()
                                ).hexdigest()[:16]
                                masked = pf.masked_secrets()
                                p20_findings.append(CanonicalFinding(
                                    finding_id=pf_id,
                                    query=self.query,
                                    source_type="pastebin_monitor",
                                    confidence=0.6,
                                    ts=time.time(),
                                    provenance=("pastebin", pf.source, target),
                                    payload_text=(
                                        f"uri={pf.uri}\n"
                                        f"emails={pf.emails}\n"
                                        f"ips={pf.ip_addresses}\n"
                                        f"masked_secrets={masked}\n"
                                        f"snippet={pf.context_snippet[:300]}"
                                    ),
                                ))
                            if p20_findings:
                                await self.store.async_ingest_findings_batch(p20_findings)
                                pastebin_findings_count = len(p20_findings)

                        org_candidate = _match.group().rsplit(".", 1)[0]
                        from hledac.universal.intelligence.github_secret_scanner import (
                            search_org_secrets,
                        )
                        gh_findings: list[CanonicalFinding] = []
                        if org_candidate:
                            try:
                                gh_results = await search_org_secrets(org_candidate)
                            except Exception:
                                gh_results = []
                            for gf in gh_results:
                                gf_id = hashlib.sha256(
                                    f"{self.query}\x00{gf.file_path}\x00{gf.pattern}\x00github".encode()
                                ).hexdigest()[:16]
                                gh_findings.append(CanonicalFinding(
                                    finding_id=gf_id,
                                    query=self.query,
                                    source_type="github_secret_scanner",
                                    confidence=0.55,
                                    ts=time.time(),
                                    provenance=("github", gf.pattern, org_candidate),
                                    payload_text=(
                                        f"pattern={gf.pattern}\n"
                                        f"file={gf.file_path}\n"
                                        f"line={gf.line}\n"
                                        f"context={gf.context[:300]}"
                                    ),
                                ))
                        if gh_findings:
                            await self.store.async_ingest_findings_batch(gh_findings)
                            github_secrets_count = len(gh_findings)
                except Exception as e:
                    import logging as _logging
                    _logging.getLogger(__name__).warning(f"[P20] Pastebin/GitHub scan failed: {e}")

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
        uma_state, _ = _get_uma_state()
    except Exception:
        pass  # Defensive: proceed with ok state

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
    _pub_bootstrap_prevented_discovery_timeout = discovery_telemetry.get('public_bootstrap_prevented_discovery_timeout', False)
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

    # F207J-C: PUBLIC Acceptance — local accumulator for rejection reasons
    public_acceptance_reject_reasons: dict[str, int] = {}

    # ---- Fetch batch ---------------------------------------------------------
    # Per-call semaphore, no global batch timeout
    # F208G-A: URL-level dedup — skip duplicate URLs before creating fetch tasks
    # Sprint F213B: track discovery stage counts before dedup
    # F221H: Public Discovery Relevance / Shopping Noise Filter
    is_threat = _is_threat_query(query)
    hits, noise_rejections = _filter_public_noise(hits, is_threat)
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

        task = asyncio.create_task(
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

    # asyncio.gather preserves order; _check_gathered enforces [I6][I7][I8]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # _check_gathered propagates CancelledError [I6] and BaseException [I7]
    from hledac.universal.utils.async_helpers import _check_gathered
    ok_results, error_results = _check_gathered(raw_results)

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
        public_stage_failure_reason = f"discovery returned {public_discovery_deduped_count} URLs but no findings were accepted"

    # F231A: PUBLIC Candidate Ledger — derive from page results
    # Tracks stage progression: discovery → fetch_attempted → fetch_success → parse_success → pattern_matched → built → store_attempted → stored/rejected
    # fetch_attempted = pages that passed quality gate and entered page processing
    public_candidates_discovered = total_discovered
    public_candidates_fetch_attempted = public_pages_fetched  # pages that entered fetch/parse
    public_candidates_fetch_success = sum(
        1 for p in all_page_results if p.fetched and p.error and not p.error.startswith(("fetch_text_none_or_empty", "html_extract_failed"))
    )
    public_candidates_parse_success = sum(
        1 for p in all_page_results if p.fetched and p.error not in ("fetch_text_none_or_empty", "html_extract_failed", None)
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
    except Exception:
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
        round(sum(1 for p in fetched_pages if getattr(p, "structural_quality", "") == "healthy") / max(fetched_count, 1), 3)
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
    _FALLBACK_STATE_MAP: dict[str, str] = {
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
                "transport_fallback_reason": getattr(pfr, "transport_fallback_reason", None) if pfr is not None else None,
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
    _BLOCKER_BY_BACKEND_ERROR: dict[str, str] = {
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
            export_path = os.path.expanduser("~/new_hledac_graph.html")
            graph.export_html(export_path)
        except Exception:
            pass  # Fail-soft: graph export errors don't fail pipeline

    # P17: Run ResearchLoop if --loop flag was set
    # Supports either rl_steps count (--rl-steps N) or time limit (default 5 min)
    if run_loop and hermes_engine is not None:
        try:
            from hledac.universal.knowledge.duckdb_store import CanonicalFinding
            from hledac.universal.loops.research_loop import ResearchLoop, ResearchResult

            # P17: Default RL loop time limit (5 minutes)
            _RL_LOOP_TIME_LIMIT_S = 300.0

            research_loop = ResearchLoop(
                hypothesis_engine=hermes_engine,
                graph=graph,
                duckdb_store=store,
                memory_manager=memory_manager,
            )

            # P17: Run either N steps or until time limit
            rl_start_time = time.monotonic()
            step_count = 0

            while True:
                # Check step limit first
                if rl_steps > 0 and step_count >= rl_steps:
                    break

                # Check time limit
                elapsed = time.monotonic() - rl_start_time
                if elapsed >= _RL_LOOP_TIME_LIMIT_S:
                    logger.info(f"[P17] RL loop time limit reached ({elapsed:.1f}s)")
                    break

                # Run one RL iteration
                loop_result: ResearchResult = await research_loop.run_once(query)

                # P17: Store findings to DuckDB if available
                if store is not None and loop_result.findings:
                    try:
                        for finding_data in loop_result.findings:
                            finding_id = hashlib.sha256(
                                f"{query}\x00{str(finding_data)}\x00rl".encode()
                            ).hexdigest()[:16]
                            rl_finding = CanonicalFinding(
                                finding_id=finding_id,
                                query=query,
                                source_type="rl_research",
                                confidence=0.7,
                                ts=time.time(),
                                provenance=("rl", loop_result.action),
                                payload_text=str(finding_data)[:500],
                            )
                            await store.async_ingest_findings_batch([rl_finding])
                    except Exception as e:
                        logger.warning(f"[P17] Failed to store RL finding: {e}")

                # P17: Store RL result to memory manager
                if memory_manager is not None and session_id is not None:
                    try:
                        await memory_manager.put(
                            session_id,
                            f"rl_result:{step_count}",
                            {
                                "action": loop_result.action,
                                "reward": loop_result.reward,
                                "findings_count": len(loop_result.findings),
                                "timestamp": time.time(),
                            }
                        )
                    except Exception:
                        pass  # Fail-soft

                step_count += 1

                logger.info(
                    f"[P17] RL step {step_count}: action={loop_result.action}, "
                    f"reward={loop_result.reward:.3f}, findings={len(loop_result.findings)}"
                )

            logger.info(f"[P17] ResearchLoop completed {step_count} RL steps")

        except Exception as e:
            logger.warning(f"[P17] ResearchLoop.run_once failed: {e}")

    # FÁZE P18: Export to Obsidian Markdown and interactive HTML graph
    # Only export on successful pipeline completion (run_error is None)
    if run_error is None:
        try:
            from hledac.universal.export.export_manager import get_export_manager
            from hledac.universal.memory.memory_manager import export_session

            export_mgr = get_export_manager()

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
    tot_solution_count = 0
    if store is not None and hermes_engine is not None and total_stored > 0:
        try:
            from hledac.universal.brain.hypothesis_engine import HypothesisEngine
            from hledac.universal.tot_integration import TotIntegrationLayer

            hypo_engine = HypothesisEngine()
            tot_layer = TotIntegrationLayer()

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
                            "source_type": f.source_type if hasattr(f, "source_type") else str(f.get("source_type", "")),
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
                        """Run ToT solve with per-hypothesis timeout. Fail-soft: returns empty string on timeout/error."""
                        try:
                            return await asyncio.wait_for(tot_layer.solve_with_tot(hypo), timeout=timeout_s)
                        except TimeoutError:
                            logger.debug(f"[P12] ToT timed out after {timeout_s}s for hypothesis: {hypo[:50]}...")
                            return ""
                        except Exception as e:
                            logger.debug(f"[P12] ToT failed for hypothesis: {hypo[:50]}... — {e}")
                            return ""

                    # Fire all 5 ToT tasks concurrently
                    tasks = [run_tot_with_timeout(hypo) for hypo in hypotheses_to_eval]

                    # Process results as they complete — first 3 successful results
                    # trigger immediate pivot enqueue (scheduler caps naturally limit to 3)
                    for coro in asyncio.as_completed(tasks):
                        tot_result = await coro
                        if tot_result:
                            tot_solution_count += 1
                            try:
                                from hledac.universal.knowledge.duckdb_store import CanonicalFinding
                                tot_finding = CanonicalFinding(
                                    finding_id=f"tot_{hashlib.sha256(tot_result.encode()).hexdigest()[:16]}",
                                    query=query,
                                    source_type="tot_synthesis",
                                    confidence=0.7,
                                    ts=time.time(),
                                    provenance=("tot", hypo[:100]),
                                    payload_text=tot_result[:1000],
                                )
                                await store.async_ingest_findings_batch([tot_finding])
                            except Exception:
                                pass  # Fail-soft

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
                                except Exception:
                                    pass  # Fail-soft

        except Exception:
            pass  # P12: fail-soft, hypothesis generation is optional

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
        _env = os.environ.get("HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY", "0").strip().lower()
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
