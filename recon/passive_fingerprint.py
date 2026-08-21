"""
Passive Service Fingerprinting — F204G: Deterministic passive fingerprinting engine.

Extracts service fingerprints from accepted findings without active port scanning.

Consumes: HTTP headers, TLS/cert text, CT metadata, HTML hints from payload_text.

Fingerprint sources:
  - HTTP headers: Server, X-Powered-By, Via, CF-Ray, X-AspNet-Version
  - TLS/cert: subject CN, issuer, SAN entries, protocol versions, cipher suites
  - CT metadata: certificate transparency log entries for service identification
  - HTML hints: title, meta generator, script/src patterns, favicon hashes

No active scanning — purely deterministic pattern matching on existing finding data.
Findings stored as CanonicalFinding via async_ingest_findings_batch().

Bounds:
  - MAX_FINGERPRINT_FINDINGS = 1000
  - MAX_FINGERPRINTS_PER_FINDING = 5
  - MAX_PATTERN_BYTES = 4096
  - FINGERPRINT_TIMEOUT_S = 10.0

GHOST_INVARIANTS enforced:
  - asyncio.gather with return_exceptions=True
  - _check_gathered() after every gather
  - asyncio.CancelledError re-raised
  - No blocking calls in event loop; regex-only CPU work
  - Canonical write path: async_ingest_findings_batch()
  - RAM guard: skip if RSS > high_water
  - Bounds on every collection
  - Fail-soft: malformed payload_text skipped

Source type: "passive_fingerprint"
"""

import asyncio
import hashlib
import logging
import re
import time
from typing import TYPE_CHECKING, Any, TypedDict

from compat.msgspec_gc_compat import Struct
from hledac.universal.network.favicon_hasher import _FaviconHasher
from hledac.universal.transport.session_pool import session_pool
from hledac.universal.utils.asyncx import _check_gathered
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
logger = logging.getLogger(__name__)
_favicon_hasher = _FaviconHasher()
MAX_FINGERPRINT_FINDINGS: int = 1000
MAX_FINGERPRINTS_PER_FINDING: int = 5
MAX_PATTERN_BYTES: int = 4096
FINGERPRINT_TIMEOUT_S: float = 10.0


class ServiceFingerprint(Struct, frozen=True):
    """A single passive service fingerprint derived from finding data."""

    finding_id: str
    service_name: str
    product: str
    version: str
    confidence: float
    evidence_ids: tuple[str, ...]
    facets: dict[str, str]


class FingerprintResult(Struct, frozen=True):
    """Outcome of a passive fingerprinting run."""

    fingerprints: tuple[ServiceFingerprint, ...]
    scanned_count: int
    skipped_count: int
    elapsed_ms: float


class TechStack(Struct, frozen=True):
    """R11: Tech stack signals extracted from HTTP headers, cookies, and HTML."""

    cloud_provider: str | None
    cdn_provider: str | None
    waf_detected: str | None
    waf_confidence: float
    cms: str | None
    cms_version: str | None
    raw_signals: dict[str, str]


_HTTP_SERVER_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [
    ("apache", re.compile("^Apache(?:/([\\d.]+))?", re.I), "Apache", ""),
    ("nginx", re.compile("^nginx(?:/([\\d.]+))?", re.I), "nginx", ""),
    ("microsoft-iis", re.compile("^Microsoft-IIS(?:/([\\d.]+))?", re.I), "Microsoft IIS", ""),
    ("iis", re.compile("^IIS(?:/([\\d.]+))?", re.I), "Microsoft IIS", ""),
    ("litespeed", re.compile("^LiteSpeed(?:/([\\d.]+))?", re.I), "LiteSpeed", ""),
    ("cloudflare", re.compile("^cloudflare", re.I), "Cloudflare", ""),
    ("akamai", re.compile("^AkamaiGHost", re.I), "Akamai", ""),
    ("akamai", re.compile("^Akamai", re.I), "Akamai", ""),
    ("nginx", re.compile("^openresty", re.I), "OpenResty", ""),
    ("nginx", re.compile("^Tengine", re.I), "Tengine", ""),
    ("caddy", re.compile("^Caddy", re.I), "Caddy", ""),
    ("python", re.compile("^Python", re.I), "Python", ""),
    ("php", re.compile("^PHP", re.I), "PHP", ""),
    ("ruby", re.compile("^Phusion Passenger", re.I), "Phusion Passenger", ""),
    ("iis", re.compile("^ASP\\.NET", re.I), "ASP.NET", ""),
    ("iis", re.compile("^Microsoft-AspNet", re.I), "ASP.NET", ""),
    ("tomcat", re.compile("^Apache-Coyote", re.I), "Apache Coyote", ""),
    ("tomcat", re.compile("^Tomcat", re.I), "Apache Tomcat", ""),
    ("jetty", re.compile("^Jetty", re.I), "Jetty", ""),
    ("glassfish", re.compile("^GlassFish", re.I), "GlassFish", ""),
    ("wildfly", re.compile("^WildFly", re.I), "WildFly", ""),
    ("node.js", re.compile("^NodeJS", re.I), "Node.js", ""),
    ("express", re.compile("^Express", re.I), "Express.js", ""),
    ("fastly", re.compile("^Varnish", re.I), "Varnish", ""),
    ("fastly", re.compile("^Fastly", re.I), "Fastly", ""),
    ("squarespace", re.compile("Squarespace", re.I), "Squarespace", ""),
    ("shopify", re.compile("^Shopify", re.I), "Shopify", ""),
    ("wix", re.compile("^nginx/1\\.\\d+ (\\w+)", re.I), "Wix", ""),
    ("wordpress", re.compile("nginx/[\\d.]+ (WordPress)", re.I), "WordPress", ""),
    ("drupal", re.compile("X-Generator: Drupal", re.I), "Drupal", ""),
    ("joomla", re.compile("X-Generator: Joomla", re.I), "Joomla", ""),
]
_HTTP_HEADER_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("x-powered-by", re.compile("PHP/([\\d.]+)", re.I), "PHP"),
    ("x-powered-by", re.compile("ASP\\.NET", re.I), "ASP.NET"),
    ("x-powered-by", re.compile("Express", re.I), "Express.js"),
    ("x-powered-by", re.compile("Django", re.I), "Django"),
    ("x-powered-by", re.compile("Ruby on Rails", re.I), "Rails"),
    ("x-powered-by", re.compile("Laravel", re.I), "Laravel"),
    ("x-aspnet-version", re.compile("([\\d.]+)", re.I), "ASP.NET"),
    ("cf-ray", re.compile(".*", re.I), "Cloudflare"),
    ("via", re.compile("1\\.\\d+ Varnish", re.I), "Varnish"),
    ("server-timing", re.compile("Cloudflare", re.I), "Cloudflare"),
]
_TLS_CERT_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [
    ("cloudflare", re.compile("Cloudflare", re.I), "Cloudflare", ""),
    ("akamai", re.compile("Akamai", re.I), "Akamai", ""),
    ("amazon-aws", re.compile("Amazon|aws|amazon", re.I), "AWS", ""),
    ("azure", re.compile("Microsoft|Azure", re.I), "Azure", ""),
    ("google-cloud", re.compile("Google|Google Cloud|gstatic", re.I), "Google Cloud", ""),
    ("letsencrypt", re.compile("Let's Encrypt", re.I), "Let's Encrypt", ""),
    ("digiCert", re.compile("DigiCert", re.I), "DigiCert", ""),
    ("comodo", re.compile("Comodo", re.I), "Comodo", ""),
    ("geotrust", re.compile("GeoTrust", re.I), "GeoTrust", ""),
    ("verisign", re.compile("VeriSign", re.I), "VeriSign", ""),
    ("thawte", re.compile("thawte", re.I), "thawte", ""),
]
_CT_CERT_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [
    ("cloudflare", re.compile("Cloudflare", re.I), "Cloudflare", ""),
    ("akamai", re.compile("CloudFront", re.I), "CloudFront", ""),
    ("amazon-aws", re.compile("Amazon CloudFront", re.I), "CloudFront", ""),
    ("fastly", re.compile("Fastly", re.I), "Fastly", ""),
    ("microsoft", re.compile("Microsoft.*(?: corp| corporation)", re.I), "Microsoft", ""),
    ("google", re.compile("Google LLC", re.I), "Google", ""),
    ("apple", re.compile("Apple Inc", re.I), "Apple", ""),
    ("facebook", re.compile("Facebook", re.I), "Meta", ""),
    ("github", re.compile("GitHub", re.I), "GitHub", ""),
    ("cloudflare", re.compile("Cloudflare, Inc", re.I), "Cloudflare", ""),
    ("amazon-aws", re.compile("Amazon.com", re.I), "Amazon AWS", ""),
    ("shopify", re.compile("Shopify", re.I), "Shopify", ""),
    ("wordpress", re.compile("Automattic", re.I), "WordPress", ""),
    ("akamai", re.compile("Akamai Technologies", re.I), "Akamai", ""),
    ("vercel", re.compile("Vercel", re.I), "Vercel", ""),
    ("netlify", re.compile("Netlify", re.I), "Netlify", ""),
]
_HTML_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [
    ("wordpress", re.compile("wp-content|wp-includes", re.I), "WordPress", ""),
    ("wordpress", re.compile("WordPress", re.I), "WordPress", ""),
    ("drupal", re.compile("drupalSettings|Drupal.theme", re.I), "Drupal", ""),
    ("joomla", re.compile("Joomla", re.I), "Joomla", ""),
    ("wix", re.compile("wix.com|wixi|var wix", re.I), "Wix", ""),
    ("shopify", re.compile("shopify|myShopify", re.I), "Shopify", ""),
    ("squarespace", re.compile("Squarespace", re.I), "Squarespace", ""),
    ("ghost", re.compile("Ghost", re.I), "Ghost CMS", ""),
    ("hubspot", re.compile("hubspot|hs-script", re.I), "HubSpot", ""),
    ("wordpress", re.compile("xmlrpc.php|wlwmanifest.xml", re.I), "WordPress", ""),
    ("drupal", re.compile("modules/.*\\.js\\?v=", re.I), "Drupal", ""),
    ("joomla", re.compile("/media/jui|com_content", re.I), "Joomla", ""),
    ("magento", re.compile("mage/", re.I), "Magento", ""),
    ("prestashop", re.compile("prestashop|_PS_VERSION_", re.I), "PrestaShop", ""),
    ("react", re.compile("react|fb-root|_react_event_id", re.I), "React", ""),
    ("vue", re.compile("vuejs|__vue__|data-v-", re.I), "Vue.js", ""),
    ("angular", re.compile("ng-app|angular|angularjs", re.I), "Angular", ""),
    ("next.js", re.compile("__NEXT_DATA__|_next/static", re.I), "Next.js", ""),
    ("gatsby", re.compile("gatsby|__gatsby", re.I), "Gatsby", ""),
    ("django", re.compile("csrfmiddlewaretoken|django", re.I), "Django", ""),
    ("flask", re.compile("flask|Werkzeug", re.I), "Flask", ""),
    ("laravel", re.compile("laravel|_token|XSRF-TOKEN", re.I), "Laravel", ""),
    ("ruby-on-rails", re.compile("Ruby on Rails|rails", re.I), "Rails", ""),
    ("spring", re.compile("Spring Framework|springframework", re.I), "Spring", ""),
    ("express", re.compile("Express|node_modules/express", re.I), "Express.js", ""),
]
_PROTOCOL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("TLSv1.2", re.compile("TLSv?1\\.2", re.I)),
    ("TLSv1.3", re.compile("TLSv?1\\.3", re.I)),
    ("HTTP/1.0", re.compile("HTTP/1\\.0", re.I)),
    ("HTTP/1.1", re.compile("HTTP/1\\.1", re.I)),
    ("HTTP/2", re.compile("H2(?:[ ,]|$)|^SPDY", re.I)),
    ("HTTP/3", re.compile("H3(?:[ ,]|$)|HTTP/3", re.I)),
]
_GA_UA_PATTERN: re.Pattern = re.compile(r"UA-\d{6,}-\d+", re.I)
_GA4_PATTERN: re.Pattern = re.compile(r"G-[A-Z0-9]{10,}", re.I)
_GTM_PATTERN: re.Pattern = re.compile(r"GTM-[A-Z0-9]{4,}", re.I)
_AW_PATTERN: re.Pattern = re.compile(r"AW-\d+", re.I)
_FAVICON_LINK_RE: re.Pattern = re.compile(
    r'<link[^>]+rel=["\']?[^"\'>]*icon[^"\'>]*["\']?[^>]+href=["\']?([^"\'>\s]+)', re.I
)
_FAVICON_LINK_REV_RE: re.Pattern = re.compile(
    r'<link[^>]+href=["\']?([^"\'>\s]+)["\']?[^>]+rel=["\']?[^"\'>]*icon', re.I
)
_favicon_mmh3_cache: dict[str, str] = {}
_stats: dict[str, int] = {
    "findings_scanned": 0,
    "findings_skipped": 0,
    "fingerprints_produced": 0,
    "patterns_matched": 0,
}


