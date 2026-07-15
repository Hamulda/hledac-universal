"""
DuckDB Subprocess Adapter — P1-1: DuckDB in-process adapter for M1 8GB UMA
===========================================================================

Drop-in wrapper around DuckDBShadowStore for M1 8GB.

On M1 (darwin, <=4 cores): always uses in-process DuckDB via DuckDBShadowStore.
DuckDBProxy subprocess path is dead code on M1 — subprocess mode is disabled by
default and offers no RAM benefit on UMA architecture.

ARCHITECTURA:
-------------
DuckDBSubprocessAdapter
    └── DuckDBShadowStore (in-process, Arrow zero-copy, WAL)
            ├── Quality gate (Rust rayon)
            ├── LMDB WAL (mmap)
            └── Arrow INSERT (zero-copy)

WIRE MAP:
---------
core/__main__.py (1565)
    └── DuckDBSubprocessAdapter()         ← in-process DuckDB (M1 default)

Author: Sprint P1-1

STORAGE-DUP-003 CHANGE LOG:
- DuckDBIPCStore removed (legacy, replaced by in-process DuckDB since F273F)
- _ipc_enabled() and _get_ipc_store() removed
- All IPC references stripped — single in-process path only
"""
from __future__ import annotations


import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec

if TYPE_CHECKING:
    from .duckdb_store import CanonicalFinding


# ---------------------------------------------------------------------------
# Result types (aligned with duckdb_store.py)
# ---------------------------------------------------------------------------

class ActivationResult(msgspec.Struct, frozen=True):
    """
    Typed result contract for activation record operations.
    Frozen msgspec.Struct — hashable, comparable, M1 8GB RAM-friendly.
    """
    finding_id: str
    lmdb_success: bool | list[bool]
    duckdb_success: bool | None
    lmdb_key: str
    desync: bool
    error: str | None
    accepted: bool


class FindingQualityDecision(msgspec.Struct, frozen=True):
    """Quality decision contract for CanonicalFinding ingest."""
    accepted: bool
    reason: str | None
    entropy: float
    normalized_hash: str | None
    duplicate: bool


# ---------------------------------------------------------------------------
# DuckDBSubprocessAdapter — drop-in DuckDBShadowStore replacement
# ---------------------------------------------------------------------------

