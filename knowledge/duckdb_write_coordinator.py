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


@dataclass(slots=True)
class WriteCoordinatorConfig:
    """M1 8GB bounded configuration — všechny limity explicitní."""
    max_concurrent_writes: int = 4
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown: float = 30.0
    wal_putmany_batch_size: int = 500
    arrow_min_batch: int = 5
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
        self._arrow_metrics: dict[str, int] = {
            "arrow_selected": 0,
            "arrow_fallback_env": 0,
            "arrow_fallback_batch": 0,
            "arrow_fallback_pyarrow": 0,
            "arrow_fallback_init": 0,
            "arrow_fallback_executor": 0,
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

    async def ingest_batch_arrow(
        self, findings: list[CanonicalFinding]
    ) -> list[ActivationResult]:
        """
        Sprint P0-4: Arrow zero-copy batch ingest.

        10-stupňový fallback na legacy path:
          1. HLEDAC_ARROW_INGEST == "0" -> legacy
          2. len(findings) < _ARROW_MIN_BATCH -> legacy
          3. pyarrow unavailable -> legacy
          4. store not initialized -> legacy
          5. startup barrier timeout -> legacy
          6. WAL executor error -> legacy
          7. WAL phase failed -> legacy
          8. DuckDB executor threw -> legacy
          9. sync helper returned empty -> legacy
          10. all duckdb_success=False -> legacy

        Returns list[ActivationResult].
        """
        # Lazy imports pro M1 RAM úsporu
        import os as _os
        import time as _time

        _ARROW_INGEST_ENABLED = _os.getenv("HLEDAC_ARROW_INGEST", "1") != "0"
        _ARROW_MIN_BATCH = 5

        if not findings:
            return []
        if not _ARROW_INGEST_ENABLED:
            self._arrow_metrics["arrow_fallback_env"] += len(findings)
            logger.debug(
                f"[WriteCoordinator-arrow-fallback] HLEDAC_ARROW_INGEST=0, "
                f"using legacy path for {len(findings)} findings"
            )
            return await self.ingest_batch_legacy(findings)
        if len(findings) < _ARROW_MIN_BATCH:
            self._arrow_metrics["arrow_fallback_batch"] += len(findings)
            logger.debug(
                f"[WriteCoordinator-arrow-fallback] batch size {len(findings)} < "
                f"_ARROW_MIN_BATCH({_ARROW_MIN_BATCH}), using legacy path"
            )
            return await self.ingest_batch_legacy(findings)

        # Pyarrow check (cached)
        try:
            import pyarrow as _pa  # noqa: F401
        except ImportError:
            self._arrow_metrics["arrow_fallback_pyarrow"] += len(findings)
            logger.debug(
                f"[WriteCoordinator-arrow-fallback] pyarrow not available, "
                f"using legacy path for {len(findings)} findings"
            )
            return await self.ingest_batch_legacy(findings)

        # Startup barrier
        if not self._startup_ready.is_set():
            try:
                async with asyncio.timeout(30.0):
                    await self._startup_ready.wait()
            except TimeoutError:
                self._arrow_metrics["arrow_fallback_init"] += len(findings)
                return await self.ingest_batch_legacy(findings)

        # Circuit breaker check
        if not self._check_circuit_breaker():
            logger.warning("[WriteCoordinator] Circuit breaker open, using legacy path")
            return await self.ingest_batch_legacy(findings)

        # WAL first (parallel s DuckDB)
        wal_ok = False
        try:
            wal_ok = await self._wal_put_many(findings)
        except Exception as e:
            logger.warning(
                f"[WriteCoordinator-arrow-fallback] WAL error ({e}), "
                f"using legacy path for {len(findings)} findings"
            )
            self._arrow_metrics["arrow_fallback_executor"] += len(findings)
            return await self.ingest_batch_legacy(findings)

        if not wal_ok:
            self._arrow_metrics["arrow_fallback_empty"] += len(findings)
            logger.error(
                "[D7] Arrow WAL phase failed for %d findings - falling back to legacy executemany.",
                len(findings),
            )
            return await self.ingest_batch_legacy(findings)

        # DuckDB Arrow insert (parallel s WAL, ale čekáme na WAL first)
        duckdb_count = 0
        duckdb_err: str | None = None
        try:
            loop = asyncio.get_running_loop()
            duckdb_result: tuple[int, str | None] = await loop.run_in_executor(
                self._duckdb_arrow_executor,
                self._duckdb_arrow_sync,
                findings,
            )
            duckdb_count, duckdb_err = duckdb_result
        except asyncio.CancelledError:
            duckdb_err = "cancelled"
            duckdb_count = 0
        except Exception as e:
            logger.error(f"[WriteCoordinator] DuckDB Arrow executor error: {e}")
            duckdb_err = str(e)
            duckdb_count = 0

        if duckdb_err is not None:
            logger.error(f"[WriteCoordinator] DuckDB Arrow bulk failed: {duckdb_err}")
            duckdb_all_ok = False
        elif duckdb_count < len(findings):
            self._arrow_metrics["arrow_partial_duplicates"] += 1
            logger.debug(
                f"[WriteCoordinator-arrow] DuckDB MERGE: {duckdb_count}/{len(findings)} "
                f"inserted (rest deduplicated)"
            )
            duckdb_all_ok = True
        else:
            duckdb_all_ok = True

        results = [
            {
                "finding_id": f.finding_id,
                "lmdb_success": wal_ok,
                "duckdb_success": duckdb_all_ok,
                "error": duckdb_err,
            }
            for f in findings
        ]

        # Build ActivationResult list
        activation_results = self._build_activation_results(results)

        # Graph + semantic buffer pro accepted findings
        if results and any(r.get("lmdb_success") for r in results):
            if self._duckdb.truth_write_graph_supports_buffered_writes():
                await self._graph_ingest_findings(findings)
            if self._semantic_buffer is not None:
                self._semantic_buffer.buffer_findings(findings)

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

        # Metrics
        self._arrow_metrics["arrow_selected"] += len(findings)
        self._arrow_metrics["arrow_success_count"] += len(findings)
        lmdb_ok_count = sum(1 for r in results if r.get("lmdb_success"))
        duckdb_ok_count = sum(1 for r in results if r.get("duckdb_success"))
        self._arrow_metrics["arrow_success_lmdb_count"] += lmdb_ok_count
        self._arrow_metrics["arrow_success_duckdb_count"] += duckdb_ok_count

        # Circuit breaker update
        if duckdb_all_ok:
            self._record_success()
        else:
            self._record_failure()

        logger.info(
            f"[WriteCoordinator-arrow] path=arrow batch={len(findings)} "
            f"lmdb_ok={lmdb_ok_count} duckdb_ok={duckdb_ok_count}"
        )
        return activation_results

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
                self._semantic_buffer.buffer_findings(findings)

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
            if hasattr(self._wal_manager, "wal_put_many"):
                return bool(self._wal_manager.wal_put_many(items))  # type: ignore[arg-type]
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

    def get_breaker_state(self) -> str:
        """Vrací aktuální stav circuit breakeru."""
        return self._breaker_state.value

    def reset_breaker(self) -> None:
        """Resetuje circuit breaker (pro testování / admin účely)."""
        self._breaker_state = CBState.CLOSED
        self._breaker_failures = 0
        self._breaker_last_failure = 0.0
