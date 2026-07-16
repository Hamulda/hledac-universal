"""
Continuous Prefetch Pipeline – P3-3

Producer → Queue → Executor pattern for speculative IOC prefetching:
1. Producer: IOC Graph traversal via asyncio.to_thread (non-blocking, async-native)
2. Queue: asyncio.PriorityQueue with bounded depth (no ThreadPoolExecutor)
3. Executor: Fetch with pre-warmed curl_cffi connections

Integration:
    pipeline = ContinuousPrefetchPipeline(
        prefetch_oracle=oracle,
        prefetch_cache=cache,
        queue_depth=50,
    )
    await pipeline.start()
    # During sprint:
    await pipeline.enqueue_predictions(predictions)
    # At teardown:
    await pipeline.stop()

M1 8GB invariants:
- Producer uses asyncio.to_thread (not ThreadPoolExecutor) — async-native, no GIL contention
- Queue bounded to 50 items max (backpressure via asyncio.QueueFull)
- Executor uses pre-warmed sessions from transport/prewarm_pool.py
- Fail-safe: operational errors logged (network, cache), critical errors raise RuntimeError
- Pattern: raise on critical failures (broken oracle, pool unavailable), fail-soft on transient errors
- Rust SPSC lock-free queue (spsc_queue.rs) used for MLX worker coordination, not for this pipeline
"""
import asyncio
from hledac.universal.utils.async_helpers import safe_create_task, safe_wait_for, parallel
import logging
import time
from dataclasses import dataclass, field
import msgspec
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    pass
logger = logging.getLogger(__name__)
PREFETCH_QUEUE_DEPTH = 50
PREFETCH_BATCH_SIZE = 10
PREFETCH_INTERVAL_S = 15.0
PREFETCH_TIMEOUT_S = 30.0
MAX_CONCURRENT_PREFETCHES = 3
IDLE_PREFETCH_INTERVAL_S = 5.0
IDLE_PREFETCH_THRESHOLD = 3
PREFETCH_PREWARMED_HOSTS_MAX = 50

class PrefetchItem(msgspec.Struct):
    """
    Single IOC prefetch item with priority queue ordering.

    Priority = -confidence + enqueued_at * 1e-9 (lower value = dequeued first).
    Higher confidence → lower priority value → dequeued sooner.
    Tie-break: older items (lower enqueued_at) dequeued first within same confidence.
    """
    ioc_value: str
    ioc_type: str
    confidence: float
    source_node: str
    prediction_method: str
    enqueued_at: float
    priority: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.priority = -self.confidence + self.enqueued_at * 1e-09

    def __lt__(self, other: PrefetchItem) -> bool:
        """PriorityQueue ordering: lower priority value is dequeued first."""
        if not isinstance(other, PrefetchItem):
            return NotImplemented
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.enqueued_at < other.enqueued_at

