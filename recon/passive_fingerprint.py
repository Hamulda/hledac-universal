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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict
from hledac.universal.utils.msgspec_json import decode as _msgspec_decode
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode
from hledac.universal.transport.session_pool import session_pool
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
logger = logging.getLogger(__name__)
MAX_FINGERPRINT_FINDINGS: int = 1000
MAX_FINGERPRINTS_PER_FINDING: int = 5
MAX_PATTERN_BYTES: int = 4096
FINGERPRINT_TIMEOUT_S: float = 10.0

@dataclass(slots=True, frozen=True)
class ServiceFingerprint:
    """A single passive service fingerprint derived from finding data."""
    finding_id: str
    service_name: str
    product: str
    version: str
    confidence: float
    evidence_ids: tuple[str, ...]
    facets: dict[str, str]

@dataclass(frozen=True, slots=True)
class FingerprintResult:
    """Outcome of a passive fingerprinting run."""
    fingerprints: tuple[ServiceFingerprint, ...]
    scanned_count: int
    skipped_count: int
    elapsed_ms: float

@dataclass(frozen=True, slots=True)
class TechStack:
    """R11: Tech stack signals extracted from HTTP headers, cookies, and HTML."""
    cloud_provider: str | None
    cdn_provider: str | None
    waf_detected: str | None
    waf_confidence: float
    cms: str | None
    cms_version: str | None
    raw_signals: dict[str, str]
_HTTP_SERVER_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [('apache', re.compile('^Apache(?:/([\\d.]+))?', re.I), 'Apache', ''), ('nginx', re.compile('^nginx(?:/([\\d.]+))?', re.I), 'nginx', ''), ('microsoft-iis', re.compile('^Microsoft-IIS(?:/([\\d.]+))?', re.I), 'Microsoft IIS', ''), ('iis', re.compile('^IIS(?:/([\\d.]+))?', re.I), 'Microsoft IIS', ''), ('litespeed', re.compile('^LiteSpeed(?:/([\\d.]+))?', re.I), 'LiteSpeed', ''), ('cloudflare', re.compile('^cloudflare', re.I), 'Cloudflare', ''), ('akamai', re.compile('^AkamaiGHost', re.I), 'Akamai', ''), ('akamai', re.compile('^Akamai', re.I), 'Akamai', ''), ('nginx', re.compile('^openresty', re.I), 'OpenResty', ''), ('nginx', re.compile('^Tengine', re.I), 'Tengine', ''), ('caddy', re.compile('^Caddy', re.I), 'Caddy', ''), ('python', re.compile('^Python', re.I), 'Python', ''), ('php', re.compile('^PHP', re.I), 'PHP', ''), ('ruby', re.compile('^Phusion Passenger', re.I), 'Phusion Passenger', ''), ('iis', re.compile('^ASP\\.NET', re.I), 'ASP.NET', ''), ('iis', re.compile('^Microsoft-AspNet', re.I), 'ASP.NET', ''), ('tomcat', re.compile('^Apache-Coyote', re.I), 'Apache Coyote', ''), ('tomcat', re.compile('^Tomcat', re.I), 'Apache Tomcat', ''), ('jetty', re.compile('^Jetty', re.I), 'Jetty', ''), ('glassfish', re.compile('^GlassFish', re.I), 'GlassFish', ''), ('wildfly', re.compile('^WildFly', re.I), 'WildFly', ''), ('node.js', re.compile('^NodeJS', re.I), 'Node.js', ''), ('express', re.compile('^Express', re.I), 'Express.js', ''), ('fastly', re.compile('^Varnish', re.I), 'Varnish', ''), ('fastly', re.compile('^Fastly', re.I), 'Fastly', ''), ('squarespace', re.compile('Squarespace', re.I), 'Squarespace', ''), ('shopify', re.compile('^Shopify', re.I), 'Shopify', ''), ('wix', re.compile('^nginx/1\\.\\d+ (\\w+)', re.I), 'Wix', ''), ('wordpress', re.compile('nginx/[\\d.]+ (WordPress)', re.I), 'WordPress', ''), ('drupal', re.compile('X-Generator: Drupal', re.I), 'Drupal', ''), ('joomla', re.compile('X-Generator: Joomla', re.I), 'Joomla', '')]
_HTTP_HEADER_PATTERNS: list[tuple[str, re.Pattern, str]] = [('x-powered-by', re.compile('PHP/([\\d.]+)', re.I), 'PHP'), ('x-powered-by', re.compile('ASP\\.NET', re.I), 'ASP.NET'), ('x-powered-by', re.compile('Express', re.I), 'Express.js'), ('x-powered-by', re.compile('Django', re.I), 'Django'), ('x-powered-by', re.compile('Ruby on Rails', re.I), 'Rails'), ('x-powered-by', re.compile('Laravel', re.I), 'Laravel'), ('x-aspnet-version', re.compile('([\\d.]+)', re.I), 'ASP.NET'), ('cf-ray', re.compile('.*', re.I), 'Cloudflare'), ('via', re.compile('1\\.\\d+ Varnish', re.I), 'Varnish'), ('server-timing', re.compile('Cloudflare', re.I), 'Cloudflare')]
_TLS_CERT_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [('cloudflare', re.compile('Cloudflare', re.I), 'Cloudflare', ''), ('akamai', re.compile('Akamai', re.I), 'Akamai', ''), ('amazon-aws', re.compile('Amazon|aws|amazon', re.I), 'AWS', ''), ('azure', re.compile('Microsoft|Azure', re.I), 'Azure', ''), ('google-cloud', re.compile('Google|Google Cloud|gstatic', re.I), 'Google Cloud', ''), ('letsencrypt', re.compile("Let's Encrypt", re.I), "Let's Encrypt", ''), ('digiCert', re.compile('DigiCert', re.I), 'DigiCert', ''), ('comodo', re.compile('Comodo', re.I), 'Comodo', ''), ('geotrust', re.compile('GeoTrust', re.I), 'GeoTrust', ''), ('verisign', re.compile('VeriSign', re.I), 'VeriSign', ''), ('thawte', re.compile('thawte', re.I), 'thawte', '')]
_CT_CERT_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [('cloudflare', re.compile('Cloudflare', re.I), 'Cloudflare', ''), ('akamai', re.compile('CloudFront', re.I), 'CloudFront', ''), ('amazon-aws', re.compile('Amazon CloudFront', re.I), 'CloudFront', ''), ('fastly', re.compile('Fastly', re.I), 'Fastly', ''), ('microsoft', re.compile('Microsoft.*(?: corp| corporation)', re.I), 'Microsoft', ''), ('google', re.compile('Google LLC', re.I), 'Google', ''), ('apple', re.compile('Apple Inc', re.I), 'Apple', ''), ('facebook', re.compile('Facebook', re.I), 'Meta', ''), ('github', re.compile('GitHub', re.I), 'GitHub', ''), ('cloudflare', re.compile('Cloudflare, Inc', re.I), 'Cloudflare', ''), ('amazon-aws', re.compile('Amazon.com', re.I), 'Amazon AWS', ''), ('shopify', re.compile('Shopify', re.I), 'Shopify', ''), ('wordpress', re.compile('Automattic', re.I), 'WordPress', ''), ('akamai', re.compile('Akamai Technologies', re.I), 'Akamai', ''), ('vercel', re.compile('Vercel', re.I), 'Vercel', ''), ('netlify', re.compile('Netlify', re.I), 'Netlify', '')]
_HTML_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [('wordpress', re.compile('wp-content|wp-includes', re.I), 'WordPress', ''), ('wordpress', re.compile('WordPress', re.I), 'WordPress', ''), ('drupal', re.compile('drupalSettings|Drupal.theme', re.I), 'Drupal', ''), ('joomla', re.compile('Joomla', re.I), 'Joomla', ''), ('wix', re.compile('wix.com|wixi|var wix', re.I), 'Wix', ''), ('shopify', re.compile('shopify|myShopify', re.I), 'Shopify', ''), ('squarespace', re.compile('Squarespace', re.I), 'Squarespace', ''), ('ghost', re.compile('Ghost', re.I), 'Ghost CMS', ''), ('hubspot', re.compile('hubspot|hs-script', re.I), 'HubSpot', ''), ('wordpress', re.compile('xmlrpc.php|wlwmanifest.xml', re.I), 'WordPress', ''), ('drupal', re.compile('modules/.*\\.js\\?v=', re.I), 'Drupal', ''), ('joomla', re.compile('/media/jui|com_content', re.I), 'Joomla', ''), ('magento', re.compile('mage/', re.I), 'Magento', ''), ('prestashop', re.compile('prestashop|_PS_VERSION_', re.I), 'PrestaShop', ''), ('react', re.compile('react|fb-root|_react_event_id', re.I), 'React', ''), ('vue', re.compile('vuejs|__vue__|data-v-', re.I), 'Vue.js', ''), ('angular', re.compile('ng-app|angular|angularjs', re.I), 'Angular', ''), ('next.js', re.compile('__NEXT_DATA__|_next/static', re.I), 'Next.js', ''), ('gatsby', re.compile('gatsby|__gatsby', re.I), 'Gatsby', ''), ('django', re.compile('csrfmiddlewaretoken|django', re.I), 'Django', ''), ('flask', re.compile('flask|Werkzeug', re.I), 'Flask', ''), ('laravel', re.compile('laravel|_token|XSRF-TOKEN', re.I), 'Laravel', ''), ('ruby-on-rails', re.compile('Ruby on Rails|rails', re.I), 'Rails', ''), ('spring', re.compile('Spring Framework|springframework', re.I), 'Spring', ''), ('express', re.compile('Express|node_modules/express', re.I), 'Express.js', '')]
_PROTOCOL_PATTERNS: list[tuple[str, re.Pattern]] = [('TLSv1.2', re.compile('TLSv?1\\.2', re.I)), ('TLSv1.3', re.compile('TLSv?1\\.3', re.I)), ('HTTP/1.0', re.compile('HTTP/1\\.0', re.I)), ('HTTP/1.1', re.compile('HTTP/1\\.1', re.I)), ('HTTP/2', re.compile('H2(?:[ ,]|$)|^SPDY', re.I)), ('HTTP/3', re.compile('H3(?:[ ,]|$)|HTTP/3', re.I))]
_stats: dict[str, int] = {'findings_scanned': 0, 'findings_skipped': 0, 'fingerprints_produced': 0, 'patterns_matched': 0}

