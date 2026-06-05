"""IOC Extractor — Rust-powered high-performance IOC extraction.

Rust bindings for:
- fast_ioc_extract: regex-based IOC extraction (IPv4/IPv6/domain/md5/sha1/sha256/email/CVE)
- url_normalize: canonical URL normalization
- batch_dedup_urls: in-memory URL dedup with normalization

Falls back to pure Python if Rust extension unavailable.
"""

from __future__ import annotations

from typing import Any


try:
    from hledac_rust_extensions import (
        batch_dedup_urls,
        fast_ioc_extract,
        url_normalize,
    )
    RUST_IOC_AVAILABLE = True
except ImportError:
    RUST_IOC_AVAILABLE = False

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
        try:
            parsed = urlparse(url)
        except Exception:
            return url

        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port
        path = parsed.path or "/"

        # Strip default ports
        if port == 80 and scheme == "http":
            port = None
        elif port == 443 and scheme == "https":
            port = None

        result = f"{scheme}://{host}"
        if port:
            result += f":{port}"
        result += path

        # Ensure path ends with / if no extension
        if "." not in path:
            result = result.rstrip("/") + "/"

        # Filter tracking params, sort, encode
        params = [(k, v) for k, v in parse_qsl(parsed.query) if k not in _TRACKING_PARAMS]
        params.sort()

        if params:
            result += "?" + urlencode(params)

        return result

    def batch_dedup_urls(urls: list[str]) -> list[str]:
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


# ---------------------------------------------------------------------------
# Sprint F261: IOC extraction → CanonicalFinding wiring
# ---------------------------------------------------------------------------
# Bridges the low-level regex IOC extractor to the canonical DuckDB write
# path. Each unique (ioc_type, ioc_value) becomes its own CanonicalFinding
# with source_type="forensic_analysis". Bounded by IOC_FINDINGS_MAX to
# prevent payload blowup on adversarial inputs.

try:
    from forensics.enrichment_service import FORENSIC_SOURCE_TYPE
except ImportError:
    FORENSIC_SOURCE_TYPE = "forensic_analysis"  # Fallback for import-cycle safety

# Hard limits for IOC findings output
IOC_FINDINGS_MAX: int = 50
_IOC_VALUE_MAX: int = 256
_IOC_IOC_CONFIDENCE: float = 0.85  # Regex extraction is deterministic

# Sprint F262: global per-sprint IOC budget. Caps DuckDB growth on
# adversarial inputs (e.g., 1k findings × 50 IOCs = 50k rows). Reached
# IOC extraction silently short-circuits (fail-soft). 500/sprint is
# roughly 10x the expected yield for a typical research sprint and well
# under M1 8GB DuckDB shadow-store headroom.
GLOBAL_IOC_BUDGET_DEFAULT: int = 500


def _ioc_finding_id_deterministic(ioc_type: str, value: str) -> str:
    """
    Sprint F262: content-hash-based IOC finding_id (deterministic).

    Replaces the position-based suffix (`len(out)`) so the same IOC
    extracted from N parents produces the same finding_id across all N.
    The DuckDB LMDB WAL upsert keyed by finding_id then collapses
    duplicates naturally (last write wins, no row growth).

    Uses BLAKE2b (8-byte digest) — same primitive used elsewhere in
    the codebase (see :mod:`knowledge.semantic_deduplicator`) for
    consistency and Metal-accelerated throughput.
    """
    try:
        import hashlib as _hl
        digest = _hl.blake2b(
            f"{ioc_type}={value}".encode("utf-8", errors="replace"),
            digest_size=8,
        ).hexdigest()[:16]
    except Exception:
        # Fallback: builtin hash() — non-cryptographic but stable
        digest = format(abs(hash(f"{ioc_type}={value}")) & 0xFFFFFFFFFFFFFFFF, "x")[:16]
    return f"ioc_{ioc_type}_{digest}"[:128]


def ioc_extract_to_canonical_findings(
    text: str,
    source_finding_id: str,
    query: str = "",
    *,
    source_type: str | None = None,
    budget_remaining: int | None = None,
) -> list[Any]:
    """
    Convert IOC extraction results to a list of CanonicalFinding instances.

    Sprint F261: ioc_extractor → CanonicalFinding wiring.
    Each unique ``(ioc_type, ioc_value)`` becomes its own finding, bounded
    to :data:`IOC_FINDINGS_MAX` entries. Fail-safe: returns ``[]`` on any
    error — never raises.

    Sprint F262: deterministic ``finding_id`` (content-hash based) so
    cross-parent duplicates collapse on the LMDB WAL upsert path. New
    ``budget_remaining`` parameter lets the caller enforce a global
    per-sprint IOC budget (defaults to :data:`GLOBAL_IOC_BUDGET_DEFAULT`).

    Args:
        text: Text to extract IOCs from (may be None or empty).
        source_finding_id: Parent finding ID (used for diagnostic
            provenance; the emitted finding_id is content-derived and
            independent of this parent).
        query: Sprint query (copied to the new finding).
        source_type: Override for source_type
            (default: :data:`FORENSIC_SOURCE_TYPE`).
        budget_remaining: Cap on number of IOC findings to emit.
            ``None`` falls back to :data:`GLOBAL_IOC_BUDGET_DEFAULT`.
            When the budget is exhausted the function short-circuits
            with what it has so far (fail-soft).

    Returns:
        List of :class:`knowledge.duckdb_store.CanonicalFinding` instances
        (may be empty).
    """
    if not text:
        return []
    try:
        from knowledge.duckdb_store import CanonicalFinding

        iocs = fast_ioc_extract(text)
        if not iocs:
            return []

        try:
            import time as _time
            ts = float(_time.time())
        except Exception:
            ts = 0.0

        new_source_type = str(source_type or FORENSIC_SOURCE_TYPE)[:64]
        bounded_query = str(query or "")[:512]

        # Sprint F262: per-call budget = min(per-finding cap, global budget)
        if budget_remaining is None:
            budget_remaining = GLOBAL_IOC_BUDGET_DEFAULT
        elif budget_remaining <= 0:
            return []  # Global budget exhausted — fail-soft skip
        per_call_cap = min(IOC_FINDINGS_MAX, int(budget_remaining))

        out: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for value, ioc_type in iocs:
            key = (ioc_type, value)
            if key in seen:
                continue
            seen.add(key)
            if len(out) >= per_call_cap:
                break
            value_bounded = str(value)[:_IOC_VALUE_MAX]
            # Sprint F262: deterministic content-hash finding_id
            ioc_finding_id = _ioc_finding_id_deterministic(ioc_type, value)
            try:
                out.append(
                    CanonicalFinding(
                        finding_id=ioc_finding_id,
                        query=bounded_query,
                        source_type=new_source_type,
                        confidence=_IOC_IOC_CONFIDENCE,
                        ts=ts,
                        provenance=("forensic_analysis", "ioc_extractor"),
                        payload_text=(
                            f"ioc_type={ioc_type}; value={value_bounded}; "
                            f"parent={str(source_finding_id or '')[:64]}"
                        ),
                    )
                )
            except Exception:
                # Skip individual finding on construction failure, keep going
                continue
        return out
    except Exception:
        return []
