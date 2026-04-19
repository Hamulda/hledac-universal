"""
Memory Module
=============

Persistent memory storage for entities, queries, and files.
Provides session-based storage using LMDB.

Features:
- Session-based isolation
- Async LMDB operations
- orjson serialization
- Bounded storage per session
- Automatic cleanup of old sessions

API:
- MemoryManager: Main class for memory storage
- get_memory_manager(): Get singleton instance
- memory_put(session_id, key, value): Store value
- memory_get(session_id, key): Retrieve value
- memory_get_history(session_id, limit): Get session history

Example:
    from hledac.universal.memory import get_memory_manager

    mgr = await get_memory_manager()
    await mgr.put("session123", "key1", {"data": "value"})
    result = await mgr.get("session123", "key1")
"""

from __future__ import annotations

from .memory_manager import (
    MemoryManager,
    get_memory_manager,
    close_memory_manager,
    memory_put,
    memory_get,
    memory_delete,
    memory_get_history,
)

__all__ = [
    "MemoryManager",
    "get_memory_manager",
    "close_memory_manager",
    "memory_put",
    "memory_get",
    "memory_delete",
    "memory_get_history",
]
