"""
Sprint F206BG — Canonical Acquisition Strategy Layer.

ROLE: Model-free planner/admission layer that decides which acquisition lanes
are allowed per sprint/cycle under M1 constraints.

ARCHITECTURAL RULE:
  AcquisitionStrategy does NOT fetch network.
  It only emits a bounded plan dict per lane.

Lane plan fields:
  lane, enabled, reason, max_items, timeout_s, concurrency, risk_level

LANES:
  FEED         — structured TI feeds (always allowed unless hardware critical)
  PUBLIC       — public discovery pipeline
  CT           — certificate transparency log discovery
  WAYBACK      — Wayback Machine archive enumeration
  PASSIVE_DNS  — passive DNS lookup
  BLOCKCHAIN   — blockchain analyzer (wallet/hash/crypto indicators)
  STEALTH      — stealth/dark web (disabled by default)
  PIVOT_EXECUTOR — pivot-driven domain/IP expansion

STRATEGY RULES:
  - FEED: always unless hardware critical
  - PUBLIC: unless transport degraded or hardware critical
  - CT: domain-like query OR aggressive mode
  - WAYBACK: query has URL/domain OR enough budget (duration >= 300s)
  - PASSIVE_DNS: query has domain/IP indicator
  - BLOCKCHAIN: query has wallet/hash/crypto indicator
  - STEALTH: disabled by default unless explicit flag AND transport phase >= breaker_seam
  - PIVOT_EXECUTOR: always allowed (lightweight, advisory)
  - Concurrency reduced under UMA warn/critical
  - Heavy optional lanes hard-disabled under swap/critical

INVARIANTS (GHOST_INVARIANTS):
  - No network I/O
  - No model/MLX load
  - No asyncio.run() / loop.run_until_complete()
  - Bounded: max 12 lanes in plan
  - Fail-soft: returns minimal plan on any error
  - Deterministic: same inputs always produce same plan
"""
from __future__ import annotations
import logging
import msgspec
import re
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any
from hledac.universal.network.session_runtime import async_get_httpx_session
import msgspec
logger = logging.getLogger(__name__)
try:
    from hledac.universal.utils.source_types import SourceType
except ImportError:
    SourceType: type | None = None
from hledac.universal.runtime.acquisition_telemetry_reconcile import complete_source_family_outcomes_from_lane_details, reconcile_lane_detail_fields
from hledac.universal.runtime.nonfeed_candidate_ledger import extract_domain_candidates_from_text
from hledac.universal.runtime.source_finding_bridge import MAX_SAMPLE_REJECTIONS, ct_results_to_findings, passive_dns_results_to_findings, wayback_results_to_findings
from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_ok
from hledac.universal.runtime.acquisition.profile import AcquisitionProfile, normalize_acquisition_profile, is_academic_profile, is_deep_osint_m1_profile
DOMAIN_EXPANSIONS: dict[str, tuple[str, ...]] = {'ransomware': ('ransomware_tracker.abuse.ch', 'malwarebytes.com/threat-center', 'bleepingcomputer.com'), 'botnet': ('abuse.ch', 'feodotracker.nl', 'urlhaus.abuse.ch'), 'leak': ('haveibeenpwned.com', 'breachlevelindex.com'), 'c2': ('malware-traffic-analysis.net', 'otx.alienvault.com'), 'lockbit': ('ransomware_tracker.abuse.ch', 'bleepingcomputer.com', 'malwarebytes.com'), 'conti': ('ransomware_tracker.abuse.ch', 'bleepingcomputer.com', 'mandiant.com'), 'apt29': ('otx.alienvault.com', 'threatconnect.com', 'mandiant.com'), 'apt41': ('otx.alienvault.com', 'threatconnect.com', ' Recorded Future'), 'fin7': ('mandiant.com', 'threatconnect.com', 'otx.alienvault.com'), 'alphv': ('ransomware_tracker.abuse.ch', 'bleepingcomputer.com'), 'blackcat': ('ransomware_tracker.abuse.ch', 'bleepingcomputer.com'), 'revil': ('ransomware_tracker.abuse.ch', 'bleepingcomputer.com'), 'clop': ('ransomware_tracker.abuse.ch', 'bleepingcomputer.com'), 'emotet': ('malwarebytes.com', 'bleepingcomputer.com', 'urlhaus.abuse.ch'), 'qakbot': ('malwarebytes.com', 'bleepingcomputer.com', 'urlhaus.abuse.ch'), 'icedid': ('malwarebytes.com', 'bleepingcomputer.com'), 'raccoon': ('ransomware_tracker.abuse.ch', 'bleepingcomputer.com'), 'dridex': ('abuse.ch', 'malwarebytes.com'), 'trickbot': ('malwarebytes.com', 'bleepingcomputer.com'), 'ryuk': ('ransomware_tracker.abuse.ch', 'bleepingcomputer.com')}
_THREAT_DICTIONARY: dict[str, tuple[str, list[str]]] = {'lockbit': ('malware_family', ['lockbit 2.0', 'lockbit3', 'ldx']), 'lockbit 2.0': ('malware_family', ['lockbit', 'lockbit3', 'ldx']), 'lockbit3': ('malware_family', ['lockbit', 'lockbit 2.0', 'ldx']), 'conti': ('malware_family', ['conti ransomware', 'wizard spider']), 'conti ransomware': ('malware_family', ['conti', 'wizard spider']), 'wizard spider': ('malware_family', ['conti']), 'revil': ('malware_family', ['revil ransomware', 'sodinokibi']), 'sodinokibi': ('malware_family', ['revil', 'revil ransomware']), 'revil ransomware': ('malware_family', ['revil', 'sodinokibi']), 'blackcat': ('malware_family', ['alphv', 'blackcat ransomware']), 'alphv': ('malware_family', ['blackcat', 'blackcat ransomware']), 'blackcat ransomware': ('malware_family', ['blackcat', 'alphv']), 'clop': ('malware_family', ['clop ransomware', 'clopv2']), 'clop ransomware': ('malware_family', ['clop']), 'hive': ('malware_family', ['hive ransomware']), 'hive ransomware': ('malware_family', ['hive']), 'ryuk': ('malware_family', ['ryuk ransomware']), 'ryuk ransomware': ('malware_family', ['ryuk']), 'ransomexx': ('malware_family', ['ransomexx', 'nexway']), 'nexway': ('malware_family', ['ransomexx']), 'malware_family': ('malware_family', ['malware family']), 'emotet': ('malware_family', ['emotet trojan', 'heodo']), 'emotet trojan': ('malware_family', ['emotet']), 'heodo': ('malware_family', ['emotet']), 'qakbot': ('malware_family', ['qakbot trojan', 'qbot']), 'qbot': ('malware_family', ['qakbot']), 'qakbot trojan': ('malware_family', ['qakbot']), 'icedid': ('malware_family', ['icedid trojan', 'bokbot']), 'bokbot': ('malware_family', ['icedid']), 'icedid trojan': ('malware_family', ['icedid']), 'dridex': ('malware_family', ['dridex trojan', 'bugat']), 'bugat': ('malware_family', ['dridex']), 'dridex trojan': ('malware_family', ['dridex']), 'trickbot': ('malware_family', ['trickbot trojan', 'trickster']), 'trickbot trojan': ('malware_family', ['trickbot']), 'trickster': ('malware_family', ['trickbot']), 'raccoon stealer': ('malware_family', ['raccoon', 'raccoon malware']), 'raccoon malware': ('malware_family', ['raccoon', 'raccoon stealer']), 'raccoon': ('malware_family', ['raccoon stealer']), 'stealer': ('malware_family', ['stealer malware', 'infostealer']), 'infostealer': ('malware_family', ['stealer', 'infostealer malware']), 'vidar': ('malware_family', ['vidar stealer']), 'vidar stealer': ('malware_family', ['vidar']), 'aurora': ('malware_family', ['aurora stealer']), 'aurora stealer': ('malware_family', ['aurora']), 'redline': ('malware_family', ['redline stealer']), 'redline stealer': ('malware_family', ['redline']), 'rat': ('malware_family', ['remote access trojan', 'rat malware']), 'remote access trojan': ('malware_family', ['rat']), 'rat malware': ('malware_family', ['rat']), 'cobalt strike': ('malware_family', ['cobaltstrike', 'cs']), 'cobaltstrike': ('malware_family', ['cobalt strike']), 'cs': ('malware_family', ['cobalt strike']), 'metasploit': ('malware_family', ['metasploit framework', 'msf']), 'metasploit framework': ('malware_family', ['metasploit']), 'msf': ('malware_family', ['metasploit']), 'apt29': ('threat_actor', ['cozy bear', 'the dukens', 'midnight blizzard']), 'cozy bear': ('threat_actor', ['apt29', 'cozyduke', 'midnight blizzard']), 'cozyduke': ('threat_actor', ['apt29', 'cozy bear']), 'the dukens': ('threat_actor', ['apt29']), 'midnight blizzard': ('threat_actor', ['apt29', 'cozy bear']), 'apt41': ('threat_actor', ['barium', 'wicked panda', 'zinc']), 'barium': ('threat_actor', ['apt41', 'wicked panda']), 'wicked panda': ('threat_actor', ['apt41', 'barium']), 'zinc': ('threat_actor', ['apt41', 'lazarus group']), 'apt28': ('threat_actor', ['fancy bear', 'sofacy', 'sandworm']), 'fancy bear': ('threat_actor', ['apt28', 'sofacy', 'pawn storm']), 'sofacy': ('threat_actor', ['apt28', 'fancy bear']), 'pawn storm': ('threat_actor', ['apt28', 'fancy bear']), 'sandworm': ('threat_actor', ['apt28', 'voodoo bear', 'electrum']), 'voodoo bear': ('threat_actor', ['sandworm']), 'electrum': ('threat_actor', ['sandworm']), 'lazarus': ('threat_actor', ['lazarus group', 'hidden cobra', 'zinc']), 'lazarus group': ('threat_actor', ['lazarus', 'hidden cobra']), 'hidden cobra': ('threat_actor', ['lazarus', 'lazarus group']), 'fin7': ('threat_actor', ['carbanak', 'fin7', 'carbanak gang']), 'carbanak': ('threat_actor', ['fin7', 'carbanak gang', 'anunak']), 'carbanak gang': ('threat_actor', ['fin7', 'carbanak']), 'anunak': ('threat_actor', ['carbanak', 'fin7']), 'fin8': ('threat_actor', ['fin8', 'punkey']), 'punkey': ('threat_actor', ['fin8']), 'apt17': ('threat_actor', ['apt17', 'tailgater team']), 'tailgater team': ('threat_actor', ['apt17']), 'apt19': ('threat_actor', ['apt19', 'joe team']), 'joe team': ('threat_actor', ['apt19']), 'apt32': ('threat_actor', ['apt32', 'ocean lot']), 'ocean lot': ('threat_actor', ['apt32']), 'apt37': ('threat_actor', ['apt37', 'reaper group', 'geumseong']), 'reaper group': ('threat_actor', ['apt37']), 'geumseong': ('threat_actor', ['apt37']), 'apt38': ('threat_actor', ['apt38', 'zinc', 'lazarus group']), 'unc': ('threat_actor', ['unc2452', 'unc2890']), 'unc2452': ('threat_actor', ['unc2452', 'ta428']), 'unc2890': ('threat_actor', ['unc2890']), 'ta428': ('threat_actor', ['ta428', 'apt38']), 'menupass': ('threat_actor', ['menupass', 'princess threat']), 'princess threat': ('threat_actor', ['menupass']), 'passive': ('threat_actor', ['passive', 'apt']), 'laz': ('threat_actor', ['lazarus', 'lazarus group']), 'thorny': ('threat_actor', ['carbanak', 'fin7'])}

def lookup_threat_entity(name: str) -> tuple[str, str] | None:
    """Look up threat actor or malware family. Returns (type, primary_name) or None.

    GHOST_INVARIANTS:
      - Bounded: O(1) dict lookup, no iteration over full dict
      - Fail-safe: returns None on any error
    """
    try:
        key = name.lower().strip()
        if key in _THREAT_DICTIONARY:
            entity_type, aliases = _THREAT_DICTIONARY[key]
            primary_name = aliases[0] if aliases else key
            return (entity_type, primary_name)
        return None
    except Exception:
        return None

def _expand_keyword_query(query: str) -> list[str]:
    """
    P1-2: Expand generic query to extract actionable indicators.

    Returns up to 10 keywords spanning threat actors, TTPs, and IOCs.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: max 10 keywords returned
      - Fail-safe: returns [query] on any error
    """
    try:
        if not query or not query.strip():
            return [query] if query else []
        keywords: list[str] = []
        seen: set[str] = set()
        query_lower = query.lower()
        for keyword in DOMAIN_EXPANSIONS:
            if keyword in query_lower:
                keywords.append(keyword)
        _ttp_pattern = re.compile('\\bT\\d{4}(?:\\.\\d{3})?\\b', re.IGNORECASE)
        for match in _ttp_pattern.findall(query):
            if match not in seen:
                seen.add(match)
                keywords.append(match)
        try:
            from brain.ner_engine import extract_iocs_from_text
            iocs = extract_iocs_from_text(query)
            for ioc in iocs[:5]:
                val = ioc.get('value', '')
                if val and val not in seen:
                    seen.add(val)
                    keywords.append(val)
        except Exception:
            pass
        return keywords[:10] if keywords else [query]
    except Exception:
        return [query] if query else []

def _get_keyword_domain_expansion(query: str) -> list[str]:
    """
    F1-3: Extract domain expansion seeds from keywords in query.

    Maps threat-category keywords → expansion domains for lanes that need
    a domain/IP seed (CT, WAYBACK, PASSIVE_DNS).

    E.g. "ransomware C2" → ["ransomware_tracker.abuse.ch"]
         "botnet"         → ["abuse.ch", "feodotracker.nl", "urlhaus.abuse.ch"]

    Returns:
        List of domain expansion strings (bounded, deduped, first-seen order).

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: max 10 domains returned
      - Fail-safe: returns [] on any error
    """
    try:
        keywords = _expand_keyword_query(query)
        seen: dict[str, None] = {}
        for kw in keywords:
            expansions = DOMAIN_EXPANSIONS.get(kw.lower(), ())
            for exp in expansions:
                if exp not in seen:
                    seen[exp] = None
        result = list(seen.keys())[:10]
        return result
    except Exception:
        return []
__all__ = ['AcquisitionLane', 'AcquisitionProfile', 'AcquisitionLanePlan', 'AcquisitionStrategySnapshot', 'AcquisitionLaneOutcome', 'SourceFamilyOutcome', 'NonfeedPlanDebug', 'MandatoryLaneTerminality', 'FeedDominanceBudget', '_load_feed_budget_from_env', 'required_terminal_lanes', 'lane_is_terminal', 'terminality_report', 'ACQUISITION_REPORT_SCHEMA_VERSION', 'build_acquisition_plan', 'build_acquisition_report', 'build_lane_query', 'is_lane_enabled', 'get_lane_plan', 'lane_skip_reason', 'normalize_source_family_outcome', 'normalize_source_family_name', 'canonicalize_source_family_outcomes', 'normalize_terminal_state', 'TERMINAL_STATES', 'NON_TERMINAL_STATES', 'NonfeedMissionController', 'NonfeedMissionSnapshot', 'MissionIntent', 'MissionTargetKind', 'infer_mission_intent', 'normalize_acquisition_profile', 'is_academic_profile', 'is_deep_osint_m1_profile', '_has_explicit_cid', '_extract_cids_from_text', '_CIDV0_RE', '_CIDV1_BASE32_RE', 'reconcile_lane_detail_fields', 'complete_source_family_outcomes_from_lane_details', 'DOMAIN_EXPANSIONS', '_expand_keyword_query']
ACQUISITION_REPORT_SCHEMA_VERSION = 'f208.v1'

class AcquisitionLane:
    FEED = 'FEED'
    PUBLIC = 'PUBLIC'
    CT = 'CT'
    WAYBACK = 'WAYBACK'
    PASSIVE_DNS = 'PASSIVE_DNS'
    BLOCKCHAIN = 'BLOCKCHAIN'
    STEALTH = 'STEALTH'
    PIVOT_EXECUTOR = 'PIVOT_EXECUTOR'
    ACADEMIC = 'ACADEMIC'
    IPFS = 'IPFS'
    DOH = 'DOH'
    OPEN_SOURCE = 'OPEN_SOURCE'
    SHODAN = 'SHODAN'
    CENSYS = 'CENSYS'
    GREYNOISE = 'GREYNOISE'
_CIDV0_RE = re.compile('^Qm[A-Za-z2-7]{44}$')
_CIDV1_BASE32_RE = re.compile('^bafy[a-z2-7]{50,59}$')

def _has_explicit_cid(value: str) -> bool:
    """Return True if value is an explicit IPFS CID (CIDv0 or CIDv1 base32)."""
    if not value or len(value) < 46 or len(value) > 70:
        return False
    if value.startswith('Qm') and len(value) == 46:
        return bool(_CIDV0_RE.match(value))
    if value.startswith('bafy'):
        return bool(_CIDV1_BASE32_RE.match(value))
    return False

def _extract_cids_from_text(text: str) -> list[str]:
    """Extract unique explicit CIDs from arbitrary text. Bounded dedup."""
    if not text:
        return []
    cids_seen: set[str] = set()
    cids: list[str] = []
    for word in text.split():
        word = word.strip().rstrip('/').rstrip(')')
        if _has_explicit_cid(word) and word not in cids_seen:
            cids_seen.add(word)
            cids.append(word)
        if '/' in word or ':' in word:
            for part in word.replace(':', '/').split('/'):
                part = part.strip()
                if _has_explicit_cid(part) and part not in cids_seen:
                    cids_seen.add(part)
                    cids.append(part)
    return cids
_MISSION_FEED_CAP_THRESHOLDS: dict[str, int] = {'cve_recon': 100, 'wallet_recon': 15, 'domain_recon': 20, 'infra_recon': 20, 'person_recon': 20, 'unknown': 0, 'org_recon': 0}
_NONFEED_PROFILE_FEED_CAP_THRESHOLDS: dict[str, int] = {'cve_recon': 100, 'wallet_recon': 15, 'domain_recon': 20, 'infra_recon': 20, 'person_recon': 20, 'unknown': 0, 'org_recon': 0}

def _feed_budget_to_dict(fdb) -> dict:
    """Convert FeedDominanceBudget (msgspec.Struct, dataclass, or dict) to a JSON-serializable dict.

    F216E-FIX: orjson cannot serialize msgspec.Struct directly.
    Handles FeedDominanceBudget (msgspec.Struct), dataclass instances, and plain dicts.
    Detection order: msgspec.Struct (__struct_fields__) → dataclass (__dataclass_fields__).
    """
    if fdb is None:
        return {}
    if isinstance(fdb, dict):
        return fdb
    fdb_type = type(fdb)
    if hasattr(fdb_type, '__struct_fields__'):
        return {'max_feed_accepted_before_nonfeed_terminal': getattr(fdb, 'max_feed_accepted_before_nonfeed_terminal', 0) or 0, 'max_feed_per_source': getattr(fdb, 'max_feed_per_source', 0) or 0, 'max_feed_share_before_nonfeed_terminal': getattr(fdb, 'max_feed_share_before_nonfeed_terminal', 0.0) or 0.0}
    if hasattr(fdb, '__dataclass_fields__'):
        return {'max_feed_accepted_before_nonfeed_terminal': getattr(fdb, 'max_feed_accepted_before_nonfeed_terminal', 0) or 0, 'max_feed_per_source': getattr(fdb, 'max_feed_per_source', 0) or 0, 'max_feed_share_before_nonfeed_terminal': getattr(fdb, 'max_feed_share_before_nonfeed_terminal', 0.0) or 0.0}
    return {}

