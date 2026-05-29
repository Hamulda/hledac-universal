"""
Sprint F214Q: sprint_seeds.lmdb — cross-sprint quantum pathfinder seed persistence.

Canonical path: LMDB_ROOT / "sprint_seeds.lmdb"
Key pattern: b"seeds:{sprint_id}"
Value: orjson.dumps(list[str]) — list of IOC values discovered via quantum path walk.
Max map size: 256MB (256 * 1024 * 1024).

Rationale:
  After each sprint, quantum_path_analysis() discovers undiscovered connected
  IOCs via DuckPGQGraph.find_connected(). These seeds are persisted to LMDB so
  the next sprint can use them as path-informed discovery seeds, merging with
  the degree-centrality seeds from get_top_nodes_by_degree().
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import orjson
from hledac.universal.paths import LMDB_ROOT

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LMDB_PATH: Path = LMDB_ROOT / "sprint_seeds.lmdb"
_LMDB_MAP_SIZE: int = 256 * 1024 * 1024  # 256 MB
_KEY_PREFIX: bytes = b"seeds:"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_key(sprint_id: str) -> bytes:
    """Encode sprint_id as LMDB key."""
    return f"{_KEY_PREFIX.decode()}{sprint_id}".encode()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def async_save_sprint_seeds(sprint_id: str, seeds: list[str]) -> None:
    """
    Persist quantum pathfinder seeds for a completed sprint.

    Args:
        sprint_id: Unique sprint identifier.
        seeds: List of IOC value strings discovered via find_connected() walk.
    """
    if not sprint_id:
        return
    if not seeds:
        return
    try:
        from .tools.lmdb_kv import AsyncLMDBKVStore

        store = AsyncLMDBKVStore(
            path=_LMDB_PATH,
            map_size=_LMDB_MAP_SIZE,
        )
        key = _make_key(sprint_id)
        val = orjson.dumps(seeds)
        await store.put(key.decode(), val)
    except Exception as e:
        import logger

        logger.W(f"[SEEDS] async_save_sprint_seeds failed for {sprint_id}: {e}")


async def async_load_sprint_seeds(sprint_id: str) -> list[str]:
    """
    Load quantum pathfinder seeds for a given sprint.

    Args:
        sprint_id: Unique sprint identifier.

    Returns:
        List of IOC value strings, or empty list if not found.
    """
    if not sprint_id:
        return []
    try:
        from .tools.lmdb_kv import AsyncLMDBKVStore

        store = AsyncLMDBKVStore(
            path=_LMDB_PATH,
            map_size=_LMDB_MAP_SIZE,
        )
        key = _make_key(sprint_id)
        raw = await store.get(key.decode())
        if raw is None:
            return []
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return orjson.loads(raw)
    except Exception as e:
        import logger

        logger.W(f"[SEEDS] async_load_sprint_seeds failed for {sprint_id}: {e}")
        return []


def sync_save_sprint_seeds(sprint_id: str, seeds: list[str]) -> None:
    """
    Synchronous persistence for sprint seeds (used during export phase).
    Writes directly via lmdb.open() to avoid async context requirements.

    Args:
        sprint_id: Unique sprint identifier.
        seeds: List of IOC value strings.
    """
    if not sprint_id or not seeds:
        return
    try:
        import lmdb

        _LMDB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with lmdb.open(str(_LMDB_PATH), map_size=_LMDB_MAP_SIZE, readahead=False) as env:
            key = _make_key(sprint_id)
            val = orjson.dumps(seeds)
            with env.begin(write=True) as txn:
                txn.put(key, val)
    except Exception as e:
        import logger

        logger.W(f"[SEEDS] sync_save_sprint_seeds failed for {sprint_id}: {e}")


def sync_load_sprint_seeds(sprint_id: str) -> list[str]:
    """
    Synchronous load for sprint seeds (used during export phase).

    Args:
        sprint_id: Unique sprint identifier.

    Returns:
        List of IOC value strings, or empty list if not found.
    """
    if not sprint_id:
        return []
    try:
        import lmdb

        if not _LMDB_PATH.exists():
            return []
        with lmdb.open(str(_LMDB_PATH), map_size=_LMDB_MAP_SIZE, readahead=False, readonly=True) as env:
            key = _make_key(sprint_id)
            with env.begin() as txn:
                raw = txn.get(key)
                if raw is None:
                    return []
                return orjson.loads(raw)
    except Exception as e:
        import logger

        logger.W(f"[SEEDS] sync_load_sprint_seeds failed for {sprint_id}: {e}")
        return []
