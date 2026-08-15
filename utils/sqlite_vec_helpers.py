"""
sqlite-vec helpers for RAG on M1 8GB.

Design:

- Zero-process: unlike LanceDB, sqlite-vec runs in-process via SQLite extension.
- Shared db: uses sprint_{id}.db (same as DuckDB shadow store).
- ANN via SQLite virtual table: CREATE VIRTUAL TABLE ... USING vec0().
"""
import sqlite3
from pathlib import Path
from typing import Any
import psutil
from _core import aclose
_vec0_available: bool | None = None

def _check_vec0_available() -> bool:
    global _vec0_available
    if _vec0_available is not None:
        return _vec0_available
    try:
        import sqlite_vec
        _vec0_available = True
    except ImportError:
        _vec0_available = False
    return _vec0_available

def get_sprint_db_path(sprint_id: str='default') -> Path:
    """Return path to sprint_{id}.db (shared with DuckDBShadowStore).

    Uses SPRINT_STORE_ROOT from paths.py — same location as DuckDB store.
    When RAMDISK_ACTIVE, this is in RAM for optimal M1 performance.
    """
    from hledac.universal.paths import SPRINT_STORE_ROOT
    sprint_dir = SPRINT_STORE_ROOT / sprint_id
    sprint_dir.mkdir(parents=True, exist_ok=True)
    return sprint_dir / f'{sprint_id}.db'

