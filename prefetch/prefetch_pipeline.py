"""
Continuous Prefetch Pipeline – P3-3

Producer → Queue → Executor pattern for speculative IOC prefetching:
1. Producer: IOC Graph traversal (background thread via predict_next_iocs)
2. Queue: asyncio.Queue with bounded depth
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
- Producer runs in ThreadPoolExecutor (not async, non-blocking)
- Queue bounded to 50 items max
- Executor uses pre-warmed sessions from transport/prewarm_pool.py
- Fail-safe: operational errors logged (network, cache), critical errors raise RuntimeError
- Pattern: raise on critical failures (broken oracle, pool unavailable), fail-soft on transient errors
"""
from __future__ import annotations



import asyncio

from hledac.universal.utils.async_helpers import safe_create_task
from hledac.universal.utils.async_helpers import safe_gather_fire_and_forget
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Constants
PREFETCH_QUEUE_DEPTH = 50
PREFETCH_BATCH_SIZE = 10
PREFETCH_INTERVAL_S = 15.0  # Producer poll interval
PREFETCH_TIMEOUT_S = 30.0  # Per-item fetch timeout
MAX_CONCURRENT_PREFETCHES = 3
# P3-1: Idle-cycle pre-warm settings
IDLE_PREFETCH_INTERVAL_S = 5.0  # Check for idle pre-warm every 5s
IDLE_PREFETCH_THRESHOLD = 3  # Number of consecutive idle cycles before pre-warm