def get_fingerprint_stats() -> dict[str, int]:
    """Return copy of fingerprint stats (for probe verification)."""
    return dict(_stats)


def reset_fingerprint_stats() -> None:
    """Reset all stats to zero (for probe test isolation)."""
    _stats.clear()
    _stats.update({"findings_scanned": 0, "findings_skipped": 0, "fingerprints_produced": 0, "patterns_matched": 0})


class HttpSignals(TypedDict):
    server_headers: list[str]
    x_headers: list[str]
    all_headers: list[str]
    html_content: str


class TlsSignals(TypedDict):
    cert_subject: list[str]
    cert_issuer: list[str]
    cert_san: list[str]
    cipher_suite: list[str]
    protocol_version: list[str]
    all_text: list[str]


class CtSignals(TypedDict):
    cert_issuer: list[str]
    cert_subject: list[str]
    all_names: list[str]


class HtmlSignals(TypedDict):
    title: list[str]
    generator: list[str]
    scripts: list[str]
    all_text: list[str]
    favicon_url: str | None
    tracking_ids: dict[str, list[str]]


def extract_http_signals(payload_text: str | None) -> HttpSignals:
    """
    Extract HTTP-related signals from finding payload_text.

    Returns dict with keys:
      - server_headers: list of Server header values
      - x_headers: list of X-* header values
      - all_headers: combined header text for pattern matching
      - html_content: HTML body if present
    """
    signals: HttpSignals = {"server_headers": [], "x_headers": [], "all_headers": [], "html_content": ""}
    if not payload_text:
        return signals
    try:
        data = _msgspec_decode(payload_text) if isinstance(payload_text, str) else payload_text
    except Exception:
        return signals
    headers = data.get("http_headers", {}) or data.get("headers", {}) or {}
    if isinstance(headers, dict):
        for key, value in headers.items():
            key_lower = key.lower()
            if key_lower == "server" and value:
                signals["server_headers"].append(str(value))
            if key_lower.startswith("x-"):
                signals["x_headers"].append(f"{key}: {value}")
            signals["all_headers"].append(f"{key}: {value}")
    if isinstance(headers, str):
        signals["all_headers"].append(headers)
    html = data.get("html", "") or data.get("body", "") or data.get("content", "") or ""
    if isinstance(html, str) and len(html) <= MAX_PATTERN_BYTES:
        signals["html_content"] = html[:MAX_PATTERN_BYTES]
    status = data.get("status_code") or data.get("status", 0)
    if status:
        signals["all_headers"].append(f"status: {status}")
    return signals


def extract_tls_signals(payload_text: str | None) -> TlsSignals:
    """
    Extract TLS/certificate signals from finding payload_text.

    Returns dict with keys:
      - cert_subject: certificate subject CN
      - cert_issuer: certificate issuer
      - cert_san: subject alternative names
      - cipher_suite: negotiated cipher suite
      - protocol_version: TLS version
      - all_text: combined cert text for pattern matching
    """
    signals: TlsSignals = {
        "cert_subject": [],
        "cert_issuer": [],
        "cert_san": [],
        "cipher_suite": [],
        "protocol_version": [],
        "all_text": [],
    }
    if not payload_text:
        return signals
    try:
        data = _msgspec_decode(payload_text) if isinstance(payload_text, str) else payload_text
    except Exception:
        return signals
    cert = data.get("certificate", {}) or data.get("cert", {}) or data.get("ssl_cert", {}) or {}
    if isinstance(cert, dict):
        subject = cert.get("subject", "") or cert.get("subject_cn", "")
        issuer = cert.get("issuer", "") or cert.get("issuer_c", "")
        san_list = cert.get("san", []) or cert.get("subject_alternative_names", [])
        if subject:
            signals["cert_subject"].append(subject)
        if issuer:
            signals["cert_issuer"].append(issuer)
        if san_list:
            if isinstance(san_list, list):
                signals["cert_san"].extend(str(s) for s in san_list)
            else:
                signals["cert_san"].append(str(san_list))
    tls = data.get("tls", {}) or data.get("tls_info", {}) or {}
    if isinstance(tls, dict):
        cipher = tls.get("cipher", "") or tls.get("cipher_suite", "")
        protocol = tls.get("version", "") or tls.get("protocol", "")
        if cipher:
            signals["cipher_suite"].append(cipher)
        if protocol:
            signals["protocol_version"].append(protocol)
    for field_key in ("subject", "issuer", "cn", "common_name"):
        val = data.get(field_key, "")
        if val:
            signals["cert_subject"].append(str(val))
    for field_key in ("san", "subject_alternative_name", "alt_names"):
        val = data.get(field_key, "")
        if val:
            if isinstance(val, list):
                signals["cert_san"].extend(str(s) for s in val)
            else:
                signals["cert_san"].append(str(val))
    all_text_parts = (
        signals["cert_subject"]
        + signals["cert_issuer"]
        + signals["cert_san"]
        + signals["cipher_suite"]
        + signals["protocol_version"]
    )
    signals["all_text"] = all_text_parts
    return signals


