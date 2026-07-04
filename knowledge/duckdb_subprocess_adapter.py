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

# Note: duckdb_store.async_ingest_findings_batch returns
# list[FindingQualityDecision | ActivationResult] — union type.

class ActivationResult(msgspec.Struct, frozen=True, gc=False):
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


class FindingQualityDecision(msgspec.Struct, frozen=True, gc=False):
    """Quality decision contract for CanonicalFinding ingest."""
    accepted: bool
    reason: str | None
    entropy: float
    normalized_hash: str | None
    duplicate: bool


# ---------------------------------------------------------------------------
# Environment gates
# ---------------------------------------------------------------------------

def _inprocess_enabled() -> bool:
    import os
    # F275: Default to "1" (ON) for M1 8GB — saves ~200MB RAM
    return os.environ.get("HLEDAC_DUCKDB_INPROCESS", "1") == "1"


def _ipc_enabled() -> bool:
    """
    Issue-4: Zero-copy Arrow IPC via posix_ipc.SharedMemory.

    HLEDAC_DUCKDB_IPC=1 enables DuckDBIPCStore (subprocess + posix_ipc ring buffer).
    Default ON for M1 (darwin arm64) when posix_ipc is available.

    DuckDB runs in spawned subprocess with:
      - 64 MiB ring buffer (posix_ipc.SharedMemory)
      - pa.ipc.open_stream zero-copy Arrow deserialization
      - Semaphore-based signaling (no pipe overhead)

    Fallback: DuckDBShadowStore in-process (Arrow zero-copy, WAL, no subprocess).
    """
    import os
    import sys

    if os.environ.get("HLEDAC_DUCKDB_IPC", "auto") == "0":
        return False
    if os.environ.get("HLEDAC_DUCKDB_IPC", "auto") == "1":
        return True

    # Default: auto — enable on M1 (darwin arm64) when posix_ipc available
    import platform
    if sys.platform == "darwin" and platform.machine() == "arm64":
        try:
            import posix_ipc as _
            return True
        except Exception:
            return False
    return False