class DuckDBSubprocessAdapter:
    """
    Drop-in wrapper around DuckDBShadowStore for M1 8GB.

    Public API (same as DuckDBShadowStore):
      - async_ingest_findings_batch(findings) -> list[FindingQualityDecision | ActivationResult]
      - async_initialize() / async_initialize_schema()
      - inject_duckdb_store() / inject_graph_store()
      - shutdown() / close()

    M1 8GB: always uses DuckDBShadowStore in-process (Arrow zero-copy, WAL, internal batching).

    STORAGE-DUP-003: Single in-process path only. DuckDBIPCStore subprocess removed.
    """

    __slots__ = (
        '_db_path', '_temp_dir', '_uma_state',
        '_writer', '_closed',
        '_initialized', '_startup_ready',
    )

    def __init__(
        self,
        db_path: Path | str | None = None,
        temp_dir: Path | str | None = None,
        uma_state: str | None = None,
    ) -> None:
        self._db_path: Path | None = Path(db_path) if db_path is not None else None
        self._temp_dir: Path | None = Path(temp_dir) if temp_dir is not None else None
        self._uma_state: str | None = uma_state

        # Resolve db_path if None — mirrors DuckDBShadowStore._resolve_path()
        if self._db_path is None:
            try:
                from hledac.universal.paths import DUCKDB_STORE_ROOT, RAMDISK_ACTIVE, RAMDISK_ROOT
                if RAMDISK_ACTIVE:
                    self._db_path = DUCKDB_STORE_ROOT / "shadow_analytics.duckdb"
                    self._temp_dir = RAMDISK_ROOT / "duckdb_tmp"
                else:
                    self._db_path = DUCKDB_STORE_ROOT / "analytics.duckdb"
            except Exception:  # noqa: BLE001
                pass  # Degraded — will use :memory:

        # DuckDBShadowStore writer (lazy — created on first use)
        self._writer: Any = None

        # State
        self._closed: bool = False
        self._initialized: bool = False
        self._startup_ready: asyncio.Event = asyncio.Event()

    # -------------------------------------------------------------------------
    # Public API — same as DuckDBShadowStore
    # -------------------------------------------------------------------------

    async def async_initialize(self) -> None:
        """Async init — creates DuckDBShadowStore writer (in-process, Arrow zero-copy)."""
        if self._closed:
            raise RuntimeError("DuckDBSubprocessAdapter is closed")

        if self._initialized:
            return

        writer = await self._get_writer()
        await writer.async_initialize_schema()
        self._initialized = True
        self._startup_ready.set()

    async def async_initialize_schema(self) -> None:
        """Ensure schema exists via DuckDBShadowStore."""
        if self._closed:
            raise RuntimeError("DuckDBSubprocessAdapter is closed")
        writer = await self._get_writer()
        if hasattr(writer, "async_initialize_schema"):
            await writer.async_initialize_schema()
        # ISSUE-006 fix: ensure _startup_ready is set so wait_until_ready()
        # does not timeout when async_initialize_schema() is called directly
        # (without going through async_initialize() which also sets the event)
        self._startup_ready.set()

    async def async_ingest_findings_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[FindingQualityDecision | ActivationResult]:
        """
        Batch ingest — delegates to DuckDBShadowStore (in-process, Arrow zero-copy).

        STORAGE-DUP-003: Single in-process path. DuckDBIPCStore removed.

        Returns list[FindingQualityDecision | ActivationResult] with 1:1 invariant.
        """
        if self._closed:
            return [
                FindingQualityDecision(
                    accepted=False,
                    reason="adapter_closed",
                    entropy=0.0,
                    normalized_hash=None,
                    duplicate=False,
                )
                for _ in findings
            ]

        writer = await self._get_writer()
        return await writer.async_ingest_findings_batch(findings)

    # -------------------------------------------------------------------------
    # Wiring helpers (for scheduler + graph_service injection)
    # -------------------------------------------------------------------------

    def inject_duckdb_store(self, store: Any) -> None:
        """
        Inject DuckDBShadowStore for graph_service wiring.

        Used when the caller wants to reuse an existing store instance.
        """
        self._writer = store

    def inject_graph_store(self, graph_store: Any) -> None:
        """Inject DuckPGQGraph — forward to writer."""
        if self._writer and hasattr(self._writer, "inject_graph_store"):
            self._writer.inject_graph_store(graph_store)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def shutdown(self) -> None:
        """Gracefully shutdown DuckDBShadowStore."""
        if self._closed:
            return
        self._closed = True

        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
            self._writer = None

        self._startup_ready.clear()

    def close(self) -> None:
        """Alias for shutdown."""
        self.shutdown()

    async def aclose(self, timeout_s: float = 10.0) -> None:
        """Async shutdown — delegates to sync shutdown() with timeout guard."""
        try:
            async with asyncio.timeout(timeout_s):
                await asyncio.to_thread(self.shutdown)
        except TimeoutError:
            # Fail-safe: ensure closed state even on timeout
            self._closed = True

    async def wait_until_ready(self, timeout_s: float = 10.0) -> bool:
        """
        Event-driven readiness wait — ISSUE-006 fix.

        Delegates to DuckDBShadowStore.wait_until_ready() once writer is available.
        If writer is not yet created, waits on the adapter's _startup_ready event.
        """
        if self._closed:
            return False
        if self._startup_ready.is_set():
            return True
        # If writer already exists, delegate to it
        if self._writer is not None:
            try:
                return await self._writer.wait_until_ready(timeout_s=timeout_s)
            except Exception:  # noqa: BLE001
                return False
        # Writer not yet created — wait on adapter's startup event
        try:
            async with asyncio.timeout(timeout_s):
                await self._startup_ready.wait()
            return True
        except asyncio.TimeoutError:
            return False

    async def async_healthcheck(self) -> bool:
        """Quick health check — delegates to DuckDBShadowStore."""
        if self._closed:
            return False
        writer = await self._get_writer()
        return await writer.async_healthcheck()

    def advance_ioc_sprint(self, sprint_id: int) -> None:
        """
        Advance IOC dedup store to new sprint boundary.

        P1-07: Delegates to DuckDBShadowStore.advance_ioc_sprint which propagates
        to DedupManager.advance_ioc_sprint → Rust MmapIocDedupStore.advance_sprint().
        """
        if self._closed:
            return
        writer = object.__getattribute__(self, "_writer")
        if writer is not None:
            try:
                writer.advance_ioc_sprint(sprint_id)
            except Exception:  # noqa: BLE001
                pass

    @property
    def is_closed(self) -> bool:
        """Return True if adapter has been shut down. Mirrors DuckDBShadowStore."""
        return self._closed

    @property
    def is_subprocess_mode(self) -> bool:
        """Always False — subprocess mode removed (STORAGE-DUP-003)."""
        return False

    @property
    def startup_ready(self) -> bool:
        """True if boot barrier lifted (store accepts writes). Mirrors DuckDBShadowStore."""
        return self._startup_ready.is_set()

    def get_stats(self) -> dict[str, Any]:
        """
        Sprint P2-B: Return DuckDB store statistics for sprint report.

        Delegates to DuckDBShadowStore.get_stats() when available.
        """
        try:
            writer = self._writer
            if writer is not None and hasattr(writer, "get_stats"):
                return writer.get_stats()
        except Exception:  # noqa: BLE001
            pass
        return {
            "total_findings": 0,
            "total_iocs": 0,
            "graph_stats": {},
            "uma_state": "unknown",
            "duckdb_mode": "inprocess",
        }

    @property
    def duckdb_mode(self) -> str:
        """
        Returns the active DuckDB runtime mode for sprint telemetry.

        STORAGE-DUP-003: Always "inprocess" — IPC subprocess removed.
        """
        return "inprocess"

    async def drain_and_get_accepted(
        self, findings: list[CanonicalFinding] | None = None
    ) -> list[Any]:
        """
        Flush pending coalescer items and ingest new findings, returning merged results.

        Delegates to DuckDBShadowStore.drain_and_get_accepted().
        """
        if self._closed:
            return []
        writer = await self._get_writer()
        return await writer.drain_and_get_accepted(findings if findings is not None else [])

    async def async_record_sprint_delta(self, row: dict) -> bool:
        """Insert a sprint_delta record — delegates to DuckDBShadowStore."""
        if self._closed:
            return False
        writer = await self._get_writer()
        if hasattr(writer, "async_record_sprint_delta"):
            return await writer.async_record_sprint_delta(row)
        return False

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    async def _get_writer(self) -> Any:
        """Lazily create and initialize DuckDBShadowStore writer (in-process)."""
        if self._writer is None:
            from .duckdb_store import DuckDBShadowStore
            self._writer = DuckDBShadowStore(
                db_path=self._db_path,
                temp_dir=self._temp_dir,
                uma_state=self._uma_state,
                lazy=False,
            )
            await self._writer.async_initialize()
        return self._writer


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_subprocess_adapter(
    db_path: Path | str | None = None,
    temp_dir: Path | str | None = None,
    uma_state: str | None = None,
) -> DuckDBSubprocessAdapter:
    """
    Factory: create DuckDB adapter for M1 8GB.

    Drop-in replacement for DuckDBShadowStore() in core/__main__.py.
    STORAGE-DUP-003: Single in-process path only.
    """
    return DuckDBSubprocessAdapter(
        db_path=db_path,
        temp_dir=temp_dir,
        uma_state=uma_state,
    )


__all__ = [
    "DuckDBSubprocessAdapter",
    "create_subprocess_adapter",
    "ActivationResult",
    "FindingQualityDecision",
]
