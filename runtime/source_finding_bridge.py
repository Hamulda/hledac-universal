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
    "summarize_ct_conversion",
    "Rejection",
    "RejectionReason",
    "REJECTION_MISSING_DOMAIN",
    "REJECTION_MISSING_VALUE",
    "REJECTION_LOW_INFORMATION",
    "REJECTION_DUPLICATE_CANDIDATE",
    "REJECTION_UNSUPPORTED_SHAPE",
    "REJECTION_WILDCARD_DOMAIN",
    "REJECTION_PRIVATE_OR_RESERVED_DOMAIN",
    "REJECTION_STORAGE_UNAVAILABLE",
    "REJECTION_QUALITY_GATE",
    "REJECTION_CANDIDATE_BUILT_NOT_STORED",
    "MAX_BRIDGE_OUTPUT",
    "record_ct_storage_results",
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

# Extended CT-specific rejection reasons
REJECTION_WILDCARD_DOMAIN: RejectionReason = "wildcard_domain"
REJECTION_PRIVATE_OR_RESERVED_DOMAIN: RejectionReason = "private_or_reserved_domain"
# F214A: CT acceptance closure — storage/quality gate rejections
REJECTION_STORAGE_UNAVAILABLE: RejectionReason = "storage_unavailable"
REJECTION_QUALITY_GATE: RejectionReason = "quality_gate"
REJECTION_CANDIDATE_BUILT_NOT_STORED: RejectionReason = "candidate_built_not_stored"

# ---------------------------------------------------------------------------
# Output bounds
# ---------------------------------------------------------------------------

MAX_BRIDGE_OUTPUT: int = 500
MAX_PAYLOAD_TEXT_CHARS: int = 2000
MAX_PROVENANCE_ITEMS: int = 20
MAX_SAMPLE_REJECTIONS: int = 5
MAX_CT_QUARANTINE_SAMPLES: int = 10

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

_CT_CONFIDENCE: float = 0.65
_CT_SOURCE_TYPE: str = "ct"
_CT_SALT: str = "ctbridge"


def _extract_domains_from_ct_name_value(name_value: str) -> list[tuple[str, bool]]:
    """
    Extract all concrete (non-wildcard) domains from a multiline CT name_value.

    Returns list of (domain, was_wildcard) tuples for each line.
    Wildcard-only lines are returned with was_wildcard=True; concrete domains
    are returned with was_wildcard=False.

    F213A: enables per-line wildcard rejection while preserving concrete siblings.
    """
    if not name_value:
        return []
    results: list[tuple[str, bool]] = []
    for line in name_value.split("\n"):
        line = line.strip()
        if not line:
            continue
        is_wildcard = _is_wildcard_domain(line)
        results.append((line.lower(), is_wildcard))
    return results


def _is_private_hostname(domain: str) -> bool:
    """Check if domain matches a reserved/private hostname."""
    return domain in _PRIVATE_HOSTNAMES


def _classify_domain_shape(domain: str, url: str, ct_name_value: str) -> str:
    """Classify the candidate shape for quarantine entry classification."""
    if domain.startswith("*."):
        return "wildcard"
    if not domain or "." not in domain:
        return "single_label"
    if ct_name_value and len(ct_name_value.split("\n")) > 2:
        return "multi_san"
    if url.startswith("https://"):
        return "https_derived"
    if url.startswith("http://"):
        return "http_derived"
    return "ct_entry"


def _make_ct_quarantine_entry(
    domain: str,
    reject_reason: str,
    query: str,
    source_url: str,
    candidate_shape: str,
    ts: float,
) -> dict[str, Any]:
    """
    Build a bounded CT quarantine entry for raw hits rejected by bridge criteria.

    Quarantine entries are inspectable evidence of bridge rejections WITHOUT
    being counted as accepted CanonicalFinding candidates.

    Schema:
        family         — "CT" (source family identifier)
        raw_value      — sanitized domain or subdomain if safe to log
        source_url     — sanitized source URL (no credentials)
        reject_reason  — rejection reason from RejectionReason constants
        normalized_query — query used for this CT lookup
        candidate_shape — domain shape classification
        accepted       — False (quarantine is not accepted evidence)
        quarantine     — True (this entry is quarantined)
        ts             — Unix timestamp from hit or current time

    No full sensitive payload is stored — only bounded metadata for triage.
    """
    return {
        "family": "CT",
        "raw_value": domain[:253] if domain else "",
        "source_url": source_url[:500] if source_url else "",
        "reject_reason": reject_reason,
        "normalized_query": query[:200] if query else "",
        "candidate_shape": candidate_shape,
        "accepted": False,
        "quarantine": True,
        "ts": ts,
    }


