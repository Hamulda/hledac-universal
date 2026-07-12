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
import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import msgspec
from datetime import datetime
from typing import Any
from hledac.universal.utils.async_helpers import safe_gather_shielded
logger = logging.getLogger(__name__)
_NUM_EXTRACTION_WORKERS: int = 2
_CHUNK_SIZE: int = 32
_executor: ThreadPoolExecutor | None = None

def _get_executor() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=_NUM_EXTRACTION_WORKERS, thread_name_prefix='entity_extract')
    return _executor
MAX_PROFILES: int = 500
MAX_COMPARISONS: int = 2000
_EMAIL_RE = re.compile('[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}')
_USERNAME_RE = re.compile('(?:^|[@\\s])([a-zA-Z0-9][a-zA-Z0-9_.-]{1,30})(?:@([a-zA-Z0-9][a-zA-Z0-9.-]*\\.[a-zA-Z]{2,})|$)', re.MULTILINE)
_DOMAIN_HANDLE_RE = re.compile('\\b([a-zA-Z0-9][a-zA-Z0-9_.-]{2,20})@([a-zA-Z0-9][a-zA-Z0-9.-]+\\.[a-zA-Z]{2,})\\b')
_HANDLE_RE = re.compile('@([a-zA-Z0-9][a-zA-Z0-9_.-]{1,30})')
_URL_HOST_RE = re.compile('https?://([a-zA-Z0-9][a-zA-Z0-9-]*\\.[a-zA-Z]{2,})')

@dataclass(True)
class ExtractedEntity:
    """A single extracted entity from a finding."""
    entity_type: str
    value: str
    raw_value: str
    platform: str
    finding_id: str
    confidence: float

@dataclass(frozen=True, slots=True)
class EntitySignalProfile:
    """
    Simplified identity profile for entity signal extraction.

    Unlike IdentityProfile in identity_stitching.py, this is a lightweight
    extraction-only profile used to pass entity signals to the stitching adapter.
    """
    id: str
    primary_name: str
    emails: list[str] = field(default_factory=list)
    usernames: list[str] = field(default_factory=list)
    domain_handles: list[str] = field(default_factory=list)
    platforms: set[str] = field(default_factory=set)
    finding_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {'id': self.id, 'primary_name': self.primary_name, 'emails': self.emails, 'usernames': self.usernames, 'domain_handles': self.domain_handles, 'platforms': list(self.platforms), 'finding_ids': self.finding_ids, 'confidence': self.confidence, 'created_at': self.created_at.isoformat() if self.created_at else None}

def _normalize_email(email: str) -> str:
    return email.lower().strip()

def _normalize_username(username: str) -> str:
    normalized = username.lower().strip().lstrip('@')
    normalized = re.sub('[._-]', '', normalized)
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
    for match in _EMAIL_RE.finditer(payload):
        raw = match.group(0)
        entities.append(ExtractedEntity(entity_type='email', value=_normalize_email(raw), raw_value=raw, platform=platform, finding_id=fid, confidence=min(confidence + 0.1, 1.0)))
    for match in _DOMAIN_HANDLE_RE.finditer(payload):
        handle = match.group(1).lower()
        domain = match.group(2).lower()
        if len(handle) >= 2 and len(domain) >= 3:
            entities.append(ExtractedEntity(entity_type='domain_handle', value=f'{handle}@{domain}', raw_value=f'{handle}@{domain}', platform=platform, finding_id=fid, confidence=min(confidence + 0.05, 1.0)))
    seen_usernames: set[str] = set()
    for match in _HANDLE_RE.finditer(payload):
        raw = match.group(1)
        if len(raw) >= 2 and raw.lower() not in seen_usernames:
            seen_usernames.add(raw.lower())
            entities.append(ExtractedEntity(entity_type='username', value=_normalize_username(raw), raw_value=raw, platform=platform, finding_id=fid, confidence=min(confidence + 0.05, 1.0)))
    domain = _extract_domain_from_payload(payload)
    if domain:
        for match in _USERNAME_RE.finditer(payload):
            raw = match.group(1)
            if raw and len(raw) >= 2:
                full_handle = f'{raw}@{domain}'
                if full_handle.lower() not in seen_usernames:
                    seen_usernames.add(full_handle.lower())
                    entities.append(ExtractedEntity(entity_type='domain_handle', value=_normalize_username(raw), raw_value=full_handle, platform=platform, finding_id=fid, confidence=min(confidence, 1.0)))
    return entities