class FeedDominanceBudget(msgspec.Struct, frozen=True):
    """F216E / Sprint C: Canonical feed dominance budget policy.

    Limits how many feed findings can be accepted before nonfeed lanes
    are given priority. Activated for non-default profiles when mandatory
    nonfeed lanes are unresolved.

    F227D: Added mission_intent context to adjust cap thresholds.
    Missions like domain_recon/person_recon/infra_recon cap FEED earlier
    once feed evidence accumulates and nonfeed is unresolved, while
    cve_recon preserves feed lanes because feeds are high-value for CVE ops.

    Sprint C migration: @dataclass(frozen=True) → msgspec.Struct().
    Benefits: C-level __init__ (~2-3× faster), no GC tracking (~40B saved),
    zero-cost property access on hot paths.

    Invariants:
      - max_feed_accepted_before_nonfeed_terminal >= max_feed_per_source
      - All limits are bounded (min 1, max 10000)
      - Safe to use as frozen Struct field
    """
    max_feed_accepted_before_nonfeed_terminal: int | None = None
    max_feed_per_source: int | None = None
    max_feed_share_before_nonfeed_terminal: float | None = None

    def is_sentinel(self) -> bool:
        """Return True when all caps are at sentinel (None) — feature fully disabled."""
        return self.max_feed_accepted_before_nonfeed_terminal is None and self.max_feed_per_source is None and (self.max_feed_share_before_nonfeed_terminal is None)

    def is_active(self) -> bool:
        """Return True when any cap is configured (non-sentinel)."""
        return not self.is_sentinel()

    def cap_feeding(self, feed_accepted_so_far: int, nonfeed_accepted_so_far: int, feed_per_source: dict[str, int], mission_intent: str | None=None, nonfeed_unresolved: bool=True, acquisition_profile: str | None=None) -> tuple[bool, str]:
        """Check if feeding should be capped.

        F227D: Added mission_intent and nonfeed_unresolved parameters.
        When mission_runtime is active and nonfeed lanes are unresolved,
        mission-aware thresholds override the base budget thresholds.

        F230D: Added acquisition_profile parameter for nonfeed_diagnostic profile
        per-intent feed cap thresholds.

        Returns (should_cap, reason) where reason is empty when cap not active.
        """
        if not self.is_active() and (not self._mission_cap_active(mission_intent)) and (not self._nonfeed_profile_cap_active(acquisition_profile)):
            return (False, '')
        if self._nonfeed_profile_cap_active(acquisition_profile) and nonfeed_unresolved:
            _effective_intent = mission_intent if mission_intent else 'unknown'
            profile_cap = _NONFEED_PROFILE_FEED_CAP_THRESHOLDS.get(_effective_intent, 0)
            if profile_cap > 0 and feed_accepted_so_far >= profile_cap:
                return (True, f'feed_cap_active:nonfeed_profile:{_effective_intent}:{feed_accepted_so_far}>={profile_cap}')
        if self._mission_cap_active(mission_intent) and nonfeed_unresolved:
            mission_cap = _MISSION_FEED_CAP_THRESHOLDS.get(mission_intent, 0)
            if mission_cap > 0 and feed_accepted_so_far >= mission_cap:
                return (True, f'feed_cap_active:mission:{mission_intent}:{feed_accepted_so_far}>={mission_cap}')
        if self.is_active():
            if self.max_feed_accepted_before_nonfeed_terminal is not None and nonfeed_unresolved and (feed_accepted_so_far >= self.max_feed_accepted_before_nonfeed_terminal):
                return (True, f'feed_cap_active:global:{feed_accepted_so_far}>={self.max_feed_accepted_before_nonfeed_terminal}')
            if self.max_feed_per_source is not None:
                for source, count in feed_per_source.items():
                    if count >= self.max_feed_per_source:
                        return (True, f'feed_cap_active:per_source:{source}:{count}>={self.max_feed_per_source}')
            if self.max_feed_share_before_nonfeed_terminal is not None and nonfeed_unresolved:
                total = feed_accepted_so_far + nonfeed_accepted_so_far
                if total > 0:
                    share = feed_accepted_so_far / total
                    if share >= self.max_feed_share_before_nonfeed_terminal:
                        return (True, f'feed_cap_active:share:{share:.2f}>={self.max_feed_share_before_nonfeed_terminal}')
        return (False, '')

    def _mission_cap_active(self, mission_intent: str | None) -> bool:
        """F227D: Return True when mission-aware cap should be evaluated."""
        if mission_intent is None:
            return False
        threshold = _MISSION_FEED_CAP_THRESHOLDS.get(mission_intent, 0)
        return threshold > 0

    def _nonfeed_profile_cap_active(self, acquisition_profile: str | None) -> bool:
        """F230D: Return True when nonfeed_diagnostic profile cap should be evaluated."""
        return acquisition_profile == AcquisitionProfile.NONFEED_DIAGNOSTIC

def _load_feed_budget_from_env() -> FeedDominanceBudget:
    """Load FeedDominanceBudget from environment variables with safe fallback."""
    import os

    def _int(key: str, default: int | None) -> int | None:
        try:
            val = os.environ.get(key, '')
            return max(1, min(10000, int(val))) if val else default
        except (ValueError, OverflowError):
            return default

    def _float(key: str, default: float | None) -> float | None:
        try:
            val = os.environ.get(key, '')
            return max(0.0, min(1.0, float(val))) if val else default
        except (ValueError, OverflowError):
            return default
    return FeedDominanceBudget(max_feed_accepted_before_nonfeed_terminal=_int('HLEDAC_FEED_MAX_ACCEPTED_BEFORE_NONFEED', None), max_feed_per_source=_int('HLEDAC_FEED_MAX_PER_SOURCE', None), max_feed_share_before_nonfeed_terminal=_float('HLEDAC_FEED_MAX_SHARE_BEFORE_NONFEED', None))

class RiskLevel(StrEnum):
    """Risk levels for acquisition lane planning.

    Inherits from `str` so the enum members are also `str` instances —
    preserves the existing `risk_level: str = RiskLevel.MEDIUM` field
    type without forcing all callers to migrate. Values match canonical
    `project_types.RiskLevel` (lowercase).
    """
    LOW = 'low'
    MEDIUM = 'medium'
    HIGH = 'high'
    CRITICAL = 'critical'

class AcquisitionLanePlan(msgspec.Struct, frozen=True):
    """Plan for one acquisition lane."""
    lane: str
    enabled: bool
    reason: str
    max_items: int = 50
    timeout_s: int = 30
    concurrency: int = 2
    risk_level: str = RiskLevel.MEDIUM

class AcquisitionContext(msgspec.Struct, frozen=True):
    """Derived flags bundle for lane planning — constructed once per _build_plan_impl call."""
    query: str
    duration_s: float
    aggressive_mode: bool
    uma_state: str
    swap_detected: bool
    hardware_critical: bool
    has_domain: bool
    has_url: bool
    has_crypto: bool
    has_long_duration: bool
    is_nonfeed_diagnostic: bool
    transport_degraded: bool
    stealth_ready: bool
    base_concurrency: int
    is_academic: bool
    is_deep_osint_m1: bool = False
    has_ip: bool = False
    cid_present: bool = False
    _feed_max_items: int = field(default=50)
    _feed_cap_reason: str | None = field(default=None)

class LaneSpec(msgspec.Struct, frozen=True):
    """Static per-lane execution constants."""
    max_items: int
    timeout_s: int
    risk_level: str

class LaneRule(msgspec.Struct, frozen=True):
    """Table-driven lane planning rule.

    One rule per AcquisitionLane.  The enabled/reason/concurrency logic
    is expressed as pure functions of AcquisitionContext so the full
    decision table is visible and auditable in one place.
    """
    lane: str
    spec: LaneSpec
    enabled: Callable[[AcquisitionContext], bool]
    reason: Callable[[AcquisitionContext], str]
    concurrency: Callable[[AcquisitionContext], int]
LaneSpecFeed = LaneSpec(max_items=50, timeout_s=30, risk_level=RiskLevel.LOW)
LaneSpecFeedNFD = LaneSpec(max_items=25, timeout_s=30, risk_level=RiskLevel.LOW)
LaneSpecPublic = LaneSpec(max_items=30, timeout_s=45, risk_level=RiskLevel.MEDIUM)
LaneSpecCT = LaneSpec(max_items=100, timeout_s=60, risk_level=RiskLevel.MEDIUM)
LaneSpecDOH = LaneSpec(max_items=20, timeout_s=30, risk_level=RiskLevel.MEDIUM)
LaneSpecWayback = LaneSpec(max_items=20, timeout_s=90, risk_level=RiskLevel.MEDIUM)
LaneSpecPDNS = LaneSpec(max_items=50, timeout_s=30, risk_level=RiskLevel.MEDIUM)
LaneSpecBlockchain = LaneSpec(max_items=20, timeout_s=60, risk_level=RiskLevel.HIGH)
LaneSpecStealth = LaneSpec(max_items=10, timeout_s=120, risk_level=RiskLevel.CRITICAL)
LaneSpecPivot = LaneSpec(max_items=20, timeout_s=15, risk_level=RiskLevel.LOW)
LaneSpecAcademic = LaneSpec(max_items=10, timeout_s=45, risk_level=RiskLevel.MEDIUM)
LaneSpecIPFS = LaneSpec(max_items=3, timeout_s=60, risk_level=RiskLevel.MEDIUM)
LaneSpecOpenSrc = LaneSpec(max_items=20, timeout_s=60, risk_level=RiskLevel.MEDIUM)
LaneSpecShodan = LaneSpec(max_items=20, timeout_s=30, risk_level=RiskLevel.MEDIUM)
LaneSpecCensys = LaneSpec(max_items=20, timeout_s=45, risk_level=RiskLevel.MEDIUM)
LaneSpecGreyNoise = LaneSpec(max_items=30, timeout_s=20, risk_level=RiskLevel.LOW)

def _lc(lane: str, base: int, uma_state: str) -> int:
    """Apply lane-specific concurrency adjustments on top of base."""
    if uma_state in ('critical', 'emergency'):
        if lane in (AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN, AcquisitionLane.STEALTH):
            return max(1, base // 2)
    if uma_state == 'warn':
        if lane in (AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN):
            return max(1, base - 1)
    return base

def _lane_rule(lane: str, spec: LaneSpec, enabled_fn: Callable[[AcquisitionContext], bool], reason_fn: Callable[[AcquisitionContext], str], conc_fn: Callable[[AcquisitionContext], int]) -> LaneRule:
    return LaneRule(lane=lane, spec=spec, enabled=enabled_fn, reason=reason_fn, concurrency=conc_fn)

def _disabled_reason(lane: str, ctx: AcquisitionContext) -> str:
    """Return the disabled-reason string for a lane, matching original inline logic."""
    if lane == AcquisitionLane.FEED:
        if ctx.uma_state in ('critical', 'emergency'):
            return 'hardware_critical'
        if ctx.swap_detected:
            return 'swap_detected'
        return 'uma_warn_state'
    if lane == AcquisitionLane.PUBLIC:
        if ctx.transport_degraded:
            return 'transport_degraded'
        if ctx.hardware_critical:
            return 'hardware_critical'
        return 'query_not_domain'
    if lane == AcquisitionLane.CT:
        return 'query_not_domain_like'
    if lane == AcquisitionLane.DOH:
        return 'query_without_domain_or_ip'
    if lane == AcquisitionLane.WAYBACK:
        return 'query_without_url'
    if lane == AcquisitionLane.PASSIVE_DNS:
        return 'query_without_indicator'
    if lane == AcquisitionLane.BLOCKCHAIN:
        return 'query_without_crypto'
    if lane == AcquisitionLane.STEALTH:
        if ctx.is_nonfeed_diagnostic:
            return 'nonfeed_diagnostic_disabled'
        if ctx.hardware_critical:
            return 'hardware_critical'
        return 'disabled_by_default'
    if lane == AcquisitionLane.PIVOT_EXECUTOR:
        return 'always_allowed_lightweight'
    if lane == AcquisitionLane.ACADEMIC:
        if ctx.hardware_critical:
            return 'hardware_critical'
        return 'non_academic_profile'
    if lane == AcquisitionLane.IPFS:
        if not ctx.cid_present:
            return 'no_cid_in_query'
        return 'hardware_critical'
    if lane == AcquisitionLane.OPEN_SOURCE:
        if ctx.hardware_critical:
            return 'hardware_critical'
        return 'non_academic_profile'
    return 'lane_disabled'
LANE_RULES: tuple[LaneRule, ...] = (_lane_rule(AcquisitionLane.FEED, LaneSpecFeed, lambda ctx: ctx.uma_state not in ('critical', 'emergency'), lambda _: 'always_allowed', lambda ctx: _lc(AcquisitionLane.FEED, ctx.base_concurrency, ctx.uma_state)), _lane_rule(AcquisitionLane.PUBLIC, LaneSpecPublic, lambda ctx: not ctx.transport_degraded if ctx.is_deep_osint_m1 else ctx.is_nonfeed_diagnostic and ctx.has_domain and (not ctx.transport_degraded) if ctx.is_nonfeed_diagnostic else ctx.uma_state not in ('critical', 'emergency') and (not ctx.transport_degraded), lambda ctx: 'deep_osint_m1_stage1' if ctx.is_deep_osint_m1 else 'nonfeed_diagnostic_domain' if ctx.is_nonfeed_diagnostic and ctx.has_domain else 'transport_degraded' if ctx.transport_degraded else 'hardware_critical' if ctx.uma_state in ('critical', 'emergency') else 'query_eligible', lambda ctx: _lc(AcquisitionLane.PUBLIC, ctx.base_concurrency, ctx.uma_state)), _lane_rule(AcquisitionLane.CT, LaneSpecCT, lambda ctx: (ctx.has_domain or ctx.aggressive_mode or ctx.is_nonfeed_diagnostic) and (not ctx.hardware_critical or ctx.aggressive_mode) and (not ctx.is_deep_osint_m1), lambda _: 'domain_or_aggressive_or_nonfeed_diagnostic', lambda ctx: _lc(AcquisitionLane.CT, ctx.base_concurrency, ctx.uma_state)), _lane_rule(AcquisitionLane.DOH, LaneSpecDOH, lambda ctx: (ctx.has_domain or (ctx.is_nonfeed_diagnostic and ctx.has_domain)) and (not ctx.hardware_critical or ctx.is_nonfeed_diagnostic or ctx.aggressive_mode) and (not ctx.is_deep_osint_m1), lambda _: 'domain_or_ip_or_nonfeed_diagnostic', lambda ctx: _lc(AcquisitionLane.DOH, ctx.base_concurrency, ctx.uma_state)), _lane_rule(AcquisitionLane.WAYBACK, LaneSpecWayback, lambda ctx: (ctx.has_url or ctx.has_long_duration or (ctx.is_nonfeed_diagnostic and ctx.has_domain)) and (not ctx.hardware_critical or ctx.is_nonfeed_diagnostic or ctx.aggressive_mode) and (not ctx.is_deep_osint_m1), lambda _: 'has_url_or_long_duration_or_nonfeed_domain', lambda ctx: _lc(AcquisitionLane.WAYBACK, ctx.base_concurrency, ctx.uma_state)), _lane_rule(AcquisitionLane.PASSIVE_DNS, LaneSpecPDNS, lambda ctx: ctx.has_domain and (not ctx.hardware_critical or ctx.is_nonfeed_diagnostic or ctx.aggressive_mode) and (not ctx.is_deep_osint_m1), lambda _: 'has_domain_or_ip', lambda ctx: _lc(AcquisitionLane.PASSIVE_DNS, ctx.base_concurrency, ctx.uma_state)), _lane_rule(AcquisitionLane.BLOCKCHAIN, LaneSpecBlockchain, lambda ctx: ctx.has_crypto and (not ctx.hardware_critical or ctx.aggressive_mode), lambda _: 'has_crypto_indicator', lambda ctx: _lc(AcquisitionLane.BLOCKCHAIN, ctx.base_concurrency, ctx.uma_state)), _lane_rule(AcquisitionLane.STEALTH, LaneSpecStealth, lambda ctx: ctx.stealth_ready and (not ctx.hardware_critical or ctx.aggressive_mode) and (not ctx.is_nonfeed_diagnostic), lambda _: 'stealth_ready', lambda _: 1), _lane_rule(AcquisitionLane.PIVOT_EXECUTOR, LaneSpecPivot, lambda ctx: True, lambda _: 'always_allowed_lightweight', lambda ctx: ctx.base_concurrency + 1), _lane_rule(AcquisitionLane.ACADEMIC, LaneSpecAcademic, lambda ctx: ctx.is_academic and (not ctx.hardware_critical or ctx.aggressive_mode), lambda _: 'academic_profile', lambda _: 1), _lane_rule(AcquisitionLane.IPFS, LaneSpecIPFS, lambda ctx: ctx.cid_present and (not ctx.hardware_critical or ctx.aggressive_mode), lambda _: 'explicit_cid_in_query', lambda _: 1), _lane_rule(AcquisitionLane.OPEN_SOURCE, LaneSpecOpenSrc, lambda ctx: ctx.is_academic and (not ctx.hardware_critical or ctx.aggressive_mode), lambda _: 'academic_profile', lambda _: 1), _lane_rule(AcquisitionLane.SHODAN, LaneSpecShodan, lambda ctx: ctx.has_ip and (not ctx.hardware_critical or ctx.aggressive_mode), lambda _: 'ip_or_cidr_indicator', lambda ctx: _lc(AcquisitionLane.SHODAN, ctx.base_concurrency, ctx.uma_state)), _lane_rule(AcquisitionLane.CENSYS, LaneSpecCensys, lambda ctx: ctx.has_domain and (not ctx.hardware_critical or ctx.aggressive_mode), lambda _: 'domain_or_cert_indicator', lambda ctx: _lc(AcquisitionLane.CENSYS, ctx.base_concurrency, ctx.uma_state)), _lane_rule(AcquisitionLane.GREYNOISE, LaneSpecGreyNoise, lambda ctx: ctx.has_ip and (not ctx.hardware_critical or ctx.aggressive_mode), lambda _: 'ip_or_cidr_indicator', lambda ctx: _lc(AcquisitionLane.GREYNOISE, ctx.base_concurrency, ctx.uma_state)))

class NonfeedPlanDebug(msgspec.Struct):
    """[F207L] Diagnostic snapshot of nonfeed lane planning for live KPI debugging.

    Records what the acquisition planner decided and why,
    so live KPI can diagnose nonfeed_attempted=0 root cause.
    F227D: Mutable so scheduler can annotate cap reason during sprint execution.
    """
    domain_detected: bool = False
    wallet_detected: bool = False
    enabled_nonfeed_lanes: tuple[str, ...] = ()
    disabled_nonfeed_lanes: tuple[str, ...] = ()
    disabled_reasons: tuple[str, ...] = ()
    scheduled_nonfeed_lanes: tuple[str, ...] = ()
    hardware_skipped_lanes: tuple[str, ...] = ()
    nonfeed_execution_scheduled: bool = False
    nonfeed_execution_skip_reason: str | None = None
    acquisition_profile: str = 'default'
    feed_cap_reason: str | None = None
    nonfeed_priority_enabled: bool = False
    nonfeed_profile_expected_lanes: tuple[str, ...] = ()
    pivot_executor_enabled: bool = False
    pivot_candidates_count: int = 0
    pivot_candidate_types: tuple[str, ...] = ()
    pivot_scheduled_lanes: tuple[str, ...] = ()
    pivot_skip_reason: str | None = None
    pivot_errors: tuple[str, ...] = ()
    mission_intent: str = 'unknown'
    mission_target_kind: str = 'unknown'
    mission_required_lanes: tuple[str, ...] = ()
    mission_optional_lanes: tuple[str, ...] = ()
    mission_reason: str = ''
    mission_runtime_applied: bool = False
    mission_lane_priority: tuple[str, ...] = ()
    mission_pivot_boost_applied: bool = False
    mission_feed_cap_reason: str | None = None
    feed_cap_applied_by_mission: bool = False
    feed_cap_mission_intent: str | None = None
    feed_domain_candidates_count: int = 0
    feed_domain_candidates_top: tuple[str, ...] = ()
    feed_lane_eligible_ct: bool = False
    feed_lane_eligible_doh: bool = False
    feed_lane_eligible_wayback: bool = False
    feed_lane_eligible_passive_dns: bool = False

@dataclass(slots=True)
class NonfeedSeedContext:
    """
    F222I: Bounded seed context for nonfeed lane query shaping.

    Produced by pivot planner / DuckDB seed extraction from text query.
    Threaded into build_lane_query so lanes receive deterministic domain/IP
    seeds instead of the generic text query.

    Bounds:
      - max_domains=10, max_ips=10, max_urls=10 — hard caps
      - All fields are tuples (immutable, hashable)
      - Publisher domains (source URL hostnames) are excluded from seeds

    Lane shaping rules:
      CT:          domains[0] if available, else empty
      DOH:         domains[0] if available, else _disabled
      WAYBACK:     domains[0] or URLs[0] if available
      PASSIVE_DNS: domains[0] or IPs[0] if available
      PUBLIC:      unchanged (original text query)
      FEED:        unchanged
    """
    domains: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    hashes: tuple[str, ...] = ()
    cves: tuple[str, ...] = ()
    duckpgq_entities: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        if len(self.domains) > 10:
            object.__setattr__(self, 'domains', self.domains[:10])
        if len(self.ips) > 10:
            object.__setattr__(self, 'ips', self.ips[:10])
        if len(self.urls) > 10:
            object.__setattr__(self, 'urls', self.urls[:10])

    @property
    def has_domain(self) -> bool:
        return bool(self.domains)

    @property
    def has_ip(self) -> bool:
        return bool(self.ips)

    @property
    def has_url(self) -> bool:
        return bool(self.urls)

    def kind_counts(self) -> dict[str, int]:
        """Return counts by non-empty seed kind."""
        return {k: len(v) for k, v in [('domains', self.domains), ('ips', self.ips), ('urls', self.urls), ('hashes', self.hashes), ('cves', self.cves)] if v}

class AcquisitionStrategySnapshot(msgspec.Struct):
    """Full acquisition strategy snapshot for one sprint/cycle."""
    query: str = ''
    duration_s: float = 0.0
    aggressive_mode: bool = False
    uma_state: str = 'ok'
    swap_detected: bool = False
    accepted_findings_so_far: int = 0
    branch_timeout_count: int = 0
    stealth_ready: bool = False
    transport_degraded: bool = False
    plans: tuple[AcquisitionLanePlan, ...] = ()
    nonfeed_plan_debug: NonfeedPlanDebug | None = None
    feed_dominance_budget: FeedDominanceBudget = FeedDominanceBudget()
    nonfeed_candidate_ledger_summary: dict = field(default_factory=dict)
    bootstrap_enabled: bool = False
    has_domain: bool = False

class MandatoryLaneTerminality(msgspec.Struct, frozen=True):
    """[F208A] Sprint F300 migration: @dataclass(slots=True) → msgspec.Struct.

    A mandatory lane must reach a terminal state (attempted, skipped, error, timeout)
    before a sprint is considered complete. This dataclass defines the contract.
    C-level __init__ (~2-3× faster), no GC tracking (~40B saved).
    """
    lane: str
    required: bool
    reason: str
    allowed_terminal_states: tuple[str, ...]
    max_attempts: int = 1
    timeout_s: int = 60

def required_terminal_lanes(snapshot: AcquisitionStrategySnapshot, query: str, uma_state: str, swap_detected: bool) -> tuple[MandatoryLaneTerminality, ...]:
    """[F208A] Determine which lanes are mandatory for terminality.

    Rules:
      - domain query + ok/warn memory: PUBLIC required, CT required
      - domain query + critical: CT required (as attempted or explicit skip),
        PUBLIC explicit skip allowed with memory_critical
      - emergency: all non-feed lanes explicit skip with memory_emergency
      - non-domain: CT not required (skip reason no_domain)
      - STEALTH: never required by default

    Args:
        snapshot:    Current acquisition strategy snapshot.
        query:       Sprint query string.
        uma_state:   Current UMA state (ok, warn, critical, emergency).
        swap_detected: True if swap has been detected.

    Returns:
        Tuple of MandatoryLaneTerminality, one per lane that has terminality requirements.
    """
    has_domain = _has_domain_or_ip(query)
    is_emergency = uma_state == 'emergency'
    is_critical = uma_state == 'critical'
    _nd = getattr(snapshot, 'nonfeed_plan_debug', None) if snapshot else None
    _is_nonfeed_diagnostic = getattr(_nd, 'acquisition_profile', '') == 'nonfeed_diagnostic' if _nd else False
    lanes: list[MandatoryLaneTerminality] = []
    _is_threat_query = _has_threat_indicator(query)
    if has_domain and uma_state in ('ok', 'warn'):
        lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.PUBLIC, required=True, reason='domain_query_requires_public', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    elif _is_threat_query and uma_state in ('ok', 'warn'):
        lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.PUBLIC, required=True, reason='threat_query_requires_public', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    elif _is_threat_query and is_critical:
        lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.PUBLIC, required=False, reason='critical_allows_explicit_skip', allowed_terminal_states=('skipped',), max_attempts=0))
    elif has_domain and is_critical:
        lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.PUBLIC, required=False, reason='critical_allows_explicit_skip', allowed_terminal_states=('skipped',), max_attempts=0))
    elif is_emergency:
        lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.PUBLIC, required=False, reason='memory_emergency', allowed_terminal_states=('skipped',), max_attempts=0))
    else:
        lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.PUBLIC, required=False, reason='not_required_for_query_type', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    if is_emergency:
        lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.CT, required=False, reason='memory_emergency', allowed_terminal_states=('skipped',), max_attempts=0))
    elif not has_domain:
        lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.CT, required=False, reason='no_domain', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    elif is_critical:
        lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.CT, required=True, reason='critical_requires_ct_terminal', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    else:
        lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.CT, required=True, reason='domain_query_requires_ct', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.WAYBACK, required=False, reason='wayback_not_mandatory', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.PASSIVE_DNS, required=False, reason='passive_dns_not_mandatory', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.BLOCKCHAIN, required=False, reason='blockchain_not_mandatory', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.STEALTH, required=False, reason='stealth_never_mandatory_by_default', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    lanes.append(MandatoryLaneTerminality(lane=AcquisitionLane.PIVOT_EXECUTOR, required=False, reason='pivot_not_mandatory', allowed_terminal_states=('attempted', 'skipped', 'error', 'timeout')))
    return tuple(lanes)