def _build_ct_payload(
    domain: str,
    issuer_name: str | None,
    not_before: str | None,
    not_after: str | None,
    serial_number: str | None,
    entry_timestamp: str | None,
    name_value: str | None,
    common_name: str | None,
) -> str:
    """Build evidence-rich payload text from CT certificate metadata."""
    parts: list[str] = []
    parts.append(f"domain: {domain}")
    if issuer_name:
        parts.append(f"issuer: {issuer_name}")
    if serial_number:
        parts.append(f"serial: {serial_number}")
    if not_before:
        parts.append(f"valid_from: {not_before}")
    if not_after:
        parts.append(f"valid_until: {not_after}")
    if entry_timestamp:
        parts.append(f"ct_entry: {entry_timestamp}")
    if name_value:
        parts.append(f"name_value: {name_value}")
    if common_name:
        parts.append(f"common_name: {common_name}")
    return "\n".join(parts)


def _build_ct_provenance(
    domain: str,
    query: str,
    sprint_id: str,
    issuer_name: str | None,
    entry_timestamp: str | None,
) -> Tuple[str, ...]:
    """Build provenance tuple for CT finding."""
    prov: list[str] = [
        f"source_family:ct",
        f"crtsh",
        f"query:{query[:200]}",
        f"domain:{domain}",
    ]
    if issuer_name:
        prov.append(f"issuer:{issuer_name[:200]}")
    if entry_timestamp:
        prov.append(f"ct_entry:{entry_timestamp}")
    prov.append(f"sprint:{sprint_id[:16]}")
    return tuple(prov)


def _make_ct_conversion_summary(
    raw_hits_count: int,
    ct_raw_entries: int,
    built: int,
    stored: int,
    storage_rejected: int,
    total_rejected: int,
) -> str:
    """
    F214A: Human-readable narrative of CT raw→accepted conversion.

    Explains why raw>0 but accepted might be 0:
    - built but not stored (quality gate / storage failure)
    - all rejected (wildcard/private/duplicate/etc.)
    """
    if raw_hits_count == 0:
        return "no_raw_entries"
    if built == 0:
        if total_rejected > 0:
            return f"all_{total_rejected}_entries_rejected_no_candidates_built"
        return "no_entries_processed"
    if stored > 0 and storage_rejected > 0:
        return f"built_{built}_stored_{stored}_quality_gate_rejected_{storage_rejected}"
    if stored > 0:
        return f"built_{built}_stored_{stored}_all_candidates_accepted"
    if built > 0 and storage_rejected == 0 and total_rejected > 0:
        return f"built_{built}_rejected_{total_rejected}_no_candidates_stored"
    if built > 0 and storage_rejected > 0:
        return f"built_{built}_storage_rejected_{storage_rejected}"
    if built > 0 and total_rejected > 0:
        return f"built_{built}_rejected_{total_rejected}"
    if built == raw_hits_count:
        return "all_raw_entries_became_candidates"
    return f"converted_{built}_of_{raw_hits_count}_raw_entries"