# HLEDAC_DUCKDB_SUBPROCESS=0: disable subprocess (falls back to legacy in-process)
def _subprocess_enabled() -> bool:
    import os
    import platform
    import sys

    # Inprocess mode takes precedence — subprocess is moot when DuckDB is in-process
    if _inprocess_enabled():
        return False

    # ISSUE-8 FIX: M1 Air has 8 cores (cpu_count=8), so the old cpu_count<=4
    # check never triggered on M1 Air — subprocess path was dead code.
    # FIX: Use platform.machine()=='arm64' to correctly identify Apple Silicon.
    #
    # F270: M1 8GB default — subprocess OFF saves ~200-450MB RAM.
    # Subprocess isolation is beneficial on machines with >16GB RAM where
    # DuckDB memory usage doesn't compete with MLX Metal allocation.
    # On M1 8GB: in-process is ~40% more RAM-efficient for the sprint budget.
    is_apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
    if is_apple_silicon:
        # Apple Silicon (M1/M2/M3/M4): default to in-process for RAM savings
        return os.environ.get("HLEDAC_DUCKDB_SUBPROCESS", "0") == "1"

    # Non-Apple-Silicon: use cpu_count heuristic for other platforms
    cpu_count = os.cpu_count()
    if cpu_count is not None and cpu_count <= 4:
        # Low core count: default to in-process for RAM savings
        return os.environ.get("HLEDAC_DUCKDB_SUBPROCESS", "0") == "1"

    # Multi-core non-M1: default to subprocess for memory isolation
    return os.environ.get("HLEDAC_DUCKDB_SUBPROCESS", "1") == "1"


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
    Subprocess mode is disabled on M1 — DuckDBProxy is dead code.
    """

    __slots__ = (
        '_db_path', '_temp_dir', '_uma_state',
        '_legacy_writer', '_ipc_store', '_closed',
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
        self._legacy_writer: Any = None

        # DuckDBIPCStore (lazy — Issue-4, zero-copy Arrow IPC, M1 only)
        self._ipc_store: Any = None

        # State
        self._closed: bool = False
        self._initialized: bool = False
        self._startup_ready: asyncio.Event = asyncio.Event()

    # -------------------------------------------------------------------------
    # Public API — same as DuckDBShadowStore
    # -------------------------------------------------------------------------

    async def async_initialize(self) -> None:
        """Async init — creates DuckDBShadowStore or DuckDBIPCStore writer."""
        if self._closed:
            raise RuntimeError("DuckDBSubprocessAdapter is closed")

        if self._initialized:
            return

        # Issue-4: Try DuckDBIPCStore first (zero-copy Arrow IPC subprocess)
        if _ipc_enabled():
            ipc = await self._get_ipc_store()
            if ipc is not None:
                await ipc.async_initialize()
                self._initialized = True
                self._startup_ready.set()
                return

        # Fallback: DuckDBShadowStore in-process (Arrow zero-copy, WAL)
        writer = await self._get_legacy_writer()
        await writer.async_initialize_schema()
        self._initialized = True
        self._startup_ready.set()

    async def async_initialize_schema(self) -> None:
        """Ensure schema exists via DuckDBShadowStore."""
        if self._closed:
            raise RuntimeError("DuckDBSubprocessAdapter is closed")
        legacy = await self._get_legacy_writer()
        if hasattr(legacy, "async_initialize_schema"):
            await legacy.async_initialize_schema()

    async def async_ingest_findings_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[FindingQualityDecision | ActivationResult]:
        """
        Batch ingest — delegates to DuckDBIPCStore or DuckDBShadowStore.

        Issue-4: DuckDBIPCStore (zero-copy Arrow IPC subprocess) is tried first
        on M1 when HLEDAC_DUCKDB_IPC is enabled. Falls back to DuckDBShadowStore
        in-process (Arrow zero-copy, WAL, internal batching).

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

        # Issue-4: Try IPC store first (zero-copy Arrow IPC subprocess)
        if _ipc_enabled():
            ipc = await self._get_ipc_store()
            if ipc is not None and ipc.startup_ready:
                return await ipc.async_ingest_findings_batch(findings)

        # Fallback: DuckDBShadowStore in-process
        writer = await self._get_legacy_writer()
        return await writer.async_ingest_findings_batch(findings)

    # -------------------------------------------------------------------------
    # Wiring helpers (for scheduler + graph_service injection)
    # -------------------------------------------------------------------------

    def inject_duckdb_store(self, store: Any) -> None:
        """
        Inject DuckDBShadowStore for graph_service wiring.

        Used when the caller wants to reuse an existing store instance.
        """
        self._legacy_writer = store

    def inject_graph_store(self, graph_store: Any) -> None:
        """Inject DuckPGQGraph — forward to legacy writer."""
        if self._legacy_writer and hasattr(self._legacy_writer, "inject_graph_store"):
            self._legacy_writer.inject_graph_store(graph_store)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def shutdown(self) -> None:
        """Gracefully shutdown DuckDBShadowStore and/or DuckDBIPCStore."""
        if self._closed:
            return
        self._closed = True

        # Close DuckDBIPCStore (Issue-4: zero-copy Arrow IPC subprocess)
        if self._ipc_store is not None:
            try:
                self._ipc_store.shutdown()
            except Exception:  # noqa: BLE001
                pass
            self._ipc_store = None

        # Close DuckDBShadowStore
        if self._legacy_writer is not None:
            try:
                self._legacy_writer.close()
            except Exception:  # noqa: BLE001
                pass
            self._legacy_writer = None

        self._startup_ready.clear()

    def close(self) -> None:
        """Alias for shutdown."""
        self.shutdown()

    async def aclose(self, timeout_s: float = 10.0) -> None:
        """Async shutdown — delegates to sync shutdown() with timeout guard.

        Args:
            timeout_s: max seconds (default 10.0). DuckDB subprocess shutdown
                       is typically ~10ms; the timeout is a safety bound.
        """
        try:
            async with asyncio.timeout(timeout_s):
                await asyncio.to_thread(self.shutdown)
        except TimeoutError:
            # Fail-safe: ensure closed state even on timeout
            self._closed = True

    async def async_healthcheck(self) -> bool:
        """Quick health check — delegates to DuckDBShadowStore."""
        if self._closed:
            return False
        writer = await self._get_legacy_writer()
        return await writer.async_healthcheck()

    @property
    def is_closed(self) -> bool:
        """Return True if adapter has been shut down. Mirrors DuckDBShadowStore."""
        return self._closed

    @property
    def is_subprocess_mode(self) -> bool:
        """Always False on M1 — subprocess is dead code."""
        return False

    @property
    def startup_ready(self) -> bool:
        """True if boot barrier lifted (store accepts writes). Mirrors DuckDBShadowStore."""
        return self._startup_ready.is_set()

    def get_stats(self) -> dict[str, Any]:
        """
        Sprint P2-B: Return DuckDB store statistics for sprint report.

        Delegates to DuckDBShadowStore.get_stats() when available.

        Returns duckdb_stats section: findings count, graph stats, UMA state.
        """
        try:
            writer = self._legacy_writer
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

        Returns "ipc" when DuckDBIPCStore is active (zero-copy Arrow IPC subprocess),
        "inprocess" when DuckDBShadowStore is active, "closed" when shut down.
        """
        if self._ipc_store is not None and self._ipc_store.startup_ready:
            return "ipc"
        return "inprocess"

    async def drain_and_get_accepted(
        self, findings: list[CanonicalFinding] | None = None
    ) -> list[Any]:
        """
        Flush pending coalescer items and ingest new findings, returning merged results.

        Delegates to DuckDBShadowStore.drain_and_get_accepted().

        Args:
            findings: new findings to submit alongside any pending items in the queue.

        Returns:
            Merged list of FindingQualityDecision/ActivationResult objects,
            one per finding submitted. Empty list on failure or if coalescer
            is not running.
        """
        if self._closed:
            return []
        writer = await self._get_legacy_writer()
        return await writer.drain_and_get_accepted(findings if findings is not None else [])

    async def async_record_sprint_delta(self, row: dict) -> bool:
        """Insert a sprint_delta record — delegates to DuckDBShadowStore."""
        if self._closed:
            return False
        legacy = await self._get_legacy_writer()
        if hasattr(legacy, "async_record_sprint_delta"):
            return await legacy.async_record_sprint_delta(row)
        return False

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    async def _get_legacy_writer(self) -> Any:
        """Lazily create and initialize DuckDBShadowStore writer."""
        if self._legacy_writer is None:
            from .duckdb_store import DuckDBShadowStore
            # BUG-4 FIX: lazy=False ensures async_initialize() calls _init_connection()
            # which creates the schema tables (canonical_findings, etc.).
            # With lazy=True (default), async_initialize() sets _initialized=True but
            # skips _init_connection() → empty DuckDB file, no tables.
            self._legacy_writer = DuckDBShadowStore(
                db_path=self._db_path,
                temp_dir=self._temp_dir,
                uma_state=self._uma_state,
                lazy=False,
            )
            await self._legacy_writer.async_initialize()
        return self._legacy_writer

    async def _get_ipc_store(self) -> Any:
        """Lazily create DuckDBIPCStore (zero-copy Arrow IPC subprocess, Issue-4)."""
        if self._ipc_store is None:
            try:
                from .duckdb_ipc_store import DuckDBIPCStore
                self._ipc_store = DuckDBIPCStore(
                    db_path=self._db_path,
                    temp_dir=self._temp_dir,
                    uma_state=self._uma_state,
                )
            except Exception:  # noqa: BLE001
                self._ipc_store = None
        return self._ipc_store


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
    On M1: always uses in-process DuckDBShadowStore (Arrow zero-copy, WAL).
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
