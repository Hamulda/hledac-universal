"""
LMDB cache pro rozklady úkolů.
Ukládá výsledky SLM decomposeru s verzí modelu.
"""

import orjson
import asyncio
from typing import Optional, Any
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class TaskCache:
    def __init__(self, db_path: Optional[str] = None, max_size_mb: int = 100):
        from hledac.universal.paths import SPRINT_LMDB_ROOT, open_lmdb
        if db_path is None:
            self.db_path = SPRINT_LMDB_ROOT / "task_cache.lmdb"
        else:
            self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Sprint 3D: use open_lmdb() for env-driven discipline + lock recovery
        self.env = open_lmdb(self.db_path, map_size=max_size_mb * 1024 * 1024)
        self._lock = asyncio.Lock()

    async def get(self, key: str, model_version: int) -> Optional[Any]:
        """Načte z cache, pokud model_version odpovídá."""
        async with self._lock:
            def _get():
                with self.env.begin() as txn:
                    data = txn.get(key.encode())
                    if data is None:
                        return None
                    entry = orjson.loads(data)
                    if entry.get('version') != model_version:
                        return None
                    return entry['value']

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _get)

    async def put(self, key: str, value: Any, model_version: int):
        """Uloží do cache s aktuální verzí."""
        entry = {'version': model_version, 'value': value}
        data = orjson.dumps(entry)

        async with self._lock:
            def _put():
                with self.env.begin(write=True) as txn:
                    txn.put(key.encode(), data)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _put)

    async def close(self):
        self.env.close()