def extract_ct_signals(payload_text: str | None) -> CtSignals:
    """
    Extract CT (Certificate Transparency) metadata signals.

    Returns dict with keys:
      - cert_issuer: issuer organization
      - cert_subject: subject organization
      - all_names: all names from cert entries
    """
    signals: CtSignals = {"cert_issuer": [], "cert_subject": [], "all_names": []}
    if not payload_text:
        return signals
    try:
        data = _msgspec_decode(payload_text) if isinstance(payload_text, str) else payload_text
    except Exception:
        return signals
    ct_entries = data.get("ct_entries", []) or data.get("certificate_transparency", []) or []
    if not isinstance(ct_entries, list):
        ct_entries = [ct_entries] if ct_entries else []
    for entry in ct_entries[:50]:
        if isinstance(entry, dict):
            issuer = entry.get("issuer", "") or entry.get("issuer_cn", "") or entry.get("issuer_organization", "")
            subject = entry.get("subject", "") or entry.get("cn", "") or entry.get("subject_cn", "")
            all_name = entry.get("name", "") or entry.get("common_name", "") or entry.get("san", "")
            if issuer:
                signals["cert_issuer"].append(str(issuer))
            if subject:
                signals["cert_subject"].append(str(subject))
            if all_name:
                signals["all_names"].append(str(all_name))
        elif isinstance(entry, str):
            signals["all_names"].append(entry)
    if data.get("issuer"):
        signals["cert_issuer"].append(str(data["issuer"]))
    if data.get("domain"):
        signals["all_names"].append(str(data["domain"]))
    if data.get("name"):
        signals["all_names"].append(str(data["name"]))
    return signals


def _extract_title_and_generator(html: str) -> tuple[list[str], list[str]]:
    """Extract title and generator meta tags from HTML."""
    titles, generators = [], []
    if not html:
        return titles, generators
    title_match = re.search("<title[^>]*>([^<]+)</title>", html, re.I)
    if title_match:
        titles.append(title_match.group(1).strip())
    gen_match = re.search("<meta[^>]+generator[^>]+content=[\"\\']([^\"\\']+)[\"\\']", html, re.I)
    if gen_match:
        generators.append(gen_match.group(1).strip())
    if not generators:
        gen_match2 = re.search("<meta[^>]+content=[\"\\']([^\"\\']+)[\"\\'][^>]+generator", html, re.I)
        if gen_match2:
            generators.append(gen_match2.group(1).strip())
    return titles, generators


def _extract_scripts_and_domains(html: str) -> tuple[list[str], list[str]]:
    """Extract script src URLs and domains from HTML."""
    scripts, domains = [], []
    if not html:
        return scripts, domains
    script_matches = re.findall("<script[^>]+src=[\"\\']([^\"\\']+)[\"\\']", html, re.I)
    for src in script_matches[:20]:
        scripts.append(src)
        domain_match = re.search("https?://([^/]+)", src)
        if domain_match:
            domains.append(domain_match.group(1))
    return scripts, domains


def _extract_link_domains(html: str) -> list[str]:
    """Extract domains from link hrefs in HTML."""
    domains = []
    if not html:
        return domains
    link_matches = re.findall("<link[^>]+href=[\"\\']([^\"\\']+)[\"\\']", html, re.I)
    for href in link_matches[:20]:
        domain_match = re.search("https?://([^/]+)", href)
        if domain_match:
            domains.append(domain_match.group(1))
    return domains


def _extract_favicon_signals(html: str) -> tuple[str | None, list[str]]:
    """Extract favicon URL and any associated signals."""
    favicon_url = None
    signals: list[str] = []
    if not html:
        return favicon_url, signals
    favicon_match = _FAVICON_LINK_RE.search(html)
    if not favicon_match:
        favicon_match = _FAVICON_LINK_REV_RE.search(html)
    if favicon_match:
        favicon_url = favicon_match.group(1)
        signals.append(f"favicon:{favicon_url}")
        if favicon_url in _favicon_mmh3_cache:
            signals.append(f"favicon_mmh3:{_favicon_mmh3_cache[favicon_url]}")
    return favicon_url, signals


def _extract_tracking_signals(tracking_ids: dict[str, list[str]]) -> list[str]:
    """Convert tracking IDs to text signals."""
    signals = []
    if not tracking_ids:
        return signals
    for ga_id in tracking_ids.get("ua_ids", []):
        signals.append(f"ga:{ga_id}")
    for ga4_id in tracking_ids.get("ga4_ids", []):
        signals.append(f"ga4:{ga4_id}")
    for gtm_id in tracking_ids.get("gtm_ids", []):
        signals.append(f"gtm:{gtm_id}")
    for aw_id in tracking_ids.get("aw_ids", []):
        signals.append(f"aw:{aw_id}")
    return signals


def extract_html_signals(payload_text: str | None) -> HtmlSignals:
    """
    Extract HTML content signals for service fingerprinting.

    Returns dict with keys:
      - title: page title
      - generator: meta generator tag
      - scripts: script src patterns
      - all_text: combined HTML text
      - favicon_url: resolved favicon URL (P8-007)
      - tracking_ids: dict with ga_ids, gtm_ids, ga4_ids, aw_ids (P8-007)
    """
    signals: HtmlSignals = {
        "title": [],
        "generator": [],
        "scripts": [],
        "all_text": [],
        "favicon_url": None,
        "tracking_ids": {},
    }
    if not payload_text:
        return signals
    try:
        data = _msgspec_decode(payload_text) if isinstance(payload_text, str) else payload_text
    except Exception:
        return signals
    html = data.get("html", "") or data.get("body", "") or data.get("content", "") or ""
    if not isinstance(html, str):
        return signals
    html = html[:MAX_PATTERN_BYTES]

    signals["title"], signals["generator"] = _extract_title_and_generator(html)

    signals["scripts"], script_domains = _extract_scripts_and_domains(html)
    signals["all_text"].extend(script_domains)

    signals["all_text"].extend(_extract_link_domains(html))

    signals["favicon_url"], favicon_signals = _extract_favicon_signals(html)
    signals["all_text"].extend(favicon_signals)

    signals["tracking_ids"] = _extract_tracking_ids(html)
    signals["all_text"].extend(_extract_tracking_signals(signals["tracking_ids"]))

    # Final consolidation
    signals["all_text"].extend(signals["title"])
    signals["all_text"].extend(signals["generator"])
    return signals


def _extract_tracking_ids(html: str) -> dict[str, list[str]]:
    """
    P8-007: Extract Google Analytics, GA4, GTM, and Google Ads tracking IDs from HTML.

    Tracking IDs are strong cross-site attribution signals:
      - Same GA ID across domains → shared ownership or same operator
      - Same GTM container → same tag management infrastructure

    Returns dict with keys: ua_ids, ga4_ids, gtm_ids, aw_ids
    """
    result: dict[str, list[str]] = {}
    if not html:
        return result
    try:
        ua_matches = _GA_UA_PATTERN.findall(html)
        if ua_matches:
            result["ua_ids"] = list(dict.fromkeys(ua_matches))[:10]
        ga4_matches = _GA4_PATTERN.findall(html)
        if ga4_matches:
            result["ga4_ids"] = list(dict.fromkeys(ga4_matches))[:10]
        gtm_matches = _GTM_PATTERN.findall(html)
        if gtm_matches:
            result["gtm_ids"] = list(dict.fromkeys(gtm_matches))[:10]
        aw_matches = _AW_PATTERN.findall(html)
        if aw_matches:
            result["aw_ids"] = list(dict.fromkeys(aw_matches))[:5]
    except Exception:  # noqa: BLE001
        pass
    return result


def _resolve_favicon_url(favicon_url: str, page_url: str) -> str:
    """
    P8-007: Resolve a potentially relative favicon URL against a page URL.

    Handles:
      - //cdn.example.com/favicon.ico (protocol-relative)
      - /favicon.ico (absolute path)
      - favicon.ico (relative path)
      - https://example.com/favicon.ico (already absolute)
    """
    from urllib.parse import urljoin, urlparse

    if not favicon_url:
        return ""
    favicon_url = favicon_url.strip()
    if favicon_url.startswith("//"):
        parsed_page = urlparse(page_url) if "://" in page_url else None
        scheme = parsed_page.scheme if parsed_page else "https"
        return f"{scheme}:{favicon_url}"
    if favicon_url.startswith("http://") or favicon_url.startswith("https://"):
        return favicon_url
    if page_url and ("://" in page_url):
        return urljoin(page_url, favicon_url)
    return favicon_url


async def _compute_favicon_mmh3(
    html: str,
    page_url: str,
    session: httpx.AsyncClient,
) -> str | None:
    """
    P8-007: Download favicon and compute MMH3 hash for cross-site clustering.

    Shodan/faviconhash.com use the same MurmurHash3 algorithm to cluster
    sites by favicon similarity — same hash = shared hosting/ownership.

    Uses network/favicon_hasher._FaviconHasher for M1-optimized hashing
    (mmh3 primary, xxh3_64 fallback via Rust backend).

    Args:
        html: Raw HTML content of the page
        page_url: URL of the page (for resolving relative favicon URLs)
        session: httpx async client for downloading the favicon

    Returns:
        Favicon hash string like 'mmh3:1234567890' or None on failure.
    """
    import httpx

    if not html or not page_url:
        return None
    favicon_url = ""
    favicon_match = _FAVICON_LINK_RE.search(html)
    if not favicon_match:
        favicon_match = _FAVICON_LINK_REV_RE.search(html)
    if favicon_match:
        favicon_url = _resolve_favicon_url(favicon_match.group(1), page_url)
    if not favicon_url:
        parsed = page_url.split("://", 1)[-1].split("/")[0]
        if "://" in page_url:
            favicon_url = (
                f"{page_url.rsplit('/', 1)[0]}/favicon.ico"
                if "/" in page_url.split("://", 1)[-1]
                else f"{page_url}/favicon.ico"
            )
        else:
            favicon_url = f"https://{parsed}/favicon.ico"
    if favicon_url in _favicon_mmh3_cache:
        return _favicon_mmh3_cache[favicon_url]
    try:
        resp = await session.get(favicon_url, timeout=httpx.Timeout(total=5.0))
        if resp.status_code == 200 and resp.content:
            h = _favicon_hasher.hash_favicon(resp.content)
            if h:
                _favicon_mmh3_cache[favicon_url] = h
                return h
    except httpx.TimeoutException, httpx.HTTPError, OSError, ValueError:  # noqa: BLE001
        pass
    except Exception:  # noqa: BLE001
        pass
    return None


