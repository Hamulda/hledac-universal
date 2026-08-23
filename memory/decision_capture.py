"""
Decision Capture — Automatic Decision Logging to EvidenceLog
============================================================

PEP 698: Auto-capture decisions from Hermes decision engine.

Key invariants:
- Decisions from `decide_next_action()` are auto-captured to EvidenceLog
- DecisionStore provides Haft-style DecisionRecord persistence
- Zero overhead when decision capture is disabled

Usage:
    from memory.decision_capture import DecisionCapture, DecisionStore

    # Wrap engine's decide_next_action
    capture = DecisionCapture(evidence_log)
    decision = await capture.decide_next_action(engine, context)

    # Or use DecisionStore for structured records
    store = DecisionStore(path)
    await store.record_decision(decision_record)
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import Enum
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DecisionKind(Enum):
    """Classification of decision types for auto-capture."""
    BANDIT = "bandit"
    PLAYBOOK = "playbook"
    BACKPRESSURE = "backpressure"
    DELTA = "delta"
    ALIGNMENT = "alignment"
    PRIMARY_CHASE = "primary_chase"
    DRIFT = "drift"


@dataclass
class DecisionRecord:
    """
    Structured decision record for DecisionStore.

    Compatible with Haft DecisionRecord when integrated.
    """
    decision_id: str
    timestamp: float
    kind: str  # DecisionKind.value
    action: str
    params: dict[str, Any]
    reasoning: str
    confidence: float
    complete: bool = False
    problem_statement: str | None = None
    options_considered: list[str] = field(default_factory=list)
    why_selected: str | None = None
    weakest_link: str | None = None
    rollback: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp,
            "kind": self.kind,
            "action": self.action,
            "params": self.params,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "complete": self.complete,
            "problem_statement": self.problem_statement,
            "options_considered": self.options_considered,
            "why_selected": self.why_selected,
            "weakest_link": self.weakest_link,
            "rollback": self.rollback,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionRecord:
        return cls(**data)


class DecisionCapture:
    """
    Wraps Hermes decision engine to auto-capture decisions to EvidenceLog.

    PEP 698: Fail-safe auto-capture — never raises, never blocks inference.
    """

    def __init__(
        self,
        evidence_log: Any | None = None,
        sample_rate: float = 1.0,
    ) -> None:
        """
        Initialize DecisionCapture.

        Args:
            evidence_log: EvidenceLog instance for event persistence
            sample_rate: 0.0-1.0, fraction of decisions to capture
        """
        self._evidence_log = evidence_log
        self._sample_rate = sample_rate
        self._decision_count = 0

    def _should_capture(self) -> bool:
        """Determine if this decision should be captured."""
        import random
        self._decision_count += 1
        if self._sample_rate >= 1.0:
            return True
        return random.random() < self._sample_rate

    async def decide_next_action(
        self,
        engine: Any,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Intercept decide_next_action and auto-capture to EvidenceLog.

        Args:
            engine: DeepHermes3Engine or compatible
            context: Decision context

        Returns:
            Decision dict from engine
        """
        # Call the actual decision engine
        try:
            decision = await engine.decide_next_action(context)
        except Exception as e:
            logger.warning(f"Decision engine failed: {e}")
            return {"action": "error", "params": {"error": str(e)}, "reasoning": "decision_failed", "complete": False}

        # Auto-capture to EvidenceLog
        if self._evidence_log is not None and self._should_capture():
            try:
                self._log_decision(decision, context)
            except Exception as e:
                logger.debug(f"Decision capture failed (non-fatal): {e}")

        return decision

    def _log_decision(self, decision: dict[str, Any], context: dict[str, Any]) -> None:
        """Log decision to EvidenceLog as a decision event."""
        import random

        # Determine decision kind from action
        kind = self._infer_kind(decision.get("action", ""))

        summary = {
            "action": decision.get("action"),
            "params": decision.get("params", {}),
            "reasoning": decision.get("reasoning", "")[:500],  # Truncate
            "complete": decision.get("complete", False),
        }

        reasons = [
            f"Step {context.get('step', 0)}/{context.get('max_steps', '?')}",
            f"Query: {context.get('query', '')[:100]}",
        ]

        refs: dict[str, list[str]] = {
            "evidence_ids": [],
            "cluster_ids": [],
            "url_hashes": [],
        }

        # Get history refs if available
        history = context.get("history", [])
        if history and isinstance(history[-1], dict):
            last_action = history[-1].get("action", "")
            if last_action:
                refs["evidence_ids"].append(f"history:{last_action}")

        confidence = decision.get("confidence", 1.0)

        try:
            self._evidence_log.create_decision_event(
                kind=kind,
                summary=summary,
                reasons=reasons,
                refs=refs,
                confidence=confidence,
            )
            logger.debug(f"Captured decision: {decision.get('action')} ({kind})")
        except Exception as e:
            logger.debug(f"EvidenceLog create_decision_event failed: {e}")

    def _infer_kind(self, action: str) -> str:
        """Infer decision kind from action name."""
        action_lower = action.lower()
        if "search" in action_lower or "google" in action_lower:
            return DecisionKind.PRIMARY_CHASE.value
        if "archive" in action_lower:
            return DecisionKind.DELTA.value
        if "synthesize" in action_lower or "complete" in action_lower:
            return DecisionKind.ALIGNMENT.value
        if "backpressure" in action_lower or "throttle" in action_lower:
            return DecisionKind.BACKPRESSURE.value
        if "bandit" in action_lower or "explore" in action_lower:
            return DecisionKind.BANDIT.value
        if "playbook" in action_lower or "rule" in action_lower:
            return DecisionKind.PLAYBOOK.value
        return DecisionKind.DRIFT.value


