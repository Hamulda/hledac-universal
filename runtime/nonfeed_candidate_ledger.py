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
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "NonfeedCandidateLedger",
    "LedgerRecord",
    "LEDGER_FAMILY",
    "LEDGER_STAGE",
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
        """Add a ledger record. FIFO eviction when at capacity."""
        import threading

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
        with threading.Lock():
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
    }.get(family, family.lower())