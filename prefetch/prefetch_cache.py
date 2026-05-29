"""
PrefetchCache – dočasné úložiště pro prefetched data s LRU, TTL a background writerem.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import orjson

logger = logging.getLogger(__name__)


class PrefetchCache:
    def __init__(self, db_path: str | None = None, max_size_mb: int = 100,
                 max_entries: int = 10000):
        from hledac.universal.paths import SPRINT_LMDB_ROOT, open_lmdb
        if db_path is None:
            self.db_path = SPRINT_LMDB_ROOT / "prefetch.lmdb"
        else:
            self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Sprint 3D: use open_lmdb() for env-driven discipline + lock recovery
        self.env = open_lmdb(self.db_path, map_size=max_size_mb * 1024 * 1024)
        self.max_entries = max_entries
        self._write_queue = asyncio.Queue(maxsize=1000)  # C2: bounded to prevent unbounded growth
        self._writer_task: asyncio.Task | None = None
        self._running = True

        # F196B: Track background tasks for proper cleanup
        self._background_tasks: set[asyncio.Task] = set()

    def _track_task(self, coro) -> asyncio.Task:
        """F196B: Track background tasks for proper cleanup."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return task

    async def start(self):
        self._writer_task = self._track_task(self._writer_loop())

    async def stop(self):
        """Bezpečně ukončí writer a zpracuje zbytek fronty."""
        self._running = False
        await self._write_queue.put(("__stop__", "", None))
        await self._write_queue.join()

        # F196B: Cancel all tracked background tasks
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

    def close(self):
        """F196B: Close LMDB environment."""
        if hasattr(self, 'env') and self.env:
            self.env.close()
            self.env = None

    async def put(self, url: str, data: dict[str, Any], ttl: int = 3600):
        """Zařadí zápis do fronty (neblokující)."""
        if not self._running:
            raise RuntimeError("Cache is shutting down, cannot put new data")
        entry = {
            'data': data,
            'expires': time.time() + ttl,
            'access_count': 0
        }
        await self._write_queue.put(('put', url, entry))

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
            await self._write_queue.put(('update', url, entry))
        return entry['data']

    async def _writer_loop(self):
        """Background writer – sekvenční zpracování požadavků."""
        while True:
            try:
                op, url, entry = await self._write_queue.get()
                if op == "__stop__":
                    self._write_queue.task_done()
                    break
                with self.env.begin(write=True) as txn:
                    if op == 'put' or op == 'update':
                        txn.put(url.encode(), orjson.dumps(entry))
                    elif op == 'delete':
                        txn.delete(url.encode())
                self._write_queue.task_done()
            except Exception as e:
                logger.error(f"Cache writer error: {e}")
                self._write_queue.task_done()

        # Zpracujeme zbytek fronty (drain) – už žádné nové položky nepřibývají
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
                logger.error(f"Final drain error: {e}")
                self._write_queue.task_done()
