"""
runtime/source_finding_bridge.py
===============================

Sprint F207K-A: Non-feed adapter → CanonicalFinding candidate bridge.

Conversion helpers for CT, Wayback, and PassiveDNS adapter outputs into
canonical finding candidates without new storage authority.

Boundaries (non-feed lane contract):
    - No DB write
    - No graph write
    - No live network
    - Deterministic output (no hash() builtin)
    - Bounded output size
    - Rejection metadata for non-transformable results

Rejection reasons:
    missing_domain   — required domain/host field absent or empty
    missing_value   — required value (IP, URL, digest) absent or empty
    low_information — result provides no actionable signal
    duplicate_candidate — already seen in this batch (via blake2b dedup)
    unsupported_shape — input structure does not match expected schema

Verify:
    python -m pytest tests/probe_f207j_nonfeed_finding_bridge/ -v
    python -m pytest tests/probe_f207k_nonfeed_accepted_path/ -v
"""
from __future__ import annotations

import hashlib
import re
import time
from typing import Any, List, Optional, Tuple

try:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
except ImportError:
    CanonicalFinding = None  # type: ignore[assignment]

__all__ = [
    "ct_results_to_findings",
    "wayback_results_to_findings",
    "passive_dns_results_to_findings",
    "summarize_bridge_conversion",
    "Rejection",
    "RejectionReason",
    "MAX_BRIDGE_OUTPUT",
]

# ---------------------------------------------------------------------------
# Rejection reason constants
# ---------------------------------------------------------------------------

Rejection = str
RejectionReason = Rejection

REJECTION_MISSING_DOMAIN: RejectionReason = "missing_domain"
REJECTION_MISSING_VALUE: RejectionReason = "missing_value"
REJECTION_LOW_INFORMATION: RejectionReason = "low_information"
REJECTION_DUPLICATE_CANDIDATE: RejectionReason = "duplicate_candidate"
REJECTION_UNSUPPORTED_SHAPE: RejectionReason = "unsupported_shape"

# ---------------------------------------------------------------------------
# Output bounds
# ---------------------------------------------------------------------------

MAX_BRIDGE_OUTPUT: int = 500
MAX_PAYLOAD_TEXT_CHARS: int = 2000
MAX_PROVENANCE_ITEMS: int = 20
MAX_SAMPLE_REJECTIONS: int = 5

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_URL_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_URL_TRAILING_SLASH_RE = re.compile(r"/+$")
_WILDCARD_RE = re.compile(r"^\*\.")


def _strip_url_scheme(url: str) -> str:
    """Strip https:// or http:// prefix and trailing slashes."""
    return _URL_TRAILING_SLASH_RE.sub("", _URL_SCHEME_RE.sub("", url))


def _make_blake2b_hex(value: str, salt: str = "", max_len: int = 16) -> str:
    """
    Deterministic BLAKE2b hex digest — avoids hash() builtin.

    Uses a fixed salt (per-call-site salt param) to separate domains
    of the same input value across different finding types.
    """
    b2 = hashlib.blake2b(
        (salt + value).encode("utf-8"),
        digest_size=16,
    )
    return b2.hexdigest()[:max_len]


def _canonical_finding(
    *,
    finding_id: str,
    source_type: str,
    query: str,
    confidence: float,
    ts: float,
    provenance: Tuple[str, ...],
    payload_text: Optional[str] = None,
) -> Optional[Any]:
    """
    Build a CanonicalFinding or return None if CanonicalFinding unavailable.

    Provenance items are truncated to MAX_PROVENANCE_ITEMS.
    Payload text is truncated to MAX_PAYLOAD_TEXT_CHARS.
    """
    if CanonicalFinding is None:
        return None

    prov = provenance[:MAX_PROVENANCE_ITEMS]
    payload: Optional[str] = None
    if payload_text:
        payload = payload_text[:MAX_PAYLOAD_TEXT_CHARS]

    try:
        return CanonicalFinding(
            finding_id=finding_id,
            query=query[:500],
            source_type=source_type,
            confidence=confidence,
            ts=ts,
            provenance=prov,
            payload_text=payload,
        )
    except Exception:
        return None


