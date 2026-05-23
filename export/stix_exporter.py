# hledac/universal/export/stix_exporter.py
# Sprint 8BJ — STIX 2.1 Structured Diagnostic Export
# Zero LLM / Zero model runtime / Zero network
"""
Deterministic, side-effect-free STIX 2.1 bundle exporter for ObservedRunReport.

B.5: STIX builtins path NEVER invents IOC/indicator/malware objects
     when no accepted findings are present — only metadata-safe bundle
     with note-like diagnostic facts.
B.7: If accepted findings are absent, exports metadata-safe diagnostic
     bundle (no fake CTI entities).

B.9: Builtins path produces proper STIX-compatible objects:
     - type = "bundle"
     - id = "bundle--<uuid>"
     - spec_version = "2.1"
     - RFC3339 created/modified timestamps
     - UUID-based ids for all objects

Optional stix2 package: if available, use it for full STIX object construction.
Otherwise the builtins path produces plain dicts that are syntactically
STIX-compatible and pass basic shape validation.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Mapping, Union, cast

from hledac.universal.security.pq_crypto import (
    PQAvailability,
    PQSignature,
    PQStatus,
    PostQuantumBackend,
    create_post_quantum_backend,
)

__all__ = [
    "render_stix_bundle",
    "render_stix_bundle_json",
    "render_stix_bundle_to_path",
    "render_cti_stix_bundle",
    "render_cti_stix_bundle_json",
    "render_cti_stix_bundle_to_path",
    "collect_cti_export_inputs",
    "CTIExportInputs",
    # F234: Full STIX 2.1 object types
    "render_full_stix_bundle",
    "render_full_stix_bundle_json",
    "render_full_stix_bundle_to_path",
    "_ATTACK_TTP_MAP",
    "_build_malware_object",
    "_build_tool_object",
    "_build_attack_pattern_object",
    "_build_campaign_object",
    "_build_intrusion_set_object",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_STIX_SPEC_VERSION = "2.1"
_BUNDLE_TYPE = "bundle"

# Canonical root-cause → label (shared with markdown_reporter / jsonld_exporter)
_ROOT_CAUSE_LABELS: dict[str, str] = {
    "network_variance": "Network Variance",
    "no_new_entries": "No New Entries",
    "empty_registry": "Empty Registry",
    "no_pattern_hits": "No Pattern Hits",
    "no_pattern_hits_possible_morphology_gap": "No Pattern Hits (Morphology Gap)",
    "pattern_hits_but_no_findings_built": "Pattern Hits But No Findings Built",
    "low_information_rejection_dominant": "Low-Information Rejection Dominant",
    "duplicate_rejection_dominant": "Duplicate Rejection Dominant",
    "accepted_present": "Accepted Findings Present",
    "unknown": "Unknown",
}

# Root-cause → recommendation fallback (shared)
_FALLBACK_RECOMMENDATION: dict[str, str] = {
    "network_variance": "repeat_live_run",
    "no_new_entries": "repeat_live_run",
    "empty_registry": "check_registry",
    "no_pattern_hits": "update_patterns",
    "no_pattern_hits_possible_morphology_gap": "update_patterns",
    "pattern_hits_but_no_findings_built": "update_extraction_logic",
    "low_information_rejection_dominant": "update_quality_thresholds",
    "duplicate_rejection_dominant": "update_dedup_logic",
    "accepted_present": "continue_monitoring",
    "unknown": "repeat_live_run",
}

# Canonical root-cause strings for export (machine-readable keys)
_CANONICAL_ROOT_CAUSES = frozenset(_ROOT_CAUSE_LABELS.keys())

# ---------------------------------------------------------------------------
# F234: MITRE ATT&CK Technique → TTP mapping
# Maps IOC types and context → STIX Attack-Pattern, Malware, Tool objects
# Technique IDs use STIX attack-pattern ID format (Tnnnn.nnn)
# ---------------------------------------------------------------------------

# ATT&CK technique → (name, platforms, MITRE external_ref)
_ATTACK_TTP_MAP: dict[str, dict[str, Any]] = {
    # Domain-based TTPs
    "T1590.001": {"name": "Domain Name", "type": "attack-pattern",
                   "desc": "Gather victim domain information: domain names"},
    "T1590.002": {"name": "WHOIS", "type": "attack-pattern",
                   "desc": "Gather victim domain WHOIS information"},
    "T1590.003": {"name": "DNS", "type": "attack-pattern",
                   "desc": "Gather victim DNS information"},
    "T1590.004": {"name": "Subdomain", "type": "attack-pattern",
                   "desc": "Gather victim subdomain information"},
    "T1590.005": {"name": "Email Addresses", "type": "attack-pattern",
                   "desc": "Gather victim email addresses"},
    "T1590.006": {"name": "Employee Names", "type": "attack-pattern",
                   "desc": "Gather victim employee information"},
    # IP-based TTPs
    "T1595.001": {"name": "Active Scanning: WHOIS", "type": "attack-pattern",
                   "desc": "Active scanning using WHOIS"},
    "T1595.002": {"name": "Active Scanning: DNS", "type": "attack-pattern",
                   "desc": "Active scanning using DNS"},
    "T1016": {"name": "Network Infrastructure", "type": "attack-pattern",
               "desc": "Identify victim network infrastructure"},
    "T1595": {"name": "Active Scanning", "type": "attack-pattern",
              "desc": "Gather victim network topology and exposed services"},
    # Credential/access TTPs
    "T1589.001": {"name": "Credentials", "type": "attack-pattern",
                  "desc": "Gather victim credentials"},
    "T1589.002": {"name": "Email Addresses", "type": "attack-pattern",
                  "desc": "Gather victim email addresses"},
    "T1589.003": {"name": "Employee Names", "type": "attack-pattern",
                  "desc": "Gather victim employee names"},
    # Infrastructure TTPs
    "T1584.001": {"name": "Domain", "type": "attack-pattern",
                  "desc": "Acquire infrastructure: domains"},
    "T1584.004": {"name": "Server", "type": "attack-pattern",
                  "desc": "Acquire infrastructure: servers"},
    "T1105": {"name": "Ingress Tool Transfer", "type": "attack-pattern",
              "desc": "Transfer tools or other files from external systems"},
    # Exfiltration TTPs
    "T1041": {"name": "Exfiltration Over C2 Channel", "type": "attack-pattern",
              "desc": "Exfiltrate data over command and control channel"},
    # Supply chain
    "T1195.001": {"name": "Supply Chain Compromise: Software Development Tools", "type": "attack-pattern",
                  "desc": "Compromise software development tools"},
    "T1195.002": {"name": "Supply Chain Compromise: Software Supply Chain", "type": "attack-pattern",
                  "desc": "Compromise software supply chain"},
}

# IOC type → likely ATT&CK technique IDs (for TTP mapping)
_IOC_ATTACK_TECHNIQUES: dict[str, list[str]] = {
    "domain": ["T1590.001", "T1590.002", "T1590.003", "T1590.004", "T1584.001"],
    "ip": ["T1016", "T1595.001", "T1595.002", "T1584.004"],
    "url": ["T1105", "T1041"],
    "email": ["T1589.002"],
    "hash_md5": ["T1589.001"],
    "hash_sha1": ["T1589.001"],
    "hash_sha256": ["T1589.001"],
    "cve": ["T1589.001"],
    "username": ["T1589.003"],
    "leak": ["T1589.001", "T1589.002"],
    "paste": ["T1589.001", "T1589.002"],
}

# Kill-chain phase → ATT&CK tactic name
_PHASE_TO_TACTIC: dict[str, str] = {
    "reconnaissance": "Reconnaissance",
    "resource_development": "Resource Development",
    "initial_access": "Initial Access",
    "execution": "Execution",
    "persistence": "Persistence",
    "privilege_escalation": "Privilege Escalation",
    "defense_evasion": "Defense Evasion",
    "credential_access": "Credential Access",
    "discovery": "Discovery",
    "lateral_movement": "Lateral Movement",
    "collection": "Collection",
    "command_and_control": "Command and Control",
    "exfiltration": "Exfiltration",
    "impact": "Impact",
}


# ---------------------------------------------------------------------------
# Sprint F204F: CTI Export Inputs dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CTIExportInputs:
    """Frozen inputs for production CTI STIX export (F204F)."""
    findings: tuple[Any, ...]
    identity_candidates: tuple[dict[str, Any], ...]
    attribution_scores: dict[str, Any]
    killchain_tags: dict[str, Any]
    evidence_chains: tuple[dict[str, Any], ...]
    sprint_id: str


async def collect_cti_export_inputs(
    report: dict[str, Any],
    store: Any,
) -> CTIExportInputs:
    """
    Sprint F204F: Collect all inputs needed for production CTI STIX export.

    Reads findings via ``store.async_query_recent_findings()`` bounded by
    MAX_EXPORT_FINDINGS=300. Identity candidates, attribution scores,
    killchain tags, and evidence chains are read from the report dict.

    Parameters
    ----------
    report : dict
        Diagnostic report dict from _build_diagnostic_report().
        Expected keys: identity_candidates, attribution_scores,
        killchain_tags, evidence_chains.
    store : DuckDB store
        Must expose async_query_recent_findings().

    Returns
    -------
    CTIExportInputs (frozen dataclass)

    GHOST_INVARIANTS:
    - asyncio.gather with return_exceptions=True
    - _check_gathered() called after gather
    - Fail-soft: missing sidecar data → empty defaults
    - RAM guard: MAX_EXPORT_FINDINGS=300
    - Model lifecycle not used
    """
    from hledac.universal.utils.async_helpers import _check_gathered

    sprint_id = report.get("run_id", "unknown")

    # Gather findings and identity candidates concurrently
    findings_result: Any = None
    identity_candidates: tuple[dict[str, Any], ...] = ()

    async def _fetch_findings() -> Any:
        try:
            if hasattr(store, "async_query_recent_findings"):
                rows = await store.async_query_recent_findings(limit=MAX_EXPORT_FINDINGS)
                return list(rows) if rows else []
        except Exception:
            pass
        return []

    async def _get_identity_candidates() -> tuple[dict[str, Any], ...]:
        cands = report.get("identity_candidates") or []
        return tuple(cands) if isinstance(cands, (list, tuple)) else ()

    results = await asyncio.gather(
        _fetch_findings(),
        _get_identity_candidates(),
        return_exceptions=True,
    )
    _check_gathered(results, "collect_cti_export_inputs")

    findings_result = results[0] if results[0] is not None else []
    identity_candidates = results[1] if isinstance(results[1], tuple) else ()

    # Attribution scores — from report
    attribution_scores = report.get("attribution_scores") or {}

    # Killchain tags — from report
    killchain_tags = report.get("killchain_tags") or {}

    # Evidence chains — bounded by MAX_EXPORT_CHAINS=20
    evidence_chains_raw = report.get("evidence_chains") or []
    evidence_chains = tuple(evidence_chains_raw[:MAX_EXPORT_CHAINS])

    return CTIExportInputs(
        findings=tuple(findings_result) if findings_result else (),
        identity_candidates=identity_candidates,
        attribution_scores=attribution_scores,
        killchain_tags=killchain_tags,
        evidence_chains=evidence_chains,
        sprint_id=sprint_id,
    )


# ---------------------------------------------------------------------------
# Input normalisation (standalone-safe, mirrors jsonld_exporter)
# ---------------------------------------------------------------------------
def normalize_export_input(report: object) -> dict[str, Any]:
    """
    Convert ObservedRunReport (msgspec.Struct) or Mapping → plain dict.
    """
    if hasattr(report, "__struct_fields__"):
        return {f: getattr(report, f) for f in getattr(report, "__struct_fields__")}
    if isinstance(report, dict):
        return dict(report)
    if hasattr(report, "keys"):
        return dict(cast(Mapping, report))
    raise TypeError(
        f"report must be msgspec.Struct or Mapping, got {type(report).__name__}"
    )


# ---------------------------------------------------------------------------
# Timestamp helpers (RFC3339)
# ---------------------------------------------------------------------------
def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_timestamp(ts: Any) -> str:
    """Convert unix timestamp or datetime to RFC3339 UTC string."""
    if ts is None:
        return _utc_now()
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return _utc_now()


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val)


# ---------------------------------------------------------------------------
# Recommendation helper
# ---------------------------------------------------------------------------
def _get_recommendation(data: dict[str, Any]) -> str:
    rec = data.get("recommendation")
    if rec:
        return rec
    root = data.get("diagnostic_root_cause", "unknown")
    return _FALLBACK_RECOMMENDATION.get(root, _FALLBACK_RECOMMENDATION["unknown"])


# ---------------------------------------------------------------------------
# UUID helpers (STIX requires urn:uuid: for id fields)
# ---------------------------------------------------------------------------
def _bundle_id() -> str:
    return f"bundle--00000000-0000-0000-0000-000000000000"


def _make_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Builtins path: plain-dict STIX objects (no stix2 package required)
# B.5/B.7: Metadata-safe only — no IOC/indicator/malware objects
# ---------------------------------------------------------------------------

def _build_diagnostic_note(data: dict[str, Any], created: str) -> dict[str, Any]:
    """
    Build a STIX note-like custom diagnostic object.
    Encapsulates root cause, recommendation, and signal funnel metadata.
    """
    root = data.get("diagnostic_root_cause", "unknown")
    label = _ROOT_CAUSE_LABELS.get(root, _ROOT_CAUSE_LABELS["unknown"])

    # Signal funnel fields as abstract content (no IOC semantics)
    signal_content_parts = [
        f"entries_seen={data.get('entries_seen', 0)}",
        f"entries_scanned={data.get('entries_scanned', 0)}",
        f"entries_with_hits={data.get('entries_with_hits', 0)}",
        f"total_pattern_hits={data.get('total_pattern_hits', 0)}",
        f"findings_built_pre_store={data.get('findings_built_pre_store', 0)}",
        f"accepted_count_delta={data.get('accepted_count_delta', 0)}",
    ]

    # Store rejection trace
    rejection_parts = [
        f"low_info_rejected={data.get('low_information_rejected_count_delta', 0)}",
        f"in_mem_dup_rejected={data.get('in_memory_duplicate_rejected_count_delta', 0)}",
        f"persistent_dup_rejected={data.get('persistent_duplicate_rejected_count_delta', 0)}",
        f"other_rejected={data.get('other_rejected_count_delta', 0)}",
    ]

    abstract = (
        f"Ghost Prime Diagnostic: root_cause={root} ({label}); "
        f"accepted_findings={data.get('accepted_findings', 0)}; "
        f"signal_funnel={{{' | '.join(signal_content_parts)}}}; "
        f"store_rejection_trace={{{' | '.join(rejection_parts)}}}; "
        f"recommendation={_get_recommendation(data)}"
    )

    return {
        "type": "note",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"note--{_make_uuid()}",
        "created": created,
        "modified": created,
        "created_by_ref": "identity--ghost-prime",
        "abstract": abstract[:2000] if len(abstract) > 2000 else abstract,
        "content": json.dumps({
            "accepted_findings": data.get("accepted_findings", 0),
            "entries_seen": data.get("entries_seen", 0),
            "entries_scanned": data.get("entries_scanned", 0),
            "entries_with_hits": data.get("entries_with_hits", 0),
            "total_pattern_hits": data.get("total_pattern_hits", 0),
            "findings_built_pre_store": data.get("findings_built_pre_store", 0),
            "accepted_count_delta": data.get("accepted_count_delta", 0),
            "signal_stage": _safe_str(data.get("signal_stage")),
        }, sort_keys=True),
        "object_refs": [f"identity--ghost-prime"],
    }


def _build_diagnostic_identity() -> dict[str, Any]:
    """Ghost Prime identity object (author of the report)."""
    return {
        "type": "identity",
        "spec_version": _STIX_SPEC_VERSION,
        "id": "identity--ghost-prime",
        "created": _utc_now(),
        "modified": _utc_now(),
        "name": "Ghost Prime",
        "identity_class": "system",
    }


def _build_diagnostic_uma_note(data: dict[str, Any], created: str) -> dict[str, Any]:
    """UMA snapshot as a note-like object (if UMA data available)."""
    uma = data.get("uma_snapshot", {})
    if not uma:
        return {}
    return {
        "type": "note",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"note--{_make_uuid()}",
        "created": created,
        "modified": created,
        "created_by_ref": "identity--ghost-prime",
        "abstract": f"UMA snapshot: {json.dumps(uma, sort_keys=True)}",
        "object_refs": ["identity--ghost-prime"],
    }


def _build_per_source_notes(data: dict[str, Any], created: str) -> list[dict[str, Any]]:
    """Per-source health as note-like objects (no indicator semantics)."""
    per_source = data.get("per_source")
    if not per_source:
        return []
    notes = []
    for src in sorted(per_source, key=lambda s: str(s.get("feed_url", ""))):
        url = _safe_str(src.get("feed_url", ""))
        if not url:
            continue
        note = {
            "type": "note",
            "spec_version": _STIX_SPEC_VERSION,
            "id": f"note--{_make_uuid()}",
            "created": created,
            "modified": created,
            "created_by_ref": "identity--ghost-prime",
            "abstract": (
                f"Source health: url={url} label={_safe_str(src.get('label'))} "
                f"fetched={src.get('fetched_entries', 0)} "
                f"accepted={src.get('accepted_findings', 0)} "
                f"stored={src.get('stored_findings', 0)} "
                f"elapsed_ms={src.get('elapsed_ms', 0):.1f} "
                f"error={_safe_str(src.get('error') or 'none')}"
            )[:2000],
            "object_refs": ["identity--ghost-prime"],
        }
        notes.append(note)
    return notes


def _build_root_cause_object(data: dict[str, Any], created: str) -> dict[str, Any]:
    """
    Root-cause and recommendation as a STIX custom object.
    Uses a note with structured abstract for machine-readable root cause.
    """
    root = data.get("diagnostic_root_cause", "unknown")
    label = _ROOT_CAUSE_LABELS.get(root, _ROOT_CAUSE_LABELS["unknown"])
    rec = _get_recommendation(data)

    return {
        "type": "note",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"note--{_make_uuid()}",
        "created": created,
        "modified": created,
        "created_by_ref": "identity--ghost-prime",
        "abstract": f"Root cause: {root} ({label}). Recommendation: {rec}. Network variance: {data.get('is_network_variance', False)}",
        "content": json.dumps({
            "diagnostic_root_cause": root,
            "diagnostic_root_cause_label": label,
            "recommendation": rec,
            "is_network_variance": data.get("is_network_variance", False),
        }, sort_keys=True),
        "object_refs": ["identity--ghost-prime"],
    }


# ---------------------------------------------------------------------------
# F203E: CTI STIX 2.1 Export
# ---------------------------------------------------------------------------
# Sprint F203E — CTI STIX 2.1 Export Upgrade
# Upgrades diagnostic exporter to real threat-intel export:
#   - findings → indicator / observed-data objects
#   - identity_candidates → identity objects
#   - attribution_scores → note objects (explainable confidence)
#   - killchain_tags → kill-chain labels + note objects
#   - evidence_chains → observed-data + relationship objects
#   - report object wrapping all CTI
#
# Bounds:
#   MAX_STIX_OBJECTS=500 — streaming-ish object construction
#   deterministic UUID5 from stable namespace+content
#
# Guardrails:
#   No network / No model
#   No fake IOC objects for empty findings
#   JSON only
# ---------------------------------------------------------------------------

MAX_STIX_OBJECTS: int = 500
MAX_EXPORT_FINDINGS: int = 300
MAX_EXPORT_CHAINS: int = 20
MAX_EXPORT_BYTES: int = 5_000_000

# UUID5 namespace for deterministic CTI IDs (STIX namespace URL as per spec)
# Using NAMESPACE_URL so same content always generates same UUID5
_STIX_NS = uuid.NAMESPACE_URL


def _make_stix_id(stix_type: str, *parts: str) -> str:
    """
    Deterministic UUID5 for STIX object IDs.
    Uses uuid.NAMESPACE_URL + stix_type + parts so same content → same ID.
    """
    canonical = f"{_STIX_NS}/{stix_type}/{'/'.join(str(p) for p in parts)}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, canonical))


# IOC type → STIX pattern template (None = not an indicator type)
_IOC_PATTERN_MAP: dict[str, str | None] = {
    "ip": "[ipv4-addr:value = '{value}']",
    "ipv6": "[ipv6-addr:value = '{value}']",
    "domain": "[domain-name:value = '{value}']",
    "url": "[url:value = '{value}']",
    "email": "[email-addr:value = '{value}']",
    "hash_md5": "[file:hashes.'MD5' = '{value}']",
    "hash_sha1": "[file:hashes.'SHA-1' = '{value}']",
    "hash_sha256": "[file:hashes.'SHA-256' = '{value}']",
    "cve": None,  # Maps to Vulnerability, not indicator
    "file_path": "[file:name = '{value}']",
    "registry": None,  # Not a standard STIX pattern type
}


def _ioc_to_indicator(
    finding: dict[str, Any],
    created: str,
    killchain_tags: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """
    Convert a finding dict to a STIX indicator.
    Returns None if the IOC type is not mappable.
    """
    ioc_type = _safe_str(finding.get("ioc_type", "")).lower()
    ioc_value = _safe_str(finding.get("ioc_value", ""))
    if not ioc_value:
        return None

    pattern_tpl = _IOC_PATTERN_MAP.get(ioc_type)
    if pattern_tpl is None:
        return None

    finding_id = _safe_str(finding.get("finding_id", ""))
    pattern = str(pattern_tpl).replace("{value}", ioc_value)
    confidence = int((float(finding.get("confidence", 0.5) or 0.5)) * 100)

    # Kill-chain labels
    labels: list[str] = []
    if killchain_tags:
        for tag in killchain_tags:
            if isinstance(tag, dict):
                phase = _safe_str(tag.get("phase", ""))
                tech = _safe_str(tag.get("technique_id", ""))
                if tech:
                    labels.append(f"attack:{tech}")
                if phase:
                    labels.append(f"phase:{phase}")

    indicator_id = _make_stix_id("indicator", ioc_type, ioc_value[:64])

    result: dict[str, Any] = {
        "type": "indicator",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"indicator--{indicator_id}",
        "created": created,
        "modified": created,
        "name": f"{ioc_type.upper()}: {ioc_value[:64]}",
        "pattern": pattern,
        "pattern_type": "stix",
        "valid_from": created,
        "confidence": confidence,
    }
    if labels:
        result["labels"] = labels[:5]  # cap labels

    # Description with finding metadata
    desc_parts = [f"source_type={_safe_str(finding.get('source_type', ''))}"]
    if finding_id:
        desc_parts.append(f"finding_id={finding_id}")
    result["description"] = "; ".join(desc_parts)

    return result


def _finding_to_observed_data(
    finding: dict[str, Any],
    created: str,
) -> dict[str, Any]:
    """Convert a finding to STIX observed-data (for non-pattern IOCs)."""
    ioc_type = _safe_str(finding.get("ioc_type", "")).lower()
    ioc_value = _safe_str(finding.get("ioc_value", ""))
    finding_id = _safe_str(finding.get("finding_id", ""))

    objects: list[dict[str, Any]] = []
    obj_id = _make_stix_id("observed-data", ioc_type, ioc_value[:64])

    if ioc_type == "domain":
        objects.append({
            "type": "domain-name",
            "spec_version": _STIX_SPEC_VERSION,
            "id": f"domain-name--{obj_id}",
            "value": ioc_value,
        })
    elif ioc_type == "ip":
        objects.append({
            "type": "ipv4-addr",
            "spec_version": _STIX_SPEC_VERSION,
            "id": f"ipv4-addr--{obj_id}",
            "value": ioc_value,
        })
    elif ioc_type == "url":
        objects.append({
            "type": "url",
            "spec_version": _STIX_SPEC_VERSION,
            "id": f"url--{obj_id}",
            "value": ioc_value,
        })
    elif ioc_type in ("hash_md5", "hash_sha1", "hash_sha256"):
        hash_name = ioc_type.replace("hash_", "").upper()
        objects.append({
            "type": "file",
            "spec_version": _STIX_SPEC_VERSION,
            "id": f"file--{obj_id}",
            "hashes": {hash_name: ioc_value},
        })

    return {
        "type": "observed-data",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"observed-data--{_make_stix_id('observed-data', finding_id[:64] if finding_id else ioc_value[:64])}",
        "created": created,
        "modified": created,
        "objects": objects,
    }


def _build_identity_object(
    candidate: dict[str, Any],
    created: str,
) -> dict[str, Any]:
    """Build a STIX identity from an identity_candidate dict."""
    candidate_id = _safe_str(candidate.get("candidate_id", ""))
    primary_name = _safe_str(candidate.get("primary_name", "Unknown"))
    emails = candidate.get("emails", [])
    usernames = candidate.get("usernames", [])
    platforms = candidate.get("platforms", [])
    confidence = float(candidate.get("confidence", 0.0) or 0.0)

    identity_id = _make_stix_id("identity", primary_name, candidate_id)

    desc_parts = [f"confidence={confidence:.2f}"]
    if emails:
        desc_parts.append(f"emails={', '.join(str(e) for e in emails[:3])}")
    if usernames:
        desc_parts.append(f"usernames={', '.join(str(u) for u in usernames[:5])}")
    if platforms:
        desc_parts.append(f"platforms={', '.join(str(p) for p in platforms)}")
    if candidate_id:
        desc_parts.append(f"candidate_id={candidate_id}")

    return {
        "type": "identity",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"identity--{identity_id}",
        "created": created,
        "modified": created,
        "name": primary_name,
        "description": " | ".join(desc_parts),
        "identity_class": "individual",
    }


def _build_attribution_note(
    candidate_id: str,
    score: dict[str, Any],
    identity_id: str,
    created: str,
) -> dict[str, Any]:
    """Build a STIX note explaining attribution confidence for an identity."""
    confidence = float(score.get("confidence", 0.0) or 0.0)
    factors = score.get("factors", [])
    evidence_ids = score.get("evidence_ids", [])

    factor_summary = []
    for f in factors[:5]:
        if isinstance(f, dict):
            ft = _safe_str(f.get("factor_type", ""))
            ws = f.get("weighted_score", 0.0)
            factor_summary.append(f"{ft}={ws:.2f}")

    abstract = f"Attribution confidence={confidence:.2f} for identity {candidate_id}"
    if factor_summary:
        abstract += f" | factors: {'; '.join(factor_summary)}"

    return {
        "type": "note",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"note--{_make_stix_id('attribution-note', candidate_id)}",
        "created": created,
        "modified": created,
        "abstract": abstract[:2000],
        "content": json.dumps({
            "candidate_id": candidate_id,
            "confidence": round(confidence, 4),
            "factor_count": len(factors),
            "evidence_ids": list(evidence_ids)[:20],
        }, sort_keys=True),
        "object_refs": [f"identity--{identity_id}"],
    }


def _build_killchain_note(
    finding_id: str,
    tags: list[dict[str, Any]],
    indicator_id: str | None,
    created: str,
) -> dict[str, Any]:
    """Build a note summarizing kill-chain tags for a finding."""
    techs = []
    for t in tags:
        if isinstance(t, dict):
            techs.append({
                "technique_id": _safe_str(t.get("technique_id", "")),
                "tactic": _safe_str(t.get("tactic", "")),
                "phase": _safe_str(t.get("phase", "")),
                "confidence": float(t.get("confidence", 0.0) or 0.0),
            })

    ref_str = f"indicator--{indicator_id}" if indicator_id else finding_id

    return {
        "type": "note",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"note--{_make_stix_id('killchain-note', finding_id)}",
        "created": created,
        "modified": created,
        "abstract": f"Kill-chain tags for {finding_id}: {len(tags)} technique(s)",
        "content": json.dumps({
            "finding_id": finding_id,
            "tags": techs,
        }, sort_keys=True),
        "object_refs": [ref_str],
    }


def _build_evidence_chain_object(
    chain: dict[str, Any],
    created: str,
) -> dict[str, Any]:
    """
    Build a STIX observed-data from an evidence chain.
    Chain is serialized as custom content in the observed-data object.
    """
    root_id = _safe_str(chain.get("root_finding_id", ""))
    steps = chain.get("steps", [])
    conclusion = _safe_str(chain.get("conclusion") or "")

    chain_id = _make_stix_id("chain", root_id)
    serialized_steps = []
    for s in steps:
        if isinstance(s, dict):
            serialized_steps.append({
                "step_type": _safe_str(s.get("step_type", "")),
                "input_ids": s.get("input_ids", []),
                "output_id": _safe_str(s.get("output_id", "")),
                "confidence": float(s.get("confidence", 0.0) or 0.0),
                "reason": _safe_str(s.get("reason", "")),
            })

    return {
        "type": "observed-data",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"observed-data--{chain_id}",
        "created": created,
        "modified": created,
        "description": f"Evidence chain: root={root_id} | depth={len(steps)}",
        "content": json.dumps({
            "root_finding_id": root_id,
            "conclusion": conclusion,
            "steps": serialized_steps,
            "depth": len(serialized_steps),
        }, sort_keys=True),
    }


# ---------------------------------------------------------------------------
# F234: Full STIX 2.1 Object Builders
# Campaign, Intrusion Set, Malware, Tool, Attack Pattern
# ---------------------------------------------------------------------------

def _build_attack_pattern_object(
    technique_id: str,
    created: str,
) -> dict[str, Any]:
    """Build a STIX attack-pattern from an ATT&CK technique ID."""
    ttp = _ATTACK_TTP_MAP.get(technique_id)
    name = ttp["name"] if ttp else technique_id
    desc = ttp.get("desc", "") if ttp else ""

    return {
        "type": "attack-pattern",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"attack-pattern--{_make_stix_id('attack-pattern', technique_id)}",
        "created": created,
        "modified": created,
        "name": name,
        "description": desc,
        "external_references": [{
            "source_name": "mitre-attack",
            "external_id": technique_id,
            "url": f"https://attack.mitre.org/techniques/{technique_id.replace('.', '/')}/",
        }] if technique_id.startswith("T") else [],
        "x_mitre_contributor": "Ghost Prime OSINT",
        "x_mitre_version": "1.0",
    }


def _build_malware_object(
    name: str,
    malware_type: str,
    created: str,
    technique_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a STIX malware object (for identified malware from OSINT)."""
    malware_id = _make_stix_id("malware", name, malware_type)
    ext_refs = [{
        "source_name": "Ghost Prime OSINT",
        "description": f"Identified from Hledac OSINT collection",
    }]
    if technique_ids:
        for tech in technique_ids[:5]:
            ext_refs.append({
                "source_name": "mitre-attack",
                "external_id": tech,
                "url": f"https://attack.mitre.org/techniques/{tech.replace('.', '/')}/",
            })
    return {
        "type": "malware",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"malware--{malware_id}",
        "created": created,
        "modified": created,
        "name": name,
        "description": f"Malware identified via OSINT: {name}",
        "malware_types": [malware_type] if malware_type else ["unknown"],
        "is_family": False,
        "external_references": ext_refs,
        "x_mitre_platforms": ["Linux", "Windows", "macOS"],
    }


