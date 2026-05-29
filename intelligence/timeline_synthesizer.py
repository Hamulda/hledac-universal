"""
Timeline Synthesizer — Sprint F202E
===================================

Canonical timeline builder that synthesizes temporal events from:
  - CT (Certificate Transparency) timestamps
  - Archive observations (Wayback, Archive.today)
  - Document metadata timestamps
  - Finding timestamps

Produces bounded explainable timeline (max 200 events per sprint export).
Invalid timestamps are skipped fail-soft.

Timeline events are rendered in export via sprint_markdown_reporter.py
and optionally persisted as derived findings via async_ingest_findings_batch.

M1 8GB safe: pure Python, no model load, bounded O(n log n) sort.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_TIMELINE_EVENTS: int = 200
MAX_EVENT_AGE_DAYS: int = 365 * 5  # 5 years max span


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class TimelineEvent:
    """
    A single timestamped event in the synthesized timeline.

    Fields:
        ts:             Unix timestamp (float)
        event_type:     Category of event (ct_observed, archive_snapshot,
                        document_created, finding_accepted, etc.)
        source:         Which system produced this event
                        (ct_log, wayback, archive_today, metadata_extractor, etc.)
        description:    Human-readable description of the event
        entity_id:     Associated entity identifier (URL, domain, hash, etc.)
        confidence:     Confidence score [0.0, 1.0]
        evidence:       List of evidence pointers (URLs, file refs, IDs)
    """
    ts: float
    event_type: str
    source: str
    description: str
    entity_id: str = ""
    confidence: float = 1.0
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "event_type": self.event_type,
            "source": self.source,
            "description": self.description,
            "entity_id": self.entity_id,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass
class TimelineMetadata:
    """
    Metadata about the synthesized timeline.
    """
    total_events: int = 0
    oldest_event_ts: float | None = None
    newest_event_ts: float | None = None
    event_types: dict[str, int] = field(default_factory=dict)
    sources: dict[str, int] = field(default_factory=dict)


@dataclass
class SynthesizedTimeline:
    """
    Complete synthesized timeline with events and metadata.
    """
    events: list[TimelineEvent]
    metadata: TimelineMetadata
    entity_id: str  # primary entity this timeline is about

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "metadata": {
                "total_events": self.metadata.total_events,
                "oldest_event_ts": self.metadata.oldest_event_ts,
                "newest_event_ts": self.metadata.newest_event_ts,
                "event_types": self.metadata.event_types,
                "sources": self.metadata.sources,
            },
            "events": [e.to_dict() for e in self.events],
        }


# ── Timeline Synthesizer ─────────────────────────────────────────────────────


class TimelineSynthesizer:
    """
    Canonical timeline builder for Sprint F202E.

    Accepts events from multiple temporal sources and synthesizes them into
    a bounded, sorted, explainable timeline.

    Sources supported:
      - CT timestamps (from Certificate Transparency findings)
      - Archive observations (Wayback, Archive.today snapshots)
      - Document metadata (PDF, Office, image timestamps)
      - Finding timestamps (when findings were accepted)

    Bounds:
      - MAX_TIMELINE_EVENTS=200 cap applied to output events
      - Invalid timestamps (NaN, negative, far future) are skipped fail-soft
      - Events older than MAX_EVENT_AGE_DAYS are excluded

    Usage:
        synthesizer = TimelineSynthesizer()
        synthesizer.add_ct_events(findings)
        synthesizer.add_archive_events(archive_results)
        synthesizer.add_document_timestamps(doc_metadata)
        timeline = synthesizer.build(query)
    """

    def __init__(self) -> None:
        self._events: list[TimelineEvent] = []
        self._stats: dict[str, int] = {
            "ct_events_added": 0,
            "archive_events_added": 0,
            "document_events_added": 0,
            "finding_events_added": 0,
            "invalid_skipped": 0,
            "events_output": 0,
        }

    # ── Event Sources ─────────────────────────────────────────────────────────

    def add_ct_events(self, findings: list[Any]) -> int:
        """
        Add Certificate Transparency timestamp events from findings.

        For each CT finding, creates an event with:
          - event_type: "ct_observed"
          - source: "ct_log"
          - entity_id: domain or cert subject
          - description: certificate observed for domain

        Args:
            findings: List of CanonicalFinding with source_type="ct_log"

        Returns:
            Number of events added
        """
        count = 0
        for f in findings:
            try:
                src = getattr(f, "source_type", "") or ""
                if src != "ct_log":
                    continue

                ts = getattr(f, "ts", 0.0) or 0.0
                if not self._is_valid_timestamp(ts):
                    self._stats["invalid_skipped"] += 1
                    continue

                fid = getattr(f, "finding_id", "") or ""
                query = getattr(f, "query", "") or ""
                confidence = getattr(f, "confidence", 0.5) or 0.5

                # Extract domain from query
                entity_id = query if query else fid[:16]

                event = TimelineEvent(
                    ts=ts,
                    event_type="ct_observed",
                    source="ct_log",
                    description=f"CT cert observed: {entity_id}",
                    entity_id=entity_id,
                    confidence=confidence,
                    evidence=[f"finding:{fid}"] if fid else [],
                )
                self._events.append(event)
                count += 1
            except Exception:
                self._stats["invalid_skipped"] += 1

        self._stats["ct_events_added"] += count
        return count

    def add_archive_events(self, archive_results: list[Any]) -> int:
        """
        Add archive snapshot events from archive discovery results.

        For each archive result, creates an event with:
          - event_type: "archive_snapshot"
          - source: the specific archive source (wayback, archive_today, etc.)
          - entity_id: URL that was archived
          - description: archived snapshot title/timestamp

        Args:
            archive_results: List of ArchiveResult or ArchivedVersion objects

        Returns:
            Number of events added
        """
        count = 0
        for ar in archive_results:
            try:
                # Handle both ArchiveResult and ArchivedVersion
                if hasattr(ar, "timestamp"):
                    ts = self._to_timestamp(getattr(ar, "timestamp", None))
                elif hasattr(ar, "ts"):
                    ts = getattr(ar, "ts", 0.0) or 0.0
                else:
                    ts = 0.0

                if not self._is_valid_timestamp(ts):
                    self._stats["invalid_skipped"] += 1
                    continue

                url = getattr(ar, "url", "") or ""
                source = getattr(ar, "source", "unknown") or "unknown"
                title = getattr(ar, "title", "") or url

                event = TimelineEvent(
                    ts=ts,
                    event_type="archive_snapshot",
                    source=source,
                    description=f"Archive snapshot: {title[:60]}",
                    entity_id=url[:128],
                    confidence=0.7,  # archive events have moderate confidence
                    evidence=[url] if url else [],
                )
                self._events.append(event)
                count += 1
            except Exception:
                self._stats["invalid_skipped"] += 1

        self._stats["archive_events_added"] += count
        return count

    def add_document_timestamps(self, doc_metadata: list[Any]) -> int:
        """
        Add document creation/modification timestamp events.

        For each document metadata entry, creates an event with:
          - event_type: "document_dated"
          - source: "metadata_extractor"
          - entity_id: document hash or path
          - description: document type and creation date

        Args:
            doc_metadata: List of DocumentMetadata objects

        Returns:
            Number of events added
        """
        count = 0
        for doc in doc_metadata:
            try:
                # Extract creation timestamp from document metadata
                ts = 0.0
                created = getattr(doc, "created", None)
                if created is not None:
                    ts = self._to_timestamp(created)
                else:
                    modified = getattr(doc, "modified", None)
                    if modified is not None:
                        ts = self._to_timestamp(modified)

                if not self._is_valid_timestamp(ts):
                    self._stats["invalid_skipped"] += 1
                    continue

                doc_type = getattr(doc, "doc_type", "") or "document"
                path = getattr(doc, "path", "") or getattr(doc, "file_path", "") or ""

                event = TimelineEvent(
                    ts=ts,
                    event_type="document_dated",
                    source="metadata_extractor",
                    description=f"Document: {doc_type} created",
                    entity_id=path[:128] if path else doc_type,
                    confidence=0.8,  # document timestamps are reliable
                    evidence=[path] if path else [],
                )
                self._events.append(event)
                count += 1
            except Exception:
                self._stats["invalid_skipped"] += 1

        self._stats["document_events_added"] += count
        return count

    def add_finding_events(self, findings: list[Any], source_label: str = "finding") -> int:
        """
        Add finding acceptance timestamp events.

        For each finding, creates an event with:
          - event_type: "finding_accepted"
          - source: source_label
          - entity_id: finding_id
          - description: source_type and query

        Args:
            findings: List of CanonicalFinding objects
            source_label: Label for this finding source (default: "finding")

        Returns:
            Number of events added
        """
        count = 0
        for f in findings:
            try:
                ts = getattr(f, "ts", 0.0) or 0.0
                if not self._is_valid_timestamp(ts):
                    self._stats["invalid_skipped"] += 1
                    continue

                fid = getattr(f, "finding_id", "") or ""
                src_type = getattr(f, "source_type", "") or "unknown"
                query = getattr(f, "query", "") or ""

                event = TimelineEvent(
                    ts=ts,
                    event_type="finding_accepted",
                    source=source_label,
                    description=f"{src_type} finding: {query[:50]}",
                    entity_id=fid[:32] if fid else "",
                    confidence=getattr(f, "confidence", 0.5) or 0.5,
                    evidence=[f"finding:{fid}"] if fid else [],
                )
                self._events.append(event)
                count += 1
            except Exception:
                self._stats["invalid_skipped"] += 1

        self._stats["finding_events_added"] += count
        return count

    # ── Timeline Build ────────────────────────────────────────────────────────

    def build(self, entity_id: str = "") -> SynthesizedTimeline:
        """
        Build the synthesized timeline from all added events.

        Applies:
          - Timestamp validation (skip invalid)
          - Age filtering (exclude events older than MAX_EVENT_AGE_DAYS)
          - Sorting by timestamp (ascending)
          - Bounded cap at MAX_TIMELINE_EVENTS

        Args:
            entity_id: Primary entity this timeline is about

        Returns:
            SynthesizedTimeline with sorted, bounded events
        """
        now = time.time()
        cutoff = now - (MAX_EVENT_AGE_DAYS * 86400)

        # Filter and validate
        valid_events: list[TimelineEvent] = []
        for event in self._events:
            if event.ts < cutoff:
                self._stats["invalid_skipped"] += 1
                continue
            if not self._is_valid_timestamp(event.ts):
                self._stats["invalid_skipped"] += 1
                continue
            valid_events.append(event)

        # Sort by timestamp ascending
        valid_events.sort(key=lambda e: e.ts)

        # Cap at MAX_TIMELINE_EVENTS
        if len(valid_events) > MAX_TIMELINE_EVENTS:
            valid_events = valid_events[:MAX_TIMELINE_EVENTS]

        self._stats["events_output"] = len(valid_events)

        # Build metadata
        oldest = valid_events[0].ts if valid_events else None
        newest = valid_events[-1].ts if valid_events else None

        event_types: dict[str, int] = {}
        sources: dict[str, int] = {}
        for e in valid_events:
            event_types[e.event_type] = event_types.get(e.event_type, 0) + 1
            sources[e.source] = sources.get(e.source, 0) + 1

        metadata = TimelineMetadata(
            total_events=len(valid_events),
            oldest_event_ts=oldest,
            newest_event_ts=newest,
            event_types=event_types,
            sources=sources,
        )

        return SynthesizedTimeline(
            events=valid_events,
            metadata=metadata,
            entity_id=entity_id,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_valid_timestamp(ts: float) -> bool:
        """Check if timestamp is valid (not NaN, not negative, not far future)."""
        import math

        if ts <= 0:
            return False
        if math.isnan(ts) or math.isinf(ts):
            return False
        # Not more than 1 year in the future
        if ts > time.time() + 86400 * 365:
            return False
        return True

    @staticmethod
    def _to_timestamp(value: Any) -> float:
        """Convert various timestamp formats to float."""
        if isinstance(value, datetime):
            return value.timestamp()
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt.timestamp()
            except Exception:
                return 0.0
        return 0.0

    # ── Stats ────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict[str, int]:
        """Return synthesizer statistics."""
        return self._stats.copy()

    def clear(self) -> None:
        """Clear all events and reset stats."""
        self._events.clear()
        self._stats = dict.fromkeys(self._stats, 0)


# ── Factory ───────────────────────────────────────────────────────────────────

def create_timeline_synthesizer() -> TimelineSynthesizer:
    """Factory to create TimelineSynthesizer."""
    return TimelineSynthesizer()


__all__ = [
    "TimelineSynthesizer",
    "TimelineEvent",
    "TimelineMetadata",
    "SynthesizedTimeline",
    "create_timeline_synthesizer",
    "MAX_TIMELINE_EVENTS",
    "MAX_EVENT_AGE_DAYS",
]
