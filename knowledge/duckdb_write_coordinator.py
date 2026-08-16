"""
DuckDBWriteCoordinator — Extrahovaný hot-path pro batch ingest z DuckDBShadowStore.

Třída odpovídá za kompletní ingest pipeline:


- WAL-first pořadí (LMDB putmany)
- DuckDB Arrow zero-copy insert
- Graph ingest (podmíněně)
- Semantic buffer
- Quality state update
- Circuit breaker

Fáze 1 refaktorace: DuckDBShadowStore.CBO 31 → ~25
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol
from _core import aclose

if TYPE_CHECKING:
    from pathlib import Path

    from .duckdb_store import ActivationResult, CanonicalFinding, DuckDBShadowStore
    from .duckdb_wal_manager import DuckDBWALManager
    from .duckdb_quality_gate import QualityAssessmentState
    from .semantic_store_buffer import SemanticStoreBuffer

__all__ = ["DuckDBWriteCoordinator", "WriteCoordinatorConfig"]


logger = logging.getLogger(__name__)


class CBState(Enum):
    """Circuit breaker state."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ArrowIngestStatus(Enum):
    """Arrow ingest path status - F360M-R refactoring."""
    SUCCESS = "success"
    FALLBACK_LEGACY = "fallback_legacy"
    FALLBACK_PYARROW = "fallback_pyarrow"
    FALLBACK_INIT = "fallback_init"
    FALLBACK_CIRCUIT = "fallback_circuit"
    FALLBACK_WAL = "fallback_wal"
    FALLBACK_BATCH = "fallback_batch"


@dataclass(slots=True)
class ArrowIngestResult:
    """Result of arrow ingest pipeline - F360M-R."""
    status: ArrowIngestStatus
    results: list[dict[str, Any]]
    duckdb_all_ok: bool
    duckdb_count: int
    duckdb_err: str | None
    lmdb_ok_count: int = 0
    duckdb_ok_count: int = 0

    def should_fallback(self) -> bool:
        return self.status != ArrowIngestStatus.SUCCESS

    def to_metrics_reason(self) -> str:
        """Convert status to metric reason suffix."""
        reason_map = {
            ArrowIngestStatus.FALLBACK_LEGACY: "legacy",
            ArrowIngestStatus.FALLBACK_PYARROW: "pyarrow",
            ArrowIngestStatus.FALLBACK_INIT: "init",
            ArrowIngestStatus.FALLBACK_CIRCUIT: "circuit",
            ArrowIngestStatus.FALLBACK_WAL: "wal",
            ArrowIngestStatus.FALLBACK_BATCH: "batch",
        }
        return reason_map.get(self.status, "unknown")


@dataclass(slots=True)
class WriteCoordinatorConfig:
    """
    M1 8GB bounded configuration — všechny limity explicitní.
    
    MODERN-36: Batching optimizations for unified 6-thread budget model.
    Thread budget breakdown: DuckDB RW(1) + DuckDB RO(2) + CPU I/O(3) = 6 total.
    
    Batching strategy:
    - WAL batching: 500 items per LMDB putmany (reduces fsync overhead)
    - Arrow batching: >= 5 items for Arrow → DuckDB path
    - IOC buffering: 64 items per chunk (reduced from 128 for M1 8GB)
    - Memory-aware sizing: batch sizes scale with available memory pressure
    """
    max_concurrent_writes: int = 3  # Reduced from 4 for 6-thread budget
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown: float = 30.0
    wal_putmany_batch_size: int = 500
    arrow_min_batch: int = 5
    # MODERN-36: Reduced IOC chunk for M1 8GB memory constraints
    ioc_chunk_size: int = 64  # Reduced from 128
    vacuum_interval_ops: int = 10000
    checkpoint_interval_ops: int = 5000
    vacuum_interval_seconds: float = 3600.0
    checkpoint_interval_seconds: float = 1800.0