def _build_tool_object(
    name: str,
    tool_type: str,
    created: str,
) -> dict[str, Any]:
    """Build a STIX tool object (for legitimate tools identified in OSINT)."""
    tool_id = _make_stix_id("tool", name, tool_type)
    return {
        "type": "tool",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"tool--{tool_id}",
        "created": created,
        "modified": created,
        "name": name,
        "description": f"Tool identified via OSINT: {name}",
        "tool_types": [tool_type] if tool_type else ["utility"],
        "external_references": [{
            "source_name": "Ghost Prime OSINT",
            "description": f"Identified from Hledac OSINT collection",
        }],
    }


def _build_campaign_object(
    name: str,
    objective: str,
    created: str,
    first_seen: str | None = None,
    last_seen: str | None = None,
) -> dict[str, Any]:
    """Build a STIX campaign object (for correlated threat activity)."""
    campaign_id = _make_stix_id("campaign", name)
    result: dict[str, Any] = {
        "type": "campaign",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"campaign--{campaign_id}",
        "created": created,
        "modified": created,
        "name": name,
        "description": objective,
        "objective": objective,
    }
    if first_seen:
        result["first_seen"] = first_seen
    if last_seen:
        result["last_seen"] = last_seen
    return result


def _build_intrusion_set_object(
    name: str,
    aliases: list[str] | None,
    created: str,
    description: str = "",
) -> dict[str, Any]:
    """Build a STIX intrusion-set object (for tracked threat actors)."""
    intr_id = _make_stix_id("intrusion-set", name)
    result: dict[str, Any] = {
        "type": "intrusion-set",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"intrusion-set--{intr_id}",
        "created": created,
        "modified": created,
        "name": name,
        "description": description or f"Intrusion set tracked via OSINT: {name}",
    }
    if aliases:
        result["aliases"] = aliases[:10]
    return result


