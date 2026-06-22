"""IOC Extractor — Rust-powered high-performance IOC extraction.

Rust bindings for:
- fast_ioc_extract: regex-based IOC extraction (IPv4/IPv6/domain/md5/sha1/sha256/email/CVE)
- url_normalize: canonical URL normalization
- batch_dedup_urls: in-memory URL dedup with normalization

Falls back to pure Python if Rust extension unavailable.
"""

from __future__ import annotations

# F265C: Use centralized rust backend
_RUST_IOC_AVAILABLE = False
try:
    from core.rust_backend import rust as _rust_backend

    _RUST_IOC_AVAILABLE = (
        _rust_backend.is_available
        and _rust_backend.ioc is not None
        and _rust_backend.url is not None
    )
except ImportError:
    pass

# Python fallback regexes (always defined)
import re
from urllib.parse import parse_qsl, urlencode, urlparse

_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)
_IPV6_RE = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b")
_DOMAIN_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b"
)
_MD5_RE = re.compile(r"\b[a-fA-F0-9]{32}\b")
_SHA1_RE = re.compile(r"\b[a-fA-F0-9]{40}\b")
_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b")
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url",
}


def fast_ioc_extract(text: str) -> list[tuple[str, str]]:
    """Extract IOCs. Uses Rust fast_ioc_extract when available."""
    if _RUST_IOC_AVAILABLE and _rust_backend is not None:
        return _rust_backend.ioc.fast_ioc_extract(text)
    # Python fallback
    iocs = []
    seen = set()

    for m in _IPV4_RE.finditer(text):
        v = m.group()
        if v not in seen:
            seen.add(v)
            iocs.append((v, "ipv4"))

    for m in _IPV6_RE.finditer(text):
        v = m.group()
        if v not in seen:
            seen.add(v)
            iocs.append((v, "ipv6"))

    for m in _DOMAIN_RE.finditer(text):
        v = m.group().lower()
        if v not in seen:
            seen.add(v)
            iocs.append((v, "domain"))

    for m in _MD5_RE.finditer(text):
        v = m.group()
        if v not in seen:
            seen.add(v)
            iocs.append((v, "md5"))

    for m in _SHA1_RE.finditer(text):
        v = m.group()
        if v not in seen:
            seen.add(v)
            iocs.append((v, "sha1"))

    for m in _SHA256_RE.finditer(text):
        v = m.group()
        if v not in seen:
            seen.add(v)
            iocs.append((v, "sha256"))

    for m in _EMAIL_RE.finditer(text):
        v = m.group().lower()
        if v not in seen:
            seen.add(v)
            iocs.append((v, "email"))

    for m in _CVE_RE.finditer(text):
        v = m.group()
        if v not in seen:
            seen.add(v)
            iocs.append((v, "cve"))

    return iocs


def url_normalize(url: str) -> str:
    """Normalize URL. Uses Rust url_normalize when available."""
    if _RUST_IOC_AVAILABLE and _rust_backend is not None:
        return _rust_backend.url.normalize(url)
    # Python fallback — mirrors Rust url_engine::normalize() behavior.
    try:
        trimmed = url.strip()
        if not trimmed:
            return url

        # Scheme-less rescue (same as Rust: try synthetic http:// prefix)
        if "://" not in trimmed:
            synthetic = f"http://{trimmed.lstrip('/')}"
        else:
            synthetic = trimmed

        parsed = urlparse(synthetic)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port
        path = parsed.path or "/"

        # Strip default ports (mirrors Rust url_engine)
        if port == 80 and scheme == "http":
            port = None
        elif port == 443 and scheme == "https":
            port = None

        result = f"{scheme}://{host}"
        if port:
            result += f":{port}"
        result += path

        # Filter tracking params, sort, encode (mirrors Rust url_engine)
        params = [(k, v) for k, v in parse_qsl(parsed.query) if k not in _TRACKING_PARAMS]
        params.sort()
        if params:
            result += "?" + urlencode(params)

        return result
    except Exception:
        return url


def batch_dedup_urls(urls: list[str]) -> list[str]:
    """Deduplicate URLs. Uses Rust batch_dedup_urls when available."""
    if _RUST_IOC_AVAILABLE and _rust_backend is not None:
        return _rust_backend.url.batch_dedup_urls(urls)
    # Python fallback
    seen = set()
    result = []
    for url in urls:
        normalized = url_normalize(url)
        if normalized not in seen:
            seen.add(normalized)
            result.append(url)
    return result


__all__ = [
    "RUST_IOC_AVAILABLE",
    "fast_ioc_extract",
    "url_normalize",
    "batch_dedup_urls",
    "ioc_extract_to_canonical_findings",
    "IOC_FINDINGS_MAX",
]