class SqliteVecStore:
    """
    M1-native ANN store using sqlite-vec.

    Unlike LanceDB (subprocess, ~200MB overhead), sqlite-vec runs
    in-process via SQLite extension. Memory footprint ~5MB vs ~200MB.

    Usage:
        store = SqliteVecStore(sprint_id="abc123")
        await store.initialize()
        await store.upsert("item1", [0.1, 0.2, ...], {"text": "source content"})
        results = await store.search([0.1, 0.2, ...], top_k=10)
    """
    MAX_DIM = 384
    TABLE_NAME = 'vec_items'
    _DEFAULT_SPRINT_ID = 'default'
    __slots__ = tuple(('_conn', '_dim', '_path', '_sprint_id'))

    def __init__(self, sprint_id: str=_DEFAULT_SPRINT_ID, dim: int=MAX_DIM) -> None:
        self._sprint_id = sprint_id
        self._dim = dim
        self._conn: sqlite3.Connection | None = None
        self._path = get_sprint_db_path(sprint_id)

    @property
    def is_initialized(self) -> bool:
        return self._conn is not None

    def _check_vec0_available(self) -> bool:
        return _check_vec0_available()

    async def initialize(self) -> bool:
        """
        Initialize sqlite-vec store. Creates tables if not exist.

        Returns True if vec0 is available, False otherwise.
        """
        if not self._check_vec0_available():
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.execute('PRAGMA synchronous=NORMAL')
        try:
            self._conn.execute(f'\n                CREATE VIRTUAL TABLE IF NOT EXISTS {self.TABLE_NAME} USING vec0(\n                    embedding float[{self._dim}],\n                    item_id TEXT,\n                    metadata TEXT\n                )\n                ')
            self._conn.execute(f'\n                CREATE TABLE IF NOT EXISTS {self.TABLE_NAME}_meta (\n                    item_id TEXT PRIMARY KEY,\n                    text TEXT,\n                    created_at REAL\n                )\n                ')
            self._conn.commit()
            return True
        except Exception:
            self._conn.close()
            self._conn = None
            return False

    async def upsert(self, item_id: str, embedding: list[float], metadata: dict[str, Any]) -> bool:
        """Insert or replace an embedding."""
        if self._conn is None:
            return False
        import json
        try:
            vec_bytes = self._pack_f32(embedding)
            text = metadata.get('text', '')
            now = _now()
            meta_json = json.dumps(metadata)
            self._conn.execute(f'DELETE FROM {self.TABLE_NAME} WHERE item_id = ?', (item_id,))
            self._conn.execute(f'DELETE FROM {self.TABLE_NAME}_meta WHERE item_id = ?', (item_id,))
            self._conn.execute(f'INSERT INTO {self.TABLE_NAME} (embedding, item_id, metadata) VALUES (?, ?, ?)', (vec_bytes, item_id, meta_json))
            self._conn.execute(f'INSERT INTO {self.TABLE_NAME}_meta (item_id, text, created_at) VALUES (?, ?, ?)', (item_id, text, now))
            self._conn.commit()
            return True
        except Exception:
            return False

    async def search(self, query_embedding: list[float], top_k: int=10, threshold: float=0.0) -> list[dict[str, Any]]:
        """
        ANN search via sqlite-vec.

        Returns list of {item_id, distance, metadata} dicts.
        """
        if self._conn is None:
            return []
        if not self._check_vec0_available():
            return []
        import json
        try:
            vec_bytes = self._pack_f32(query_embedding)
            rows = self._conn.execute(f'\n                SELECT item_id, distance, metadata\n                FROM {self.TABLE_NAME}\n                WHERE embedding MATCH ?\n                ORDER BY distance\n                LIMIT ?\n                ', (vec_bytes, top_k)).fetchall()
            results = []
            for item_id, distance, meta_json in rows:
                if distance <= threshold:
                    continue
                try:
                    meta = json.loads(meta_json) if meta_json else {}
                except Exception:
                    meta = {}
                results.append({'item_id': item_id, 'distance': distance, 'metadata': meta})
            return results
        except Exception:
            return []

    async def close(self) -> None:
        """Close connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    async def upsert_entity(self, entity_id: str, embedding: list[float], aliases: list[str], extra_metadata: dict[str, Any] | None=None) -> bool:
        """
        Insert or replace an entity with its embedding and aliases.

        Entity data lives in the same vec_items table (shared ANN index)
        plus an entity_meta table for alias text search.
        """
        if self._conn is None:
            return False
        import json
        try:
            vec_bytes = self._pack_f32(embedding)
            now = _now()
            meta = {'type': 'entity', 'aliases': aliases, **(extra_metadata or {})}
            meta_json = json.dumps(meta)
            aliases_text = '\n'.join(aliases)
            self._conn.execute(f'DELETE FROM {self.TABLE_NAME} WHERE item_id = ?', (entity_id,))
            self._conn.execute(f'INSERT INTO {self.TABLE_NAME} (embedding, item_id, metadata) VALUES (?, ?, ?)', (vec_bytes, entity_id, meta_json))
            self._conn.execute(f'\n                INSERT INTO {self.TABLE_NAME}_meta (item_id, text, created_at)\n                VALUES (?, ?, ?)\n                ON CONFLICT(item_id) DO UPDATE SET\n                    text = excluded.text,\n                    created_at = excluded.created_at\n                ', (entity_id, aliases_text, now))
            self._conn.commit()
            return True
        except Exception:
            return False

    async def search_entities(self, query_embedding: list[float], top_k: int=10, threshold: float=0.0, entity_type: str | None=None) -> list[dict[str, Any]]:
        """
        ANN search restricted to entity items.

        Filters by metadata.type == "entity" (or entity_type if specified).
        """
        if self._conn is None:
            return []
        if not self._check_vec0_available():
            return []
        import json
        try:
            vec_bytes = self._pack_f32(query_embedding)
            rows = self._conn.execute(f'\n                SELECT item_id, distance, metadata\n                FROM {self.TABLE_NAME}\n                WHERE embedding MATCH ?\n                ORDER BY distance\n                LIMIT ?\n                ', (vec_bytes, top_k * 3)).fetchall()
            results = []
            for item_id, distance, meta_json in rows:
                if distance <= threshold:
                    continue
                try:
                    meta = json.loads(meta_json) if meta_json else {}
                except Exception:
                    meta = {}
                item_type = meta.get('type', '')
                if entity_type and item_type != entity_type:
                    continue
                if item_type != 'entity' and (not entity_type):
                    continue
                results.append({'id': item_id, 'item_id': item_id, 'distance': distance, 'similarity': 1.0 - distance, 'metadata': meta})
                if len(results) >= top_k:
                    break
            return results
        except Exception:
            return []

    async def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Retrieve entity metadata by ID."""
        if self._conn is None:
            return None
        import json
        try:
            row = self._conn.execute(f'SELECT item_id, metadata FROM {self.TABLE_NAME} WHERE item_id = ?', (entity_id,)).fetchone()
            if not row:
                return None
            item_id, meta_json = row
            meta = json.loads(meta_json) if meta_json else {}
            aliases = meta.get('aliases', [])
            text_row = self._conn.execute(f'SELECT text FROM {self.TABLE_NAME}_meta WHERE item_id = ?', (entity_id,)).fetchone()
            text = text_row[0] if text_row else ''
            return {'id': item_id, 'item_id': item_id, 'aliases': aliases, 'text': text, 'metadata': meta}
        except Exception:
            return None

    @staticmethod
    def _pack_f32(vec: list[float]) -> bytes:
        """Pack float32 list into raw bytes (little-endian)."""
        import struct
        return struct.pack(f'<{len(vec)}f', *vec)

    def _unpack_f32(self, data: bytes) -> list[float]:
        """Unpack bytes to float32 list."""
        import struct
        count = len(data) // 4
        return list(struct.unpack(f'<{count}f', data))

    @staticmethod
    def _has_memory_headroom(required_gb: float=0.5) -> bool:
        """Check if system has required free memory headroom."""
        try:
            available = psutil.virtual_memory().available / 1024 ** 3
            return available >= required_gb
        except Exception:
            return False

def _now() -> float:
    """Return current time as Unix timestamp."""
    import time
    return time.time()