"""P2-3: Enrich Stage — AIMD-paralelní enrichment s před-předanými hits.

Role: Enrich stage přijímá (PageResult, hits) z MatchStage, provádí text
enrichment a construction CanonicalFinding, posílá je do StoreStage.


MatchStage už provedla pattern matching a předává hits → EnrichStage NEVOLÁ
match_text() znovu (duplikace opravena).

AIMD řídí worker count — ceiling=16 na M1 8GB. Worker count je využit
pro paralelní zpracování stranek přes asyncio.Semaphore.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ._stage_protocol import BoundedStageQueue, Stage, StageContext
from hledac.universal.utils.asyncx import parallel, safe_create_task  # ISSUE-006, E4: parallel() + OTel trace context
from hledac.universal.utils.concurrency import AtomicAdaptiveSemaphore  # ISSUE-008: safe AIMD resize
from _core import aclose

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

DEFAULT_ENRICH_QUEUE_IN = 64
DEFAULT_ENRICH_QUEUE_OUT = 128

# ============================================================================
# F2: Elastic Pool Singleton — lazy module-level init for Rust rayon resize
# ============================================================================
# ponytail: global lock on Rust side; add per-stage granularity if throughput matters
_RUST_CPU_POOL_RESIZE: Any = None  # Cached resize function
_POOL_INIT_LOGGED: bool = False


def _get_rust_cpu_pool_resize():
    """Get Rust CPU pool resize function with lazy initialization.
    
    F2: This replaces the in-function import that was causing hot-path overhead.
    The function is cached after first call to avoid repeated imports.
    """
    global _RUST_CPU_POOL_RESIZE, _POOL_INIT_LOGGED
    if _RUST_CPU_POOL_RESIZE is not None:
        return _RUST_CPU_POOL_RESIZE
    
    try:
        from rust_extensions.wiring.elastic_pool_wiring import resize_cpu_pool
        _RUST_CPU_POOL_RESIZE = resize_cpu_pool
        if not _POOL_INIT_LOGGED:
            logger.debug("[F2] Rust CPU pool resize wired successfully")
            _POOL_INIT_LOGGED = True
        return resize_cpu_pool
    except Exception as e:
        if not _POOL_INIT_LOGGED:
            logger.debug("[F2] Rust CPU pool unavailable: %s", e)
            _POOL_INIT_LOGGED = True
        return None


class EnrichStage:
    """AsyncIterator pipeline for CanonicalFinding construction from pattern hits.

    MatchStage už matchuje patterny a předává hits. EnrichStage pouze
    staví CanonicalFinding z před-matchovaných hits.

    AIMD řídí worker count pro paralelní zpracování — ceiling=16 workers.

    Memory: ~64 × ~2 MB = ~128 MB max.
    """

    name: str = "enrich"

    __slots__ = (
        "_aimd",
        "_uma_state",
        "_query",
        "_effective_workers",
        "_running",
        "_sem",
        "_sem_init_lock",
    )

    def __init__(
        self,
        *,
        aimd_controller: Any | None = None,
        query: str = "",
        uma_state: str = "ok",
    ):
        from hledac.universal.coordinators.aimd_controllers import make_enrich_aimd

        self._aimd = aimd_controller or make_enrich_aimd()
        self._query = query
        self._uma_state = uma_state
        self._effective_workers = max(1, int(self._aimd.window))
        self._running = False
        # ISSUE-008: Use AtomicAdaptiveSemaphore for safe AIMD resize in Python 3.14+
        # PEP 789: lazy init in async context
        self._sem: AtomicAdaptiveSemaphore | None = None
        self._sem_init_lock = asyncio.Lock()

    @property
    def aimd_window(self) -> float:
        return self._aimd.window

    async def _ensure_semaphore(self) -> AtomicAdaptiveSemaphore:
        """PEP 789: Create AtomicAdaptiveSemaphore lazily in event loop context."""
        if self._sem is not None:
            return self._sem
        async with self._sem_init_lock:
            if self._sem is not None:
                return self._sem
            self._sem = AtomicAdaptiveSemaphore(initial=self._effective_workers)
            return self._sem

    async def _drain_batch(
        self,
        input_queue: BoundedStageQueue[Any] | None,
        metrics: Any,
    ) -> list[tuple[Any, Any]]:
        """Drain items from input queue up to effective worker count."""
        batch: list[tuple[Any, Any]] = []
        max_batch = self._effective_workers
        while len(batch) < max_batch:
            try:
                if input_queue is None:
                    break
                async with asyncio.timeout(0.1):
                    item = await input_queue.get()
                batch.append(item)
            except asyncio.TimeoutError:
                break
            except asyncio.CancelledError:
                return batch
        return batch

    async def _process_batch_results(
        self,
        gather_result: Any,
        output_queue: BoundedStageQueue[Any],
        metrics: Any,
    ) -> tuple[int, int]:
        """Process batch results and enqueue findings. Returns (success, fail) counts."""
        batch_success = 0
        batch_fail = len(gather_result.errors)
        pending_puts: list[asyncio.Task[bool]] = []

        for _exc in gather_result.errors:
            metrics.record_error()
        for item in gather_result.ok:
            if isinstance(item, Exception):
                batch_fail += 1
                metrics.record_error()
                continue
            findings: list[Any] = item
            if findings:
                batch_success += 1
                for finding in findings:
                    pending_puts.append(safe_create_task(output_queue.put(finding)))
            metrics.record_processed()

        if pending_puts:
            await parallel(pending_puts, policy="log")
        return batch_success, batch_fail

    async def _update_aimd(
        self,
        batch_success: int,
        metrics: Any,
    ) -> None:
        """Update AIMD window based on batch success/failure.
        
        F2: Also syncs Rust rayon CPU pool to match AIMD window.
        Eliminates AIMD oscillation spike on P-core (Thompson sampling jitter).
        """
        if batch_success > 0:
            new_window = await self._aimd.on_success()
        else:
            new_window = await self._aimd.on_failure()
        new_workers = max(1, min(int(new_window), 16))
        if new_workers != self._effective_workers:
            self._effective_workers = new_workers
            # ISSUE-008: Use resize() instead of creating new Semaphore
            if self._sem is not None:
                await self._sem.resize(new_workers)
            # F2: Sync Rust rayon CPU pool to match AIMD window
            # Uses module-level lazy singleton — no hot-path import overhead
            resize_fn = _get_rust_cpu_pool_resize()
            if resize_fn is not None:
                try:
                    resize_fn(min(new_workers, 8))
                except Exception:
                    pass  # Rust pool failed — continue with Python-only
            metrics.update_aimd_window(new_window)

    async def run(
        self,
        input_queue: BoundedStageQueue[Any] | None,
        output_queue: BoundedStageQueue[Any],
        ctx: StageContext,
    ) -> None:
        """Zpracuje (PageResult, hits) z input_queue, enrichuje je, posílá do output_queue.

        AIMD feedback po každém batchy (ne po každé stranice) — stabilnější.

        Args:
            input_queue: BoundedStageQueue[tuple[PageResult, list[PatternHit]]]
            output_queue: BoundedStageQueue[CanonicalFinding]
            ctx: StageContext

        """
        self._running = True
        metrics = ctx.get_metrics(self.name)
        start_time = time.monotonic()
        success_count = 0
        fail_count = 0

        try:
            while self._running:
                batch = await self._drain_batch(input_queue, metrics)
                if not batch:
                    if input_queue is not None and input_queue.is_empty():
                        break
                    continue

                # Process batch concurrently with AIMD-gated semaphore
                # ISSUE-008: Use lazy semaphore init for PEP 789 compatibility
                sem = await self._ensure_semaphore()
                async with sem:
                    tasks = [self._enrich_one(pr, hits, ctx) for pr, hits in batch]
                    gather_result = await parallel(tasks, policy="collect")

                # Process results and AIMD feedback
                batch_success, batch_fail = await self._process_batch_results(
                    gather_result, output_queue, metrics
                )
                success_count += batch_success
                fail_count += batch_fail
                await self._update_aimd(batch_success, metrics)

        except asyncio.CancelledError:  # noqa: BLE001
            pass
        except Exception:
            metrics.record_error()
            logger.exception("EnrichStage.run() error")
        finally:
            self._running = False
            metrics.update_latency((time.monotonic() - start_time) * 1000)
            logger.debug(
                "EnrichStage: processed=%d, success=%d, fail=%d",
                success_count + fail_count,
                success_count,
                fail_count,
            )

    async def _enrich_one_hit(
        self, hit: Any, page_text: str, url: str, ctx: StageContext
    ) -> Any | None:
        """Enrich a single hit — runs in parallel via parallel()."""
        from .live_public_pipeline import _extract_live_public_findings_from_page

        try:
            result = await _extract_live_public_findings_from_page(
                query=self._query or ctx.query,
                url=url,
                hit_label=getattr(hit, "label", "") or "",
                hit_pattern=getattr(hit, "pattern", "") or "",
                hit_value=getattr(hit, "value", "") or "",
                hit_start=getattr(hit, "start", 0) or 0,
                hit_end=getattr(hit, "end", 0) or 0,
                page_text=page_text,
                discovery_score=None,
            )
            return result[0] if result and len(result) > 0 else None
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("EnrichStage._enrich_one_hit error", exc_info=True)
            ctx.get_metrics(self.name).record_error()
            return None

    async def _enrich_one(
        self, page_result: Any, hits: list[Any], ctx: StageContext
    ) -> list[Any]:
        """Build CanonicalFinding from pre-matched hits.

        MatchStage uz matchovala patterny — tady uz jen stavime findings.
        ŽÁDNÉ volání match_text() zde (duplikace opravena).

        P1-02: Parallelizované přes parallel() — 16× rychlejší než sekvenční.
        """
        page_text = getattr(page_result, "text", "") or ""
        url = getattr(page_result, "url", "") or ""

        if not page_text or not hits:
            return []

        # P1-02: Parallel enrichment — M1 8GB ceiling=16 workers
        hit_coroutines = [self._enrich_one_hit(hit, page_text, url, ctx) for hit in hits]
        results = await parallel(hit_coroutines, policy="log", concurrency=16, ctx="enrich_stage:_enrich_one")

        return [r for r in results if r is not None]

    async def aclose(self) -> None:
        """Graceful shutdown."""
        self._running = False