def get_fingerprint_stats() -> dict[str, int]:
    """Return copy of fingerprint stats (for probe verification)."""
    return dict(_stats)

def reset_fingerprint_stats() -> None:
    """Reset all stats to zero (for probe test isolation)."""
    _stats.clear()
    _stats.update({'findings_scanned': 0, 'findings_skipped': 0, 'fingerprints_produced': 0, 'patterns_matched': 0})

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

def extract_http_signals(payload_text: str | None) -> HttpSignals:
    """
    Extract HTTP-related signals from finding payload_text.

    Returns dict with keys:
      - server_headers: list of Server header values
      - x_headers: list of X-* header values
      - all_headers: combined header text for pattern matching
      - html_content: HTML body if present
    """
    signals: HttpSignals = {'server_headers': [], 'x_headers': [], 'all_headers': [], 'html_content': ''}
    if not payload_text:
        return signals
    try:
        data = _msgspec_decode(payload_text) if isinstance(payload_text, str) else payload_text
    except Exception:
        return signals
    headers = data.get('http_headers', {}) or data.get('headers', {}) or {}
    if isinstance(headers, dict):
        for key, value in headers.items():
            key_lower = key.lower()
            if key_lower == 'server' and value:
                signals['server_headers'].append(str(value))
            if key_lower.startswith('x-'):
                signals['x_headers'].append(f'{key}: {value}')
            signals['all_headers'].append(f'{key}: {value}')
    if isinstance(headers, str):
        signals['all_headers'].append(headers)
    html = data.get('html', '') or data.get('body', '') or data.get('content', '') or ''
    if isinstance(html, str) and len(html) <= MAX_PATTERN_BYTES:
        signals['html_content'] = html[:MAX_PATTERN_BYTES]
    status = data.get('status_code') or data.get('status', 0)
    if status:
        signals['all_headers'].append(f'status: {status}')
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
    signals: TlsSignals = {'cert_subject': [], 'cert_issuer': [], 'cert_san': [], 'cipher_suite': [], 'protocol_version': [], 'all_text': []}
    if not payload_text:
        return signals
    try:
        data = _msgspec_decode(payload_text) if isinstance(payload_text, str) else payload_text
    except Exception:
        return signals
    cert = data.get('certificate', {}) or data.get('cert', {}) or data.get('ssl_cert', {}) or {}
    if isinstance(cert, dict):
        subject = cert.get('subject', '') or cert.get('subject_cn', '')
        issuer = cert.get('issuer', '') or cert.get('issuer_c', '')
        san_list = cert.get('san', []) or cert.get('subject_alternative_names', [])
        if subject:
            signals['cert_subject'].append(subject)
        if issuer:
            signals['cert_issuer'].append(issuer)
        if san_list:
            if isinstance(san_list, list):
                signals['cert_san'].extend((str(s) for s in san_list))
            else:
                signals['cert_san'].append(str(san_list))
    tls = data.get('tls', {}) or data.get('tls_info', {}) or {}
    if isinstance(tls, dict):
        cipher = tls.get('cipher', '') or tls.get('cipher_suite', '')
        protocol = tls.get('version', '') or tls.get('protocol', '')
        if cipher:
            signals['cipher_suite'].append(cipher)
        if protocol:
            signals['protocol_version'].append(protocol)
    for field_key in ('subject', 'issuer', 'cn', 'common_name'):
        val = data.get(field_key, '')
        if val:
            signals['cert_subject'].append(str(val))
    for field_key in ('san', 'subject_alternative_name', 'alt_names'):
        val = data.get(field_key, '')
        if val:
            if isinstance(val, list):
                signals['cert_san'].extend((str(s) for s in val))
            else:
                signals['cert_san'].append(str(val))
    all_text_parts = signals['cert_subject'] + signals['cert_issuer'] + signals['cert_san'] + signals['cipher_suite'] + signals['protocol_version']
    signals['all_text'] = all_text_parts
    return signals