def lane_is_terminal(outcome_or_dict) -> bool:
    """[F208A] Return True if the lane outcome is in a terminal state.

    Terminal states:
      - attempted=True (lane ran at least once)
      - skipped=True (lane was intentionally skipped)
      - error is not None (lane encountered an error)
      - timeout=True (lane exceeded its time limit)
    """
    if outcome_or_dict is None:
        return False
    d: dict
    if hasattr(outcome_or_dict, 'to_dict'):
        d = outcome_or_dict.to_dict()
    elif isinstance(outcome_or_dict, dict):
        d = outcome_or_dict
    else:
        return False
    if d.get('attempted'):
        return True
    if d.get('skipped'):
        return True
    if d.get('error') is not None:
        return True
    if d.get('timeout'):
        return True
    return False
TERMINAL_STATES = frozenset(['success', 'success_empty', 'empty', 'attempted', 'skipped', 'error', 'timeout'])
NON_TERMINAL_STATES = frozenset(['pending', 'running', 'not_attempted', 'missing', '', None])

def normalize_terminal_state(outcome_or_dict) -> str | None:
    """[F208L] Map an outcome dict to a canonical terminal state string.

    Supported terminal states:
      - success       : attempted=True, accepted_count > 0
      - success_empty : attempted=True, raw_count > 0, accepted_count = 0
      - empty         : attempted=True, raw_count = 0, accepted_count = 0
      - attempted     : attempted=True, no other qualifier
      - skipped       : skipped=True
      - error         : error is not None and not empty string
      - timeout       : timeout=True

    Non-terminal states (return as-is for identity check):
      - pending
      - running
      - not_attempted
      - missing
      - ""  (empty string)
      - None

    accepted_count=0 alone does NOT make a lane non-terminal.
    raw_count > 0 with accepted_count = 0 normalizes to success_empty.
    raw_count = 0 with attempted = True normalizes to empty.
    """
    if outcome_or_dict is None:
        return None
    d: dict
    if hasattr(outcome_or_dict, 'to_dict'):
        d = outcome_or_dict.to_dict()
    elif isinstance(outcome_or_dict, dict):
        d = outcome_or_dict
    else:
        return None
    raw_state = d.get('terminal_state')
    if raw_state is not None and raw_state in NON_TERMINAL_STATES:
        return raw_state
    if d.get('skipped'):
        return 'skipped'
    if d.get('timeout'):
        return 'timeout'
    if d.get('error') is not None and d.get('error') != '':
        return 'error'
    if d.get('attempted'):
        has_raw_count = 'raw_count' in d
        raw_count = d.get('raw_count', 0)
        accepted_count = d.get('accepted_count', 0)
        if accepted_count > 0:
            return 'success'
        if has_raw_count and raw_count > 0 and (accepted_count == 0):
            return 'success_empty'
        if has_raw_count and raw_count == 0 and (accepted_count == 0):
            return 'empty'
        return 'attempted'
    return None

def terminality_report(required_lanes: tuple[MandatoryLaneTerminality, ...], observed_outcomes: tuple[dict, ...]) -> dict:
    """[F208A] Produce a terminality report comparing required vs observed lane states.

    Args:
        required_lanes:    Tuple of MandatoryLaneTerminality from required_terminal_lanes().
        observed_outcomes: Tuple of outcome dicts (from AcquisitionLaneOutcome.to_dict()).

    Returns:
        Dict with:
          checked: list of lane names checked
          satisfied: list of lane names with terminal outcomes
          required_lanes: list of mandatory lane specs
          terminal_lanes: list of lanes at terminal state
          missing_lanes: list of mandatory lanes NOT at terminal state
          skipped_lanes: list of lanes that were skipped
          errors: list of lanes with errors
          reasons: dict mapping lane → terminality reason string
    """
    checked: list[str] = []
    satisfied: list[str] = []
    terminal_lanes: list[str] = []
    missing_lanes: list[str] = []
    skipped_lanes: list[str] = []
    errors: list[str] = []
    reasons: dict[str, str] = {}
    outcomes_by_lane: dict[str, dict] = {}
    for outcome in observed_outcomes:
        lane = outcome.get('lane', '')
        if lane:
            outcomes_by_lane[lane] = outcome
    for mlt in required_lanes:
        checked.append(mlt.lane)
        reasons[mlt.lane] = mlt.reason
        outcome = outcomes_by_lane.get(mlt.lane, {})
        is_term = lane_is_terminal(outcome)
        if is_term:
            satisfied.append(mlt.lane)
            terminal_lanes.append(mlt.lane)
            if outcome.get('skipped'):
                skipped_lanes.append(mlt.lane)
            if outcome.get('error') is not None:
                errors.append(mlt.lane)
        elif mlt.required:
            missing_lanes.append(mlt.lane)
    return {'checked': checked, 'satisfied': satisfied, 'required_lanes': [mlt.lane for mlt in required_lanes if mlt.required], 'terminal_lanes': terminal_lanes, 'missing_lanes': missing_lanes, 'skipped_lanes': skipped_lanes, 'errors': errors, 'reasons': reasons}

def _build_nonfeed_lane_eligibility(query: str, acquisition_profile: str, plan: AcquisitionStrategySnapshot | None) -> dict:
    """
    F214: Build the nonfeed lane eligibility matrix for acquisition reporting.

    Computed from query indicators (not plan.enabled) so the matrix explains WHY
    each lane was or was not planned — using the same indicator logic as the
    planner, independent of runtime state (hardware, transport, etc.).

    Schema::

        {
            "public":  {"eligible": true, "reason": "...", "required_inputs": [], "available_inputs": {...}},
            "ct":      {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},
            "doh":     {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},
            "wayback": {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},
            "passive_dns": {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},  # noqa: E501
        }

    Profile rules (active300/default):
      - public:   always eligible if provider available (advisory, not gated by candidates)
      - CT:       eligible if domain (not IP-only) candidates present
      - DOH:      eligible if domain (not IP-only) candidates present
      - WAYBACK:  eligible if URL or domain candidates present
      - passive_dns: eligible if domain or IP candidates present

    Profile rules (nonfeed_diagnostic):
      - public:   expected if provider available
      - DOH:      expected if domains exist
      - CT:       expected if domains exist
      - WAYBACK:  expected if URLs/domains exist
      - passive_dns: expected if domains/IPs exist
    """
    import re as _re
    _raw_has_domain = _has_domain_or_ip(query)
    has_domain = getattr(plan, 'has_domain', _raw_has_domain) if plan is not None else _raw_has_domain
    has_url = _has_url(query)
    has_ip = bool(_re.search('\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b', query))
    has_fqdn = has_domain and (not has_ip)
    is_nonfeed_diagnostic = acquisition_profile == AcquisitionProfile.NONFEED_DIAGNOSTIC
    available = {'domain': has_fqdn, 'url': has_url, 'ip': has_ip}
    public_eligible = True
    public_reason = 'always_eligible_advisory' if not is_nonfeed_diagnostic else 'nonfeed_diagnostic_expected'
    ct_eligible = has_fqdn
    if ct_eligible:
        ct_reason = 'domain_candidates_present'
    elif is_nonfeed_diagnostic:
        ct_reason = 'nonfeed_diagnostic_no_domain_candidates'
    else:
        ct_reason = 'no_domain_candidates'
    doh_eligible = has_fqdn
    if doh_eligible:
        doh_reason = 'domain_candidates_present'
    elif is_nonfeed_diagnostic:
        doh_reason = 'nonfeed_diagnostic_no_domain_candidates'
    else:
        doh_reason = 'no_domain_candidates'
    wayback_eligible = has_url or has_fqdn
    if wayback_eligible:
        wayback_reason = 'url_or_domain_candidates_present'
    elif is_nonfeed_diagnostic:
        wayback_reason = 'nonfeed_diagnostic_no_url_or_domain_candidates'
    else:
        wayback_reason = 'no_url_or_domain_candidates'
    pdns_eligible = has_domain
    if pdns_eligible:
        pdns_reason = 'domain_or_ip_candidates_present'
    elif is_nonfeed_diagnostic:
        pdns_reason = 'nonfeed_diagnostic_no_domain_or_ip_candidates'
    else:
        pdns_reason = 'no_domain_or_ip_candidates'
    return {'public': {'eligible': public_eligible, 'reason': public_reason, 'required_inputs': [], 'available_inputs': available}, 'ct': {'eligible': ct_eligible, 'reason': ct_reason, 'required_inputs': ['domain'], 'available_inputs': available}, 'doh': {'eligible': doh_eligible, 'reason': doh_reason, 'required_inputs': ['domain'], 'available_inputs': available}, 'wayback': {'eligible': wayback_eligible, 'reason': wayback_reason, 'required_inputs': ['url', 'domain'], 'available_inputs': available}, 'passive_dns': {'eligible': pdns_eligible, 'reason': pdns_reason, 'required_inputs': ['domain', 'ip'], 'available_inputs': available}}