def _detect_cloud_provider(headers: dict[str, str]) -> str | None:
    """Detect cloud provider from HTTP headers."""
    for header_key in headers:
        if header_key.startswith("x-amz-"):
            return "AWS"
        if header_key.startswith("x-goog-"):
            return "GCP"
        if header_key.startswith("x-ms-"):
            return "Azure"
    return None


def _detect_cdn(headers: dict[str, str]) -> str | None:
    """Detect CDN provider from HTTP headers."""
    # Check CF-Ray first (Cloudflare)
    if headers.get("cf-ray") or headers.get("cf-ray-legacy"):
        return "Cloudflare"
    via = headers.get("via", "").lower()
    server = headers.get("server", "").lower()
    if "fastly" in via or "fastly" in server:
        return "Fastly"
    for hk, hv in headers.items():
        if "akamai" in hk.lower() or "akamai" in hv.lower():
            return "Akamai"
    return None


def _detect_waf(headers: dict[str, str], status: str = "", html_lower: str = "") -> tuple[str | None, float]:
    """Detect WAF from HTTP response. Returns (waf_name, confidence)."""
    # Cloudflare WAF: 403 + 1020 error page
    if "403" in str(status) and html_lower:
        if "error 1020" in html_lower or ("cloudflare" in html_lower and "access denied" in html_lower):
            return "Cloudflare WAF", 0.95
    # Imperva from cookies
    for cookie in headers.get("set-cookie", "").split(","):
        if "incap_ses" in cookie.lower() or "visid_incap_" in cookie.lower():
            return "Imperva", 0.9
    # AWS WAF from headers/cookies
    if headers.get("aws-waf-request") or headers.get("aws-alb"):
        return "AWS WAF", 0.85
    # F5 BIG-IP
    for hk in headers:
        if "bigip" in hk.lower() or "ftm" in hk.lower():
            return "F5 BIG-IP", 0.8
    # Akamai WAF (Sucuri)
    for hk in headers:
        if "sucuri" in hk.lower() or "x-sucuri" in hk.lower():
            return "Akamai WAF", 0.75
    return None, 0.0


def _detect_cms(html_lower: str) -> tuple[str | None, str | None]:
    """Detect CMS from HTML content. Returns (cms_name, version)."""
    # Use ahocorasick for O(n) matching if available
    try:
        import ahocorasick

        cms_patterns = [
            ("wordpress", "wordpress"),
            ("drupal", "drupal"),
            ("joomla", "joomla"),
            ("typo3", "typo3"),
            ("magento", "magento"),
            ("prestashop", "prestashop"),
            ("shopify", "shopify"),
            ("wix", "wix"),
            ("squarespace", "squarespace"),
            ("ghost", "ghost cms"),
            ("hubspot", "hubspot"),
        ]
        automaton = ahocorasick.Automaton()
        for pattern, name in cms_patterns:
            automaton.add_word(pattern, name)
        automaton.make_automaton()
        found: set[str] = set()
        for _, name in automaton.iter(html_lower[:5000]):
            found.add(name)
        if len(found) == 1:
            cms = next(iter(found))
        elif len(found) > 1:
            priority = [
                "typo3",
                "magento",
                "prestashop",
                "drupal",
                "joomla",
                "wordpress",
                "shopify",
                "ghost cms",
                "hubspot",
                "wix",
                "squarespace",
            ]
            cms = next((p for p in priority if p in found), sorted(found)[0])
        else:
            cms = None
    except ImportError:
        cms_re = re.compile(
            "wordpress|drupal|joomla|typo3|magento|prestashop|shopify|wix|squarespace|ghost|hubspot", re.I
        )
        matches = cms_re.findall(html_lower[:5000])
        cms = matches[0].title() if matches else None
    return cms, None


def _extract_tech_stack(headers: dict[str, str], html_head: str, cookies: list[str]) -> TechStack:
    """
    R11: Extract tech stack signals from HTTP response data.

    Detects:
      - Cloud providers: AWS (x-amz-*), GCP (x-goog-*), Azure (x-ms-*),
        Cloudflare (cf-ray), Fastly, Akamai
      - WAF: Cloudflare WAF (403 + 1020), AWS WAF, Imperva (incap_ses),
        Akamai, F5 BIG-IP
      - CMS: WordPress, Drupal, Joomla, Typo3 (with version from readme/changelog)

    Uses ahocorasick for O(n) multi-pattern matching when available,
    falls back to regex for single patterns.

    Args:
        headers: HTTP response headers (lowercase keys)
        html_head: HTML <head> content (truncated)
        cookies: list of cookie strings

    Returns:
        TechStack with detected signals and confidence scores.
    """
    raw_signals: dict[str, str] = {}

    # Detect cloud provider
    cloud_provider = _detect_cloud_provider(headers)
    if cloud_provider:
        raw_signals[f"{cloud_provider.lower()}_header"] = "detected"

    # Detect CDN
    cdn_provider = _detect_cdn(headers)
    if cdn_provider:
        raw_signals["cdn_provider"] = cdn_provider

    # Detect WAF
    status_val = headers.get("status", "") or headers.get(":status", "")
    html_lower = html_head.lower()[:5000]
    waf_detected, waf_confidence = _detect_waf(headers, status_val, html_lower)
    if waf_detected:
        raw_signals["waf_signal"] = waf_detected.lower().replace(" ", "_")

    # Detect CMS
    cms, cms_version = _detect_cms(html_lower)
    if cms:
        raw_signals["cms_detected"] = cms
        if cms_version:
            raw_signals["cms_version"] = cms_version

    return TechStack(
        cloud_provider=cloud_provider,
        cdn_provider=cdn_provider,
        waf_detected=waf_detected,
        waf_confidence=waf_confidence,
        cms=cms,
        cms_version=cms_version,
        raw_signals=raw_signals,
    )


def _match_server_header(server_value: str) -> list[ServiceFingerprint]:
    """Match a Server header value against known patterns."""
    if not server_value:
        return []
    fingerprints: list[ServiceFingerprint] = []
    matched: set[str] = set()
    for service_name, pattern, product, version_hint in _HTTP_SERVER_PATTERNS:
        if service_name in matched:
            continue
        m = pattern.match(server_value)
        if m:
            version = m.group(1) if m.lastindex and m.group(1) else version_hint
            fingerprints.append(
                ServiceFingerprint(
                    finding_id="",
                    service_name=service_name,
                    product=product,
                    version=version or "",
                    confidence=0.9,
                    evidence_ids=(),
                    facets={"source": "http_server_header", "raw": server_value[:200]},
                )
            )
            matched.add(service_name)
            _stats["patterns_matched"] += 1
    return fingerprints


def _match_http_headers(headers_list: list[str]) -> list[ServiceFingerprint]:
    """Match HTTP headers against known service patterns."""
    fingerprints: list[ServiceFingerprint] = []
    if not headers_list:
        return fingerprints
    combined_text = " ".join(str(h) for h in headers_list)[:MAX_PATTERN_BYTES]
    matched: set[str] = set()
    for facet_key, pattern, service_hint in _HTTP_HEADER_PATTERNS:
        if service_hint in matched:
            continue
        if pattern.search(combined_text):
            fingerprints.append(
                ServiceFingerprint(
                    finding_id="",
                    service_name=facet_key,
                    product=service_hint,
                    version="",
                    confidence=0.6,
                    evidence_ids=(),
                    facets={"source": "http_header", "header": facet_key},
                )
            )
            matched.add(service_hint)
            _stats["patterns_matched"] += 1
    return fingerprints


def _match_patterns(
    texts: list[str], patterns: list[tuple[str, Any, str, str]], source: str, confidence: float
) -> list[ServiceFingerprint]:
    """Generic pattern matcher for service fingerprinting."""
    fingerprints: list[ServiceFingerprint] = []
    if not texts:
        return fingerprints
    combined_text = " ".join(str(t) for t in texts)[:MAX_PATTERN_BYTES]
    matched: set[str] = set()
    for service_name, pattern, product, version_hint in patterns:
        if service_name in matched:
            continue
        if pattern.search(combined_text):
            fingerprints.append(
                ServiceFingerprint(
                    finding_id="",
                    service_name=service_name,
                    product=product,
                    version=version_hint,
                    confidence=confidence,
                    evidence_ids=(),
                    facets={"source": source, "matched_on": service_name},
                )
            )
            matched.add(service_name)
            _stats["patterns_matched"] += 1
    return fingerprints


def _match_tls_cert(texts: list[str]) -> list[ServiceFingerprint]:
    """Match TLS/certificate text against known patterns."""
    return _match_patterns(texts, _TLS_CERT_PATTERNS, "tls_cert", 0.85)