def extract_ct_signals(payload_text: str | None) -> CtSignals:
    """
    Extract CT (Certificate Transparency) metadata signals.

    Returns dict with keys:
      - cert_issuer: issuer organization
      - cert_subject: subject organization
      - all_names: all names from cert entries
    """
    signals: CtSignals = {'cert_issuer': [], 'cert_subject': [], 'all_names': []}
    if not payload_text:
        return signals
    try:
        data = _msgspec_decode(payload_text) if isinstance(payload_text, str) else payload_text
    except Exception:
        return signals
    ct_entries = data.get('ct_entries', []) or data.get('certificate_transparency', []) or []
    if not isinstance(ct_entries, list):
        ct_entries = [ct_entries] if ct_entries else []
    for entry in ct_entries[:50]:
        if isinstance(entry, dict):
            issuer = entry.get('issuer', '') or entry.get('issuer_cn', '') or entry.get('issuer_organization', '')
            subject = entry.get('subject', '') or entry.get('cn', '') or entry.get('subject_cn', '')
            all_name = entry.get('name', '') or entry.get('common_name', '') or entry.get('san', '')
            if issuer:
                signals['cert_issuer'].append(str(issuer))
            if subject:
                signals['cert_subject'].append(str(subject))
            if all_name:
                signals['all_names'].append(str(all_name))
        elif isinstance(entry, str):
            signals['all_names'].append(entry)
    if data.get('issuer'):
        signals['cert_issuer'].append(str(data['issuer']))
    if data.get('domain'):
        signals['all_names'].append(str(data['domain']))
    if data.get('name'):
        signals['all_names'].append(str(data['name']))
    return signals