def build_acquisition_report(query: str='', plan: AcquisitionStrategySnapshot | None=None, terminality: dict | None=None, nonfeed_plan_debug: NonfeedPlanDebug | dict | None=None, source_family_outcomes: list[dict] | None=None, return_guard: dict | None=None, prewindup_barrier: dict | None=None, scheduler_exit: dict | None=None, windup_guard_observation: dict | None=None, acquisition_profile: str | None=None, feed_cap_reason: str | None=None, nonfeed_priority_enabled: bool=False, nonfeed_profile_expected_lanes: list[str] | None=None, public_terminal_stage: str='', public_stage_counters: dict | None=None, public_discovery_empty_reason: str='', public_discovery_debug_reason: str='', public_provider_selection_debug: dict | None=None, public_bootstrap_order: str='disabled', public_bootstrap_prevented_discovery_timeout: bool=False, public_bootstrap_first_fetch_attempted: bool=False, keyword_seed_fallback_triggered: bool=False, ct_provider_status: str='', ct_cache_used: bool=False, ct_cache_stale: bool=False, ct_cache_age_s: float=0.0, ct_quarantine_count: int=0, ct_quarantine_samples: list[str] | None=None, ct_planned: bool=False, ct_scheduled: bool=False, ct_provider_selected: str='', ct_request_attempted: bool=False, ct_request_timeout: bool=False, ct_raw_count: int=0, ct_bridge_invoked: bool=False, ct_candidates_built: int=0, ct_storage_attempted: bool=False, ct_storage_accepted: bool=False, ct_terminal_stage: str='', ct_prelude_missing_but_final_attempted: bool=False, doh_planned: bool=False, doh_scheduled: bool=False, doh_request_attempted: bool=False, doh_domains_attempted: int=0, doh_raw_count: int=0, doh_accepted_findings: int=0, doh_terminal_stage: str='', doh_provider_errors: tuple[str, ...]=(), doh_cache_used: bool=False, ct_bridge_rejections_count: int=0, ct_storage_rejected: int=0, arrow_last_flush_error: str='', arrow_batch_dropped: int=0, arrow_flush_failure_count: int=0, prewindup_barrier_errors: dict | None=None, return_guard_errors: dict | None=None, wayback_unchanged_rejected: int=0, nonfeed_provider_failures: list | None=None, quality_rejection_summary_by_family: dict | None=None, duplicate_rejection_summary_by_family: dict | None=None, low_information_by_family: dict | None=None, nonfeed_candidate_ledger_summary: dict | None=None, feed_dominance_budget: dict | None=None, nonfeed_expected_lanes: list[str] | None=None, nonfeed_missing_expected_lanes: list[str] | None=None, wayback_terminal_state: str='', passive_dns_terminal_state: str='', nonfeed_surface_complete: bool=False, pivot_seed_domains: tuple[str, ...]=(), pivot_seed_ips: tuple[str, ...]=(), pivot_seed_urls: tuple[str, ...]=(), pivot_seed_hashes: tuple[str, ...]=(), pivot_seed_cves: tuple[str, ...]=(), seed_context_available: bool=False, seed_context_propagated: bool=False, seed_context_skip_reason: str='', seed_context_source: str='', lanes_unlocked_by_seed_context: list[str] | None=None, acquisition_plan_build_failed: bool=False, acquisition_plan_build_error_type: str='', acquisition_plan_build_error: str='', acquisition_plan_present_for_prelude: bool=False, acquisition_plan_lanes_for_prelude: tuple[str, ...]=(), acquisition_plan_enabled_lanes_for_prelude: tuple[str, ...]=(), acquisition_plan_profile_for_prelude: str='', acquisition_plan_build_error_for_prelude: str='', nonfeed_prelude_enabled: bool=False, nonfeed_prelude_expected_lanes: tuple[str, ...]=(), nonfeed_prelude_attempted_lanes: tuple[str, ...]=(), nonfeed_prelude_terminal_lanes: tuple[str, ...]=(), nonfeed_prelude_missing_lanes: tuple[str, ...]=(), nonfeed_prelude_error_by_lane: dict | None=None, nonfeed_prelude_accepted_by_lane: dict | None=None, nonfeed_prelude_duration_s: float=0.0, nonfeed_prelude_feed_blocked_until_complete: bool=False, circuit_breakers_state: dict | None=None) -> dict:
    """
    [F208C] Build a stable canonical acquisition report dict.
    [F219A] Canonical Surface Contract Seal — extends F208C with full F216/F217 telemetry.

    This is the ONE canonical schema for acquisition telemetry. The benchmark
    parser checks report["acquisition_report"] FIRST before falling back to
    legacy sibling fields. This stops the parser whack-a-mole.

    Output shape::

        {
            "schema_version": "f208.v1",
            "plan": ...          # AcquisitionStrategySnapshot plans as dicts
            "terminality": ...   # terminality report from terminality_report()
            "nonfeed_plan_debug": ...  # NonfeedPlanDebug as dict
            "source_family_outcomes": ...  # list of SourceFamilyOutcome.to_dict()
            "return_guard": ...  # return guard observation dict
            "prewindup_barrier": ...  # prewindup barrier dict
            "scheduler_exit": ...  # scheduler exit telemetry dict
            "windup_guard_observation": ...  # windup guard observation dict
            # F216B: Nonfeed diagnostic profile telemetry
            "acquisition_profile": "default",
            "feed_cap_reason": None,
            "nonfeed_priority_enabled": False,
            "nonfeed_profile_expected_lanes": [],
            # F217C: PUBLIC bootstrap telemetry
            "public_terminal_stage": "",
            "public_stage_counters": {},
            # F217D: CT provider resilience telemetry
            "ct_provider_status": "",
            "ct_cache_used": False,
            "ct_cache_stale": False,
            "ct_cache_age_s": 0.0,
            "ct_quarantine_count": 0,
            "ct_quarantine_samples": [],
            # F216G: Quality rejection ledger
            "quality_rejection_summary_by_family": {},
            # F216G: Duplicate rejection ledger
            "duplicate_rejection_summary_by_family": {},
            # F216G: Low information rejection
            "low_information_by_family": {},
            # F217E: Nonfeed candidate ledger summary
            "nonfeed_candidate_ledger_summary": {},
            # F216E: Feed dominance budget telemetry
            "feed_dominance_budget": {},
        }

    Args:
        query:                      F214: Sprint query string (used for lane eligibility matrix).
        plan:                          AcquisitionStrategySnapshot from build_acquisition_plan().
        terminality:                    Result of terminality_report().
        nonfeed_plan_debug:             NonfeedPlanDebug snapshot.
        source_family_outcomes:         List of SourceFamilyOutcome.to_dict() dicts.
        return_guard:                  Return guard observation dict.
        prewindup_barrier:             Pre-windup barrier dict.
        scheduler_exit:                Scheduler exit telemetry dict.
        windup_guard_observation:      Windup guard observation dict.
        acquisition_profile:            F216B: Nonfeed diagnostic profile name.
        feed_cap_reason:                F216B: Reason FEED was capped (if any).
        nonfeed_priority_enabled:       F216B: Whether nonfeed priority was active.
        nonfeed_profile_expected_lanes: F216B: Expected nonfeed lanes for profile.
        public_terminal_stage:          F217C: PUBLIC bootstrap terminal stage.
        public_stage_counters:          F217C: PUBLIC stage counters dict.
        ct_provider_status:             F217D: CT provider status string.
        ct_cache_used:                 F217D: Whether CT cache was used.
        ct_cache_stale:                F217D: Whether CT cache was stale.
        ct_cache_age_s:                F217D: CT cache age in seconds.
        ct_quarantine_count:           F217D: CT quarantine entry count.
        ct_quarantine_samples:         F217D: CT quarantine sample strings.
        quality_rejection_summary_by_family: F216G: Quality rejection counts by family.
        duplicate_rejection_summary_by_family: F216G: Duplicate rejection counts.
        low_information_by_family:     F216G: Low-information rejection counts.
        nonfeed_candidate_ledger_summary: F217E: Nonfeed candidate ledger summary.
        feed_dominance_budget:         F216E: Feed dominance budget telemetry.
        # F228C: Nonfeed surface completeness telemetry
        nonfeed_expected_lanes:         F228C: Expected nonfeed lanes from profile.
        nonfeed_missing_expected_lanes: F228C: Expected lanes not surfaced.
        wayback_terminal_state:         F228C: WAYBACK family terminal state.
        passive_dns_terminal_state:     F228C: PASSIVE_DNS family terminal state.
        nonfeed_surface_complete:       F228C: True when all expected lanes surfaced.

    Returns:
        Canonical acquisition report dict with schema_version="f208.v1".
    """
    plan_dicts: list[dict] = []
    if plan is not None:
        for p in plan.plans:
            plan_dicts.append({'lane': p.lane, 'enabled': p.enabled, 'reason': p.reason, 'max_items': p.max_items, 'timeout_s': p.timeout_s, 'concurrency': p.concurrency, 'risk_level': p.risk_level})
    nonfeed_debug_dict: dict | None = None
    if nonfeed_plan_debug is not None:
        nd = nonfeed_plan_debug
        if isinstance(nd, dict):
            nonfeed_debug_dict = nd
        else:
            nonfeed_debug_dict = {'domain_detected': nd.domain_detected, 'wallet_detected': nd.wallet_detected, 'enabled_nonfeed_lanes': list(nd.enabled_nonfeed_lanes), 'disabled_nonfeed_lanes': list(nd.disabled_nonfeed_lanes), 'disabled_reasons': list(nd.disabled_reasons), 'scheduled_nonfeed_lanes': list(nd.scheduled_nonfeed_lanes), 'hardware_skipped_lanes': list(nd.hardware_skipped_lanes), 'nonfeed_execution_scheduled': nd.nonfeed_execution_scheduled, 'nonfeed_execution_skip_reason': nd.nonfeed_execution_skip_reason, 'acquisition_profile': getattr(nd, 'acquisition_profile', 'default'), 'feed_cap_reason': getattr(nd, 'feed_cap_reason', None), 'nonfeed_priority_enabled': getattr(nd, 'nonfeed_priority_enabled', False), 'nonfeed_profile_expected_lanes': list(getattr(nd, 'nonfeed_profile_expected_lanes', ()) or ()), 'pivot_executor_enabled': getattr(nd, 'pivot_executor_enabled', False), 'pivot_candidates_count': getattr(nd, 'pivot_candidates_count', 0), 'pivot_candidate_types': list(getattr(nd, 'pivot_candidate_types', ()) or ()), 'pivot_scheduled_lanes': list(getattr(nd, 'pivot_scheduled_lanes', ()) or ()), 'pivot_skip_reason': getattr(nd, 'pivot_skip_reason', None), 'pivot_errors': list(getattr(nd, 'pivot_errors', ()) or ()), 'mission_intent': getattr(nd, 'mission_intent', 'unknown'), 'mission_target_kind': getattr(nd, 'mission_target_kind', 'unknown'), 'mission_required_lanes': list(getattr(nd, 'mission_required_lanes', ()) or ()), 'mission_optional_lanes': list(getattr(nd, 'mission_optional_lanes', ()) or ()), 'mission_reason': getattr(nd, 'mission_reason', ''), 'mission_runtime_applied': getattr(nd, 'mission_runtime_applied', False), 'mission_lane_priority': list(getattr(nd, 'mission_lane_priority', ()) or ()), 'mission_pivot_boost_applied': getattr(nd, 'mission_pivot_boost_applied', False), 'mission_feed_cap_reason': getattr(nd, 'mission_feed_cap_reason', None), 'feed_cap_applied_by_mission': getattr(nd, 'feed_cap_applied_by_mission', False), 'feed_cap_mission_intent': getattr(nd, 'feed_cap_mission_intent', None)}
    _effective_profile = acquisition_profile if acquisition_profile is not None else 'default'
    import os as _os
    if _effective_profile == 'default':
        _env_override = _os.environ.get('HLEDAC_ACQUISITION_PROFILE', None)
        if _env_override is not None:
            logger.debug("[build_acquisition_report] acquisition_profile='default' resolved to %r from HLEDAC_ACQUISITION_PROFILE env var. This is expected only when called directly without normalization.", _env_override)
            _effective_profile = _env_override
    runtime_attempted_lanes: list[str] = []
    if source_family_outcomes:
        for outcome in source_family_outcomes:
            if outcome.get('attempted'):
                _family = outcome.get('family', '')
                if _family:
                    runtime_attempted_lanes.append(_family)
    required_lane_plan: list[str] = []
    if terminality:
        required_lane_plan = list(terminality.get('required_lanes', []) or [])
    _effective_seen: set[str] = set()
    effective_acquisition_plan: list[str] = []
    for lane in required_lane_plan:
        if lane and lane not in _effective_seen:
            _effective_seen.add(lane)
            effective_acquisition_plan.append(lane)
    for lane in runtime_attempted_lanes:
        if lane and lane not in _effective_seen:
            _effective_seen.add(lane)
            effective_acquisition_plan.append(lane)
    if runtime_attempted_lanes:
        plan_semantics: str = 'effective_runtime'
    else:
        plan_semantics = 'prelude_only'
    prelude_plan: list[dict] = plan_dicts
    return {'schema_version': ACQUISITION_REPORT_SCHEMA_VERSION, 'acquisition_report_fallback_used': False, 'plan': plan_dicts, 'prelude_plan': prelude_plan, 'required_lane_plan': required_lane_plan, 'runtime_attempted_lanes': runtime_attempted_lanes, 'effective_acquisition_plan': effective_acquisition_plan, 'plan_semantics': plan_semantics, 'terminality': terminality, 'nonfeed_plan_debug': nonfeed_debug_dict, 'source_family_outcomes': source_family_outcomes or [], 'return_guard': return_guard, 'prewindup_barrier': prewindup_barrier, 'scheduler_exit': scheduler_exit, 'windup_guard_observation': windup_guard_observation, 'acquisition_profile': _effective_profile, 'feed_cap_reason': feed_cap_reason, 'nonfeed_priority_enabled': nonfeed_priority_enabled, 'nonfeed_profile_expected_lanes': nonfeed_profile_expected_lanes or [], 'public_terminal_stage': public_terminal_stage, 'public_stage_counters': public_stage_counters or {}, 'public_discovery_empty_reason': public_discovery_empty_reason, 'public_discovery_debug_reason': public_discovery_debug_reason or '', 'public_provider_selection_debug': public_provider_selection_debug or {}, 'public_bootstrap_order': public_bootstrap_order, 'public_bootstrap_prevented_discovery_timeout': public_bootstrap_prevented_discovery_timeout, 'public_bootstrap_first_fetch_attempted': public_bootstrap_first_fetch_attempted, 'keyword_seed_fallback_triggered': keyword_seed_fallback_triggered, 'ct_provider_status': ct_provider_status, 'ct_cache_used': ct_cache_used, 'ct_cache_stale': ct_cache_stale, 'ct_cache_age_s': ct_cache_age_s, 'ct_quarantine_count': ct_quarantine_count, 'ct_quarantine_samples': ct_quarantine_samples or [], 'ct_planned': ct_planned, 'ct_scheduled': ct_scheduled, 'ct_provider_selected': ct_provider_selected, 'ct_request_attempted': ct_request_attempted, 'ct_request_timeout': ct_request_timeout, 'ct_raw_count': ct_raw_count, 'ct_bridge_invoked': ct_bridge_invoked, 'ct_candidates_built': ct_candidates_built, 'ct_storage_attempted': ct_storage_attempted, 'ct_storage_accepted': ct_storage_accepted, 'ct_terminal_stage': ct_terminal_stage, 'ct_prelude_missing_but_final_attempted': ct_prelude_missing_but_final_attempted, 'ct_bridge_rejections_count': ct_bridge_rejections_count, 'ct_storage_rejected': ct_storage_rejected, 'arrow_last_flush_error': arrow_last_flush_error or '', 'arrow_batch_dropped': arrow_batch_dropped, 'arrow_flush_failure_count': arrow_flush_failure_count, 'prewindup_barrier_errors': sum(prewindup_barrier_errors.values()) if isinstance(prewindup_barrier_errors, dict) else int(prewindup_barrier_errors or 0), 'return_guard_errors': sum(return_guard_errors.values()) if isinstance(return_guard_errors, dict) else int(return_guard_errors or 0), 'wayback_unchanged_rejected': wayback_unchanged_rejected, 'nonfeed_provider_failures': nonfeed_provider_failures or [], 'quality_rejection_summary_by_family': quality_rejection_summary_by_family or {}, 'duplicate_rejection_summary_by_family': duplicate_rejection_summary_by_family or {}, 'low_information_by_family': low_information_by_family or {}, 'nonfeed_candidate_ledger_summary': nonfeed_candidate_ledger_summary or {}, 'feed_dominance_budget': _feed_budget_to_dict(feed_dominance_budget), 'nonfeed_expected_lanes': nonfeed_expected_lanes or [], 'nonfeed_missing_expected_lanes': nonfeed_missing_expected_lanes or [], 'wayback_terminal_state': wayback_terminal_state, 'passive_dns_terminal_state': passive_dns_terminal_state, 'nonfeed_surface_complete': nonfeed_surface_complete, 'doh_planned': doh_planned, 'doh_scheduled': doh_scheduled, 'doh_request_attempted': doh_request_attempted, 'doh_domains_attempted': doh_domains_attempted, 'doh_raw_count': doh_raw_count, 'doh_accepted_findings': doh_accepted_findings, 'doh_terminal_stage': doh_terminal_stage, 'doh_provider_errors': list(doh_provider_errors) if doh_provider_errors else [], 'doh_cache_used': doh_cache_used, 'pivot_seed_domains': list(pivot_seed_domains) if pivot_seed_domains else [], 'pivot_seed_ips': list(pivot_seed_ips) if pivot_seed_ips else [], 'pivot_seed_urls': list(pivot_seed_urls) if pivot_seed_urls else [], 'pivot_seed_hashes': list(pivot_seed_hashes) if pivot_seed_hashes else [], 'pivot_seed_cves': list(pivot_seed_cves) if pivot_seed_cves else [], 'seed_context_available': seed_context_available, 'seed_context_propagated': seed_context_propagated, 'seed_context_skip_reason': seed_context_skip_reason, 'seed_context_source': seed_context_source, 'lanes_unlocked_by_seed_context': lanes_unlocked_by_seed_context or [], 'nonfeed_lane_eligibility': _build_nonfeed_lane_eligibility(query=query, acquisition_profile=_effective_profile, plan=plan), 'acquisition_plan_build_failed': acquisition_plan_build_failed, 'acquisition_plan_build_error_type': acquisition_plan_build_error_type, 'acquisition_plan_build_error': acquisition_plan_build_error, 'acquisition_plan_present_for_prelude': acquisition_plan_present_for_prelude, 'acquisition_plan_lanes_for_prelude': list(acquisition_plan_lanes_for_prelude) if acquisition_plan_lanes_for_prelude else [], 'acquisition_plan_enabled_lanes_for_prelude': list(acquisition_plan_enabled_lanes_for_prelude) if acquisition_plan_enabled_lanes_for_prelude else [], 'acquisition_plan_profile_for_prelude': acquisition_plan_profile_for_prelude, 'acquisition_plan_build_error_for_prelude': acquisition_plan_build_error_for_prelude, 'nonfeed_prelude_enabled': nonfeed_prelude_enabled, 'nonfeed_prelude_expected_lanes': list(nonfeed_prelude_expected_lanes) if nonfeed_prelude_expected_lanes else [], 'nonfeed_prelude_attempted_lanes': list(nonfeed_prelude_attempted_lanes) if nonfeed_prelude_attempted_lanes else [], 'nonfeed_prelude_terminal_lanes': list(nonfeed_prelude_terminal_lanes) if nonfeed_prelude_terminal_lanes else [], 'nonfeed_prelude_missing_lanes': list(nonfeed_prelude_missing_lanes) if nonfeed_prelude_missing_lanes else [], 'nonfeed_prelude_error_by_lane': nonfeed_prelude_error_by_lane or {}, 'nonfeed_prelude_accepted_by_lane': nonfeed_prelude_accepted_by_lane or {}, 'nonfeed_prelude_duration_s': nonfeed_prelude_duration_s, 'nonfeed_prelude_feed_blocked_until_complete': nonfeed_prelude_feed_blocked_until_complete, 'circuit_breakers': circuit_breakers_state or {}}
    reconcile_lane_detail_fields(result)

def is_lane_enabled(snapshot: AcquisitionStrategySnapshot, lane_name: str) -> bool:
    """
    Return True if the given lane is enabled in the acquisition plan.

    Fail-soft: returns False if snapshot is None or lane is not found.
    """
    if snapshot is None:
        return False
    for plan in snapshot.plans:
        if plan.lane == lane_name:
            return plan.enabled
    return False

def get_lane_plan(snapshot: AcquisitionStrategySnapshot, lane_name: str) -> AcquisitionLanePlan | None:
    """
    Return the AcquisitionLanePlan for the given lane, or None if not found.

    Fail-soft: returns None if snapshot is None or lane is not found.
    """
    if snapshot is None:
        return None
    for plan in snapshot.plans:
        if plan.lane == lane_name:
            return plan
    return None

def lane_skip_reason(snapshot: AcquisitionStrategySnapshot, lane_name: str) -> str | None:
    """
    Return the skip reason for the given lane, or None if lane is enabled or not found.

    Fail-soft: returns None if snapshot is None or lane is not found.
    """
    if snapshot is None:
        return None
    for plan in snapshot.plans:
        if plan.lane == lane_name:
            return None if plan.enabled else plan.reason
    return None

class SourceFamilyOutcome(msgspec.Struct, frozen=True):
    """Normalized outcome for one source family (lane) in the scheduler report.
    Migrated from @dataclass(frozen=True) → msgspec.Struct.

    F207G: Unifies CTOutcome, PassiveDNSOutcome, WaybackDiffResult, and feed
    balance telemetry into one canonical shape so diagnostics have a single
    place to explain per-family zero-yield.
    """
    family: str
    attempted: bool
    skipped: bool
    skip_reason: str | None
    raw_count: int
    built_count: int
    accepted_count: int
    error: str | None
    timeout: bool
    duration_s: float | None
    terminal_state: str = 'UNKNOWN'

    def to_dict(self) -> dict:
        return {'family': self.family, 'attempted': self.attempted, 'skipped': self.skipped, 'skip_reason': self.skip_reason, 'raw_count': self.raw_count, 'built_count': self.built_count, 'accepted_count': self.accepted_count, 'error': self.error, 'timeout': self.timeout, 'duration_s': round(self.duration_s, 3) if self.duration_s is not None else None, 'terminal_state': self.terminal_state}

def normalize_source_family_name(value: str) -> str:
    """Normalize a source family name to its canonical lowercase form.

    Maps mixed-case variants to their canonical lowercase representation so that
    "CT", "ct", "Ct" all resolve to "ct", preventing duplicate outcomes for the same
    logical family in a single acquisition report.

    Canonical families: feed, public, ct, wayback, passive_dns, academic, ipfs, pivot.
    """
    if not isinstance(value, str):
        return 'unknown'
    _v = value.strip().lower()
    _alias_map = {'ct': 'ct', 'public': 'public', 'feed': 'feed', 'wayback': 'wayback', 'passive_dns': 'passive_dns', 'academic': 'academic', 'ipfs': 'ipfs', 'pivot': 'pivot', 'blockchain': 'blockchain', 'ct_log': 'ct', 'passivedns': 'passive_dns', 'passive-dns': 'passive_dns'}
    return _alias_map.get(_v, _v)
_TERMINAL_PRIORITY = {'ATTEMPTED_ACCEPTED': 0, 'ATTEMPTED_TIMEOUT': 1, 'ATTEMPTED_ERROR': 2, 'ATTEMPTED_NO_RESULTS': 3, 'SKIPPED_BY_MEMORY': 4, 'SKIPPED_BY_POLICY': 5, 'SKIPPED': 6, 'NEVER_SCHEDULED': 7, 'UNKNOWN': 8}

def _pick_best_terminal(outcomes: list[dict]) -> str:
    """Pick the highest-priority terminal_state from a list of same-family outcomes."""
    _best_ts = 'UNKNOWN'
    _best_prio = 99
    for o in outcomes:
        ts = o.get('terminal_state', 'UNKNOWN')
        prio = _TERMINAL_PRIORITY.get(ts, 99)
        if prio < _best_prio:
            _best_prio = prio
            _best_ts = ts
    return _best_ts

def canonicalize_source_family_outcomes(outcomes: list[dict]) -> list[dict]:
    """Deduplicate and merge source family outcomes that normalize to the same family.

    When multiple outcomes normalize to the same family name (e.g., "CT" and "ct"),
    they are merged into a single outcome using the merge rules:
      - attempted = any(attempted=True)
      - skipped   = all(skipped) only if no outcome was attempted; otherwise False
      - timeout   = any(timeout=True)
      - error     = prefer real provider/runtime error over synthetic "no_candidates"
      - terminal_state = highest-priority from TERMINAL_PRIORITY table
      - raw_count / built_count / accepted_count = max of all
      - duration_s = max non-null duration
    """
    if not outcomes:
        return []
    _groups: dict[str, list[dict]] = {}
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        fam_raw = o.get('family', '')
        fam_norm = normalize_source_family_name(fam_raw)
        _groups.setdefault(fam_norm, []).append(o)
    _result: list[dict] = []
    for fam_norm, group in _groups.items():
        if len(group) == 1:
            merged = dict(group[0])
            merged['family'] = fam_norm
            _result.append(merged)
            continue
        attempted = any((o.get('attempted', False) for o in group))
        skipped = all((o.get('skipped', False) for o in group)) and (not attempted)
        timeout = any((o.get('timeout', False) for o in group))
        errors = [o.get('error') for o in group if o.get('error')]
        _real_errors = [e for e in errors if e not in ('no_candidates', 'never_scheduled', 'no_outcome_recorded')]
        error = _real_errors[0] if _real_errors else errors[0] if errors else None
        raw_count = max((o.get('raw_count', 0) or 0 for o in group))
        built_count = max((o.get('built_count', 0) or 0 for o in group))
        accepted_count = max((o.get('accepted_count', 0) or 0 for o in group))
        durations = [o.get('duration_s') for o in group if o.get('duration_s') is not None]
        duration_s = max(durations) if durations else None
        terminal_state = _pick_best_terminal(group)
        skip_reasons = list({o.get('skip_reason') for o in group if o.get('skip_reason')})
        skip_reason = skip_reasons[0] if len(skip_reasons) == 1 else None
        lane_candidates = [o.get('lane') for o in group if o.get('lane')]
        lane = lane_candidates[0] if lane_candidates else fam_norm.upper()
        _result.append({'family': fam_norm, 'attempted': attempted, 'skipped': skipped, 'skip_reason': skip_reason, 'raw_count': raw_count, 'built_count': built_count, 'accepted_count': accepted_count, 'error': error, 'timeout': timeout, 'duration_s': duration_s, 'terminal_state': terminal_state, 'lane': lane})
    return _result