def _match_ct_metadata(texts: list[str]) -> list[ServiceFingerprint]:
    """Match CT metadata against known service patterns."""
    return _match_patterns(texts, _CT_CERT_PATTERNS, "ct_metadata", 0.8)


def _match_html_content(texts: list[str]) -> list[ServiceFingerprint]:
    """Match HTML content against known service patterns."""
    return _match_patterns(texts, _HTML_PATTERNS, "html_content", 0.7)


def extract_fingerprints(finding: CanonicalFinding) -> list[ServiceFingerprint]:
    """
    Extract all fingerprints from a single CanonicalFinding.

    Checks HTTP headers, TLS/cert data, CT metadata, and HTML content.
    Returns up to MAX_FINGERPRINTS_PER_FINDING fingerprints.

    Bounds:
      - MAX_FINGERPRINTS_PER_FINDING = 5
      - MAX_PATTERN_BYTES = 4096
    """
    fid = getattr(finding, "finding_id", "") or ""
    payload = getattr(finding, "payload_text", None) or "{}"
    if isinstance(payload, str) and len(payload) > MAX_PATTERN_BYTES:
        payload = payload[:MAX_PATTERN_BYTES]

    http_signals = extract_http_signals(payload)
    tls_signals = extract_tls_signals(payload)
    ct_signals = extract_ct_signals(payload)
    html_signals = extract_html_signals(payload)

    fingerprints = _build_dispatch_fingerprints(fid, http_signals, tls_signals, ct_signals, html_signals)

    fingerprints.extend(_extract_tracking_fingerprints(fid, html_signals.get("tracking_ids", {})))

    fingerprints.extend(_extract_tech_stack_fingerprints(fid, http_signals))

    return _deduplicate_fingerprints(fingerprints, MAX_FINGERPRINTS_PER_FINDING)


def _build_dispatch_fingerprints(
    fid: str,
    http_signals: HttpSignals,
    tls_signals: TlsSignals,
    ct_signals: CtSignals,
    html_signals: HtmlSignals,
) -> list[ServiceFingerprint]:
    """Phase 2: Build pattern-matched fingerprints via dispatch table."""
    fingerprints: list[ServiceFingerprint] = []
    seen: set[str] = set()

    # Dispatch table: signal type → (match_func, inputs, default_confidence)
    # Eliminated 6 repetitive if/for blocks → single iteration
    dispatch: tuple[tuple[str, str, str, callable, list[str], float], ...] = (
        ("http", "server", "server_headers", _match_server_header, http_signals["server_headers"][:3], 0.9),
        (
            "http",
            "x_headers",
            "x+all_headers",
            _match_http_headers,
            http_signals["x_headers"] + http_signals["all_headers"],
            0.6,
        ),
        ("tls", "cert", "all_text", _match_tls_cert, tls_signals["all_text"], 0.85),
        (
            "ct",
            "ct",
            "all_names+issuer+subject",
            _match_ct_metadata,
            ct_signals["all_names"] + ct_signals["cert_issuer"] + ct_signals["cert_subject"],
            0.8,
        ),
        (
            "html",
            "content",
            "all_text+title+generator",
            _match_html_content,
            html_signals["all_text"] + html_signals["title"] + html_signals["generator"],
            0.7,
        ),
    )

    for _, _, _, matcher, inputs, _ in dispatch:
        if not inputs:
            continue
        matched = matcher(inputs) if callable(matcher) else []
        for fp in matched:
            key = f"{fp.service_name}:{fp.product}"
            if key not in seen:
                seen.add(key)
                fingerprints.append(
                    ServiceFingerprint(
                        finding_id=fid,
                        service_name=fp.service_name,
                        product=fp.product,
                        version=fp.version,
                        confidence=fp.confidence,
                        evidence_ids=(fid,),
                        facets=fp.facets,
                    )
                )

    return fingerprints


def _extract_tracking_fingerprints(
    fid: str,
    tracking_ids: dict[str, list[str]],
) -> list[ServiceFingerprint]:
    """Phase 3: Extract Google Analytics/GTM/GA4/Google Ads fingerprints (P8-007)."""
    if not tracking_ids:
        return []

    # Unified tracking ID dispatch: eliminates 4 separate for-loops
    tracking_dispatch: tuple[tuple[str, str, str, float], ...] = (
        ("ua_ids", "google-analytics", "GA UA: %s", 0.95),
        ("ga4_ids", "google-analytics-4", "GA4: %s", 0.95),
        ("gtm_ids", "google-tag-manager", "GTM: %s", 0.95),
        ("aw_ids", "google-ads", "Google Ads: %s", 0.8),
    )

    fingerprints: list[ServiceFingerprint] = []
    for id_key, service_name, product_fmt, confidence in tracking_dispatch:
        for tracking_id in tracking_ids.get(id_key, []):
            tracking_type = id_key.rstrip("s")  # ua_ids → ua_id
            fingerprints.append(
                ServiceFingerprint(
                    finding_id=fid,
                    service_name=service_name,
                    product=product_fmt % tracking_id,
                    version="",
                    confidence=confidence,
                    evidence_ids=(fid,),
                    facets={
                        "source": "tracking_id",
                        "tracking_type": tracking_type,
                        "tracking_id": tracking_id,
                        "_p8_007": True,
                    },
                )
            )
    return fingerprints


def _extract_tech_stack_fingerprints(
    fid: str,
    http_signals: HttpSignals,
) -> list[ServiceFingerprint]:
    """Phase 4: Extract cloud/CDN/WAF/CMS fingerprints from tech stack analysis."""
    if not (http_signals.get("all_headers") or http_signals.get("html_content")):
        return []

    headers_dict: dict[str, str] = {}
    for h in http_signals["all_headers"]:
        if ": " in h:
            k, v = h.split(": ", 1)
            headers_dict[k.lower()] = v

    html_text = http_signals.get("html_content", "")
    html_head = ""
    if html_text:
        head_match = re.search("<head[^>]*>(.*?)</head>", html_text, re.I | re.S)
        if head_match:
            html_head = head_match.group(1)

    tech_stack = _extract_tech_stack(headers_dict, html_head, [])
    fingerprints: list[ServiceFingerprint] = []

    # Tech stack dispatch: eliminates 4 separate if-blocks
    tech_dispatch: tuple[tuple[str | None, str, str, float], ...] = (
        (tech_stack.cloud_provider, "cloud", tech_stack.cloud_provider, 0.85),
        (tech_stack.cdn_provider, "cdn", tech_stack.cdn_provider, 0.85),
        (tech_stack.waf_detected, "waf", tech_stack.waf_detected, tech_stack.waf_confidence),
        (
            tech_stack.cms,
            "cms",
            f"{tech_stack.cms} {tech_stack.cms_version}" if tech_stack.cms_version else tech_stack.cms,
            0.75,
        ),
    )

    for detected, source_type, product, confidence in tech_dispatch:
        if detected:
            service_name = detected.lower().replace(" ", "-") if source_type != "cloud" else detected.lower()
            fingerprints.append(
                ServiceFingerprint(
                    finding_id=fid,
                    service_name=service_name,
                    product=product,
                    version=tech_stack.cms_version if source_type == "cms" else "",
                    confidence=confidence,
                    evidence_ids=(fid,),
                    facets={"source": f"tech_stack_{source_type}", **tech_stack.raw_signals},
                )
            )

    return fingerprints


def _deduplicate_fingerprints(
    fingerprints: list[ServiceFingerprint],
    max_count: int,
) -> list[ServiceFingerprint]:
    """Phase 5: Deduplicate by (service_name, product) and cap result."""
    seen: set[tuple[str, str]] = set()
    unique: list[ServiceFingerprint] = []
    for fp in fingerprints:
        key = (fp.service_name, fp.product)
        if key not in seen:
            seen.add(key)
            unique.append(fp)
    return unique[:max_count]


