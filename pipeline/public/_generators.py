"""URL Generator functions — extracted from live_public_pipeline.py.

F360-REFACTOR: These helper functions have been extracted to reduce
god function complexity in the main pipeline module.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import urllib.parse
from typing import TYPE_CHECKING, Any

from hledac.universal.discovery.duckduckgo_adapter import DiscoveryHit

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

_MAX_BOOTSTRAP_URLS: int = 5
_BOOTSTRAP_DEFAULT_URLS: list[str] = [
    "",
    "/www.",
    "/.well-known/security.txt",
    "/robots.txt",
    "/sitemap.xml",
]
_MAX_SEED_CONTEXT_BOOTSTRAP: int = 10
_PUBLIC_BOOTSTRAP_SEARCH_ENGINES: tuple[str, ...] = (
    "duckduckgo",
    "yahoo",
    "bing",
    "startpage",
)
_MAX_KEYWORD_BOOTSTRAP_URLS: int = 10
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
]

# ----------------------------------------------------------------------
# Query Classification
# ----------------------------------------------------------------------


def _strip_query_prefix(q: str) -> str:
    """Strip site:, domain:, url:, asn:, ip:, vpn:, tor: prefixes."""
    for prefix in ("site:", "domain:", "url:", "asn:", "ip:", "vpn:", "tor:"):
        if q.lower().startswith(prefix):
            return q[len(prefix) :].strip()
    return q


def _strip_prefix(q: str) -> str:
    """Strip site:, domain:, url: prefixes."""
    for prefix in ("site:", "domain:", "url:"):
        if q.lower().startswith(prefix):
            return q[len(prefix) :]
    return q


def _extract_host_from_url(q: str) -> str | None:
    """Extract host from URL using urllib."""
    try:
        parsed = urllib.parse.urlparse(q)
        return parsed.netloc or parsed.path.split("/")[0]
    except Exception:
        return None


def _normalize_domain(q: str) -> str | None:
    """Normalize domain string: strip ports, www, wildcards."""
    q = q.rstrip("/")
    if "/" in q and "://" in q:
        if (host := _extract_host_from_url(q)):
            q = host
    if ":" in q:
        q = q.rsplit(":", 1)[0]
    if q.lower().startswith("www."):
        q = q[4:]
    if q.startswith("*."):
        q = q[2:]
    return q


def _is_valid_domain(q: str) -> bool:
    """Validate domain format."""
    if not q or "." not in q:
        return False
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", q):
        return False
    if not re.match(r"^[a-zA-Z0-9.\-]+$", q):
        return False
    if len(q.rsplit(".", 1)[-1]) < 2:
        return False
    return True


def _extract_domain_from_query(query: str) -> str | None:
    """Extract domain from OSINT query string."""
    if not query:
        return None
    candidates = [query]
    if (" " in query or "\t" in query) and (first := query.strip().split()[0]) and (first != query):
        candidates.append(first)
    for candidate in candidates:
        q = _normalize_domain(_strip_prefix(candidate))
        if q and _is_valid_domain(q):
            return q.lower()
    return None


def _build_domain_candidates(query: str) -> list[str]:
    """Extract domain-like candidates from query."""
    q = query.strip()
    if not q or len(q) > 253:
        return []
    candidates = [q]
    for token in q.split():
        if "." in token and token != q:
            candidates.append(token)
    return candidates


# ----------------------------------------------------------------------
# Threat Query Detection
# ----------------------------------------------------------------------


def _check_ip_cve(q: str) -> bool:
    """Check if query is IP address or CVE pattern."""
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}(?:\/\d{1,2})?$", q):
        return True
    if re.match(r"^CVE-\d{4}-\d{4,}$", q, re.IGNORECASE):
        return True
    return False


def _check_threat_patterns(q: str, first_token: str) -> bool:
    """Check ransomware/malware/threat actor patterns."""
    THREAT_PAT = re.compile(
        r"^(?:lockbit|conti|revil|clop|darkside|blackcat|alphv|ransomware|"
        r"apt[_\s]?\d+|apt[_-]\w+|sidecopy|callback|triangle|temp|"
        r"wanna[_\s]?cry|wannacry|petya|notpetya|badrabbit|emotet|trickbot|"
        r"cobalt[_\s]?strike|koadic|metasploit|fin7|carbanak|finacrypt|"
        r"prodaft|labyrinth|zCrypt|poisonivy|plugx|gh0st|gain|wellmess|whispergate|hermetic)$",
        re.IGNORECASE,
    )
    if THREAT_PAT.match(q):
        return True
    EXTENDED_PAT = re.compile(
        r"^(?:meterpreter|sandworm|lazarus|log4shell|finacrypt|prodaft|labyrinth|"
        r"zcrypt|poisonivy|plugx|gh0st|gain|wellmess|whispergate|hermetic|"
        r"sidecopy|callback|triangle|temp|sofacy)$",
        re.IGNORECASE,
    )
    for token in re.split(r"[\s\-_]+", q):
        if len(token) >= 4 and THREAT_PAT.match(token):
            return True
        if len(token) >= 3 and EXTENDED_PAT.match(token):
            return True
    if first_token and THREAT_PAT.match(first_token):
        return True
    return False


def _check_generic_keywords(q: str, first_token: str) -> bool:
    """Check generic threat/OSINT keywords."""
    THREAT_KW = re.compile(
        r"^(?:ransomware|malware|threat[_-]?actor|cobalt[_\s]?strike|"
        r"breach|exploit|0day|zero[_\s]?day|vulnerability|phishing|"
        r"spam|botnet|trojan|rootkit|keylogger|Ransomware|Malware|ThreatActor|CVE|APT)$",
        re.IGNORECASE,
    )
    OSINT_KW = re.compile(
        r"^(?:osint|osint infrastructure|infrastructure|telemetry|leak|"
        r"dark[_\s]?web|exposure|credential|breach|darkweb|onion|leakdb|"
        r"intel|threat|hunting|recon|scanning|fingerprint|iot|ics|scada)$",
        re.IGNORECASE,
    )
    if THREAT_KW.match(q) or OSINT_KW.match(q) or (first_token and OSINT_KW.match(first_token)):
        return True
    return False


def _check_multi_word_patterns(q: str) -> bool:
    """Check multi-word OSINT/threat compound patterns."""
    MULTI_PAT = re.compile(
        r"(?:ransomware\s+(?:threat|intelligence|leak|attack|group|operation)|"
        r"threat\s+(?:intelligence|actor|actor\s+group|intel)|"
        r"malware\s+(?:analysis|sample|family|variant)|"
        r"data\s+(?:breach|leak|exposure|dump)|dark\s+web|deep\s+web|"
        r"surface\s+web|credential\s+(?:dump|leak|breach|stuffing)|"
        r"osint\s+(?:reconnaissance|recon|automation)|"
        r"vulnerability\s+(?:scan|scanner|assessment|intelligence)|"
        r"threat\s+hunting|incident\s+response|infosec|"
        r"cybersecurity\s+intelligence|iosint|geoint)$",
        re.IGNORECASE,
    )
    return bool(MULTI_PAT.search(q))


def _is_threat_query(query: str) -> bool:
    """Detect if query is a non-domain threat/malware/ransomware/entity query."""
    if not query or not query.strip():
        return False
    q, first_token = _strip_query_prefix(query.strip()), query.split()[0] if query else ""
    return _check_ip_cve(q) or _check_threat_patterns(q, first_token) or _check_generic_keywords(q, first_token) or _check_multi_word_patterns(q)


def _query_looks_like_domain(query: str) -> bool:
    """Detect if query is a domain name suitable for CT subdomain lookup."""
    CT_QUERY_RE = re.compile(r"^(?:\*\.)?[a-zA-Z0-9][a-zA-Z0-9.*-]*\.[a-zA-Z]{2,}$")
    return any(CT_QUERY_RE.match(c) for c in _build_domain_candidates(query))


def _query_looks_like_domain_for_cc(query: str) -> bool:
    """Detect if query is a domain name suitable for CommonCrawl CDX lookup."""
    CC_QUERY_RE = re.compile(r"^(?:\*\.)?[a-zA-Z0-9][a-zA-Z0-9.*-]*\.[a-zA-Z]{2,}$|^(?:site|domain):")
    return any(CC_QUERY_RE.match(c) for c in _build_domain_candidates(query))


def _extract_base_domain(domain: str) -> str:
    """Extract base domain from domain string."""
    if domain.startswith("*."):
        domain = domain[2:]
    parts = domain.split(".")
    if len(parts) >= 3:
        return ".".join(parts[-2:])
    return domain


# ----------------------------------------------------------------------
# Noise Filtering
# ----------------------------------------------------------------------


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
)


def _is_shopping_noise_url(url: str, is_threat_query: bool) -> tuple[bool, str]:
    """Detect if a URL is shopping/e-commerce noise."""
    if not url:
        return (False, "public_relevance_pass")
    parsed = urllib.parse.urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.lower()
    for allowed_domain in _CTI_NEWS_ALLOWED_DOMAINS:
        if netloc.endswith(allowed_domain) or netloc == allowed_domain:
            return (False, "public_relevance_pass")
    for blocked_domain in _SHOPPING_NOISE_DOMAINS:
        if netloc.endswith(blocked_domain) or netloc == blocked_domain:
            return (True, "public_noise_shopping")
    if is_threat_query:
        for blocked_path in _SHOPPING_NOISE_PATHS_STRICT:
            if blocked_path in path:
                return (True, "public_noise_unrelated_marketplace")
    return (False, "public_relevance_pass")


def _filter_public_noise(hits: list | tuple, is_threat_query: bool) -> tuple[list, list[tuple[str, str]]]:
    """Filter shopping/e-commerce noise from public discovery hits."""
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
    return (filtered, rejected)


# ----------------------------------------------------------------------
# URL Generators
# ----------------------------------------------------------------------


def generate_bootstrap_urls(query: str, max_urls: int = _MAX_BOOTSTRAP_URLS) -> list[str]:
    """Generate deterministic bootstrap URLs for domain/URL queries."""
    if not query or max_urls < 1:
        return []
    clean_query = query.strip()
    for prefix in ("site:", "domain:", "url:"):
        if clean_query.lower().startswith(prefix):
            clean_query = clean_query[len(prefix) :].strip()
            break
    domain = _extract_domain_from_query(clean_query)
    if not domain:
        return []
    paths = _BOOTSTRAP_DEFAULT_URLS[:max_urls]
    urls: list[str] = [
        f"https://www.{domain}" if path == "/www." else f"https://{domain}{path}" if path else f"https://{domain}"
        for path in paths
    ]
    return urls


def generate_rescue_urls(query: str, max_urls: int = 8) -> list[DiscoveryHit]:
    """Generate lightweight rescue DiscoveryHits for non-domain threat queries."""
    if not query or max_urls < 1:
        return []
    if not _is_threat_query(query):
        return []
    hits: list[DiscoveryHit] = []
    for name, base_url in _RESGUE_SOURCE_CANDIDATES[:max_urls]:
        url = f"{base_url}{urllib.parse.quote(query.strip())}"
        hits.append(
            DiscoveryHit(
                query=query,
                title=f"Rescue: {name}",
                url=url,
                snippet=f"Rescue search via {name}: {query}",
                score=0.7,
                reason="rescue_candidate",
                rank=-1,
                source="rescue",
                retrieved_ts=0.0,
            )
        )
    return hits


def generate_seed_context_bootstrap_urls(seed_context: Any, max_candidates: int = _MAX_SEED_CONTEXT_BOOTSTRAP) -> list[str]:
    """Generate deterministic bootstrap URLs from NonfeedSeedContext."""
    if not seed_context or max_candidates < 1:
        return []
    urls: list[str] = []
    _has_domains = bool(getattr(seed_context, "domains", ()))
    _has_urls = bool(getattr(seed_context, "urls", ()))
    _both_sources = _has_domains and _has_urls
    if _both_sources:
        _max_per_source = (max_candidates + 1) // 2
    else:
        _max_per_source = max_candidates
    if _has_domains:
        for domain in list(getattr(seed_context, "domains", ()))[:_max_per_source]:
            if len(urls) >= max_candidates:
                break
            if not domain or "." not in domain:
                continue
            try:
                domain = domain.lower().strip()
                if not domain.startswith(("http://", "https://")):
                    urls.append(f"https://{domain}")
                else:
                    urls.append(domain)
            except Exception:
                continue
    if _has_urls:
        for url in list(getattr(seed_context, "urls", ()))[:_max_per_source]:
            if len(urls) >= max_candidates:
                break
            if not url:
                continue
            try:
                url_str = str(url).strip()
                if not url_str.startswith(("http://", "https://")):
                    continue
                urls.append(url_str)
            except Exception:
                continue
    return urls[:max_candidates]


async def generate_keyword_bootstrap_urls(query: str, max_urls: int = _MAX_KEYWORD_BOOTSTRAP_URLS) -> list[DiscoveryHit]:
    """Keyword-based search engine bootstrap — falls back through multiple engines."""
    from hledac.universal.discovery.duckduckgo_adapter import search_multi_engine

    if not query or not query.strip():
        return []
    for engine in _PUBLIC_BOOTSTRAP_SEARCH_ENGINES:
        try:
            raw_results = await search_multi_engine(query, max_results=max_urls)
            if not raw_results:
                continue
            hits: list[DiscoveryHit] = []
            for i, item in enumerate(raw_results[:max_urls]):
                url = item.get("url", "") if isinstance(item, dict) else getattr(item, "url", "")
                title = item.get("title", "") if isinstance(item, dict) else getattr(item, "title", "")
                snippet = item.get("snippet", "") if isinstance(item, dict) else getattr(item, "snippet", "")
                if not url:
                    continue
                hits.append(
                    DiscoveryHit(
                        query=query,
                        title=title or f"{engine.capitalize()} result {i + 1}",
                        url=url,
                        snippet=snippet or f"Keyword bootstrap via {engine}: {query}",
                        score=0.75,
                        reason=f"keyword_bootstrap_{engine}",
                        rank=i,
                        source=engine,
                        retrieved_ts=time.time(),
                    )
                )
            if hits:
                return hits
        except Exception:
            continue
    return []
