"""
Temporal Archaeologist Adapter — Sprint F202E
=============================================



Canonical adapter wrapping TemporalArchaeologist for the sprint pipeline.

Responsibilities:
  1. Accept findings and archive results from sprint pipeline
  2. Convert to timeline events via TimelineSynthesizer
  3. Produce derived timeline CanonicalFinding objects
  4. Optionally persist timeline through async_ingest_findings_batch

Role: advisory sidecar, NOT the main write path.
Derived findings go through async_ingest_findings_batch() like any finding.

M1 8GB CEILING:
  - MAX_TIMELINE_EVENTS=200 events per sprint (hard cap)
  - No model load (pure Python timestamp processing)
  - All methods fail-soft: errors never crash the sprint
"""
import logging
import time
from dataclasses import dataclass
import msgspec
from typing import Any
from hledac.universal.utils.msgspec_json import dumps_str as _msgspec_dumps_str
from _core import aclose
logger = logging.getLogger(__name__)
MAX_TIMELINE_FINDINGS: int = 20
try:
    from .temporal_archaeologist import ArchivedVersion, EntityTimeline, TemporalArchaeologist
    _TEMPORAL_AVAILABLE = True
except ImportError:
    _TEMPORAL_AVAILABLE = False
    TemporalArchaeologist = None
    ArchivedVersion = None
    EntityTimeline = None
try:
    from .timeline_synthesizer import SynthesizedTimeline, TimelineEvent, TimelineSynthesizer
    _SYNTHESIZER_AVAILABLE = True
except ImportError:
    _SYNTHESIZER_AVAILABLE = False
    TimelineSynthesizer = None
    SynthesizedTimeline = None
    TimelineEvent = None
try:
    from ..knowledge.duckdb_store import CanonicalFinding
except ImportError:
    CanonicalFinding = None

class TimelineFindingResult(msgspec.Struct, gc=False):
    """
    Result of timeline synthesis containing events and derived findings.
    """
    timeline: SynthesizedTimeline | None
    derived_findings: list[Any]
    stats: dict[str, int]

class TemporalArchaeologistAdapter:
    """
    Canonical adapter for TemporalArchaeologist in the sprint pipeline.

    Wraps TimelineSynthesizer with:
      - Multi-source event aggregation (CT, archive, document, findings)
      - Bounded timeline output (MAX_TIMELINE_EVENTS=200)
      - Conversion to CanonicalFinding for async_ingest_findings_batch()
      - M1 8GB memory management
      - Fail-soft: errors never crash the sprint

    Usage:
        adapter = TemporalArchaeologistAdapter()
        result = adapter.synthesize_timeline(findings, archive_results, doc_metadata)
        timeline = result.timeline
        derived = result.derived_findings
    """
    __slots__ = tuple(('_stats', '_synthesizer'))

    def __init__(self) -> None:
        self._synthesizer = TimelineSynthesizer() if _SYNTHESIZER_AVAILABLE else None
        self._stats: dict[str, int] = {'ct_events_added': 0, 'archive_events_added': 0, 'document_events_added': 0, 'finding_events_added': 0, 'findings_produced': 0, 'invalid_skipped': 0}

    def synthesize_timeline(self, ct_findings: list[Any] | None=None, archive_results: list[Any] | None=None, doc_metadata: list[Any] | None=None, entity_id: str='') -> TimelineFindingResult:
        """
        Synthesize a timeline from multiple source event types.

        Fails-soft: returns empty result on any error.

        Args:
            ct_findings:      CT log findings (source_type="ct_log")
            archive_results:  Archive discovery results
            doc_metadata:     Document metadata with timestamps
            entity_id:        Primary entity this timeline is about

        Returns:
            TimelineFindingResult with synthesized timeline and derived findings
        """
        if self._synthesizer is None:
            return TimelineFindingResult(timeline=None, derived_findings=[], stats=self._stats)
        try:
            self._synthesizer.clear()
            if ct_findings:
                count = self._synthesizer.add_ct_events(ct_findings)
                self._stats['ct_events_added'] += count
            if archive_results:
                count = self._synthesizer.add_archive_events(archive_results)
                self._stats['archive_events_added'] += count
            if doc_metadata:
                count = self._synthesizer.add_document_timestamps(doc_metadata)
                self._stats['document_events_added'] += count
            timeline = self._synthesizer.build(entity_id=entity_id)
            derived = self._to_derived_findings(timeline)
            return TimelineFindingResult(timeline=timeline, derived_findings=derived, stats=self._synthesizer.get_stats())
        except Exception as e:
            logger.debug(f'TemporalArchaeologistAdapter.synthesize_timeline error: {e}')
            return TimelineFindingResult(timeline=None, derived_findings=[], stats=self._stats)

    def add_finding_events(self, findings: list[Any], source_label: str='finding') -> int:
        """
        Add finding events to the current timeline.

        Args:
            findings:     List of CanonicalFinding objects
            source_label: Label for the source (e.g., "identity_stitching", "exposure")

        Returns:
            Number of events added
        """
        if self._synthesizer is None:
            return 0
        try:
            count = self._synthesizer.add_finding_events(findings, source_label)
            self._stats['finding_events_added'] += count
            return count
        except Exception:
            return 0

    def _to_derived_findings(self, timeline: SynthesizedTimeline) -> list[Any]:
        """
        Convert SynthesizedTimeline to list of CanonicalFinding.

        Each timeline becomes a derived finding with source_type="temporal_archaeology".
        Only produces findings if there are events in the timeline.

        Args:
            timeline: SynthesizedTimeline from synthesize_timeline

        Returns:
            List of CanonicalFinding objects (empty if CanonicalFinding unavailable)
        """
        if not timeline or not timeline.events:
            return []
        if CanonicalFinding is None:
            return []
        findings: list[Any] = []
        try:
            import json
            timeline_data = timeline.to_dict()
            payload_text = _msgspec_dumps_str(timeline_data, default=str)
            fid = f'timeline_{int(time.time() * 1000) % 1000000:06d}'
            finding = CanonicalFinding(finding_id=fid, query=f'Timeline: {timeline.entity_id}', source_type='temporal_archaeology', confidence=0.7, ts=time.time(), provenance=('temporal_archaeology',), payload_text=payload_text)
            findings.append(finding)
            self._stats['findings_produced'] = 1
        except Exception as e:
            logger.debug(f'TemporalArchaeologistAdapter._to_derived_findings error: {e}')
        return findings

    def get_stats(self) -> dict[str, int]:
        """Return adapter statistics."""
        return self._stats.copy()

    def clear(self) -> None:
        """Clear synthesizer state and reset stats."""
        if self._synthesizer:
            self._synthesizer.clear()
        self._stats = dict.fromkeys(self._stats, 0)

def create_temporal_archaeologist_adapter() -> TemporalArchaeologistAdapter:
    """Factory to create TemporalArchaeologistAdapter."""
    return TemporalArchaeologistAdapter()
__all__ = ['TemporalArchaeologistAdapter', 'TimelineFindingResult', 'create_temporal_archaeologist_adapter', 'MAX_TIMELINE_FINDINGS']