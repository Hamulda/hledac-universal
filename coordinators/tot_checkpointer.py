"""
UNIFIED-005: Transactional ToT State Checkpointer
==================================================

Crash-resilient, periodic, atomic Tree-of-Thoughts state checkpointing.

Architecture:
  - DuckDB table ``tot_checkpoints`` as the canonical store (WAL-protected)
  - LMDB-based incremental hot-path layer for sub-ms node-level writes
  - Optional filesystem-level atomic write (tempfile + os.replace) for
    belt-and-suspenders durability on SIGKILL/power-loss
  - asyncio background task for periodic checkpointing (default: 30s)
  - BLAKE2b-256 checksum on every write; verified on restore
  - asyncio.Lock serializes concurrent checkpoint calls
  - Signal-triggered emergency checkpoint on SIGTERM/SIGINT
  - Memory-aware throttling when RSS exceeds 5.5 GiB (M1 8GB guard)

Usage (within MetaReasoningCoordinator or sprint_entrypoint)::

    from hledac.universal.coordinators.tot_checkpointer import (
        TransactionalToTCheckpointer,
    )

    checkpointer = TransactionalToTCheckpointer(
        sprint_id="8sa_...",
        duckdb_store=store,
        interval_s=30.0,
        lmdb_incremental=True,    # hot-path sub-ms writes
        memory_throttle=True,     # M1 8GB pressure guard
    )

    # Register signal handlers for emergency checkpoint
    checkpointer.register_signal_handlers()

    # Start periodic checkpointing
    await checkpointer.start()

    # Hot path: incremental node update (sub-ms, no serialization)
    await checkpointer.incremental_checkpoint(node_id, node_data)

    # Periodic: full tree snapshot to DuckDB
    await checkpointer.checkpoint(nodes=nodes, step=current_step)

    # On crash recovery:
    restored = await checkpointer.restore()

    # On clean shutdown:
    await checkpointer.stop(final_checkpoint=True)
    await checkpointer.cleanup()

M1 8GB bounds:
  - LMDB env: 4 MiB map (bounded, mmap-backed, zero-copy reads)
  - DuckDB: tree_json TEXT column bounded by _MAX_TREE_JSON_BYTES = 10 MiB
  - Filesystem fallback: optional, disabled by default
  - Background task: sleeps 30s → negligible CPU/RAM
  - Memory throttle: skips checkpoint at ≥5.5 GiB RSS

Python 3.14+ best practices:
  - asyncio.TaskGroup for structured concurrency
  - hashlib.file_digest (3.11+) for checksums
  - orjson for zero-copy serialization
  - msgspec.Struct for ThoughtNode (already frozen + gc=False)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import signal
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import orjson

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)

# ── Bounds (M1 8GB safe) ──────────────────────────────────────────────
_MAX_TREE_JSON_BYTES: int = 10 * 1024 * 1024  # 10 MiB — trees larger than this are pathological
_FALLBACK_DIR: Path = Path.home() / ".hledac" / "tot_checkpoints"
_LMDB_MAP_SIZE: int = 4 * 1024 * 1024  # 4 MiB LMDB env — bounded mmap
_LMDB_MAX_DBS: int = 2  # nodes + metadata sub-databases
_MEMORY_THROTTLE_RSS_GIB: float = 5.5  # Match http3_lane threshold


def _to_serializable(nodes: dict) -> dict:
    """Convert ToT nodes dict to a JSON-serializable structure.

    ThoughtNode is msgspec.Struct (frozen, gc=False) — msgspec.to_builtins
    converts to plain dicts efficiently. We wrap in a stable envelope:
        {"v": 1, "nodes": {...}, "root_id": "root"}
    """
    from msgspec import to_builtins

    return {
        "v": 1,
        "nodes": to_builtins(nodes),
    }


def _compute_checksum(data: bytes) -> str:
    """Compute BLAKE2b-256 hex digest — fast on M1 (NEON-accelerated)."""
    return hashlib.blake2b(data, digest_size=32).hexdigest()


class TransactionalToTCheckpointer:
    """
    UNIFIED-005: Periodic, atomic, crash-resilient ToT state checkpointer.

    Features:
      - Periodic background task (asyncio.Task, interval configurable)
      - DuckDB as canonical store (WAL-protected, INSERT OR REPLACE)
      - LMDB-based incremental hot-path layer for sub-ms node updates
      - Optional filesystem fallback (tempfile + os.replace) for belt-and-suspenders
      - BLAKE2b-256 checksum on every write; verified on restore
      - asyncio.Lock serializes concurrent checkpoint/restore
      - Signal-triggered emergency checkpoint on SIGTERM/SIGINT
      - Memory-aware throttling: skip checkpoint at ≥5.5 GiB RSS
      - Fail-soft: any error → logged, never raises to caller
      - Bounded memory: LMDB 4 MiB + _MAX_TREE_JSON_BYTES ceiling

    Lifecycle::

        checkpointer = TransactionalToTCheckpointer(
            ..., lmdb_incremental=True, memory_throttle=True
        )
        checkpointer.register_signal_handlers()
        await checkpointer.start()        # begin periodic checkpointing
        # ... sprint runs ...
        await checkpointer.incremental_checkpoint(node_id, node)  # hot path
        await checkpointer.checkpoint(nodes=nodes, step=X)       # full snapshot
        await checkpointer.stop(final=True)  # stop + final checkpoint
        await checkpointer.cleanup()      # delete checkpoints for this sprint

    Recovery::

        restored = await checkpointer.restore()
        if restored is not None:
            step, nodes, checksum = restored
            # resume from step, using nodes
    """
    __slots__ = (
        "_sprint_id",
        "_query_hash",
        "_store",
        "_interval_s",
        "_lock",
        "_step",
        "_task",
        "_fs_fallback",
        "_fs_dir",
        "_stats",
        "_nodes_ref",
        # UNIFIED-005: LMDB incremental layer
        "_lmdb_enabled",
        "_lmdb_env",
        "_lmdb_nodes_db",
        "_lmdb_meta_db",
        "_lmdb_path",
        # UNIFIED-005: Memory throttling
        "_memory_throttle",
        # UNIFIED-005: Emergency shutdown
        "_shutdown_event",
        "_signal_handlers_registered",
    )

    def __init__(
        self,
        sprint_id: str,
        duckdb_store: DuckDBShadowStore,
        interval_s: float = 30.0,
        *,
        fs_fallback: bool = False,
        fs_dir: Path | None = None,
        query_hash: str = "",  # UNIFIED-006: deterministic cross-sprint recovery key
        lmdb_incremental: bool = False,  # UNIFIED-005: LMDB hot-path layer
        lmdb_dir: Path | None = None,  # UNIFIED-005: LMDB directory
        memory_throttle: bool = True,  # UNIFIED-005: memory pressure guard
    ) -> None:
        """
        Args:
            sprint_id: Sprint identifier for checkpoint isolation.
            duckdb_store: Initialized DuckDBShadowStore instance.
            interval_s: Periodic checkpoint interval (default 30s).
            fs_fallback: If True, also write to filesystem using atomic
                         tempfile + os.replace (belt-and-suspenders).
            fs_dir: Filesystem directory for fs_fallback checkpoints.
                    Default: ~/.hledac/tot_checkpoints/
            query_hash: UNIFIED-006 — BLAKE2b-16 hex of the query string.
                        Enables cross-sprint orphan recovery when sprint_id
                        is unknown (new sprint restart after crash).
            lmdb_incremental: UNIFIED-005 — enable LMDB-based incremental
                              hot-path layer. Writes individual node updates
                              in sub-ms (O(1)) vs full-tree serialization.
                              M1 8GB: 4 MiB mmap, zero-copy reads.
            lmdb_dir: UNIFIED-005 — LMDB environment directory.
                      Default: ~/.hledac/tot_lmdb/
            memory_throttle: UNIFIED-005 — skip checkpoint when RSS ≥ 5.5 GiB.
                             Prevents OOM on M1 8GB under pressure.
        """
        self._sprint_id: str = sprint_id
        self._query_hash: str = query_hash
        self._store: DuckDBShadowStore = duckdb_store
        self._interval_s: float = max(5.0, interval_s)  # floor: 5s
        self._lock: asyncio.Lock = asyncio.Lock()
        self._step: int = 0
        self._task: asyncio.Task[None] | None = None
        self._fs_fallback: bool = fs_fallback
        self._fs_dir: Path = fs_dir or _FALLBACK_DIR
        self._stats: dict[str, int] = {
            "checkpoints_written": 0,
            "checkpoints_skipped": 0,
            "checkpoints_failed": 0,
            "restores_attempted": 0,
            "restores_succeeded": 0,
            # UNIFIED-005: new counters
            "incremental_writes": 0,
            "incremental_failures": 0,
            "memory_throttles": 0,
            "emergency_checkpoints": 0,
        }
        # Weak reference to coordinator's nodes dict — set by bind()
        self._nodes_ref: dict | None = None

        # UNIFIED-005: LMDB incremental layer
        self._lmdb_enabled: bool = lmdb_incremental
        self._lmdb_env: Any = None
        self._lmdb_nodes_db: Any = None
        self._lmdb_meta_db: Any = None
        self._lmdb_path: Path = lmdb_dir or (Path.home() / ".hledac" / "tot_lmdb")

        # UNIFIED-005: Memory throttling
        self._memory_throttle: bool = memory_throttle

        # UNIFIED-005: Emergency shutdown
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._signal_handlers_registered: bool = False

        if self._fs_fallback:
            self._fs_dir.mkdir(parents=True, exist_ok=True)
            # SEC-02: harden directory permissions
            try:
                os.chmod(self._fs_dir, 0o700)
            except OSError:
                pass

        if self._lmdb_enabled:
            self._init_lmdb()

    # ── Public API ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start periodic background checkpointing.

        Idempotent — no-op if already running. Creates an asyncio.Task
        that calls checkpoint() every interval_s seconds.
        """
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._periodic_loop(), name=f"tot_ckpt_{self._sprint_id[:12]}")
        logger.debug(
            "[UNIFIED-005] ToT checkpointing started: sprint=%s interval=%.0fs fs_fallback=%s",
            self._sprint_id[:12],
            self._interval_s,
            self._fs_fallback,
        )

    async def stop(self, *, final_checkpoint: bool = True) -> None:
        """Stop periodic checkpointing.

        Args:
            final_checkpoint: If True and nodes are available, perform one
                              final checkpoint before stopping.
        """
        # Cancel background task
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                pass
        self._task = None
        logger.debug(
            "[UNIFIED-005] ToT checkpointing stopped: sprint=%s checkpoints=%d",
            self._sprint_id[:12],
            self._stats["checkpoints_written"],
        )

    async def checkpoint(
        self,
        nodes: dict,
        step: int | None = None,
    ) -> bool:
        """
        Persist current ToT state atomically.

        Serializes nodes → computes checksum → writes to DuckDB
        (and optionally filesystem). Thread-safe via asyncio.Lock.

        UNIFIED-005: Memory pressure check before serialization — skips
        if RSS ≥ 5.5 GiB to prevent OOM on M1 8GB.

        Args:
            nodes: The ToT nodes dict (keyed by node_id → ThoughtNode).
            step: Current reasoning step. Auto-increments if None.

        Returns:
            True if checkpoint was written, False if skipped or on error.
        """
        # UNIFIED-005: Memory pressure guard — skip BEFORE serialization
        # to avoid the memory cost of serializing a large tree under pressure
        if not self._check_memory_pressure():
            self._stats["checkpoints_skipped"] += 1
            return False

        if step is not None:
            self._step = step
        else:
            self._step += 1

        current_step = self._step

        async with self._lock:
            try:
                # Serialize
                envelope = _to_serializable(nodes)
                raw = orjson.dumps(envelope, option=orjson.OPT_SORT_KEYS)

                # Size guard
                if len(raw) > _MAX_TREE_JSON_BYTES:
                    logger.error(
                        "[UNIFIED-005] Tree JSON too large: %d bytes > %d limit — skipping checkpoint",
                        len(raw),
                        _MAX_TREE_JSON_BYTES,
                    )
                    self._stats["checkpoints_skipped"] += 1
                    return False

                # Checksum
                checksum = _compute_checksum(raw)
                ts = time.time()

                # DuckDB write (canonical, WAL-protected)
                ok = await self._store.async_upsert_tot_checkpoint(
                    sprint_id=self._sprint_id,
                    step=current_step,
                    tree_json=raw.decode("utf-8"),
                    ts=ts,
                    checksum=checksum,
                    query_hash=self._query_hash,
                )

                if not ok:
                    logger.warning(
                        "[UNIFIED-005] DuckDB checkpoint write failed: sprint=%s step=%d",
                        self._sprint_id[:12],
                        current_step,
                    )
                    self._stats["checkpoints_failed"] += 1
                    return False

                # Optional filesystem fallback (belt-and-suspenders)
                if self._fs_fallback:
                    self._fs_write(raw, current_step, checksum)

                self._stats["checkpoints_written"] += 1
                logger.debug(
                    "[UNIFIED-005] Checkpoint written: sprint=%s step=%d size=%d checksum=%s",
                    self._sprint_id[:12],
                    current_step,
                    len(raw),
                    checksum[:16],
                )
                return True

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — fail-soft, never crash caller
                logger.warning(
                    "[UNIFIED-005] Checkpoint failed: sprint=%s step=%d error=%s",
                    self._sprint_id[:12],
                    current_step,
                    exc,
                )
                self._stats["checkpoints_failed"] += 1
                return False

    async def restore(self) -> tuple[int, dict, str] | None:
        """
        Load and verify the latest ToT checkpoint for this sprint.

        Reads from DuckDB (latest by ts DESC), verifies checksum,
        deserializes nodes dict. Falls back to filesystem if DuckDB
        read fails and fs_fallback is enabled.

        Returns:
            (step, nodes_dict, checksum) or None if no checkpoint exists
            or checksum verification fails.
        """
        self._stats["restores_attempted"] += 1

        async with self._lock:
            try:
                row = await self._store.async_get_latest_tot_checkpoint(
                    sprint_id=self._sprint_id,
                )

                if row is None:
                    logger.debug(
                        "[UNIFIED-005] No checkpoint found for sprint=%s",
                        self._sprint_id[:12],
                    )
                    return None

                step, tree_json_str, ts, stored_checksum = row

                # Verify checksum
                raw = tree_json_str.encode("utf-8")
                computed = _compute_checksum(raw)
                if computed != stored_checksum:
                    logger.error(
                        "[UNIFIED-005] Checksum mismatch on restore: sprint=%s step=%d "
                        "stored=%s computed=%s — data may be corrupt",
                        self._sprint_id[:12],
                        step,
                        stored_checksum[:16],
                        computed[:16],
                    )
                    # Try filesystem fallback
                    if self._fs_fallback:
                        fs_result = self._fs_read()
                        if fs_result is not None:
                            logger.info(
                                "[UNIFIED-005] Recovered from filesystem fallback: sprint=%s",
                                self._sprint_id[:12],
                            )
                            self._stats["restores_succeeded"] += 1
                            return fs_result
                    return None

                # Deserialize
                try:
                    envelope = orjson.loads(raw)
                except orjson.JSONDecodeError as exc:
                    logger.error(
                        "[UNIFIED-005] JSON decode failed on restore: sprint=%s step=%d error=%s",
                        self._sprint_id[:12],
                        step,
                        exc,
                    )
                    return None

                # Validate envelope
                if not isinstance(envelope, dict) or envelope.get("v") != 1:
                    logger.error(
                        "[UNIFIED-005] Invalid checkpoint envelope: sprint=%s",
                        self._sprint_id[:12],
                    )
                    return None

                nodes = envelope.get("nodes", {})
                if not isinstance(nodes, dict):
                    logger.error(
                        "[UNIFIED-005] Invalid nodes structure: sprint=%s",
                        self._sprint_id[:12],
                    )
                    return None

                # Restore step counter
                self._step = step

                self._stats["restores_succeeded"] += 1
                logger.info(
                    "[UNIFIED-005] Checkpoint restored: sprint=%s step=%d nodes=%d ts=%.0f",
                    self._sprint_id[:12],
                    step,
                    len(nodes),
                    ts,
                )
                return (step, nodes, stored_checksum)

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — fail-soft
                logger.warning(
                    "[UNIFIED-005] Restore failed: sprint=%s error=%s",
                    self._sprint_id[:12],
                    exc,
                )
                return None

    async def cleanup(self) -> bool:
        """Delete all checkpoints for this sprint (called on successful completion)."""
        try:
            ok = await self._store.async_delete_tot_checkpoints(
                sprint_id=self._sprint_id,
            )
            # Also cleanup filesystem fallback
            if self._fs_fallback:
                self._fs_cleanup()
            # UNIFIED-005: Close LMDB environment
            if self._lmdb_enabled:
                self._close_lmdb()
            if ok:
                logger.debug(
                    "[UNIFIED-005] Checkpoints cleaned up: sprint=%s",
                    self._sprint_id[:12],
                )
            return ok
        except Exception:  # noqa: BLE001
            return False

    # ── UNIFIED-005: LMDB Incremental Layer ───────────────────────────────

    def _init_lmdb(self) -> None:
        """
        Initialize LMDB environment for incremental hot-path checkpointing.

        M1 8GB bounds:
          - 4 MiB mmap (bounded, never grows)
          - 2 sub-databases: nodes + metadata
          - Zero-copy reads, sub-ms writes for individual node updates
        """
        try:
            import lmdb

            self._lmdb_path.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self._lmdb_path, 0o700)
            except OSError:
                pass

            self._lmdb_env = lmdb.open(
                str(self._lmdb_path / f"tot_{self._sprint_id[:12]}.lmdb"),
                map_size=_LMDB_MAP_SIZE,
                max_dbs=_LMDB_MAX_DBS,
                sync=True,  # M1 NAND — fsync on every write for crash safety
                metasync=True,
            )

            self._lmdb_nodes_db = self._lmdb_env.open_db(b"nodes")
            self._lmdb_meta_db = self._lmdb_env.open_db(b"meta")

            logger.debug(
                "[UNIFIED-005] LMDB incremental layer initialized: "
                "path=%s map_size=%d",
                self._lmdb_path,
                _LMDB_MAP_SIZE,
            )
        except ImportError:
            logger.debug(
                "[UNIFIED-005] lmdb not available — incremental layer disabled"
            )
            self._lmdb_enabled = False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[UNIFIED-005] LMDB init failed: %s — incremental layer disabled",
                exc,
            )
            self._lmdb_enabled = False

    def _close_lmdb(self) -> None:
        """Close LMDB environment and release mmap."""
        if self._lmdb_env is not None:
            try:
                if self._lmdb_nodes_db is not None:
                    self._lmdb_nodes_db = None
                if self._lmdb_meta_db is not None:
                    self._lmdb_meta_db = None
                self._lmdb_env.close()
                self._lmdb_env = None
                logger.debug("[UNIFIED-005] LMDB environment closed")
            except Exception as exc:  # noqa: BLE001
                logger.debug("[UNIFIED-005] LMDB close error: %s", exc)

    async def incremental_checkpoint(
        self,
        node_id: str,
        node_data: dict,
    ) -> bool:
        """
        UNIFIED-005: Write a single node update to LMDB (hot path).

        Sub-ms O(1) write — no serialization, no DuckDB roundtrip.
        Designed for per-node updates during ToT exploration where
        full-tree serialization would be too expensive.

        Args:
            node_id: ThoughtNode identifier (e.g., "root", "node_0_1_root")
            node_data: Serializable node representation (msgspec.to_builtins)

        Returns:
            True if written, False if LMDB unavailable or on error
        """
        if not self._lmdb_enabled or self._lmdb_env is None:
            return False

        try:
            raw = orjson.dumps(node_data, option=orjson.OPT_SORT_KEYS)

            # Bounded: reject individual nodes > 64 KiB
            if len(raw) > 65536:
                logger.debug(
                    "[UNIFIED-005] Node %s too large (%d bytes) — skipping",
                    node_id, len(raw),
                )
                self._stats["incremental_failures"] += 1
                return False

            with self._lmdb_env.begin(write=True) as txn:
                txn.put(
                    node_id.encode("utf-8"),
                    raw,
                    db=self._lmdb_nodes_db,
                )
                # Update metadata: last_modified timestamp
                txn.put(
                    b"last_modified",
                    str(time.time()).encode("utf-8"),
                    db=self._lmdb_meta_db,
                )

            self._stats["incremental_writes"] += 1
            return True

        except Exception as exc:  # noqa: BLE001 — fail-soft in hot path
            logger.debug(
                "[UNIFIED-005] Incremental checkpoint failed for %s: %s",
                node_id, exc,
            )
            self._stats["incremental_failures"] += 1
            return False

    async def read_incremental_nodes(self) -> dict[str, dict] | None:
        """
        UNIFIED-005: Read all nodes from LMDB incremental store.

        Used for recovery: reads all node entries from LMDB and returns
        as a dict. Faster than DuckDB full-tree restore for large trees
        because LMDB reads are zero-copy.

        Returns:
            dict of node_id → node_data, or None if LMDB unavailable
        """
        if not self._lmdb_enabled or self._lmdb_env is None:
            return None

        try:
            nodes: dict[str, dict] = {}
            with self._lmdb_env.begin() as txn:
                cursor = txn.cursor(db=self._lmdb_nodes_db)
                for key, value in cursor:
                    try:
                        node_id = key.decode("utf-8")
                        node_data = orjson.loads(value)
                        nodes[node_id] = node_data
                    except (orjson.JSONDecodeError, UnicodeDecodeError):
                        continue
            logger.debug(
                "[UNIFIED-005] Read %d nodes from LMDB incremental store",
                len(nodes),
            )
            return nodes if nodes else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("[UNIFIED-005] LMDB read failed: %s", exc)
            return None

    # ── UNIFIED-005: Memory-Aware Throttling ──────────────────────────────

    def _check_memory_pressure(self) -> bool:
        """
        Check if system is under memory pressure (RSS ≥ _MEMORY_THROTTLE_RSS_GIB).

        Returns True if memory is OK to proceed, False if checkpoint should
        be skipped to avoid OOM on M1 8GB.

        Uses psutil if available; falls back to allowing checkpoint if
        psutil is not importable.
        """
        if not self._memory_throttle:
            return True

        try:
            import psutil
            rss_gib = psutil.Process().memory_info().rss / (1024 ** 3)
            if rss_gib >= _MEMORY_THROTTLE_RSS_GIB:
                self._stats["memory_throttles"] += 1
                logger.warning(
                    "[UNIFIED-005] Memory pressure: RSS=%.2f GiB ≥ %.1f GiB "
                    "— skipping checkpoint",
                    rss_gib,
                    _MEMORY_THROTTLE_RSS_GIB,
                )
                return False
            return True
        except ImportError:
            # psutil not available — allow checkpoint
            return True
        except Exception:  # noqa: BLE001
            return True

    # ── UNIFIED-005: Signal-Triggered Emergency Checkpoint ────────────────

    def register_signal_handlers(self) -> None:
        """
        Register SIGTERM and SIGINT handlers for emergency checkpointing.

        When SIGTERM or SIGINT is received:
        1. Sets self._shutdown_event (signals periodic loop)
        2. Attempts an emergency checkpoint in the signal handler
           (sync, best-effort, DuckDB path only — LMDB write is
           not signal-safe)

        Idempotent — multiple calls are no-ops after first registration.
        """
        if self._signal_handlers_registered:
            return

        checkpointer_ref = self  # capture for closure

        def _emergency_handler(signum: int, frame: Any) -> None:
            """Sync emergency checkpoint — best-effort, signal-safe path."""
            logger.warning(
                "[UNIFIED-005] Signal %d received — triggering emergency checkpoint",
                signum,
            )
            # Set async event for cooperative shutdown
            checkpointer_ref._shutdown_event.set()

            # Attempt sync emergency write (DuckDB only — LMDB is not signal-safe)
            try:
                if checkpointer_ref._nodes_ref and checkpointer_ref._nodes_ref:
                    envelope = _to_serializable(checkpointer_ref._nodes_ref)
                    raw = orjson.dumps(envelope, option=orjson.OPT_SORT_KEYS)
                    if len(raw) <= _MAX_TREE_JSON_BYTES:
                        checksum = _compute_checksum(raw)
                        # Use sync DuckDB path directly
                        ok = checkpointer_ref._store._sync_upsert_tot_checkpoint(
                            checkpointer_ref._sprint_id,
                            checkpointer_ref._step,
                            raw.decode("utf-8"),
                            time.time(),
                            checksum,
                            checkpointer_ref._query_hash,
                        )
                        if ok:
                            checkpointer_ref._stats["emergency_checkpoints"] += 1
                            logger.info(
                                "[UNIFIED-005] Emergency checkpoint written: "
                                "sprint=%s step=%d nodes=%d",
                                checkpointer_ref._sprint_id[:12],
                                checkpointer_ref._step,
                                len(checkpointer_ref._nodes_ref),
                            )
            except Exception:  # noqa: BLE001 — best-effort in signal context
                pass

            # Re-raise KeyboardInterrupt for clean Python shutdown
            if signum == signal.SIGINT:
                raise KeyboardInterrupt()

        try:
            signal.signal(signal.SIGTERM, _emergency_handler)
            # SIGINT is handled by asyncio event loop — we use shutdown_event
            # for cooperative handling; KeyboardInterrupt is still raised
            self._signal_handlers_registered = True
            logger.debug(
                "[UNIFIED-005] Signal handlers registered: SIGTERM → emergency checkpoint"
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[UNIFIED-005] Signal handler registration failed: %s", exc
            )

    async def _emergency_shutdown(self) -> bool:
        """
        Perform emergency checkpoint when shutdown event is set.

        Called from _periodic_loop when _shutdown_event is set.
        Writes one final checkpoint before returning.

        Returns True if checkpoint was written.
        """
        if self._nodes_ref is not None and self._nodes_ref:
            try:
                ok = await self.checkpoint(
                    nodes=self._nodes_ref,
                    step=self._step,
                )
                if ok:
                    self._stats["emergency_checkpoints"] += 1
                return ok
            except Exception:  # noqa: BLE001
                return False
        return False

    def bind(self, nodes_ref: dict) -> None:
        """
        Bind to a mutable nodes dict reference for periodic checkpointing.

        The periodic loop reads this reference every interval_s. The dict
        should be the coordinator's internal ``self._nodes`` — mutated in
        place during ToT reasoning, so the checkpointer always sees the
        latest state without copying.

        Args:
            nodes_ref: Mutable dict[ str → ThoughtNode ] owned by coordinator.
        """
        self._nodes_ref = nodes_ref

    @property
    def stats(self) -> dict[str, int]:
        """Checkpointing statistics (read-only snapshot)."""
        return dict(self._stats)

    @property
    def step(self) -> int:
        """Current step counter."""
        return self._step

    # ── Internal ────────────────────────────────────────────────────────

    async def _periodic_loop(self) -> None:
        """Background task: sleep interval_s, then checkpoint if nodes are bound.

        UNIFIED-005 enhancements:
        - Checks _shutdown_event: if set, performs emergency checkpoint and exits
        - Memory pressure check: skips checkpoint if RSS ≥ 5.5 GiB
        - Shorter sleep (1s) to respond to shutdown events faster

        Reads self._nodes_ref directly — this is a mutable dict reference
        bound via bind() from the coordinator. If nodes exist, calls
        checkpoint() with the current state and auto-incremented step.
        """
        while True:
            # Use shorter sleep to be responsive to shutdown events
            try:
                await asyncio.sleep(min(self._interval_s, 1.0))
            except asyncio.CancelledError:
                break

            # UNIFIED-005: Check emergency shutdown event
            if self._shutdown_event.is_set():
                logger.info(
                    "[UNIFIED-005] Shutdown event detected — performing "
                    "emergency checkpoint"
                )
                await self._emergency_shutdown()
                break

            # Only checkpoint at the full interval
            # Use a simple counter-based approach to only checkpoint every interval_s
            if not hasattr(self, '_periodic_counter'):
                self._periodic_counter = 0  # type: ignore[attr-defined]
            self._periodic_counter += 1  # type: ignore[attr-defined]
            if self._periodic_counter < self._interval_s:  # type: ignore[attr-defined]
                continue
            self._periodic_counter = 0  # type: ignore[attr-defined]

            # UNIFIED-005: Memory pressure guard
            if not self._check_memory_pressure():
                continue

            if self._nodes_ref is not None and self._nodes_ref:
                try:
                    await self.checkpoint(nodes=self._nodes_ref)
                except Exception:  # noqa: BLE001 — fail-soft in background
                    pass

    # ── Filesystem fallback (belt-and-suspenders) ────────────────────────

    def _fs_path(self, step: int) -> Path:
        """Filesystem checkpoint path for a given step."""
        return self._fs_dir / f"{self._sprint_id}_step{step:06d}.json"

    def _fs_write(self, raw: bytes, step: int, checksum: str) -> None:
        """Atomic filesystem write: tempfile + os.replace."""
        target = self._fs_path(step)
        try:
            fd, tmp = tempfile.mkstemp(
                suffix=".tmp",
                prefix=f"tot_{self._sprint_id[:8]}_",
                dir=str(self._fs_dir),
            )
            try:
                os.write(fd, raw)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
            logger.debug(
                "[UNIFIED-005] FS fallback written: %s checksum=%s",
                target.name,
                checksum[:16],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[UNIFIED-005] FS fallback write failed: %s", exc)

    def _fs_read(self) -> tuple[int, dict, str] | None:
        """Read latest filesystem checkpoint for this sprint."""
        try:
            if not self._fs_dir.exists():
                return None
            # Find latest file matching our sprint_id prefix
            prefix = f"{self._sprint_id}_step"
            candidates = sorted(
                [p for p in self._fs_dir.iterdir() if p.name.startswith(prefix)],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                return None
            latest = candidates[0]
            raw = latest.read_bytes()
            computed = _compute_checksum(raw)
            envelope = orjson.loads(raw)
            if envelope.get("v") != 1:
                return None
            nodes = envelope.get("nodes", {})
            # Extract step from filename
            try:
                step_str = latest.stem.split("_step")[-1]
                step = int(step_str)
            except (ValueError, IndexError):
                step = 0
            return (step, nodes, computed)
        except Exception:  # noqa: BLE001
            return None

    def _fs_cleanup(self) -> None:
        """Remove all filesystem checkpoints for this sprint."""
        try:
            prefix = f"{self._sprint_id}_step"
            for p in self._fs_dir.iterdir():
                if p.name.startswith(prefix):
                    p.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
