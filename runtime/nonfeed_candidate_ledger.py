"""
Sprint F217E: NonfeedCandidateLedger — Unified Bounded Evidence Ledger
======================================================================

runtime/nonfeed_candidate_ledger.py
-----------------------------------
Bounded in-memory ledger for nonfeed candidate lifecycle events:
  PUBLIC stage machine | CT quarantine | Pivot discovered | Quality rejection

Design constraints:
  - max 500 records (FIFO eviction)
  - No full payload text, no sensitive raw blobs
  - sample_url / sample_value bounded to 200 chars
  - candidate_id is short stable hash (not full finding_id)
  - All fields are primitives (no nested dicts/lists at record level)

Ledger record schema:
  family         — PUBLIC | CT | WAYBACK | PASSIVE_DNS | PIVOT
  stage          — discovered | fetched | parsed | quarantined | rejected | stored | accepted | provider_failed
  candidate_id   — first 16 chars BLAKE2b of value (stable, not secret)
  source         — lightweight source tag (e.g., "live_public_pipeline", "ct_bridge", "pivot_planner")
  reason         — short reason string (e.g., "quality_rejected", "wildcard_domain")
  accepted       — True iff candidate became accepted CanonicalFinding
  quarantine     — True iff candidate was quarantined (CT bridge rejection)
  stale          — True iff candidate was later superseded/deduped
  sample_url     — bounded URL or query string (max 200 chars)
  sample_value   — bounded IOC value sample (max 200 chars, e.g. domain/IP)
  ts_monotonic   — time.monotonic() at event creation

Owned files (Sprint F217E):
  runtime/nonfeed_candidate_ledger.py   ← this module
  runtime/sprint_scheduler.py            — ledger integration + wiring
  runtime/source_finding_bridge.py       — CT quarantine producer
  pipeline/live_public_pipeline.py        — PUBLIC stage machine producer
  runtime/pivot_planner.py               — pivot discovered producer
  tools/live_result_sanity.py            — sanity validation
  tests/probe_f217e_nonfeed_candidate_ledger/  — test suite
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    "NonfeedCandidateLedger",
    "LedgerRecord",
    "LEDGER_FAMILY",
    "LEDGER_STAGE",
    "DomainCandidate",
    "extract_domain_candidates_from_text",
    "extract_domain_candidates_from_finding",
    "compute_lane_eligibility",
    "rank_candidates",
    "filter_source_host_only",
    "FAMILY_FEED",
    # F214 bounds
    "MAX_DOMAIN_CANDIDATES_FOR_LANES",
    "MAX_FEED_CANDIDATES",
    "MAX_DOH_DOMAINS",
    "MAX_CT_DOMAINS",
    "MAX_WAYBACK_CANDIDATES",
    "MAX_PASSIVE_DNS_CANDIDATES",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEDGER_FAMILY: Final[type] = str
LEDGER_STAGE: Final[type] = str

MAX_LEDGER_SIZE: Final[int] = 500
"""Hard cap on in-memory ledger records (FIFO eviction)."""

MAX_SAMPLE_CHARS: Final[int] = 200
"""Max chars for sample_url and sample_value."""

CANDIDATE_ID_TRUNC: Final[int] = 16
"""BLAKE2b candidate_id is first 16 hex chars of value hash."""

# Families
FAMILY_PUBLIC: Final[str] = "PUBLIC"
FAMILY_CT: Final[str] = "CT"
FAMILY_WAYBACK: Final[str] = "WAYBACK"
FAMILY_PASSIVE_DNS: Final[str] = "PASSIVE_DNS"
FAMILY_PIVOT: Final[str] = "PIVOT"
# F214: Domain candidates extracted from FEED/PUBLIC findings for non-domain queries
FAMILY_FEED: Final[str] = "FEED"

# F214: Bounding constants for domain candidate extraction and lane planning
MAX_DOMAIN_CANDIDATES_FOR_LANES: Final[int] = 10
"""Max domain candidates extracted from FEED/PUBLIC findings to feed lane planner."""

MAX_FEED_CANDIDATES: Final[int] = 10
"""Max candidates per FEED source URL to prevent oversized extraction."""

MAX_DOH_DOMAINS: Final[int] = 5
"""Max domains passed to DOH lane planner."""

MAX_CT_DOMAINS: Final[int] = 10
"""Max domains passed to CT lane planner."""

MAX_WAYBACK_CANDIDATES: Final[int] = 10
"""Max candidates passed to Wayback lane planner."""

MAX_PASSIVE_DNS_CANDIDATES: Final[int] = 10
"""Max candidates passed to PassiveDNS lane planner."""

# Stages
STAGE_DISCOVERED: Final[str] = "discovered"
STAGE_FETCHED: Final[str] = "fetched"
STAGE_PARSED: Final[str] = "parsed"
STAGE_QUARANTINED: Final[str] = "quarantined"
STAGE_REJECTED: Final[str] = "rejected"
STAGE_STORED: Final[str] = "stored"
STAGE_ACCEPTED: Final[str] = "accepted"
STAGE_PROVIDER_FAILED: Final[str] = "provider_failed"


# ---------------------------------------------------------------------------
# Ledger Record
# ---------------------------------------------------------------------------

@dataclass(frozen=True, order=False)
class LedgerRecord:
    """
    Sprint F217E: Bounded nonfeed candidate lifecycle record.

    No full payload. No sensitive blobs. All fields are primitives.
    candidate_id is truncated BLAKE2b hash of the actual value — stable
    identifier without leaking the raw IOC.
    """

    family: str  # PUBLIC | CT | WAYBACK | PASSIVE_DNS | PIVOT
    stage: str  # discovered | fetched | parsed | quarantined | rejected | stored | accepted | provider_failed
    candidate_id: str  # first 16 chars of BLAKE2b(value.encode())
    source: str  # lightweight source tag
    reason: str  # short reason
    accepted: bool  # True iff accepted CanonicalFinding
    quarantine: bool  # True iff quarantined (CT bridge rejection)
    stale: bool  # True iff later superseded/deduped
    sample_url: str  # bounded URL/query (max 200 chars)
    sample_value: str  # bounded IOC value (max 200 chars)
    ts_monotonic: float  # time.monotonic() at creation

    def __post_init__(self) -> None:
        # Enforce bounds at construction (fail-fast on bad data)
        if len(self.sample_url) > MAX_SAMPLE_CHARS:
            object.__setattr__(self, "sample_url", self.sample_url[:MAX_SAMPLE_CHARS])
        if len(self.sample_value) > MAX_SAMPLE_CHARS:
            object.__setattr__(self, "sample_value", self.sample_value[:MAX_SAMPLE_CHARS])
        if len(self.candidate_id) > CANDIDATE_ID_TRUNC:
            object.__setattr__(self, "candidate_id", self.candidate_id[:CANDIDATE_ID_TRUNC])


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------

@dataclass
class NonfeedCandidateLedger:
    """
    Sprint F217E: Bounded in-memory nonfeed candidate evidence ledger.

    FIFO eviction at MAX_LEDGER_SIZE records. Thread-safe for async use via
    a lock. All mutating operations acquire the lock; reads do not.

    Producers wire:
      - PUBLIC stage machine → discovered / fetched / rejected / accepted
      - CT quarantine         → quarantined / rejected / provider_failed
      - Pivot planner        → discovered (PIVOT family)
      - Quality rejection    → rejected (mirrored from quality_rejection_ledger)

    ABORT CONDITIONS (enforced by tests):
      - NEVER count quarantine as accepted
      - NEVER store full payload text
      - NEVER generate ledger in benchmark context
    """

    _records: deque[LedgerRecord] = field(default_factory=lambda: deque(maxlen=MAX_LEDGER_SIZE))
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(
        self,
        *,
        family: str,
        stage: str,
        candidate_id: str,
        source: str,
        reason: str,
        accepted: bool = False,
        quarantine: bool = False,
        stale: bool = False,
        sample_url: str = "",
        sample_value: str = "",
        ts_monotonic: float | None = None,
    ) -> None:
        record = LedgerRecord(
            family=family,
            stage=stage,
            candidate_id=candidate_id[:CANDIDATE_ID_TRUNC],
            source=source,
            reason=reason,
            accepted=accepted,
            quarantine=quarantine,
            stale=stale,
            sample_url=sample_url[:MAX_SAMPLE_CHARS] if sample_url else "",
            sample_value=sample_value[:MAX_SAMPLE_CHARS] if sample_value else "",
            ts_monotonic=ts_monotonic if ts_monotonic is not None else time.monotonic(),
        )
        with self._lock:
            self._records.append(record)

    def add_ct_quarantine(
        self,
        *,
        domain: str,
        reject_reason: str,
        source_url: str = "",
        query: str = "",
        ts_monotonic: float | None = None,
    ) -> None:
        """Add CT quarantine event. quarantine=True, accepted=False, family=CT."""
        self.add(
            family=FAMILY_CT,
            stage=STAGE_QUARANTINED,
            candidate_id=_hash_candidate(domain),
            source="ct_bridge",
            reason=reject_reason,
            accepted=False,
            quarantine=True,
            stale=False,
            sample_url=source_url[:MAX_SAMPLE_CHARS] if source_url else query[:MAX_SAMPLE_CHARS],
            sample_value=domain[:MAX_SAMPLE_CHARS],
            ts_monotonic=ts_monotonic,
        )

    def add_public_event(
        self,
        *,
        stage: str,
        candidate_id: str,
        reason: str,
        accepted: bool = False,
        sample_url: str = "",
        sample_value: str = "",
        ts_monotonic: float | None = None,
    ) -> None:
        """Add PUBLIC stage machine event."""
        self.add(
            family=FAMILY_PUBLIC,
            stage=stage,
            candidate_id=candidate_id,
            source="live_public_pipeline",
            reason=reason,
            accepted=accepted,
            quarantine=False,
            stale=False,
            sample_url=sample_url,
            sample_value=sample_value,
            ts_monotonic=ts_monotonic,
        )

    def add_pivot_discovered(
        self,
        *,
        pivot_type: str,
        ioc_value: str,
        source_hint: str = "",
        reason: str = "",
        ts_monotonic: float | None = None,
    ) -> None:
        """Add PIVOT family discovered event."""
        self.add(
            family=FAMILY_PIVOT,
            stage=STAGE_DISCOVERED,
            candidate_id=_hash_candidate(ioc_value),
            source="pivot_planner",
            reason=reason or f"pivot_type={pivot_type}",
            accepted=False,
            quarantine=False,
            stale=False,
            sample_url=source_hint[:MAX_SAMPLE_CHARS] if source_hint else "",
            sample_value=ioc_value[:MAX_SAMPLE_CHARS],
            ts_monotonic=ts_monotonic,
        )

    def add_quality_rejection(
        self,
        *,
        source_family: str,
        reason: str,
        sample_url: str = "",
        sample_value: str = "",
        ts_monotonic: float | None = None,
    ) -> None:
        """Add quality rejection event (mirrored from quality_rejection_ledger)."""
        self.add(
            family=source_family,
            stage=STAGE_REJECTED,
            candidate_id=_hash_candidate(sample_value or sample_url),
            source="quality_gate",
            reason=reason,
            accepted=False,
            quarantine=False,
            stale=False,
            sample_url=sample_url[:MAX_SAMPLE_CHARS] if sample_url else "",
            sample_value=sample_value[:MAX_SAMPLE_CHARS] if sample_value else "",
            ts_monotonic=ts_monotonic,
        )

    def add_provider_failed(
        self,
        *,
        family: str,
        candidate_id: str,
        reason: str,
        sample_url: str = "",
        sample_value: str = "",
        ts_monotonic: float | None = None,
    ) -> None:
        """Add provider_failed event (e.g., CT/WAYBACK timeout or error)."""
        self.add(
            family=family,
            stage=STAGE_PROVIDER_FAILED,
            candidate_id=candidate_id,
            source=_source_for_family(family),
            reason=reason,
            accepted=False,
            quarantine=False,
            stale=False,
            sample_url=sample_url,
            sample_value=sample_value,
            ts_monotonic=ts_monotonic,
        )

    def add_feed_candidate(
        self,
        *,
        domain: str,
        source_field: str,
        confidence: float,
        reason: str,
        sample_context: str = "",
        ts_monotonic: float | None = None,
    ) -> None:
        """
        F214: Record a FEED-sourced domain candidate for non-domain queries.

        Adds to FEED family with stage=discovered.
        """
        self.add(
            family=FAMILY_FEED,
            stage=STAGE_DISCOVERED,
            candidate_id=_hash_candidate(domain),
            source="feed_candidate_extractor",
            reason=reason,
            accepted=False,
            quarantine=False,
            stale=False,
            sample_url=source_field,
            sample_value=domain[:MAX_SAMPLE_CHARS],
            ts_monotonic=ts_monotonic,
        )

    # ---------------------------------------------------------------------------
    # F214: Candidate extraction facade — makes the ledger boundary real
    # ---------------------------------------------------------------------------

    def ingest_text_for_candidates(
        self,
        text: str,
        source_url: str | None = None,
        source_family: str = FAMILY_PUBLIC,
        max_candidates: int = MAX_FEED_CANDIDATES,
    ) -> list[DomainCandidate]:
        """
        F214: Extract domain candidates from text and record as FEED candidates.

        Convenience facade that combines extraction + ledger recording.
        Returns extracted candidates (for immediate use by caller).

        Args:
            text:           Text to scan
            source_url:     Optional source URL for hostname extraction
            source_family:  "PUBLIC" or "FEED"
            max_candidates: Max candidates to record per source

        Returns:
            List of DomainCandidate extracted (may be empty).
        """
        candidates = extract_domain_candidates_from_text(
            text,
            source_url=source_url,
            source_family=source_family,
        )
        for tc in candidates[:max_candidates]:
            try:
                self.add_feed_candidate(
                    domain=tc.domain,
                    source_field=tc.source_field,
                    confidence=tc.confidence,
                    reason=f"{tc.reason} (seen={tc.seen_count})",
                    sample_context=tc.sample_context[:200] if tc.sample_context else "",
                )
            except Exception:
                pass  # fail-soft: ledger errors must never crash caller
        return candidates

    def compute_eligibility_from_candidates(
        self,
        candidates: list[DomainCandidate],
    ) -> dict[str, bool]:
        """
        F214: Compute lane eligibility from domain candidates.

        Facade for compute_lane_eligibility — returns the same dict.

        Args:
            candidates:  List of DomainCandidate

        Returns:
            Dict with ct, doh, wayback, passive_dns bools.
        """
        return compute_lane_eligibility(candidates)

    def record_candidates(
        self,
        candidates: list[DomainCandidate],
        source_url: str | None = None,
        max_total: int = MAX_DOMAIN_CANDIDATES_FOR_LANES,
    ) -> list[DomainCandidate]:
        """
        F214: Filter, rank, and record candidates in one call.

        Combines filter_source_host_only + rank_candidates + add_feed_candidate.
        Use this for the final ranking/recording step after deduplication.

        Args:
            candidates:  Deduplicated list of DomainCandidate
            source_url:   Optional source URL for hostname filtering
            max_total:    Maximum candidates to return/record

        Returns:
            Ranked, bounded list of DomainCandidate.
        """
        filtered = candidates
        source_host_domains: frozenset[str] = frozenset()
        if source_url:
            filtered, source_host_domains = filter_source_host_only(
                candidates, source_url
            )
        ranked = rank_candidates(
            filtered,
            max_total=max_total,
            source_host_domains=source_host_domains,
        )
        for tc in ranked:
            try:
                self.add_feed_candidate(
                    domain=tc.domain,
                    source_field=tc.source_field,
                    confidence=tc.confidence,
                    reason=f"{tc.reason} (seen={tc.seen_count})",
                    sample_context=tc.sample_context[:200] if tc.sample_context else "",
                )
            except Exception:
                pass  # fail-soft
        return ranked

    def records(self) -> tuple[LedgerRecord, ...]:
        """Return immutable snapshot of all records (oldest first)."""
        with self._lock:
            return tuple(self._records)

    def count_by_stage(self, stage: str) -> int:
        """Count records with given stage."""
        with self._lock:
            return sum(1 for r in self._records if r.stage == stage)

    def count_by_family(self, family: str) -> int:
        """Count records with given family."""
        with self._lock:
            return sum(1 for r in self._records if r.family == family)

    def count_accepted(self) -> int:
        """Count accepted=True records."""
        with self._lock:
            return sum(1 for r in self._records if r.accepted)

    def count_quarantine(self) -> int:
        """Count quarantine=True records."""
        with self._lock:
            return sum(1 for r in self._records if r.quarantine)

    def summary(self) -> dict:
        """
        Sprint F217E: Compute bounded summary for reporting.

        Returns dict with counts per family, per stage, and key booleans.
        Does NOT include full records (prevents payload leakage in reports).
        """
        with threading.Lock():
            records = tuple(self._records)

        by_family: dict[str, int] = {}
        by_stage: dict[str, int] = {}
        accepted_count = 0
        quarantine_count = 0
        stale_count = 0

        for r in records:
            by_family[r.family] = by_family.get(r.family, 0) + 1
            by_stage[r.stage] = by_stage.get(r.stage, 0) + 1
            if r.accepted:
                accepted_count += 1
            if r.quarantine:
                quarantine_count += 1
            if r.stale:
                stale_count += 1

        # Bounded sample: first 3 URLs per family
        sample_by_family: dict[str, list[str]] = {}
        for r in records:
            if r.family not in sample_by_family:
                sample_by_family[r.family] = []
            if len(sample_by_family[r.family]) < 3 and r.sample_url:
                sample_by_family[r.family].append(r.sample_url)

        return {
            "total_records": len(records),
            "max_records": MAX_LEDGER_SIZE,
            "by_family": by_family,
            "by_stage": by_stage,
            "accepted_count": accepted_count,
            "quarantine_count": quarantine_count,
            "stale_count": stale_count,
            "sample_urls_by_family": sample_by_family,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_candidate(value: str) -> str:
    """Generate short stable candidate_id from IOC value (first 16 hex chars)."""
    return hashlib.blake2b(value.encode(), digest_size=8).hexdigest()[:CANDIDATE_ID_TRUNC]


def _source_for_family(family: str) -> str:
    """Map family to default source tag."""
    return {
        FAMILY_CT: "ct_bridge",
        FAMILY_PUBLIC: "live_public_pipeline",
        FAMILY_WAYBACK: "wayback_bridge",
        FAMILY_PASSIVE_DNS: "pdns_bridge",
        FAMILY_PIVOT: "pivot_planner",
        FAMILY_FEED: "feed_candidate_extractor",
    }.get(family, family.lower())


# ---------------------------------------------------------------------------
# F214: Candidate Ranking and Source-Host Filtering
# ---------------------------------------------------------------------------


def rank_candidates(
    candidates: list[DomainCandidate],
    *,
    max_total: int = MAX_DOMAIN_CANDIDATES_FOR_LANES,
    source_host_domains: frozenset[str] | None = None,
) -> list[DomainCandidate]:
    """
    F214: Rank and bound domain candidates for lane planner input.

    Ranking priority (highest first):
      1. body-extracted domains (confidence 0.7, likely target IOCs)
      2. title-extracted domains
      3. url-extracted domains (may include source infrastructure)
      4. source_host_only candidates (deprioritized unless only option)

    Source-host filtering:
      - Domains that appear ONLY in source_url hostname (not in body/text)
        are flagged as source_host_only and ranked last.
      - This prevents krebsonsecurity.com from becoming a target candidate
        when it appears only as a source URL.

    Args:
        candidates:       List of DomainCandidate to rank.
        max_total:        Maximum candidates to return.
        source_host_domains: Optional frozenset of domains that appear ONLY as
                          source URL hostnames (will be ranked last).

    Returns:
        Bounded, ranked list of candidates (top max_total).
    """
    if not candidates:
        return []

    # Separate source_host_only candidates
    source_host: list[DomainCandidate] = []
    others: list[DomainCandidate] = []

    source_host_set = source_host_domains or frozenset()

    for c in candidates:
        if c.domain in source_host_set:
            source_host.append(c)
        else:
            others.append(c)

    # Sort each group by confidence desc, then seen_count desc
    def _sort_key(c: DomainCandidate) -> tuple:
        # source_field priority: body=0, title=1, url=2 → negate so body ranks first with reverse=True
        field_order = {"body": 0, "title": 1, "url": 2}
        field_prio = field_order.get(c.source_field, 3)
        return (c.confidence, c.seen_count, -field_prio)

    others.sort(key=_sort_key, reverse=True)
    source_host.sort(key=_sort_key, reverse=True)

    # Combine: others first, then source_host_only
    ranked = others[:max_total]
    remaining = max_total - len(ranked)
    if remaining > 0:
        ranked.extend(source_host[:remaining])

    return ranked


def filter_source_host_only(
    candidates: list[DomainCandidate],
    source_url: str,
) -> tuple[list[DomainCandidate], frozenset[str]]:
    """
    F214: Filter candidates that appear ONLY in source URL hostname.

    Args:
        candidates:  Candidates extracted from text body + url.
        source_url:  The source URL whose hostname to check.

    Returns:
        (filtered_candidates, source_host_domains):
          - filtered_candidates: candidates with source_host_only removed
          - source_host_domains: frozenset of domains that appeared ONLY in source URL
    """
    if not candidates:
        return [], frozenset()

    hostname = _extract_hostname(source_url)
    if not hostname:
        return list(candidates), frozenset()

    normalized_host = hostname.lower()

    # Separate candidates that appear in body vs only in url
    body_domains: set[str] = set()
    url_only_domains: set[str] = set()

    for c in candidates:
        if c.source_field == "url" and c.domain == normalized_host:
            url_only_domains.add(c.domain)
        else:
            body_domains.add(c.domain)

    # Domains that are ONLY in source URL (not in body/text) are source_host_only
    source_host_only = url_only_domains - body_domains

    filtered = [c for c in candidates if c.domain not in source_host_only]

    return filtered, frozenset(source_host_only)


# ---------------------------------------------------------------------------
# F214: Domain Candidate Extraction
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# F214: Domain Candidate Extraction Helpers
# ---------------------------------------------------------------------------

# Defang normalization — order matters (most specific first)
_DEFANG_PATTERNS: tuple[tuple[str, str], ...] = (
    # Square-bracket defang: example[.]com → example.com
    ("[.]", "."),
    # Parenthesis defang: example(.)com → example.com
    ("(.)", "."),
    # hxxp:// scheme defang: hxxp://evil.com → http://evil.com
    ("hxxp://", "http://"),
    ("hxxps://", "https://"),
    ("hXXp://", "http://"),
    ("hXXPs://", "https://"),
    # Single-bracket defang variants (less common)
    ("[dot]", "."),
    ("(dot)", "."),
)


def _normalize_defanged_text(text: str) -> str:
    """
    F214: Normalize defanged text before domain extraction.

    Strips obfuscation markers so regex can match the full domain.
    Operates on the whole text to handle mixed content.
    """
    result = text
    for pattern, replacement in _DEFANG_PATTERNS:
        result = result.replace(pattern, replacement)
    return result


def _is_ip_literal(domain: str) -> bool:
    """F214: Return True if domain is an IP address literal (IPv4 or IPv6)."""
    if not domain:
        return False
    # IPv4: dotted quad
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", domain):
        return True
    # IPv6: bracket form or plain
    if ":" in domain:
        return True
    return False


def _is_valid_domain_candidate(domain: str) -> bool:
    """
    F214: Validate domain candidate has a proper FQDN structure.

    Rejects:
    - Empty strings or too short (< 3 chars)
    - Single labels without dots
    - Two-label fragments where first label is short/no-digit AND second is a word
      (e.g. "c2.bad" from "c2.bad actor[.]com" — "bad" is a word, not a TLD)
    - Three-label fragments where last label is a word-like fragment
      (e.g. "leak.lockbit-example" from broken "leak.lockbit-example[.]test")
    """
    if not domain or len(domain) < 3:
        return False
    parts = domain.split(".")
    if len(parts) < 2:
        return False

    _WORD_LIKE_TLDS: frozenset[str] = frozenset({
        "bad", "actor", "leak", "lockbit", "example", "link",
        "data", "info", "site", "host",
    })

    if len(parts) == 2:
        first, second = parts
        second_is_word_like = second in _WORD_LIKE_TLDS
        first_is_short = len(first) <= 4
        # Reject: short first label (with or without digit) paired with word-like second
        # "c2.bad" (bad=word), "actor.com" (actor=word) — not real FQDNs
        if first_is_short and second_is_word_like:
            return False

    if len(parts) == 3:
        # Reject "X-Y.Z" patterns where middle has hyphen AND last is a known word fragment
        # (not a real TLD) — signals broken defang like "leak.lockbit-example[.]test"
        # We intentionally do NOT reject short TLDs like "test" here since they are real DNS TLDs
        mid = parts[1]
        last = parts[-1]
        mid_has_hyphen = "-" in mid
        last_is_word_like = last in _WORD_LIKE_TLDS
        if mid_has_hyphen and last_is_word_like:
            return False

    return True


# Matches domains: example.com, foo.bar.baz.io
# NOTE: Does NOT handle defanged [.] markers — apply _normalize_defanged_text first
_DEDUP_DOMAIN_RE = re.compile(
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}"
)

# Matches URLs: http://..., https://..., www.example.com/path
_URL_PREFIX_RE = re.compile(
    r"https?://|[a-zA-Z][a-zA-Z0-9+.-]*://|www\."
)


@dataclass(frozen=True)
class DomainCandidate:
    """
    F214: Domain candidate extracted from feed/public findings text.

    Fields:
        domain:        Normalized lower-case domain (or IP address)
        source_family: "PUBLIC" | "FEED"
        source_field:  "body" | "title" | "url"
        confidence:    Extraction confidence [0.0, 1.0]
        reason:        Why this was extracted
        seen_count:    How many findings mentioned this domain
        sample_context: Bounded text snippet where domain appeared (max 200 chars)
    """

    domain: str
    source_family: str  # PUBLIC | FEED
    source_field: str  # body | title | url
    confidence: float
    reason: str
    seen_count: int = 1
    sample_context: str = ""


def extract_domain_candidates_from_text(
    text: str,
    source_url: str | None = None,
    source_family: str = FAMILY_PUBLIC,
    min_confidence: float = 0.3,
) -> list[DomainCandidate]:
    """
    F214: Extract domain and URL hostname candidates from arbitrary text.

    No external dependencies — uses only stdlib urllib.parse and regex.

    Normalization pipeline:
      1. Normalize defanged markers ([.], (.), hxxp://, etc.) on whole text
      2. Run domain regex on normalized text
      3. Validate each candidate with _is_valid_domain_candidate
      4. Deduplicate by normalized domain + source_field

    Args:
        text:           Text to scan (body content, title, etc.)
        source_url:     Optional source URL for hostname extraction
        source_family:  "PUBLIC" or "FEED" for ledger attribution
        min_confidence: Minimum confidence threshold (0.0–1.0)

    Returns:
        List of DomainCandidate (may be empty).
        Deduplicated by normalized domain (first-seen per field wins).
    """
    if not text or not isinstance(text, str):
        return []

    # Step 1: normalize defanged markers so regex can match full domains
    normalized_text = _normalize_defanged_text(text)

    seen: dict[str, DomainCandidate] = {}  # key = f"{domain}|{source_field}"

    for match in _DEDUP_DOMAIN_RE.finditer(normalized_text):
        raw = match.group(0)
        domain = raw.lower()

        # Step 2: validate FQDN structure (rejects fragments like "c2.bad")
        if not _is_valid_domain_candidate(domain):
            continue

        # Step 3: reject .onion explicitly (can't CT/DOH-query TOR)
        if domain.endswith(".onion"):
            continue

        # Skip government/educational domains
        if ".gov" in domain or ".edu" in domain:
            continue

        # Skip IP literals in body (IP candidates handled separately)
        if _is_ip_literal(domain):
            continue

        # Get surrounding context from original text (±50 chars)
        # Map match position back to original text (defanging may shift indices)
        orig_start = match.start()
        orig_end = match.end()
        start = max(0, orig_start - 50)
        end = min(len(text), orig_end + 50)
        context = text[start:end]
        if len(context) > 200:
            context = context[:200]

        reason = "text_domain_match"
        key = f"{domain}|body"
        if key not in seen:
            seen[key] = DomainCandidate(
                domain=domain,
                source_family=source_family,
                source_field="body",
                confidence=0.7,
                reason=reason,
                seen_count=1,
                sample_context=context,
            )

    # ── 2. Extract from source URL if provided ──────────────────────────────
    if source_url:
        hostname = _extract_hostname(source_url)
        if hostname:
            normalized = hostname.lower()
            key = f"{normalized}|url"
            if key not in seen and normalized:
                seen[key] = DomainCandidate(
                    domain=normalized,
                    source_family=source_family,
                    source_field="url",
                    confidence=0.9,
                    reason="source_url_hostname",
                    seen_count=1,
                    sample_context=source_url[:200],
                )

    # Build result list
    result: list[DomainCandidate] = list(seen.values())

    # Filter by confidence
    result = [c for c in result if c.confidence >= min_confidence]
    return result


def _extract_hostname(url: str) -> str:
    """Extract hostname from URL using stdlib only. Handles defanged hxxp:// variants."""
    if not url:
        return ""
    try:
        # Normalize defanged scheme markers first (hxxp:// → http://)
        normalized = _normalize_defanged_text(url)
        from urllib.parse import urlparse
        if "://" in normalized:
            parsed = urlparse(normalized)
            hostname = parsed.hostname or ""
            if hostname.startswith("www."):
                hostname = hostname[4:]
            return hostname
        # Bare domain-like string — clean it
        clean = normalized.lstrip("htps:/").lstrip("//")
        slash_idx = clean.find("/")
        if slash_idx > 0:
            clean = clean[:slash_idx]
        return clean
    except Exception:
        return ""


def extract_domain_candidates_from_finding(
    finding: Any,
    source_family: str = FAMILY_PUBLIC,
) -> list[DomainCandidate]:
    """
    F214: Extract domain candidates from a CanonicalFinding-like object.

    Scans: finding.payload_text, finding.query (as URL), source_url from provenance.

    Args:
        finding:  CanonicalFinding or dict with payload_text / query fields
        source_family: "PUBLIC" or "FEED"

    Returns:
        List of DomainCandidate, deduplicated.
    """
    if not finding:
        return []

    seen: dict[str, DomainCandidate] = {}

    # ── 1. payload_text ──────────────────────────────────────────────────────
    payload = getattr(finding, "payload_text", None) or ""
    if isinstance(payload, str) and payload:
        for c in extract_domain_candidates_from_text(payload, source_family=source_family):
            key = f"{c.domain}|{c.source_field}"
            if key not in seen:
                seen[key] = c
            else:
                # Increment seen_count
                existing = seen[key]
                seen[key] = DomainCandidate(
                    domain=existing.domain,
                    source_family=existing.source_family,
                    source_field=existing.source_field,
                    confidence=existing.confidence,
                    reason=existing.reason,
                    seen_count=existing.seen_count + 1,
                    sample_context=existing.sample_context,
                )

    # ── 2. query field as URL / domain ───────────────────────────────────────
    query = getattr(finding, "query", None) or ""
    if isinstance(query, str) and query:
        # Check if query is itself a URL or domain
        if _URL_PREFIX_RE.search(query) or _DEDUP_DOMAIN_RE.search(query):
            hostname = _extract_hostname(query)
            if hostname:
                normalized = hostname.lower()
                key = f"{normalized}|url"
                if key not in seen:
                    seen[key] = DomainCandidate(
                        domain=normalized,
                        source_family=source_family,
                        source_field="url",
                        confidence=0.9,
                        reason="query_as_url",
                        seen_count=1,
                        sample_context=query[:200],
                    )

    # ── 3. provenance tuple for source URL ────────────────────────────────────
    prov = getattr(finding, "provenance", None) or ()
    if isinstance(prov, (list, tuple)) and len(prov) > 0:
        source_url = str(prov[0]) if prov else ""
        if source_url and ("://" in source_url or _DEDUP_DOMAIN_RE.search(source_url)):
            hostname = _extract_hostname(source_url)
            if hostname:
                normalized = hostname.lower()
                key = f"{normalized}|url"
                if key not in seen:
                    seen[key] = DomainCandidate(
                        domain=normalized,
                        source_family=source_family,
                        source_field="url",
                        confidence=0.8,
                        reason="provenance_url",
                        seen_count=1,
                        sample_context=source_url[:200],
                    )

    return list(seen.values())


def compute_lane_eligibility(
    candidates: list[DomainCandidate],
) -> dict[str, bool]:
    """
    F214: Compute lane eligibility from domain candidates.

    Returns dict:
        ct:           CT lane eligible if any domain candidate exists
                      (.onion excluded — TOR cannot be queried via CT)
        doh:          DOH lane eligible if any domain candidate exists
                      (.onion excluded — DOH does not resolve .onion)
        wayback:      WAYBACK lane eligible if any candidates exist
        passive_dns:  PASSIVE_DNS lane eligible if any domain candidates exist
    """
    has_domain = any(
        c.domain and not c.domain[0].isdigit() and not c.domain.endswith(".onion")
        for c in candidates
    )
    has_ip = any(c.domain[0].isdigit() for c in candidates if c.domain)
    has_any = len(candidates) > 0

    return {
        "ct": bool(has_domain),
        "doh": bool(has_domain),
        "wayback": bool(has_any),
        "passive_dns": bool(has_domain or has_ip),
    }