def _ts_from_wayback_timestamp(ts: str) -> float:
    """Convert CDX timestamp (YYYYMMDDHHMMSS) to Unix float."""
    try:
        from datetime import datetime

        return datetime.strptime(ts, "%Y%m%d%H%M%S").timestamp()
    except Exception:
        return 0.0


_PRIVATE_HOSTNAMES: frozenset[str] = frozenset({
    "localhost",
    "invalid",
    "test",
    "example",
})


def _extract_domain_from_ct_hit(url: str, title: str) -> Optional[str]:
    """
    Extract the domain/subdomain from a CT DiscoveryHit URL or title.

    URL format: "https://subdomain.example.com/"
    Title format: "CT: subdomain.example.com"

    Returns None if no domain-like string is found.
    """
    stripped = _strip_url_scheme(url).strip()
    if stripped:
        return stripped.lower()

    if title.startswith("CT: "):
        domain = title[4:].strip().rstrip("/")
        if domain:
            return domain.lower()

    return None


def _is_wildcard_domain(domain: str) -> bool:
    """Check if domain is a wildcard pattern like *.example.com."""
    return bool(_WILDCARD_RE.match(domain))


# ---------------------------------------------------------------------------
# CT → CanonicalFinding
# ---------------------------------------------------------------------------

_CT_CONFIDENCE: float = 0.6
_CT_SOURCE_TYPE: str = "ct"
_CT_SALT: str = "ctbridge"


def ct_results_to_findings(
    batch_result: Any,
    _outcome: Any,
    query: str,
    sprint_id: str,
) -> Tuple[List[Any], List[RejectionReason]]:
    """
    Convert CT (crt.sh) DiscoveryBatchResult + CTOutcome to finding candidates.

    Each DiscoveryHit becomes one CanonicalFinding with:
        source_type = "ct"
        confidence  = 0.6

    Returns (findings, rejection_reasons).
    Findings are capped at MAX_BRIDGE_OUTPUT.

    Rejection reasons:
        missing_domain    — URL/title contains no parseable domain
        missing_value     — hit has no usable url or title
        low_information   — domain too short or looks like a private host
        duplicate_candidate — same domain already seen in this batch
    """
    findings: List[Any] = []
    rejections: List[RejectionReason] = []
    seen_domains: set[str] = set()

    if not hasattr(batch_result, "hits"):
        rejections.append(REJECTION_UNSUPPORTED_SHAPE)
        return [], rejections

    hits = batch_result.hits
    if not hits:
        rejections.append(REJECTION_MISSING_VALUE)
        return [], rejections

    capped = hits[:MAX_BRIDGE_OUTPUT]

    for hit in capped:
        url = getattr(hit, "url", "") or ""
        title = getattr(hit, "title", "") or ""
        snippet = getattr(hit, "snippet", "") or ""
        retrieved_ts = getattr(hit, "retrieved_ts", 0.0) or 0.0

        if not url and not title:
            rejections.append(REJECTION_MISSING_VALUE)
            continue

        domain = _extract_domain_from_ct_hit(url, title)
        if not domain:
            rejections.append(REJECTION_MISSING_DOMAIN)
            continue

        if domain in _PRIVATE_HOSTNAMES:
            rejections.append(REJECTION_LOW_INFORMATION)
            continue

        if "." not in domain:
            rejections.append(REJECTION_LOW_INFORMATION)
            continue

        if _is_wildcard_domain(domain):
            rejections.append(REJECTION_LOW_INFORMATION)
            continue

        if domain in seen_domains:
            rejections.append(REJECTION_DUPLICATE_CANDIDATE)
            continue
        seen_domains.add(domain)

        ts = retrieved_ts if retrieved_ts > 0 else time.time()
        blake2_id = _make_blake2b_hex(domain, _CT_SALT)
        finding_id = f"ct-{blake2_id}-{sprint_id[:8]}"

        provenance: Tuple[str, ...] = (
            f"source_family:ct",
            f"domain:{domain}",
            f"query:{query[:200]}",
            f"sprint:{sprint_id[:16]}",
            f"title:{title[:200]}",
        )

        payload_text = snippet if snippet else None

        finding = _canonical_finding(
            finding_id=finding_id,
            source_type=_CT_SOURCE_TYPE,
            query=query,
            confidence=_CT_CONFIDENCE,
            ts=ts,
            provenance=provenance,
            payload_text=payload_text,
        )
        if finding is not None:
            findings.append(finding)

    return findings, rejections