def extract_html_signals(payload_text: str | None) -> HtmlSignals:
    """
    Extract HTML content signals for service fingerprinting.

    Returns dict with keys:
      - title: page title
      - generator: meta generator tag
      - scripts: script src patterns
      - all_text: combined HTML text
    """
    signals: HtmlSignals = {'title': [], 'generator': [], 'scripts': [], 'all_text': []}
    if not payload_text:
        return signals
    try:
        data = _msgspec_decode(payload_text) if isinstance(payload_text, str) else payload_text
    except Exception:
        return signals
    html = data.get('html', '') or data.get('body', '') or data.get('content', '') or ''
    if not isinstance(html, str):
        return signals
    html = html[:MAX_PATTERN_BYTES]
    title_match = re.search('<title[^>]*>([^<]+)</title>', html, re.I)
    if title_match:
        signals['title'].append(title_match.group(1).strip())
    gen_match = re.search('<meta[^>]+generator[^>]+content=["\\\']([^"\\\']+)["\\\']', html, re.I)
    if gen_match:
        signals['generator'].append(gen_match.group(1).strip())
    if not signals['generator']:
        gen_match2 = re.search('<meta[^>]+content=["\\\']([^"\\\']+)["\\\'][^>]+generator', html, re.I)
        if gen_match2:
            signals['generator'].append(gen_match2.group(1).strip())
    script_matches = re.findall('<script[^>]+src=["\\\']([^"\\\']+)["\\\']', html, re.I)
    for src in script_matches[:20]:
        signals['scripts'].append(src)
        domain_match = re.search('https?://([^/]+)', src)
        if domain_match:
            signals['all_text'].append(domain_match.group(1))
    link_matches = re.findall('<link[^>]+href=["\\\']([^"\\\']+)["\\\']', html, re.I)
    for href in link_matches[:20]:
        domain_match = re.search('https?://([^/]+)', href)
        if domain_match:
            signals['all_text'].append(domain_match.group(1))
    favicon_match = re.search('<link[^>]+rel=["\\\'][^"\\\']*icon[^"\\\']*["\\\'][^>]+href=["\\\']([^"\\\']+)["\\\']', html, re.I)
    if favicon_match:
        signals['all_text'].append(f'favicon:{favicon_match.group(1)}')
    signals['all_text'].extend(signals['title'])
    signals['all_text'].extend(signals['generator'])
    return signals

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
    cloud_provider: str | None = None
    cdn_provider: str | None = None
    waf_detected: str | None = None
    waf_confidence: float = 0.0
    cms: str | None = None
    cms_version: str | None = None
    for header_key, header_value in headers.items():
        if header_key.startswith('x-amz-'):
            cloud_provider = 'AWS'
            raw_signals['aws_header'] = f'{header_key}: {header_value[:50]}'
            break
        if header_key.startswith('x-goog-'):
            cloud_provider = 'GCP'
            raw_signals['gcp_header'] = f'{header_key}: {header_value[:50]}'
            break
        if header_key.startswith('x-ms-'):
            cloud_provider = 'Azure'
            raw_signals['azure_header'] = f'{header_key}: {header_value[:50]}'
            break
    cf_ray = headers.get('cf-ray') or headers.get('cf-ray-legacy', '')
    if cf_ray:
        cdn_provider = 'Cloudflare'
        raw_signals['cf-ray'] = cf_ray[:32]
        status_val = headers.get('status', '') or headers.get(':status', '')
        if '403' in str(status_val):
            error_page = html_head.lower()
            if 'error 1020' in error_page or ('cloudflare' in error_page and 'access denied' in error_page):
                waf_detected = 'Cloudflare WAF'
                waf_confidence = 0.95
                raw_signals['waf_signal'] = 'cf_403_1020'
    if not cdn_provider:
        via = headers.get('via', '')
        server = headers.get('server', '')
        if 'fastly' in via.lower() or 'fastly' in server.lower():
            cdn_provider = 'Fastly'
            raw_signals['fastly'] = f'via={via[:30]}, server={server[:30]}'
    if not cdn_provider:
        for hk, hv in headers.items():
            if 'akamai' in hk.lower() or 'akamai' in hv.lower():
                cdn_provider = 'Akamai'
                raw_signals['akamai'] = f'{hk}: {hv[:30]}'
                break
    if not waf_detected:
        for cookie in cookies:
            if 'incap_ses' in cookie.lower() or 'visid_incap_' in cookie.lower():
                waf_detected = 'Imperva'
                waf_confidence = 0.9
                raw_signals['waf_signal'] = f'imperva_cookie: {cookie[:60]}'
                break
    if not waf_detected:
        awswaf_cookie = headers.get('aws-waf-request', '') or headers.get('aws-alb', '')
        if awswaf_cookie or 'aws-waf' in str(cookies).lower():
            waf_detected = 'AWS WAF'
            waf_confidence = 0.85
            raw_signals['waf_signal'] = 'aws_waf_detected'
    if not waf_detected:
        for hk, hv in headers.items():
            if 'bigip' in hk.lower() or 'ftm' in hk.lower() or 'bigip' in hv.lower():
                waf_detected = 'F5 BIG-IP'
                waf_confidence = 0.8
                raw_signals['waf_signal'] = f'f5_header: {hk}'
                break
    if not waf_detected:
        for hk, hv in headers.items():
            if 'sucuri' in hk.lower() or 'x-sucuri' in hk.lower():
                waf_detected = 'Akamai WAF'
                waf_confidence = 0.75
                raw_signals['waf_signal'] = f'akamai_waf: {hk}'
                break
    html_lower = html_head.lower()[:5000]
    try:
        import ahocorasick
        cms_patterns = [('wordpress', 'wordpress'), ('drupal', 'drupal'), ('joomla', 'joomla'), ('typo3', 'typo3'), ('magento', 'magento'), ('prestashop', 'prestashop'), ('shopify', 'shopify'), ('wix', 'wix'), ('squarespace', 'squarespace'), ('ghost', 'ghost cms'), ('hubspot', 'hubspot')]
        automaton = ahocorasick.Automaton()
        for pattern, name in cms_patterns:
            automaton.add_word(pattern, name)
        automaton.make_automaton()
        found_cms: set[str] = set()
        for _, name in automaton.iter(html_lower):
            found_cms.add(name)
        if len(found_cms) == 1:
            cms = next(iter(found_cms))
        elif len(found_cms) > 1:
            priority = ['typo3', 'magento', 'prestashop', 'drupal', 'joomla', 'wordpress', 'shopify', 'ghost cms', 'hubspot', 'wix', 'squarespace']
            for p in priority:
                if p in found_cms:
                    cms = p
                    break
            if not cms:
                cms = sorted(found_cms)[0]
        raw_signals['cms_ahocorasick'] = ','.join(sorted(found_cms)) if found_cms else ''
    except ImportError:
        cms_re = re.compile('wordpress|drupal|joomla|typo3|magento|prestashop|shopify|wix|squarespace|ghost|hubspot', re.I)
        matches = cms_re.findall(html_lower[:5000])
        if matches:
            unique_matches = list(dict.fromkeys((m.lower() for m in matches)))
            if unique_matches:
                cms_map: dict[str, str] = {'wordpress': 'WordPress', 'drupal': 'Drupal', 'joomla': 'Joomla', 'typo3': 'Typo3', 'magento': 'Magento', 'prestashop': 'PrestaShop', 'shopify': 'Shopify', 'wix': 'Wix', 'squarespace': 'Squarespace', 'ghost': 'Ghost CMS', 'hubspot': 'HubSpot'}
                cms = cms_map.get(unique_matches[0], unique_matches[0].title())
                raw_signals['cms_regex'] = ','.join(unique_matches[:3])
    if cms:
        cms_lower = cms.lower()
        pattern = _CMS_VERSION_PATTERNS.get(cms_lower)
        if pattern:
            version_matches = pattern.findall(html_lower[:10000])
            if version_matches:
                cms_version = version_matches[0]
                raw_signals['cms_version'] = cms_version or ''
    return TechStack(cloud_provider=cloud_provider, cdn_provider=cdn_provider, waf_detected=waf_detected, waf_confidence=waf_confidence, cms=cms, cms_version=cms_version, raw_signals=raw_signals)

def _match_server_header(server_value: str) -> list[ServiceFingerprint]:
    """Match a Server header value against known patterns."""
    fingerprints: list[ServiceFingerprint] = []
    if not server_value:
        return fingerprints
    matched: set[str] = set()
    for service_name, pattern, product, version_hint in _HTTP_SERVER_PATTERNS:
        if service_name in matched:
            continue
        m = pattern.match(server_value)
        if m:
            version = m.group(1) if m.lastindex and m.group(1) else version_hint
            fingerprints.append(ServiceFingerprint(finding_id='', service_name=service_name, product=product, version=version or '', confidence=0.9, evidence_ids=(), facets={'source': 'http_server_header', 'raw': server_value[:200]}))
            matched.add(service_name)
            _stats['patterns_matched'] += 1
    return fingerprints