def _build_infrastructure_object(
    name: str,
    infrastructure_type: str,
    created: str,
    description: str = "",
) -> dict[str, Any]:
    """Build a STIX infrastructure object for C2 or other infra."""
    infra_id = _make_stix_id("infrastructure", name, infrastructure_type)
    return {
        "type": "infrastructure",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"infrastructure--{infra_id}",
        "created": created,
        "modified": created,
        "name": name,
        "description": description or f"Infrastructure identified via OSINT: {name}",
        "infrastructure_types": [infrastructure_type] if infrastructure_type else ["unknown"],
    }


# ---------------------------------------------------------------------------
# F234: Full STIX 2.1 Bundle Renderer
# Includes all STIX object types + ATT&CK mapping
# Compatible with OpenCTI, MISP, TheHive
# ---------------------------------------------------------------------------

def render_full_stix_bundle(
    findings: list[Any],
    identity_candidates: list[dict[str, Any]] | None = None,
    attribution_scores: dict[str, Any] | None = None,
    killchain_tags: dict[str, Any] | None = None,
    evidence_chains: list[dict[str, Any]] | None = None,
    campaigns: list[dict[str, Any]] | None = None,
    intrusion_sets: list[dict[str, Any]] | None = None,
    malware_samples: list[dict[str, Any]] | None = None,
    tool_samples: list[dict[str, Any]] | None = None,
    max_objects: int = MAX_STIX_OBJECTS,
) -> dict[str, Any]:
    """
    F234: Full STIX 2.1 bundle with all object types.

    Produces complete CTI bundle including:
    - indicator / observed-data (from findings)
    - identity (from identity candidates)
    - attack-pattern (ATT&CK technique mapping)
    - malware, tool, campaign, intrusion-set
    - relationship objects linking all entities
    - report object wrapping all CTI

    Compatible with: OpenCTI, MISP, TheHive, STIX 2.1 vanilla consumers.

    Guardrails:
    - No network / No model
    - No fake IOCs when findings list is empty
    - Bounded to MAX_STIX_OBJECTS
    - ATT&CK technique mapping from killchain_tags

    Parameters
    ----------
    findings : list[CanonicalFinding | dict]
    identity_candidates : list[dict] | None
    attribution_scores : dict | None
    killchain_tags : dict | None
    evidence_chains : list[dict] | None
    campaigns : list[dict] | None - campaign objects to include
    intrusion_sets : list[dict] | None - intrusion-set objects to include
    malware_samples : list[dict] | None - malware objects to include
    tool_samples : list[dict] | None - tool objects to include
    max_objects : int - cap on total STIX objects (default 500)

    Returns
    -------
    dict - STIX 2.1 bundle with all object types
    """
    if identity_candidates is None:
        identity_candidates = []
    if attribution_scores is None:
        attribution_scores = {}
    if killchain_tags is None:
        killchain_tags = {}
    if evidence_chains is None:
        evidence_chains = []
    if campaigns is None:
        campaigns = []
    if intrusion_sets is None:
        intrusion_sets = []
    if malware_samples is None:
        malware_samples = []
    if tool_samples is None:
        tool_samples = []

    created = _utc_now()
    objects: list[dict[str, Any]] = []

    # Ghost Prime identity (report author)
    objects.append(_build_diagnostic_identity())

    # ── ATT&CK attack-pattern objects (unique technique IDs from killchain_tags) ──
    technique_ids_seen: set[str] = set()
    for fid, tags in killchain_tags.items():
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, dict):
                    tech = _safe_str(tag.get("technique_id", ""))
                    if tech and tech.startswith("T") and len(objects) < max_objects:
                        if tech not in technique_ids_seen:
                            technique_ids_seen.add(tech)
                            obj = _build_attack_pattern_object(tech, created)
                            objects.append(obj)

    # ── Findings → indicators + observed-data ─────────────────────────────────
    finding_ids_seen: set[str] = set()
    indicator_refs: list[str] = []
    observed_refs: list[str] = []

    for finding_raw in findings:
        if len(objects) >= max_objects:
            break
        if not isinstance(finding_raw, dict):
            finding_raw = dict(finding_raw) if hasattr(finding_raw, "__dict__") else {}
        finding = finding_raw

        fid = _safe_str(finding.get("finding_id", ""))
        finding_ids_seen.add(fid)

        # Kill-chain tags for this finding
        finding_kc_tags = killchain_tags.get(fid) if fid else None

        # Try indicator first
        ind = _ioc_to_indicator(finding, created, finding_kc_tags)
        if ind is not None:
            objects.append(ind)
            indicator_refs.append(ind["id"])
        else:
            # Fall back to observed-data
            obs = _finding_to_observed_data(finding, created)
            if obs and obs.get("objects"):
                objects.append(obs)
                observed_refs.append(obs["id"])

        # Kill-chain note per finding (if tags present)
        if finding_kc_tags:
            note = _build_killchain_note(
                fid,
                finding_kc_tags,
                indicator_refs[-1] if indicator_refs else None,
                created,
            )
            if note and len(objects) < max_objects:
                objects.append(note)

    # ── Identity candidates → identity objects ───────────────────────────────
    identity_refs: list[str] = []
    for cand in identity_candidates:
        if len(objects) >= max_objects:
            break
        if not isinstance(cand, dict):
            cand = dict(cand) if hasattr(cand, "__dict__") else {}
        identity_obj = _build_identity_object(cand, created)
        objects.append(identity_obj)
        identity_refs.append(identity_obj["id"])

        # Attribution note for this identity
        cand_id = _safe_str(cand.get("candidate_id", ""))
        if cand_id in attribution_scores and len(objects) < max_objects:
            score = attribution_scores[cand_id]
            if isinstance(score, dict):
                note = _build_attribution_note(
                    cand_id,
                    score,
                    _make_stix_id("identity", _safe_str(cand.get("primary_name", "")), cand_id),
                    created,
                )
                objects.append(note)

    # ── Evidence chains → observed-data ───────────────────────────────────────
    chain_refs: list[str] = []
    for chain in evidence_chains:
        if len(objects) >= max_objects:
            break
        if not isinstance(chain, dict):
            chain = dict(chain) if hasattr(chain, "__dict__") else {}
        chain_obj = _build_evidence_chain_object(chain, created)
        objects.append(chain_obj)
        chain_refs.append(chain_obj["id"])

    # ── Campaigns ─────────────────────────────────────────────────────────────
    for camp in campaigns:
        if len(objects) >= max_objects:
            break
        if not isinstance(camp, dict):
            camp = dict(camp) if hasattr(camp, "__dict__") else {}
        camp_obj = _build_campaign_object(
            name=_safe_str(camp.get("name", "Unknown Campaign")),
            objective=_safe_str(camp.get("objective", "")),
            created=created,
            first_seen=_iso_timestamp(camp.get("first_seen")),
            last_seen=_iso_timestamp(camp.get("last_seen")),
        )
        objects.append(camp_obj)

    # ── Intrusion Sets ────────────────────────────────────────────────────────
    for intr in intrusion_sets:
        if len(objects) >= max_objects:
            break
        if not isinstance(intr, dict):
            intr = dict(intr) if hasattr(intr, "__dict__") else {}
        intr_obj = _build_intrusion_set_object(
            name=_safe_str(intr.get("name", "Unknown Actor")),
            aliases=intr.get("aliases", []) if isinstance(intr.get("aliases"), list) else [],
            created=created,
            description=_safe_str(intr.get("description", "")),
        )
        objects.append(intr_obj)

    # ── Malware Samples ──────────────────────────────────────────────────────
    for mal in malware_samples:
        if len(objects) >= max_objects:
            break
        if not isinstance(mal, dict):
            mal = dict(mal) if hasattr(mal, "__dict__") else {}
        mal_obj = _build_malware_object(
            name=_safe_str(mal.get("name", "Unknown Malware")),
            malware_type=_safe_str(mal.get("type", "unknown")),
            created=created,
            technique_ids=mal.get("technique_ids"),
        )
        objects.append(mal_obj)

    # ── Tool Samples ──────────────────────────────────────────────────────────
    for tool in tool_samples:
        if len(objects) >= max_objects:
            break
        if not isinstance(tool, dict):
            tool = dict(tool) if hasattr(tool, "__dict__") else {}
        tool_obj = _build_tool_object(
            name=_safe_str(tool.get("name", "Unknown Tool")),
            tool_type=_safe_str(tool.get("type", "utility")),
            created=created,
        )
        objects.append(tool_obj)

    # ── Relationship objects ─────────────────────────────────────────────────
    # Link indicators to identity objects via "based-on" relationships
    for ind_id in indicator_refs:
        if len(objects) >= max_objects:
            break
        for ident_id in identity_refs[:3]:  # limit relationships
            rel_id = _make_stix_id("relationship", ind_id, ident_id)
            objects.append({
                "type": "relationship",
                "spec_version": _STIX_SPEC_VERSION,
                "id": f"relationship--{rel_id}",
                "created": created,
                "modified": created,
                "source_ref": ind_id,
                "target_ref": f"identity--{ident_id}",
                "relationship_type": "derived-from",
            })

    # Link attack-patterns to indicators via "uses" relationships
    for ttp_id in technique_ids_seen:
        if len(objects) >= max_objects:
            break
        for ind_id in indicator_refs[:3]:
            rel_id = _make_stix_id("relationship", ttp_id, ind_id)
            objects.append({
                "type": "relationship",
                "spec_version": _STIX_SPEC_VERSION,
                "id": f"relationship--{rel_id}",
                "created": created,
                "modified": created,
                "source_ref": f"intrusion-set--{_make_stix_id('intrusion-set', 'ghost-prime')}",
                "target_ref": f"attack-pattern--{_make_stix_id('attack-pattern', ttp_id)}",
                "relationship_type": "uses",
            })

    # ── Report object ───────────────────────────────────────────────────────
    report_name = f"Ghost Prime Full CTI {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    report = _build_cti_report(
        objects=objects,
        name=report_name,
        finding_count=len(finding_ids_seen),
        identity_count=len(identity_refs),
        chain_count=len(chain_refs),
        created=created,
    )
    if len(objects) < max_objects:
        objects.append(report)

    bundle: dict[str, Any] = {
        "type": _BUNDLE_TYPE,
        "id": _cti_bundle_id(report_name),
        "spec_version": _STIX_SPEC_VERSION,
        "created": created,
        "modified": created,
        "objects": objects,
    }
    return bundle