class DecisionStore:
    """
    Lightweight LMDB-backed store for structured DecisionRecords.

    Provides Haft-style DecisionRecord persistence without full Haft integration.
    When Haft is integrated, DecisionRecords can be exported to Haft format.

    Thread safety: Not thread-safe (same as MemoryManager).
    """

    MAX_KEYS_PER_SESSION = 10000
    DECISION_TTL_SECONDS = 30 * 24 * 3600  # 30 days

    def __init__(self, path: Path | str | None = None) -> None:
        """
        Initialize DecisionStore.

        Args:
            path: Directory for LMDB storage (None = in-memory only)
        """
        self._path = Path(path) if path else None
        self._lmdb_env: Any = None
        self._in_memory: dict[str, bytes] = {}
        self._initialized = False

        if self._path:
            self._path.mkdir(parents=True, exist_ok=True)
            self._init_lmdb()

    def _init_lmdb(self) -> None:
        """Initialize LMDB environment."""
        try:
            import lmdb
            lmdb_path = str(self._path / "decisions.lmdb")
            self._lmdb_env = lmdb.open(
                lmdb_path,
                max_dbs=4,
                map_size=10 * 1024 * 1024,  # 10MB
            )
            self._initialized = True
        except ImportError:
            logger.debug("LMDB not available, using in-memory store")
            self._initialized = False
        except Exception as e:
            logger.warning(f"LMDB init failed: {e}, using in-memory")
            self._initialized = False

    async def record_decision(self, record: DecisionRecord) -> str:
        """
        Record a decision to the store.

        Args:
            record: DecisionRecord to persist

        Returns:
            decision_id
        """
        key = f"dec:{record.decision_id}"
        value = self._serialize(record)

        if self._lmdb_env is not None:
            try:
                with self._lmdb_env.begin(write=True) as txn:
                    txn.put(key.encode(), value)
            except Exception as e:
                logger.warning(f"LMDB write failed: {e}")
                self._in_memory[key] = value
        else:
            self._in_memory[key] = value

        return record.decision_id

    async def get_decision(self, decision_id: str) -> DecisionRecord | None:
        """
        Retrieve a decision by ID.

        Args:
            decision_id: Decision ID

        Returns:
            DecisionRecord or None
        """
        key = f"dec:{decision_id}"

        if self._lmdb_env is not None:
            try:
                with self._lmdb_env.begin() as txn:
                    data = txn.get(key.encode())
                    if data:
                        return self._deserialize(data)
            except Exception as e:
                logger.debug(f"LMDB read failed: {e}")

        data = self._in_memory.get(key)
        if data:
            return self._deserialize(data)
        return None

    async def query_decisions(
        self,
        kind: str | None = None,
        action: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 100,
    ) -> list[DecisionRecord]:
        """
        Query decisions with filters.

        Args:
            kind: Filter by DecisionKind
            action: Filter by action name (prefix match)
            min_confidence: Minimum confidence threshold
            limit: Max results

        Returns:
            List of matching DecisionRecords
        """
        results: list[DecisionRecord] = []

        if self._lmdb_env is not None:
            try:
                with self._lmdb_env.begin() as txn:
                    cursor = txn.cursor()
                    for key, value in cursor:
                        try:
                            record = self._deserialize(value)
                            if self._matches_filter(record, kind, action, min_confidence):
                                results.append(record)
                                if len(results) >= limit:
                                    break
                        except Exception:
                            continue
            except Exception as e:
                logger.debug(f"LMDB query failed: {e}")

        # Also check in-memory
        for key, value in self._in_memory.items():
            try:
                record = self._deserialize(value)
                if self._matches_filter(record, kind, action, min_confidence):
                    if record not in results:
                        results.append(record)
                        if len(results) >= limit:
                            break
            except Exception:
                continue

        # Sort by timestamp descending
        results.sort(key=lambda r: r.timestamp, reverse=True)
        return results[:limit]

    def _matches_filter(
        self,
        record: DecisionRecord,
        kind: str | None,
        action: str | None,
        min_confidence: float,
    ) -> bool:
        """Check if record matches filters."""
        if kind and record.kind != kind:
            return False
        if action and not record.action.startswith(action):
            return False
        if record.confidence < min_confidence:
            return False
        return True

    def _serialize(self, record: DecisionRecord) -> bytes:
        """Serialize DecisionRecord to bytes."""
        try:
            import orjson
            return orjson.dumps(record.to_dict())
        except ImportError:
            import json
            return json.dumps(record.to_dict()).encode()

    def _deserialize(self, data: bytes) -> DecisionRecord:
        """Deserialize bytes to DecisionRecord."""
        try:
            import orjson
            return DecisionRecord.from_dict(orjson.loads(data))
        except ImportError:
            import json
            return DecisionRecord.from_dict(json.loads(data.decode()))

    async def close(self) -> None:
        """Close LMDB environment."""
        if self._lmdb_env is not None:
            try:
                self._lmdb_env.close()
            except Exception:
                pass
            self._lmdb_env = None

    def generate_decision_id(self) -> str:
        """Generate a unique decision ID."""
        return f"dec-{datetime.now(UTC).strftime('%Y%m%d')}-{int(time.time() * 1000)}"


# Global singleton for process-wide decision capture
_decision_capture: DecisionCapture | None = None
_decision_store: DecisionStore | None = None


def get_decision_capture() -> DecisionCapture:
    """Get global DecisionCapture instance."""
    global _decision_capture
    if _decision_capture is None:
        _decision_capture = DecisionCapture()
    return _decision_capture


def get_decision_store(path: Path | str | None = None) -> DecisionStore:
    """Get global DecisionStore instance."""
    global _decision_store
    if _decision_store is None:
        _decision_store = DecisionStore(path)
    return _decision_store


def init_decision_capture(evidence_log: Any, sample_rate: float = 1.0) -> DecisionCapture:
    """Initialize global DecisionCapture with EvidenceLog."""
    global _decision_capture
    _decision_capture = DecisionCapture(evidence_log, sample_rate)
    return _decision_capture
