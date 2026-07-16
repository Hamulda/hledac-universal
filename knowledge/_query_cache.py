"""
_query_cache — Two-tier bounded query cache for DuckDB.

Moved from duckdb_store.py to break circular import with sprint_boundary.py.

DEPENDENCIES (resolved lazily inside methods to avoid circular imports):
    - orjson / json, lmdb, _DUCKDB_QUERY_CACHE_ENABLED

CAN IMPORT:
    - pathlib.Path
    - standard library only
"""

from __future__ import annotations

from pathlib import Path


class _DuckDBQueryCache:
    """
    Two-tier bounded query cache for DuckDB read queries.

    L1 — in-memory LRU (500 entries, TTL 300s): sub-millisecond hit.
    L2 — LMDB (5000 entries, TTL 300s, 16 MB map): persistent across
         process restarts but still bounded by TTL eviction.

    Cache invalidation on schema migration:
        _invalidate_on_migration() is called by DuckDBShadowStore._apply_schema_migrations()
        so that cached results from old schemas are never served after ALTER.

    Opt-in via HLEDAC_DUCKDB_QUERY_CACHE=1 (default OFF).
    Always-on, bounded, fail-safe invariants:
        - Any error on hit path returns None (cache miss, no exception)
        - Any error on write path is silently swallowed
        - LMDB write failures do not propagate
        - :memory: DuckDB mode bypasses L2 (no persistence path available)
        - TTL-based LRU eviction keeps memory bounded
    """

    __slots__ = ("_l1", "_l2_env", "_l2_path", "_max_l1", "_max_l2", "_ttl_s", "_enabled")

    def __init__(self, lmdb_path: Path, *, max_l1: int = 500, max_l2: int = 5000, ttl_s: int = 300) -> None:
        import os
        from collections import OrderedDict
        from core.env_config import ENV

        _DUCKDB_QUERY_CACHE_ENABLED = ENV.get_bool("HLEDAC_DUCKDB_QUERY_CACHE")

        object.__setattr__(self, "_l1", OrderedDict())
        object.__setattr__(self, "_max_l1", max_l1)
        object.__setattr__(self, "_max_l2", max_l2)
        object.__setattr__(self, "_ttl_s", ttl_s)
        object.__setattr__(self, "_enabled", _DUCKDB_QUERY_CACHE_ENABLED)
        object.__setattr__(self, "_l2_path", lmdb_path)
        if not _DUCKDB_QUERY_CACHE_ENABLED:
            object.__setattr__(self, "_l2_env", None)
            return
        try:
            import lmdb

            lmdb_path.parent.mkdir(parents=True, exist_ok=True)
            env = lmdb.open(str(lmdb_path), map_size=16 * 1024 * 1024, writemap=False, readahead=False, meminit=False)
            object.__setattr__(self, "_l2_env", env)
        except Exception:  # noqa: BLE001 — best-effort; export failure; non-critical
            object.__setattr__(self, "_l2_env", None)

    @staticmethod
    def _key(sql: str, params: tuple) -> str:
        """Stable cache key: sha256(sql + "|" + json(params))."""
        import hashlib
        import json as _stdjson

        try:
            import orjson as _orjson_mod

            opts = getattr(_orjson_mod, "OPT_SORT_KEYS", 0)
            params_json = _orjson_mod.dumps(params, option=opts)
        except Exception:
            params_json = _stdjson.dumps(params, sort_keys=True)
        data = sql + "|" + params_json.decode()
        return hashlib.sha256(data.encode()).hexdigest()[:32]

    def _l1_get(self, key: str) -> list | None:
        import time

        l1: dict = object.__getattribute__(self, "_l1")
        if key not in l1:
            return None
        entry = l1[key]
        if time.monotonic() - entry["ts"] > object.__getattribute__(self, "_ttl_s"):
            l1.pop(key, None)
            return None
        l1.move_to_end(key)
        return entry["rows"]

    def _l1_set(self, key: str, rows: list) -> None:
        import time

        l1: dict = object.__getattribute__(self, "_l1")
        max_l1: int = object.__getattribute__(self, "_max_l1")
        l1[key] = {"rows": rows, "ts": time.monotonic()}
        while len(l1) > max_l1:
            l1.popitem(last=False)

    def _l2_get(self, key: str) -> list | None:
        import time

        env = object.__getattribute__(self, "_l2_env")
        if env is None:
            return None
        try:
            import orjson as _orjson_mod

            with env.begin(write=False) as txn:
                raw = txn.get(key.encode())
            if raw is None:
                return None

            entry = _orjson_mod.loads(raw)
            if time.monotonic() - entry["ts"] > object.__getattribute__(self, "_ttl_s"):
                return None
            return entry["rows"]
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            return None

    def _l2_set(self, key: str, rows: list) -> None:
        import time

        env = object.__getattribute__(self, "_l2_env")
        if env is None:
            return
        try:
            import orjson as _orjson_mod

            entry_bytes = _orjson_mod.dumps({"rows": rows, "ts": time.monotonic()})
            with env.begin(write=True) as txn:
                cursor = txn.cursor()
                if txn.stat()["entries"] >= object.__getattribute__(self, "_max_l2"):
                    for k, _ in cursor.iternext():
                        txn.delete(k)
                        break
                txn.put(key.encode(), entry_bytes)
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            pass

    def get(self, sql: str, params: tuple) -> list | None:
        from core.env_config import ENV

        if not ENV.get_bool("HLEDAC_DUCKDB_QUERY_CACHE"):
            return None
        key = self._key(sql, params)
        rows = self._l1_get(key)
        if rows is not None:
            return rows
        rows = self._l2_get(key)
        if rows is not None:
            self._l1_set(key, rows)
        return rows

    def put(self, sql: str, params: tuple, rows: list) -> None:
        from core.env_config import ENV

        if not ENV.get_bool("HLEDAC_DUCKDB_QUERY_CACHE"):
            return
        key = self._key(sql, params)
        self._l1_set(key, rows)
        self._l2_set(key, rows)

    def invalidate(self) -> None:
        """Clear L1 and L2. Called after schema migration."""
        l1: dict = object.__getattribute__(self, "_l1")
        l1.clear()
        env = object.__getattribute__(self, "_l2_env")
        if env is not None:
            try:
                with env.begin(write=True) as txn:
                    txn.drop(txn.database(), delete=False)
            except Exception:  # noqa: BLE001 — best-effort; export failure; non-critical
                pass

    def close(self) -> None:
        env = object.__getattribute__(self, "_l2_env")
        if env is not None:
            try:
                env.close()
            except Exception:  # noqa: BLE001 — best-effort; cleanup failure; non-critical
                pass
            object.__setattr__(self, "_l2_env", None)


__all__ = ["_DuckDBQueryCache"]