def normalize_source_family_outcome(family: str, raw: dict) -> dict:
    """Normalize a raw lane or adapter outcome dict into SourceFamilyOutcome fields.

    Handles three F207F shapes:
    - AcquisitionLaneOutcome  (ct, wayback, passive_dns, blockchain lanes)
    - dict with ct_results_raw / produced_items / accepted_findings keys
    - Feed balance tuple (verdict_tag, signal, fallback_use, fallback_waste, quality)
      which maps to family=FEED, attempted=True, raw_count=signal

    Also handles the "missing family" case where no outcome was produced at all,
    returning a skipped/attempted=False outcome for documentation purposes.
    """
    _canonical_family = normalize_source_family_name(family)

    def _derive_terminal(ts_raw: str | None, attempted: bool, skipped: bool, skip_reason: str | None, error: str | None, timeout: bool, accepted_count: int) -> str:
        if ts_raw:
            _l = ts_raw.lower()
            if _l in ('attempted_empty', 'no_candidates', 'no_terminal', 'terminal_no_results'):
                return 'ATTEMPTED_NO_RESULTS'
            if _l in ('provider_error', 'dependency_missing', 'error', 'provider_cooldown', 'provider_unavailable'):
                return 'ATTEMPTED_ERROR'
            if _l in ('timeout', 'request_timeout'):
                return 'ATTEMPTED_TIMEOUT'
            if _l in ('attempted_accepted', 'accepted', 'storage_accepted'):
                return 'ATTEMPTED_ACCEPTED'
            if _l == 'skipped' or 'skipped' in _l:
                return 'SKIPPED'
            return ts_raw
        if skip_reason in ('never_scheduled', 'no_outcome_recorded'):
            return 'NEVER_SCHEDULED'
        if not attempted:
            if skip_reason and ('memory' in skip_reason.lower() or 'hw_skip' in skip_reason.lower() or 'hardware' in skip_reason.lower()):
                return 'SKIPPED_BY_MEMORY'
            if skip_reason and ('policy' in skip_reason.lower() or 'disabled' in skip_reason.lower() or 'not_enabled' in skip_reason.lower()):
                return 'SKIPPED_BY_POLICY'
            return 'SKIPPED'
        if timeout:
            return 'ATTEMPTED_TIMEOUT'
        if error == 'timeout':
            return 'ATTEMPTED_TIMEOUT'
        if error:
            return 'ATTEMPTED_ERROR'
        if accepted_count > 0:
            return 'ATTEMPTED_ACCEPTED'
        return 'ATTEMPTED_NO_RESULTS'
    if raw is None:
        _ts = _derive_terminal(None, False, True, 'no_outcome_recorded', None, False, 0)
        return SourceFamilyOutcome(family=_canonical_family, attempted=False, skipped=True, skip_reason='no_outcome_recorded', raw_count=0, built_count=0, accepted_count=0, error=None, timeout=False, duration_s=None, terminal_state=_ts).to_dict()
    _to_dict = getattr(raw, 'to_dict', None)
    if _to_dict is not None:
        raw = _to_dict()
    if isinstance(raw, (list, tuple)) and (not isinstance(raw, dict)):
        _verdict = raw if isinstance(raw, tuple) else raw[0]
        if len(_verdict) >= 5 and isinstance(_verdict[1], int):
            _tag, _sig, _fb_use, _fb_waste, _qual = _verdict[:5]
            _ts = _derive_terminal(None, True, False, None, None, False, 0)
            return SourceFamilyOutcome(family=_canonical_family, attempted=True, skipped=False, skip_reason=None, raw_count=_sig, built_count=0, accepted_count=0, error=None, timeout=False, duration_s=None, terminal_state=_ts).to_dict()
    _d: Any = raw
    attempted = _d.get('attempted', False)
    skip_reason = _d.get('skip_reason') if not attempted else None
    skipped = _d.get('skipped', not attempted)
    _error = _d.get('error')
    _timeout = _d.get('timeout', False)
    _accepted = _d.get('accepted_count', _d.get('accepted_findings', 0))
    _ts_raw = _d.get('terminal_state')
    built_count = _d.get('built_count', _d.get('produced_items', _d.get('ct_results_raw', 0)))
    raw_count = _d.get('raw_count', _d.get('ct_results_raw', 0))
    accepted_count = _d.get('accepted_count', _d.get('accepted_findings', 0))
    _ts = _derive_terminal(_ts_raw, attempted, skipped, skip_reason, _error, _timeout, accepted_count)
    return SourceFamilyOutcome(family=_canonical_family, attempted=attempted, skipped=skipped, skip_reason=skip_reason, raw_count=raw_count, built_count=built_count, accepted_count=accepted_count, error=_error, timeout=_timeout, duration_s=_d.get('duration_s'), terminal_state=_ts).to_dict()

class AcquisitionLaneOutcome(msgspec.Struct, frozen=True):
    """Acquisition lane outcome DTO. Migrated from @dataclass(frozen=True) → msgspec.Struct."""
    lane: str
    enabled: bool
    attempted: bool
    accepted_findings: int = 0
    produced_items: int = 0
    timeout: bool = False
    error: str | None = None
    duration_s: float = 0.0
    source_family: str = 'unknown'
    ct_query: str = ''
    ct_results_raw: int = 0
    candidate_findings: tuple = ()
    rejection_reasons: tuple = ()
    rejected_count: int = 0
    sample_rejections: tuple = ()
    wayback_raw_count: int = 0
    passive_dns_raw_count: int = 0
    doh_query: str = ''
    wayback_query: str = ''
    passive_dns_query: str = ''
    ipfs_cid_count: int = 0
    ipfs_terminal_state: str = 'none'
    ct_candidates_built: int = 0

    def to_dict(self) -> dict:
        return {'lane': self.lane, 'enabled': self.enabled, 'attempted': self.attempted, 'accepted_findings': self.accepted_findings, 'produced_items': self.produced_items, 'timeout': self.timeout, 'error': self.error, 'duration_s': round(self.duration_s, 3), 'source_family': self.source_family, 'ct_query': self.ct_query, 'ct_results_raw': self.ct_results_raw, 'rejected_count': self.rejected_count, 'sample_rejections': list(self.sample_rejections), 'wayback_raw_count': self.wayback_raw_count, 'passive_dns_raw_count': self.passive_dns_raw_count, 'doh_query': self.doh_query, 'wayback_query': self.wayback_query, 'passive_dns_query': self.passive_dns_query, 'ipfs_cid_count': self.ipfs_cid_count, 'ipfs_terminal_state': self.ipfs_terminal_state, 'ct_candidates_built': self.ct_candidates_built}
_NONFEED_LANE_FAMILY_MAP = {'PUBLIC': AcquisitionLane.PUBLIC, 'CT': AcquisitionLane.CT, 'PIVOT_EXECUTOR': AcquisitionLane.PIVOT_EXECUTOR, 'WAYBACK': AcquisitionLane.WAYBACK, 'PASSIVE_DNS': AcquisitionLane.PASSIVE_DNS}
_ACCEPTED_TERMINAL_STATES = frozenset(['success', 'success_empty', 'empty'])

class NonfeedMissionSnapshot(msgspec.Struct):
    """F217B: Snapshot of nonfeed mission controller state at a point in time.

    This is a plain msgspec.Struct (mutable) so that the scheduler can
    accumulate state over the sprint lifetime.
    """
    mission_active: bool = False
    acquisition_profile: str = 'default'
    required_families: tuple[str, ...] = ()
    optional_families: tuple[str, ...] = ()
    family_status: dict[str, str] = field(default_factory=dict)
    all_required_terminal: bool = False
    any_accepted: bool = False
    provider_failures: tuple[str, ...] = ()
    memory_skips: tuple[str, ...] = ()
    mission_exit_reason: str = ''

    def to_dict(self) -> dict:
        return {'nonfeed_mission_active': self.mission_active, 'nonfeed_acquisition_profile': self.acquisition_profile, 'nonfeed_required_families': list(self.required_families), 'nonfeed_optional_families': list(self.optional_families), 'nonfeed_family_status': dict(self.family_status), 'nonfeed_all_required_terminal': self.all_required_terminal, 'nonfeed_any_accepted': self.any_accepted, 'nonfeed_provider_failures': list(self.provider_failures), 'nonfeed_memory_skips': list(self.memory_skips), 'nonfeed_mission_exit_reason': self.mission_exit_reason}

class NonfeedMissionExitReason:
    """F217B: Canonical mission exit reason values."""
    MISSION_NOT_FINISHED = ''
    DIAGNOSTIC_COMPLETE_NONFEED_ACCEPTED = 'diagnostic_complete_nonfeed_accepted'
    DIAGNOSTIC_COMPLETE_NO_NONFEED_ACCEPTED = 'diagnostic_complete_no_nonfeed_accepted'
    DIAGNOSTIC_BLOCKED_BY_MEMORY = 'diagnostic_blocked_by_memory'
    MISSION_INCOMPLETE = 'mission_incomplete'

class NonfeedMissionController:
    """F217B: Canonical nonfeed mission contract for nonfeed_diagnostic profile.

    Coordinates lane family expectations without benchmark-owned logic.
    For acquisition_profile=nonfeed_diagnostic:
      - Required lane families: PUBLIC, CT, PIVOT_EXECUTOR
      - Optional lane families: WAYBACK, PASSIVE_DNS
      - FEED is capped until required nonfeed lanes are terminal
      - Mission finishes only when each required family has:
          accepted evidence
          OR explicit terminal state
          OR explicit provider failure
          OR explicit memory skip

    IMPORTANT — what does NOT count as accepted evidence:
      - CT quarantine is NOT accepted evidence (raw hits rejected by bridge criteria)
      - Quality rejection ledger is NOT accepted evidence (quality gate rejection)
      - PUBLIC explicit failure (FETCH_ZERO_SUCCESS, QUALITY_REJECTED, etc.) counts
        as terminal but NOT accepted
      - Feed findings do NOT satisfy nonfeed mission
    """
    __slots__ = ()

    @staticmethod
    def is_mission_profile(acquisition_profile: str | None) -> bool:
        """Return True when the profile is any nonfeed_diagnostic variant."""
        if acquisition_profile is None:
            return False
        return acquisition_profile.startswith('nonfeed_diagnostic')

    @staticmethod
    def get_required_families() -> tuple[str, ...]:
        """Required lane families for nonfeed_diagnostic mission."""
        return ('PUBLIC', 'CT', 'PIVOT_EXECUTOR')

    @staticmethod
    def get_optional_families() -> tuple[str, ...]:
        """Optional lane families for nonfeed_diagnostic mission."""
        return ('WAYBACK', 'PASSIVE_DNS')

    @staticmethod
    def _family_to_lane(family: str) -> str:
        """Map lane family string to AcquisitionLane constant."""
        return _NONFEED_LANE_FAMILY_MAP.get(family, family)

    @staticmethod
    def _get_lane_outcome(family: str, acquisition_lane_outcomes: tuple, public_outcome: dict | None, ct_quarantine_count: int, quality_rejection_ledger: tuple) -> dict | None:
        """Get the outcome dict for a lane family.

        Returns a dict with keys: accepted_findings, terminal_state, error, skipped
        suitable for mission evaluation.

        Args:
            family: Lane family string (PUBLIC, CT, etc.)
            acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes
            public_outcome: _public_outcome dict from SprintScheduler (for PUBLIC lane)
            ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult
            quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult
        """
        if family == 'PUBLIC':
            if public_outcome is None:
                return None
            accepted = public_outcome.get('accepted_count', 0) or 0
            terminal_state = normalize_terminal_state(public_outcome)
            return {'accepted_findings': accepted, 'terminal_state': terminal_state, 'error': public_outcome.get('error'), 'skipped': public_outcome.get('skipped', False)}
        elif family == 'CT':
            lane = AcquisitionLane.CT
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, 'lane') and outcome.lane == lane:
                    return {'accepted_findings': outcome.accepted_findings, 'terminal_state': normalize_terminal_state(outcome.to_dict()), 'error': outcome.error, 'skipped': False}
            return None
        elif family == 'PIVOT_EXECUTOR':
            lane = AcquisitionLane.PIVOT_EXECUTOR
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, 'lane') and outcome.lane == lane:
                    return {'accepted_findings': outcome.accepted_findings, 'terminal_state': normalize_terminal_state(outcome.to_dict()), 'error': outcome.error, 'skipped': False}
            return None
        elif family == 'WAYBACK':
            lane = AcquisitionLane.WAYBACK
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, 'lane') and outcome.lane == lane:
                    return {'accepted_findings': outcome.accepted_findings, 'terminal_state': normalize_terminal_state(outcome.to_dict()), 'error': outcome.error, 'skipped': False}
            return None
        elif family == 'PASSIVE_DNS':
            lane = AcquisitionLane.PASSIVE_DNS
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, 'lane') and outcome.lane == lane:
                    return {'accepted_findings': outcome.accepted_findings, 'terminal_state': normalize_terminal_state(outcome.to_dict()), 'error': outcome.error, 'skipped': False}
            return None
        return None

    @staticmethod
    def _evaluate_family_status(outcome: dict | None, memory_skipped: bool=False) -> str:
        """Evaluate the mission status of a single family.

        Returns one of: accepted, terminal, provider_failure, memory_skip, pending, missing
        """
        if memory_skipped:
            return 'memory_skip'
        if outcome is None:
            return 'missing'
        accepted = outcome.get('accepted_findings', 0) or 0
        if accepted > 0:
            return 'accepted'
        terminal_state = outcome.get('terminal_state', '')
        error = outcome.get('error', '')
        skipped = outcome.get('skipped', False)
        if error and any((err in str(error).lower() for err in ['timeout', 'error', 'unavailable', 'connection', 'refused', 'dns'])):
            if any((err in str(error).lower() for err in ['timeout', 'unavailable', 'connection', 'refused', 'dns', 'network'])):
                return 'provider_failure'
            return 'terminal'
        if skipped:
            return 'terminal'
        if terminal_state in _ACCEPTED_TERMINAL_STATES:
            return 'terminal'
        if terminal_state:
            return 'terminal'
        return 'pending'

    @classmethod
    def build_snapshot(cls, acquisition_profile: str, acquisition_lane_outcomes: tuple, public_outcome: dict | None, ct_quarantine_count: int, quality_rejection_ledger: tuple, memory_skipped_families: tuple[str, ...]=()) -> NonfeedMissionSnapshot:
        """Build a NonfeedMissionSnapshot from current scheduler state.

        Args:
            acquisition_profile: Current acquisition profile name
            acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes
            public_outcome: _public_outcome dict from SprintScheduler (None if PUBLIC never ran)
            ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult
            quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult
            memory_skipped_families: Families skipped due to memory pressure
        """
        snapshot = NonfeedMissionSnapshot()
        snapshot.acquisition_profile = acquisition_profile
        snapshot.mission_active = cls.is_mission_profile(acquisition_profile)
        if not snapshot.mission_active:
            return snapshot
        snapshot.required_families = cls.get_required_families()
        snapshot.optional_families = cls.get_optional_families()
        snapshot.memory_skips = tuple(memory_skipped_families)
        all_statuses: list[str] = []
        accepted_families: list[str] = []
        provider_failure_families: list[str] = []
        for family in snapshot.required_families:
            memory_skip = family in memory_skipped_families
            outcome = cls._get_lane_outcome(family, acquisition_lane_outcomes, public_outcome, ct_quarantine_count, quality_rejection_ledger)
            status = cls._evaluate_family_status(outcome, memory_skipped=memory_skip)
            snapshot.family_status[family] = status
            all_statuses.append(status)
            if status == 'accepted':
                accepted_families.append(family)
            elif status == 'provider_failure':
                provider_failure_families.append(family)
            elif status == 'memory_skip':
                pass
            elif status == 'missing':
                pass
        for family in snapshot.optional_families:
            memory_skip = family in memory_skipped_families
            outcome = cls._get_lane_outcome(family, acquisition_lane_outcomes, public_outcome, ct_quarantine_count, quality_rejection_ledger)
            status = cls._evaluate_family_status(outcome, memory_skipped=memory_skip)
            snapshot.family_status[family] = status
            all_statuses.append(status)
            if status == 'accepted':
                accepted_families.append(family)
            elif status == 'provider_failure':
                provider_failure_families.append(family)
        snapshot.any_accepted = len(accepted_families) > 0
        snapshot.provider_failures = tuple(provider_failure_families)
        terminal_statuses = {'accepted', 'terminal', 'provider_failure', 'memory_skip'}
        snapshot.all_required_terminal = all((snapshot.family_status.get(f, 'missing') in terminal_statuses for f in snapshot.required_families))
        snapshot.mission_exit_reason = cls._derive_exit_reason(snapshot, memory_skipped_families)
        return snapshot

    @classmethod
    def _derive_exit_reason(cls, snapshot: NonfeedMissionSnapshot, memory_skipped_families: tuple[str, ...]) -> str:
        """Derive the canonical mission exit reason."""
        if not snapshot.mission_active:
            return ''
        if snapshot.any_accepted:
            return NonfeedMissionExitReason.DIAGNOSTIC_COMPLETE_NONFEED_ACCEPTED
        if memory_skipped_families:
            required_set = set(snapshot.required_families)
            skipped_set = set(memory_skipped_families)
            if skipped_set.issuperset(required_set) or all((snapshot.family_status.get(f, 'missing') == 'memory_skip' for f in snapshot.required_families)):
                return NonfeedMissionExitReason.DIAGNOSTIC_BLOCKED_BY_MEMORY
        if snapshot.all_required_terminal:
            return NonfeedMissionExitReason.DIAGNOSTIC_COMPLETE_NO_NONFEED_ACCEPTED
        return NonfeedMissionExitReason.MISSION_INCOMPLETE
_DOMAIN_OR_IP_RE = re.compile('(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\\.)+[a-zA-Z]{2,}|\\d{1,3}(?:\\.\\d{1,3}){3}')
_URL_RE = re.compile('(?:https?://|[a-zA-Z][a-zA-Z0-9+.-]*://)')
_WALLET_RE = re.compile('(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}|0x[a-fA-F0-9]{40}|L[a-zA-HJ-NP-Z0-9]{32,34}|4[0-9AB][1-9A-HJ-NP-Za-km-z]{92}|X[1-9A-HJ-NP-Za-km-z]{95}|ripple:rvr?[a-zA-HJ-NP-Z0-9]{24,}|dust:qty[0-9a-f]{40}|')
_CRYPTO_HASH_RE = re.compile('\\b[0-9a-fA-F]{64}\\b|\\b[0-9a-fA-F]{80}\\b|\\b[0-9a-fA-F]{16}\\b')
_CVE_RE = re.compile('\\bCVE-\\d{4}-\\d{4,}\\b', re.IGNORECASE)

class MissionIntent:
    """F225A: Lightweight mission intent classification.

    Additive telemetry — does NOT change lane enable/disable logic.
    Does NOT bypass UMA/hardware safety, enable stealth/browser,
    or increase network aggressiveness.
    """
    DOMAIN_RECON: str = 'domain_recon'
    ORG_RECON: str = 'org_recon'
    PERSON_RECON: str = 'person_recon'
    WALLET_RECON: str = 'wallet_recon'
    CVE_RECON: str = 'cve_recon'
    INFRA_RECON: str = 'infra_recon'
    UNKNOWN: str = 'unknown'

class MissionTargetKind:
    """F225A: Target kind derived from query analysis."""
    DOMAIN: str = 'domain'
    URL: str = 'url'
    EMAIL: str = 'email'
    WALLET: str = 'wallet'
    CVE: str = 'cve'
    IP: str = 'ip'
    ORG: str = 'org'
    UNKNOWN: str = 'unknown'
_SAFE_LANES: tuple[str, ...] = (AcquisitionLane.PUBLIC, AcquisitionLane.CT, AcquisitionLane.PIVOT_EXECUTOR)
_SAFE_OPTIONAL: tuple[str, ...] = (AcquisitionLane.WAYBACK, AcquisitionLane.PASSIVE_DNS)

def infer_mission_intent(query: str) -> str:
    """F225A: Infer mission intent from query string.

    Rules:
      - CVE-* pattern          → cve_recon
      - crypto wallet/hash     → wallet_recon
      - email-like indicator   → person_recon
      - domain/IP/URL         → domain_recon / infra_recon
      - otherwise             → unknown (safe lanes only)

    Returns a string constant from MissionIntent.
    No network I/O, no model load. Deterministic.
    """
    if _CVE_RE.search(query):
        return MissionIntent.CVE_RECON
    if _has_crypto_indicator(query):
        return MissionIntent.WALLET_RECON
    if re.match('\\d{1,3}(?:\\.\\d{1,3}){3}$', query.strip()):
        return MissionIntent.INFRA_RECON
    if re.search('[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}', query):
        return MissionIntent.PERSON_RECON
    if _URL_RE.search(query):
        return MissionIntent.INFRA_RECON
    if _has_domain_or_ip(query):
        return MissionIntent.DOMAIN_RECON
    return MissionIntent.UNKNOWN
_MISSION_TARGET_KIND: dict[str, str] = {MissionIntent.DOMAIN_RECON: MissionTargetKind.DOMAIN, MissionIntent.ORG_RECON: MissionTargetKind.ORG, MissionIntent.PERSON_RECON: MissionTargetKind.EMAIL, MissionIntent.WALLET_RECON: MissionTargetKind.WALLET, MissionIntent.CVE_RECON: MissionTargetKind.CVE, MissionIntent.INFRA_RECON: MissionTargetKind.IP}

def _mission_target_kind(intent: str) -> str:
    """F225A: Derive target kind from mission intent."""
    return _MISSION_TARGET_KIND.get(intent, MissionTargetKind.UNKNOWN)