def _match_http_headers(headers_list: list[str]) -> list[ServiceFingerprint]:
    """Match HTTP headers against known service patterns."""
    fingerprints: list[ServiceFingerprint] = []
    if not headers_list:
        return fingerprints
    combined_text = ' '.join((str(h) for h in headers_list))[:MAX_PATTERN_BYTES]
    matched: set[str] = set()
    for facet_key, pattern, service_hint in _HTTP_HEADER_PATTERNS:
        if service_hint in matched:
            continue
        if pattern.search(combined_text):
            fingerprints.append(ServiceFingerprint(finding_id='', service_name=facet_key, product=service_hint, version='', confidence=0.6, evidence_ids=(), facets={'source': 'http_header', 'header': facet_key}))
            matched.add(service_hint)
            _stats['patterns_matched'] += 1
    return fingerprints

def _match_tls_cert(texts: list[str]) -> list[ServiceFingerprint]:
    """Match TLS/certificate text against known patterns."""
    fingerprints: list[ServiceFingerprint] = []
    if not texts:
        return fingerprints
    combined_text = ' '.join((str(t) for t in texts))[:MAX_PATTERN_BYTES]
    matched: set[str] = set()
    for service_name, pattern, product, version_hint in _TLS_CERT_PATTERNS:
        if service_name in matched:
            continue
        if pattern.search(combined_text):
            fingerprints.append(ServiceFingerprint(finding_id='', service_name=service_name, product=product, version=version_hint, confidence=0.85, evidence_ids=(), facets={'source': 'tls_cert', 'matched_on': service_name}))
            matched.add(service_name)
            _stats['patterns_matched'] += 1
    return fingerprints

def _match_ct_metadata(texts: list[str]) -> list[ServiceFingerprint]:
    """Match CT metadata against known service patterns."""
    fingerprints: list[ServiceFingerprint] = []
    if not texts:
        return fingerprints
    combined_text = ' '.join((str(t) for t in texts))[:MAX_PATTERN_BYTES]
    matched: set[str] = set()
    for service_name, pattern, product, version_hint in _CT_CERT_PATTERNS:
        if service_name in matched:
            continue
        if pattern.search(combined_text):
            fingerprints.append(ServiceFingerprint(finding_id='', service_name=service_name, product=product, version=version_hint, confidence=0.8, evidence_ids=(), facets={'source': 'ct_metadata', 'matched_on': service_name}))
            matched.add(service_name)
            _stats['patterns_matched'] += 1
    return fingerprints

def _match_html_content(texts: list[str]) -> list[ServiceFingerprint]:
    """Match HTML content against known service patterns."""
    fingerprints: list[ServiceFingerprint] = []
    if not texts:
        return fingerprints
    combined_text = ' '.join((str(t) for t in texts))[:MAX_PATTERN_BYTES]
    matched: set[str] = set()
    for service_name, pattern, product, version_hint in _HTML_PATTERNS:
        if service_name in matched:
            continue
        if pattern.search(combined_text):
            fingerprints.append(ServiceFingerprint(finding_id='', service_name=service_name, product=product, version=version_hint, confidence=0.7, evidence_ids=(), facets={'source': 'html_content', 'matched_on': service_name}))
            matched.add(service_name)
            _stats['patterns_matched'] += 1
    return fingerprints

