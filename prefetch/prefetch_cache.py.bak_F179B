"""
PrefetchCache – dočasné úložiště pro prefetched data s LRU, TTL a background writerem.
"""

import asyncio
import orjson
import time
from pathlib import Path
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class PrefetchCache:
    def __init__(self, db_path: Optional[str] = None, max_size_mb: int = 100,
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
        self._write_queue = asyncio.Queue()
        self._writer_task: Optional[asyncio.Task] = None
        self._running = True

    async def start(self):
        self._writer_task = asyncio.create_task(self._writer_loop())

    async def stop(self):
        """Bezpečně ukončí writer a zpracuje zbytek fronty."""
        self._running = False
        await self._write_queue.put(("__stop__", "", None))
        await self._write_queue.join()
        if self._writer_task:
            await self._writer_task

    async def put(self, url: str, data: Dict[str, Any], ttl: int = 3600):
        """Zařadí zápis do fronty (neblokující)."""
        if not self._running:
            raise RuntimeError("Cache is shutting down, cannot put new data")
        entry = {
            'data': data,
            'expires': time.time() + ttl,
            'access_count': 0
        }
        await self._write_queue.put(('put', url, entry))

    async def get(self, url: str) -> Optional[Dict]:
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
