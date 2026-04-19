"""
ToolExecLog - Tamper-evident tool execution logging
===================================================

This module implements append-only logging for tool execution events.
Unlike EvidenceLog (which stores research evidence), ToolExecLog tracks
tool invocations with hashes for forensic audit.

M1 8GB Optimization:
- Ring buffer in RAM (max 100 events)
- Append-only JSONL persistence to disk
- Bounded metadata only (no raw tool outputs)
- Hashes only - no sensitive data persisted
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Bounded error class names
BOUNDED_ERROR_CLASSES = frozenset([
    "TimeoutError", "ConnectionError", "HTTPError", "ValueError",
    "TypeError", "AttributeError", "KeyError", "IOError",
    "RuntimeError", "CancelledError", "AuthenticationError",
    "PermissionError", "NotFoundError", "ValidationError",
    "RateLimitError", "CircuitBreakerError", "Unknown"
])

# Bounded status values
BOUNDED_STATUSES = frozenset(["success", "error", "cancelled"])


# =============================================================================
# CORRELATION GRAMMAR — SHARED BOUNDARY (Sprint F2B)
# =============================================================================
# All planes (ToolExecLog, MetricsRegistry, EvidenceLog) share the same
# correlation key names: run_id, branch_id, provider_id, action_id.
# Key names are the grammar contract. Scope semantics (per-event vs per-registry)
# are plane-specific by design — see docstrings below.
# =============================================================================

SHARED_CORRELATION_KEYS = frozenset(["run_id", "branch_id", "provider_id", "action_id"])
"""
Shared correlation key grammar.

All ledger planes MUST use these key names for cross-component correlation.
Deviation = silent correlation loss in cross-plane queries.

SCOPE SEMANTICS (intentional, NOT drift):
- ToolExecLog:        per-event  — each log() call carries its own correlation dict
- MetricsRegistry:    per-registry — set at __init__, batched on flush
- EvidenceLog:        per-event  — each create_event carries correlation

These are efficiency trade-offs, not inconsistency. Tool audit events are
discrete; metrics are aggregated. Unifying scope would sacrifice one plane's
design for the other's convenience.
"""


def normalize_correlation(corr: Optional[Dict[str, Optional[str]]]) -> Optional[Dict[str, Optional[str]]]:
    """
    Normalize correlation dict to shared grammar.

    Returns canonical form:
    - Only keys in SHARED_CORRELATION_KEYS are present
    - Values are either str or None
    - Keys not in grammar are silently dropped (fail-soft hardening)

    This is a grammar seam for cross-plane correlation queries,
    NOT a general validator.
    """
    if corr is None:
        return None
    return {k: corr.get(k) for k in SHARED_CORRELATION_KEYS if k in corr}


@dataclass
class ToolExecEvent:
    """
    Tool execution event - bounded metadata only.

    Stores hashes instead of actual data to maintain:
    - Tamper-evidence (hash chain)
    - No sensitive data in logs
    - Forensic audit capability
    """
    event_id: str
    ts: datetime
    tool_name: str
    input_hash: str  # SHA256 of input (not stored)
    output_hash: str  # SHA256 of output (not stored)
    output_len: int  # Bounded: actual output length
    status: str  # "success" | "error" | "cancelled"
    error_class: Optional[str] = None  # Bounded error type
    seq_no: int = 0
    prev_chain_hash: Optional[str] = None
    chain_hash: Optional[str] = None
    correlation: Optional[Dict[str, Optional[str]]] = None  # run_id, branch_id, provider_id, action_id

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSONL"""
        result = {
            "event_id": self.event_id,
            "ts": self.ts.isoformat(),
            "tool_name": self.tool_name,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "output_len": self.output_len,
            "status": self.status,
            "error_class": self.error_class,
            "seq_no": self.seq_no,
            "prev_chain_hash": self.prev_chain_hash,
            "chain_hash": self.chain_hash,
        }
        if self.correlation:
            result["correlation"] = self.correlation
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolExecEvent":
        """Deserialize from dict"""
        if isinstance(data.get("ts"), str):
            data["ts"] = datetime.fromisoformat(data["ts"])
        return cls(**data)