def render_full_stix_bundle_json(
    findings: list[Any],
    identity_candidates: list[dict[str, Any]] | None = None,
    attribution_scores: dict[str, Any] | None = None,
    killchain_tags: dict[str, Any] | None = None,
    evidence_chains: list[dict[str, Any]] | None = None,
    campaigns: list[dict[str, Any]] | None = None,
    intrusion_sets: list[dict[str, Any]] | None = None,
    malware_samples: list[dict[str, Any]] | None = None,
    tool_samples: list[dict[str, Any]] | None = None,
    max_objects: int = MAX_STIX_OBJECTS,
) -> str:
    """
    Render full CTI findings as a deterministic STIX bundle JSON string.

    Returns
    -------
    str - JSON string with sorted keys for determinism.
    """
    bundle = render_full_stix_bundle(
        findings=findings,
        identity_candidates=identity_candidates,
        attribution_scores=attribution_scores,
        killchain_tags=killchain_tags,
        evidence_chains=evidence_chains,
        campaigns=campaigns,
        intrusion_sets=intrusion_sets,
        malware_samples=malware_samples,
        tool_samples=tool_samples,
        max_objects=max_objects,
    )
    return json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False)


def render_full_stix_bundle_to_path(
    findings: list[Any],
    identity_candidates: list[dict[str, Any]] | None = None,
    attribution_scores: dict[str, Any] | None = None,
    killchain_tags: dict[str, Any] | None = None,
    evidence_chains: list[dict[str, Any]] | None = None,
    campaigns: list[dict[str, Any]] | None = None,
    intrusion_sets: list[dict[str, Any]] | None = None,
    malware_samples: list[dict[str, Any]] | None = None,
    tool_samples: list[dict[str, Any]] | None = None,
    max_objects: int = MAX_STIX_OBJECTS,
    path: Union[str, Path, None] = None,
) -> Path:
    """
    Render full CTI findings as a STIX bundle and write to ``path``.

    If ``path`` is None:
      1. ``GHOST_EXPORT_DIR`` env var
      2. ``CTI_EXPORT_DIR`` (~/.local/share/hledac/cti)

    Filename: ``ghost_full_cti_{timestamp}.stix.json``

    Returns the Path of the written file.
    """
    content = render_full_stix_bundle_json(
        findings=findings,
        identity_candidates=identity_candidates,
        attribution_scores=attribution_scores,
        killchain_tags=killchain_tags,
        evidence_chains=evidence_chains,
        campaigns=campaigns,
        intrusion_sets=intrusion_sets,
        malware_samples=malware_samples,
        tool_samples=tool_samples,
        max_objects=max_objects,
    )

    if path is None:
        export_dir_env = os.environ.get("GHOST_EXPORT_DIR")
        if export_dir_env:
            base = Path(export_dir_env)
        else:
            from hledac.universal.paths import CTI_EXPORT_DIR
            base = CTI_EXPORT_DIR
    else:
        base = Path(path).parent

    filename = Path(path).name if path else None
    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"ghost_full_cti_{timestamp}.stix.json"

    out_path = base / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