def _extract_chunk(findings_chunk: list[Any]) -> list[tuple[str, Any, ExtractedEntity]]:
    """
    Extract entities from a chunk of findings.
    Returns list of (finding_id, finding, entity) tuples for profile building.
    """
    results: list[tuple[str, Any, ExtractedEntity]] = []
    for finding in findings_chunk:
        fid = getattr(finding, 'finding_id', None)
        if not fid:
            continue
        entities = extract_entities_from_finding(finding)
        for ent in entities:
            results.append((fid, finding, ent))
    return results

def extract_entities_from_findings(findings: list[Any], max_profiles: int=MAX_PROFILES) -> list[EntitySignalProfile]:
    """
    Extract entity signals from a batch of CanonicalFinding objects (sync version).

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
    if not findings:
        return []
    chunks: list[list[Any]] = [findings[i:i + _CHUNK_SIZE] for i in range(0, len(findings), _CHUNK_SIZE)]
    executor = _get_executor()
    futures = [executor.submit(_extract_chunk, chunk) for chunk in chunks]
    all_results: list[tuple[str, Any, ExtractedEntity]] = []
    for f in futures:
        try:
            all_results.extend(f.result())
        except Exception as exc:
            logger.warning(f'Entity extraction chunk failed: {exc}')
    profile_map: dict[str, EntitySignalProfile] = {}
    for fid, _finding, ent in all_results:
        if len(profile_map) >= max_profiles:
            break
        if ent.entity_type == 'email':
            key = f'email:{ent.value}'
            if key not in profile_map:
                profile_map[key] = EntitySignalProfile(id=key, primary_name=ent.value.split('@')[0], emails=[ent.raw_value], finding_ids=[fid], confidence=ent.confidence)
            else:
                prof = profile_map[key]
                if ent.raw_value not in prof.emails:
                    prof.emails.append(ent.raw_value)
                if fid not in prof.finding_ids:
                    prof.finding_ids.append(fid)
                prof.platforms.add(ent.platform)
        elif ent.entity_type in ('username', 'domain_handle'):
            key = f'handle:{ent.value}'
            if key not in profile_map:
                profile_map[key] = EntitySignalProfile(id=key, primary_name=ent.raw_value, usernames=[ent.raw_value], domain_handles=[ent.raw_value] if ent.entity_type == 'domain_handle' else [], finding_ids=[fid], confidence=ent.confidence)
            else:
                prof = profile_map[key]
                if ent.raw_value not in prof.usernames:
                    prof.usernames.append(ent.raw_value)
                if ent.entity_type == 'domain_handle' and ent.raw_value not in prof.domain_handles:
                    prof.domain_handles.append(ent.raw_value)
                if fid not in prof.finding_ids:
                    prof.finding_ids.append(fid)
                prof.platforms.add(ent.platform)
                prof.confidence = max(prof.confidence, ent.confidence)
    logger.debug(f'EntitySignalExtractor: {len(profile_map)} profiles from {len(findings)} findings')
    return list(profile_map.values())

async def extract_entities_from_findings_async(findings: list[Any], max_profiles: int=MAX_PROFILES, max_concurrency: int=4) -> list[EntitySignalProfile]:
    """
    P1-2: Async batch entity signal extraction via asyncio.gather.

    Extract entity signals from a batch of CanonicalFinding objects using
    asyncio.to_thread for concurrent regex extraction without blocking the event loop.

    Groups entities by normalized value to build lightweight EntitySignalProfile
    objects. Each profile is keyed by normalized email or primary identifier.

    Bounded: max_profiles caps the number of profiles returned.
    Comparisons are capped at MAX_COMPARISONS in the stitching adapter.

    Architecture:
      findings → chunks (size=_CHUNK_SIZE)
               → asyncio.gather with asyncio.to_thread per chunk
               → results merged and grouped into profiles

    M1 8GB: max_concurrency=4 keeps thread pool bounded.

    Args:
        findings: List of CanonicalFinding objects
        max_profiles: Maximum number of profiles to return (default MAX_PROFILES)
        max_concurrency: Max concurrent chunk extractions (default 4, M1 8GB safe)

    Returns:
        List of EntitySignalProfile objects, bounded to max_profiles
    """
    if not findings:
        return []
    chunks: list[list[Any]] = [findings[i:i + _CHUNK_SIZE] for i in range(0, len(findings), _CHUNK_SIZE)]
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _run_chunk_with_sem(chunk: list[Any]) -> list[tuple[str, Any, ExtractedEntity]]:
        async with semaphore:
            return await asyncio.to_thread(_extract_chunk, chunk)
    tasks = [_run_chunk_with_sem(chunk) for chunk in chunks]
    gathered = await safe_gather_shielded(*tasks, label='entity_extraction', logger_instance=logger)
    all_results: list[tuple[str, Any, ExtractedEntity]] = []
    for chunk_results in gathered.ok:
        all_results.extend(chunk_results)
    for exc in gathered.errors:
        logger.warning(f'Entity extraction chunk failed: {exc}')
    if gathered.re_raised is not None:
        raise gathered.re_raised
    profile_map: dict[str, EntitySignalProfile] = {}
    for fid, _finding, ent in all_results:
        if len(profile_map) >= max_profiles:
            break
        if ent.entity_type == 'email':
            key = f'email:{ent.value}'
            if key not in profile_map:
                profile_map[key] = EntitySignalProfile(id=key, primary_name=ent.value.split('@')[0], emails=[ent.raw_value], finding_ids=[fid], confidence=ent.confidence)
            else:
                prof = profile_map[key]
                if ent.raw_value not in prof.emails:
                    prof.emails.append(ent.raw_value)
                if fid not in prof.finding_ids:
                    prof.finding_ids.append(fid)
                prof.platforms.add(ent.platform)
        elif ent.entity_type in ('username', 'domain_handle'):
            key = f'handle:{ent.value}'
            if key not in profile_map:
                profile_map[key] = EntitySignalProfile(id=key, primary_name=ent.raw_value, usernames=[ent.raw_value], domain_handles=[ent.raw_value] if ent.entity_type == 'domain_handle' else [], finding_ids=[fid], confidence=ent.confidence)
            else:
                prof = profile_map[key]
                if ent.raw_value not in prof.usernames:
                    prof.usernames.append(ent.raw_value)
                if ent.entity_type == 'domain_handle' and ent.raw_value not in prof.domain_handles:
                    prof.domain_handles.append(ent.raw_value)
                if fid not in prof.finding_ids:
                    prof.finding_ids.append(fid)
                prof.platforms.add(ent.platform)
                prof.confidence = max(prof.confidence, ent.confidence)
    logger.debug(f'EntitySignalExtractor: {len(profile_map)} profiles from {len(findings)} findings')
    return list(profile_map.values())
_extracted_profiles_total: int = 0
_extracted_entities_total: int = 0

def reset_extractor_stats() -> None:
    """Reset module-level statistics. Call at sprint teardown."""
    global _extracted_profiles_total, _extracted_entities_total
    _extracted_profiles_total = 0
    _extracted_entities_total = 0

def get_extractor_stats() -> dict[str, int]:
    """Return extractor statistics."""
    return {'profiles_extracted': _extracted_profiles_total, 'entities_extracted': _extracted_entities_total}

def shutdown_executor() -> None:
    """Shutdown thread pool executor. Call at sprint teardown."""
    global _executor
    if _executor is not None:
        _executor.shutdown(wait=False)
        _executor = None
__all__ = ['ExtractedEntity', 'EntitySignalProfile', 'extract_entities_from_finding', 'extract_entities_from_findings', 'extract_entities_from_findings_async', 'reset_extractor_stats', 'get_extractor_stats', 'shutdown_executor', 'MAX_PROFILES', 'MAX_COMPARISONS']