def ct_results_to_findings(
    batch_result: Any,
    _outcome: Any,  # intentionally kept for backward API compat; CT telemetry from _outcome is not needed
    query: str,
    sprint_id: str,
) -> Tuple[List[Any], List[RejectionReason], dict[str, Any]]:
    # Backward compat: _outcome kept in signature; telemetry comes from hits, not outcome
    del _outcome
    """
    Convert CT (crt.sh) DiscoveryBatchResult + CTOutcome to finding candidates.

    Each DiscoveryHit can yield one or more CanonicalFinding candidates when
    ct_name_value contains multiple domains (including wildcard siblings).

    Each CanonicalFinding has:
        source_type = "ct"
        confidence  = 0.65
        payload_text: evidence-rich CT metadata (issuer, validity, serial)
        provenance: source_family:ct, crtsh, query, domain, [issuer], [ct_entry]

    Returns (findings, rejection_reasons, telemetry).
    Findings are capped at MAX_BRIDGE_OUTPUT.

    Telemetry fields:
        ct_raw_entries          — hits processed
        ct_extracted_domains    — total domains extracted from ct_name_value splits
        ct_candidate_domains    — domains that passed structural/information checks
        ct_accepted_candidates  — CanonicalFinding candidates produced
        ct_rejected_wildcard    — domains rejected as wildcard-only
        ct_rejected_invalid     — domains rejected as private/reserved/invalid TLD
        ct_rejected_duplicate   — domains rejected as duplicate within batch

    Rejection reasons:
        missing_domain          — no parseable domain found
        missing_value          — hit has no usable url, title, or CT metadata
        wildcard_domain         — domain is a wildcard pattern (e.g. *.example.com)
        private_or_reserved_domain — domain is private, internal, or reserved
        duplicate_candidate     — same domain already seen in this batch
        low_information        — single-label domain with no TLD
    """
    findings: List[Any] = []
    rejections: List[RejectionReason] = []
    seen_domains: set[str] = set()
    ct_quarantine_entries: list[dict[str, Any]] = []

    # Telemetry counters
    ct_raw_entries = 0
    ct_extracted_domains = 0
    ct_candidate_domains = 0

    if not hasattr(batch_result, "hits"):
        rejections.append(REJECTION_UNSUPPORTED_SHAPE)
        return [], rejections, {
            "ct_raw_entries": 0,
            "ct_extracted_domains": 0,
            "ct_candidate_domains": 0,
            "ct_accepted_candidates": 0,
            "ct_candidates_built": 0,
            "ct_candidates_stored": 0,
            "ct_storage_rejected": 0,
            "ct_rejected_wildcard": 0,
            "ct_rejected_invalid": 0,
            "ct_rejected_duplicate": 0,
            "ct_rejected_missing_domain": 0,
            "ct_rejected_missing_value": 0,
            "ct_quarantine_count": 0,
            "ct_quarantine_entries": [],
        }

    hits = batch_result.hits
    if not hits:
        rejections.append(REJECTION_MISSING_VALUE)
        return [], rejections, {
            "ct_raw_entries": 0,
            "ct_extracted_domains": 0,
            "ct_candidate_domains": 0,
            "ct_accepted_candidates": 0,
            "ct_candidates_built": 0,
            "ct_candidates_stored": 0,
            "ct_storage_rejected": 0,
            "ct_rejected_wildcard": 0,
            "ct_rejected_invalid": 0,
            "ct_rejected_duplicate": 0,
            "ct_rejected_missing_domain": 0,
            "ct_rejected_missing_value": 0,
            "ct_quarantine_count": 0,
            "ct_quarantine_entries": [],
        }

    capped = hits[:MAX_BRIDGE_OUTPUT]

    for hit in capped:
        ct_raw_entries += 1
        url = getattr(hit, "url", "") or ""
        title = getattr(hit, "title", "") or ""
        retrieved_ts = getattr(hit, "retrieved_ts", 0.0) or 0.0

        # CT metadata from crtsh_adapter (F213A)
        ct_name_value = getattr(hit, "ct_name_value", None) or ""
        ct_common_name = getattr(hit, "ct_common_name", None) or ""
        ct_issuer_name = getattr(hit, "ct_issuer_name", None) or ""
        ct_not_before = getattr(hit, "ct_not_before", None) or ""
        ct_not_after = getattr(hit, "ct_not_after", None) or ""
        ct_entry_timestamp = getattr(hit, "ct_entry_timestamp", None) or ""
        ct_serial_number = getattr(hit, "ct_serial_number", None) or ""

        # Collect all candidate domains from this hit
        candidate_domains: list[str] = []

        # Primary: extract from URL
        domain_from_url = _extract_domain_from_ct_hit(url, title)
        if domain_from_url:
            candidate_domains.append(domain_from_url)

        # Additional: extract from ct_name_value multiline split (F213A)
        # This captures sibling domains when a cert has multiple SANs
        name_value_wildcards: list[str] = []
        for name_val_line, is_wildcard in _extract_domains_from_ct_name_value(ct_name_value):
            if is_wildcard:
                # Wildcard sibling found alongside concrete domains — track for rejection
                name_value_wildcards.append(name_val_line)
                continue
            if name_val_line and name_val_line not in candidate_domains:
                candidate_domains.append(name_val_line)

        # Fallback: use common_name if no domains found yet
        if not candidate_domains and ct_common_name:
            cn = ct_common_name.strip()
            if cn and not _is_wildcard_domain(cn):
                candidate_domains.append(cn.lower())

        # Always initialize ts from retrieved_ts (used in both branches)
        ts = retrieved_ts if retrieved_ts > 0 else time.time()

        if not candidate_domains:
            # Wildcard-only name_value: reject all wildcards before the domain-level rejection
            for _wc in name_value_wildcards:
                rejections.append(REJECTION_WILDCARD_DOMAIN)
                entry = _make_ct_quarantine_entry(
                    domain=_wc,
                    reject_reason=REJECTION_WILDCARD_DOMAIN,
                    query=query,
                    source_url=url,
                    candidate_shape="wildcard",
                    ts=ts,
                )
                if len(ct_quarantine_entries) < MAX_CT_QUARANTINE_SAMPLES:
                    ct_quarantine_entries.append(entry)
            if not url and not title:
                rejections.append(REJECTION_MISSING_VALUE)
                entry = _make_ct_quarantine_entry(
                    domain="",
                    reject_reason=REJECTION_MISSING_VALUE,
                    query=query,
                    source_url=url or "",
                    candidate_shape="missing",
                    ts=ts,
                )
                if len(ct_quarantine_entries) < MAX_CT_QUARANTINE_SAMPLES:
                    ct_quarantine_entries.append(entry)
            else:
                rejections.append(REJECTION_MISSING_DOMAIN)
                entry = _make_ct_quarantine_entry(
                    domain="",
                    reject_reason=REJECTION_MISSING_DOMAIN,
                    query=query,
                    source_url=url or "",
                    candidate_shape="missing",
                    ts=ts,
                )
                if len(ct_quarantine_entries) < MAX_CT_QUARANTINE_SAMPLES:
                    ct_quarantine_entries.append(entry)
            continue

        # Track wildcards already quarantined via name_value_wildcards loop
        # to avoid double-quarantining URL-derived wildcards that also appear in name_value_wildcards
        _quarantined_wildcards: set[str] = set()
        for _wc in name_value_wildcards:
            rejections.append(REJECTION_WILDCARD_DOMAIN)
            entry = _make_ct_quarantine_entry(
                domain=_wc,
                reject_reason=REJECTION_WILDCARD_DOMAIN,
                query=query,
                source_url=url,
                candidate_shape="wildcard",
                ts=ts,
            )
            if len(ct_quarantine_entries) < MAX_CT_QUARANTINE_SAMPLES:
                ct_quarantine_entries.append(entry)
            _quarantined_wildcards.add(_wc.lower())

        for domain in candidate_domains:
            ct_extracted_domains += 1

            # Skip if already quarantined as sibling wildcard (from name_value_wildcards)
            if domain.lower() in _quarantined_wildcards:
                # Already quarantined in name_value_wildcards loop above, skip duplicate
                continue

            # Private/reserved check FIRST — more specific than single-label
            if _is_private_hostname(domain):
                rejections.append(REJECTION_PRIVATE_OR_RESERVED_DOMAIN)
                entry = _make_ct_quarantine_entry(
                    domain=domain,
                    reject_reason=REJECTION_PRIVATE_OR_RESERVED_DOMAIN,
                    query=query,
                    source_url=url,
                    candidate_shape="single_label",
                    ts=ts,
                )
                if len(ct_quarantine_entries) < MAX_CT_QUARANTINE_SAMPLES:
                    ct_quarantine_entries.append(entry)
                continue

            # Single-label (no TLD) check — after private check
            if "." not in domain:
                rejections.append(REJECTION_LOW_INFORMATION)
                entry = _make_ct_quarantine_entry(
                    domain=domain,
                    reject_reason=REJECTION_LOW_INFORMATION,
                    query=query,
                    source_url=url,
                    candidate_shape="single_label",
                    ts=ts,
                )
                if len(ct_quarantine_entries) < MAX_CT_QUARANTINE_SAMPLES:
                    ct_quarantine_entries.append(entry)
                continue

            # Wildcard check — handles URL-derived wildcards (e.g. https://*.example.com/)
            # Wildcards from ct_name_value are already handled via _quarantined_wildcards above
            if _is_wildcard_domain(domain):
                rejections.append(REJECTION_WILDCARD_DOMAIN)
                entry = _make_ct_quarantine_entry(
                    domain=domain,
                    reject_reason=REJECTION_WILDCARD_DOMAIN,
                    query=query,
                    source_url=url,
                    candidate_shape="wildcard",
                    ts=ts,
                )
                if len(ct_quarantine_entries) < MAX_CT_QUARANTINE_SAMPLES:
                    ct_quarantine_entries.append(entry)
                continue

            # Duplicate check
            if domain in seen_domains:
                rejections.append(REJECTION_DUPLICATE_CANDIDATE)
                entry = _make_ct_quarantine_entry(
                    domain=domain,
                    reject_reason=REJECTION_DUPLICATE_CANDIDATE,
                    query=query,
                    source_url=url,
                    candidate_shape=_classify_domain_shape(domain, url, ct_name_value),
                    ts=ts,
                )
                if len(ct_quarantine_entries) < MAX_CT_QUARANTINE_SAMPLES:
                    ct_quarantine_entries.append(entry)
                continue
            seen_domains.add(domain)
            ct_candidate_domains += 1

            ts = retrieved_ts if retrieved_ts > 0 else time.time()
            blake2_id = _make_blake2b_hex(domain, _CT_SALT)
            finding_id = f"ct-{blake2_id}-{sprint_id[:8]}"

            provenance = _build_ct_provenance(
                domain=domain,
                query=query,
                sprint_id=sprint_id,
                issuer_name=ct_issuer_name or None,
                entry_timestamp=ct_entry_timestamp or None,
            )

            payload_text = _build_ct_payload(
                domain=domain,
                issuer_name=ct_issuer_name or None,
                not_before=ct_not_before or None,
                not_after=ct_not_after or None,
                serial_number=ct_serial_number or None,
                entry_timestamp=ct_entry_timestamp or None,
                name_value=ct_name_value or None,
                common_name=ct_common_name or None,
            )

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

    # Tally per-category rejections from the rejection list
    ct_rejected_wildcard = sum(1 for r in rejections if r == REJECTION_WILDCARD_DOMAIN)
    ct_rejected_invalid = sum(
        1 for r in rejections
        if r in (REJECTION_PRIVATE_OR_RESERVED_DOMAIN, REJECTION_LOW_INFORMATION)
    )
    ct_rejected_duplicate = sum(1 for r in rejections if r == REJECTION_DUPLICATE_CANDIDATE)

    telemetry = {
        "ct_raw_entries": ct_raw_entries,
        "ct_extracted_domains": ct_extracted_domains,
        "ct_candidate_domains": ct_candidate_domains,
        "ct_accepted_candidates": len(findings),
        "ct_candidates_built": len(findings),
        "ct_candidates_stored": 0,  # filled by caller via record_storage_results()
        "ct_storage_rejected": 0,  # filled by caller via record_storage_results()
        "ct_rejected_wildcard": ct_rejected_wildcard,
        "ct_rejected_invalid": ct_rejected_invalid,
        "ct_rejected_duplicate": ct_rejected_duplicate,
        "ct_rejected_missing_domain": sum(
            1 for r in rejections if r == REJECTION_MISSING_DOMAIN
        ),
        "ct_rejected_missing_value": sum(
            1 for r in rejections if r == REJECTION_MISSING_VALUE
        ),
        "ct_quarantine_count": len(ct_quarantine_entries),
        "ct_quarantine_entries": ct_quarantine_entries,
    }

    return findings, rejections, telemetry