def _mission_lanes(intent: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """F225A: Derive required and optional lanes from mission intent.

    Returns (required_lanes, optional_lanes).
    Lane priority/reason adjustments only — all safety gates preserved.
    """
    if intent == MissionIntent.WALLET_RECON:
        return ((AcquisitionLane.PUBLIC, AcquisitionLane.PIVOT_EXECUTOR), (AcquisitionLane.BLOCKCHAIN, AcquisitionLane.CT))
    if intent == MissionIntent.CVE_RECON:
        return ((AcquisitionLane.PUBLIC, AcquisitionLane.CT, AcquisitionLane.PIVOT_EXECUTOR), (AcquisitionLane.WAYBACK, AcquisitionLane.PASSIVE_DNS))
    if intent == MissionIntent.DOMAIN_RECON:
        return ((AcquisitionLane.PUBLIC, AcquisitionLane.CT, AcquisitionLane.PIVOT_EXECUTOR), (AcquisitionLane.WAYBACK, AcquisitionLane.PASSIVE_DNS))
    if intent == MissionIntent.INFRA_RECON:
        return ((AcquisitionLane.PUBLIC, AcquisitionLane.CT, AcquisitionLane.PIVOT_EXECUTOR), (AcquisitionLane.PASSIVE_DNS, AcquisitionLane.WAYBACK))
    return ((), ())

def _should_enable_bootstrap(query: str, acquisition_profile: str, has_domain: bool) -> bool:
    """P0-3: Enable bootstrap for threat queries even without domain.

    Enables rescue URLs (CISA KEV, NVD, Shodan, Exploit-DB) for:
      - Threat indicator queries (ransomware, malware, C2, botnet, APT...)
      - CVE patterns (CVE-YYYY-NNNNN)
      - Bare IP addresses
      - nonfeed_diagnostic profile

    This mirrors the F221A threat-query logic in required_terminal_lanes()
    but surfaces the decision as a boolean flag stored in AcquisitionStrategySnapshot
    so the scheduler can propagate it to LivePublicPipeline.run(public_bootstrap_enabled).

    Returns:
        True when bootstrap should be enabled for the query.
        False when domain bootstrap handles it or profile opts out.
    """
    _is_threat = _has_threat_indicator(query)
    _is_nonfeed = acquisition_profile == AcquisitionProfile.NONFEED_DIAGNOSTIC
    return has_domain or _is_threat or _is_nonfeed
    if intent == MissionIntent.PERSON_RECON:
        return ((AcquisitionLane.PUBLIC, AcquisitionLane.PIVOT_EXECUTOR), (AcquisitionLane.CT, AcquisitionLane.PASSIVE_DNS))
    return (_SAFE_LANES, _SAFE_OPTIONAL)

def _has_domain_or_ip(query: str) -> bool:
    return bool(_DOMAIN_OR_IP_RE.search(query))

def _has_url(query: str) -> bool:
    return bool(_URL_RE.search(query)) or _has_domain_or_ip(query)

def _has_crypto_wallet(query: str) -> bool:
    m = _WALLET_RE.search(query)
    return bool(m) and len(m.group()) > 0

def _has_crypto_hash(query: str) -> bool:
    return bool(_CRYPTO_HASH_RE.search(query))

def _has_crypto_indicator(query: str) -> bool:
    return _has_crypto_wallet(query) or _has_crypto_hash(query)
_THREAT_INDICATOR_RE = re.compile('(ransomware|malware|c2|command[- ]and[- ]control|botnet|trojan|keylogger|spyware|adware|loader|dropper|payload|c2[._-]?(?:server|panel|callback)|darkweb|tor-hidden|onion[-\\s]service|illicit[.]exchange|breach[- ]forum|stolen[- ]?credential|stolen credentials|leaked[- ]data|exploit[- ]kit|packer|obfuscator|infostealer|stealer|keylog|rootkit|backdoor|rat[- ](?:server|client)|apt\\d*[- ](?:group|team|campaign|actor)|apt29|nation[- ]state|advanced[- ]persistent)', re.IGNORECASE)

def _has_threat_indicator(query: str) -> bool:
    """Return True if query contains threat/crime indicators suggesting active investigation.

    Used by required_terminal_lanes() to ensure PUBLIC lane is mandatory for threat
    queries even when no domain/IP is present — PUBLIC can discover infrastructure
    from text search results alone (no seeds required).
    """
    return bool(_THREAT_INDICATOR_RE.search(query))

def _base_concurrency(uma_state: str, swap_detected: bool) -> int:
    """Return base concurrency based on hardware state."""
    if swap_detected or uma_state == 'emergency':
        return 1
    if uma_state == 'critical':
        return 2
    if uma_state == 'warn':
        return 3
    return 5

def _lane_concurrency(lane: str, base: int, uma_state: str) -> int:
    """Apply lane-specific adjustments on top of base concurrency."""
    if uma_state in ('critical', 'emergency'):
        if lane in (AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN, AcquisitionLane.STEALTH):
            return max(1, base // 2)
    if uma_state == 'warn':
        if lane in (AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN):
            return max(1, base - 1)
    return base

def build_acquisition_plan(query: str, duration_s: float, aggressive_mode: bool, uma_state: str, swap_detected: bool, accepted_findings_so_far: int=0, branch_timeout_count: int=0, transport_authority_status: dict | None=None, stealth_phase: dict | None=None, acquisition_profile: str='default', source_quality_weights: dict | None=None, rl_lane_combo: frozenset[str] | None=None, feed_domain_seeds: tuple[str, ...]=(), synthetic_domains: tuple[str, ...]=()) -> AcquisitionStrategySnapshot:
    """
    Build an acquisition strategy snapshot for the given sprint context.

    Args:
        query:              The sprint query string.
        duration_s:         Sprint duration in seconds.
        aggressive_mode:    True if running in aggressive (parallel) mode.
        uma_state:          Current UMA state string ("ok", "warn", "critical", "emergency").
        swap_detected:      True if system swap has been detected.
        accepted_findings_so_far: Number of accepted findings collected so far.
        branch_timeout_count:    Number of branch timeouts in current sprint.
        transport_authority_status: Optional dict with transport authority signals.
            Supported keys:
              - "degraded": bool — True if transport is degraded
              - "stealth_phase": int — current stealth phase (1-4)
        stealth_phase:      Optional dict with stealth phase info.
            Supported keys:
              - "phase": int — current stealth phase
              - "breaker_seam_ready": bool — True when phase >= 3
        acquisition_profile: F216B: Runtime profile controlling lane caps.
            "default" = standard behavior.
            "nonfeed_diagnostic" = caps FEED at 25, enables nonfeed lanes for domain queries.
            Falls back to HLEDAC_ACQUISITION_PROFILE env var if not explicitly passed.
        rl_lane_combo: F265LANE: Optional frozenset of lane names (e.g. {"CT","WAYBACK"})
            from RL policy action. When set, overrides lane enabled/disabled decisions
            to match the RL-chosen combination.
        feed_domain_seeds: P0-8: Domain seeds extracted from accepted feed findings.
            When query has no domain indicator but feed findings contain domains,
            these seeds enable CT/DOH/WAYBACK lanes mid-sprint.

    Returns:
        AcquisitionStrategySnapshot with per-lane plans.

    GHOST_INVARIANTS:
      - No network I/O
      - No model/MLX load
      - No asyncio.run() / loop.run_until_complete()
      - Bounded: max 12 lane plans (all canonical acquisition lanes)
      - Fail-soft: on any error returns minimal snapshot with all lanes disabled
    """
    _input_profile = acquisition_profile
    if acquisition_profile == 'nonfeed_diagnostic180':
        acquisition_profile = 'nonfeed_diagnostic'
    if acquisition_profile == 'default':
        import os
        _env_profile = os.environ.get('HLEDAC_ACQUISITION_PROFILE', None)
        if _env_profile is not None:
            logger.info("[F228B] acquisition_profile overridden by env var HLEDAC_ACQUISITION_PROFILE: 'default' → %r", _env_profile)
            acquisition_profile = _env_profile
    feed_budget = _load_feed_budget_from_env() if acquisition_profile != 'default' else FeedDominanceBudget()
    _has_domain = _has_domain_or_ip(query)
    _bootstrap_enabled = _should_enable_bootstrap(query, acquisition_profile, _has_domain)
    try:
        return _build_plan_impl(query=query, duration_s=duration_s, aggressive_mode=aggressive_mode, uma_state=uma_state, swap_detected=swap_detected, accepted_findings_so_far=accepted_findings_so_far, branch_timeout_count=branch_timeout_count, transport_authority_status=transport_authority_status, stealth_phase=stealth_phase, acquisition_profile=acquisition_profile, feed_budget=feed_budget, rl_lane_combo=rl_lane_combo, feed_domain_seeds=feed_domain_seeds, synthetic_domains=synthetic_domains, bootstrap_enabled=_bootstrap_enabled)
    except Exception:
        return AcquisitionStrategySnapshot(query=query, duration_s=duration_s, aggressive_mode=aggressive_mode, uma_state=uma_state, swap_detected=swap_detected, accepted_findings_so_far=accepted_findings_so_far, branch_timeout_count=branch_timeout_count, feed_dominance_budget=feed_budget, nonfeed_plan_debug=None, plans=(), bootstrap_enabled=_bootstrap_enabled)

def _build_plan_impl(query: str, duration_s: float, aggressive_mode: bool, uma_state: str, swap_detected: bool, accepted_findings_so_far: int, branch_timeout_count: int, transport_authority_status: dict | None, stealth_phase: dict | None, acquisition_profile: str='default', feed_budget: FeedDominanceBudget=FeedDominanceBudget(), rl_lane_combo: frozenset[str] | None=None, feed_domain_seeds: tuple[str, ...]=(), synthetic_domains: tuple[str, ...]=(), bootstrap_enabled: bool=False) -> AcquisitionStrategySnapshot:
    """Internal implementation — raises on error (caller catches)."""
    hardware_critical = uma_state in ('critical', 'emergency')
    has_domain = _has_domain_or_ip(query)
    has_ip = bool(_DOMAIN_OR_IP_RE.search(query) and re.search('\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b', query))
    has_url = _has_url(query)
    has_crypto = _has_crypto_indicator(query)
    has_long_duration = duration_s >= 300.0
    is_nonfeed_diagnostic = acquisition_profile == AcquisitionProfile.NONFEED_DIAGNOSTIC
    is_deep_osint_m1 = is_deep_osint_m1_profile(acquisition_profile)
    _feed_domain_candidates: tuple[str, ...] = ()
    _feed_domain_candidates_count = 0
    if not has_domain and (accepted_findings_so_far > 0 or feed_domain_seeds or synthetic_domains):
        if feed_domain_seeds:
            _feed_domain_candidates = feed_domain_seeds[:10]
            _feed_domain_candidates_count = len(_feed_domain_candidates)
            has_domain = True
        elif synthetic_domains:
            _feed_domain_candidates = synthetic_domains[:10]
            _feed_domain_candidates_count = len(_feed_domain_candidates)
            has_domain = True
        elif accepted_findings_so_far > 0:
            _candidates = extract_domain_candidates_from_text(query)
            if _candidates:
                _feed_domains = tuple((c.domain for c in _candidates[:10]))
                _feed_domain_candidates = _feed_domains
                _feed_domain_candidates_count = len(_feed_domains)
                has_domain = True
    elif not has_domain:
        _has_explicit_ioc_intent = _has_threat_indicator(query) or _has_crypto_indicator(query)
        if _has_explicit_ioc_intent:
            _keyword_expansion = _expand_keyword_query(query)
            if _keyword_expansion:
                _feed_domain_candidates = tuple(_keyword_expansion[:5])
                _feed_domain_candidates_count = len(_feed_domain_candidates)
                has_domain = True
    transport_degraded = False
    stealth_phase_num = 0
    stealth_breaker_ready = False
    if transport_authority_status:
        transport_degraded = bool(transport_authority_status.get('degraded', False))
    if stealth_phase:
        stealth_phase_num = int(stealth_phase.get('phase', 0))
        stealth_breaker_ready = bool(stealth_phase.get('breaker_seam_ready', False))
    stealth_ready = stealth_breaker_ready or stealth_phase_num >= 3
    base_conc = _base_concurrency(uma_state, swap_detected)
    if is_nonfeed_diagnostic:
        _feed_max = 25
        _feed_cap_r = 'nonfeed_diagnostic_profile_capped_25'
    else:
        _feed_max = 50
        _feed_cap_r = None
    ctx = AcquisitionContext(query=query, duration_s=duration_s, aggressive_mode=aggressive_mode, uma_state=uma_state, swap_detected=swap_detected, hardware_critical=hardware_critical, has_domain=has_domain, has_url=has_url, has_crypto=has_crypto, has_long_duration=has_long_duration, is_nonfeed_diagnostic=is_nonfeed_diagnostic, transport_degraded=transport_degraded, stealth_ready=stealth_ready, base_concurrency=base_conc, is_academic=is_academic_profile(acquisition_profile), is_deep_osint_m1=is_deep_osint_m1, has_ip=has_ip, cid_present=_has_explicit_cid(query.strip()), _feed_max_items=_feed_max, _feed_cap_reason=_feed_cap_r)
    plans: list[AcquisitionLanePlan] = []
    for rule in LANE_RULES:
        enabled = rule.enabled(ctx)
        plans.append(AcquisitionLanePlan(lane=rule.lane, enabled=enabled, reason=rule.reason(ctx) if enabled else _disabled_reason(rule.lane, ctx), max_items=rule.spec.max_items if not (rule.lane == AcquisitionLane.FEED and is_nonfeed_diagnostic) else 25, timeout_s=rule.spec.timeout_s, concurrency=rule.concurrency(ctx), risk_level=rule.spec.risk_level))
    if rl_lane_combo is not None:
        _rl_lanes = frozenset(rl_lane_combo)
        _protected = frozenset([AcquisitionLane.FEED, AcquisitionLane.PUBLIC, AcquisitionLane.STEALTH, AcquisitionLane.ACADEMIC])
        plans = [AcquisitionLanePlan(lane=p.lane, enabled=p.lane in _rl_lanes, reason=f'rl_override:{p.lane}' if p.lane in _rl_lanes else f'rl_disabled:{p.lane}', max_items=p.max_items, timeout_s=p.timeout_s, concurrency=p.concurrency, risk_level=p.risk_level) if p.lane not in _protected else p for p in plans]
    _NONFEED_LANES = (AcquisitionLane.CT, AcquisitionLane.WAYBACK, AcquisitionLane.PASSIVE_DNS, AcquisitionLane.DOH, AcquisitionLane.BLOCKCHAIN, AcquisitionLane.IPFS, AcquisitionLane.OPEN_SOURCE)
    _hardware_blocked = {AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN} if hardware_critical else set()
    _enabled_nonfeed = []
    _disabled_nonfeed = []
    _disabled_reasons = []
    _scheduled_nonfeed = []
    _hardware_skipped = []
    _intent = infer_mission_intent(query)
    _target_kind = _mission_target_kind(_intent)
    _required_lanes, _optional_lanes = _mission_lanes(_intent)
    _intent_reason = f'intent:{_intent}'
    for _plan in plans:
        if _plan.lane not in _NONFEED_LANES:
            continue
        if _plan.enabled:
            _enabled_nonfeed.append(_plan.lane)
            if _plan.lane not in _hardware_blocked:
                _scheduled_nonfeed.append(_plan.lane)
            else:
                _hardware_skipped.append(_plan.lane)
        else:
            _disabled_nonfeed.append(_plan.lane)
            _disabled_reasons.append(_plan.reason)
    _nonfeed_debug = NonfeedPlanDebug(domain_detected=ctx.has_domain, wallet_detected=ctx.has_crypto, enabled_nonfeed_lanes=tuple(_enabled_nonfeed), disabled_nonfeed_lanes=tuple(_disabled_nonfeed), disabled_reasons=tuple(_disabled_reasons), scheduled_nonfeed_lanes=tuple(_scheduled_nonfeed), hardware_skipped_lanes=tuple(_hardware_skipped), nonfeed_execution_scheduled=bool(_scheduled_nonfeed), nonfeed_execution_skip_reason='hardware_critical' if ctx.hardware_critical and _hardware_skipped else None, acquisition_profile=acquisition_profile, feed_cap_reason=ctx._feed_cap_reason, nonfeed_priority_enabled=ctx.is_nonfeed_diagnostic, nonfeed_profile_expected_lanes=(AcquisitionLane.CT, AcquisitionLane.WAYBACK, AcquisitionLane.PASSIVE_DNS, AcquisitionLane.PIVOT_EXECUTOR, AcquisitionLane.DOH) if is_nonfeed_diagnostic or is_deep_osint_m1 else _required_lanes if _intent not in (MissionIntent.UNKNOWN, MissionIntent.ORG_RECON) else (), pivot_executor_enabled=False, pivot_candidates_count=0, pivot_candidate_types=(), pivot_scheduled_lanes=(), pivot_skip_reason=None, pivot_errors=(), mission_intent=_intent, mission_target_kind=_target_kind, mission_required_lanes=_required_lanes, mission_optional_lanes=_optional_lanes, mission_reason=_intent_reason, mission_runtime_applied=_intent not in (MissionIntent.UNKNOWN, MissionIntent.ORG_RECON), mission_lane_priority=_required_lanes, mission_pivot_boost_applied=_intent not in (MissionIntent.UNKNOWN, MissionIntent.ORG_RECON), mission_feed_cap_reason=None)
    return AcquisitionStrategySnapshot(query=query, duration_s=duration_s, aggressive_mode=aggressive_mode, uma_state=uma_state, swap_detected=swap_detected, accepted_findings_so_far=accepted_findings_so_far, branch_timeout_count=branch_timeout_count, stealth_ready=stealth_ready, transport_degraded=transport_degraded, plans=tuple(plans), nonfeed_plan_debug=_nonfeed_debug, feed_dominance_budget=feed_budget, has_domain=has_domain)
_ct_adapter: Any = None

def _get_ct_adapter():
    """Return the CT adapter: real call_crtsh or the patched fake."""
    global _ct_adapter
    if _ct_adapter is not None:
        return _ct_adapter
    from hledac.universal.discovery.crtsh_adapter import call_crtsh
    return call_crtsh

async def run_enabled_acquisition_lanes(snapshot, query: str, store, uma_state: str='ok', seed_context: NonfeedSeedContext | None=None, graph_accumulator=None) -> tuple:
    """
    Run all enabled optional acquisition lanes (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN)
    bounded by their per-lane plans from the acquisition strategy snapshot.

    FEED and PUBLIC lanes are NOT run here — they are run by SprintScheduler
    via its own pipeline calls.

    STEALTH lane is NOT run here — caller must explicitly enable it.

    Args:
        snapshot:   AcquisitionStrategySnapshot from build_acquisition_plan().
        query:      Sprint query string.
        store:      DuckDBShadowStore for canonical storage (async_ingest_findings_batch).
        uma_state:  Current UMA state ("ok" | "warn" | "critical" | "emergency").
        seed_context: NonfeedSeedContext for domain/IP seeding.
        graph_accumulator: SprintGraphAccumulator instance for graph wiring.
                           If None, graph accumulation is skipped (fail-soft, F265C).

    Returns:
        Tuple of AcquisitionLaneOutcome, one per optional lane.

    GHOST_INVARIANTS:
      - gather(return_exceptions=True) so one lane crash never fails others
      - per-lane timeout enforced via asyncio.timeout
      - per-lane max_items enforced by each lane adapter
      - STEALTH never auto-enabled
      - No MLX/model load
    """
    import asyncio
    import time
    outcomes: list = []
    tasks: list[asyncio.Task] = []
    hardware_critical = uma_state in ('critical', 'emergency')

    async def _run_ct_lane(plan) -> AcquisitionLaneOutcome:
        """Run CT/crt.sh lane — wired to call_crtsh() for measurable outcome.

        [F207K-A] Uses bridge helpers to produce CanonicalFinding candidates
        with rejection tracking. DB write is the lane runner's job (not adapter).
        """
        start = time.monotonic()
        _raw = build_lane_query(query, AcquisitionLane.CT, seed_context)
        shaped_query = _raw if isinstance(_raw, str) else ''
        ct_error: str | None = None
        ct_results_raw = 0
        candidate_findings: tuple = ()
        rejection_reasons: tuple = ()
        rejected_count = 0
        sample_rejections: tuple = ()
        try:
            async with asyncio.timeout(plan.timeout_s):
                _ct_call = _get_ct_adapter()
                result, ct_outcome = await _ct_call(query=shaped_query, max_results=plan.max_items, timeout_s=plan.timeout_s)
                ct_results_raw = ct_outcome.raw_count
                candidates, rejections, _ct_telemetry = ct_results_to_findings(result, ct_outcome, query, sprint_id=f'ct-{int(time.time())}')
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])
                if candidate_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(list(candidate_findings), sprint_id=f'ct-{int(time.time())}')
                    except Exception:
                        pass
                if ct_outcome.error:
                    ct_error = ct_outcome.error
                return AcquisitionLaneOutcome(lane=AcquisitionLane.CT, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=ct_results_raw, duration_s=time.monotonic() - start, source_family='ct', ct_query=shaped_query, ct_results_raw=ct_results_raw, error=ct_error, candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, ct_candidates_built=len(candidate_findings))
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.CT, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='ct', ct_query=shaped_query, ct_results_raw=ct_results_raw, candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, ct_candidates_built=len(candidate_findings))
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.CT, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=ct_results_raw, error=f'{type(exc).__name__}:{exc}', duration_s=time.monotonic() - start, source_family='ct', ct_query=shaped_query, ct_results_raw=ct_results_raw, candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, ct_candidates_built=len(candidate_findings))

    async def _run_wayback_lane(plan) -> AcquisitionLaneOutcome:
        """Run Wayback diff mining lane — runtime safety check before network call.

        [F207K-A] Uses bridge helpers to produce CanonicalFinding candidates
        with rejection tracking.
        """
        start = time.monotonic()
        candidate_findings: tuple = ()
        rejection_reasons: tuple = ()
        rejected_count = 0
        sample_rejections: tuple = ()
        try:
            from hledac.universal.intel.wayback_diff_miner import WaybackDiffMiner as _WDM
            if not callable(_WDM):
                raise ImportError('WaybackDiffMiner not callable')
        except Exception as _exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.WAYBACK, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=0, duration_s=time.monotonic() - start, source_family='archive', error=f'adapter_not_runtime_safe: {_exc}', candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections)
        try:
            async with asyncio.timeout(plan.timeout_s):
                shaped_query = build_lane_query(query, AcquisitionLane.WAYBACK, seed_context)
                shaped_query_str = shaped_query if isinstance(shaped_query, str) else query
                miner = _WDM()
                try:
                    result = await miner.mine([shaped_query_str])
                finally:
                    await miner.close()
                candidates, rejections, _wb_telemetry = wayback_results_to_findings(result, query, sprint_id=f'wayback-{int(time.time())}')
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])
                if candidate_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(list(candidate_findings), sprint_id=f'wayback-{int(time.time())}')
                    except Exception:
                        pass
                return AcquisitionLaneOutcome(lane=AcquisitionLane.WAYBACK, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=len(result.change_events), duration_s=time.monotonic() - start, source_family='archive', candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, wayback_raw_count=len(result.change_events), wayback_query=shaped_query_str)
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.WAYBACK, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='archive', candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, wayback_raw_count=0, wayback_query=shaped_query_str)
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.WAYBACK, enabled=plan.enabled, attempted=True, error=f'{type(exc).__name__}:{exc}', duration_s=time.monotonic() - start, source_family='archive', candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, wayback_raw_count=0, wayback_query=shaped_query_str)

    async def _run_pdns_lane(plan) -> AcquisitionLaneOutcome:
        """Run passive DNS lookup lane — wired to call_lookup_passive_dns with domain/IP shaping.

        [F207K-A] Uses bridge helpers to produce CanonicalFinding candidates
        with rejection tracking.
        """
        start = time.monotonic()
        _raw = build_lane_query(query, AcquisitionLane.PASSIVE_DNS, seed_context)
        shaped_query = _raw if isinstance(_raw, str) else ''
        pdns_error: str | None = None
        produced = 0
        candidate_findings: tuple = ()
        rejection_reasons: tuple = ()
        rejected_count = 0
        sample_rejections: tuple = ()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.security.passive_dns import call_lookup_passive_dns as _pdns_lookup
                ips, pdns_outcome = await _pdns_lookup(shaped_query)
                produced = pdns_outcome.result_count
                if pdns_outcome.skip_reason:
                    pdns_error = pdns_outcome.skip_reason
                elif pdns_outcome.error:
                    pdns_error = pdns_outcome.error
                candidates, rejections, _pdns_telemetry = passive_dns_results_to_findings(ips, pdns_outcome, query, sprint_id=f'pdns-{int(time.time())}')
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])
                if candidate_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(list(candidate_findings), sprint_id=f'pdns-{int(time.time())}')
                    except Exception:
                        pass
                return AcquisitionLaneOutcome(lane=AcquisitionLane.PASSIVE_DNS, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=produced, duration_s=time.monotonic() - start, source_family='passive_dns', error=pdns_error, candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, passive_dns_raw_count=produced, passive_dns_query=shaped_query)
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.PASSIVE_DNS, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='passive_dns', candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, passive_dns_raw_count=0, passive_dns_query=shaped_query)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.PASSIVE_DNS, enabled=plan.enabled, attempted=True, error=f'{type(exc).__name__}:{exc}', duration_s=time.monotonic() - start, source_family='passive_dns', candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, passive_dns_raw_count=0, passive_dns_query=shaped_query)

    async def _run_academic_lane(plan) -> AcquisitionLaneOutcome:
        """Run academic search lane — R9: bounded, research-profile-only, no query expansion."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intel.academic_search import AcademicSearchEngine, SearchResult
                from hledac.universal.runtime.source_finding_bridge import academic_results_to_findings
                engine = AcademicSearchEngine(enable_expansion=False)
                try:
                    result = await engine.search(query, max_results=plan.max_items, sources=['arxiv', 'crossref'])
                finally:
                    await engine.cleanup()
                search_results = [r for r in result.deduplicated_results if isinstance(r, SearchResult)]
                candidates, rejections, _telemetry = academic_results_to_findings(search_results, query, sprint_id=f'academic-{int(time.time())}')
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:5])
                if candidate_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(list(candidate_findings), sprint_id=f'academic-{int(time.time())}')
                    except Exception:
                        pass
                return AcquisitionLaneOutcome(lane=AcquisitionLane.ACADEMIC, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=len(search_results), duration_s=time.monotonic() - start, source_family='academic', candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.ACADEMIC, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='academic')
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.ACADEMIC, enabled=plan.enabled, attempted=True, error=f'{type(exc).__name__}:{exc}', duration_s=time.monotonic() - start, source_family='academic')

    async def _run_ipfs_lane(plan) -> AcquisitionLaneOutcome:
        """R10: CID-only IPFS evidence fetch — bounded gateway fetch, no search/DHT/recursive.

        Enabled only when query is an explicit CID. No model load, no browser,
        no stealth. Hard cap: 5 CIDs per sprint regardless of max_items.
        """
        start = time.monotonic()
        query_cid = query.strip()
        all_cids: list[str] = [query_cid] if _has_explicit_cid(query_cid) else []
        MAX_IPFS_CIDS = 5
        cids_to_fetch = all_cids[:MAX_IPFS_CIDS]
        accepted = 0
        produced = 0
        candidate_findings: tuple = ()
        terminal_state = 'success'
        ipfs_cid_count = 0
        try:
            async with asyncio.timeout(plan.timeout_s):
                import hashlib
                from hledac.universal.network.ipfs_client import fetch_ipfs, ipfs_content_to_finding_dict
                findings_list: list = []
                for cid in cids_to_fetch:
                    content: bytes | None = None
                    gateway_used = 'none'
                    for gw_name, _gw_url in [('cloudflare', 'https://cloudflare-ipfs.com/ipfs/'), ('ipfs.io', 'https://ipfs.io/ipfs/')]:
                        try:
                            content = await fetch_ipfs(cid, timeout=25)
                            if content is not None:
                                gateway_used = gw_name
                                break
                        except Exception:
                            continue
                    if content is None:
                        terminal_state = 'empty'
                        continue
                    content_text = content.decode('utf-8', errors='replace')
                    content_hash = hashlib.sha256(content_text[:2000].encode()).hexdigest()[:16]
                    f'ipfs_{cid}_{int(start * 1000)}_{content_hash}'
                    finding_dict = ipfs_content_to_finding_dict(cid=cid, content=content, gateway=gateway_used, query=query_cid, ts=start, finding_id_prefix='ipfs')
                    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
                    try:
                        finding = CanonicalFinding(finding_id=finding_dict['finding_id'], query=finding_dict['query'], source_type=finding_dict['source_type'], confidence=finding_dict['confidence'], ts=finding_dict['ts'], provenance=finding_dict['provenance'], payload_text=finding_dict.get('payload_text'))
                        findings_list.append(finding)
                        produced += 1
                        ipfs_cid_count += 1
                    except Exception:
                        terminal_state = 'error'
                        continue
                candidate_findings = tuple(findings_list)
                if candidate_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(list(candidate_findings), sprint_id=f'ipfs-{int(time.time())}')
                    except Exception:
                        pass
                return AcquisitionLaneOutcome(lane=AcquisitionLane.IPFS, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=produced, duration_s=time.monotonic() - start, source_family='ipfs', candidate_findings=candidate_findings, ipfs_cid_count=ipfs_cid_count, ipfs_terminal_state=terminal_state)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.IPFS, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='ipfs', ipfs_terminal_state='timeout')
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.IPFS, enabled=plan.enabled, attempted=True, error=f'{type(exc).__name__}:{exc}', duration_s=time.monotonic() - start, source_family='ipfs', ipfs_terminal_state='error')

    async def _run_open_source_lane(plan) -> AcquisitionLaneOutcome:
        """Run OpenSourceCollectors lane — pastebin, usenet, matrix, academic, sec_edgar, court records."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intel.open_source_collectors import get_open_source_collectors
                collector = get_open_source_collectors()
                results = await collector.gather_all(query)
                all_findings: list = []
                for _source, findings in results.items():
                    all_findings.extend(findings)
                if all_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(all_findings, sprint_id=f'open_source-{int(time.time())}')
                    except Exception:
                        pass
                return AcquisitionLaneOutcome(lane=AcquisitionLane.OPEN_SOURCE, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=len(all_findings), duration_s=time.monotonic() - start, source_family='public')
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.OPEN_SOURCE, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='public')
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.OPEN_SOURCE, enabled=plan.enabled, attempted=True, error=f'{type(exc).__name__}:{exc}', duration_s=time.monotonic() - start, source_family='public')

    async def _run_doh_lane(plan) -> AcquisitionLaneOutcome:
        """Run DOH lane — DNS-over-HTTPS passive DNS recon via DOHAdapter.

        F222B: First-class nonfeed lane. No model load, no browser, no stealth.
        Bounds: max_items=20, timeout_s=30, concurrency=2.
        Fail-soft: provider errors never break other lanes.
        """
        start = time.monotonic()
        doh_raw_count = 0
        candidate_findings: tuple = ()
        accepted = 0
        shaped_query = build_lane_query(query, AcquisitionLane.DOH, seed_context)
        if shaped_query is None or (isinstance(shaped_query, dict) and shaped_query.get('_disabled')):
            return AcquisitionLaneOutcome(lane=AcquisitionLane.DOH, enabled=plan.enabled, attempted=False, source_family='doh', error=shaped_query.get('_disabled_reason', 'no_domain_seed') if isinstance(shaped_query, dict) else 'build_lane_query_returned_none', doh_query=shaped_query if isinstance(shaped_query, str) else '')
        domain = shaped_query if isinstance(shaped_query, str) else str(shaped_query)
        if not domain:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.DOH, enabled=plan.enabled, attempted=False, source_family='doh', error='empty_domain', doh_query=domain)
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intel.doh_lane import DOHAdapter
                from hledac.universal.runtime.source_finding_bridge import doh_results_to_findings
                adapter = DOHAdapter()
                session = await async_get_httpx_session()
                findings = await adapter.run(domain=domain, session=session)
                doh_raw_count = len(findings)
                if findings:
                    candidates, _rejections, _tel = doh_results_to_findings(findings, None, query, sprint_id=f'doh-{int(time.time())}')
                    candidate_findings = tuple(candidates)
                if candidate_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(list(candidate_findings), sprint_id=f'doh-{int(time.time())}')
                    except Exception:
                        pass
                return AcquisitionLaneOutcome(lane=AcquisitionLane.DOH, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=doh_raw_count, duration_s=time.monotonic() - start, source_family='doh', doh_query=domain)
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.DOH, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='doh', produced_items=doh_raw_count, doh_query=domain)
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.DOH, enabled=plan.enabled, attempted=True, error=f'{type(exc).__name__}:{exc}', duration_s=time.monotonic() - start, source_family='doh', produced_items=doh_raw_count, doh_query=domain)

    async def _run_blockchain_lane(plan) -> AcquisitionLaneOutcome:
        """Run blockchain forensics lane."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intel.blockchain_analyzer import BlockchainForensics
                wallets = _extract_crypto_from_query(query)
                accepted = 0
                total_tx = 0
                all_blockchain_findings: list = []
                for address in wallets[:plan.max_items]:
                    try:
                        bf = BlockchainForensics()
                        result = await bf.analyze_wallet(address)
                        await bf.close()
                        if result:
                            findings = _wallet_to_findings(result, query)
                            if findings:
                                all_blockchain_findings.extend(findings)
                                total_tx += getattr(result, 'transaction_count', 0) or 0
                    except Exception:
                        continue
                if all_blockchain_findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(all_blockchain_findings, sprint_id=f'blockchain-{int(time.time())}')
                    except Exception:
                        pass
                return AcquisitionLaneOutcome(lane=AcquisitionLane.BLOCKCHAIN, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=total_tx, duration_s=time.monotonic() - start, source_family='blockchain')
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.BLOCKCHAIN, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='blockchain')
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.BLOCKCHAIN, enabled=plan.enabled, attempted=True, error=f'{type(exc).__name__}:{exc}', duration_s=time.monotonic() - start, source_family='blockchain')
    if snapshot is None:
        return ()

    async def _stealth_never_run(plan) -> AcquisitionLaneOutcome:
        """STEALTH is never auto-run — always record the skip."""
        return AcquisitionLaneOutcome(lane=AcquisitionLane.STEALTH, enabled=False, attempted=False, error='stealth_not_auto_run', source_family='stealth')

    async def _run_shodan_lane(plan) -> AcquisitionLaneOutcome:
        """Run Shodan intelligence lane — device/IP fingerprints."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intel.shodan_lane import ShodanLane
                lane_obj = ShodanLane()
                findings = await lane_obj.query(query)
                if findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(findings, sprint_id=f'shodan-{int(time.time())}')
                    except Exception:
                        pass
                return AcquisitionLaneOutcome(lane=AcquisitionLane.SHODAN, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=len(findings), duration_s=time.monotonic() - start, source_family='shodan_intel')
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.SHODAN, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='shodan_intel')
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.SHODAN, enabled=plan.enabled, attempted=True, duration_s=time.monotonic() - start, error=f'{type(exc).__name__}:{exc}', source_family='shodan_intel')

    async def _run_censys_lane(plan) -> AcquisitionLaneOutcome:
        """Run Censys intelligence lane — certificate transparency."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intel.censys_lane import CensysLane
                lane_obj = CensysLane()
                findings = await lane_obj.query(query)
                if findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(findings, sprint_id=f'censys-{int(time.time())}')
                    except Exception:
                        pass
                return AcquisitionLaneOutcome(lane=AcquisitionLane.CENSYS, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=len(findings), duration_s=time.monotonic() - start, source_family='censys_intel')
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.CENSYS, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='censys_intel')
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.CENSYS, enabled=plan.enabled, attempted=True, duration_s=time.monotonic() - start, error=f'{type(exc).__name__}:{exc}', source_family='censys_intel')

    async def _run_greynoise_lane(plan) -> AcquisitionLaneOutcome:
        """Run GreyNoise intelligence lane — mass scanner classification."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intel.greynoise_lane import GreyNoiseLane
                lane_obj = GreyNoiseLane()
                findings = await lane_obj.query(query)
                if findings and graph_accumulator is not None:
                    try:
                        graph_accumulator.accumulate_findings(findings, sprint_id=f'greynoise-{int(time.time())}')
                    except Exception:
                        pass
                return AcquisitionLaneOutcome(lane=AcquisitionLane.GREYNOISE, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=len(findings), duration_s=time.monotonic() - start, source_family='greynoise_intel')
        except TimeoutError:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.GREYNOISE, enabled=plan.enabled, attempted=True, timeout=True, duration_s=time.monotonic() - start, error='timeout', source_family='greynoise_intel')
        except Exception as exc:
            return AcquisitionLaneOutcome(lane=AcquisitionLane.GREYNOISE, enabled=plan.enabled, attempted=True, duration_s=time.monotonic() - start, error=f'{type(exc).__name__}:{exc}', source_family='greynoise_intel')
    lane_runners = {AcquisitionLane.CT: _run_ct_lane, AcquisitionLane.WAYBACK: _run_wayback_lane, AcquisitionLane.PASSIVE_DNS: _run_pdns_lane, AcquisitionLane.BLOCKCHAIN: _run_blockchain_lane, AcquisitionLane.STEALTH: _stealth_never_run, AcquisitionLane.ACADEMIC: _run_academic_lane, AcquisitionLane.IPFS: _run_ipfs_lane, AcquisitionLane.OPEN_SOURCE: _run_open_source_lane, AcquisitionLane.DOH: _run_doh_lane, AcquisitionLane.SHODAN: _run_shodan_lane, AcquisitionLane.CENSYS: _run_censys_lane, AcquisitionLane.GREYNOISE: _run_greynoise_lane}
    for plan in snapshot.plans:
        lane = plan.lane
        if lane not in lane_runners:
            continue
        if not plan.enabled:
            outcomes.append(AcquisitionLaneOutcome(lane=lane, enabled=False, attempted=False, source_family=_LANE_TO_FAMILY.get(lane, 'unknown')))
            continue
        if hardware_critical and lane in (AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN):
            outcomes.append(AcquisitionLaneOutcome(lane=lane, enabled=False, attempted=False, error='hardware_critical', source_family=_LANE_TO_FAMILY.get(lane, 'unknown')))
            continue
        tasks.append(safe_create_task(lane_runners[lane](plan), name='acquisition:lane_runner'))
    if not tasks:
        return tuple(outcomes)
    results = await safe_gather_ok(*tasks, label='acquisition_strategy:4211')
    all_candidates: list = []
    lane_candidates: list[list] = []
    for result in results:
        if isinstance(result, AcquisitionLaneOutcome):
            outcomes.append(result)
            if result.candidate_findings:
                lane_candidates.append(list(result.candidate_findings))
                all_candidates.extend(result.candidate_findings)
            else:
                lane_candidates.append([])
        elif isinstance(result, Exception):
            outcomes.append(AcquisitionLaneOutcome(lane='UNKNOWN', enabled=True, attempted=True, error=f'gather_error:{result}', source_family='unknown'))
            lane_candidates.append([])
    if all_candidates and store is not None and hasattr(store, 'async_ingest_findings_batch'):
        try:
            ingest_results = await store.async_ingest_findings_batch(all_candidates)
            idx = 0
            for outcome_idx, outcome in enumerate(outcomes):
                lane_len = len(lane_candidates[outcome_idx])
                lane_results = ingest_results[idx:idx + lane_len]
                accepted = sum((1 for r in lane_results if isinstance(r, dict) and r.get('accepted')))
                outcomes[outcome_idx] = AcquisitionLaneOutcome(lane=outcome.lane, enabled=outcome.enabled, attempted=outcome.attempted, accepted_findings=accepted, produced_items=outcome.produced_items, timeout=outcome.timeout, error=outcome.error, duration_s=outcome.duration_s, source_family=outcome.source_family, ct_query=outcome.ct_query, ct_results_raw=outcome.ct_results_raw, candidate_findings=outcome.candidate_findings, rejection_reasons=outcome.rejection_reasons, rejected_count=outcome.rejected_count, sample_rejections=outcome.sample_rejections, ct_candidates_built=outcome.ct_candidates_built, wayback_raw_count=outcome.wayback_raw_count, passive_dns_raw_count=outcome.passive_dns_raw_count, doh_query=outcome.doh_query, wayback_query=outcome.wayback_query, passive_dns_query=outcome.passive_dns_query, ipfs_cid_count=outcome.ipfs_cid_count, ipfs_terminal_state=outcome.ipfs_terminal_state)
                idx += lane_len
        except Exception:
            pass
    return tuple(outcomes)