class ToolExecLog:
    """
    Append-only tool execution log with hash-chain.

    ROLE (Sprint 8VF):
    ════════════════════════════════════════════════════════
    AUDIT/LOGGING boundary — NOT an execution authority.
    This class logs tool execution events for forensic audit
    and correlation. It does NOT execute tools.

    CORRELATION BOUNDARY:
    - Designed to wrap ToolRegistry.execute_with_limits() calls
    - ToolExecEvent.correlation dict carries: run_id, branch_id,
      provider_id, action_id for cross-referencing
    - Hash-chain provides tamper-evidence for audit

    Execution vs Audit separation:
    - ToolRegistry.execute_with_limits() → executes tools (canonical)
    - ToolExecLog.log()                → records execution (audit)

    DO NOT:
    - Execute tools here — use ToolRegistry for that
    - Make this a second execution authority
    - Store raw inputs/outputs (hashes only)

    RELATED COMPONENTS:
    - ToolRegistry:    CANONICAL execution (controls what runs)
    - GhostExecutor:   DONOR/COMPAT (legacy actions)
    - CapabilityRouter: SIGNAL mapping (capability recommendations)
    ════════════════════════════════════════════════════════

    Design principles:
    - NEVER store raw tool inputs/outputs
    - Store only hashes for tamper evidence
    - Bounded metadata (sizes, error types)
    - Disk-first with RAM ring buffer
    """

    MAX_RAM_EVENTS = 100
    MAX_OUTPUT_LEN = 1024 * 1024  # 1MB max output tracked
    _FSYNC_EVERY_N_EVENTS = 25  # Batch fsync for performance

    def __init__(
        self,
        run_dir: Path,
        enable_persist: bool = True,
        run_id: str = "default"
    ):
        """
        Initialize ToolExecLog.

        Args:
            run_dir: Directory for JSONL persistence
            enable_persist: Whether to persist to disk
            run_id: Run identifier for this execution
        """
        self._run_dir = run_dir
        self._persist_enabled = enable_persist  # Explicit persist mode flag
        self._run_id = run_id

        # Chain state
        self._seq = 0
        self._chain_head = "genesis"  # Initial chain head

        # RAM ring buffer
        self._log: deque = deque(maxlen=self.MAX_RAM_EVENTS)

        # Batching state for fsync
        self._events_since_fsync = 0

        # Explicit closed state — never conflated with _persist_file
        self._closed = False

        # Persist file
        self._persist_file: Optional[Any] = None
        if enable_persist:
            self._persist_file = self._init_persist_file()

        logger.info(f"ToolExecLog initialized: run_id={run_id}, persist={enable_persist}")

    def _init_persist_file(self) -> Any:
        """Initialize persistence file"""
        log_dir = self._run_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "tool_exec.jsonl"

        # Open in append mode
        f = open(log_file, "ab")  # Binary for encryption ready
        return f

    def _hash_bytes(self, data: bytes) -> str:
        """Compute SHA256 hash of bytes"""
        return hashlib.sha256(data).hexdigest()

    def _bound_error_class(self, error: Optional[Exception]) -> Optional[str]:
        """Bound error class name to safe set"""
        if error is None:
            return None
        error_name = type(error).__name__
        # Use bounded set or "Unknown" if not in list
        return error_name if error_name in BOUNDED_ERROR_CLASSES else "Unknown"

    def log(
        self,
        tool_name: str,
        input_data: bytes,
        output_data: bytes,
        status: str,
        error: Optional[Exception] = None,
        correlation: Optional[Dict[str, Optional[str]]] = None,
    ) -> ToolExecEvent:
        """
        Log a tool execution event.

        Args:
            tool_name: Name of the tool executed
            input_data: Raw input bytes (will be hashed, not stored)
            output_data: Raw output bytes (will be hashed, not stored)
            status: "success" | "error" | "cancelled"
            error: Optional exception if status is "error"
            correlation: Optional correlation dict with keys:
                run_id, branch_id, provider_id, action_id

        Returns:
            The created ToolExecEvent

        Raises:
            RuntimeError: If log has been finalized/closed and can no longer
                truthfully persist events.
        """
        import uuid

        # Audit truth guard: after finalize()/close() the audit trail is closed.
        # Explicit _closed state — not a proxy via _persist_file.
        if self._closed:
            raise RuntimeError(
                "ToolExecLog.log() called after finalize()/close(): "
                "audit trail is closed, refusing to log event"
            )

        # Compute hashes (never store raw data)
        input_hash = self._hash_bytes(input_data) if input_data else ""
        output_len = min(len(output_data), self.MAX_OUTPUT_LEN)
        output_hash = self._hash_bytes(output_data[:self.MAX_OUTPUT_LEN]) if output_data else ""

        # Bound error class
        error_class = self._bound_error_class(error)

        # Bound status to known values (fail-soft: invalid/unknown → "error")
        if status not in BOUNDED_STATUSES:
            status = "error"

        # Normalize correlation at audit boundary (drops extra keys)
        correlation = normalize_correlation(correlation)

        # Create event with chain
        self._seq += 1

        # Chain hash: sha256(prev + event_id + input_hash + output_hash + status + error_class)
        # Status and error_class are included to make metadata tamper-evident
        event_id = f"tool_{self._seq}_{uuid.uuid4().hex[:8]}"
        chain_input = (
            f"{self._chain_head}:{event_id}:{input_hash}:{output_hash}"
            f":{status}:{error_class}"
        )
        chain_hash = hashlib.sha256(chain_input.encode()).hexdigest()

        event = ToolExecEvent(
            event_id=event_id,
            ts=datetime.utcnow(),
            tool_name=tool_name,
            input_hash=input_hash,
            output_hash=output_hash,
            output_len=output_len,
            status=status,
            error_class=error_class,
            seq_no=self._seq,
            prev_chain_hash=self._chain_head,
            chain_hash=chain_hash,
            correlation=correlation,
        )

        # Update chain head
        self._chain_head = chain_hash

        # Persist to disk
        if self._persist_file:
            try:
                line = json.dumps(event.to_dict(), separators=(',', ':'))
                self._persist_file.write(line.encode('utf-8') + b'\n')
                # Always flush to OS buffer for crash safety, but fsync only every N events
                self._persist_file.flush()
                self._events_since_fsync += 1

                # Batch fsync: only os.fsync every N events for performance
                if self._events_since_fsync >= self._FSYNC_EVERY_N_EVENTS:
                    os.fsync(self._persist_file.fileno())
                    self._events_since_fsync = 0
            except Exception as e:
                logger.error(f"Failed to persist tool event: {e}")

        # Add to ring buffer — always, while ledger is open.
        # Ring buffer is meaningful for audit in both persist and no-persist modes.
        # Closed logs must not accumulate RAM (enforced via _closed check at top).
        self._log.append(event)

        return event

    def verify_all(self) -> Dict[str, Any]:
        """
        Verify the entire chain for tampering.

        Returns:
            Dict with:
                - chain_valid: bool
                - head_hash: str
                - event_count: int
                - first_seq: int
                - errors: list of issues
        """
        # Read all events from disk if persist was enabled
        events: List[ToolExecEvent] = []
        if self._persist_enabled:
            log_file = self._run_dir / "logs" / "tool_exec.jsonl"
            if log_file.exists():
                with open(log_file, 'r') as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            events.append(ToolExecEvent.from_dict(data))

        # Also include RAM events
        for event in self._log:
            if event not in events:
                events.append(event)

        # Sort by seq_no
        events.sort(key=lambda e: e.seq_no)

        errors = []
        expected_head = "genesis"

        for event in events:
            # Verify chain linkage
            if event.prev_chain_hash != expected_head:
                errors.append(
                    f"Chain break at seq {event.seq_no}: "
                    f"expected prev={expected_head}, got {event.prev_chain_hash}"
                )

            # Verify chain hash (must match log() computation: includes status + error_class)
            chain_input = (
                f"{expected_head}:{event.event_id}:{event.input_hash}:{event.output_hash}"
                f":{event.status}:{event.error_class}"
            )
            expected_chain = hashlib.sha256(chain_input.encode()).hexdigest()
            if event.chain_hash != expected_chain:
                errors.append(
                    f"Hash mismatch at seq {event.seq_no}: "
                    f"expected {expected_chain[:16]}..., got {(event.chain_hash or '')[:16]}..."
                )

            expected_head = event.chain_hash

        return {
            "chain_valid": len(errors) == 0,
            "head_hash": self._chain_head,
            "event_count": len(events),
            "first_seq": events[0].seq_no if events else 0,
            "errors": errors,
        }

    def get_head_hash(self) -> str:
        """Get current chain head hash"""
        return self._chain_head

    def get_stats(self) -> Dict[str, Any]:
        """Get log statistics"""
        return {
            "seq": self._seq,
            "ram_events": len(self._log),
            "head_hash": self._chain_head,
            "run_id": self._run_id,
            "persist_enabled": self._persist_enabled,
            "closed": self._closed,
        }

    def close(self) -> None:
        """Close log and flush to disk (alias for finalize)"""
        self.finalize()

    def finalize(self) -> None:
        """Finalize log - always flush and fsync pending events for crash safety."""
        if self._closed:
            return  # Idempotent: already closed
        if self._persist_file:
            try:
                # Always flush remaining events (even if < N events since last fsync)
                self._persist_file.flush()
                os.fsync(self._persist_file.fileno())
                self._events_since_fsync = 0  # Reset counter after forced fsync
                self._persist_file.close()
            except Exception as e:
                logger.error(f"Error finalizing tool exec log: {e}")
            finally:
                self._persist_file = None
        self._closed = True

    def __enter__(self) -> "ToolExecLog":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()


# Convenience function
def create_tool_exec_log(
    run_dir: Path,
    run_id: str = "default"
) -> ToolExecLog:
    """Create a ToolExecLog instance"""
    return ToolExecLog(run_dir=run_dir, run_id=run_id)