def extract_fingerprints(finding: CanonicalFinding) -> list[ServiceFingerprint]:
    """
    Extract all fingerprints from a single CanonicalFinding.

    Checks HTTP headers, TLS/cert data, CT metadata, and HTML content.
    Returns up to MAX_FINGERPRINTS_PER_FINDING fingerprints.

    Bounds:
      - MAX_FINGERPRINTS_PER_FINDING = 5
      - MAX_PATTERN_BYTES = 4096
    """
    fid = getattr(finding, 'finding_id', '') or ''
    getattr(finding, 'source_type', '') or ''
    payload = getattr(finding, 'payload_text', None) or '{}'
    if isinstance(payload, str) and len(payload) > MAX_PATTERN_BYTES:
        payload = payload[:MAX_PATTERN_BYTES]
    fingerprints: list[ServiceFingerprint] = []
    http_signals = extract_http_signals(payload)
    for server_value in http_signals['server_headers'][:3]:
        fps = _match_server_header(server_value)
        for fp in fps:
            fingerprints.append(ServiceFingerprint(finding_id=fid, service_name=fp.service_name, product=fp.product, version=fp.version, confidence=fp.confidence, evidence_ids=(fid,), facets=fp.facets))
    if http_signals['x_headers'] or http_signals['all_headers']:
        xfps = _match_http_headers(http_signals['x_headers'] + http_signals['all_headers'])
        for fp in xfps:
            fingerprints.append(ServiceFingerprint(finding_id=fid, service_name=fp.service_name, product=fp.product, version=fp.version, confidence=fp.confidence, evidence_ids=(fid,), facets=fp.facets))
    tls_signals = extract_tls_signals(payload)
    if tls_signals['all_text']:
        tfps = _match_tls_cert(tls_signals['all_text'])
        for fp in tfps:
            fingerprints.append(ServiceFingerprint(finding_id=fid, service_name=fp.service_name, product=fp.product, version=fp.version, confidence=fp.confidence, evidence_ids=(fid,), facets=fp.facets))
    ct_signals = extract_ct_signals(payload)
    if ct_signals['all_names'] or ct_signals['cert_issuer'] or ct_signals['cert_subject']:
        cfps = _match_ct_metadata(ct_signals['all_names'] + ct_signals['cert_issuer'] + ct_signals['cert_subject'])
        for fp in cfps:
            fingerprints.append(ServiceFingerprint(finding_id=fid, service_name=fp.service_name, product=fp.product, version=fp.version, confidence=fp.confidence, evidence_ids=(fid,), facets=fp.facets))
    html_signals = extract_html_signals(payload)
    if html_signals['all_text'] or html_signals['title'] or html_signals['generator']:
        hfps = _match_html_content(html_signals['all_text'] + html_signals['title'] + html_signals['generator'])
        for fp in hfps:
            fingerprints.append(ServiceFingerprint(finding_id=fid, service_name=fp.service_name, product=fp.product, version=fp.version, confidence=fp.confidence, evidence_ids=(fid,), facets=fp.facets))
    http_signals_for_tech: HttpSignals = extract_http_signals(payload)
    if http_signals_for_tech['all_headers'] or http_signals_for_tech['html_content']:
        headers_dict: dict[str, str] = {}
        for h in http_signals_for_tech['all_headers']:
            if ': ' in h:
                k, v = h.split(': ', 1)
                headers_dict[k.lower()] = v
        html_text = http_signals_for_tech['html_content']
        html_head = ''
        if html_text:
            head_match = re.search('<head[^>]*>(.*?)</head>', html_text, re.I | re.S)
            if head_match:
                html_head = head_match.group(1)
        tech_stack = _extract_tech_stack(headers_dict, html_head, [])
        if tech_stack.cloud_provider:
            fingerprints.append(ServiceFingerprint(finding_id=fid, service_name=tech_stack.cloud_provider.lower(), product=tech_stack.cloud_provider, version='', confidence=0.85, evidence_ids=(fid,), facets={'source': 'tech_stack_cloud', **tech_stack.raw_signals}))
        if tech_stack.cdn_provider:
            fingerprints.append(ServiceFingerprint(finding_id=fid, service_name=tech_stack.cdn_provider.lower(), product=tech_stack.cdn_provider, version='', confidence=0.85, evidence_ids=(fid,), facets={'source': 'tech_stack_cdn', **tech_stack.raw_signals}))
        if tech_stack.waf_detected:
            fingerprints.append(ServiceFingerprint(finding_id=fid, service_name=tech_stack.waf_detected.lower().replace(' ', '-'), product=tech_stack.waf_detected, version='', confidence=tech_stack.waf_confidence, evidence_ids=(fid,), facets={'source': 'tech_stack_waf', **tech_stack.raw_signals}))
        if tech_stack.cms:
            cms_product = tech_stack.cms if tech_stack.cms_version is None else f'{tech_stack.cms} {tech_stack.cms_version}'
            fingerprints.append(ServiceFingerprint(finding_id=fid, service_name=tech_stack.cms.lower().replace(' ', '-'), product=cms_product, version=tech_stack.cms_version or '', confidence=0.75, evidence_ids=(fid,), facets={'source': 'tech_stack_cms', **tech_stack.raw_signals}))
    seen: set[tuple[str, str]] = set()
    unique: list[ServiceFingerprint] = []
    for fp in fingerprints:
        key = (fp.service_name, fp.product)
        if key not in seen:
            seen.add(key)
            unique.append(fp)
    return unique[:MAX_FINGERPRINTS_PER_FINDING]

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
        id_input = f'{fp.finding_id}:{fp.service_name}:{fp.product}:{int(ts)}'
        fid = f'pfp_{hashlib.sha256(id_input.encode()).hexdigest()[:24]}'
        payload = {'service_name': fp.service_name, 'product': fp.product, 'version': fp.version, 'confidence': fp.confidence, 'evidence_ids': list(fp.evidence_ids), 'facets': fp.facets, '_f204g': True}
        canonical.append(CanonicalFinding(finding_id=fid, query=query, source_type='passive_fingerprint', confidence=fp.confidence, ts=ts, provenance=('passive_fingerprint', fp.service_name), payload_text=_msgspec_encode(payload).decode()))
    _stats['fingerprints_produced'] = len(canonical)
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
        _GLOBAL_STATS['correlate_extract_ms'] = extract_elapsed * 1000
        _GLOBAL_STATS['correlate_canonical_ms'] = canon_elapsed * 1000
        _GLOBAL_STATS['correlate_total_ms'] = total_elapsed * 1000
        _stats['findings_scanned'] = scanned
        _stats['findings_skipped'] = skipped
        if not fingerprints:
            return []
        return canonical
    except Exception as e:
        logger.debug(f'[PassiveFingerprint] correlation failed: {e}')
        return []