async def run_enabled_acquisition_lanes_streaming(snapshot, query: str, store, uma_state: str='ok', clearnet_max: int=4, seed_context: NonfeedSeedContext | None=None, graph_accumulator=None, min_finished: int=0, on_lane_complete: Callable[[AcquisitionLaneOutcome], None] | None=None) -> AsyncGenerator[tuple[AcquisitionLaneOutcome, ...]]:
    """
    P2-1: Streaming variant -- lanes run concurrently, yields per-lane as they complete.

    Yields cumulative (outcome,) tuples so callers can accumulate incrementally.
    Early-exit when min_finished lanes done (min_finished=0 means wait for all).

    GHOST_INVARIANTS:
      - safe_gather_ok(return_exceptions=True) preserves fail-soft
      - per-lane asyncio.timeout enforced
      - STEALTH never auto-enabled
      - M1 8GB safe: Semaphore(clearnet_max), bounded [1, 4] for M1 8GB safety
    """
    import asyncio as _asyncio
    outcomes: list = []
    hardware_critical = uma_state in ('critical', 'emergency')
    _lane_sem_limit = max(1, min(clearnet_max, 4)) if not hardware_critical else 1
    _sem = _asyncio.Semaphore(_lane_sem_limit)

    async def _run_ct_lane(plan) -> AcquisitionLaneOutcome:
        async with _sem:
            start = __import__('time').monotonic()
            try:
                async with _asyncio.timeout(plan.timeout_s):
                    from hledac.universal.discovery.crtsh_adapter import call_crtsh
                    _raw = build_lane_query(query, AcquisitionLane.CT, seed_context)
                    shaped_query = _raw if isinstance(_raw, str) else ''
                    result, ct_outcome = await call_crtsh(query=shaped_query, max_results=plan.max_items, timeout_s=plan.timeout_s)
                    ct_results_raw = ct_outcome.raw_count
                    candidates, rejections, _ct_telemetry = ct_results_to_findings(result, ct_outcome, query, sprint_id=f"ct-{int(__import__('time').time())}")
                    candidate_findings = tuple(candidates)
                    rejection_reasons = tuple(rejections)
                    rejected_count = len(rejections)
                    sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])
                    if candidate_findings and graph_accumulator is not None:
                        try:
                            graph_accumulator.accumulate_findings(list(candidate_findings), sprint_id=f"ct-{int(__import__('time').time())}")
                        except Exception:
                            pass
                    ct_error = ct_outcome.error if ct_outcome.error else None
                    return AcquisitionLaneOutcome(lane=AcquisitionLane.CT, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=ct_results_raw, duration_s=__import__('time').monotonic() - start, source_family='ct', ct_query=shaped_query, ct_results_raw=ct_results_raw, error=ct_error, candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections)
            except TimeoutError:
                return AcquisitionLaneOutcome(lane=AcquisitionLane.CT, enabled=plan.enabled, attempted=True, timeout=True, duration_s=__import__('time').monotonic() - start, error='timeout', source_family='ct', ct_query=shaped_query, ct_results_raw=0, candidate_findings=(), rejection_reasons=(), rejected_count=0, sample_rejections=())
            except Exception as exc:
                return AcquisitionLaneOutcome(lane=AcquisitionLane.CT, enabled=plan.enabled, attempted=True, error=f'{type(exc).__name__}:{exc}', duration_s=__import__('time').monotonic() - start, source_family='ct', ct_query=shaped_query, ct_results_raw=0, candidate_findings=(), rejection_reasons=(), rejected_count=0, sample_rejections=())

    async def _run_wayback_lane(plan) -> AcquisitionLaneOutcome:
        async with _sem:
            start = __import__('time').monotonic()
            try:
                async with _asyncio.timeout(plan.timeout_s):
                    from hledac.universal.intel.wayback_diff_miner import WaybackDiffMiner
                    shaped_query = build_lane_query(query, AcquisitionLane.WAYBACK, seed_context)
                    shaped_query_str = shaped_query if isinstance(shaped_query, str) else query
                    miner = WaybackDiffMiner()
                    try:
                        result = await miner.mine([shaped_query_str])
                    finally:
                        await miner.close()
                    candidates, rejections, _wb_telemetry = wayback_results_to_findings(result, query, sprint_id=f"wayback-{int(__import__('time').time())}")
                    candidate_findings = tuple(candidates)
                    rejection_reasons = tuple(rejections)
                    rejected_count = len(rejections)
                    sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])
                    if candidate_findings and graph_accumulator is not None:
                        try:
                            graph_accumulator.accumulate_findings(list(candidate_findings), sprint_id=f"wayback-{int(__import__('time').time())}")
                        except Exception:
                            pass
                    return AcquisitionLaneOutcome(lane=AcquisitionLane.WAYBACK, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=len(result.change_events), duration_s=__import__('time').monotonic() - start, source_family='archive', candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, wayback_raw_count=len(result.change_events), wayback_query=shaped_query_str)
            except TimeoutError:
                return AcquisitionLaneOutcome(lane=AcquisitionLane.WAYBACK, enabled=plan.enabled, attempted=True, timeout=True, duration_s=__import__('time').monotonic() - start, error='timeout', source_family='archive', candidate_findings=(), rejection_reasons=(), rejected_count=0, sample_rejections=(), wayback_raw_count=0, wayback_query=shaped_query_str)
            except Exception as exc:
                return AcquisitionLaneOutcome(lane=AcquisitionLane.WAYBACK, enabled=plan.enabled, attempted=True, error=f'{type(exc).__name__}:{exc}', duration_s=__import__('time').monotonic() - start, source_family='archive', candidate_findings=(), rejection_reasons=(), rejected_count=0, sample_rejections=(), wayback_raw_count=0, wayback_query=shaped_query_str)

    async def _run_pdns_lane(plan) -> AcquisitionLaneOutcome:
        async with _sem:
            start = __import__('time').monotonic()
            try:
                async with _asyncio.timeout(plan.timeout_s):
                    from hledac.universal.security.passive_dns import call_lookup_passive_dns as _pdns_lookup
                    _raw = build_lane_query(query, AcquisitionLane.PASSIVE_DNS, seed_context)
                    shaped_query = _raw if isinstance(_raw, str) else ''
                    ips, pdns_outcome = await _pdns_lookup(shaped_query)
                    produced = pdns_outcome.result_count
                    pdns_error = pdns_outcome.skip_reason or (pdns_outcome.error if pdns_outcome.error else None)
                    candidates, rejections, _pdns_telemetry = passive_dns_results_to_findings(ips, pdns_outcome, query, sprint_id=f"pdns-{int(__import__('time').time())}")
                    candidate_findings = tuple(candidates)
                    rejection_reasons = tuple(rejections)
                    rejected_count = len(rejections)
                    sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])
                    if candidate_findings and graph_accumulator is not None:
                        try:
                            graph_accumulator.accumulate_findings(list(candidate_findings), sprint_id=f"pdns-{int(__import__('time').time())}")
                        except Exception:
                            pass
                    return AcquisitionLaneOutcome(lane=AcquisitionLane.PASSIVE_DNS, enabled=plan.enabled, attempted=True, accepted_findings=0, produced_items=produced, duration_s=__import__('time').monotonic() - start, source_family='passive_dns', error=pdns_error, candidate_findings=candidate_findings, rejection_reasons=rejection_reasons, rejected_count=rejected_count, sample_rejections=sample_rejections, passive_dns_raw_count=produced, passive_dns_query=shaped_query)
            except TimeoutError:
                return AcquisitionLaneOutcome(lane=AcquisitionLane.PASSIVE_DNS, enabled=plan.enabled, attempted=True, timeout=True, duration_s=__import__('time').monotonic() - start, error='timeout', source_family='passive_dns', candidate_findings=(), rejection_reasons=(), rejected_count=0, sample_rejections=(), passive_dns_raw_count=0, passive_dns_query=shaped_query)
            except _asyncio.CancelledError:
                raise
            except Exception as exc:
                return AcquisitionLaneOutcome(lane=AcquisitionLane.PASSIVE_DNS, enabled=plan.enabled, attempted=True, error=f'{type(exc).__name__}:{exc}', duration_s=__import__('time').monotonic() - start, source_family='passive_dns', candidate_findings=(), rejection_reasons=(), rejected_count=0, sample_rejections=(), passive_dns_raw_count=0, passive_dns_query=shaped_query)

    async def _stealth_never_run(plan) -> AcquisitionLaneOutcome:
        return AcquisitionLaneOutcome(lane=AcquisitionLane.STEALTH, enabled=plan.enabled, attempted=False, source_family='stealth', error='stealth_never_auto_run')
    lane_runners = {AcquisitionLane.CT: _run_ct_lane, AcquisitionLane.WAYBACK: _run_wayback_lane, AcquisitionLane.PASSIVE_DNS: _run_pdns_lane, AcquisitionLane.STEALTH: _stealth_never_run, AcquisitionLane.BLOCKCHAIN: _stealth_never_run, AcquisitionLane.ACADEMIC: _stealth_never_run, AcquisitionLane.IPFS: _stealth_never_run, AcquisitionLane.OPEN_SOURCE: _stealth_never_run, AcquisitionLane.DOH: _stealth_never_run, AcquisitionLane.SHODAN: _stealth_never_run, AcquisitionLane.CENSYS: _stealth_never_run, AcquisitionLane.GREYNOISE: _stealth_never_run}
    tasks: list[_asyncio.Task] = []
    if snapshot is None:
        yield tuple(outcomes)
        return
    for plan in snapshot.plans:
        lane = plan.lane
        if lane not in lane_runners:
            continue
        if not plan.enabled:
            outcomes.append(AcquisitionLaneOutcome(lane=lane, enabled=False, attempted=False, source_family=_LANE_TO_FAMILY.get(lane, 'unknown')))
            continue
        if hardware_critical and lane in (AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN):
            outcomes.append(AcquisitionLaneOutcome(lane=lane, enabled=False, attempted=False, error='hardware_critical', source_family=_LANE_TO_FAMILY.get(lane, 'unknown')))
            continue
        tasks.append(safe_create_task(lane_runners[lane](plan), name='acquisition:lane_runner'))
    if not tasks:
        yield tuple(outcomes)
        return
    pending: set[_asyncio.Task] = set(tasks)
    finished_count = 0
    while pending:
        if min_finished > 0 and finished_count >= min_finished:
            for t in pending:
                t.cancel()
            break
        done, pending = await _asyncio.wait(pending, return_when=_asyncio.FIRST_COMPLETED)
        for t in done:
            try:
                result = t.result()
            except Exception as exc:
                result = exc
            if isinstance(result, AcquisitionLaneOutcome):
                outcomes.append(result)
            elif isinstance(result, Exception):
                outcomes.append(AcquisitionLaneOutcome(lane='UNKNOWN', enabled=True, attempted=True, error=f'gather_error:{result}', source_family='unknown'))
            finished_count += 1
            if on_lane_complete is not None:
                try:
                    on_lane_complete(outcomes[-1])
                except Exception:
                    pass
            yield tuple(outcomes)
    if pending:
        remaining = await safe_gather_ok(*pending, label='acquisition_strategy:streaming_remainder')
        for result in remaining:
            if isinstance(result, AcquisitionLaneOutcome):
                outcomes.append(result)
            elif isinstance(result, Exception):
                outcomes.append(AcquisitionLaneOutcome(lane='UNKNOWN', enabled=True, attempted=True, error=f'gather_error:{result}', source_family='unknown'))
    yield tuple(outcomes)
