"""
Atomic JSON Storage - RAM-efficient knowledge graph for M1 Macs.

Replaces vector databases (ChromaDB, FAISS) with JSON-based storage
to minimize memory footprint on resource-constrained systems.

Features sharding for improved scalability and memory efficiency.

.. deprecated::
    knowledge.atomic_storage is DEPRECATED. Use knowledge.duckdb_store instead.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "knowledge.atomic_storage is DEPRECATED. Use knowledge.duckdb_store instead.",
    DeprecationWarning, stacklevel=2)

import asyncio
import gc
import hashlib
import json
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from heapq import heappush, heappushpop
from pathlib import Path
from typing import Any

import orjson  # faster than pickle for JSON-serializable data

# Optional delta compressor for snapshot storage
try:
    from ..tools.delta_compressor import DeltaCompressor
    DELTA_AVAILABLE = True
except ImportError:
    DELTA_AVAILABLE = False
    DeltaCompressor = None

# Hypothesis module for BetaBinomial
try:
    from ..hypothesis import BetaBinomial
except ImportError:
    BetaBinomial = None
    DELTA_AVAILABLE = False
    DeltaCompressor = None

# Optional LMDB for persistent storage
try:
    import lmdb
    LMDB_AVAILABLE = True
except ImportError:
    lmdb = None
    LMDB_AVAILABLE = False

# Sprint 79b: ZSTD compression for snapshots
try:
    import zstandard as zstd
    ZSTD_AVAILABLE = True
except ImportError:
    ZSTD_AVAILABLE = False
    zstd = None

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeEntry:
    """A single knowledge entry in the graph."""

    content: str
    source: str
    entry_type: str = "text"
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    id: str | None = None

    def __post_init__(self):
        """Generate ID if not provided."""
        if self.id is None:
            content_hash = hashlib.md5(
                f"{self.content}:{self.source}".encode()
            ).hexdigest()[:12]
            self.id = f"ke_{content_hash}"

    def to_dict(self) -> dict[str, Any]:
        """Convert entry to dictionary."""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeEntry:
        """Create entry from dictionary."""
        if isinstance(data.get('timestamp'), str):
            data['timestamp'] = datetime.fromisoformat(data['timestamp'])
        return cls(**data)


class ShardCache:
    """LRU cache for shard files with configurable max size."""

    def __init__(self, max_shards: int = 4):
        self.max_shards = max_shards
        self._cache: Ordereddict[str, dict[str, KnowledgeEntry]] = OrderedDict()
        self._access_count = 0
        self._hit_count = 0

    def get(self, shard_id: str) -> dict[str, KnowledgeEntry] | None:
        """Get shard from cache, updating LRU order."""
        self._access_count += 1
        if shard_id in self._cache:
            # Move to end (most recently used)
            self._cache.move_to_end(shard_id)
            self._hit_count += 1
            return self._cache[shard_id]
        return None

    def put(self, shard_id: str, entries: dict[str, KnowledgeEntry]) -> None:
        """Add shard to cache, evicting oldest if necessary."""
        if shard_id in self._cache:
            # Update existing and move to end
            self._cache[shard_id] = entries
            self._cache.move_to_end(shard_id)
        else:
            # Evict oldest if at capacity
            if len(self._cache) >= self.max_shards:
                oldest = next(iter(self._cache))
                del self._cache[oldest]
                logger.debug(f"Evicted shard {oldest} from cache")
            self._cache[shard_id] = entries

    def clear(self) -> None:
        """Clear all cached shards."""
        self._cache.clear()

    def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        hit_rate = self._hit_count / max(1, self._access_count)
        return {
            "max_shards": self.max_shards,
            "cached_shards": len(self._cache),
            "access_count": self._access_count,
            "hit_count": self._hit_count,
            "hit_rate": round(hit_rate, 3),
            "cached_shard_ids": list(self._cache.keys())
        }


class AtomicJSONKnowledgeGraph:
    """
    RAM-efficient JSON-based knowledge graph storage with sharding.

    Features:
    - Sharded storage (entries/ directory with 2-char shard prefixes)
    - LRU cache for shards (configurable max_shards)
    - Atomic file operations (write to temp, then rename)
    - Incremental updates
    - Query by metadata filters
    - Memory-efficient streaming for large datasets
    - Aggressive memory mode for constrained systems
    """

    def __init__(
        self,
        storage_dir: str = "storage/knowledge_graph",
        max_shards: int = 4,
        aggressive_memory_mode: bool = False
    ):
        """
        Initialize the knowledge graph storage.

        Args:
            storage_dir: Directory for storage files
            max_shards: Maximum number of shards to keep in LRU cache
            aggressive_memory_mode: If True, clear cache after each operation
        """
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # Sharded storage directory
        self.entries_dir = self.storage_dir / "entries"
        self.entries_dir.mkdir(exist_ok=True)

        # Legacy file (for migration)
        self.legacy_entries_file = self.storage_dir / "entries.json"
        self.migrated_marker = self.storage_dir / "entries.json.migrated"

        # Index file (for quick lookups)
        self.index_file = self.storage_dir / "index.json"

        # Shard cache (LRU)
        self._shard_cache = ShardCache(max_shards=max_shards)
        self.max_shards = max_shards

        # Aggressive memory mode
        self.aggressive_memory_mode = aggressive_memory_mode

        # In-memory index (lazy-loaded)
        self._index: dict[str, Any] | None = None

        # Statistics
        self.stats = {
            "writes": 0,
            "reads": 0,
            "queries": 0,
            "shards_accessed": 0
        }

        # Total entries counter (initialized with fail-safe scan)
        self._total_entries: int = 0
        self._init_total_entries()

        # LMDB backend for persistent storage
        self._env = None
        if LMDB_AVAILABLE:
            try:
                self._env = lmdb.open(str(self.storage_dir / 'data.lmdb'), map_size=10 * 1024 * 1024 * 1024)  # 10GB
                self._migrate_from_json()
                logger.info("LMDB backend initialized")
            except Exception as e:
                logger.warning(f"LMDB init failed, falling back to JSON: {e}")
                self._env = None

        # Run migration if needed
        self._maybe_migrate()

    def _init_total_entries(self) -> None:
        """Initialize total entries counter with a fail-safe scan."""
        try:
            total = 0
            for shard_file in self.entries_dir.glob("*.json"):
                try:
                    with open(shard_file, encoding='utf-8') as f:
                        data = json.load(f)
                    total += len(data)
                except (OSError, json.JSONDecodeError):
                    # Skip corrupted shards
                    pass
            self._total_entries = total
        except Exception:
            # Fail-safe: start at 0 if scan fails
            self._total_entries = 0

    def _get_shard_id(self, entry_id: str) -> str:
        """Get shard ID from entry ID (first 2 characters)."""
        # Extract first 2 alphanumeric characters from ID
        clean_id = ''.join(c for c in entry_id if c.isalnum())
        return clean_id[:2].lower() if len(clean_id) >= 2 else "00"

    def _get_shard_path(self, shard_id: str) -> Path:
        """Get path to shard file."""
        return self.entries_dir / f"{shard_id}.json"

    def _migrate_from_json(self) -> None:
        """Migrate existing JSON shards into LMDB."""
        if self._env is None:
            return
        try:
            # Check if migration already done
            with self._env.begin() as txn:
                if txn.get(b'__migrated__'):
                    return

            # Read JSON shards and write to LMDB
            for shard_file in self.entries_dir.glob("*.json"):
                try:
                    with open(shard_file, encoding='utf-8') as f:
                        data = json.load(f)
                    with self._env.begin(write=True) as txn:
                        for entry_id, entry_data in data.items():
                            entry = KnowledgeEntry(**entry_data)
                            txn.put(entry_id.encode(), orjson.dumps(entry.to_dict()))
                except (OSError, json.JSONDecodeError):
                    pass

            # Mark migration complete
            with self._env.begin(write=True) as txn:
                txn.put(b'__migrated__', b'1')
            logger.info("Migration from JSON to LMDB completed")
        except Exception as e:
            logger.warning(f"Migration to LMDB failed: {e}")

    def _maybe_migrate(self) -> None:
        """Migrate legacy entries.json to sharded format if needed."""
        if not self.legacy_entries_file.exists():
            return

        if self.migrated_marker.exists():
            # Already migrated, clean up legacy file if still exists
            logger.info("Migration already completed, cleaning up legacy file")
            try:
                self.legacy_entries_file.unlink()
            except OSError:
                pass
            return

        logger.info("Migrating legacy entries.json to sharded format...")

        try:
            with open(self.legacy_entries_file, encoding='utf-8') as f:
                data = json.load(f)

            # Convert and shard entries
            shard_entries: dict[str, dict[str, Any]] = {}
            for entry_id, entry_data in data.items():
                shard_id = self._get_shard_id(entry_id)
                if shard_id not in shard_entries:
                    shard_entries[shard_id] = {}
                shard_entries[shard_id][entry_id] = entry_data

            # Write shards
            for shard_id, entries in shard_entries.items():
                shard_path = self._get_shard_path(shard_id)
                temp_file = shard_path.with_suffix('.tmp')
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(entries, f, indent=2, ensure_ascii=False)
                temp_file.replace(shard_path)

            # Rename legacy file to mark migration complete
            self.legacy_entries_file.rename(self.migrated_marker)

            logger.info(f"Migration complete: {len(data)} entries -> {len(shard_entries)} shards")

        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"Migration failed: {e}")
            # Don't delete legacy file on error

    def _load_shard(self, shard_id: str) -> dict[str, KnowledgeEntry]:
        """Load a shard from disk (with caching)."""
        # Check cache first
        cached = self._shard_cache.get(shard_id)
        if cached is not None:
            return cached

        shard_path = self._get_shard_path(shard_id)

        if not shard_path.exists():
            entries = {}
            self._shard_cache.put(shard_id, entries)
            return entries

        try:
            with open(shard_path, encoding='utf-8') as f:
                data = json.load(f)

            entries = {
                k: KnowledgeEntry.from_dict(v)
                for k, v in data.items()
            }
            self.stats["reads"] += 1
            self.stats["shards_accessed"] += 1

            # Add to cache
            self._shard_cache.put(shard_id, entries)

            logger.debug(f"Loaded shard {shard_id} with {len(entries)} entries")

        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Error loading shard {shard_id}: {e}. Starting fresh.")
            entries = {}
            self._shard_cache.put(shard_id, entries)

        return entries

    def _save_shard(self, shard_id: str, entries: dict[str, KnowledgeEntry]) -> None:
        """Save a shard to disk atomically."""
        shard_path = self._get_shard_path(shard_id)

        # Convert to serializable format
        data = {k: v.to_dict() for k, v in entries.items()}

        # Write to temp file first (atomic operation)
        temp_file = shard_path.with_suffix('.tmp')
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            # Atomic rename
            temp_file.replace(shard_path)
            self.stats["writes"] += 1

            # Update cache
            self._shard_cache.put(shard_id, entries)

            logger.debug(f"Saved shard {shard_id} with {len(entries)} entries")

        except OSError as e:
            logger.error(f"Error saving shard {shard_id}: {e}")
            if temp_file.exists():
                temp_file.unlink()
            raise

    def _clear_memory_if_aggressive(self) -> None:
        """Clear cache and run GC if aggressive memory mode is enabled."""
        if self.aggressive_memory_mode:
            self._shard_cache.clear()
            gc.collect()
            logger.debug("Aggressive memory mode: cache cleared")

    def add_entry(self, entry: KnowledgeEntry) -> str:
        """Add a knowledge entry to the graph."""
        # Try LMDB first if available
        if self._env is not None:
            try:
                with self._env.begin(write=True) as txn:
                    txn.put(entry.id.encode(), orjson.dumps(entry.to_dict()))
                self._total_entries += 1
                self._clear_memory_if_aggressive()
                return entry.id
            except Exception as e:
                logger.debug(f"LMDB add failed: {e}")

        # Fallback to JSON
        shard_id = self._get_shard_id(entry.id)
        entries = self._load_shard(shard_id)
        entries[entry.id] = entry
        self._save_shard(shard_id, entries)
        self._total_entries += 1
        self._clear_memory_if_aggressive()
        return entry.id

    def add_entries(self, entries: list[KnowledgeEntry]) -> list[str]:
        """Add multiple entries efficiently."""
        # Group by shard
        shard_groups: dict[str, dict[str, KnowledgeEntry]] = {}
        ids = []

        for entry in entries:
            shard_id = self._get_shard_id(entry.id)
            if shard_id not in shard_groups:
                shard_groups[shard_id] = {}
            shard_groups[shard_id][entry.id] = entry
            ids.append(entry.id)

        # Save each shard
        for shard_id, shard_entries in shard_groups.items():
            current = self._load_shard(shard_id)
            current.update(shard_entries)
            self._save_shard(shard_id, current)

        self._total_entries += len(entries)
        self._clear_memory_if_aggressive()
        return ids

    def get_entry(self, entry_id: str) -> KnowledgeEntry | None:
        """Get a specific entry by ID."""
        # Try LMDB first if available
        if self._env is not None:
            try:
                with self._env.begin() as txn:
                    data = txn.get(entry_id.encode())
                    if data:
                        return KnowledgeEntry.from_dict(orjson.loads(data))
            except Exception as e:
                logger.debug(f"LMDB get failed: {e}")

        # Fallback to JSON
        shard_id = self._get_shard_id(entry_id)
        entries = self._load_shard(shard_id)
        return entries.get(entry_id)

    def query(
        self,
        filter_dict: dict[str, Any] | None = None,
        max_shards_scanned: int | None = None
    ) -> Iterator[KnowledgeEntry]:
        """
        Query entries by metadata filters.

        Args:
            filter_dict: Dictionary of field-value pairs to match
            max_shards_scanned: Maximum number of shards to scan (None = all)

        Returns:
            Iterator of matching KnowledgeEntry objects
        """
        self.stats["queries"] += 1

        # Get list of shard files
        shard_files = sorted(self.entries_dir.glob("*.json"))

        if max_shards_scanned is not None:
            shard_files = shard_files[:max_shards_scanned]

        for shard_file in shard_files:
            shard_id = shard_file.stem
            entries = self._load_shard(shard_id)

            for entry in entries.values():
                if filter_dict is None:
                    yield entry
                    continue

                # Check if entry matches all filters
                match = True
                for key, value in filter_dict.items():
                    # Check metadata first
                    if key in entry.metadata:
                        if entry.metadata[key] != value:
                            match = False
                            break
                    # Check top-level fields
                    elif hasattr(entry, key):
                        if getattr(entry, key) != value:
                            match = False
                            break
                    else:
                        match = False
                        break

                if match:
                    yield entry

        self._clear_memory_if_aggressive()

    def search_recent(self, limit: int = 100) -> list[KnowledgeEntry]:
        """
        Search only the most recent N entries.

        Efficiently scans shards and returns entries sorted by timestamp
        (newest first), limited to N entries.

        Args:
            limit: Maximum number of recent entries to return

        Returns:
            List of KnowledgeEntry objects sorted by timestamp (newest first)
        """
        all_entries: list[KnowledgeEntry] = []

        # Get all shard files
        shard_files = list(self.entries_dir.glob("*.json"))

        for shard_file in shard_files:
            shard_id = shard_file.stem
            entries = self._load_shard(shard_id)
            all_entries.extend(entries.values())

        # Sort by timestamp (newest first) and limit
        all_entries.sort(key=lambda e: e.timestamp, reverse=True)

        self._clear_memory_if_aggressive()
        return all_entries[:limit]

    def search_content(
        self,
        query: str,
        case_sensitive: bool = False,
        max_shards_scanned: int | None = None
    ) -> list[KnowledgeEntry]:
        """
        Search for query string in entry content.

        Args:
            query: Search string
            case_sensitive: Whether search is case sensitive
            max_shards_scanned: Maximum number of shards to scan

        Returns:
            List of matching KnowledgeEntry objects
        """
        results = []

        if not case_sensitive:
            query = query.lower()

        # Get list of shard files
        shard_files = sorted(self.entries_dir.glob("*.json"))

        if max_shards_scanned is not None:
            shard_files = shard_files[:max_shards_scanned]

        for shard_file in shard_files:
            shard_id = shard_file.stem
            entries = self._load_shard(shard_id)

            for entry in entries.values():
                content = entry.content if case_sensitive else entry.content.lower()
                if query in content:
                    results.append(entry)

        self._clear_memory_if_aggressive()
        return results

    def delete_entry(self, entry_id: str) -> bool:
        """Delete an entry by ID."""
        # Try LMDB first if available
        if self._env is not None:
            try:
                with self._env.begin(write=True) as txn:
                    result = txn.delete(entry_id.encode())
                    if result:
                        self._total_entries = max(0, self._total_entries - 1)
                        self._clear_memory_if_aggressive()
                        return True
            except Exception as e:
                logger.debug(f"LMDB delete failed: {e}")

        # Fallback to JSON
        shard_id = self._get_shard_id(entry_id)
        entries = self._load_shard(shard_id)

        if entry_id in entries:
            del entries[entry_id]
            self._save_shard(shard_id, entries)
            self._total_entries = max(0, self._total_entries - 1)
            self._clear_memory_if_aggressive()
            return True

        return False

    def clear(self) -> None:
        """Clear all entries."""
        # Delete all shard files
        for shard_file in self.entries_dir.glob("*.json"):
            shard_file.unlink()

        self._shard_cache.clear()
        self._total_entries = 0
        gc.collect()
        logger.info("Knowledge graph cleared")

    def get_stats(self) -> dict[str, Any]:
        """Get storage statistics."""
        # Use cached total entries count
        total_entries = self._total_entries
        shard_count = 0
        total_size = 0

        for shard_file in self.entries_dir.glob("*.json"):
            shard_count += 1
            total_size += shard_file.stat().st_size

        # Count by type (requires scanning all entries)
        type_counts = {}
        for shard_file in self.entries_dir.glob("*.json"):
            entries = self._load_shard(shard_file.stem)
            for entry in entries.values():
                type_counts[entry.entry_type] = type_counts.get(entry.entry_type, 0) + 1

        return {
            "total_entries": total_entries,
            "shard_count": shard_count,
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "type_distribution": type_counts,
            "operations": self.stats.copy(),
            "cache_stats": self._shard_cache.get_stats(),
            "storage_dir": str(self.storage_dir),
            "aggressive_memory_mode": self.aggressive_memory_mode
        }

    def cleanup_old_files(self, months_to_keep: int = 1) -> int:
        """Remove entries older than specified months."""
        from datetime import timedelta

        cutoff_date = datetime.now() - timedelta(days=30 * months_to_keep)
        total_deleted = 0

        for shard_file in self.entries_dir.glob("*.json"):
            shard_id = shard_file.stem
            entries = self._load_shard(shard_id)

            to_delete = [
                entry_id for entry_id, entry in entries.items()
                if entry.timestamp < cutoff_date
            ]

            for entry_id in to_delete:
                del entries[entry_id]
                total_deleted += 1

            if to_delete:
                self._save_shard(shard_id, entries)
                logger.info(f"Cleaned up {len(to_delete)} old entries from shard {shard_id}")

        self._clear_memory_if_aggressive()
        return total_deleted

    def get_all_shard_ids(self) -> list[str]:
        """Get list of all shard IDs."""
        return sorted([f.stem for f in self.entries_dir.glob("*.json")])

    def clear_cache(self) -> None:
        """Manually clear the shard cache."""
        self._shard_cache.clear()
        gc.collect()

    async def aadd_entry(self, entry: KnowledgeEntry) -> str:
        """Async version of add_entry."""
        await asyncio.to_thread(self.add_entry, entry)
        return entry.id

    async def aquery(
        self,
        filter_dict: dict[str, Any] | None = None,
        max_shards_scanned: int | None = None
    ) -> list[KnowledgeEntry]:
        """Async version of query."""
        return await asyncio.to_thread(
            lambda: list(self.query(filter_dict, max_shards_scanned))
        )

    async def aget_stats(self) -> dict[str, Any]:
        """Async version of get_stats."""
        return await asyncio.to_thread(self.get_stats)

    async def asearch_recent(self, limit: int = 100) -> list[KnowledgeEntry]:
        """Async version of search_recent."""
        return await asyncio.to_thread(self.search_recent, limit)


# Global instance cache
_storage_instances: dict[str, AtomicJSONKnowledgeGraph] = {}


def get_atomic_storage(
    storage_dir: str = "storage/knowledge_graph",
    max_shards: int = 4,
    aggressive_memory_mode: bool = False
) -> AtomicJSONKnowledgeGraph:
    """
    Get or create a singleton instance of AtomicJSONKnowledgeGraph.

    Args:
        storage_dir: Directory for storage files
        max_shards: Maximum number of shards to keep in cache
        aggressive_memory_mode: If True, clear cache after each operation

    Returns:
        AtomicJSONKnowledgeGraph instance
    """
    cache_key = f"{storage_dir}:{max_shards}:{aggressive_memory_mode}"
    if cache_key not in _storage_instances:
        _storage_instances[cache_key] = AtomicJSONKnowledgeGraph(
            storage_dir=storage_dir,
            max_shards=max_shards,
            aggressive_memory_mode=aggressive_memory_mode
        )

    return _storage_instances[cache_key]


def clear_storage_cache() -> None:
    """Clear the storage instance cache."""
    global _storage_instances
    for storage in _storage_instances.values():
        storage.clear_cache()
    _storage_instances = {}


# =============================================================================
# SNAPSHOT STORAGE - WARC-lite disk-only storage for high-value evidence
# =============================================================================

@dataclass
class SnapshotEntry:
    """Metadata snapshotu ulozeneho na disku."""
    evidence_id: str
    url: str
    snapshot_path: str
    content_hash: str
    content_type: str
    size_bytes: int
    compressed: bool
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)


class SnapshotStorage:
    """WARC-lite snapshot storage - disk-only, RAM nikdy nedrzi full body."""

    # M1 8GB HARD LIMITY
    MAX_SNAPSHOT_SIZE = 5 * 1024 * 1024  # 5MB hard limit
    MAX_TOTAL_SNAPSHOTS = 100  # Max pocet snapshotu v RAM indexu
    CHUNK_SIZE = 64 * 1024  # 64KB chunks for streaming encryption

    def __init__(self, storage_dir: Path | None = None, encrypt_at_rest: bool = False):
        import os
        from pathlib import Path

        self._storage_dir = storage_dir or Path.home() / '.hledac' / 'snapshots'
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        # Check for encryption flag
        self._encrypt_at_rest = encrypt_at_rest or os.environ.get('ENCRYPT_AT_REST', '0') == '1'
        self._encryption_key = os.environ.get('ENCRYPTION_KEY', '').encode() if self._encrypt_at_rest else None

        if self._encrypt_at_rest:
            logger.info("[ENCRYPT] enabled=True target=snapshot")
            # Initialize encryption
            self._init_encryption()
        else:
            self._cipher = None

        self._index: dict[str, SnapshotEntry] = {}  # evidence_id -> metadata only
        self._cas_index: dict[str, str] = {}  # content_hash -> blob_path (CAS)

        # Delta compressor for snapshot storage (Sprint 39)
        self._delta_compressor = DeltaCompressor(compress=True) if DELTA_AVAILABLE else None
        self._previous_content: str | None = None  # For delta computation

        # Sprint 79b: ZSTD compression for snapshots
        if ZSTD_AVAILABLE:
            self._zstd_compressor = zstd.ZstdCompressor(level=3)
            self._zstd_decompressor = zstd.ZstdDecompressor()
        else:
            self._zstd_compressor = None
            self._zstd_decompressor = None

        self._load_index()

    def _init_encryption(self):
        """Initialize encryption cipher."""
        if not self._encryption_key:
            # Generate temporary key - data won't persist across sessions
            import secrets
            self._encryption_key = secrets.token_bytes(32)
            logger.warning("[ENCRYPT] No ENCRYPTION_KEY env var - using temporary key")

        try:
            import secrets as sec

            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            self._nonce = sec.token_bytes(12)
            self._cipher = Cipher(algorithms.AES(self._encryption_key), modes.GCM(self._nonce))
        except ImportError:
            logger.warning("[ENCRYPT] cryptography not available, encryption disabled")
            self._encrypt_at_rest = False
            self._cipher = None

    def _load_index(self) -> None:
        """Load index from disk."""
        index_path = self._storage_dir / 'index.json'
        if index_path.exists():
            try:
                with open(index_path) as f:
                    data = json.load(f)
                for evidence_id, entry_data in data.items():
                    self._index[evidence_id] = SnapshotEntry(**entry_data)
                    # Build CAS index from existing entries
                    if entry_data.get('content_hash') and entry_data.get('snapshot_path'):
                        self._cas_index[entry_data['content_hash']] = entry_data['snapshot_path']
                logger.info(f"[SNAPSHOT] Loaded {len(self._index)} entries from index, {len(self._cas_index)} CAS entries")
            except Exception as e:
                logger.warning(f"Failed to load snapshot index: {e}")

    def _save_index(self) -> None:
        """Save index to disk."""
        try:
            index_path = self._storage_dir / 'index.json'
            data = {
                evidence_id: {
                    'evidence_id': entry.evidence_id,
                    'url': entry.url,
                    'snapshot_path': entry.snapshot_path,
                    'content_hash': entry.content_hash,
                    'content_type': entry.content_type,
                    'size_bytes': entry.size_bytes,
                    'compressed': entry.compressed,
                    'created_at': entry.created_at,
                    'metadata': entry.metadata,
                }
                for evidence_id, entry in self._index.items()
            }
            with open(index_path, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            logger.warning(f"Failed to save snapshot index: {e}")

    def _get_snapshot_path(self, content_hash: str) -> Path:
        """Generate path for snapshot file (CAS - keyed by content_hash)."""
        # Shard by first 2 chars of content_hash
        shard = content_hash[:2]
        shard_dir = self._storage_dir / 'blobs' / shard
        shard_dir.mkdir(parents=True, exist_ok=True)
        return shard_dir / f"{content_hash}.gz"

    async def store_snapshot(self, evidence_id: str, url: str,
                            content_bytes: bytes, content_type: str,
                            metadata: dict[str, Any] | None = None) -> SnapshotEntry | None:
        """Ulozi snapshot na disk (CAS - content-addressable)."""
        import gzip

        # Check size limit
        if len(content_bytes) > self.MAX_SNAPSHOT_SIZE:
            logger.warning(f"[SNAPSHOT] Content too large ({len(content_bytes)} bytes), truncating")
            content_bytes = content_bytes[:self.MAX_SNAPSHOT_SIZE]

        try:
            content_hash = hashlib.sha256(content_bytes).hexdigest()[:32]
            logger.debug(f"[SNAPSHOT] content_hash={content_hash}")

            # CAS DEDUP: Check if blob already exists
            if content_hash in self._cas_index:
                existing_blob_path = self._cas_index[content_hash]
                # Blob already exists - create metadata entry pointing to existing blob
                entry = SnapshotEntry(
                    evidence_id=evidence_id,
                    url=url,
                    snapshot_path=existing_blob_path,  # Points to existing blob
                    content_hash=content_hash,
                    content_type=content_type,
                    size_bytes=len(content_bytes),
                    compressed=True,
                    created_at=time.time(),
                    metadata=metadata or {}
                )
                self._index[evidence_id] = entry
                self._save_index()
                logger.info(f"[SNAPSHOT CAS] Skipped blob_hash={content_hash} - using existing {existing_blob_path}")
                return entry

            # Sprint 79b: ZSTD compression if available, fallback to gzip
            if self._zstd_compressor:
                try:
                    compressed = self._zstd_compressor.compress(content_bytes)
                except Exception as e:
                    logger.warning(f"[SNAPSHOT] ZSTD compression failed: {e}, falling back to gzip")
                    compressed = gzip.compress(content_bytes, compresslevel=6)
            else:
                compressed = gzip.compress(content_bytes, compresslevel=6)

            bytes_to_write = compressed
            bytes_in = len(compressed)

            # Encrypt if enabled (streaming/chunked)
            if self._encrypt_at_rest and self._cipher:
                try:
                    encryptor = self._cipher.encryptor()
                    encrypted = encryptor.update(compressed) + encryptor.finalize()
                    # Prepend nonce for decryption
                    bytes_to_write = self._nonce + encryptor.tag + encrypted
                    logger.debug(f"[ENCRYPT] stored bytes_in={bytes_in} bytes_out={len(bytes_to_write)}")
                except Exception as e:
                    logger.warning(f"[ENCRYPT] encryption failed: {e}, storing unencrypted")
                    bytes_to_write = compressed

            blob_path = self._get_snapshot_path(content_hash)  # Key by content_hash
            with open(blob_path, 'wb') as f:
                f.write(bytes_to_write)

            # Update CAS index
            self._cas_index[content_hash] = str(blob_path)

            # Store only metadata in RAM
            entry = SnapshotEntry(
                evidence_id=evidence_id,
                url=url,
                snapshot_path=str(blob_path),
                content_hash=content_hash,
                content_type=content_type,
                size_bytes=len(content_bytes),
                compressed=True,
                created_at=time.time(),
                metadata=metadata or {}
            )

            # Evict oldest if needed (by evidence_id index)
            if len(self._index) >= self.MAX_TOTAL_SNAPSHOTS:
                oldest = min(self._index.items(), key=lambda x: x[1].created_at)
                del self._index[oldest[0]]

            self._index[evidence_id] = entry
            self._save_index()

            logger.info(f"[SNAPSHOT CAS] Stored blob_hash={content_hash}: {len(content_bytes)} bytes -> {len(compressed)} compressed")
            return entry

        except Exception as e:
            logger.error(f"[SNAPSHOT] Failed to store: {e}")
            return None

    async def load_snapshot(self, evidence_id: str) -> bytes | None:
        """Nacte snapshot z disku (on-demand, ne cache)."""
        import gzip

        if evidence_id not in self._index:
            return None

        entry = self._index[evidence_id]
        try:
            with open(entry.snapshot_path, 'rb') as f:
                data = f.read()

            # Decrypt if needed (check for encrypted header)
            if self._encrypt_at_rest and self._cipher and len(data) > 32:
                try:
                    # Extract nonce (12 bytes) + tag (16 bytes) + ciphertext
                    nonce = data[:12]
                    tag = data[12:28]
                    ciphertext = data[28:]

                    cipher = Cipher(
                        algorithms.AES(self._encryption_key),
                        modes.GCM(nonce, tag)
                    )
                    decryptor = cipher.decryptor()
                    compressed = decryptor.update(ciphertext) + decryptor.finalize()
                except Exception as e:
                    logger.warning(f"[ENCRYPT] decryption failed: {e}")
                    # Try as uncompressed
                    compressed = data
            else:
                compressed = data

            # Sprint 79b: ZSTD detection and decompression
            if len(compressed) >= 4:
                magic = compressed[:4]
                # ZSTD magic: 0x28, 0xB5, 0x2F, 0xFD
                if magic == b'\x28\xb5\x2f\xfd':
                    if self._zstd_decompressor:
                        return self._zstd_decompressor.decompress(compressed)
                    else:
                        logger.error("[SNAPSHOT] ZSTD compressed but decompressor not available")
                        return None
                # gzip magic: 0x1F, 0x8B
                elif magic[:2] == b'\x1f\x8b':
                    return gzip.decompress(compressed)

            # Fallback: try gzip
            return gzip.decompress(compressed)
        except Exception as e:
            logger.error(f"[SNAPSHOT] Failed to load: {e}")
            return None

    def get_entry(self, evidence_id: str) -> SnapshotEntry | None:
        """Vrati metadata snapshotu (bez obsahu)."""
        return self._index.get(evidence_id)

    def is_stored(self, evidence_id: str) -> bool:
        return evidence_id in self._index

    def get_stats(self) -> dict[str, Any]:
        """Get storage statistics."""
        total_size = sum(e.size_bytes for e in self._index.values())
        return {
            'total_snapshots': len(self._index),
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'storage_dir': str(self._storage_dir),
        }


# =============================================================================
# EVIDENCE PACKET - Disk-first provenance for OSINT audit trail
# =============================================================================

@dataclass
class EvidencePacket:
    """
    Evidence Packet - disk-first provenance record for OSINT audit trail.

    V RAM drží jen reference/pointery, žádné fulltexty.
    Všechny heavy data jsou na disku (snapshot_ref, graph_refs).
    """
    evidence_id: str
    url: str
    final_url: str
    domain: str
    fetched_at: float
    status: int
    headers_digest: str  # SHA-256 hash of selected headers (not full)
    snapshot_ref: dict[str, Any]  # {blob_hash, path, size, encrypted: bool}
    content_hash: str
    simhash: str | None = None
    page_type: str | None = None
    metadata_digests: dict[str, str] = field(default_factory=dict)  # {json_ld_hash, opengraph_hash}
    # Delta recrawl fields
    delta_recrawl: bool = False  # This is a delta recrawl
    delta_score: float = 0.0  # 0..1 change score
    delta_reason: str = ""  # Max 60 chars
    delta_fields_changed: list[str] = field(default_factory=list)  # Max 10 fields
    previous_evidence_id: str | None = None  # Pointer to previous evidence

    flags: dict[str, bool] = field(default_factory=dict)  # {stale, swr, blocked}
    graph_refs: dict[str, list[str]] = field(default_factory=dict)  # {node_ids: [], edge_ids: []}
    claims: list[dict[str, Any]] = field(default_factory=list)  # Claim references

    # Hard limits
    MAX_GRAPH_REFS = 10  # Max edge_ids in graph_refs
    MAX_NODE_REFS = 20  # Max node_ids in graph_refs
    MAX_CLAIMS_PER_PACKET = 12  # Max claims per packet
    MAX_DELTA_FIELDS = 10  # Max delta fields changed

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidencePacket:
        """Create from dictionary."""
        return cls(**data)

    def add_edge_ref(self, edge_id: str) -> None:
        """Add edge_id to graph_refs with hard limit (ring-like eviction)."""
        edge_ids = self.graph_refs.setdefault('edge_ids', deque(maxlen=self.MAX_GRAPH_REFS))
        if edge_id in edge_ids:
            return  # Already present
        if len(edge_ids) >= self.MAX_GRAPH_REFS:
            # Ring-like: remove oldest (first), add new at end
            edge_ids.popleft()
        edge_ids.append(edge_id)

    def add_node_ref(self, node_id: str) -> None:
        """Add node_id to graph_refs with hard limit (ring-like eviction)."""
        node_ids = self.graph_refs.setdefault('node_ids', deque(maxlen=self.MAX_NODE_REFS))
        if node_id in node_ids:
            return  # Already present
        if len(node_ids) >= self.MAX_NODE_REFS:
            # Ring-like: remove oldest (first), add new at end
            node_ids.popleft()
        node_ids.append(node_id)

    def add_claims(self, claims: list[Claim]) -> None:
        """Add claims to packet with hard limit."""
        for claim in claims:
            if len(self.claims) >= self.MAX_CLAIMS_PER_PACKET:
                break
            self.claims.append({
                "claim_id": claim.claim_id,
                "subject": claim.subject,
                "predicate": claim.predicate,
                "object": claim.object,
                "confidence": claim.confidence
            })

    def get_claim_ids(self) -> list[str]:
        """Get list of claim IDs from this packet."""
        return [c["claim_id"] for c in self.claims]


# =============================================================================
# CLAIM EXTRACTION - RAM-safe claim representation
# =============================================================================

@dataclass
class Claim:
    """
    Claim - normalized fact extracted from EvidencePacket.
    RAM-safe: short strings only, everything else is digest.
    """
    claim_id: str  # sha256 hash of canonical string
    subject: str  # Max 80 chars
    predicate: str  # Max 80 chars
    object: str  # Max 80 chars
    polarity: int  # +1 positive, -1 negative, 0 neutral
    time_anchor: str | None = None  # YYYY-MM or similar
    confidence: float = 0.5  # 0-1
    source_evidence_id: str = ""

    # Hard limits
    MAX_FIELD_LEN = 80

    def __post_init__(self):
        """Truncate fields to hard limits."""
        self.subject = self.subject[:self.MAX_FIELD_LEN]
        self.predicate = self.predicate[:self.MAX_FIELD_LEN]
        self.object = self.object[:self.MAX_FIELD_LEN]
        self.polarity = max(-1, min(1, self.polarity))
        self.confidence = max(0.0, min(1.0, self.confidence))

    @classmethod
    def create_from_text(cls, text: str, evidence_id: str, hermes_available: bool = False) -> list[Claim]:
        """
        Extract claims from text using Hermes or fallback heuristics.
        Returns max 12 claims per text.
        """
        claims = []

        if hermes_available:
            # TODO: Use Hermes for extraction (requires integration)
            pass

        # Fallback: simple SVO heuristics
        claims = cls._extract_svo_heuristic(text, evidence_id)
        return claims[:12]  # Hard limit

    @classmethod
    def _extract_svo_heuristic(cls, text: str, evidence_id: str) -> list[Claim]:
        """Simple SVO extraction fallback - limited patterns."""
        import re

        claims = []
        text = text[:2000]  # Limit input

        # Pattern: "X is Y", "X was Y", "X will be Y"
        copula_pattern = re.compile(
            r'([A-Z][a-zA-Z]{2,40})\s+(is|was|will be|were|are|been)\s+([A-Z][a-zA-Z]{2,40}[a-zA-Z0-9,\-\s]{0,40})',
            re.IGNORECASE
        )
        for match in copula_pattern.finditer(text):
            subject = match.group(1).strip()
            predicate = match.group(2).lower()
            obj = match.group(3).strip()[:80]
            claim_id = hashlib.sha256(f"{subject}|{predicate}|{obj}".encode()).hexdigest()[:16]
            claims.append(cls(
                claim_id=claim_id,
                subject=subject,
                predicate=predicate,
                object=obj,
                polarity=0,
                confidence=0.3,  # Low confidence for heuristic
                source_evidence_id=evidence_id
            ))

        # Pattern: "X announced Y", "X revealed Y"
        verb_pattern = re.compile(
            r'([A-Z][a-zA-Z]{2,40})\s+(announced|revealed|released|unveiled|launched|introduced)\s+([a-zA-Z][a-zA-Z0-9,\-\s]{0,50})',
            re.IGNORECASE
        )
        for match in verb_pattern.finditer(text):
            subject = match.group(1).strip()
            predicate = match.group(2).lower()
            obj = match.group(3).strip()[:80]
            if len(claims) < 12:
                claim_id = hashlib.sha256(f"{subject}|{predicate}|{obj}".encode()).hexdigest()[:16]
                claims.append(cls(
                    claim_id=claim_id,
                    subject=subject,
                    predicate=predicate,
                    object=obj,
                    polarity=0,
                    confidence=0.3,
                    source_evidence_id=evidence_id
                ))

        return claims


@dataclass
class ClaimCluster:
    """
    ClaimCluster - groups evidence for same claim across sources.
    Disk-first with LRU RAM cache.
    """
    claim_id: str
    subject: str  # First seen subject variant
    predicate: str
    evidence_ids: deque = field(default_factory=lambda: deque(maxlen=MAX_EVIDENCE))  # Ring buffer max 20
    domains: deque = field(default_factory=lambda: deque(maxlen=MAX_DOMAINS))  # Ring buffer max 20
    first_seen: str = ""  # ISO timestamp
    last_seen: str = ""  # ISO timestamp
    positive_count: int = 0
    negative_count: int = 0
    object_variants: deque = field(default_factory=lambda: deque(maxlen=MAX_OBJECT_VARIANTS))  # Max 10
    timeline_events: list[dict[str, Any]] = field(default_factory=list) # Max 10
    has_drift: bool = False  # Drift detected flag
    uncertainty_score: float = 0.0  # 0..1 uncertainty for evidence minimization
    metadata_digests: dict[str, str] = field(default_factory=dict)  # {source_fp, veracity_prior, ...}
    source_fp_map: dict[str, str] = field(default_factory=dict)  # evidence_id -> source_fp (bounded to MAX_EVIDENCE)
    # Stance tracking per evidence (UPGRADE C)
    evidence_stances: dict[str, dict[str, Any]] = field(default_factory=dict)  # evidence_id -> {stance_label, stance_confidence, stance_anchors}
    # Veracity prior (UPGRADE B)
    veracity_prior: float = 0.5  # 0..1 prior belief in claim veracity
    veracity_prior_confidence: float = 0.0  # Confidence in prior (0..1)

    # Sprint 62: BetaBinomial for belief tracking
    _bb: Any = field(default=None, repr=False)  # BetaBinomial instance
    supporting_evidence: list[dict] = field(default_factory=list)
    contradicting_evidence: list[dict] = field(default_factory=list)
    _max_evidence: int = 100

    # Hard limits
    MAX_EVIDENCE = 20
    MAX_DOMAINS = 20
    MAX_OBJECT_VARIANTS = 10
    MAX_TIMELINE_EVENTS = 10

    def __post_init__(self):
        """Initialize BetaBinomial after dataclass initialization."""
        if BetaBinomial is not None:
            self._bb = BetaBinomial()
        else:
            self._bb = None

    def add_evidence(self, evidence_id: str, domain: str, obj_variant: str, polarity: int, source_fp: str | None = None) -> None:
        """Add evidence with hard limits (ring buffer)."""
        if evidence_id not in self.evidence_ids:
            if len(self.evidence_ids) >= self.MAX_EVIDENCE:
                # Ring eviction - remove oldest evidence_id and its source_fp
                evicted_id = self.evidence_ids.popleft()
                self.domains.popleft()
                # Also evict from source_fp_map
                if evicted_id in self.source_fp_map:
                    del self.source_fp_map[evicted_id]
                # Also evict from evidence_stances
                if evicted_id in self.evidence_stances:
                    del self.evidence_stances[evicted_id]
            self.evidence_ids.append(evidence_id)

            # Track source_fp per evidence (bounded to MAX_EVIDENCE)
            if source_fp:
                self.source_fp_map[evidence_id] = source_fp

        if domain not in self.domains:
            if len(self.domains) >= self.MAX_DOMAINS:
                self.domains.popleft()
            self.domains.append(domain)

        if obj_variant not in self.object_variants:
            if len(self.object_variants) >= self.MAX_OBJECT_VARIANTS:
                self.object_variants.popleft()
            self.object_variants.append(obj_variant)

        if polarity > 0:
            self.positive_count += 1
        elif polarity < 0:
            self.negative_count += 1

        now = datetime.now().isoformat()
        if not self.first_seen:
            self.first_seen = now
        self.last_seen = now

    def add_stance(self, evidence_id: str, stance: dict[str, Any]) -> None:
        """
        Add stance for evidence (bounded).

        Args:
            evidence_id: Evidence ID
            stance: Dict with stance_label, stance_confidence, stance_anchors
        """
        if evidence_id not in self.evidence_ids:
            return  # Only add stance for existing evidence

        # Enforce limits - evict oldest if needed
        if len(self.evidence_stances) >= self.MAX_EVIDENCE:
            # Remove oldest
            oldest_id = next(iter(self.evidence_stances))
            del self.evidence_stances[oldest_id]

        self.evidence_stances[evidence_id] = {
            'stance_label': stance.get('stance_label', 'discuss'),
            'stance_confidence': min(max(stance.get('stance_confidence', 0.5), 0.0), 1.0),
            'stance_anchors': stance.get('stance_anchors', [])[:2]  # Max 2 anchors
        }

    def update_veracity_prior(self, prior: float, confidence: float) -> bool:
        """
        Update veracity prior. Returns True if changed materially (>0.15).

        Args:
            prior: New veracity prior (0..1)
            confidence: Confidence in prior (0..1)

        Returns:
            True if prior changed by > 0.15
        """
        old_prior = self.veracity_prior
        self.veracity_prior = min(max(prior, 0.0), 1.0)
        self.veracity_prior_confidence = min(max(confidence, 0.0), 1.0)

        # Check for material change
        return abs(self.veracity_prior - old_prior) > 0.15

    def get_stance_metrics(self) -> dict[str, Any]:
        """
        Get stance metrics for this cluster.

        Returns:
            Dict with contradiction_rate, stance_entropy, support_count, refute_count, discuss_count
        """
        import math
        from collections import Counter

        if not self.evidence_stances:
            return {
                'contradiction_rate': 0.0,
                'stance_entropy': 0.0,
                'support_count': 0,
                'refute_count': 0,
                'discuss_count': 0,
                'unrelated_count': 0
            }

        counts = Counter()
        for stance in self.evidence_stances.values():
            counts[stance.get('stance_label', 'discuss')] += 1

        support = counts.get('support', 0)
        refute = counts.get('refute', 0)
        discuss = counts.get('discuss', 0)
        unrelated = counts.get('unrelated', 0)

        total = support + refute + discuss + unrelated
        if total == 0:
            return {
                'contradiction_rate': 0.0,
                'stance_entropy': 0.0,
                'support_count': 0,
                'refute_count': 0,
                'discuss_count': 0,
                'unrelated_count': 0
            }

        # Contradiction rate
        contradiction_rate = refute / max(1, support + refute)

        # Entropy
        probs = [support / total, refute / total, discuss / total, unrelated / total]
        entropy = -sum(p * math.log2(p) for p in probs if p > 0)

        return {
            'contradiction_rate': contradiction_rate,
            'stance_entropy': entropy,
            'support_count': support,
            'refute_count': refute,
            'discuss_count': discuss,
            'unrelated_count': unrelated
        }

    def is_contested(self, threshold: int = 2) -> bool:
        """Check if claim is contested (multiple object variants or polarity conflict)."""
        # Multiple different object variants
        if len(self.object_variants) >= threshold:
            return True
        # Polarity conflict
        if self.positive_count > 0 and self.negative_count > 0:
            return True
        return False

    def get_dominant_object(self) -> str | None:
        """Get most common object variant."""
        if not self.object_variants:
            return None
        return self.object_variants[-1]  # Most recent

    def compute_uncertainty(self) -> float:
        """
        Compute uncertainty score 0..1 based on:
        - contested (+0.5)
        - drift (+0.3)
        - low domains (1/domains_count) * 0.3
        - low evidence (1/evidence_count) * 0.2
        """
        score = 0.0
        if self.is_contested():
            score += 0.5
        if self.has_drift:
            score += 0.3
        if self.domains:
            score += (1.0 / len(self.domains)) * 0.3
        if self.evidence_ids:
            score += (1.0 / len(self.evidence_ids)) * 0.2
        return min(score, 1.0)

    # Sprint 62: BetaBinomial methods for belief tracking
    def add_bb_evidence(self, evidence_id: str, weight: float, supports: bool, timestamp: float, domain: str) -> None:
        """Add evidence to BetaBinomial tracker."""
        if self._bb is None:
            return
        entry = {"evidence_id": evidence_id, "weight": float(weight), "timestamp": float(timestamp), "domain": domain}
        if supports:
            self.supporting_evidence.append(entry)
            self._bb.add_support(weight)
            if len(self.supporting_evidence) > self._max_evidence:
                self.supporting_evidence = self.supporting_evidence[-self._max_evidence:]
        else:
            self.contradicting_evidence.append(entry)
            self._bb.add_contradict(weight)
            if len(self.contradicting_evidence) > self._max_evidence:
                self.contradicting_evidence = self.contradicting_evidence[-self._max_evidence:]

    def belief(self) -> float:
        """Return belief mean from BetaBinomial."""
        if self._bb is None:
            return 0.5
        return self._bb.mean()

    def uncertainty(self) -> float:
        """Return uncertainty (std) from BetaBinomial."""
        if self._bb is None:
            return 0.5
        import math
        return math.sqrt(self._bb.variance())

    def conflict(self) -> float:
        """Return conflict score from BetaBinomial."""
        if self._bb is None:
            return 0.0
        return self._bb.conflict()

    def independent_sources(self) -> int:
        """Return count of independent sources from supporting evidence."""
        return len({ev.get("domain") for ev in self.supporting_evidence if ev.get("domain")})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClaimCluster:
        return cls(**data)


class ClaimClusterIndex:
    """
    Disk-first claim cluster index with LRU RAM cache.
    Hard limits: max 200 clusters in RAM, unlimited on disk.
    """

    MAX_CLAIMS_RAM = 200
    MAX_CLAIMS_DISK = 5000

    def __init__(self, storage_dir: Path | None = None):
        self._storage_dir = storage_dir or Path.home() / '.hledac' / 'claim_clusters'
        self._clusters_dir = self._storage_dir / 'clusters'
        self._clusters_dir.mkdir(parents=True, exist_ok=True)

        # LRU cache for RAM
        self._ram_cache: Ordereddict[str, ClaimCluster] = OrderedDict()
        self._disk_count = 0

        # Alignment tracking - cross-source independence
        self._source_fp_to_domain: dict[str, str] = {}
        self._last_author_entity: str | None = None
        self._repost_domains: set[str] = set()

    def _get_cluster_path(self, claim_id: str) -> Path:
        """Get path for cluster file (sharded by claim_id prefix)."""
        shard = claim_id[:2] if len(claim_id) >= 2 else 'xx'
        shard_dir = self._clusters_dir / shard
        shard_dir.mkdir(parents=True, exist_ok=True)
        return shard_dir / f"{claim_id}.json"

    def get_or_create(self, claim_id: str, subject: str, predicate: str) -> ClaimCluster:
        """Get cluster from RAM or disk, create if not exists."""
        # Try RAM first
        if claim_id in self._ram_cache:
            self._ram_cache.move_to_end(claim_id)
            return self._ram_cache[claim_id]

        # Try disk
        cluster = self._load_from_disk(claim_id)
        if cluster:
            self._add_to_ram_cache(claim_id, cluster)
            return cluster

        # Create new
        cluster = ClaimCluster(
            claim_id=claim_id,
            subject=subject[:80],
            predicate=predicate[:80]
        )
        self._add_to_ram_cache(claim_id, cluster)
        return cluster

    def _add_to_ram_cache(self, claim_id: str, cluster: ClaimCluster) -> None:
        """Add to RAM cache with LRU eviction."""
        if len(self._ram_cache) >= self.MAX_CLAIMS_RAM:
            # Evict oldest
            evicted_id, evicted_cluster = self._ram_cache.popitem(last=False)
            self._save_to_disk(evicted_id, evicted_cluster)

        self._ram_cache[claim_id] = cluster

    def _save_to_disk(self, claim_id: str, cluster: ClaimCluster) -> None:
        """Save cluster to disk."""
        try:
            path = self._get_cluster_path(claim_id)
            temp_path = path.with_suffix('.tmp')
            with open(temp_path, 'w') as f:
                json.dump(cluster.to_dict(), f, separators=(',', ':'))
            temp_path.replace(path)
            self._disk_count += 1
        except Exception as e:
            logger.warning(f"[CLAIM] Failed to save cluster {claim_id}: {e}")

    def _load_from_disk(self, claim_id: str) -> ClaimCluster | None:
        """Load cluster from disk."""
        try:
            path = self._get_cluster_path(claim_id)
            if not path.exists():
                return None
            with open(path) as f:
                data = json.load(f)
            return ClaimCluster.from_dict(data)
        except Exception:
            return None

    def add_evidence_to_cluster(
        self,
        claim_id: str,
        subject: str,
        predicate: str,
        object_variant: str,
        evidence_id: str,
        domain: str,
        polarity: int = 0,
        source_fp: str | None = None
    ) -> ClaimCluster:
        """Add evidence to cluster and persist."""
        cluster = self.get_or_create(claim_id, subject, predicate)
        cluster.add_evidence(evidence_id, domain, object_variant, polarity, source_fp=source_fp)

        # Persist to disk
        self._save_to_disk(claim_id, cluster)

        return cluster

    def get_high_uncertainty_clusters(self, topk: int = 10) -> list[tuple[str, float]]:
        """
        Get top K clusters by uncertainty score.
        Uses streaming heap to avoid loading all clusters.
        Returns: list of (claim_id, uncertainty_score) tuples.
        """
        # Use heap to find top K
        heap = []
        for claim_id in list(self._ram_cache.keys()):
            cluster = self._ram_cache[claim_id]
            uncertainty = cluster.compute_uncertainty()
            if len(heap) < topk:
                heappush(heap, (uncertainty, claim_id))
            elif uncertainty > heap[0][0]:
                heappushpop(heap, (uncertainty, claim_id))

        # Sort by uncertainty descending
        result = sorted(heap, key=lambda x: x[0], reverse=True)
        return result

    def flush(self) -> None:
        """Flush all RAM cache to disk."""
        for claim_id, cluster in self._ram_cache.items():
            self._save_to_disk(claim_id, cluster)
        self._ram_cache.clear()

    def get_cluster(self, claim_id: str) -> ClaimCluster | None:
        """Get cluster by ID."""
        return self.get_or_create(claim_id, "", "")

    def get_stats(self) -> dict[str, Any]:
        """Get index statistics."""
        return {
            "ram_clusters": len(self._ram_cache),
            "disk_clusters": self._disk_count,
            "max_ram": self.MAX_CLAIMS_RAM,
            "max_disk": self.MAX_CLAIMS_DISK
        }

    def compact_clusters(self) -> int:
        """
        Compact clusters: reduce per-cluster data to hard limits.

        Hard limits applied:
        - max evidence_ids: 10
        - max domains: 10
        - max object_variants: 5

        Returns:
            Number of clusters compacted
        """
        compacted = 0

        # Compact RAM cache
        for claim_id in list(self._ram_cache.keys()):
            cluster = self._ram_cache[claim_id]
            changed = False

            # Reduce evidence_ids to top 10
            if len(cluster.evidence_ids) > 10:
                cluster.evidence_ids = cluster.evidence_ids[:10]
                changed = True

            # Reduce domains to top 10
            if len(cluster.domains) > 10:
                cluster.domains = cluster.domains[:10]
                changed = True

            # Reduce object_variants to top 5
            if len(cluster.object_variants) > 5:
                cluster.object_variants = cluster.object_variants[:5]
                changed = True

            # Reduce timeline_events
            if len(cluster.timeline_events) > 20:
                cluster.timeline_events = cluster.timeline_events[:20]
                changed = True

            if changed:
                self._save_to_disk(claim_id, cluster)
                compacted += 1

        logger.info(f"[COMPACT] clusters_compacted={compacted}")
        return compacted

    def get_top_clusters(self, limit: int = 200) -> list[tuple[str, ClaimCluster]]:
        """
        Get top clusters by evidence count.

        Returns:
            List of (claim_id, cluster) tuples sorted by evidence count
        """
        # Combine RAM and disk
        all_clusters: list[tuple[str, ClaimCluster]] = []

        # From RAM
        for claim_id, cluster in self._ram_cache.items():
            all_clusters.append((claim_id, cluster))

        # Sample from disk (streaming, don't load all)
        if self._clusters_dir.exists():
            for shard_dir in self._clusters_dir.iterdir():
                if shard_dir.is_dir():
                    count = 0
                    for f in shard_dir.glob('*.json'):
                        if count >= 10:  # Max 10 per shard
                            break
                        try:
                            with open(f) as fp:
                                data = json.load(fp)
                            cluster = ClaimCluster.from_dict(data)
                            all_clusters.append((f.stem, cluster))
                            count += 1
                        except Exception:
                            continue

        # Sort by evidence count and return top N
        all_clusters.sort(key=lambda x: len(x[1].evidence_ids), reverse=True)
        return all_clusters[:limit]

    # =========================================================================
    # ALIGNMENT TABLE - cross-source independence + stance tracking
    # =========================================================================

    def set_source_fingerprint(self, evidence_id: str, source_fp: str, domain: str) -> None:
        """Set source fingerprint for evidence (disk-only)."""
        self._source_fp_to_domain[source_fp] = domain
        # Also store in cluster for quick access
        cluster = self.get_or_create(evidence_id[:8], "source_fp", evidence_id)
        cluster.metadata_digests = getattr(cluster, 'metadata_digests', {})
        cluster.metadata_digests['source_fp'] = source_fp

    def compute_independence(self, domain: str, source_fp: str, author_entity_id: str | None = None,
                            canonical_domain: str | None = None) -> float:
        """
        Compute independence score for a source (0..1).
        Penalizes:
        - Same source_fp across domains (-0.5)
        - Same author_entity_id (-0.2)
        - Repost chain: canonical != domain and repeating (-0.2)
        """
        score = 1.0

        # Penalize same source fingerprint across domains
        if source_fp in self._source_fp_to_domain:
            existing_domain = self._source_fp_to_domain[source_fp]
            if existing_domain != domain:
                score -= 0.5

        # Penalize same author entity
        if author_entity_id and getattr(self, '_last_author_entity', None) == author_entity_id:
            score -= 0.2
        if author_entity_id:
            self._last_author_entity = author_entity_id

        # Penalize repost chain
        if canonical_domain and canonical_domain != domain:
            # Check if this domain has been seen as canonical before
            if getattr(self, '_repost_domains', None) and canonical_domain in self._repost_domains:
                score -= 0.2
            if not hasattr(self, '_repost_domains'):
                self._repost_domains = set()
            self._repost_domains.add(domain)

        return max(0.0, min(1.0, score))

    def compute_alignment_for_cluster(self, claim_id: str) -> dict[str, Any]:
        """
        Compute alignment table for a cluster.
        Returns: {supports: [], contradicts: [], unclear: [], independent_support_count: int}

        Uses per-evidence source_fp_map from ClaimCluster for accurate independence counting.
        """
        cluster = self.get_or_create(claim_id, "", "")
        stance_domains: dict[str, list[str]] = {'supports': [], 'contradicts': [], 'unclear': []}
        unique_source_fps = set()

        # Get all evidence for this cluster and compute stance
        for i, evidence_id in enumerate(cluster.evidence_ids[:20]):  # Hard limit
            # Try to get domain from packet - use index alignment
            domain = cluster.domains[i] if i < len(cluster.domains) else "unknown"

            # Use source_fp_map per evidence_id (fallback to deterministic fp if missing)
            source_fp = cluster.source_fp_map.get(evidence_id, f"fp_{evidence_id[:8]}")
            independence = self.compute_independence(domain, source_fp)

            # Categorize by polarity
            if cluster.positive_count > cluster.negative_count:
                stance_domains['supports'].append(domain)
                if independence >= 0.6:
                    unique_source_fps.add(source_fp)
            elif cluster.negative_count > cluster.positive_count:
                stance_domains['contradicts'].append(domain)
            else:
                stance_domains['unclear'].append(domain)

        # Hard limits per stance
        stance_domains['supports'] = list(set(stance_domains['supports']))[:20]
        stance_domains['contradicts'] = list(set(stance_domains['contradicts']))[:20]
        stance_domains['unclear'] = list(set(stance_domains['unclear']))[:10]

        return {
            'stance_domains': stance_domains,
            'independent_support_count': len(unique_source_fps)
        }


class EvidencePacketStorage:
    """Disk-only storage pro EvidencePacket - žádný RAM index navíc."""

    def __init__(self, storage_dir: Path | None = None):
        from pathlib import Path

        self._storage_dir = storage_dir or Path.home() / '.hledac' / 'evidence_packets'
        self._packets_dir = self._storage_dir / 'packets'
        self._packets_dir.mkdir(parents=True, exist_ok=True)

    def _get_packet_path(self, evidence_id: str) -> Path:
        """Get path for packet file (sharded by evidence_id prefix)."""
        shard = evidence_id[:2] if len(evidence_id) >= 2 else 'xx'
        shard_dir = self._storage_dir / 'shards' / shard
        shard_dir.mkdir(parents=True, exist_ok=True)
        return shard_dir / f"{evidence_id}.json"

    def store_packet(self, evidence_id: str, packet: EvidencePacket) -> bool:
        """
        Uloží packet na disk (sharded JSON).
        Disk-only, žádný RAM index navíc.
        """
        try:
            packet_path = self._get_packet_path(evidence_id)
            # Atomic write: temp then rename
            temp_path = packet_path.with_suffix('.tmp')
            with open(temp_path, 'w') as f:
                json.dump(packet.to_dict(), f, separators=(',', ':'))
            temp_path.replace(packet_path)
            logger.info(f"[EVIDENCE PACKET] Stored: {evidence_id} -> {packet_path}")
            return True
        except Exception as e:
            logger.error(f"[EVIDENCE PACKET] Failed to store {evidence_id}: {e}")
            return False

    def load_packet(self, evidence_id: str) -> EvidencePacket | None:
        """Load packet from disk."""
        try:
            packet_path = self._get_packet_path(evidence_id)
            if not packet_path.exists():
                return None
            with open(packet_path) as f:
                data = json.load(f)
            return EvidencePacket.from_dict(data)
        except Exception as e:
            logger.error(f"[EVIDENCE PACKET] Failed to load {evidence_id}: {e}")
            return None

    def exists(self, evidence_id: str) -> bool:
        """Check if packet exists on disk."""
        return self._get_packet_path(evidence_id).exists()

    def verify_integrity(self, packet: EvidencePacket) -> dict[str, bool]:
        """
        Verify integrity of packet pointers (test-only helper).
        Returns dict with verification results.
        """
        results = {}

        # Check snapshot_ref - blob exists
        if packet.snapshot_ref:
            blob_path = packet.snapshot_ref.get('path')
            if blob_path:
                results['blob_exists'] = Path(blob_path).exists()
                results['blob_size_match'] = (
                    Path(blob_path).stat().st_size == packet.snapshot_ref.get('size', 0)
                    if results.get('blob_exists') else False
                )
            else:
                results['blob_exists'] = False
                results['blob_size_match'] = False

        # Verify content_hash (if blob exists)
        if results.get('blob_exists') and packet.content_hash:
            try:
                import hashlib
                with open(packet.snapshot_ref['path'], 'rb') as f:
                    actual_hash = hashlib.sha256(f.read()).hexdigest()[:32]
                results['content_hash_match'] = (actual_hash == packet.content_hash)
            except Exception:
                results['content_hash_match'] = False
        else:
            results['content_hash_match'] = False

        return results

    def get_stats(self) -> dict[str, Any]:
        """Get storage statistics."""
        # Packets are stored in shards/ directory
        shards_dir = self._storage_dir / 'shards'
        total_packets = len(list(shards_dir.glob('**/*.json'))) if shards_dir.exists() else 0
        total_size = sum(
            f.stat().st_size for f in shards_dir.glob('**/*.json')
        ) if shards_dir.exists() else 0
        return {
            'total_packets': total_packets,
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'storage_dir': str(self._storage_dir),
        }


# Alias pro zpětnou kompatibilitu
EvidencePacketStorage.__module__ = "hledac.universal.knowledge.atomic_storage"


# =============================================================================
# PATTERN STATS - Disk-first pattern/prefix learning for frontier
# =============================================================================

@dataclass
class PatternStats:
    """
    Pattern statistics for frontier URL pattern learning.

    Tracks which URL patterns yield "signal" vs "noise" across runs.
    Disk-first: only metadata in RAM, heavy data on disk.
    """
    pattern_key: str  # (domain, path_prefix_bucket)
    requests: int = 0
    new_docs: int = 0
    dedup_hits: int = 0
    blocked_hits: int = 0
    trap_hits: int = 0
    avg_score: float = 0.5  # EMA
    last_seen: str = ""  # ISO timestamp
    trap_hard_limit: int = 10  # Hard limit for trap hits before penalization
    yield_score: float = 0.0  # Computed: new_docs / max(1, requests)

    # Hard limits
    MAX_PREFIX_LEN = 60

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatternStats:
        """Create from dictionary."""
        return cls(**data)

    def compute_yield(self) -> float:
        """Compute yield score: new_docs / requests."""
        if self.requests == 0:
            return 0.0
        self.yield_score = self.new_docs / max(1, self.requests)
        return self.yield_score

    def update_score(self, new_score: float, alpha: float = 0.2) -> None:
        """Update EMA of average score."""
        self.avg_score = alpha * new_score + (1 - alpha) * self.avg_score


class PatternStatsManager:
    """
    Disk-first pattern statistics manager with LRU eviction.

    Hard limits:
    - max_patterns_ram: 200 patterns in RAM cache
    - max_patterns_disk: 5000 patterns on disk
    - max_prefix_len: 60 characters for path prefix
    """

    # Hard limits
    MAX_PREFIX_LEN = 60

    def __init__(
        self,
        storage_dir: Path | None = None,
        max_patterns_ram: int = 200,
        max_patterns_disk: int = 5000,
        ema_alpha: float = 0.2
    ):
        self._storage_dir = storage_dir or Path.home() / '.hledac' / 'pattern_stats'
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        self._max_patterns_ram = max_patterns_ram
        self._max_patterns_disk = max_patterns_disk
        self._ema_alpha = ema_alpha

        # RAM cache: LRUOrderedDict
        self._ram_cache: Ordereddict[str, PatternStats] = OrderedDict()
        self._ram_cache_set: set[str] = set()  # For fast lookup

    def _get_pattern_key(self, domain: str, url: str) -> str:
        """Extract path prefix bucket from URL and create pattern key."""
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = parsed.path

        # Extract first 2-3 path segments (bucket)
        segments = [s for s in path.split('/') if s]
        if len(segments) >= 2:
            prefix = '/'.join(segments[:2])
        elif len(segments) == 1:
            prefix = segments[0]
        else:
            prefix = ''

        # Truncate to max_prefix_len
        prefix = prefix[:self.MAX_PREFIX_LEN]

        # Create pattern key: domain|prefix
        return f"{domain}|{prefix}"

    def _get_pattern_path(self, pattern_key: str) -> Path:
        """Get path for pattern stats file (sharded by key prefix)."""
        # Create safe key hash
        safe_key = ''.join(c if c.isalnum() else '_' for c in pattern_key)
        shard = safe_key[:2] if len(safe_key) >= 2 else 'xx'
        shard_dir = self._storage_dir / 'shards' / shard
        shard_dir.mkdir(parents=True, exist_ok=True)

        # Use hash of full key for filename to avoid too long names
        key_hash = hashlib.sha256(pattern_key.encode()).hexdigest()[:16]
        return shard_dir / f"{key_hash}.json"

    def get_or_create(self, domain: str, url: str) -> PatternStats:
        """Get or create pattern stats for domain+url combination."""
        pattern_key = self._get_pattern_key(domain, url)

        # Check RAM cache first
        if pattern_key in self._ram_cache:
            # Move to end (most recently used)
            self._ram_cache.move_to_end(pattern_key)
            return self._ram_cache[pattern_key]

        # Try to load from disk
        pattern_path = self._get_pattern_path(pattern_key)
        if pattern_path.exists():
            try:
                with open(pattern_path) as f:
                    data = json.load(f)
                stats = PatternStats.from_dict(data)
                self._add_to_ram_cache(pattern_key, stats)
                return stats
            except Exception as e:
                logger.warning(f"[PATTERN] Failed to load {pattern_key}: {e}")

        # Create new pattern stats
        stats = PatternStats(
            pattern_key=pattern_key,
            last_seen=datetime.now().isoformat()
        )
        self._add_to_ram_cache(pattern_key, stats)
        return stats

    def _add_to_ram_cache(self, pattern_key: str, stats: PatternStats) -> None:
        """Add pattern to RAM cache with LRU eviction."""
        # Evict oldest if at capacity
        while len(self._ram_cache) >= self._max_patterns_ram:
            oldest_key, oldest_stats = self._ram_cache.popitem(last=False)
            self._ram_cache_set.discard(oldest_key)
            # Persist to disk before evicting
            self._persist_pattern(oldest_key, oldest_stats)
            logger.debug(f"[PATTERN] Evicted from RAM: {oldest_key[:50]}...")

        self._ram_cache[pattern_key] = stats
        self._ram_cache_set.add(pattern_key)

    def _persist_pattern(self, pattern_key: str, stats: PatternStats) -> None:
        """Persist pattern stats to disk."""
        try:
            pattern_path = self._get_pattern_path(pattern_key)
            temp_path = pattern_path.with_suffix('.tmp')
            with open(temp_path, 'w') as f:
                json.dump(stats.to_dict(), f, separators=(',', ':'))
            temp_path.replace(pattern_path)
        except Exception as e:
            logger.error(f"[PATTERN] Failed to persist {pattern_key}: {e}")

    def update(
        self,
        domain: str,
        url: str,
        result_type: str,  # 'new_doc', 'dedup', 'blocked', 'trap'
        score: float = 0.5
    ) -> PatternStats:
        """
        Update pattern stats in streaming style.

        Args:
            domain: Domain name
            url: Full URL
            result_type: 'new_doc', 'dedup', 'blocked', 'trap'
            score: Result score (0-1)

        Returns:
            Updated PatternStats
        """
        stats = self.get_or_create(domain, url)

        # Update counters
        stats.requests += 1

        if result_type == 'new_doc':
            stats.new_docs += 1
        elif result_type == 'dedup':
            stats.dedup_hits += 1
        elif result_type == 'blocked':
            stats.blocked_hits += 1
        elif result_type == 'trap':
            stats.trap_hits += 1

        # Update EMA score
        stats.update_score(score, self._ema_alpha)

        # Update last_seen
        stats.last_seen = datetime.now().isoformat()

        # Compute yield
        stats.compute_yield()

        # Log significant updates
        if stats.requests % 10 == 0:
            action = "boost" if stats.yield_score > 0.3 else "penalize"
            logger.info(
                f"[PATTERN] domain={domain} prefix={url.split('/')[3] if len(url.split('/')) > 3 else '/'} "
                f"yield={stats.yield_score:.2f} action={action} reason={result_type}"
            )

        return stats

    def get_yield(self, domain: str, url: str) -> float:
        """Get yield score for domain+url pattern."""
        stats = self.get_or_create(domain, url)
        return stats.yield_score

    def get_boost_factor(self, domain: str, url: str) -> float:
        """
        Get scoring boost/penalty factor based on pattern yield.

        Returns:
            - >1.0 for high-yield patterns (boost)
            - <1.0 for low-yield or blocked-heavy patterns (penalty)
        """
        stats = self.get_or_create(domain, url)

        # Base factor
        factor = 1.0

        # Yield-based adjustment (0.0 to 1.0 maps to 0.5 to 1.5)
        if stats.yield_score > 0.3:
            factor = 1.0 + (stats.yield_score - 0.3)  # 0.7 to 1.7
        elif stats.yield_score < 0.1:
            factor = 0.5 + stats.yield_score  # 0.5 to 0.6

        # Penalize high block rate
        block_rate = stats.blocked_hits / max(1, stats.requests)
        if block_rate > 0.5:
            factor *= 0.5  # Heavy penalty for blocked patterns

        # Penalize trap-heavy patterns
        if stats.trap_hits >= stats.trap_hard_limit:
            factor *= 0.3

        return max(0.1, min(2.0, factor))  # Clamp to [0.1, 2.0]

    def flush_all(self) -> None:
        """Flush all RAM cache to disk."""
        for pattern_key, stats in self._ram_cache.items():
            self._persist_pattern(pattern_key, stats)
        logger.info(f"[PATTERN] Flushed {len(self._ram_cache)} patterns to disk")

    def get_stats(self) -> dict[str, Any]:
        """Get pattern stats manager statistics."""
        # Count patterns on disk
        disk_patterns = 0
        total_size = 0
        shards_dir = self._storage_dir / 'shards'
        if shards_dir.exists():
            for f in shards_dir.glob('**/*.json'):
                try:
                    disk_patterns += 1
                    total_size += f.stat().st_size
                except Exception:
                    pass

        return {
            'ram_cache_size': len(self._ram_cache),
            'max_ram': self._max_patterns_ram,
            'disk_patterns': disk_patterns,
            'max_disk': self._max_patterns_disk,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
        }

    def evict_old_patterns(self) -> int:
        """
        Evict oldest/low-value patterns from disk if over limit.
        Returns count of evicted patterns.
        """
        shards_dir = self._storage_dir / 'shards'
        if not shards_dir.exists():
            return 0

        # Get all pattern files sorted by mtime (oldest first)
        pattern_files = sorted(
            shards_dir.glob('**/*.json'),
            key=lambda f: f.stat().st_mtime
        )

        if len(pattern_files) <= self._max_patterns_disk:
            return 0

        # Evict oldest files
        evicted = 0
        excess = len(pattern_files) - self._max_patterns_disk

        for f in pattern_files[:excess]:
            try:
                f.unlink()
                evicted += 1
            except Exception:
                pass

        if evicted > 0:
            logger.info(f"[PATTERN] Evicted {evicted} old patterns from disk")


# =============================================================================
# VERACITY & QUALITY SCORING PIPELINE
# =============================================================================

import re
from urllib.parse import urlparse


class SourceQualityScorer:
    """
    Feature-based source quality scorer.

    Computes source_quality_score (0..1) from available signals:
    - Domain/URL hygiene
    - Document quality
    - Structured signals
    - Independence signals
    - Archive signals

    Disk-first: stores only small feature digests and top reasons.
    """

    # Hard limits
    MAX_SCHEMA_TYPES = 5
    MAX_REASONS = 5

    # Precompiled regex for performance
    _REFUTATION_CUES = re.compile(
        r'\b(hoax|fake|false|debunk|misleading|lie|fraud|scam|fabricated|untrue|incorrect|inaccurate)\b',
        re.IGNORECASE
    )
    _SUPPORT_CUES = re.compile(
        r'\b(confirmed|official|study|research|evidence|verified|authentic|legitimate|true|accurate|correct)\b',
        re.IGNORECASE
    )
    _NEGATION = re.compile(r"\b(not|no|never|n't|without)\b", re.IGNORECASE)

    def __init__(self):
        pass

    def compute_source_quality(
        self,
        url: str,
        packet_metadata: dict[str, Any] | None = None,
        preview: str | None = None,
        title: str | None = None
    ) -> dict[str, Any]:
        """
        Compute source quality score from available signals.

        Args:
            url: Source URL
            packet_metadata: EvidencePacket metadata_digests
            preview: Page preview/snippet (optional)
            title: Page title (optional)

        Returns:
            Dict with score, features_hash, reasons_topk
        """
        features: dict[str, Any] = {}

        # === Domain/URL hygiene ===
        parsed = urlparse(url)
        features['https'] = parsed.scheme == 'https'
        features['url_depth'] = min(len([p for p in parsed.path.split('/') if p]), 5)  # Cap at 5
        features['query_param_count'] = min(len(parsed.query.split('&')) if parsed.query else 0, 5)

        # Check for URL normalization
        features['canonical_present'] = bool(parsed.query and 'utm_' in parsed.query)

        # === Document quality (from preview/title) ===
        if preview or title:
            text = f"{title or ''} {preview or ''}"
            features['has_byline'] = 'by' in text.lower() or 'author' in text.lower()
            features['has_date'] = bool(re.search(r'\b\d{4}[-/]\d{2}[-/]\d{2}\b', text))

            # Check date conflicts
            dates = re.findall(r'\b(\d{4})\b', text)
            if len(set(dates)) > 1:
                features['date_conflict'] = True

            # Count outbound citations (simple heuristic)
            features['outbound_citations_count'] = min(len(re.findall(r'\[\d+\]', text)), 10)

            # Primary doc links
            features['primary_doc_links_count'] = min(
                len(re.findall(r'(pdf|doc|docx)\b', text.lower())), 5
            )
        else:
            features['has_byline'] = False
            features['has_date'] = False
            features['date_conflict'] = False
            features['outbound_citations_count'] = 0
            features['primary_doc_links_count'] = 0

        # === Structured signals (from packet metadata) ===
        features['json_ld_present'] = bool(packet_metadata and 'json_ld_hash' in packet_metadata)
        features['og_present'] = bool(packet_metadata and 'opengraph_hash' in packet_metadata)

        # Schema types digest
        if packet_metadata and 'schema_types' in packet_metadata:
            schema_types = packet_metadata['schema_types'][:self.MAX_SCHEMA_TYPES]
            features['schema_types'] = sorted(set(schema_types))
        else:
            features['schema_types'] = []

        # === Independence signals ===
        # (source_fp presence is checked externally via ClaimCluster)
        features['source_fp_present'] = bool(packet_metadata and 'source_fp' in packet_metadata)

        # === Archive signals ===
        features['warc_written'] = False  # Set externally if WARC was written
        features['has_payload_digest'] = bool(packet_metadata and 'content_hash' in packet_metadata)

        # === Compute score ===
        score = self._compute_weighted_score(features)

        # === Extract top reasons ===
        reasons = self._extract_reasons(features, url, preview, title)

        # === Compute stable hash ===
        features_hash = self._compute_features_hash(features)

        return {
            'score': score,
            'features_hash': features_hash,
            'features': features,
            'reasons_topk': reasons
        }

    def _compute_weighted_score(self, features: dict[str, Any]) -> float:
        """Compute weighted quality score."""
        score = 0.0
        weights = {
            'https': 0.10,
            'url_depth': -0.02,  # Penalty per depth level
            'query_param_count': -0.01,
            'has_byline': 0.08,
            'has_date': 0.05,
            'outbound_citations_count': 0.02,
            'primary_doc_links_count': 0.05,
            'json_ld_present': 0.10,
            'og_present': 0.08,
            'has_payload_digest': 0.15,
        }

        for key, weight in weights.items():
            val = features.get(key, 0)
            if weight < 0:
                score += weight * val  # Penalty
            elif val:
                score += weight

        # Cap at 1.0
        return min(max(score, 0.0), 1.0)

    def _extract_reasons(
        self,
        features: dict[str, Any],
        url: str,
        preview: str | None,
        title: str | None
    ) -> list[str]:
        """Extract top reasons for score."""
        reasons = []

        # Positive signals
        if features.get('https'):
            reasons.append("secure HTTPS")
        if features.get('has_byline'):
            reasons.append("has byline")
        if features.get('has_date'):
            reasons.append("has date")
        if features.get('json_ld_present'):
            reasons.append("structured data")
        if features.get('og_present'):
            reasons.append("OpenGraph metadata")
        if features.get('has_payload_digest'):
            reasons.append("content verified")

        # Negative signals
        if features.get('url_depth', 0) > 3:
            reasons.append("deep URL")
        if features.get('query_param_count', 0) > 3:
            reasons.append("tracked URL")

        return reasons[:self.MAX_REASONS]

    def _compute_features_hash(self, features: dict[str, Any]) -> str:
        """Compute stable hash of features."""
        # Sort keys for deterministic serialization
        stable = json.dumps(features, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(stable.encode()).hexdigest()[:16]


class VeracityPriorCalculator:
    """
    Computes claim_veracity_prior (0..1) per ClaimCluster.

    Combines:
    - Mean source_quality_score weighted by independence
    - Contradiction rate from stances

    Stores only small digests in cluster metadata.
    """

    # Hard limits
    MAX_EVIDENCE_FOR_PRIOR = 10
    MIN_INDEPENDENCE = 0.6

    def __init__(self):
        self._scorer = SourceQualityScorer()

    def compute_veracity_prior(
        self,
        evidence_scores: list[dict[str, Any]],
        source_fp_map: dict[str, str],
        stances: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """
        Compute veracity prior from evidence scores.

        Args:
            evidence_scores: List of {evidence_id, score, features_hash}
            source_fp_map: evidence_id -> source_fp mapping
            stances: Optional evidence_id -> stance mapping

        Returns:
            Dict with prior, confidence, reasons
        """
        # Adjust for contradiction rate if stances available, even without evidence scores
        if stances:
            contradiction_rate = self._compute_contradiction_rate(stances)
            # Even without evidence scores, we can infer prior from stance distribution
            support_count = sum(1 for s in stances.values() if s.get('stance_label') == 'support')
            refute_count = sum(1 for s in stances.values() if s.get('stance_label') == 'refute')
            total = support_count + refute_count

            if total > 0:
                # Infer prior from stance ratio
                mean_score = support_count / total
                if contradiction_rate > 0.3:
                    mean_score *= (1.0 - contradiction_rate * 0.5)

                return {
                    'veracity_prior': mean_score,
                    'confidence': min(total / 5.0, 1.0),
                    'reasons': ['stance_based'],
                    'sources_considered': total
                }

        if not evidence_scores:
            return {
                'veracity_prior': 0.5,
                'confidence': 0.0,
                'reasons': ['no_evidence'],
                'sources_considered': 0
            }

        # Weight by independence (source_fp uniqueness)
        weighted_scores = []
        fp_counts: dict[str, int] = {}

        # Count FP occurrences for independence
        for _evidence_id, fp in source_fp_map.items():
            fp_counts[fp] = fp_counts.get(fp, 0) + 1

        for ev in evidence_scores[:self.MAX_EVIDENCE_FOR_PRIOR]:
            fp = source_fp_map.get(ev['evidence_id'], '')
            fp_count = fp_counts.get(fp, 1)
            independence = 1.0 / fp_count  # Lower if same source

            if independence >= self.MIN_INDEPENDENCE:
                weighted_scores.append(ev['score'] * independence)

        if not weighted_scores:
            # Fallback: use simple mean
            weighted_scores = [ev['score'] for ev in evidence_scores[:self.MAX_EVIDENCE_FOR_PRIOR]]

        mean_score = sum(weighted_scores) / len(weighted_scores) if weighted_scores else 0.5

        # Adjust for contradiction rate if stances available
        prior = mean_score
        if stances:
            contradiction_rate = self._compute_contradiction_rate(stances)
            if contradiction_rate > 0.3:
                prior *= (1.0 - contradiction_rate * 0.5)  # Reduce prior if high contradiction

        # Compute confidence based on evidence count and independence
        confidence = min(len(weighted_scores) / 5.0, 1.0)  # Max confidence with 5+ independent sources

        return {
            'veracity_prior': prior,
            'confidence': confidence,
            'reasons': ['source_quality_weighted'],
            'sources_considered': len(evidence_scores)
        }

    def _compute_contradiction_rate(
        self,
        stances: dict[str, dict[str, Any]]
    ) -> float:
        """Compute contradiction rate from stances."""
        support = 0
        refute = 0

        for _evidence_id, stance in stances.items():
            label = stance.get('stance_label', 'discuss')
            if label == 'support':
                support += 1
            elif label == 'refute':
                refute += 1

        total = support + refute
        if total == 0:
            return 0.0

        return refute / total


# =============================================================================
# HYBRID STANCE & CONTRADICTION SCORER
# =============================================================================

class StanceScorer:
    """
    Hybrid stance scorer:
    - Cheap deterministic baseline first (regex/lexicon)
    - Hermes only for hard cases (uncertainty >= 0.6 OR confidence in [0.35, 0.65])
    """

    # Hard limits
    MAX_ANCHORS = 2
    MAX_ANCHOR_LEN = 160

    # Precompiled regex for performance
    _REFUTATION_CUES = re.compile(
        r'\b(hoax|fake|false|debunk|misleading|lie|fraud|scam|fabricated|untrue|incorrect|inaccurate|rumor|myth|exposed|refuted|denied)\b',
        re.IGNORECASE
    )
    _SUPPORT_CUES = re.compile(
        r'\b(confirmed|official|study|research|evidence|verified|authentic|legitimate|true|accurate|correct|announcement|declared)\b',
        re.IGNORECASE
    )
    _NEGATION = re.compile(r"\b(not|no|never|n't|without|doubt|cannot|unlikely)\b", re.IGNORECASE)
    _DISCUSS_TERMS = re.compile(
        r'\b(debate|discuss|examining|exploring|question|analysis|review|investigation|potential|could|may|might)\b',
        re.IGNORECASE
    )

    def __init__(self):
        pass

    def score_stance(
        self,
        claim_surface: str,
        evidence_preview: str,
        title: str | None = None
    ) -> dict[str, Any]:
        """
        Score stance using deterministic baseline.

        Args:
            claim_surface: Claim subject/predicate/object variant
            evidence_preview: Evidence preview/snippet
            title: Page title (optional)

        Returns:
            Dict with stance_label, stance_confidence, stance_anchors
        """
        # Combine text for analysis
        text = f"{claim_surface} {evidence_preview} {title or ''}"

        # Count cues
        refutation_matches = self._REFUTATION_CUES.findall(text)
        support_matches = self._SUPPORT_CUES.findall(text)
        negation_matches = self._NEGATION.findall(text)
        discuss_matches = self._DISCUSS_TERMS.findall(text)

        # Determine stance
        refute_count = len(refutation_matches)
        support_count = len(support_matches)

        # Apply negation flip
        if negation_matches and (refute_count > 0 or support_count > 0):
            # Negation flips the meaning
            refute_count, support_count = support_count, refute_count

        # Determine label and confidence
        if refute_count > support_count + 1:
            stance_label = "refute"
            stance_confidence = min(0.5 + (refute_count - support_count) * 0.15, 0.95)
        elif support_count > refute_count + 1:
            stance_label = "support"
            stance_confidence = min(0.5 + (support_count - refute_count) * 0.15, 0.95)
        elif discuss_matches:
            stance_label = "discuss"
            stance_confidence = 0.55
        else:
            stance_label = "unrelated"
            stance_confidence = 0.5

        # Extract anchors (short snippets)
        anchors = self._extract_anchors(evidence_preview, title)

        return {
            'stance_label': stance_label,
            'stance_confidence': stance_confidence,
            'stance_anchors': anchors,
            'deterministic': True
        }

    def _extract_anchors(
        self,
        evidence_preview: str,
        title: str | None
    ) -> list[str]:
        """Extract short anchor snippets (bounded)."""
        anchors = []
        texts_to_check = [title] if title else []

        # Get first meaningful sentences from preview
        if evidence_preview:
            sentences = re.split(r'[.!?]', evidence_preview)
            for sent in sentences[:3]:  # Check first 3 sentences
                sent = sent.strip()
                if len(sent) >= 30:
                    texts_to_check.append(sent)

        for text in texts_to_check:
            if text and len(anchors) < self.MAX_ANCHORS:
                # Truncate to max anchor length
                anchor = text[:self.MAX_ANCHOR_LEN].strip()
                anchors.append(anchor)

        return anchors[:self.MAX_ANCHORS]

    def needs_hermes(
        self,
        cluster_uncertainty: float,
        baseline_confidence: float
    ) -> bool:
        """
        Determine if Hermes is needed for hard case resolution.

        Args:
            cluster_uncertainty: Cluster uncertainty score (0..1)
            baseline_confidence: Deterministic scorer confidence (0..1)

        Returns:
            True if Hermes should be invoked
        """
        # Hermes triggers for uncertain clusters
        if cluster_uncertainty >= 0.6:
            return True

        # Or when baseline is in uncertain middle ground
        if 0.35 <= baseline_confidence <= 0.65:
            return True

        return False

    def compute_contradiction_metrics(
        self,
        stances: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Compute contradiction metrics for a cluster.

        Args:
            stances: evidence_id -> stance mapping

        Returns:
            Dict with contradiction_rate, stance_entropy, support_count, refute_count, discuss_count
        """
        import math
        from collections import Counter

        counts = Counter()
        for stance in stances.values():
            counts[stance.get('stance_label', 'discuss')] += 1

        support = counts.get('support', 0)
        refute = counts.get('refute', 0)
        discuss = counts.get('discuss', 0)

        total = support + refute + discuss
        if total == 0:
            return {
                'contradiction_rate': 0.0,
                'stance_entropy': 0.0,
                'support_count': 0,
                'refute_count': 0,
                'discuss_count': 0
            }

        # Contradiction rate
        contradiction_rate = refute / max(1, support + refute)

        # Entropy over stance distribution
        probs = [support / total, refute / total, discuss / total]
        entropy = -sum(p * math.log2(p) for p in probs if p > 0)

        return {
            'contradiction_rate': contradiction_rate,
            'stance_entropy': entropy,
            'support_count': support,
            'refute_count': refute,
            'discuss_count': discuss
        }


# Module exports
SourceQualityScorer.__module__ = "hledac.universal.knowledge.atomic_storage"
VeracityPriorCalculator.__module__ = "hledac.universal.knowledge.atomic_storage"
StanceScorer.__module__ = "hledac.universal.knowledge.atomic_storage"
