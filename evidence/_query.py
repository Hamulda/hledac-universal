"""
evidence/_query.py — Evidence Query for event retrieval and verification.

Read path: queries, chain verification, forensic analysis, retrospectives.

Architecture (Sprint Split-Brain):
- EvidenceWriter: Write path (create_event, persist, chain hash)
- EvidenceQuery: Read path (get, query, verify)
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import msgspec
import orjson
from core import aclose

logger = logging.getLogger(__name__)

# ─── EvidenceQuery ───────────────────────────────────────────────────────────────


class EvidenceQuery:
    """
    Read path for evidence events.

    Sprint Split-Brain: Extracted from EvidenceLog to isolate
    read path from write path. Enables independent testing and
    query-only workflows.
    """

    __slots__ = (
        '_events', '_index', '_run_id', '_total_count',
        '_chain_head', '_genesis_hash', '_frozen',
    )

    def __init__(
        self,
        run_id: str,
        events: list | None = None,
        chain_head: str | None = None,
        genesis_hash: str | None = None,
    ) -> None:
        self._events: deque = deque(events or [], maxlen=10000)
        self._index: dict[str, int] = {}
        self._run_id = run_id
        self._total_count = len(self._events)
        self._chain_head = chain_head
        self._genesis_hash = genesis_hash or ''
        self._frozen = True
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        """Rebuild event_id index."""
        self._index.clear()
        for i, event in enumerate(self._events):
            self._index[event.event_id] = i

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def total_count(self) -> int:
        return self._total_count

    @property
    def chain_head(self) -> str | None:
        return self._chain_head

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def get(self, index: int):
        """Get event by index."""
        if 0 <= index < len(self._events):
            return self._events[index]
        return None

    def get_by_id(self, event_id: str):
        """Get event by event_id."""
        idx = self._index.get(event_id)
        if idx is not None:
            return self._events[idx]
        return None

    def query(
        self,
        event_type: str | None = None,
        min_confidence: float = 0.0,
        after_timestamp: datetime | None = None,
        before_timestamp: datetime | None = None,
        limit: int | None = None,
    ) -> list:
        """Query events by criteria."""
        results = []
        for event in self._events:
            if event_type and event.event_type != event_type:
                continue
            if event.confidence < min_confidence:
                continue
            if after_timestamp and event.timestamp < after_timestamp.timestamp():
                continue
            if before_timestamp and event.timestamp > before_timestamp.timestamp():
                continue
            results.append(event)
            if limit and len(results) >= limit:
                break
        return results

    def get_summary_lines(self, last_n: int = 10) -> Iterator[str]:
        """Yield summary lines for last N events."""
        for event in list(self._events)[-last_n:]:
            ts = datetime.fromtimestamp(event.timestamp).strftime('%H:%M:%S')
            summary = self._summarize_payload(event.payload)
            yield f"[{ts}] {event.event_type}: {summary}"

    def get_summary(self, last_n: int = 10) -> str:
        """Get summary of last N events."""
        return '\n'.join(self.get_summary_lines(last_n))

    @staticmethod
    def _summarize_payload(payload: dict[str, Any] | None, max_length: int = 60) -> str:
        """Summarize payload for display."""
        if not payload:
            return ''
        kind = payload.get('kind', 'unknown')
        if kind == 'claim':
            text = payload.get('claim', {}).get('text', '')[:max_length]
            return f"claim: {text}"
        if kind == 'decision':
            summary = payload.get('summary', {})[:max_length]
            return f"decision: {summary}"
        if kind == 'evidence_packet':
            source = payload.get('source', 'unknown')
            return f"evidence: {source}"
        return str(payload)[:max_length]

    def to_jsonl(self, path: Path | None = None) -> str | None:
        """Export events to JSONL."""
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'wb') as f:
                for event in self._events:
                    f.write(event.to_jsonl_line().encode('utf-8') + b'\n')
            return None
        return '\n'.join(e.to_jsonl_line() for e in self._events)

    @classmethod
    def from_jsonl(cls, path: Path, run_id: str | None = None) -> EvidenceQuery:
        """Load events from JSONL."""
        from hledac.universal.evidence._writer import EvidenceEvent
        events = []
        chain_head = None
        total_count = 0

        try:
            with open(path, 'rb') as f:
                for line in f:
                    try:
                        data = orjson.loads(line)
                        event = EvidenceEvent.from_dict(data)
                        events.append(event)
                        chain_head = event.chain_hash
                        total_count += 1
                    except Exception:
                        continue
        except Exception:
            pass

        return cls(
            run_id=run_id or 'unknown',
            events=list(events),
            chain_head=chain_head,
        )

    def verify_all(self) -> dict[str, Any]:
        """Verify integrity of all events."""
        valid = 0
        invalid = 0
        broken_chains = []

        prev_hash = None
        for event in self._events:
            if not event.verify_integrity():
                invalid += 1
            else:
                valid += 1

            expected_chain = event._compute_chain_hash(prev_hash, event.content_hash, event.event_id)
            if expected_chain != event.chain_hash:
                broken_chains.append(event.event_id)

            prev_hash = event.chain_hash

        return {
            'total': len(self._events),
            'valid': valid,
            'invalid': invalid,
            'broken_chains': broken_chains,
            'verified': invalid == 0 and len(broken_chains) == 0,
        }

    def get_chain(self, event_id: str) -> list:
        """Get chain of events leading to given event."""
        chain = []
        visited = set()

        def traverse(eid: str):
            if eid in visited:
                return
            visited.add(eid)
            event = self.get_by_id(eid)
            if not event:
                return
            for source_id in event.source_ids:
                traverse(source_id)
            chain.append(event)

        traverse(event_id)
        return chain

    def get_statistics(self) -> dict[str, Any]:
        """Get event statistics."""
        from collections import Counter
        event_types = Counter(e.event_type for e in self._events)
        confidences = [e.confidence for e in self._events]

        return {
            'total_events': len(self._events),
            'event_types': dict(event_types),
            'avg_confidence': sum(confidences) / len(confidences) if confidences else 0,
            'low_confidence_count': sum(1 for c in confidences if c < 0.7),
        }

    def get_sprint_health_summary(self) -> dict[str, Any]:
        """Get sprint health summary."""
        stats = self.get_statistics()
        total = stats['total_events']
        avg_conf = stats['avg_confidence']
        low_conf = stats['low_confidence_count']

        error_count = stats['event_types'].get('error', 0)
        error_rate = error_count / total if total > 0 else 0

        health = 'healthy'
        if error_rate > 0.2:
            health = 'noisy'
        elif low_conf > total * 0.3:
            health = 'low_confidence'

        return {
            'total_events': total,
            'health': health,
            'error_rate_pct': error_rate * 100,
            'low_conf_pressure': 'high' if low_conf > total * 0.3 else 'normal',
            'avg_confidence': avg_conf,
            'decision_count': stats['event_types'].get('decision', 0),
        }

    def get_retrospective_bundle(self) -> dict[str, Any]:
        """Single-call retrospective seam for private sprint retro."""
        health = self.get_sprint_health_summary()
        total = health['total_events']

        verdict = 'confident'
        if health['health'] == 'noisy':
            verdict = 'uncertain'
        elif health['low_conf_pressure'] == 'high':
            verdict = 'low_confidence'

        return {
            'run_id': self._run_id,
            'total_events': total,
            'verdict': verdict,
            'health': health['health'],
            'operator_retro_brief': f"Sprint processed {total} events with {verdict} verdict.",
            'continue_reason': 'healthy sprint, no pivot needed' if health['health'] == 'healthy' else 'review needed',
            'trust_level': 'high' if health['health'] == 'healthy' else 'moderate',
            'biggest_win': '',
            'retro_priority': 'review errors' if health['health'] == 'noisy' else 'continue monitoring',
        }