_LANE_TO_FAMILY: dict[str, str] = {AcquisitionLane.FEED: 'feed', AcquisitionLane.PUBLIC: 'public', AcquisitionLane.CT: 'ct', AcquisitionLane.WAYBACK: 'archive', AcquisitionLane.PASSIVE_DNS: 'passive_dns', AcquisitionLane.BLOCKCHAIN: 'blockchain', AcquisitionLane.STEALTH: 'stealth', AcquisitionLane.PIVOT_EXECUTOR: 'pivot', AcquisitionLane.ACADEMIC: 'academic', AcquisitionLane.OPEN_SOURCE: 'public', AcquisitionLane.DOH: 'doh'}

def _hits_to_ct_findings(hits: tuple, query: str) -> list:
    """Convert crt.sh DiscoveryHit tuple to CanonicalFinding list."""
    try:
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    except ImportError:
        return []
    findings = []
    for hit in hits:
        try:
            finding = CanonicalFinding(finding_id=f'ct-{hit.url[:32]}-{hash(str(hit.rank)) % 10000:04d}', source_type=getattr(SourceType, 'CT_LOG', 'ct_log') if SourceType else 'ct_log', confidence=0.8, query=query[:128], ts=getattr(hit, 'retrieved_ts', 0.0) or 0.0, payload_text=f'{hit.title}\n{hit.url}\n{hit.snippet}', provenance=('source:crtsh', f'url:{hit.url}'))
            findings.append(finding)
        except Exception:
            continue
    return findings

def _ips_to_pdns_findings(ips: list[str], query: str) -> list:
    """Convert passive DNS IP list to CanonicalFinding list."""
    try:
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    except ImportError:
        return []
    findings = []
    for ip in ips[:100]:
        try:
            finding = CanonicalFinding(finding_id=f'pdns-{ip}', source_type=getattr(SourceType, 'PASSIVE_DNS', 'passive_dns') if SourceType else 'passive_dns', confidence=0.7, query=query[:128], ts=0.0, payload_text=f'ip:{ip}', provenance=('source:circl_pdns', f'resolved_ip:{ip}'))
            findings.append(finding)
        except Exception:
            continue
    return findings

def _wallet_to_findings(wallet_analysis, query: str) -> list:
    """Convert blockchain WalletAnalysis to CanonicalFinding list."""
    try:
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    except ImportError:
        return []
    findings = []
    try:
        address = getattr(wallet_analysis, 'address', '') or ''
        chain = getattr(wallet_analysis, 'chain', '') or 'unknown'
        balance = getattr(wallet_analysis, 'balance', None)
        risk = getattr(wallet_analysis, 'risk_score', None)
        finding = CanonicalFinding(finding_id=f'bc-{address[:16]}', source_type=getattr(SourceType, 'BLOCKCHAIN_FORENSICS', 'blockchain_forensics') if SourceType else 'blockchain_forensics', confidence=0.75, query=query[:128], ts=0.0, payload_text=f'address:{address} chain:{chain} balance:{balance} risk_score:{risk}', provenance=('source:blockchain', f'address:{address}'))
        findings.append(finding)
    except Exception:
        pass
    return findings

def _extract_crypto_from_query(query: str) -> list[str]:
    """Extract crypto wallet addresses and hashes from query string."""
    wallets: list[str] = []
    for pattern in (_WALLET_RE, _CRYPTO_HASH_RE):
        for match in pattern.finditer(query):
            g = match.group()
            if g:
                wallets.append(g)
    return wallets[:20]
_NONFEED_SEED_EMPTY = NonfeedSeedContext()

def build_lane_query(base_query: str, lane: str, seed_context: NonfeedSeedContext | None=None) -> str | dict:
    """
    Shape a source-specific query for an acquisition lane.

    F222I: When seed_context is provided (pivot/DuckDB domain extraction),
    lanes receive the extracted domain/IP seed instead of the generic text query.
    This enables CT/DOH/PassiveDNS/Wayback for "LockBit ransomware" style queries
    that have no explicit domain/IP in the raw query text.

    Rules per lane:
      CT:          seed.domains[0] if available, else extract domains from base_query
      WAYBACK:     seed.domains[0] or seed.urls[0] if available, else base_query
      PASSIVE_DNS: seed.domains[0] or seed.ips[0] if available, else base_query
      BLOCKCHAIN:  wallet/hash only; returns {"_disabled": True} if no crypto indicator
      PUBLIC:      original query plus 1-2 bounded variants (seed ignored)
      FEED:        original query unchanged (seed ignored)

    No LLM, no network I/O. Deterministic.

    Args:
        base_query:  The sprint query string.
        lane:        One of AcquisitionLane values.
        seed_context: Optional NonfeedSeedContext from pivot/DuckDB extraction.

    Returns:
        Shaped query string, or a dict with lane guidance (e.g. {"_disabled": True}).
        Returns {"_disabled": True} for BLOCKCHAIN when no crypto indicator present.
    """
    if lane == AcquisitionLane.CT:
        if seed_context and seed_context.domains:
            return seed_context.domains[0]
        domains = _DOMAIN_OR_IP_RE.findall(base_query)
        if domains:
            unique = list(dict.fromkeys(domains))[:5]
            return ' '.join(unique)
        expansions = _get_keyword_domain_expansion(base_query)
        if expansions:
            return expansions[0]
        return ''
    elif lane == AcquisitionLane.WAYBACK:
        if seed_context and seed_context.domains:
            return seed_context.domains[0]
        if seed_context and seed_context.urls:
            return seed_context.urls[0]
        domains = _DOMAIN_OR_IP_RE.findall(base_query)
        if domains:
            return domains[0]
        expansions = _get_keyword_domain_expansion(base_query)
        if expansions:
            return expansions[0]
        return ''
    elif lane == AcquisitionLane.PASSIVE_DNS:
        return normalize_passive_dns_query(base_query, seed_context)
    elif lane == AcquisitionLane.BLOCKCHAIN:
        wallets = _extract_crypto_from_query(base_query)
        if wallets:
            return wallets[0]
        return {'_disabled': True, 'reason': 'no_crypto_indicator'}
    elif lane == AcquisitionLane.DOH:
        if seed_context and seed_context.domains:
            return seed_context.domains[0]
        ips = _extract_ips_from_query(base_query)
        domains = [d for d in _DOMAIN_OR_IP_RE.findall(base_query) if not _looks_like_ip(d)]
        if domains:
            return domains[0]
        if ips:
            return {'_disabled': True, 'reason': 'ip_seed_reverse_doh_deferred'}
        return {'_disabled': True, 'reason': 'no_domain_seed'}
    elif lane == AcquisitionLane.PUBLIC:
        try:
            from hledac.universal.runtime.osint_query_expander import expand_osint_query
            variants = expand_osint_query(base_query, max_variants=1)
            if variants:
                return variants[0][:200]
        except Exception:
            pass
        trimmed = base_query[:200] if len(base_query) > 200 else base_query
        return trimmed
    return base_query

def _extract_ips_from_query(query: str) -> list[str]:
    """Extract IP address strings from query."""
    ip_pattern = re.compile('\\b\\d{1,3}(?:\\.\\d{1,3}){3}\\b')
    return ip_pattern.findall(query)

def _looks_like_ip(s: str) -> bool:
    """Return True if string looks like an IP address."""
    return bool(re.match('\\d{1,3}(?:\\.\\d{1,3}){3}$', s))

def _looks_like_domain(value: str) -> bool:
    """Return True if value looks like a domain name (no IP, has TLD)."""
    if not value or len(value) > 253:
        return False
    if '.' not in value:
        return False
    if re.match('^\\d{1,3}(?:\\.\\d{1,3}){3}$', value):
        return False
    parts = value.split('.')
    if len(parts) < 2:
        return False
    tld = parts[-1]
    if len(tld) < 1 or len(tld) > 63:
        return False
    if not re.match('^[a-z0-9.\\-_]+$', tld):
        return False
    return True

def normalize_passive_dns_query(base_query: str, seed_context: NonfeedSeedContext | None) -> str:
    """
    Shape a PassiveDNS query with fallback domain extraction from raw query.

    F265: When seed_context.domains is empty (PUBLIC lane NameError caused
    domain seeds to never populate), fall back to extracting a domain directly
    from the raw query using the same regex used elsewhere in build_lane_query.

    P2-4 Tier 2: If no domain/IP indicators found anywhere, return the full
    base_query as a free-text PDNS search rather than empty string.
    Many PDNS providers accept free-text queries (brand, actor, campaign names)
    and return associated IPs/domains.

    Returns:
        First domain/IP indicator found, or full base_query as fallback, or "".
    """
    if seed_context and seed_context.domains:
        return seed_context.domains[0]
    if seed_context and seed_context.ips:
        return seed_context.ips[0]
    ips = _extract_ips_from_query(base_query)
    domains = [d for d in _DOMAIN_OR_IP_RE.findall(base_query) if not _looks_like_ip(d)]
    indicators = ips + domains
    if indicators:
        return indicators[0]
    try:
        keywords = _expand_keyword_query(base_query)
        for kw in keywords:
            expansions = DOMAIN_EXPANSIONS.get(kw.lower(), ())
            if expansions:
                logger.debug('passive_dns keyword_expand: kw=%r -> domain=%r', kw, expansions[0])
                return expansions[0]
    except Exception:
        pass
    if base_query and base_query.strip():
        logger.debug('passive_dns freetext_fallback: raw_query=%r', base_query)
        return base_query.strip()
    logger.warning('passive_dns empty_query: seed_domains=%s, seed_ips=%s, raw_query=%r, extracted_ips=%r, extracted_domains=%r', len(seed_context.domains) if seed_context else 0, len(seed_context.ips) if seed_context else 0, base_query, ips, domains)
    return ''

def select_ct_domains_for_passivedns_pivot(ct_candidate_findings: list, *, max_pivots: int=5) -> list[str]:
    """
    Sprint R5: Extract deduplicated domains from CT-accepted CanonicalFinding
    candidates for PassiveDNS one-hop pivot.

    Pure function: deterministic output from deterministic input.
    No network I/O, no side effects.

    Args:
        ct_candidate_findings: List of CanonicalFinding (or dict-like) objects
            with source_type="ct" and payload_text containing domain lines.
        max_pivots: Default cap on pivot domains (default=5, hard_max=10).

    Returns:
        Deduplicated list of domain strings (max 10), in first-seen order.

    Invariants:
        - pivot depth = 1 (caller enforces)
        - no recursive pivoting
        - no network I/O
        - no new queue framework
        - deterministic: same input always yields same output

    Domain extraction:
        - Parse "domain: <value>" lines from payload_text
        - Fallback: query field if no domain line found
        - Skip: empty/whitespace-only domains
        - Order: first-seen (dict.fromkeys preserves insertion order)
    """
    if not ct_candidate_findings:
        return []
    _hard_max = 10
    _effective_max = min(max_pivots, _hard_max)
    seen: dict[str, str] = {}
    for finding in ct_candidate_findings:
        domain = _extract_domain_from_ct_finding(finding)
        if domain and domain not in seen:
            seen[domain] = domain
            if len(seen) >= _effective_max:
                break
    return list(seen.values())

def _extract_domain_from_ct_finding(finding: Any) -> str | None:
    """
    Extract domain from a CT CanonicalFinding (or dict-like) object.

    Strategy:
        1. Try payload_text: parse "domain: <value>" lines
        2. Fallback: query field

    Returns:
        Normalized lowercase domain string, or None if not extractable.
    """
    payload: str | None = getattr(finding, 'payload_text', None)
    if payload and isinstance(payload, str):
        for line in payload.splitlines():
            line = line.strip()
            if line.startswith('domain:'):
                domain = line[len('domain:'):].strip()
                if domain:
                    return domain.lower()
        for line in payload.splitlines():
            line = line.strip()
            if line and (not line.startswith('#')) and ('.' in line):
                if len(line) <= 253 and ' ' not in line and (line.startswith(('www.', 'http', '//')) is False):
                    if re.match('^[a-z0-9.\\-_]+$', line):
                        return line.lower()
    query: str = getattr(finding, 'query', '') or ''
    if query:
        domains = _DOMAIN_OR_IP_RE.findall(query)
        if domains:
            for d in domains:
                if d and '.' in d and (not _looks_like_ip(d)):
                    return d.lower()
        if _looks_like_domain(query.strip()):
            return query.strip().lower()
    return None