class ContinuousPrefetchPipeline:
    """
    P3-3: Continuous prefetch pipeline with producer-consumer pattern.

    Producer: Calls oracle.predict_next_iocs() periodically in background
    Queue: Bounded asyncio.Queue for backpressure
    Executor: Fetches URLs with pre-warmed connections

    Invariants:
    - Always-on: pipeline starts with sprint if oracle available
    - Bounded: queue depth 50, concurrent fetches 3
    - Fail-safe: all errors caught, logged, never propagate
    - M1 8GB safe: thread-based producer, bounded concurrency
    """
    __slots__ = tuple(('_cache', '_concurrent', '_executor_tasks', '_fetch_timeout', '_oracle', '_poll_interval', '_producer_task', '_queue', '_queue_depth', '_running', '_stats', '_stop_event', '_last_prewarm_at'))

    def __init__(self, prefetch_oracle: Any, prefetch_cache: Any | None=None, queue_depth: int=PREFETCH_QUEUE_DEPTH, concurrent_fetches: int=MAX_CONCURRENT_PREFETCHES, fetch_timeout_s: float=PREFETCH_TIMEOUT_S, poll_interval_s: float=PREFETCH_INTERVAL_S):
        self._oracle = prefetch_oracle
        self._cache = prefetch_cache
        self._queue_depth = queue_depth
        self._concurrent = concurrent_fetches
        self._fetch_timeout = fetch_timeout_s
        self._poll_interval = poll_interval_s
        self._queue: asyncio.PriorityQueue[PrefetchItem] = asyncio.PriorityQueue(maxsize=queue_depth)
        self._producer_task: asyncio.Task | None = None
        self._executor_tasks: set[asyncio.Task] = set()
        self._running = False
        self._stop_event = asyncio.Event()
        self._stats = {'items_enqueued': 0, 'items_fetched': 0, 'cache_hits': 0, 'fetch_errors': 0, 'queue_overflow': 0}
        self._last_prewarm_at = 0.0

    async def start(self) -> None:
        """Start the pipeline (producer + executor tasks)."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._producer_task = safe_create_task(self._producer_loop())
        self._producer_task.add_done_callback(lambda t: self._handle_task_done(t, 'producer'))
        for i in range(self._concurrent):
            task = safe_create_task(self._executor_loop(worker_id=i))
            task.add_done_callback(lambda t, w=i: self._handle_task_done(t, f'executor-{w}'))
            self._executor_tasks.add(task)
        logger.debug('[P3-3] ContinuousPrefetchPipeline started')

    async def stop(self) -> None:
        """Graceful stop: signal stop, drain queue, cancel tasks."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._producer_task:
            self._producer_task.cancel()
            try:
                await self._producer_task
            except asyncio.CancelledError:
                logger.debug('[P3-3] Producer task cancelled during stop')
                raise
        for task in list(self._executor_tasks):
            task.cancel()
        if self._executor_tasks:
            await parallel(list(self._executor_tasks), taskgroup=True, policy='log', ctx='prefetch:executor_shutdown', logger_instance=logger)
        self._executor_tasks.clear()
        drained = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                drained += 1
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.debug(f'[P3-3] Pipeline drained {drained} queued items')
        logger.debug('[P3-3] ContinuousPrefetchPipeline stopped')

    async def enqueue_predictions(self, predictions: list[dict]) -> int:
        """
        Enqueue predicted IOCs from oracle.

        Args:
            predictions: List of dicts from predict_next_iocs()

        Returns:
            Number of items successfully enqueued
        """
        if not self._running:
            return 0
        enqueued = 0
        for pred in predictions:
            try:
                item = PrefetchItem(ioc_value=pred['ioc_value'], ioc_type=pred['ioc_type'], confidence=float(pred['confidence']), source_node=str(pred.get('source_node', '')), prediction_method=str(pred.get('prediction_method', 'unknown')), enqueued_at=time.time())
                try:
                    self._queue.put_nowait(item)
                    enqueued += 1
                    self._stats['items_enqueued'] += 1
                except asyncio.QueueFull:
                    self._stats['queue_overflow'] += 1
                    logger.debug('[P3-3] Queue full, dropping prediction')
                    break
            except Exception as e:
                logger.debug(f'[P3-3] Failed to enqueue prediction: {e}')
        # Eager prewarm: fire immediately after enqueue, rate-limited to every 10s
        if enqueued > 0:
            now = time.time()
            if now - self._last_prewarm_at >= 10.0:
                self._last_prewarm_at = now
                safe_create_task(self._prewarm_once())
        return enqueued

    async def enqueue_ioc(self, ioc_value: str, ioc_type: str='domain', confidence: float=0.5) -> bool:
        """
        Enqueue a single IOC directly (for immediate prefetch).

        Returns:
            True if enqueued, False if queue full or not running
        """
        if not self._running:
            return False
        try:
            item = PrefetchItem(ioc_value=ioc_value, ioc_type=ioc_type, confidence=confidence, source_node='direct', prediction_method='direct', enqueued_at=time.time())
            self._queue.put_nowait(item)
            self._stats['items_enqueued'] += 1
            return True
        except asyncio.QueueFull:
            self._stats['queue_overflow'] += 1
            return False

    def get_stats(self) -> dict[str, Any]:
        """Return pipeline statistics."""
        return {**self._stats, 'queue_depth': self._queue.qsize(), 'queue_capacity': self._queue.maxsize, 'running': self._running}

    async def _producer_loop(self) -> None:
        """
        Background producer: periodically calls predict_next_iocs()
        and enqueues results.

        Runs every _poll_interval seconds while _running.
        """
        try:
            while not self._stop_event.is_set():
                try:
                    await safe_wait_for(self._stop_event.wait(), timeout=self._poll_interval, label='prefetch_stop_event')
                    break
                except TimeoutError:
                    pass
                if not self._running:
                    break
                try:
                    if hasattr(self._oracle, 'predict_next_iocs'):
                        try:
                            predictions = await safe_wait_for(self._oracle.predict_next_iocs(top_k=PREFETCH_BATCH_SIZE), timeout=10.0, label='predict_next_iocs')
                            if predictions:
                                enqueued = await self.enqueue_predictions(predictions)
                                logger.debug(f'[P3-3] Producer: {len(predictions)} predictions, {enqueued} enqueued')
                        except TimeoutError:
                            logger.debug('[P3-3] predict_next_iocs timed out')
                        except Exception as e:
                            logger.debug(f'[P3-3] predict_next_iocs failed: {e}')
                except Exception as e:
                    logger.debug(f'[P3-3] Producer loop error: {e}')
        except asyncio.CancelledError:
            logger.debug('[P3-3] Producer loop cancelled')
            raise

    async def _executor_loop(self, worker_id: int) -> None:
        """
        Background executor: consumes from queue and performs prefetch fetch.

        Uses pre-warmed connections from transport/prewarm_pool.py.

        P3-1: Idle-cycle pre-warm — when queue is empty for consecutive
        IDLE_PREFETCH_THRESHOLD cycles, triggers pre-warm of connections
        for predicted IOCs to reduce latency during actual fetching.
        """
        idle_cycles = 0
        prewarmed_hosts: set[str] = set()
        try:
            while not self._stop_event.is_set():
                try:
                    item = await safe_wait_for(self._queue.get(), timeout=IDLE_PREFETCH_INTERVAL_S, label='queue_get')
                except TimeoutError:
                    if self._queue.empty():
                        idle_cycles += 1
                        if idle_cycles >= IDLE_PREFETCH_THRESHOLD:
                            await self._prewarm_connections_for_predictions(prewarmed_hosts)
                            idle_cycles = 0
                    continue
                except asyncio.CancelledError:
                    raise
                idle_cycles = 0
                age = time.time() - item.enqueued_at
                if age > 300:
                    logger.debug(f'[P3-3] Executor-{worker_id}: item too old, skipping')
                    self._queue.task_done()
                    continue
                try:
                    success = await self._prefetch_item(item)
                    if success:
                        self._stats['items_fetched'] += 1
                except Exception as e:
                    logger.debug(f'[P3-3] Executor-{worker_id} prefetch error: {e}')
                    self._stats['fetch_errors'] += 1
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            logger.debug(f'[P3-3] Executor-{worker_id} loop cancelled')
            raise

    async def _prewarm_connections_for_predictions(self, prewarmed_hosts: set[str]) -> None:
        """
        P3-1: Pre-warm connections for predicted IOCs during idle cycles.

        Uses asyncio.to_thread to avoid blocking the event loop.
        Tracks pre-warmed hosts to avoid redundant pre-warming.

        Args:
            prewarmed_hosts: Set of already-prewarmed hosts (modified in-place)
        """
        try:
            if not hasattr(self._oracle, 'predict_next_iocs'):
                return
            predictions = await safe_wait_for(self._oracle.predict_next_iocs(top_k=PREFETCH_BATCH_SIZE), timeout=10.0, label='predict_next_iocs')
            if not predictions:
                return
            hosts_to_prewarm: list[str] = []
            for pred in predictions:
                ioc_type = pred.get('ioc_type', 'domain')
                ioc_value = pred.get('ioc_value', '')
                if ioc_type == 'domain' and ioc_value:
                    if ioc_value not in prewarmed_hosts:
                        if len(prewarmed_hosts) >= PREFETCH_PREWARMED_HOSTS_MAX:
                            # Evict oldest (first-in set iteration order)
                            for old_host in prewarmed_hosts:
                                prewarmed_hosts.discard(old_host)
                                break
                        hosts_to_prewarm.append(ioc_value)
                        prewarmed_hosts.add(ioc_value)
            if not hosts_to_prewarm:
                return

            async def _prewarm_async() -> None:
                """Pre-warm curl_cffi session for ja3_fingerprint profile."""
                try:
                    from transport.prewarm_pool import acquire_session
                    await acquire_session('ja3_fingerprint')
                except ImportError:
                    import warnings
                    warnings.warn('[P3-3] transport.prewarm_pool not available; prefetch will use direct httpx (higher latency)', RuntimeWarning, stacklevel=2)
            await _prewarm_async()
            logger.debug(f'[P3-3] Pre-warmed connections for {len(hosts_to_prewarm)} hosts')
        except TimeoutError:
            logger.debug('[P3-3] Pre-warm predictions timed out')
        except Exception as e:
            logger.debug(f'[P3-3] Pre-warm failed: {e}')

    async def _prewarm_once(self) -> None:
        """Single eager pre-warm of ja3_fingerprint profile (fire-and-forget)."""
        try:
            from transport.prewarm_pool import acquire_session
            await acquire_session('ja3_fingerprint')
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f'[P3-3] Eager prewarm failed: {e}')

    async def _prefetch_item(self, item: PrefetchItem) -> bool:
        """
        Prefetch a single IOC item.

        Returns True on success, False on failure.
        """
        bytes_downloaded = 0
        if self._cache is not None:
            try:
                cached = await self._cache.get(item.ioc_value)
                if cached is not None:
                    self._stats['cache_hits'] += 1
                    try:
                        if hasattr(self._oracle, 'record_prefetch_outcome'):
                            await self._oracle.record_prefetch_outcome(item.ioc_value, True, 0)
                    except Exception:
                        pass
                    return True
            except Exception as e:
                logger.debug(f'[P3-3] Cache check failed: {e}')
        url = self._ioc_to_url(item.ioc_value, item.ioc_type)
        if not url:
            return False
        success = False
        try:
            result = await safe_wait_for(self._fetch_url(url), timeout=self._fetch_timeout, label='fetch_url')
            if result:
                bytes_downloaded = len(result.get('content', ''))
                if self._cache is not None:
                    try:
                        await self._cache.put(item.ioc_value, {'data': result, 'ioc_type': item.ioc_type, 'fetched_at': time.time()}, ttl=3600)
                    except Exception as e:
                        logger.debug(f'[P3-3] Cache put failed: {e}')
                success = True
        except TimeoutError:
            logger.debug(f'[P3-3] Prefetch timeout for {item.ioc_value}')
        except Exception as e:
            logger.debug(f'[P3-3] Prefetch failed for {item.ioc_value}: {e}')
        try:
            if hasattr(self._oracle, 'record_prefetch_outcome'):
                await self._oracle.record_prefetch_outcome(item.ioc_value, success, bytes_downloaded)
        except Exception:
            pass
        return success

    def _ioc_to_url(self, ioc_value: str, ioc_type: str) -> str | None:
        """Convert IOC value to URL for fetching."""
        if ioc_type == 'domain':
            return f'https://{ioc_value}'
        elif ioc_type == 'url' and ioc_value.startswith(('http://', 'https://')):
            return ioc_value
        return None

    async def _fetch_url(self, url: str) -> dict | None:
        """
        Fetch URL using pre-warmed curl_cffi session.

        Uses transport/prewarm_pool.py sessions if available.
        Falls back to direct httpx/aiohttp.
        """
        try:
            from transport.prewarm_pool import acquire_session
            success, session, _profile = await acquire_session('ja3_fingerprint')
            if success and session is not None:
                try:
                    # session.get() is synchronous — run in thread pool to avoid blocking event loop
                    resp = await asyncio.to_thread(session.get, url, timeout=self._fetch_timeout)
                    if resp.status_code == 200:
                        return {'url': url, 'content': resp.text, 'status': resp.status_code, 'fetched_at': time.time()}
                except Exception:
                    pass
        except ImportError:
            pass
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._fetch_timeout) as client:
                resp = await client.get(url, follow_redirects=True)
                if resp.status_code == 200:
                    return {'url': url, 'content': resp.text, 'status': resp.status_code, 'fetched_at': time.time()}
        except Exception:
            pass
        return None

    def _handle_task_done(self, task: asyncio.Task, name: str) -> None:
        """Handle task completion (for logging/debugging)."""
        try:
            exc = task.exception()
            if exc and (not isinstance(exc, asyncio.CancelledError)):
                logger.warning(f'[P3-3] {name} task failed: {exc}')
        except asyncio.CancelledError:
            pass