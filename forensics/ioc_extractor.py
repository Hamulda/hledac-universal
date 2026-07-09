"""IOC Extractor — Rust-powered high-performance IOC extraction.

Rust bindings for:
- fast_ioc_extract: regex-based IOC extraction (IPv4/IPv6/domain/md5/sha1/sha256/email/CVE)
- url_normalize: canonical URL normalization
- batch_dedup_urls: in-memory URL dedup with normalization

Falls back to pure Python if Rust extension unavailable.
"""
from __future__ import annotations

import re as _re
from urllib.parse import parse_qsl, urlencode, urlparse

# F265C: Use centralized rust backend
_RUST_IOC_AVAILABLE = False
try:
    from core.rust_backend import rust as _rust_backend  # noqa: E402

    _RUST_IOC_AVAILABLE = (
        _rust_backend.is_available
        and _rust_backend.ioc is not None
        and _rust_backend.url is not None
    )
except ImportError:
    pass

# Python fallback regexes — combined named-group regex for single-pass IOC extraction.
# Issue #3: Replaces 7 sequential finditer loops with one scan.
# Each pattern uses a named group so we know WHICH pattern matched in one pass.
_IOC_PATTERNS: list[tuple[str, str]] = [
    # (group_name, pattern)
    ("ipv4",   r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"),
    ("ipv6_full", r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"),
    ("domain", r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b"),
    ("md5",    r"\b[a-fA-F0-9]{32}\b"),
    ("sha1",   r"\b[a-fA-F0-9]{40}\b"),
    ("sha256", r"\b[a-fA-F0-9]{64}\b"),
    ("email",  r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    ("cve",    r"\bCVE-\d{4}-\d{4,}\b"),
]

_IOC_TYPE_NAMES: list[str] = [name for name, _ in _IOC_PATTERNS]

# Single combined regex with named groups — one finditer pass, no rescanning.
_IOC_COMBINED = _re.compile(
    "|".join(f"(?P<{name}>{pattern})" for name, pattern in _IOC_PATTERNS)
)

_HASH_VALIDATORS = {
    "md5":    lambda v: len(v) == 32 and all(c in "0123456789abcdefABCDEF" for c in v),
    "sha1":   lambda v: len(v) == 40 and all(c in "0123456789abcdefABCDEF" for c in v),
    "sha256": lambda v: len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v),
}

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url",
}

# IPv6 compact forms: ::1, fe80::1, 2001:db8::1, ::ffff:192.168.1.1
_IPV6_COMPRESSED_RE = _re.compile(r"\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{1,4}){1,7}\b")
_IPV6_MAPPED_RE = _re.compile(r"\b::ffff:(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b")


def fast_ioc_extract(text: str) -> list[tuple[str, str]]:
    """Extract IOCs. Uses Rust SIMD extractor (Teddy/NEON on M1) when available.

    Priority:
    1. extract_iocs_simd — regex-automata with Teddy SIMD (M1 NEON ~5× faster)
    2. fast_ioc_extract — basic regex (fallback if SIMD unavailable)
    3. Python fallback — named-group combined regex single-pass (Issue #3: was 7 sequential loops)
    """
    if _RUST_IOC_AVAILABLE and _rust_backend is not None:
        ioc = _rust_backend.ioc
        # Prefer SIMD extractor (Teddy/NEON on M1) for bulk text
        if hasattr(ioc, "extract_iocs_simd"):
            return ioc.extract_iocs_simd(text)
        # Fallback to basic Rust regex extractor
        return ioc.fast_ioc_extract(text)
    # Python fallback — Issue #3: named-group combined regex, single finditer pass.
    # Named groups eliminate the nested loop that rescanned text for every RegexSet match.
    iocs = []
    seen: set[str] = set()

    for m in _IOC_COMBINED.finditer(text):
        # Determine which named group matched — this tells us the IOC type directly.
        for name in _IOC_TYPE_NAMES:
            if m.lastgroup == name:
                # Normalize all IPv6 variants to "ipv6"
                ioc_type = "ipv6" if name.startswith("ipv6") else name
                value = m.group()
                # Validate hex hashes to prevent false positives (e.g. 'abc...40chars' as SHA1)
                if name in _HASH_VALIDATORS and not _HASH_VALIDATORS[name](value):
                    break
                # Normalize: domain and email to lowercase
                if ioc_type in ("domain", "email"):
                    value = value.lower()
                if value not in seen:
                    seen.add(value)
                    iocs.append((value, ioc_type))
                break

    # Issue #4: Extract IPv6 compact forms (::1, fe80::1, 2001:db8::1, ::ffff:x.x.x.x)
    for m in _IPV6_COMPRESSED_RE.finditer(text):
        v = m.group()
        # Skip full form (already captured by ipv6_full pattern above)
        if ':' in v and v.count(':') >= 7:
            continue
        if v not in seen:
            seen.add(v)
            iocs.append((v, "ipv6"))
    for m in _IPV6_MAPPED_RE.finditer(text):
        v = m.group()
        if v not in seen:
            seen.add(v)
            iocs.append((v, "ipv6"))

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
            result.append(normalized)
    return result


def ioc_extract_to_canonical_findings(
    text: str,
    source_finding_id: str,
    query: str,
    min_confidence: float = 0.5,
) -> list:
    """
    Extract IOCs from text and convert to CanonicalFinding objects.

    Each finding has payload_text in format:
        ioc_type=<type>; value=<value>; parent=<source_finding_id>

    This format is parsed by export/markdown_reporter.py:_parse_forensic_payload().

    M1 8GB safe: bounded at 100 IOCs per extraction, no recursion,
    no regex compilation, pure Python path uses pre-compiled regexes.
    """
    import time

    # Use fast_ioc_extract for extraction (Rust or Python fallback)
    iocs = fast_ioc_extract(text)

    # M1 8GB: hard cap on number of findings per extraction
    MAX_IOC_FINDINGS = 100

    # Import here to avoid circular dependency at module level
    from knowledge.duckdb_store import CanonicalFinding

    findings = []
    for ioc_value, ioc_type in iocs[:MAX_IOC_FINDINGS]:
        # Confidence based on IOC type certainty
        if ioc_type in ("ipv4", "ipv6", "md5", "sha1", "sha256"):
            confidence = 0.9
        elif ioc_type in ("domain", "email", "cve"):
            confidence = 0.8
        else:
            confidence = 0.7

        if confidence < min_confidence:
            continue

        finding = CanonicalFinding(
            finding_id=f"{source_finding_id}_ioc_{len(findings)}",
            query=query,
            source_type="ioc_extraction",
            confidence=confidence,
            ts=time.time(),
            provenance=("ioc_extractor",),
            payload_text=f"ioc_type={ioc_type}; value={ioc_value}; parent={source_finding_id}",
        )
        findings.append(finding)

    return findings


# IOC_FINDINGS_MAX was referenced in __all__ but never existed — bounded constant for doc purposes
IOC_FINDINGS_MAX = 100


__all__ = [
    "_RUST_IOC_AVAILABLE",
    "fast_ioc_extract",
    "url_normalize",
    "batch_dedup_urls",
    "ioc_extract_to_canonical_findings",
    "IOC_FINDINGS_MAX",
]
