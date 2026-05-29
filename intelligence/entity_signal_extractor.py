"""
Entity Signal Extractor — Sprint F202B
======================================

Deterministic entity extraction from accepted CanonicalFinding objects.
No ML models — pure regex/string heuristics.

Extracts:
  - Username patterns (platform handles)
  - Email addresses
  - Domain handles (domain@ handle format)
  - Platform signals

Bounded for M1 8GB:
  - MAX_PROFILES=500 per sprint
  - MAX_COMPARISONS=2000 per sprint

Role: feeds identity_stitching_canonical.py adapter which produces
derived identity findings for async_ingest_findings_batch().
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_PROFILES: int = 500
MAX_COMPARISONS: int = 2000

# ── Patterns ──────────────────────────────────────────────────────────────────

# Email: standard email pattern
_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

# Platform handle patterns: @username or username@platform.domain
_USERNAME_RE = re.compile(
    r'(?:^|[@\s])([a-zA-Z0-9][a-zA-Z0-9_.-]{1,30})'
    r'(?:@([a-zA-Z0-9][a-zA-Z0-9.-]*\.[a-zA-Z]{2,})|$)',
    re.MULTILINE,
)

# Domain handle: user@domain (extrapolated handles from domains)
_DOMAIN_HANDLE_RE = re.compile(
    r'\b([a-zA-Z0-9][a-zA-Z0-9_.-]{2,20})@([a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b',
)

# Generic handle: starts with @ followed by 2+ chars
_HANDLE_RE = re.compile(r'@([a-zA-Z0-9][a-zA-Z0-9_.-]{1,30})')

# URL host extractor for domain-based handles
_URL_HOST_RE = re.compile(
    r'https?://([a-zA-Z0-9][a-zA-Z0-9-]*\.[a-zA-Z]{2,})',
)


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class ExtractedEntity:
    """A single extracted entity from a finding."""
    entity_type: str          # "email" | "username" | "domain_handle"
    value: str                # normalized value
    raw_value: str            # original raw value (for display)
    platform: str             # platform context if known
    finding_id: str           # source finding
    confidence: float          # extraction confidence [0-1]


@dataclass
class EntitySignalProfile:
    """
    Simplified identity profile for entity signal extraction.

    Unlike IdentityProfile in identity_stitching.py, this is a lightweight
    extraction-only profile used to pass entity signals to the stitching adapter.
    """
    id: str
    primary_name: str          # extracted from payload or finding_id
    emails: list[str] = field(default_factory=list)
    usernames: list[str] = field(default_factory=list)
    domain_handles: list[str] = field(default_factory=list)
    platforms: set[str] = field(default_factory=set)
    finding_ids: list[str] = field(default_factory=list)   # source findings
    confidence: float = 0.5
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "primary_name": self.primary_name,
            "emails": self.emails,
            "usernames": self.usernames,
            "domain_handles": self.domain_handles,
            "platforms": list(self.platforms),
            "finding_ids": self.finding_ids,
            "confidence": self.confidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ── Normalization ─────────────────────────────────────────────────────────────

def _normalize_email(email: str) -> str:
    return email.lower().strip()


def _normalize_username(username: str) -> str:
    normalized = username.lower().strip().lstrip('@')
    normalized = re.sub(r'[._-]', '', normalized)
    return normalized


def _extract_platform_from_finding(finding: Any) -> str:
    """Extract platform/source context from finding."""
    src = getattr(finding, 'source_type', 'unknown') or 'unknown'
    prov = getattr(finding, 'provenance', ()) or ()
    if prov:
        first_prov = prov[0] if prov else ''
        if isinstance(first_prov, str) and first_prov:
            return first_prov
    return src


def _extract_domain_from_payload(payload_text: str | None) -> str | None:
    """Extract a domain from payload text (URL host)."""
    if not payload_text:
        return None
    m = _URL_HOST_RE.search(payload_text)
    return m.group(1) if m else None


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_entities_from_finding(finding: Any) -> list[ExtractedEntity]:
    """
    Extract all entity signals from a single CanonicalFinding.

    Args:
        finding: CanonicalFinding (or duckdb_store.CanonicalFinding)

    Returns:
        List of ExtractedEntity objects
    """
    entities: list[ExtractedEntity] = []
    fid = getattr(finding, 'finding_id', None)
    if not fid:
        return entities

    payload = getattr(finding, 'payload_text', None) or ''
    platform = _extract_platform_from_finding(finding)
    confidence = getattr(finding, 'confidence', 0.5) or 0.5

    # 1. Emails
    for match in _EMAIL_RE.finditer(payload):
        raw = match.group(0)
        entities.append(ExtractedEntity(
            entity_type="email",
            value=_normalize_email(raw),
            raw_value=raw,
            platform=platform,
            finding_id=fid,
            confidence=min(confidence + 0.1, 1.0),
        ))

    # 2. Domain handles (user@domain patterns)
    for match in _DOMAIN_HANDLE_RE.finditer(payload):
        handle = match.group(1).lower()
        domain = match.group(2).lower()
        if len(handle) >= 2 and len(domain) >= 3:
            entities.append(ExtractedEntity(
                entity_type="domain_handle",
                value=f"{handle}@{domain}",
                raw_value=f"{handle}@{domain}",
                platform=platform,
                finding_id=fid,
                confidence=min(confidence + 0.05, 1.0),
            ))

    # 3. Username handles (bare @username)
    seen_usernames: set[str] = set()
    for match in _HANDLE_RE.finditer(payload):
        raw = match.group(1)
        if len(raw) >= 2 and raw.lower() not in seen_usernames:
            seen_usernames.add(raw.lower())
            entities.append(ExtractedEntity(
                entity_type="username",
                value=_normalize_username(raw),
                raw_value=raw,
                platform=platform,
                finding_id=fid,
                confidence=min(confidence + 0.05, 1.0),
            ))

    # 4. Domain-extracted handles (username@subdomain.domain)
    domain = _extract_domain_from_payload(payload)
    if domain:
        for match in _USERNAME_RE.finditer(payload):
            raw = match.group(1)
            if raw and len(raw) >= 2:
                full_handle = f"{raw}@{domain}"
                if full_handle.lower() not in seen_usernames:
                    seen_usernames.add(full_handle.lower())
                    entities.append(ExtractedEntity(
                        entity_type="domain_handle",
                        value=_normalize_username(raw),
                        raw_value=full_handle,
                        platform=platform,
                        finding_id=fid,
                        confidence=min(confidence, 1.0),
                    ))

    return entities


def extract_entities_from_findings(
    findings: list[Any],
    max_profiles: int = MAX_PROFILES,
) -> list[EntitySignalProfile]:
    """
    Extract entity signals from a batch of CanonicalFinding objects.

    Groups entities by normalized value to build lightweight EntitySignalProfile
    objects. Each profile is keyed by normalized email or primary identifier.

    Bounded: max_profiles caps the number of profiles returned.
    Comparisons are capped at MAX_COMPARISONS in the stitching adapter.

    Args:
        findings: List of CanonicalFinding objects
        max_profiles: Maximum number of profiles to return (default MAX_PROFILES)

    Returns:
        List of EntitySignalProfile objects, bounded to max_profiles
    """
    # Group entities by normalized email or value
    profile_map: dict[str, EntitySignalProfile] = {}

    for finding in findings:
        entities = extract_entities_from_finding(finding)
        fid = getattr(finding, 'finding_id', f"fid_{len(profile_map)}") or f"fid_{len(profile_map)}"

        for ent in entities:
            if len(profile_map) >= max_profiles:
                break

            if ent.entity_type == "email":
                key = f"email:{ent.value}"
                if key not in profile_map:
                    profile_map[key] = EntitySignalProfile(
                        id=key,
                        primary_name=ent.value.split('@')[0],
                        emails=[ent.raw_value],
                        finding_ids=[fid],
                        confidence=ent.confidence,
                    )
                else:
                    prof = profile_map[key]
                    if ent.raw_value not in prof.emails:
                        prof.emails.append(ent.raw_value)
                    if fid not in prof.finding_ids:
                        prof.finding_ids.append(fid)
                    prof.platforms.add(ent.platform)

            elif ent.entity_type in ("username", "domain_handle"):
                key = f"handle:{ent.value}"
                if key not in profile_map:
                    profile_map[key] = EntitySignalProfile(
                        id=key,
                        primary_name=ent.raw_value,
                        usernames=[ent.raw_value],
                        domain_handles=[ent.raw_value] if ent.entity_type == "domain_handle" else [],
                        finding_ids=[fid],
                        confidence=ent.confidence,
                    )
                else:
                    prof = profile_map[key]
                    if ent.raw_value not in prof.usernames:
                        prof.usernames.append(ent.raw_value)
                    if ent.entity_type == "domain_handle" and ent.raw_value not in prof.domain_handles:
                        prof.domain_handles.append(ent.raw_value)
                    if fid not in prof.finding_ids:
                        prof.finding_ids.append(fid)
                    prof.platforms.add(ent.platform)
                    prof.confidence = max(prof.confidence, ent.confidence)

        if len(profile_map) >= max_profiles:
            break

    logger.debug(f"EntitySignalExtractor: {len(profile_map)} profiles from {len(findings)} findings")
    return list(profile_map.values())


# ── Module-level counter for probe tests ──────────────────────────────────────

_extracted_profiles_total: int = 0
_extracted_entities_total: int = 0


def reset_extractor_stats() -> None:
    """Reset module-level statistics. Call at sprint teardown."""
    global _extracted_profiles_total, _extracted_entities_total
    _extracted_profiles_total = 0
    _extracted_entities_total = 0


def get_extractor_stats() -> dict[str, int]:
    """Return extractor statistics."""
    return {
        "profiles_extracted": _extracted_profiles_total,
        "entities_extracted": _extracted_entities_total,
    }


__all__ = [
    "ExtractedEntity",
    "EntitySignalProfile",
    "extract_entities_from_finding",
    "extract_entities_from_findings",
    "reset_extractor_stats",
    "get_extractor_stats",
    "MAX_PROFILES",
    "MAX_COMPARISONS",
]