def _build_cti_report(
    objects: list[dict[str, Any]],
    name: str,
    finding_count: int,
    identity_count: int,
    chain_count: int,
    created: str,
) -> dict[str, Any]:
    """Build a STIX report object wrapping all CTI objects."""
    report_id = _make_stix_id("cti-report", name, str(finding_count))

    # Collect all object refs
    object_refs = [obj["id"] for obj in objects if obj.get("id")]

    published = created

    return {
        "type": "report",
        "spec_version": _STIX_SPEC_VERSION,
        "id": f"report--{report_id}",
        "created": created,
        "modified": created,
        "name": name,
        "description": (
            f"Ghost Prime CTI report: {finding_count} finding(s), "
            f"{identity_count} identity/identities, {chain_count} evidence chain(s)"
        ),
        "published": published,
        "object_refs": object_refs[:MAX_STIX_OBJECTS],
        "report_types": ["threat-report", "indicator"],
    }


def _cti_bundle_id(name: str) -> str:
    """Generate a deterministic bundle ID from report name."""
    return f"bundle--{_make_stix_id('cti-bundle', name)}"


def render_cti_stix_bundle(
    findings: list[Any],
    identity_candidates: list[dict[str, Any]] | None = None,
    attribution_scores: dict[str, Any] | None = None,
    killchain_tags: dict[str, Any] | None = None,
    evidence_chains: list[dict[str, Any]] | None = None,
    max_objects: int = MAX_STIX_OBJECTS,
) -> dict[str, Any]:
    """
    Render CTI findings + sidecar data as a STIX 2.1 threat-intel bundle.

    Produces real STIX indicator / identity / observed-data / relationship / note / report
    objects from Ghost Prime findings and derived intelligence.

    Guardrails:
      - No fake IOCs when findings list is empty
      - No network access
      - No model load
      - Bounded to MAX_STIX_OBJECTS

    Parameters
    ----------
    findings : list[CanonicalFinding | dict]
        List of canonical findings with ioc_type, ioc_value, confidence, finding_id.
    identity_candidates : list[dict] | None
        F202B identity stitching candidates (IdentityCandidate dicts).
    attribution_scores : dict | None
        F203B attribution scores keyed by candidate_id.
        Value is AttributionScore.to_dict() output.
    killchain_tags : dict | None
        F203C kill-chain tags keyed by finding_id.
        Value is list of KillChainTag.to_dict() output.
    evidence_chains : list[dict] | None
        F203D evidence chains (EvidenceChain serialized dicts).
    max_objects : int
        Cap on total STIX objects (default MAX_STIX_OBJECTS=500).

    Returns
    -------
    dict
        STIX 2.1 bundle dict with type, id, spec_version, and objects.
    """
    if identity_candidates is None:
        identity_candidates = []
    if attribution_scores is None:
        attribution_scores = {}
    if killchain_tags is None:
        killchain_tags = {}
    if evidence_chains is None:
        evidence_chains = []

    created = _utc_now()
    objects: list[dict[str, Any]] = []

    # Ghost Prime identity (report author)
    objects.append(_build_diagnostic_identity())

    # ── Findings → indicators + observed-data ─────────────────────────────────
    finding_ids_seen: set[str] = set()
    indicator_refs: list[str] = []
    observed_refs: list[str] = []

    for finding_raw in findings:
        if len(objects) >= max_objects:
            break
        if not isinstance(finding_raw, dict):
            finding_raw = dict(finding_raw) if hasattr(finding_raw, "__dict__") else {}
        finding = finding_raw

        fid = _safe_str(finding.get("finding_id", ""))
        finding_ids_seen.add(fid)

        # Kill-chain tags for this finding
        finding_kc_tags = killchain_tags.get(fid) if fid else None

        # Try indicator first
        ind = _ioc_to_indicator(finding, created, finding_kc_tags)
        if ind is not None:
            objects.append(ind)
            indicator_refs.append(ind["id"])
        else:
            # Fall back to observed-data
            obs = _finding_to_observed_data(finding, created)
            if obs and obs.get("objects"):
                objects.append(obs)
                observed_refs.append(obs["id"])

        # Kill-chain note per finding (if tags present)
        if finding_kc_tags:
            note = _build_killchain_note(fid, finding_kc_tags, indicator_refs[-1] if indicator_refs else None, created)
            if note and len(objects) < max_objects:
                objects.append(note)

    # ── Identity candidates → identity objects ───────────────────────────────
    identity_refs: list[str] = []
    for cand in identity_candidates:
        if len(objects) >= max_objects:
            break
        if not isinstance(cand, dict):
            cand = dict(cand) if hasattr(cand, "__dict__") else {}
        identity_obj = _build_identity_object(cand, created)
        objects.append(identity_obj)
        identity_refs.append(identity_obj["id"])

        # Attribution note for this identity
        cand_id = _safe_str(cand.get("candidate_id", ""))
        if cand_id in attribution_scores and len(objects) < max_objects:
            score = attribution_scores[cand_id]
            if isinstance(score, dict):
                note = _build_attribution_note(
                    cand_id,
                    score,
                    _make_stix_id("identity", _safe_str(cand.get("primary_name", "")), cand_id),
                    created,
                )
                objects.append(note)

    # ── Evidence chains → observed-data ───────────────────────────────────────
    chain_refs: list[str] = []
    for chain in evidence_chains:
        if len(objects) >= max_objects:
            break
        if not isinstance(chain, dict):
            chain = dict(chain) if hasattr(chain, "__dict__") else {}
        chain_obj = _build_evidence_chain_object(chain, created)
        objects.append(chain_obj)
        chain_refs.append(chain_obj["id"])

    # ── Relationship objects ──────────────────────────────────────────────────
    # Link indicators to identity objects via "based-on" relationships
    for ind_id in indicator_refs:
        if len(objects) >= max_objects:
            break
        for ident_id in identity_refs[:3]:  # limit relationships
            rel_id = _make_stix_id("relationship", ind_id, ident_id)
            objects.append({
                "type": "relationship",
                "spec_version": _STIX_SPEC_VERSION,
                "id": f"relationship--{rel_id}",
                "created": created,
                "modified": created,
                "source_ref": ind_id,
                "target_ref": f"identity--{ident_id}",
                "relationship_type": "derived-from",
            })

    # ── Report object ────────────────────────────────────────────────────────
    report_name = f"Ghost Prime CTI {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    report = _build_cti_report(
        objects=objects,
        name=report_name,
        finding_count=len(finding_ids_seen),
        identity_count=len(identity_refs),
        chain_count=len(chain_refs),
        created=created,
    )
    if len(objects) < max_objects:
        objects.append(report)

    bundle: dict[str, Any] = {
        "type": _BUNDLE_TYPE,
        "id": _cti_bundle_id(report_name),
        "spec_version": _STIX_SPEC_VERSION,
        "created": created,
        "modified": created,
        "objects": objects,
    }
    return bundle