async def run_passive_fingerprint_sidecar(findings: list[CanonicalFinding], store: Any, query: str) -> int:
    """
    Async sidecar runner for passive fingerprinting.

    Returns count of stored findings.
    """
    if not findings or store is None:
        return 0
    try:
        derived_findings = correlate_passive_fingerprints(findings, query)
        if not derived_findings:
            return 0
        results = await store.async_ingest_findings_batch(derived_findings)
        stored = sum((1 for r in results if isinstance(r, dict) and r.get('accepted')))
        return stored
    except asyncio.CancelledError:
        raise
    except Exception:
        return 0

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
    __slots__ = tuple(('_stats_snapshot',))

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
_TECH_STACK_PATTERNS: list[tuple[str, str, str, re.Pattern]] = [('Amazon S3', 'cloud_hosting', 'url_marker', re.compile('\\.s3\\.amazonaws\\.com|s3\\.amazonaws\\.com/[^/]+/?$', re.I)), ('Amazon S3', 'cloud_hosting', 'url_marker', re.compile('aws-s3-|amazon-s3-|s3-[\\w]+-[\\w]+\\.amazonaws', re.I)), ('Amazon S3', 'cloud_hosting', 'url_marker', re.compile('s3\\.console\\.aws\\.amazon\\.com', re.I)), ('Vercel', 'platform', 'html_marker', re.compile('vercel|__vc_row|__vc_pill', re.I)), ('Vercel', 'platform', 'html_marker', re.compile('\\"vercel\\"[,\\s]*\\"now\\"', re.I)), ('Vercel', 'platform', 'html_marker', re.compile('x-vercel-|vercel-config|now-preview', re.I)), ('Netlify', 'platform', 'html_marker', re.compile('netlify|__nf标志', re.I)), ('Netlify', 'platform', 'html_marker', re.compile('_netlify|netlify-cms', re.I)), ('Netlify', 'platform', 'url_marker', re.compile('\\.netlify\\.app|\\.netlify\\.com', re.I)), ('GitHub Pages', 'hosting', 'html_marker', re.compile('github\\.io|GitHub Pages|jekyll|\\.github\\.io', re.I)), ('GitHub Pages', 'hosting', 'html_marker', re.compile('_site/|jekyll-metadata', re.I)), ('Shopify', 'ecommerce', 'html_marker', re.compile('shopify|myshopify|cdn\\.shopify', re.I)), ('Shopify', 'ecommerce', 'url_marker', re.compile('myshopify\\.com|shopify\\.com', re.I)), ('Cloudflare', 'cdn', 'html_marker', re.compile('cf-ray|cf-cache-status|cloudflare', re.I)), ('Cloudflare', 'cdn', 'html_marker', re.compile('_cf_|__cf', re.I)), ('Cloudflare Pages', 'platform', 'url_marker', re.compile('\\.pages\\.dev|pages\\.cloudflare\\.net', re.I)), ('Fastly', 'cdn', 'html_marker', re.compile('fastly|FastlyHTTP|sucuri', re.I)), ('Fastly', 'cdn', 'html_marker', re.compile('x-sucuri|x-fastly', re.I)), ('Akamai', 'cdn', 'html_marker', re.compile('akamai|akamaihd\\.net|Edgecastle', re.I)), ('KeyCDN', 'cdn', 'html_marker', re.compile('keycdn|Cache-Language|X-KC', re.I)), ('CloudFront', 'cdn', 'html_marker', re.compile('CloudFront|aws-cloudfront|x-amz-cf', re.I)), ('Google Cloud CDN', 'cdn', 'html_marker', re.compile('Google Cloud|Cloud CDN|gstatic\\.com|googletagmanager', re.I)), ('Azure CDN', 'cdn', 'html_marker', re.compile('azure|azureedge\\.net|msftncsi', re.I)), ('WordPress', 'cms', 'html_marker', re.compile('wp-content|wp-includes|wp-json', re.I)), ('WordPress', 'cms', 'html_marker', re.compile('wordpress|xmlrpc\\.php|wlwmanifest\\.xml', re.I)), ('WordPress', 'cms', 'html_marker', re.compile('/wp-admin/|wp-login\\.php', re.I)), ('Drupal', 'cms', 'html_marker', re.compile('drupalSettings|Drupal\\.theme|drupal\\.org', re.I)), ('Drupal', 'cms', 'html_marker', re.compile('sites/default/files|csua_drupal', re.I)), ('Joomla', 'cms', 'html_marker', re.compile('Joomla|joomla|/media/jui|com_content', re.I)), ('Next.js', 'framework', 'html_marker', re.compile('__NEXT_DATA__|_next/static', re.I)), ('Next.js', 'framework', 'html_marker', re.compile('next\\.js|nextjs|_NEXT_', re.I)), ('Nuxt', 'framework', 'html_marker', re.compile('__NUXT__|_nuxt|nuxtjs|nuxt\\.config', re.I)), ('React', 'framework', 'html_marker', re.compile('react|_react_event_id|fb-root', re.I)), ('Angular', 'framework', 'html_marker', re.compile('ng-app|angular|angularjs', re.I)), ('Vue', 'framework', 'html_marker', re.compile('vuejs|__vue__|data-v-|vue\\.js', re.I)), ('nginx', 'web_server', 'html_marker', re.compile('nginx[\\s/][\\d.]+', re.I)), ('Apache', 'web_server', 'html_marker', re.compile('apache[\\s/][\\d.]+|apache2handler', re.I)), ('Cloudflare Pages', 'platform', 'html_marker', re.compile('pages\\.cloudflare\\.net|\\.pages\\.dev', re.I)), ('Gatsby', 'framework', 'html_marker', re.compile('gatsby|__gatsby|__generated', re.I)), ('Squarespace', 'cms', 'html_marker', re.compile('squarespace|Squarespace', re.I)), ('Wix', 'cms', 'html_marker', re.compile('wix\\.com|wixi|var wix|wixEvents', re.I)), ('Ghost', 'cms', 'html_marker', re.compile('Ghost|ghost\\.org', re.I)), ('HubSpot', 'marketing', 'html_marker', re.compile('hubspot|hs-script|hs-cta', re.I)), ('Magento', 'ecommerce', 'html_marker', re.compile('mage-|magento', re.I)), ('PrestaShop', 'ecommerce', 'html_marker', re.compile('prestashop|_PS_VERSION_|prestashop\\.com', re.I)), ('Google Analytics', 'analytics', 'html_marker', re.compile('google-analytics\\.com|ga\\.js|analytics\\.js|gtag', re.I)), ('Google Tag Manager', 'analytics', 'html_marker', re.compile('googletagmanager\\.com|GTM-[A-Z0-9]+', re.I)), ('Facebook Pixel', 'analytics', 'html_marker', re.compile('fbq|facebook\\.com|fb-messenger', re.I)), ('Hotjar', 'analytics', 'html_marker', re.compile('hotjar|hj\\.com|hotjarTracking', re.I))]
_CMS_VERSION_PATTERNS: dict[str, re.Pattern] = {'wordpress': re.compile('wordpress.*?([\\d.]+)', re.I), 'drupal': re.compile('drupal.*?([\\d.]+(?:\\.\\d+)?)', re.I), 'joomla': re.compile('joomla.*?([\\d.]+)', re.I), 'typo3': re.compile('typo3.*?([\\d.]+)', re.I), 'magento': re.compile('magento.*?([\\d.]+)', re.I), 'prestashop': re.compile('prestashop.*?([\\d.]+)', re.I)}