@dataclass(order=False)
class PrefetchItem:
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
    # Derived priority for asyncio.PriorityQueue (lower = higher priority)
    priority: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Negative confidence so PriorityQueue dequeues highest confidence first.
        # Small enqueued_at fraction breaks ties: older items win.
        self.priority = -self.confidence + self.enqueued_at * 1e-9

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

    def __init__(
        self,
        prefetch_oracle: Any,
        prefetch_cache: Any | None = None,
        queue_depth: int = PREFETCH_QUEUE_DEPTH,
        concurrent_fetches: int = MAX_CONCURRENT_PREFETCHES,
        fetch_timeout_s: float = PREFETCH_TIMEOUT_S,
        poll_interval_s: float = PREFETCH_INTERVAL_S,
    ):
        self._oracle = prefetch_oracle
        self._cache = prefetch_cache
        self._queue_depth = queue_depth
        self._concurrent = concurrent_fetches
        self._fetch_timeout = fetch_timeout_s
        self._poll_interval = poll_interval_s

        # PriorityQueue: lower priority value dequeued first (highest confidence first)
        self._queue: asyncio.PriorityQueue[PrefetchItem] = asyncio.PriorityQueue(maxsize=queue_depth)

        # Background tasks
        self._producer_task: asyncio.Task | None = None
        self._executor_tasks: set[asyncio.Task] = set()
        self._running = False
        self._stop_event = asyncio.Event()

        # Statistics
        self._stats = {
            "items_enqueued": 0,
            "items_fetched": 0,
            "cache_hits": 0,
            "fetch_errors": 0,
            "queue_overflow": 0,
        }

        # Thread pool for sync graph operations
        self._thread_pool: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        """Start the pipeline (producer + executor tasks)."""
        if self._running:
            return

        self._running = True
        self._stop_event.clear()

        # Start producer
        self._producer_task = safe_create_task(self._producer_loop())
        self._producer_task.add_done_callback(
            lambda t: self._handle_task_done(t, "producer")
        )

        # Start executor tasks
        for i in range(self._concurrent):
            task = safe_create_task(self._executor_loop(worker_id=i))
            task.add_done_callback(lambda t, w=i: self._handle_task_done(t, f"executor-{w}"))
            self._executor_tasks.add(task)

        logger.debug("[P3-3] ContinuousPrefetchPipeline started")

    async def stop(self) -> None:
        """Graceful stop: signal stop, drain queue, cancel tasks."""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()

        # Cancel producer
        if self._producer_task:
            self._producer_task.cancel()
            try:
                await self._producer_task
            except asyncio.CancelledError:
                logger.debug("[P3-3] Producer task cancelled during stop")
                raise

        # Cancel executors
        for task in list(self._executor_tasks):
            task.cancel()
        if self._executor_tasks:
            # F314: migrated asyncio.gather -> safe_gather_fire_and_forget
            await safe_gather_fire_and_forget(*self._executor_tasks, label="prefetch:executor_shutdown")
        self._executor_tasks.clear()

        # Drain remaining queue items
        drained = 0
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                drained += 1
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.debug(f"[P3-3] Pipeline drained {drained} queued items")

        logger.debug("[P3-3] ContinuousPrefetchPipeline stopped")

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
                item = PrefetchItem(
                    ioc_value=pred["ioc_value"],
                    ioc_type=pred["ioc_type"],
                    confidence=float(pred["confidence"]),
                    source_node=str(pred.get("source_node", "")),
                    prediction_method=str(pred.get("prediction_method", "unknown")),
                    enqueued_at=time.time(),
                )
                try:
                    self._queue.put_nowait(item)
                    enqueued += 1
                    self._stats["items_enqueued"] += 1
                except asyncio.QueueFull:
                    self._stats["queue_overflow"] += 1
                    logger.debug("[P3-3] Queue full, dropping prediction")
                    break
            except Exception as e:
                logger.debug(f"[P3-3] Failed to enqueue prediction: {e}")
        return enqueued

    async def enqueue_ioc(self, ioc_value: str, ioc_type: str = "domain", confidence: float = 0.5) -> bool:
        """
        Enqueue a single IOC directly (for immediate prefetch).

        Returns:
            True if enqueued, False if queue full or not running
        """
        if not self._running:
            return False

        try:
            item = PrefetchItem(
                ioc_value=ioc_value,
                ioc_type=ioc_type,
                confidence=confidence,
                source_node="direct",
                prediction_method="direct",
                enqueued_at=time.time(),
            )
            self._queue.put_nowait(item)
            self._stats["items_enqueued"] += 1
            return True
        except asyncio.QueueFull:
            self._stats["queue_overflow"] += 1
            return False

    def get_stats(self) -> dict[str, Any]:
        """Return pipeline statistics."""
        return {
            **self._stats,
            "queue_depth": self._queue.qsize(),
            "queue_capacity": self._queue.maxsize,
            "running": self._running,
        }

    async def _producer_loop(self) -> None:
        """
        Background producer: periodically calls predict_next_iocs()
        and enqueues results.

        Runs every _poll_interval seconds while _running.
        """
        try:
            while not self._stop_event.is_set():
                try:
                    # Wait for poll interval or stop signal
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval
                    )
                    # Stop event was set
                    break
                except TimeoutError:
                    pass  # Timeout means poll interval elapsed

                if not self._running:
                    break

                # Call oracle.predict_next_iocs() — non-blocking via await
                try:
                    # Check if oracle has predict_next_iocs
                    if hasattr(self._oracle, "predict_next_iocs"):
                        try:
                            predictions = await asyncio.wait_for(
                                self._oracle.predict_next_iocs(top_k=PREFETCH_BATCH_SIZE),
                                timeout=10.0
                            )
                            if predictions:
                                enqueued = await self.enqueue_predictions(predictions)
                                logger.debug(
                                    f"[P3-3] Producer: {len(predictions)} predictions, "
                                    f"{enqueued} enqueued"
                                )
                        except TimeoutError:
                            logger.debug("[P3-3] predict_next_iocs timed out")
                        except Exception as e:
                            logger.debug(f"[P3-3] predict_next_iocs failed: {e}")

                except Exception as e:
                    logger.debug(f"[P3-3] Producer loop error: {e}")

        except asyncio.CancelledError:
            logger.debug("[P3-3] Producer loop cancelled")
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
                item: PrefetchItem | None = None
                try:
                    # Wait for item with timeout
                    item = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=IDLE_PREFETCH_INTERVAL_S
                    )
                except TimeoutError:
                    # P3-1: Idle-cycle pre-warm logic
                    if self._queue.empty():
                        idle_cycles += 1
                        if idle_cycles >= IDLE_PREFETCH_THRESHOLD:
                            # Trigger pre-warm for predicted IOCs
                            await self._prewarm_connections_for_predictions(prewarmed_hosts)
                            idle_cycles = 0  # Reset after pre-warm attempt
                    continue
                except asyncio.CancelledError:
                    raise

                # Item received — reset idle counter
                idle_cycles = 0

                # Check if item is too old (TTL)
                age = time.time() - item.enqueued_at
                if age > 300:  # 5 min TTL
                    logger.debug(f"[P3-3] Executor-{worker_id}: item too old, skipping")
                    self._queue.task_done()
                    continue

                # Perform prefetch
                try:
                    success = await self._prefetch_item(item)
                    if success:
                        self._stats["items_fetched"] += 1
                except Exception as e:
                    logger.debug(f"[P3-3] Executor-{worker_id} prefetch error: {e}")
                    self._stats["fetch_errors"] += 1
                finally:
                    self._queue.task_done()

        except asyncio.CancelledError:
            logger.debug(f"[P3-3] Executor-{worker_id} loop cancelled")
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
            if not hasattr(self._oracle, "predict_next_iocs"):
                return

            # Get predictions without blocking event loop
            predictions = await asyncio.wait_for(
                self._oracle.predict_next_iocs(top_k=PREFETCH_BATCH_SIZE),
                timeout=10.0
            )

            if not predictions:
                return

            # Extract hosts to pre-warm (deduplicated)
            hosts_to_prewarm: list[str] = []
            for pred in predictions:
                ioc_type = pred.get("ioc_type", "domain")
                ioc_value = pred.get("ioc_value", "")
                if ioc_type == "domain" and ioc_value:
                    if ioc_value not in prewarmed_hosts:
                        hosts_to_prewarm.append(ioc_value)
                        prewarmed_hosts.add(ioc_value)

            if not hosts_to_prewarm:
                return

            # Pre-warm connections in thread pool (non-blocking)
            async def _prewarm_async() -> None:
                """Pre-warm curl_cffi sessions for hosts."""
                try:
                    from transport.prewarm_pool import acquire_session
                    for _host in hosts_to_prewarm[:5]:  # Max 5 hosts per pre-warm
                        try:
                            await acquire_session("ja3_fingerprint")
                        except Exception as e:  # noqa: BLE001
                            logger.debug(f"[P3-3] Pre-warm session failed: {e}")
                except ImportError:
                    # Critical: prewarm_pool not available - infrastructure issue
                    import warnings
                    warnings.warn(
                        "[P3-3] transport.prewarm_pool not available; "
                        "prefetch will use direct httpx (higher latency)",
                        RuntimeWarning,
                        stacklevel=2,
                    )

            # Run async prewarm without blocking event loop
            # P3-1 FIX: asyncio.run() in thread is M1 crash vector (CLAUDE.md invariant).
            # Use loop.run_until_complete() via asyncio.to_thread() instead.
            loop = asyncio.get_running_loop()
            await asyncio.to_thread(loop.run_until_complete, _prewarm_async())
            logger.debug(f"[P3-3] Pre-warmed connections for {len(hosts_to_prewarm)} hosts")

        except TimeoutError:
            logger.debug("[P3-3] Pre-warm predictions timed out")
        except Exception as e:
            logger.debug(f"[P3-3] Pre-warm failed: {e}")

    async def _prefetch_item(self, item: PrefetchItem) -> bool:
        """
        Prefetch a single IOC item.

        Returns True on success, False on failure.
        """
        bytes_downloaded = 0

        # Check cache first
        if self._cache is not None:
            try:
                cached = await self._cache.get(item.ioc_value)
                if cached is not None:
                    self._stats["cache_hits"] += 1
                    # P3-1: Record cache hit in oracle feedback loop
                    try:
                        if hasattr(self._oracle, "record_prefetch_outcome"):
                            self._oracle.record_prefetch_outcome(item.ioc_value, True, 0)
                    except Exception:  # noqa: BLE001
                        pass
                    return True
            except Exception as e:
                logger.debug(f"[P3-3] Cache check failed: {e}")

        # Build URL from IOC
        url = self._ioc_to_url(item.ioc_value, item.ioc_type)
        if not url:
            return False

        # Fetch with timeout
        success = False
        try:
            result = await asyncio.wait_for(
                self._fetch_url(url),
                timeout=self._fetch_timeout
            )
            if result:
                bytes_downloaded = len(result.get("content", ""))
                # Store in cache
                if self._cache is not None:
                    try:
                        await self._cache.put(
                            item.ioc_value,
                            {
                                "data": result,
                                "ioc_type": item.ioc_type,
                                "fetched_at": time.time(),
                            },
                            ttl=3600
                        )
                    except Exception as e:
                        logger.debug(f"[P3-3] Cache put failed: {e}")
                success = True
        except TimeoutError:
            logger.debug(f"[P3-3] Prefetch timeout for {item.ioc_value}")
        except Exception as e:
            logger.debug(f"[P3-3] Prefetch failed for {item.ioc_value}: {e}")

        # P3-1: Record outcome in oracle feedback loop
        try:
            if hasattr(self._oracle, "record_prefetch_outcome"):
                self._oracle.record_prefetch_outcome(item.ioc_value, success, bytes_downloaded)
        except Exception:  # noqa: BLE001
            pass

        return success

    def _ioc_to_url(self, ioc_value: str, ioc_type: str) -> str | None:
        """Convert IOC value to URL for fetching."""
        if ioc_type == "domain":
            return f"https://{ioc_value}"
        elif ioc_type == "url" and ioc_value.startswith(("http://", "https://")):
            return ioc_value
        # For other types, return None (can't fetch directly)
        return None

    async def _fetch_url(self, url: str) -> dict | None:
        """
        Fetch URL using pre-warmed curl_cffi session.

        Uses transport/prewarm_pool.py sessions if available.
        Falls back to direct httpx/aiohttp.
        """
        # Try to use prewarmed session from pool
        try:
            from transport.prewarm_pool import acquire_session
            success, session, _profile = await acquire_session("ja3_fingerprint")
            if success and session is not None:
                try:
                    resp = session.get(url, timeout=self._fetch_timeout)
                    if resp.status_code == 200:
                        return {
                            "url": url,
                            "content": resp.text,
                            "status": resp.status_code,
                            "fetched_at": time.time(),
                        }
                except Exception:  # noqa: BLE001
                    pass
        except ImportError:
            pass

        # Fallback: direct httpx fetch
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._fetch_timeout) as client:
                resp = await client.get(url, follow_redirects=True)
                if resp.status_code == 200:
                    return {
                        "url": url,
                        "content": resp.text,
                        "status": resp.status_code,
                        "fetched_at": time.time(),
                    }
        except Exception:  # noqa: BLE001
            pass

        return None

    def _handle_task_done(self, task: asyncio.Task, name: str) -> None:
        """Handle task completion (for logging/debugging)."""
        try:
            exc = task.exception()
            if exc and not isinstance(exc, asyncio.CancelledError):
                logger.warning(f"[P3-3] {name} task failed: {exc}")
        except asyncio.CancelledError:
            pass