def record_ct_storage_results(
    telemetry: dict[str, Any],
    storage_results: list[Any],
) -> dict[str, Any]:
    """
    F214A: Merge storage results into CT telemetry after duckdb_store ingest.

    Updates ct_candidates_stored and ct_storage_rejected based on
    storage_results (each item is ActivationResult with .accepted or
    FindingQualityDecision with .accepted=False).

    Call this from sprint_scheduler after async_ingest_findings_batch returns.

    Returns updated telemetry dict (same object, mutated in place).
    """
    stored = 0
    rejected = 0
    for r in storage_results:
        if isinstance(r, dict):
            if r.get("accepted"):
                stored += 1
            else:
                rejected += 1
        elif hasattr(r, "accepted"):
            if r.accepted:
                stored += 1
            else:
                rejected += 1
        else:
            rejected += 1

    telemetry["ct_candidates_stored"] = stored
    telemetry["ct_storage_rejected"] = rejected
    # accepted_candidates reflects what the bridge built (candidates), not stored
    # The caller should use ct_candidates_stored for actual stored count
    return telemetry


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

    # [F213C] Validate input shape before iterating
    if not isinstance(ips, list):
        rejections.append(REJECTION_UNSUPPORTED_SHAPE)
        return [], rejections

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


def summarize_ct_conversion(
    raw_hits_count: int,
    findings: List[Any],
    rejections: List[RejectionReason],
    telemetry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Summarize CT bridge conversion with full rejection taxonomy + F213A telemetry.

    Produces a detailed breakdown of why CT candidates were rejected,
    enabling yield explanation when raw_count > 0 but accepted_count == 0.

    Pure function — no storage, no network, no MLX.

    Args:
        raw_hits_count: Number of raw DiscoveryHit records from crt.sh
        findings: List of CanonicalFinding candidates produced
        rejections: List of CT-specific rejection reason strings
        telemetry: Optional F213A telemetry dict from ct_results_to_findings.
            When provided, authoritative per-category counts are sourced from here.
            Keys: ct_raw_entries, ct_extracted_domains, ct_candidate_domains,
                  ct_accepted_candidates, ct_rejected_wildcard, ct_rejected_invalid,
                  ct_rejected_duplicate

    Returns:
        Bounded summary dict with:
        - rawHits: raw hit count from crt.sh
        - builtCandidates: count of candidates built (= len(findings))
        - acceptedCandidates: count of accepted candidates
        - ctRawEntries: raw entries processed (from telemetry or computed)
        - ctExtractedDomains: total domains extracted from ct_name_value splits
        - ctCandidateDomains: domains that passed structural/information checks
        - rejectedMissingDomain: count rejected for missing_domain
        - rejectedMissingValue: count rejected for missing_value
        - rejectedLowInformation: count rejected for low_information
        - rejectedDuplicateCandidate: count rejected for duplicate_candidate
        - rejectedUnsupportedShape: count rejected for unsupported_shape
        - rejectedWildcardDomain: count rejected for wildcard_domain
        - rejectedPrivateOrReservedDomain: count rejected for private_or_reserved_domain
        - ctRejectedWildcard: from telemetry (F213A)
        - ctRejectedInvalid: from telemetry (F213A)
        - ctRejectedDuplicate: from telemetry (F213A)
        - totalRejected: total rejection count
        - totalProcessed: rawHits minus any early-exit rejections (capped at MAX_BRIDGE_OUTPUT)
        - allRejectionReasons: all unique reasons with counts (capped at 20)
    """
    rejection_counts: dict[str, int] = {}
    for reason in rejections:
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    # Build taxonomy breakdown
    taxonomy = {
        "rejectedMissingDomain": rejection_counts.get(REJECTION_MISSING_DOMAIN, 0),
        "rejectedMissingValue": rejection_counts.get(REJECTION_MISSING_VALUE, 0),
        "rejectedLowInformation": rejection_counts.get(REJECTION_LOW_INFORMATION, 0),
        "rejectedDuplicateCandidate": rejection_counts.get(REJECTION_DUPLICATE_CANDIDATE, 0),
        "rejectedUnsupportedShape": rejection_counts.get(REJECTION_UNSUPPORTED_SHAPE, 0),
        "rejectedWildcardDomain": rejection_counts.get(REJECTION_WILDCARD_DOMAIN, 0),
        "rejectedPrivateOrReservedDomain": rejection_counts.get(
            REJECTION_PRIVATE_OR_RESERVED_DOMAIN, 0
        ),
    }

    total_rejected = sum(taxonomy.values())
    built_candidates = len(findings)

    # totalProcessed: how many were processable from raw hits
    # Cap at MAX_BRIDGE_OUTPUT since that's the processing limit
    total_processed = min(raw_hits_count, MAX_BRIDGE_OUTPUT)

    # All unique reasons capped at 20
    sorted_reasons = sorted(
        rejection_counts.items(),
        key=lambda x: (-x[1], x[0]),
    )
    all_rejection_reasons = dict(sorted_reasons[:20])

    # F213A telemetry fields — sourced from telemetry dict when available
    ct_raw_entries = (telemetry or {}).get("ct_raw_entries", raw_hits_count)
    ct_extracted_domains = (telemetry or {}).get("ct_extracted_domains", 0)
    ct_candidate_domains = (telemetry or {}).get("ct_candidate_domains", 0)
    ct_accepted_candidates = (telemetry or {}).get("ct_accepted_candidates", built_candidates)
    # F214A: candidates built/stored split
    ct_candidates_built = (telemetry or {}).get("ct_candidates_built", built_candidates)
    ct_candidates_stored = (telemetry or {}).get("ct_candidates_stored", 0)
    ct_storage_rejected = (telemetry or {}).get("ct_storage_rejected", 0)
    ct_rejected_wildcard = (telemetry or {}).get("ct_rejected_wildcard", 0)
    ct_rejected_invalid = (telemetry or {}).get("ct_rejected_invalid", 0)
    ct_rejected_duplicate = (telemetry or {}).get("ct_rejected_duplicate", 0)
    ct_rejected_missing_domain = (telemetry or {}).get("ct_rejected_missing_domain", 0)
    ct_rejected_missing_value = (telemetry or {}).get("ct_rejected_missing_value", 0)

    # F214A: ct_raw_sample — up to 5 sanitized raw entries (first 5 hits)
    ct_raw_sample = []
    # Shape keys from all hits (for diagnostics)
    shape_keys: set[str] = set()

    # F214A: ct_conversion_summary — narrative of what happened to raw entries

    return {
        "rawHits": raw_hits_count,
        "builtCandidates": built_candidates,
        "acceptedCandidates": ct_accepted_candidates,
        "ctRawEntries": ct_raw_entries,
        "ctExtractedDomains": ct_extracted_domains,
        "ctCandidateDomains": ct_candidate_domains,
        "ctCandidatesBuilt": ct_candidates_built,
        "ctCandidatesStored": ct_candidates_stored,
        "ctStorageRejected": ct_storage_rejected,
        **taxonomy,
        "ctRejectedWildcard": ct_rejected_wildcard,
        "ctRejectedInvalid": ct_rejected_invalid,
        "ctRejectedDuplicate": ct_rejected_duplicate,
        "ctRejectedMissingDomain": ct_rejected_missing_domain,
        "ctRejectedMissingValue": ct_rejected_missing_value,
        "totalRejected": total_rejected,
        "totalProcessed": total_processed,
        "allRejectionReasons": all_rejection_reasons,
        "ctRawSample": ct_raw_sample,
        "ctConversionSummary": _make_ct_conversion_summary(
            raw_hits_count=raw_hits_count,
            ct_raw_entries=ct_raw_entries,
            built=ct_candidates_built,
            stored=ct_candidates_stored,
            storage_rejected=ct_storage_rejected,
            total_rejected=total_rejected,
        ),
    }