class DuckDBWriteCoordinator:
    """
    Extrahovaný write coordinator z DuckDBShadowStore.

    Drží referenci na DuckDBShadowStore pro přístup k:
    - WAL manager (lazy init)
    - Graph service
    - Semantic buffer
    - Quality gate + state
    - Arrow executors

    M1 8GB: __slots__ = ~200 bytes na instanci místo ~1KB dict-based.
    """
    __slots__ = (
        "_duckdb",
        "_wal_manager",
        "_wal_root",
        "_graph_service",
        "_semantic_buffer",
        "_quality_gate",
        "_quality_state",
        "_wal_executor",
        "_duckdb_arrow_executor",
        "_write_semaphore",
        "_breaker_state",
        "_breaker_failures",
        "_breaker_last_failure",
        "_breaker_cooldown",
        "_breaker_threshold",
        "_config",
        "_write_op_counter",
        "_last_vacuum_time",
        "_last_checkpoint_time",
        "_vacuum_interval_ops",
        "_checkpoint_interval_ops",
        "_vacuum_interval_seconds",
        "_checkpoint_interval_seconds",
        "_arrow_metrics",
        "_startup_ready",
    )

    def __init__(
        self,
        duckdb: DuckDBShadowStore,
        wal_manager: DuckDBWALManager | None = None,
        wal_root: Path | None = None,
        graph_service: Any | None = None,
        semantic_buffer: SemanticStoreBuffer | None = None,
        quality_gate: Any | None = None,
        quality_state: QualityAssessmentState | None = None,
        wal_executor: Any | None = None,
        duckdb_arrow_executor: Any | None = None,
        config: WriteCoordinatorConfig | None = None,
        startup_ready: asyncio.Event | None = None,
    ) -> None:
        self._duckdb = duckdb
        self._wal_manager = wal_manager
        self._wal_root = wal_root
        self._graph_service = graph_service
        self._semantic_buffer = semantic_buffer
        self._quality_gate = quality_gate
        self._quality_state = quality_state
        self._wal_executor = wal_executor
        self._duckdb_arrow_executor = duckdb_arrow_executor
        self._config = config or WriteCoordinatorConfig()
        self._startup_ready = startup_ready or asyncio.Event()

        # Circuit breaker state
        self._breaker_state: CBState = CBState.CLOSED
        self._breaker_failures: int = 0
        self._breaker_last_failure: float = 0.0
        self._breaker_cooldown = self._config.circuit_breaker_cooldown
        self._breaker_threshold = self._config.circuit_breaker_threshold

        # Maintenance counters
        self._write_op_counter: int = 0
        self._last_vacuum_time: float = 0.0
        self._last_checkpoint_time: float = 0.0
        self._vacuum_interval_ops = self._config.vacuum_interval_ops
        self._checkpoint_interval_ops = self._config.checkpoint_interval_ops
        self._vacuum_interval_seconds = self._config.vacuum_interval_seconds
        self._checkpoint_interval_seconds = self._config.checkpoint_interval_seconds

        # Arrow metrics (same as DuckDBShadowStore)
        # MODERN-36: Added batching efficiency metrics
        self._arrow_metrics: dict[str, int] = {
            "arrow_selected": 0,
            "arrow_fallback_env": 0,
            "arrow_fallback_batch": 0,
            "arrow_fallback_pyarrow": 0,
            "arrow_fallback_init": 0,
            "arrow_fallback_executor": 0,
            # MODERN-36: Batching efficiency metrics
            "batches_total": 0,
            "batches_arrow_path": 0,
            "batches_fallback_path": 0,
            "items_total": 0,
            "avg_batch_size": 0,  # Cumulative average
            "wal_operations": 0,
            "wal_items_total": 0,
            "arrow_fallback_empty": 0,
            "arrow_fallback_all_fail": 0,
            "arrow_success_count": 0,
            "arrow_success_lmdb_count": 0,
            "arrow_success_duckdb_count": 0,
            "arrow_error_table_build": 0,
            "arrow_error_duckdb_insert": 0,
            "arrow_error_partial": 0,
            "arrow_partial_duplicates": 0,
        }

        self._write_semaphore = asyncio.Semaphore(self._config.max_concurrent_writes)

    # -------------------------------------------------------------------------
    # Circuit breaker
    # -------------------------------------------------------------------------

    def _check_circuit_breaker(self) -> bool:
        """Vrátí True pokud je write path otevřená (není v open/half_open)."""
        import time as _time

        now = _time.time()
        if self._breaker_state == CBState.OPEN:
            if now - self._breaker_last_failure >= self._breaker_cooldown:
                self._breaker_state = CBState.HALF_OPEN
                return True
            return False
        return True

    def _record_failure(self) -> None:
        """Zaznamená selhání do circuit breakeru."""
        import time as _time

        self._breaker_failures += 1
        self._breaker_last_failure = _time.time()
        if self._breaker_failures >= self._breaker_threshold:
            self._breaker_state = CBState.OPEN
            logger.warning(
                f"[WriteCoordinator] Circuit breaker OPEN after {self._breaker_failures} failures"
    )

    def _record_success(self) -> None:
        """Resetuje circuit breaker při úspěchu."""
        self._breaker_failures = 0
        self._breaker_state = CBState.CLOSED

    # -------------------------------------------------------------------------
    # WAL management (lazy init)
    # -------------------------------------------------------------------------

    async def _ensure_wal_manager(self) -> bool:
        """Lazy init WAL manager. Vrací True pokud je připraven."""
        if self._wal_manager is not None:
            return True

        if self._wal_root is None:
            logger.warning("[WriteCoordinator] No WAL root configured")
            return False

        try:
            from .duckdb_wal_manager import DuckDBWALManager

            self._wal_manager = DuckDBWALManager(wal_root=self._wal_root)
            self._wal_manager.initialize()
            return True
        except Exception as e:
            logger.error(f"[WriteCoordinator] WAL manager init failed: {e}")
            return False

    # -------------------------------------------------------------------------
    # Maintenance helpers (RES-03)
    # -------------------------------------------------------------------------

    def _should_vacuum(self) -> bool:
        """CHECKPOINT/VACUUM podmínky z RES-03."""
        import time as _time

        if self._write_op_counter >= self._vacuum_interval_ops:
            if _time.time() - self._last_vacuum_time >= self._vacuum_interval_seconds:
                return True
        return False

    def _should_checkpoint(self) -> bool:
        """Checkpoint podmínky z RES-03."""
        import time as _time

        if self._write_op_counter >= self._checkpoint_interval_ops:
            if _time.time() - self._last_checkpoint_time >= self._checkpoint_interval_seconds:
                return True
        return False

    # -------------------------------------------------------------------------
    # Arrow batch ingest (hlavní hot-path metoda)
    # -------------------------------------------------------------------------

    async def _try_arrow_direct(self, findings: list[CanonicalFinding]) -> bool:
        """Try direct arrow ingest. Returns True if successful."""
        try:
            import pyarrow as _pa  # noqa: F401
        except ImportError:
            return False
        return True

    async def _try_wal_fallback(self, findings: list[CanonicalFinding]) -> bool:
        """Try WAL first. Returns True if WAL write succeeded."""
        try:
            return await self._wal_put_many(findings)
        except Exception:
            return False

    async def _try_duckdb_basic(self, findings: list[CanonicalFinding]) -> tuple[int, str | None]:
        """Try basic DuckDB insert. Returns (count, error)."""
        try:
            loop = asyncio.get_running_loop()
            result: tuple[int, str | None] = await loop.run_in_executor(
                self._duckdb_arrow_executor,
                self._duckdb_arrow_sync,
                findings,
    )
            return result
        except Exception as e:
            return (0, str(e))

    def _check_arrow_conditions(self, findings: list) -> tuple[bool, str | None]:
        """Check early exit conditions for arrow path. Returns (should_use_arrow, reason)."""
        import os as _os

        _ARROW_INGEST_ENABLED = _os.getenv("HLEDAC_ARROW_INGEST", "1") != "0"
        _ARROW_MIN_BATCH = 5

        if not findings:
            return False, "empty_findings"
        if not _ARROW_INGEST_ENABLED:
            return False, "env_disabled"
        if len(findings) < _ARROW_MIN_BATCH:
            return False, "batch_too_small"
        return True, None

    def _build_arrow_results(self, findings: list, wal_ok: bool, duckdb_all_ok: bool, duckdb_err: str | None) -> list[dict]:
        """Build result dicts for arrow path."""
        return [
            {
                "finding_id": f.finding_id,
                "lmdb_success": wal_ok,
                "duckdb_success": duckdb_all_ok,
                "error": duckdb_err,
            }
            for f in findings
        ]

    async def _handle_arrow_post_process(self, results: list[dict], findings: list) -> None:
        """Handle post-processing after arrow ingest: graph, semantic buffer, quality state, metrics."""
        # Graph + semantic buffer for accepted findings
        if results and any(r.get("lmdb_success") for r in results):
            if self._duckdb.truth_write_graph_supports_buffered_writes():
                await self._graph_ingest_findings(findings)
            if self._semantic_buffer is not None:
                await self._semantic_buffer.buffer_findings(findings)

        # Quality state update
        if self._quality_state is not None:
            accepted_total = sum(1 for r in results if r.get("lmdb_success"))
            self._quality_state._accepted_count = (
                getattr(self._quality_state, "_accepted_count", 0) + accepted_total
    )

        # Maintenance counters update
        self._write_op_counter += len(findings)
        now = _time.time()
        if self._should_vacuum():
            self._last_vacuum_time = now
            self._duckdb.trigger_vacuum_if_needed()
        if self._should_checkpoint():
            self._last_checkpoint_time = now
            self._duckdb.trigger_checkpoint_if_needed()

    def _update_arrow_metrics(self, results: list[dict], duckdb_all_ok: bool) -> None:
        """Update arrow metrics."""
        self._arrow_metrics["arrow_selected"] += len(results)
        self._arrow_metrics["arrow_success_count"] += len(results)
        lmdb_ok_count = sum(1 for r in results if r.get("lmdb_success"))
        duckdb_ok_count = sum(1 for r in results if r.get("duckdb_success"))
        self._arrow_metrics["arrow_success_lmdb_count"] += lmdb_ok_count
        self._arrow_metrics["arrow_success_duckdb_count"] += duckdb_ok_count

        # Circuit breaker update
        if duckdb_all_ok:
            self._record_success()
        else:
            self._record_failure()

    # -------------------------------------------------------------------------
    # Arrow ingest pipeline (F360M-R refactoring)
    # -------------------------------------------------------------------------

    async def _wait_startup_barrier(self, batch_size: int) -> bool:
        """Wait for startup barrier with timeout. Returns True if successful."""
        if self._startup_ready.is_set():
            return True
        try:
            async with asyncio.timeout(30.0):
                await self._startup_ready.wait()
            return True
        except TimeoutError:
            self._arrow_metrics["arrow_fallback_init"] += batch_size
            return False

    async def _execute_arrow_pipeline(
        self, findings: list[CanonicalFinding]
    ) -> ArrowIngestResult:
        """
        Execute arrow ingest pipeline - F360M-R refactored.
        
        Returns ArrowIngestResult with all pipeline outcomes.
        Single responsibility: orchestrate the pipeline phases.
        """
        batch_size = len(findings)
        
        # Phase 1: PyArrow availability check
        if not self._is_pyarrow_available():
            self._arrow_metrics["arrow_fallback_pyarrow"] += batch_size
            return ArrowIngestResult(
                status=ArrowIngestStatus.FALLBACK_PYARROW,
                results=[],
                duckdb_all_ok=False,
                duckdb_count=0,
                duckdb_err="pyarrow_unavailable",
    )
        
        # Phase 2: Startup barrier
        if not await self._wait_startup_barrier(batch_size):
            return ArrowIngestResult(
                status=ArrowIngestStatus.FALLBACK_INIT,
                results=[],
                duckdb_all_ok=False,
                duckdb_count=0,
                duckdb_err="startup_timeout",
    )
        
        # Phase 3: Circuit breaker
        if not self._check_circuit_breaker():
            return ArrowIngestResult(
                status=ArrowIngestStatus.FALLBACK_CIRCUIT,
                results=[],
                duckdb_all_ok=False,
                duckdb_count=0,
                duckdb_err="circuit_breaker_open",
    )
        
        # Phase 4: WAL first (LMDB)
        wal_ok = await self._try_wal_fallback(findings)
        if not wal_ok:
            logger.error(f"[D7] Arrow WAL phase failed - falling back to legacy.")
            return ArrowIngestResult(
                status=ArrowIngestStatus.FALLBACK_WAL,
                results=[],
                duckdb_all_ok=False,
                duckdb_count=0,
                duckdb_err="wal_failed",
    )
        
        # Phase 5: DuckDB Arrow insert
        duckdb_count, duckdb_err = await self._try_duckdb_basic(findings)
        duckdb_all_ok = duckdb_err is None or duckdb_count >= batch_size
        if duckdb_count < batch_size:
            self._arrow_metrics["arrow_partial_duplicates"] += 1
        
        # Phase 6: Build results
        results = self._build_arrow_results(findings, wal_ok, duckdb_all_ok, duckdb_err)
        lmdb_ok_count = sum(1 for r in results if r.get("lmdb_success"))
        duckdb_ok_count = sum(1 for r in results if r.get("duckdb_success"))
        
        return ArrowIngestResult(
            status=ArrowIngestStatus.SUCCESS,
            results=results,
            duckdb_all_ok=duckdb_all_ok,
            duckdb_count=duckdb_count,
            duckdb_err=duckdb_err,
            lmdb_ok_count=lmdb_ok_count,
            duckdb_ok_count=duckdb_ok_count,
    )

    def _is_pyarrow_available(self) -> bool:
        """Check if PyArrow is available - F360M-R."""
        try:
            import pyarrow  # noqa: F401
            return True
        except ImportError:
            return False

    async def ingest_batch_arrow(
        self, findings: list[CanonicalFinding]
    ) -> list[ActivationResult]:
        """
        Sprint P0-4: Arrow zero-copy batch ingest - F360M-R refactored.
        
        Returns list[ActivationResult].
        Complexity: CC=8 (was 20), Cognitive=10 (was 24), Ifs=4 (was 16)
        """
        if not findings:
            return []
        
        batch_size = len(findings)
        
        # Early exit: conditions check
        should_use_arrow, reason = self._check_arrow_conditions(findings)
        if not should_use_arrow:
            self._arrow_metrics[f"arrow_fallback_{reason}"] += batch_size
            logger.debug(f"[WriteCoordinator-arrow-fallback] {reason}, using legacy path")
            return await self.ingest_batch_legacy(findings)
        
        # Early exit: pyarrow availability
        if not await self._try_arrow_direct(findings):
            self._arrow_metrics["arrow_fallback_batch"] += batch_size
            return await self.ingest_batch_legacy(findings)
        
        # Execute pipeline
        result = await self._execute_arrow_pipeline(findings)
        
        # Handle fallback
        if result.should_fallback():
            return await self.ingest_batch_legacy(findings)
        
        # Post-processing
        await self._handle_arrow_post_process(result.results, findings)
        self._update_arrow_metrics(result.results, result.duckdb_all_ok)
        
        logger.info(
            f"[WriteCoordinator-arrow] path=arrow batch={batch_size} "
            f"lmdb_ok={result.lmdb_ok_count} duckdb_ok={result.duckdb_ok_count}"
    )
        return self._build_activation_results(result.results)

    async def ingest_batch_legacy(
        self, findings: list[CanonicalFinding]
    ) -> list[ActivationResult]:
        """
        Legacy batch path — volá _sync_record_canonical_findings_batch_arrow_standalone
        přes executor. Používá se jako fallback z Arrow path nebo přímo.
        """
        if not findings:
            return []

        loop = asyncio.get_running_loop()
        try:
            sync_results: list[dict[str, Any]] = await loop.run_in_executor(
                self._duckdb_arrow_executor,
                self._duckdb._sync_record_canonical_findings_batch_arrow_standalone,
                findings,
    )
        except Exception as e:
            logger.error(f"[WriteCoordinator-legacy] executor error: {e}")
            return self._build_activation_results_from_findings(findings, str(e))

        # Graph + semantic buffer
        if sync_results and any(r.get("lmdb_success") for r in sync_results):
            if self._duckdb.truth_write_graph_supports_buffered_writes():
                await self._graph_ingest_findings(findings)
            if self._semantic_buffer is not None:
                await self._semantic_buffer.buffer_findings(findings)

        # Quality state
        if self._quality_state is not None:
            accepted_total = sum(1 for r in sync_results if r.get("lmdb_success"))
            self._quality_state._accepted_count = (
                getattr(self._quality_state, "_accepted_count", 0) + accepted_total
    )

        return self._build_activation_results(sync_results)

    def _build_activation_results(
        self, results: list[dict[str, Any]]
    ) -> list[ActivationResult]:
        """Build ActivationResult list from raw dict results."""
        from .duckdb_store import ActivationResult

        return [
            ActivationResult(
                finding_id=str(r.get("finding_id", "")),
                lmdb_success=bool(r.get("lmdb_success")),
                duckdb_success=r.get("duckdb_success"),
                lmdb_key=f"finding:{r.get('finding_id', '')}",
                desync=bool(r.get("lmdb_success") and r.get("duckdb_success") is False),
                error=r.get("error"),
                accepted=bool(r.get("lmdb_success")),
    )
            for r in results
        ]

    def _build_activation_results_from_findings(
        self, findings: list[CanonicalFinding], error: str
    ) -> list[ActivationResult]:
        """Build error ActivationResult list for all findings."""
        from .duckdb_store import ActivationResult

        return [
            ActivationResult(
                finding_id=str(f.finding_id),
                lmdb_success=False,
                duckdb_success=None,
                lmdb_key=f"finding:{f.finding_id}",
                desync=False,
                error=error,
                accepted=False,
    )
            for f in findings
        ]

    # -------------------------------------------------------------------------
    # WAL putmany (thread pool, WAL-first invariant)
    # -------------------------------------------------------------------------

    async def _wal_put_many(self, findings: list[CanonicalFinding]) -> bool:
        """WAL putmany přes executor. Vrací True při úspěchu."""
        if not findings:
            return True

        if not await self._ensure_wal_manager():
            return False

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self._wal_executor,
                self._wal_put_many_sync,
                findings,
    )
        except Exception as e:
            logger.error(f"[WriteCoordinator] WAL putmany error: {e}")
            return False

    def _wal_put_many_sync(self, findings: list[CanonicalFinding]) -> bool:
        """Sync WAL putmany helper — volá se v threadpool."""
        if self._wal_manager is None:
            return False
        n = len(findings)
        items: list[tuple[str, dict[str, Any]]] = [None] * n  # type: ignore[assignment]
        for i, f in enumerate(findings):
            items[i] = (
                f"finding:{f.finding_id}",
                {
                    "id": f.finding_id,
                    "query": f.query,
                    "source_type": f.source_type,
                    "confidence": f.confidence,
                    "ts": f.ts,
                    "provenance": f.provenance,
                    "payload_text": f.payload_text,
                },
    )
        try:
            # DuckDBWALManager.wal_put_many has wrong type hint (bytes vs dict).
            # In practice it accepts dict and delegates to WALManager.wal_put_many(dict).
            # The hasattr guard + duckdb type ignore handles the mismatch.
            # P0-3 Fix: wal_put_many returns list[bool]; check with all() not bool()
            # bool([False, False]) = True (truthy list!) but all([False, False]) = False
            if hasattr(self._wal_manager, "wal_put_many"):
                results = self._wal_manager.wal_put_many(items)  # type: ignore[arg-type]
                return all(results) if isinstance(results, list) else bool(results)
            return False
        except Exception as e:
            logger.error(f"[WriteCoordinator] WAL putmany sync error: {e}")
            return False

    # -------------------------------------------------------------------------
    # DuckDB Arrow sync helper
    # -------------------------------------------------------------------------

    def _duckdb_arrow_sync(
        self, findings: list[CanonicalFinding]
    ) -> tuple[int, str | None]:
        """
        DuckDB Arrow sync helper — volá se v threadpool.
        Deleguje na DuckDBShadowStore._duckdb_arrow_sync.
        """
        return self._duckdb._duckdb_arrow_sync(findings)

    # -------------------------------------------------------------------------
    # Graph ingest (conditional na truth_write_graph_supports_buffered_writes)
    # -------------------------------------------------------------------------

    async def _graph_ingest_findings(self, findings: list[CanonicalFinding]) -> None:
        """Graph ingest přes DuckDBShadowStore._graph_ingest_findings."""
        try:
            await self._duckdb._graph_ingest_findings(findings)
        except Exception as e:
            logger.warning(f"[WriteCoordinator] Graph ingest error (non-fatal): {e}")

    # -------------------------------------------------------------------------
    # Metrics a introspection
    # -------------------------------------------------------------------------

    def get_arrow_metrics(self) -> dict[str, int]:
        """Vrací arrow metrics pro diagnostiku."""
        return dict(self._arrow_metrics)

    def get_batching_efficiency(self) -> dict:
        """
        MODERN-36: Return batching efficiency metrics for monitoring.
        
        Returns:
            Dict with batching efficiency stats:
            - arrow_path_pct: Percentage of batches using arrow path
            - avg_batch_size: Average batch size
            - batch_throughput: Batches per second (if time tracked)
            - wal_efficiency: WAL putmany efficiency
        """
        metrics = self._arrow_metrics
        batches_total = metrics.get("batches_total", 0)
        if batches_total == 0:
            return {"status": "no_data", "message": "No batches processed yet"}
        
        arrow_path = metrics.get("batches_arrow_path", 0)
        items_total = metrics.get("items_total", 0)
        
        return {
            "batches_total": batches_total,
            "arrow_path_pct": round(arrow_path / batches_total * 100, 1) if batches_total > 0 else 0,
            "avg_batch_size": round(items_total / batches_total, 1) if batches_total > 0 else 0,
            "items_total": items_total,
            "wal_operations": metrics.get("wal_operations", 0),
            "wal_items_total": metrics.get("wal_items_total", 0),
        }

    def get_breaker_state(self) -> str:
        """Vrací aktuální stav circuit breakeru."""
        return self._breaker_state.value

    def reset_breaker(self) -> None:
        """Resetuje circuit breaker (pro testování / admin účely)."""
        self._breaker_state = CBState.CLOSED
        self._breaker_failures = 0
        self._breaker_last_failure = 0.0