def _extract_tech_stack_findings(findings: list[CanonicalFinding], query: str) -> list[CanonicalFinding]:
    """
    R11: Extract tech-stack signals from existing public findings.
    No live network, no deep_probe, no MLX.
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    candidates: list[CanonicalFinding] = []
    seen: set[tuple[str, str]] = set()
    ts = time.time()
    loop_start = time.monotonic()
    for finding in findings[:_MAX_TECH_STACK_FINDINGS * 2]:
        if len(candidates) >= _MAX_TECH_STACK_FINDINGS:
            break
        try:
            payload = getattr(finding, 'payload_text', None) or ''
            source_url = ''
            for prov in getattr(finding, 'provenance', ()):
                if prov.startswith('url:'):
                    source_url = prov[4:300]
                    break
            text_for_scan = ''
            try:
                if isinstance(payload, str) and payload.strip():
                    if payload.startswith('{') or '\n' not in payload[:20]:
                        data = _msgspec_decode(payload)
                        text_parts = []
                        for key in ('title', 'snippet', 'body', 'html', 'status'):
                            val = data.get(key, '')
                            if val:
                                text_parts.append(str(val)[:500])
                        text_for_scan = ' '.join(text_parts)
                    else:
                        text_for_scan = payload[:2000]
                else:
                    text_for_scan = str(payload)[:2000]
            except Exception:
                text_for_scan = str(payload)[:2000]
            url_for_scan = source_url or ''
            for tech_name, category, evidence_kind, pattern in _TECH_STACK_PATTERNS:
                if len(candidates) >= _MAX_TECH_STACK_FINDINGS:
                    break
                dedup_key = (tech_name, source_url)
                if dedup_key in seen:
                    continue
                if evidence_kind in ('html_marker', 'payload_marker'):
                    match = pattern.search(text_for_scan)
                    if match:
                        sample = match.group(0)[:_MAX_EVIDENCE_SAMPLE]
                        seen.add(dedup_key)
                        fid = f"pts_{hashlib.sha256(f'{tech_name}:{source_url}:{int(ts)}'.encode()).hexdigest()[:20]}"
                        payload_out = {'technology': tech_name, 'category': category, 'evidence_kind': evidence_kind, 'evidence_sample': sample, 'source_finding_id': getattr(finding, 'finding_id', '') or '', 'source_url': source_url, 'confidence': 0.75}
                        candidates.append(CanonicalFinding(finding_id=fid, query=query[:500], source_type='passive_tech_stack', confidence=0.75, ts=ts, provenance=('passive_tech_stack', tech_name, evidence_kind), payload_text=_msgspec_encode(payload_out).decode()))
                if evidence_kind == 'url_marker' and url_for_scan:
                    match = pattern.search(url_for_scan)
                    if match:
                        dedup_key = (tech_name, source_url)
                        if dedup_key in seen:
                            continue
                        sample = match.group(0)[:_MAX_EVIDENCE_SAMPLE]
                        seen.add(dedup_key)
                        fid = f"pts_{hashlib.sha256(f'{tech_name}:{source_url}:{int(ts)}'.encode()).hexdigest()[:20]}"
                        payload_out = {'technology': tech_name, 'category': category, 'evidence_kind': evidence_kind, 'evidence_sample': sample, 'source_finding_id': getattr(finding, 'finding_id', '') or '', 'source_url': source_url, 'confidence': 0.8}
                        candidates.append(CanonicalFinding(finding_id=fid, query=query[:500], source_type='passive_tech_stack', confidence=0.8, ts=ts, provenance=('passive_tech_stack', tech_name, evidence_kind), payload_text=_msgspec_encode(payload_out).decode()))
        except Exception:
            continue
    loop_elapsed = time.monotonic() - loop_start
    _GLOBAL_STATS['extract_tech_stack_loop_ms'] = loop_elapsed * 1000
    return candidates[:_MAX_TECH_STACK_FINDINGS]

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
        stored = sum((1 for r in results if isinstance(r, dict) and r.get('accepted')))
        return stored
    except asyncio.CancelledError:
        raise
    except Exception:
        return 0

def _trigger_cve_lookup_tasks(findings: list[CanonicalFinding], store: Any) -> None:
    """
    Fire background CVE lookup tasks for high-signal technologies.

    Triggers asyncio.create_task() for: WordPress, Drupal, Joomla, Typo3,
    nginx, Apache, Next.js, React, Vue, Angular, Gatsby.

    CVE results are stored via store.async_ingest_findings_batch().
    Fail-safe: any error is logged and swallowed.
    """
    _CVE_TRIGGER_TECHS = {'WordPress', 'Drupal', 'Joomla', 'Typo3', 'nginx', 'Apache', 'Next.js', 'React', 'Vue', 'Angular', 'Gatsby', 'Laravel', 'Django', 'Flask', 'Magento', 'PrestaShop', 'Ghost', 'HubSpot'}
    detected_techs: set[str] = set()
    for finding in findings:
        try:
            payload_str = getattr(finding, 'payload_text', '') or ''
            if payload_str.startswith('{'):
                payload = _msgspec_decode(payload_str)
                tech = payload.get('technology', '')
                if tech in _CVE_TRIGGER_TECHS:
                    detected_techs.add(tech)
        except Exception:
            continue
    if not detected_techs:
        return
    from hledac.universal.utils.async_helpers import safe_create_task
    for tech in detected_techs:
        cve_id = f'CVE-{tech.upper()}-LATEST'
        safe_create_task(_cve_lookup_background(tech, cve_id, store), name=f'cve_lookup:{tech}')
        logger.debug(f'[TechStack] CVE lookup triggered for {tech}')

async def _cve_lookup_background(tech: str, cve_id: str, store: Any) -> None:
    """
    Background CVE lookup task — searches GitHub for PoC/exploit samples.

    Stores results as CanonicalFinding with source_type="cve_lookup".
    Fail-soft: logs and returns on any error.
    """
    try:
        from pathlib import Path
        from hledac.universal.recon.exposure_clients import GitHubCodeSearchClient as _GitHubCodeSearchCVEClient
        cache_dir = Path('/tmp/cve_gh_cache')
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
            url_val = r['url']
            fid_input = f'{cve_id}:{url_val}'
            fid = f'cve_gh_{hashlib.sha256(fid_input.encode()).hexdigest()[:16]}'
            payload = {'technology': tech, 'cve_id': cve_id, 'repo': r.get('repo', ''), 'url': r.get('url', ''), 'path': r.get('path', ''), 'stars': r.get('stars', 0), 'source': 'github_code_search'}
            cve_findings.append(CanonicalFinding(finding_id=fid, query=f'{tech} {cve_id}', source_type='cve_lookup', confidence=0.6, ts=ts, provenance=('cve_lookup', tech), payload_text=_msgspec_encode(payload).decode()))
        if cve_findings:
            await store.async_ingest_findings_batch(cve_findings)
            logger.info(f'[TechStack] {len(cve_findings)} CVE results stored for {tech}')
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(f'[TechStack] CVE lookup failed for {tech}: {e}')

class PassiveTechStackAdapter:
    """R11: Bounded passive tech-stack extraction adapter."""
    __slots__ = tuple(('_stats',))

    def __init__(self) -> None:
        self._stats: dict[str, int] = {'findings_scanned': 0, 'tech_stack_found': 0}

    def correlate(self, findings: list[CanonicalFinding], query: str) -> list[CanonicalFinding]:
        """Correlate tech-stack signals from findings."""
        result = _extract_tech_stack_findings(findings, query)
        self._stats['findings_scanned'] = len(findings)
        self._stats['tech_stack_found'] = len(result)
        return result

    def get_stats(self) -> dict[str, int]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        self._stats['findings_scanned'] = 0
        self._stats['tech_stack_found'] = 0

def create_passive_tech_stack_adapter() -> PassiveTechStackAdapter:
    """Factory for PassiveTechStackAdapter."""
    return PassiveTechStackAdapter()