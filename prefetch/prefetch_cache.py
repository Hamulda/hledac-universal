"""
PrefetchCache – dočasné úložiště pro prefetched data s LRU, TTL a background writerem.
"""
import asyncio


import logging
import time
from pathlib import Path
from typing import Any
import orjson
from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_fire_and_forget
logger = logging.getLogger(__name__)

# C3-03: Bounded write queue with backpressure.
# asyncio.Queue(maxsize=0) is unlimited; maxsize=N enables true backpressure.
# Producers receive False on full (timeout) instead of silent drops.
_QUEUE_MAXSIZE = 2000
_QUEUE_PUT_TIMEOUT = 5.0  # seconds — prevents indefinite blocking if writer stalls

class _BoundedWriteQueue:
    """asyncio.Queue wrapper that applies backpressure on full instead of dropping.

    - ``put()`` blocks up to _QUEUE_PUT_TIMEOUT seconds, then returns False.
    - ``get()`` is unchanged (standard Queue semantics).
    - Producers can check ``full()`` before inserting to avoid blocking.
    """

    __slots__ = ('_q', '_put_timeout')

    def __init__(self, maxsize: int = _QUEUE_MAXSIZE) -> None:
        self._q: asyncio.Queue[tuple[str, str, Any]] = asyncio.Queue(maxsize=maxsize)
        self._put_timeout = _QUEUE_PUT_TIMEOUT

    def full(self) -> bool:
        return self._q.full()

    def qsize(self) -> int:
        return self._q.qsize()

    async def put(self, item: tuple[str, str, Any]) -> bool:
        """Put item on queue with backpressure. Returns True on success, False on timeout."""
        try:
            await asyncio.wait_for(self._q.put(item), timeout=self._put_timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def get(self) -> tuple[str, str, Any]:
        return await self._q.get()

    def get_nowait(self) -> tuple[str, str, Any]:
        return self._q.get_nowait()

    def task_done(self) -> None:
        self._q.task_done()

    async def join(self) -> None:
        await self._q.join()


class PrefetchCache:
    __slots__ = tuple(('_background_tasks', '_running', '_write_queue', '_writer_task', 'db_path', 'env', 'max_entries'))

    def __init__(self, db_path: str | None=None, max_size_mb: int=100, max_entries: int=10000):
        from hledac.universal.paths import SPRINT_LMDB_ROOT, open_lmdb
        if db_path is None:
            self.db_path = SPRINT_LMDB_ROOT / 'prefetch.lmdb'
        else:
            self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.env = open_lmdb(self.db_path, map_size=max_size_mb * 1024 * 1024)
        self.max_entries = max_entries
        self._write_queue = _BoundedWriteQueue(maxsize=_QUEUE_MAXSIZE)
        self._writer_task: asyncio.Task | None = None
        self._running = True
        self._background_tasks: set[asyncio.Task] = set()

    def _track_task(self, coro) -> asyncio.Task:
        """F196B: Track background tasks for proper cleanup."""
        task = safe_create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def start(self):
        self._writer_task = self._track_task(self._writer_loop())

    async def stop(self):
        """Bezpečně ukončí writer a zpracuje zbytek fronty."""
        self._running = False
        await self._write_queue.put(('__stop__', '', None))
        await self._write_queue.join()
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await safe_gather_fire_and_forget(*self._background_tasks, label='prefetch_cache:55')
            self._background_tasks.clear()

    def close(self):
        """F196B: Close LMDB environment."""
        if hasattr(self, 'env') and self.env:
            self.env.close()
            self.env = None

    async def put(self, url: str, data: dict[str, Any], ttl: int=3600) -> bool:
        """Zařadí zápis do fronty s backpressure.

        Returns:
            True if queued within timeout, False if queue is saturated (backpressure).
            Raises RuntimeError if cache is shutting down.
        """
        if not self._running:
            raise RuntimeError('Cache is shutting down, cannot put new data')
        entry = {'data': data, 'expires': time.time() + ttl, 'access_count': 0}
        queued = await self._write_queue.put(('put', url, entry))
        if not queued:
            logger.warning('PrefetchCache write queue full, dropping put for %s', url)
        return queued

    async def get(self, url: str) -> dict | None:
        """Čtení – synchronní (LMDB je thread‑safe pro čtení)."""
        with self.env.begin() as txn:
            raw = txn.get(url.encode())
        if raw is None:
            return None
        entry = orjson.loads(raw)
        if entry['expires'] < time.time():
            if self._running:
                await self._write_queue.put(('delete', url, None))
            return None
        entry['access_count'] += 1
        if self._running:
            # Access counter update — best-effort; don't fail the read path
            await self._write_queue.put(('update', url, entry))
        return entry['data']

    async def _writer_loop(self):
        """Background writer – sekvenční zpracování požadavků."""
        while True:
            try:
                op, url, entry = await self._write_queue.get()
                if op == '__stop__':
                    self._write_queue.task_done()
                    break
                with self.env.begin(write=True) as txn:
                    if op == 'put' or op == 'update':
                        txn.put(url.encode(), orjson.dumps(entry))
                    elif op == 'delete':
                        txn.delete(url.encode())
                self._write_queue.task_done()
            except Exception as e:
                logger.error(f'Cache writer error: {e}')
                self._write_queue.task_done()
        while True:
            try:
                op, url, entry = self._write_queue.get_nowait()
                with self.env.begin(write=True) as txn:
                    if op in ('put', 'update'):
                        txn.put(url.encode(), orjson.dumps(entry))
                    elif op == 'delete':
                        txn.delete(url.encode())
                self._write_queue.task_done()
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.error(f'Final drain error: {e}')
                self._write_queue.task_done()