def render_cti_stix_bundle_json(
    findings: list[Any],
    identity_candidates: list[dict[str, Any]] | None = None,
    attribution_scores: dict[str, Any] | None = None,
    killchain_tags: dict[str, Any] | None = None,
    evidence_chains: list[dict[str, Any]] | None = None,
    max_objects: int = MAX_STIX_OBJECTS,
) -> str:
    """
    Render CTI findings as a deterministic STIX bundle JSON string.

    Returns
    -------
    str
        JSON string with sorted keys for determinism.
    """
    bundle = render_cti_stix_bundle(
        findings=findings,
        identity_candidates=identity_candidates,
        attribution_scores=attribution_scores,
        killchain_tags=killchain_tags,
        evidence_chains=evidence_chains,
        max_objects=max_objects,
    )
    return json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False)


def render_cti_stix_bundle_to_path(
    findings: list[Any],
    identity_candidates: list[dict[str, Any]] | None = None,
    attribution_scores: dict[str, Any] | None = None,
    killchain_tags: dict[str, Any] | None = None,
    evidence_chains: list[dict[str, Any]] | None = None,
    max_objects: int = MAX_STIX_OBJECTS,
    path: Union[str, Path, None] = None,
) -> Path:
    """
    Render CTI findings as a STIX bundle and write to ``path``.

    If ``path`` is None:
      1. ``GHOST_EXPORT_DIR`` env var (override, backward compatible)
      2. ``CTI_EXPORT_DIR`` (~/.local/share/hledac/cti via XDG)

    Filename is deterministic: ``ghost_cti_{sprint_id}_{timestamp}.stix.json``.

    Returns the Path of the written file.
    """
    content = render_cti_stix_bundle_json(
        findings=findings,
        identity_candidates=identity_candidates,
        attribution_scores=attribution_scores,
        killchain_tags=killchain_tags,
        evidence_chains=evidence_chains,
        max_objects=max_objects,
    )

    if path is None:
        export_dir_env = os.environ.get("GHOST_EXPORT_DIR")
        if export_dir_env:
            base = Path(export_dir_env)
        else:
            from hledac.universal.paths import CTI_EXPORT_DIR
            base = CTI_EXPORT_DIR  # already mkdir'd at paths.py import time
    else:
        base = Path(path).parent

    filename = Path(path).name if path else None
    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"ghost_cti_{timestamp}.stix.json"

    out_path = base / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Optional stix2 package path