# ---------------------------------------------------------------------------
# Wayback → CanonicalFinding
# ---------------------------------------------------------------------------

_WAYBACK_CONFIDENCE: float = 0.75
_WAYBACK_SOURCE_TYPE: str = "wayback_diff"
_WAYBACK_SALT: str = "waybackbridge"


def wayback_results_to_findings(
    diff_result: Any,
    query: str,
    sprint_id: str,
) -> Tuple[List[Any], List[RejectionReason]]:
    """
    Convert WaybackDiffResult to finding candidates.

    Each CDXDiffEvent becomes one CanonicalFinding with:
        source_type = "wayback_diff"
        confidence  = 0.75

    Returns (findings, rejection_reasons).

    Rejection reasons:
        missing_value    — event has no digest or url
        low_information  — change_type is "unchanged" (no signal)
    """
    findings: List[Any] = []
    rejections: List[RejectionReason] = []

    if not hasattr(diff_result, "change_events"):
        rejections.append(REJECTION_UNSUPPORTED_SHAPE)
        return [], rejections

    events = diff_result.change_events
    if not events:
        rejections.append(REJECTION_MISSING_VALUE)
        return [], rejections

    capped = events[:MAX_BRIDGE_OUTPUT]

    for event in capped:
        url = getattr(event, "url", "") or ""
        digest = getattr(event, "digest", "") or ""
        timestamp = getattr(event, "timestamp", "") or ""
        change_type = getattr(event, "change_type", "") or ""
        evidence_url = getattr(event, "evidence_url", "") or ""

        if not digest:
            rejections.append(REJECTION_MISSING_VALUE)
            continue

        if change_type == "unchanged":
            rejections.append(REJECTION_LOW_INFORMATION)
            continue

        ts = _ts_from_wayback_timestamp(timestamp) if timestamp else time.time()
        blake2_id = _make_blake2b_hex(digest, _WAYBACK_SALT)
        finding_id = f"wdiff-{blake2_id}-{timestamp[:8]}"[:64]

        provenance: Tuple[str, ...] = (
            f"source_family:wayback_diff",
            f"url:{url[:300]}",
            f"digest:{digest[:64]}",
            f"change:{change_type}",
            f"ts:{timestamp}",
            f"sprint:{sprint_id[:16]}",
        )

        payload_parts = [
            f"change_type: {change_type}",
            f"url: {url}",
            f"digest: {digest}",
            f"timestamp: {timestamp}",
        ]
        if evidence_url:
            payload_parts.append(f"evidence_url: {evidence_url}")
        if url:
            payload_parts.append(f"source_domain: {_strip_url_scheme(url)}")

        payload_text = "\n".join(payload_parts)

        finding = _canonical_finding(
            finding_id=finding_id,
            source_type=_WAYBACK_SOURCE_TYPE,
            query=query,
            confidence=_WAYBACK_CONFIDENCE,
            ts=ts,
            provenance=provenance,
            payload_text=payload_text,
        )
        if finding is not None:
            findings.append(finding)

    return findings, rejections


# ---------------------------------------------------------------------------
# PassiveDNS → CanonicalFinding
# ---------------------------------------------------------------------------

_PDNS_CONFIDENCE: float = 0.5
_PDNS_SOURCE_TYPE: str = "passive_dns"
_PDNS_SALT: str = "pdnsbridge"

_PRIVATE_IP_PREFIXES: tuple[str, ...] = (
    "10.",
    "172.16.",
    "172.17.",
    "172.18.",
    "172.19.",
    "172.20.",
    "172.21.",
    "172.22.",
    "172.23.",
    "172.24.",
    "172.25.",
    "172.26.",
    "172.27.",
    "172.28.",
    "172.29.",
    "172.30.",
    "172.31.",
    "192.168.",
    "127.",
    "0.",
    "255.",
    "169.254.",
    "::1",
    "fe80:",
    "fc00:",
    "fd00:",
)