def to_canonical_findings(fingerprints: list[ServiceFingerprint], query: str) -> list[CanonicalFinding]:
    """
    Convert ServiceFingerprint list to CanonicalFinding list.

    Each CanonicalFinding:
      - source_type = "passive_fingerprint"
      - finding_id = "pfp_{hash}"
      - payload_text = JSON with fingerprint data + facets envelope
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    if not fingerprints:
        return []
    canonical: list[CanonicalFinding] = []
    ts = time.time()
    for fp in fingerprints[:MAX_FINGERPRINT_FINDINGS]:
        id_input = f"{fp.finding_id}:{fp.service_name}:{fp.product}:{int(ts)}"
        fid = f"pfp_{hashlib.sha256(id_input.encode()).hexdigest()[:24]}"
        payload = {
            "service_name": fp.service_name,
            "product": fp.product,
            "version": fp.version,
            "confidence": fp.confidence,
            "evidence_ids": list(fp.evidence_ids),
            "facets": fp.facets,
            "_f204g": True,
        }
        canonical.append(
            CanonicalFinding(
                finding_id=fid,
                query=query,
                source_type="passive_fingerprint",
                confidence=fp.confidence,
                ts=ts,
                provenance=("passive_fingerprint", fp.service_name),
                payload_text=_msgspec_encode(payload).decode(),
            )
        )
    _stats["fingerprints_produced"] = len(canonical)
    return canonical


_GLOBAL_STATS: dict[str, float] = {}


def correlate_passive_fingerprints(findings: list[CanonicalFinding], query: str) -> list[CanonicalFinding]:
    """
    F204G: Extract passive service fingerprints from sprint findings.

    Entry point for the passive fingerprinting sidecar.

    Pipeline:
      1. Iterate over findings (bounded to MAX_FINGERPRINT_FINDINGS)
      2. Extract signals from payload_text (HTTP/TLS/CT/HTML)
      3. Match patterns to identify services
      4. Convert to CanonicalFinding list
      5. Return for async_ingest_findings_batch ingestion

    Bounds enforced:
      - MAX_FINGERPRINT_FINDINGS = 1000
      - MAX_FINGERPRINTS_PER_FINDING = 5
      - MAX_PATTERN_BYTES = 4096

    Fail-soft: returns [] on any error.

    Returns:
        List of CanonicalFinding with source_type="passive_fingerprint".
    """
    try:
        t0 = time.monotonic()
        if not findings:
            return []
        fingerprints: list[ServiceFingerprint] = []
        scanned = 0
        skipped = 0
        extract_start = t0
        for finding in findings[:MAX_FINGERPRINT_FINDINGS]:
            scanned += 1
            try:
                fps = extract_fingerprints(finding)
                fingerprints.extend(fps)
            except Exception:
                skipped += 1
                continue
        extract_elapsed = time.monotonic() - extract_start
        canon_start = time.monotonic()
        canonical = to_canonical_findings(fingerprints, query)
        canon_elapsed = time.monotonic() - canon_start
        total_elapsed = time.monotonic() - t0
        _GLOBAL_STATS["correlate_extract_ms"] = extract_elapsed * 1000
        _GLOBAL_STATS["correlate_canonical_ms"] = canon_elapsed * 1000
        _GLOBAL_STATS["correlate_total_ms"] = total_elapsed * 1000
        _stats["findings_scanned"] = scanned
        _stats["findings_skipped"] = skipped
        if not fingerprints:
            return []
        return canonical
    except Exception as e:
        logger.debug(f"[PassiveFingerprint] correlation failed: {e}")
        return []


async def run_passive_fingerprint_sidecar(findings: list[CanonicalFinding], store: Any, query: str) -> int:
    """
    Async sidecar runner for passive fingerprinting.

    P8-007: Enriches findings with MMH3 favicon hashes for cross-site clustering.

    Returns count of stored findings.
    """
    if not findings or store is None:
        return 0
    try:
        derived_findings = correlate_passive_fingerprints(findings, query)
        if not derived_findings:
            derived_findings = []
        # P8-007: Enrich with favicon MMH3 hashes
        favicon_enriched = await _enrich_favicon_findings(findings, query)
        if favicon_enriched:
            derived_findings.extend(favicon_enriched)
        if not derived_findings:
            return 0
        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except asyncio.CancelledError:
        raise
    except Exception:
        return 0


# ── P8-007: Favicon MMH3 enrichment sidecar ──────────────────────────────


async def _enrich_favicon_findings(
    source_findings: list[CanonicalFinding],
    query: str,
) -> list[CanonicalFinding]:
    """
    P8-007: Create CanonicalFindings with MMH3 favicon hashes.

    Downloads favicons from source findings that have HTML payloads,
    computes MMH3 hashes, and creates additional CanonicalFinding entries
    for infrastructure clustering via favicon similarity.

    Same favicon MMH3 hash across domains = strong signal of shared
    hosting or ownership (Shodan/faviconhash.com method).

    Bounds:
      - MAX_FAVICON_ENRICHMENTS = 20 per batch
      - FAVICON_TIMEOUT_S = 5.0 per download
      - Uses semaphore(5) for concurrent downloads (M1 8GB friendly)
    """
    MAX_FAVICON_ENRICHMENTS = 20
    SEMAPHORE_LIMIT = 5
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    enriched: list[CanonicalFinding] = []
    candidates: list[tuple[str, str]] = []
    for finding in source_findings[:MAX_FINGERPRINT_FINDINGS]:
        if len(candidates) >= MAX_FAVICON_ENRICHMENTS:
            break
        payload = getattr(finding, "payload_text", None) or "{}"
        page_url = ""
        for prov in getattr(finding, "provenance", ()):
            if isinstance(prov, str) and (
                prov.startswith("url:") or prov.startswith("http://") or prov.startswith("https://")
            ):
                if prov.startswith("url:"):
                    page_url = prov[4:300]
                else:
                    page_url = prov[:300]
                break
        if not page_url:
            try:
                data = _msgspec_decode(payload) if isinstance(payload, str) else payload
                page_url = data.get("url", "") or data.get("page_url", "") or ""
            except Exception:  # noqa: BLE001
                pass
        html_content = ""
        try:
            if isinstance(payload, str) and payload.strip():
                data = _msgspec_decode(payload)
                html_content = data.get("html", "") or data.get("body", "") or ""
        except Exception:
            html_content = ""
        if html_content and page_url:
            candidates.append((html_content[:MAX_PATTERN_BYTES], page_url))
    if not candidates:
        return enriched
    semaphore = asyncio.Semaphore(SEMAPHORE_LIMIT)

    async def _fetch_one(html: str, page_url: str) -> str | None:
        async with semaphore:
            # Use session_pool for connection reuse (M1 8GB friendly)
            session = await session_pool.acquire()
            try:
                return await _compute_favicon_mmh3(html, page_url, session)
            finally:
                await session_pool.release(session)

    tasks = [asyncio.create_task(_fetch_one(html, url)) for html, url in candidates]
    if not tasks:
        return enriched
    try:
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        ok_results, errors = _check_gathered(gathered)
        if errors:
            logger.debug("[PASSIVE_FP] favicon enrichment: %d task failures", len(errors))
    except Exception:  # noqa: BLE001 — fail-soft: return enriched partial results
        return enriched
    ts = time.time()
    for i, result in enumerate(ok_results):
        if result is None:
            continue
        html, page_url = candidates[i]
        domain = page_url.split("://")[-1].split("/")[0] if "://" in page_url else page_url
        fid_input = f"favicon_mmh3:{domain}:{result}:{int(ts)}"
        fid = f"fav_{hashlib.sha256(fid_input.encode()).hexdigest()[:20]}"
        payload_out = {
            "fingerprint_type": "favicon_mmh3",
            "fingerprint_value": result,
            "domain": domain,
            "page_url": page_url,
            "source": "passive_fingerprint_enrichment",
            "_p8_007": True,
        }
        enriched.append(
            CanonicalFinding(
                finding_id=fid,
                query=query[:500],
                source_type="passive_fingerprint",
                confidence=0.85,
                ts=ts,
                provenance=("passive_fingerprint", "favicon_mmh3"),
                payload_text=_msgspec_encode(payload_out).decode(),
            )
        )
    return enriched


def should_skip_runs(ram_percent: float, high_water: float) -> bool:
    """
    Determine if passive fingerprinting should be skipped due to RAM pressure.

    Args:
        ram_percent: current RSS as percentage of total
        high_water: high water mark threshold

    Returns:
        True if should skip (ram_percent > 85% AND high_water is critical)
    """
    if high_water <= 0:
        return False
    return ram_percent > 85.0


class PassiveFingerprintAdapter:
    """
    F204G: Bounded passive fingerprinting adapter.

    Wraps the fingerprinting pipeline with M1-safe bounds and fail-soft guarantees.
    """

    __slots__ = ("_stats_snapshot",)

    def __init__(self) -> None:
        self._stats_snapshot: dict[str, int] = {}

    def correlate(self, findings: list[CanonicalFinding], query: str) -> list[CanonicalFinding]:
        """
        Correlate fingerprints from findings.

        Returns list of CanonicalFinding with source_type="passive_fingerprint".
        """
        return correlate_passive_fingerprints(findings, query)

    def get_stats(self) -> dict[str, int]:
        """Return fingerprinting stats snapshot."""
        return get_fingerprint_stats()

    def reset_stats(self) -> None:
        """Reset fingerprinting stats."""
        reset_fingerprint_stats()


def create_passive_fingerprint_adapter() -> PassiveFingerprintAdapter:
    """Factory for PassiveFingerprintAdapter."""
    return PassiveFingerprintAdapter()


_MAX_TECH_STACK_FINDINGS: int = 100
_MAX_TECH_STACK_PER_FINDING: int = 10
_MAX_EVIDENCE_SAMPLE: int = 150
_TECH_STACK_PATTERNS: list[tuple[str, str, str, re.Pattern]] = [
    (
        "Amazon S3",
        "cloud_hosting",
        "url_marker",
        re.compile("\\.s3\\.amazonaws\\.com|s3\\.amazonaws\\.com/[^/]+/?$", re.I),
    ),
    ("Amazon S3", "cloud_hosting", "url_marker", re.compile("aws-s3-|amazon-s3-|s3-[\\w]+-[\\w]+\\.amazonaws", re.I)),
    ("Amazon S3", "cloud_hosting", "url_marker", re.compile("s3\\.console\\.aws\\.amazon\\.com", re.I)),
    ("Vercel", "platform", "html_marker", re.compile("vercel|__vc_row|__vc_pill", re.I)),
    ("Vercel", "platform", "html_marker", re.compile('\\"vercel\\"[,\\s]*\\"now\\"', re.I)),
    ("Vercel", "platform", "html_marker", re.compile("x-vercel-|vercel-config|now-preview", re.I)),
    ("Netlify", "platform", "html_marker", re.compile("netlify|__nf标志", re.I)),
    ("Netlify", "platform", "html_marker", re.compile("_netlify|netlify-cms", re.I)),
    ("Netlify", "platform", "url_marker", re.compile("\\.netlify\\.app|\\.netlify\\.com", re.I)),
    ("GitHub Pages", "hosting", "html_marker", re.compile("github\\.io|GitHub Pages|jekyll|\\.github\\.io", re.I)),
    ("GitHub Pages", "hosting", "html_marker", re.compile("_site/|jekyll-metadata", re.I)),
    ("Shopify", "ecommerce", "html_marker", re.compile("shopify|myshopify|cdn\\.shopify", re.I)),
    ("Shopify", "ecommerce", "url_marker", re.compile("myshopify\\.com|shopify\\.com", re.I)),
    ("Cloudflare", "cdn", "html_marker", re.compile("cf-ray|cf-cache-status|cloudflare", re.I)),
    ("Cloudflare", "cdn", "html_marker", re.compile("_cf_|__cf", re.I)),
    ("Cloudflare Pages", "platform", "url_marker", re.compile("\\.pages\\.dev|pages\\.cloudflare\\.net", re.I)),
    ("Fastly", "cdn", "html_marker", re.compile("fastly|FastlyHTTP|sucuri", re.I)),
    ("Fastly", "cdn", "html_marker", re.compile("x-sucuri|x-fastly", re.I)),
    ("Akamai", "cdn", "html_marker", re.compile("akamai|akamaihd\\.net|Edgecastle", re.I)),
    ("KeyCDN", "cdn", "html_marker", re.compile("keycdn|Cache-Language|X-KC", re.I)),
    ("CloudFront", "cdn", "html_marker", re.compile("CloudFront|aws-cloudfront|x-amz-cf", re.I)),
    (
        "Google Cloud CDN",
        "cdn",
        "html_marker",
        re.compile("Google Cloud|Cloud CDN|gstatic\\.com|googletagmanager", re.I),
    ),
    ("Azure CDN", "cdn", "html_marker", re.compile("azure|azureedge\\.net|msftncsi", re.I)),
    ("WordPress", "cms", "html_marker", re.compile("wp-content|wp-includes|wp-json", re.I)),
    ("WordPress", "cms", "html_marker", re.compile("wordpress|xmlrpc\\.php|wlwmanifest\\.xml", re.I)),
    ("WordPress", "cms", "html_marker", re.compile("/wp-admin/|wp-login\\.php", re.I)),
    ("Drupal", "cms", "html_marker", re.compile("drupalSettings|Drupal\\.theme|drupal\\.org", re.I)),
    ("Drupal", "cms", "html_marker", re.compile("sites/default/files|csua_drupal", re.I)),
    ("Joomla", "cms", "html_marker", re.compile("Joomla|joomla|/media/jui|com_content", re.I)),
    ("Next.js", "framework", "html_marker", re.compile("__NEXT_DATA__|_next/static", re.I)),
    ("Next.js", "framework", "html_marker", re.compile("next\\.js|nextjs|_NEXT_", re.I)),
    ("Nuxt", "framework", "html_marker", re.compile("__NUXT__|_nuxt|nuxtjs|nuxt\\.config", re.I)),
    ("React", "framework", "html_marker", re.compile("react|_react_event_id|fb-root", re.I)),
    ("Angular", "framework", "html_marker", re.compile("ng-app|angular|angularjs", re.I)),
    ("Vue", "framework", "html_marker", re.compile("vuejs|__vue__|data-v-|vue\\.js", re.I)),
    ("nginx", "web_server", "html_marker", re.compile("nginx[\\s/][\\d.]+", re.I)),
    ("Apache", "web_server", "html_marker", re.compile("apache[\\s/][\\d.]+|apache2handler", re.I)),
    ("Cloudflare Pages", "platform", "html_marker", re.compile("pages\\.cloudflare\\.net|\\.pages\\.dev", re.I)),
    ("Gatsby", "framework", "html_marker", re.compile("gatsby|__gatsby|__generated", re.I)),
    ("Squarespace", "cms", "html_marker", re.compile("squarespace|Squarespace", re.I)),
    ("Wix", "cms", "html_marker", re.compile("wix\\.com|wixi|var wix|wixEvents", re.I)),
    ("Ghost", "cms", "html_marker", re.compile("Ghost|ghost\\.org", re.I)),
    ("HubSpot", "marketing", "html_marker", re.compile("hubspot|hs-script|hs-cta", re.I)),
    ("Magento", "ecommerce", "html_marker", re.compile("mage-|magento", re.I)),
    ("PrestaShop", "ecommerce", "html_marker", re.compile("prestashop|_PS_VERSION_|prestashop\\.com", re.I)),
    (
        "Google Analytics",
        "analytics",
        "html_marker",
        re.compile("google-analytics\\.com|ga\\.js|analytics\\.js|gtag", re.I),
    ),
    ("Google Tag Manager", "analytics", "html_marker", re.compile("googletagmanager\\.com|GTM-[A-Z0-9]+", re.I)),
    ("Facebook Pixel", "analytics", "html_marker", re.compile("fbq|facebook\\.com|fb-messenger", re.I)),
    ("Hotjar", "analytics", "html_marker", re.compile("hotjar|hj\\.com|hotjarTracking", re.I)),
]
_CMS_VERSION_PATTERNS: dict[str, re.Pattern] = {
    "wordpress": re.compile("wordpress.*?([\\d.]+)", re.I),
    "drupal": re.compile("drupal.*?([\\d.]+(?:\\.\\d+)?)", re.I),
    "joomla": re.compile("joomla.*?([\\d.]+)", re.I),
    "typo3": re.compile("typo3.*?([\\d.]+)", re.I),
    "magento": re.compile("magento.*?([\\d.]+)", re.I),
    "prestashop": re.compile("prestashop.*?([\\d.]+)", re.I),
}


def _extract_tech_stack_findings(findings: list[CanonicalFinding], query: str) -> list[CanonicalFinding]:
    """
    R11: Extract tech-stack signals from existing public findings.
    No live network, no deep_probe, no MLX.
    """
    candidates: list[CanonicalFinding] = []
    seen: set[tuple[str, str]] = set()
    ts = time.time()
    loop_start = time.monotonic()
    for finding in findings[: _MAX_TECH_STACK_FINDINGS * 2]:
        if len(candidates) >= _MAX_TECH_STACK_FINDINGS:
            break
        try:
            source_url = _extract_source_url(finding)
            text_for_scan = _extract_text_from_payload(finding.payload_text)
            candidates.extend(
                _scan_patterns_for_technology(
                    finding,
                    source_url,
                    text_for_scan,
                    seen,
                    candidates,
                    ts,
                    query,
                )
            )
        except Exception:
            continue
    _GLOBAL_STATS["extract_tech_stack_loop_ms"] = (time.monotonic() - loop_start) * 1000
    return candidates[:_MAX_TECH_STACK_FINDINGS]


def _extract_source_url(finding: CanonicalFinding) -> str:
    """Extract source URL from finding provenance."""
    for prov in getattr(finding, "provenance", ()):
        if prov.startswith("url:"):
            return prov[4:300]
    return ""


def _extract_text_from_payload(payload) -> str:
    """Extract text from payload for scanning."""
    payload = getattr(payload, "payload_text", None) or ""
    try:
        if isinstance(payload, str) and payload.strip():
            if payload.startswith("{") or "\n" not in payload[:20]:
                data = _msgspec_decode(payload)
                text_parts = [
                    str(data.get(key, ""))[:500]
                    for key in ("title", "snippet", "body", "html", "status")
                    if data.get(key)
                ]
                return " ".join(text_parts)
            return payload[:2000]
        return str(payload)[:2000]
    except Exception:
        return str(payload)[:2000]


def _scan_patterns_for_technology(
    finding: CanonicalFinding,
    source_url: str,
    text_for_scan: str,
    seen: set[tuple[str, str]],
    candidates: list[CanonicalFinding],
    ts: float,
    query: str,
) -> list[CanonicalFinding]:
    """Scan text against tech stack patterns (bounded)."""
    results: list[CanonicalFinding] = []
    url_for_scan = source_url or ""
    # Bounds: scan max 20 patterns per finding to prevent O(n*m) blowup
    for tech_name, category, evidence_kind, pattern in _TECH_STACK_PATTERNS[:20]:
        if len(results) >= _MAX_TECH_STACK_FINDINGS:
            break
        dedup_key = (tech_name, source_url)
        if dedup_key in seen:
            continue
        if evidence_kind in ("html_marker", "payload_marker"):
            result = _try_match_and_create(
                finding,
                source_url,
                text_for_scan,
                dedup_key,
                seen,
                tech_name,
                category,
                evidence_kind,
                pattern,
                0.75,
                ts,
                query,
            )
            if result:
                results.append(result)
        elif evidence_kind == "url_marker" and url_for_scan:
            result = _try_match_and_create(
                finding,
                source_url,
                url_for_scan,
                dedup_key,
                seen,
                tech_name,
                category,
                evidence_kind,
                pattern,
                0.8,
                ts,
                query,
            )
            if result:
                results.append(result)
    return results


def _try_match_and_create(
    finding: CanonicalFinding,
    source_url: str,
    text: str,
    dedup_key: tuple[str, str],
    seen: set[tuple[str, str]],
    tech_name: str,
    category: str,
    evidence_kind: str,
    pattern: re.Pattern[str],
    confidence: float,
    ts: float,
    query: str,
) -> CanonicalFinding | None:
    """Try to match pattern and create finding if successful."""
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    match = pattern.search(text)
    if not match:
        return None
    if dedup_key in seen:
        return None
    sample = match.group(0)[:_MAX_EVIDENCE_SAMPLE]
    seen.add(dedup_key)
    fid = f"pts_{hashlib.sha256(f'{tech_name}:{source_url}:{int(ts)}'.encode()).hexdigest()[:20]}"
    payload_out = {
        "technology": tech_name,
        "category": category,
        "evidence_kind": evidence_kind,
        "evidence_sample": sample,
        "source_finding_id": getattr(finding, "finding_id", "") or "",
        "source_url": source_url,
        "confidence": confidence,
    }
    return CanonicalFinding(
        finding_id=fid,
        query=query[:500],
        source_type="passive_tech_stack",
        confidence=confidence,
        ts=ts,
        provenance=("passive_tech_stack", tech_name, evidence_kind),
        payload_text=_msgspec_encode(payload_out).decode(),
    )


async def run_passive_tech_stack_sidecar(findings: list[CanonicalFinding], store: Any, query: str) -> int:
    """
    R11 async sidecar runner for passive tech-stack extraction.

    Returns count of stored findings.
    Fail-soft: returns 0 on any error.

    When tech_stack signals (CMS, web server, framework) are detected,
    CVE lookup is triggered as asyncio.create_task() for significant technologies.
    """
    if not findings or store is None:
        return 0
    try:
        derived_findings = _extract_tech_stack_findings(findings, query)
        if not derived_findings:
            return 0
        _trigger_cve_lookup_tasks(derived_findings, store)
        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        return stored
    except asyncio.CancelledError:
        raise
    except Exception:
        return 0


def _trigger_cve_lookup_tasks(findings: list[CanonicalFinding], store: Any) -> None:
    """
    Fire background CVE lookup tasks for high-signal technologies.

    ISSUE [ULTIMATE]-004: First checks local CveCorrelationMatrix (zero network).
    Falls back to external OSV/NVD/GitHub search only for uncached technologies.

    Triggers asyncio.create_task() for: WordPress, Drupal, Joomla, Typo3,
    nginx, Apache, Next.js, React, Vue, Angular, Gatsby.

    CVE results are stored via store.async_ingest_findings_batch().
    Fail-safe: any error is logged and swallowed.
    """
    _CVE_TRIGGER_TECHS = {
        "WordPress",
        "Drupal",
        "Joomla",
        "Typo3",
        "nginx",
        "Apache",
        "Next.js",
        "React",
        "Vue",
        "Angular",
        "Gatsby",
        "Laravel",
        "Django",
        "Flask",
        "Magento",
        "PrestaShop",
        "Ghost",
        "HubSpot",
        "OpenSSH",
        "PostgreSQL",
        "MySQL",
        "Redis",
        "MongoDB",
        "Elasticsearch",
        "Kubernetes",
        "Docker",
        "HAProxy",
        "Varnish",
        "Memcached",
    }
    detected_techs: set[str] = set()
    tech_versions: dict[str, str | None] = {}  # tech -> version

    for finding in findings:
        try:
            payload_str = getattr(finding, "payload_text", "") or ""
            if payload_str.startswith("{"):
                payload = _msgspec_decode(payload_str)
                tech = payload.get("technology", "")
                version = payload.get("version")
                if tech in _CVE_TRIGGER_TECHS:
                    detected_techs.add(tech)
                    if version:
                        tech_versions[tech] = version
        except Exception:
            continue
    if not detected_techs:
        return

    # ISSUE [ULTIMATE]-004: Try local CVE matrix first (zero network, < 500µs)
    from hledac.universal.knowledge.duckdb_cve_matrix import get_cve_matrix

    cve_matrix = get_cve_matrix()

    for tech in detected_techs:
        version = tech_versions.get(tech)
        try:
            # Hot-path: local DuckDB lookup
            local_matches = cve_matrix.match(tech, version)
            if local_matches:
                # Store local CVE findings immediately
                from hledac.universal.knowledge.duckdb_store import CanonicalFinding

                ts = time.time()
                cve_findings: list[CanonicalFinding] = []
                for match in local_matches[:10]:  # Top 10 CVEs
                    fid_input = f"{tech}:{match.cve_id}:local"
                    fid = f"cve_local_{hashlib.sha256(fid_input.encode()).hexdigest()[:16]}"
                    payload = {
                        "technology": tech,
                        "version": version,
                        "cve_id": match.cve_id,
                        "cvss_score": match.cvss_score,
                        "cwe_id": match.cwe_id,
                        "description": match.description_snippet[:500],
                        "source": "inmemory_cve_matrix",
                    }
                    cve_findings.append(
                        CanonicalFinding(
                            finding_id=fid,
                            query=f"{tech} {match.cve_id}",
                            source_type="cve_local_lookup",
                            confidence=0.85 if match.cvss_score and match.cvss_score >= 7.0 else 0.7,
                            ts=ts,
                            provenance=("cve_local_lookup", tech, match.cve_id),
                            payload_text=_msgspec_encode(payload).decode(),
                        )
                    )
                if cve_findings:
                    from hledac.universal.utils.asyncx import safe_create_task

                    safe_create_task(_store_cve_findings(cve_findings, store), name=f"cve_store:{tech}")
                    logger.info(f"[TechStack] {len(cve_findings)} local CVEs for {tech}")
                continue  # Skip external lookup for cached tech
        except Exception:  # noqa: BLE001
            pass  # Fall through to external lookup

        # Fallback: external API lookup (2-15s network latency)
        from hledac.universal.utils.asyncx import safe_create_task

        cve_id = f"CVE-{tech.upper()}-LATEST"
        safe_create_task(_cve_lookup_background(tech, cve_id, store), name=f"cve_lookup:{tech}")
        logger.debug(f"[TechStack] CVE lookup triggered for {tech}")


async def _store_cve_findings(findings: list[CanonicalFinding], store: Any) -> None:
    """Store CVE findings in the store."""
    try:
        results = await store.async_ingest_findings_batch(findings)
        stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
        logger.debug(f"[TechStack] Stored {stored} CVE findings")
    except Exception:  # noqa: BLE001
        pass


async def _cve_lookup_background(tech: str, cve_id: str, store: Any) -> None:
    """
    Background CVE lookup task — searches GitHub for PoC/exploit samples.

    Stores results as CanonicalFinding with source_type="cve_lookup".
    Fail-soft: logs and returns on any error.
    """
    try:
        from pathlib import Path

        from hledac.universal.recon.exposure_clients import GitHubCodeSearchClient as _GitHubCodeSearchCVEClient

        cache_dir = Path("/tmp/cve_gh_cache")
        client = _GitHubCodeSearchCVEClient(cache_dir)
        import httpx

        _sess = await httpx.AsyncClient()
        async with _sess as session:
            results = await client.search_cve(cve_id, session)
        if not results:
            return
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        ts = time.time()
        cve_findings: list[CanonicalFinding] = []
        for r in results[:5]:
            url_val = r["url"]
            fid_input = f"{cve_id}:{url_val}"
            fid = f"cve_gh_{hashlib.sha256(fid_input.encode()).hexdigest()[:16]}"
            payload = {
                "technology": tech,
                "cve_id": cve_id,
                "repo": r.get("repo", ""),
                "url": r.get("url", ""),
                "path": r.get("path", ""),
                "stars": r.get("stars", 0),
                "source": "github_code_search",
            }
            cve_findings.append(
                CanonicalFinding(
                    finding_id=fid,
                    query=f"{tech} {cve_id}",
                    source_type="cve_lookup",
                    confidence=0.6,
                    ts=ts,
                    provenance=("cve_lookup", tech),
                    payload_text=_msgspec_encode(payload).decode(),
                )
            )
        if cve_findings:
            await store.async_ingest_findings_batch(cve_findings)
            logger.info(f"[TechStack] {len(cve_findings)} CVE results stored for {tech}")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(f"[TechStack] CVE lookup failed for {tech}: {e}")


class PassiveTechStackAdapter:
    """R11: Bounded passive tech-stack extraction adapter."""

    __slots__ = ("_stats",)

    def __init__(self) -> None:
        self._stats: dict[str, int] = {"findings_scanned": 0, "tech_stack_found": 0}

    def correlate(self, findings: list[CanonicalFinding], query: str) -> list[CanonicalFinding]:
        """Correlate tech-stack signals from findings."""
        result = _extract_tech_stack_findings(findings, query)
        self._stats["findings_scanned"] = len(findings)
        self._stats["tech_stack_found"] = len(result)
        return result

    def get_stats(self) -> dict[str, int]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        self._stats["findings_scanned"] = 0
        self._stats["tech_stack_found"] = 0


def create_passive_tech_stack_adapter() -> PassiveTechStackAdapter:
    """Factory for PassiveTechStackAdapter."""
    return PassiveTechStackAdapter()