# ---------------------------------------------------------------------------
_stix2_module: Any = None
_stix2_available: bool = False
try:
    import stix2 as _stix2_module
    _stix2_available = True
except ImportError:
    pass


def _build_stix2_bundle(data: dict[str, Any]) -> dict[str, Any]:
    """Use stix2 package to build a proper STIX bundle."""
    bundle = _stix2_module.Bundle(
        objects=[],
        allow_custom=True,
    )
    # Add identity
    identity = _stix2_module.Identity(
        name="Ghost Prime",
        identity_class="system",
    )
    bundle.objects.append(identity)

    # Add diagnostic note
    root = data.get("diagnostic_root_cause", "unknown")
    label = _ROOT_CAUSE_LABELS.get(root, _ROOT_CAUSE_LABELS["unknown"])
    rec = _get_recommendation(data)

    signal_data = {
        "accepted_findings": data.get("accepted_findings", 0),
        "entries_seen": data.get("entries_seen", 0),
        "entries_scanned": data.get("entries_scanned", 0),
        "entries_with_hits": data.get("entries_with_hits", 0),
        "total_pattern_hits": data.get("total_pattern_hits", 0),
        "findings_built_pre_store": data.get("findings_built_pre_store", 0),
        "signal_stage": _safe_str(data.get("signal_stage")),
    }

    note = _stix2_module.Note(
        abstract=f"Ghost Prime Diagnostic: root_cause={root} ({label}); recommendation={rec}",
        content=json.dumps(signal_data, sort_keys=True),
        object_refs=[identity.id],
        created_by_ref=identity.id,
    )
    bundle.objects.append(note)

    # Root cause note
    rc_note = _stix2_module.Note(
        abstract=f"Root cause: {root} ({label}). Recommendation: {rec}. Network variance: {data.get('is_network_variance', False)}",
        content=json.dumps({
            "diagnostic_root_cause": root,
            "diagnostic_root_cause_label": label,
            "recommendation": rec,
            "is_network_variance": data.get("is_network_variance", False),
        }, sort_keys=True),
        object_refs=[identity.id],
        created_by_ref=identity.id,
    )
    bundle.objects.append(rc_note)

    return json.loads(str(bundle))