def passive_dns_results_to_findings(
    ips: List[str],
    _outcome: Any,
    query: str,
    sprint_id: str,
) -> Tuple[List[Any], List[RejectionReason]]:
    """
    Convert PassiveDNS IP list + PassiveDNSOutcome to finding candidates.

    Each IP becomes one CanonicalFinding with:
        source_type = "passive_dns"
        confidence  = 0.5

    Returns (findings, rejection_reasons).
    Findings are capped at MAX_BRIDGE_OUTPUT.

    Rejection reasons:
        missing_domain    — query is empty or not a valid domain/IP
        missing_value    — no IP addresses returned
        low_information  — IP looks like a private/reserved address
        duplicate_candidate — same (query, ip) pair already seen
    """
    findings: List[Any] = []
    rejections: List[RejectionReason] = []

    query_stripped = query.strip() if query else ""
    if not query_stripped:
        rejections.append(REJECTION_MISSING_DOMAIN)
        return [], rejections

    if not ips:
        rejections.append(REJECTION_MISSING_VALUE)
        return [], rejections

    capped = ips[:MAX_BRIDGE_OUTPUT]
    seen_pairs: set[str] = set()
    now = time.time()

    for ip in capped:
        ip_stripped = ip.strip()
        if not ip_stripped:
            rejections.append(REJECTION_MISSING_VALUE)
            continue

        is_private = any(ip_stripped.startswith(p) for p in _PRIVATE_IP_PREFIXES)
        if is_private:
            rejections.append(REJECTION_LOW_INFORMATION)
            continue

        pair_key = f"{query_stripped}:{ip_stripped}"
        if pair_key in seen_pairs:
            rejections.append(REJECTION_DUPLICATE_CANDIDATE)
            continue
        seen_pairs.add(pair_key)

        blake2_id = _make_blake2b_hex(pair_key, _PDNS_SALT)
        finding_id = f"pdns-{blake2_id}-{sprint_id[:8]}"

        provenance: Tuple[str, ...] = (
            f"source_family:passive_dns",
            f"domain:{query_stripped}",
            f"ip:{ip_stripped}",
            f"sprint:{sprint_id[:16]}",
            f"source:circl_pdns",
        )

        payload_text = f"domain: {query_stripped}\nip: {ip_stripped}\nsource: CIRCL PDNS"

        finding = _canonical_finding(
            finding_id=finding_id,
            source_type=_PDNS_SOURCE_TYPE,
            query=query,
            confidence=_PDNS_CONFIDENCE,
            ts=now,
            provenance=provenance,
            payload_text=payload_text,
        )
        if finding is not None:
            findings.append(finding)

    return findings, rejections


# ---------------------------------------------------------------------------
# Bridge conversion summary helper
# ---------------------------------------------------------------------------


def summarize_bridge_conversion(
    family: str,
    findings: List[Any],
    rejections: List[RejectionReason],
) -> dict[str, Any]:
    """
    Summarize bridge conversion results.

    Pure function — no storage, no network, no MLX.

    Args:
        family: Source family name (e.g., "ct", "wayback_diff", "passive_dns")
        findings: List of CanonicalFinding candidates produced
        rejections: List of rejection reason strings

    Returns:
        Bounded summary dict with:
        - family: source family
        - candidatesProduced: count of findings
        - rejectionsCount: count of rejections
        - rejectionReasons: unique reasons with counts (capped at 10)
        - totalProcessed: sum of candidates + rejections (capped at MAX_BRIDGE_OUTPUT)
    """
    rejection_counts: dict[str, int] = {}
    for reason in rejections:
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    # Cap rejection reason entries
    sorted_reasons = sorted(
        rejection_counts.items(),
        key=lambda x: (-x[1], x[0]),
    )
    top_rejections = dict(sorted_reasons[:10])

    total = min(len(findings) + len(rejections), MAX_BRIDGE_OUTPUT)

    return {
        "family": family,
        "candidatesProduced": len(findings),
        "rejectionsCount": len(rejections),
        "rejectionReasons": top_rejections,
        "totalProcessed": total,
    }