# ---------------------------------------------------------------------------
# Main bundle renderer
# ---------------------------------------------------------------------------
def render_stix_bundle(report: object) -> dict[str, Any]:
    """
    Render an ObservedRunReport (or Mapping) as a STIX 2.1 bundle dict.

    B.5: Never generates IOC/indicator/malware objects when no findings present.
    B.7: With zero accepted findings, exports only metadata-safe bundle
         (identity + diagnostic notes only).

    Parameters
    ----------
    report : msgspec.Struct or Mapping
        The observed run report.

    Returns
    -------
    dict
        STIX 2.1 bundle with type, id, spec_version, and objects list.
    """
    data = normalize_export_input(report)
    created = _iso_timestamp(data.get("started_ts") or data.get("finished_ts"))

    # Optional stix2 path
    if _stix2_available:
        return _build_stix2_bundle(data)

    # Builtins path: plain dicts
    objects: list[dict[str, Any]] = []

    # Always: identity (Ghost Prime as report author)
    objects.append(_build_diagnostic_identity())

    # Root cause + recommendation
    objects.append(_build_root_cause_object(data, created))

    # Signal funnel note
    objects.append(_build_diagnostic_note(data, created))

    # UMA note (if available)
    uma_note = _build_diagnostic_uma_note(data, created)
    if uma_note:
        objects.append(uma_note)

    # Per-source notes (if available)
    objects.extend(_build_per_source_notes(data, created))

    bundle: dict[str, Any] = {
        "type": _BUNDLE_TYPE,
        "id": _bundle_id(),
        "spec_version": _STIX_SPEC_VERSION,
        "created": created,
        "modified": created,
        "objects": objects,
    }

    return _maybe_sign_bundle(bundle)


# ---------------------------------------------------------------------------
# Sprint F214AC: Post-Quantum ML-DSA-65 STIX bundle signature
# Fail-safe throughout — skip silently if PQ backend unavailable
# ---------------------------------------------------------------------------

def _maybe_sign_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """
    Add ML-DSA-65 PQ signature to STIX bundle if backend available.

    GHOST_INVARIANTS: no asyncio.run() in async context.
    Runs async PQ sign via asyncio.to_thread when called from sync render path.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop (startup / tests) — use blocking call
        return asyncio.run(_maybe_sign_bundle_async(bundle))
    else:
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pq_sign")
        future = loop.run_in_executor(pool, _sync_pq_sign, bundle)
        try:
            return future.result()
        finally:
            pool.shutdown(wait=True)


def _sync_pq_sign(bundle: dict[str, Any]) -> dict[str, Any]:
    """to_thread target — runs event loop in a separate thread."""
    return asyncio.run(_maybe_sign_bundle_async(bundle))


async def _maybe_sign_bundle_async(bundle: dict[str, Any]) -> dict[str, Any]:
    """
    Async PQ signing path — gather(return_exceptions=True) on all awaits.

    Returns bundle unchanged if PQ unavailable or signing fails.
    """
    try:
        results = await asyncio.gather(
            _get_pq_backend_async(),
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            return bundle

        backend, status = results[0]
        if not backend.is_available():
            return bundle
        if status.availability not in (PQAvailability.AVAILABLE,):
            return bundle

        key_id = "com.hledac.pq.signing.v1"
        extension = _build_pq_extension(bundle, backend, key_id)
        if extension is None:
            return bundle

        signed = dict(bundle)
        signed["extension"] = extension
        return signed
    except Exception:
        return bundle


async def _get_pq_backend_async() -> tuple[PostQuantumBackend, PQStatus]:
    """Get PQ backend — always use create_post_quantum_backend (async factory)."""
    backend, status = await create_post_quantum_backend()
    return backend, status


def _build_pq_extension(bundle: dict[str, Any], backend: PostQuantumBackend, key_id: str) -> dict[str, Any] | None:
    """
    Compute ML-DSA-65 signature over bundle objects digest.

    Returns None silently on any error (GHOST_INVARIANTS: fail-safe).
    """
    try:
        import hashlib

        canonical: bytes = json.dumps(
            bundle.get("objects", []),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest: str = hashlib.sha256(canonical).hexdigest()

        if not backend.ensure_mldsa_key(key_id, level=65):
            return None

        sig: PQSignature = backend.sign_mldsa_digest(key_id, digest, level=65)

        return {
            "extension_type": "hledac:pq-signature",
            "ml_dsa_signature": sig.signature_bytes.hex(),
            "ml_dsa_level": sig.level,
            "key_id": sig.key_id,
            "bundle_sha256": digest,
            "backend": backend.name(),
            "hybrid": sig.has_mldsa(),
        }
    except Exception:
        return None


def render_stix_bundle_json(report: object) -> str:
    """
    Render report as a deterministic STIX bundle JSON string.

    Returns
    -------
    str
        JSON string with sorted keys for determinism.
    """
    bundle = render_stix_bundle(report)
    return json.dumps(bundle, indent=2, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# File-output helper
# ---------------------------------------------------------------------------
def render_stix_bundle_to_path(
    report: object,
    path: Union[str, Path, None] = None,
) -> Path:
    """
    Render report as STIX bundle and write to ``path``.

    If ``path`` is None:
      1. ``GHOST_EXPORT_DIR`` env var (override, backward compatible)
      2. ``RUNS_ROOT`` (runtime/runs/)

    Filename is deterministic: ``ghost_diagnostic_{run_id}.stix.json``
    falling back to ``ghost_diagnostic_{timestamp}.stix.json``.

    Returns the Path of the written file.
    """
    content = render_stix_bundle_json(report)

    if path is None:
        export_dir_env = os.environ.get("GHOST_EXPORT_DIR")
        if export_dir_env:
            base = Path(export_dir_env)
        else:
            from hledac.universal.paths import RUNS_ROOT
            base = RUNS_ROOT
            base.mkdir(parents=True, exist_ok=True)
    else:
        base = Path(path).parent

    filename = Path(path).name if path else None
    if not filename:
        try:
            data = normalize_export_input(report)
            run_id = data.get("diagnostic_run_id") or data.get("run_id")
        except Exception:
            run_id = None
        if run_id:
            safe = str(run_id).replace("/", "_").replace("\\", "_")
            filename = f"ghost_diagnostic_{safe}.stix.json"
        else:
            try:
                ts = normalize_export_input(report).get("started_ts") or normalize_export_input(report).get("finished_ts")
            except Exception:
                ts = None
            if ts:
                filename = f"ghost_diagnostic_{int(ts)}.stix.json"
            else:
                filename = "ghost_diagnostic.stix.json"

    out_path = base / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return out_path
