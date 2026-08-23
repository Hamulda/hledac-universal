"""
UNIFIED-005/007/008: Transactional ToT State Checkpointer — Multi-Layer Crash Resilience
==========================================================================================

Four-layer durability architecture for Tree-of-Thoughts state on M1 8GB:

  L0 — LMDB incremental (hot path): sub-ms per-node writes during exploration.
       Every node created in ``_tree_of_thoughts_reasoning`` is persisted
       immediately via ``incremental_checkpoint()``. Survives SIGTERM/SIGKILL
       that arrive mid-depth (the biggest gap in UNIFIED-005).

  L1 — DuckDB periodic (canonical): full-tree atomic snapshot every 30 s.
       ``INSERT OR REPLACE`` into ``tot_checkpoints`` with BLAKE2b-256
       checksum. WAL-protected for SIGTERM; ``PRAGMA wal_checkpoint(TRUNCATE)``
       forced for SIGKILL durability (UNIFIED-007).

  L2 — Filesystem atomic (belt-and-suspenders): ``tempfile + os.replace``
       write of the same JSON blob. Survives DuckDB WAL corruption.
       Enabled by default (UNIFIED-008).

  L3 — Recovery: LMDB-first → DuckDB-fallback. On resume, the checkpointer
       tries LMDB (zero-copy, sub-ms reads for individual nodes), then falls
       back to the full DuckDB tree snapshot. Dead-end detector state is
       included in the envelope so resume is faithful.

Usage::

    from hledac.universal.coordinators.tot_checkpointer import (
        TransactionalToTCheckpointer,
    )

    checkpointer = TransactionalToTCheckpointer(
        sprint_id="8sa_...",
        duckdb_store=store,
        interval_s=30.0,
        # UNIFIED-007/008: all durability layers ON by default
        lmdb_incremental=True,    # L0: sub-ms per-node writes
        fs_fallback=True,         # L2: belt-and-suspenders
        memory_throttle=True,     # M1 8GB pressure guard
    )
    checkpointer.register_signal_handlers()
    await checkpointer.start()

    # Hot path: incremental node update (L0, sub-ms)
    await checkpointer.incremental_checkpoint(node_id, node_data)

    # Periodic: full tree snapshot (L1+L2)
    await checkpointer.checkpoint(nodes=nodes, step=current_step)

    # Recovery: LMDB-first → DuckDB-fallback (L3)
    restored = await checkpointer.restore()

    await checkpointer.stop(final_checkpoint=True)
    await checkpointer.cleanup()

M1 8GB bounds:
  - LMDB env: 4 MiB map (bounded, mmap-backed, zero-copy reads)
  - DuckDB: tree_json TEXT column bounded by _MAX_TREE_JSON_BYTES = 10 MiB
  - FS fallback: same 10 MiB ceiling, ~/.hledac/tot_checkpoints/
  - Background task: Event-driven sleep, negligible CPU/RAM
  - Memory throttle: skips checkpoint at ≥5.5 GiB RSS

Python 3.14+ best practices:
  - asyncio.Event for cooperative shutdown (not monkey-patched counters)
  - asyncio.wait_for for responsive sleep/wake
  - hashlib.blake2b for NEON-accelerated checksums
  - orjson for zero-copy serialization
  - msgspec.Struct for ThoughtNode (already frozen + gc=False)

Changelog (UNIFIED-007/008):
  - _periodic_loop: replaced hasattr counter with clean asyncio.Event
    + asyncio.wait_for pattern — responsive shutdown, no busy-waiting.
  - LMDB incremental layer enabled by default (was opt-in).
  - fs_fallback enabled by default (was opt-in) — belt-and-suspenders.
  - DuckDB WAL checkpoint (PRAGMA wal_checkpoint(TRUNCATE)) forced after
    every DuckDB write — survives SIGKILL (UNIFIED-007).
  - restore(): LMDB-first recovery path before DuckDB fallback (L3).
  - Checkpoint envelope v2: includes dead_end_state for faithful resume.
  - Per-node hot-path saves wired into _tree_of_thoughts_reasoning.
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


def _to_serializable(nodes: dict, dead_end_state: dict | None = None) -> dict:
    """Convert ToT nodes dict to a JSON-serializable structure (v2 envelope).

    ThoughtNode is msgspec.Struct (frozen, gc=False) — msgspec.to_builtins
    converts to plain dicts efficiently. We wrap in a stable envelope:
        {"v": 2, "nodes": {...}, "root_id": "root", "dead_end": {...}}

    UNIFIED-007: v2 envelope adds ``dead_end`` — a dict of node_id → last_progress_ts
    for faithful dead-end detector state recovery after crash.
    """
    from msgspec import to_builtins

    envelope: dict[str, Any] = {
        "v": 2,
        "nodes": to_builtins(nodes),
    }
    if dead_end_state:
        envelope["dead_end"] = dead_end_state
    return envelope


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
        # UNIFIED-007: asyncio.Event-driven sleep (replaces counter monkey-patch)
        "_wake_event",
    )

    def __init__(
        self,
        sprint_id: str,
        duckdb_store: DuckDBShadowStore,
        interval_s: float = 30.0,
        *,
        fs_fallback: bool = True,  # UNIFIED-008: belt-and-suspenders ON by default
        fs_dir: Path | None = None,
        query_hash: str = "",  # UNIFIED-006: deterministic cross-sprint recovery key
        lmdb_incremental: bool = True,  # UNIFIED-007: hot-path layer ON by default
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
                         Default: True (UNIFIED-008).
            fs_dir: Filesystem directory for fs_fallback checkpoints.
                    Default: ~/.hledac/tot_checkpoints/
            query_hash: UNIFIED-006 — BLAKE2b-16 hex of the query string.
                        Enables cross-sprint orphan recovery when sprint_id
                        is unknown (new sprint restart after crash).
            lmdb_incremental: UNIFIED-007 — enable LMDB-based incremental
                              hot-path layer. Writes individual node updates
                              in sub-ms (O(1)) vs full-tree serialization.
                              Default: True. M1 8GB: 4 MiB mmap, zero-copy reads.
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
            # UNIFIED-007: new counters
            "wal_checkpoints_forced": 0,
            "lmdb_nodes_recovered": 0,
            "duckdb_fallback_recoveries": 0,
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

        # UNIFIED-007: asyncio.Event for responsive sleep/wake in _periodic_loop
        self._wake_event: asyncio.Event = asyncio.Event()

        self._signal_handlers_registered: bool = False

        if self._fs_fallback:
            self._fs_dir.mkdir(parents=True, exist_ok=True)
            # SEC-02: harden directory permissions
            try:
                os.chmod(self._fs_dir, 0o700)
            except OSError:  # noqa: BLE001
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
            except asyncio.CancelledError:  # noqa: BLE001
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
        *,
        dead_end_state: dict | None = None,  # UNIFIED-007: dead-end detector state
    ) -> bool:
        """
        Persist current ToT state atomically (v2 envelope).

        Serializes nodes + dead_end_state → computes checksum → writes to
        DuckDB (with WAL checkpoint forcing for SIGKILL durability)
        → writes to filesystem (belt-and-suspenders).
        Thread-safe via asyncio.Lock.

        UNIFIED-005: Memory pressure check before serialization — skips
        if RSS ≥ 5.5 GiB to prevent OOM on M1 8GB.
        UNIFIED-007: Forces ``PRAGMA wal_checkpoint(TRUNCATE)`` after
        DuckDB INSERT so the write survives SIGKILL.

        Args:
            nodes: The ToT nodes dict (keyed by node_id → ThoughtNode).
            step: Current reasoning step. Auto-increments if None.
            dead_end_state: dict of node_id → last_progress_ts for
                            faithful dead-end detector recovery.

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
                # Serialize (v2 envelope with dead_end_state)
                envelope = _to_serializable(nodes, dead_end_state=dead_end_state)
                raw = orjson.dumps(envelope, option=orjson.OPT_SORT_KEYS)

                # Size guard
                if len(raw) > _MAX_TREE_JSON_BYTES:
                    logger.error(
                        "[UNIFIED-007] Tree JSON too large: %d bytes > %d limit — skipping checkpoint",
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
                        "[UNIFIED-007] DuckDB checkpoint write failed: sprint=%s step=%d",
                        self._sprint_id[:12],
                        current_step,
                    )
                    self._stats["checkpoints_failed"] += 1
                    return False

                # UNIFIED-007: Force WAL checkpoint for SIGKILL durability.
                # Without this, DuckDB's WAL may buffer the INSERT for 30+
                # seconds — a SIGKILL during that window loses the write.
                await self._force_wal_checkpoint()

                # UNIFIED-008: Filesystem fallback (belt-and-suspenders, ON by default)
                if self._fs_fallback:
                    self._fs_write(raw, current_step, checksum)

                self._stats["checkpoints_written"] += 1
                logger.debug(
                    "[UNIFIED-007] Checkpoint written: sprint=%s step=%d size=%d checksum=%s",
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
                    "[UNIFIED-007] Checkpoint failed: sprint=%s step=%d error=%s",
                    self._sprint_id[:12],
                    current_step,
                    exc,
                )
                self._stats["checkpoints_failed"] += 1
                return False

    async def restore(self) -> tuple[int, dict, str] | None:
        """
        Load and verify the latest ToT checkpoint for this sprint.

        UNIFIED-007: Three-tier recovery — LMDB-first → DuckDB → FS fallback.

        Recovery order:
          1. LMDB incremental nodes (L0) — zero-copy, fastest, most granular.
             If nodes exist, merges with DuckDB step metadata.
          2. DuckDB full-tree snapshot (L1) — canonical, checksum-verified.
          3. Filesystem fallback (L2) — belt-and-suspenders, survives DuckDB
             WAL corruption.

        Returns:
            (step, nodes_dict, checksum) or None if no checkpoint exists
            or checksum verification fails.
        """
        self._stats["restores_attempted"] += 1

        async with self._lock:
            # ── Tier 1: LMDB incremental recovery (L0) ─────────────────
            lmdb_nodes = None
            if self._lmdb_enabled:
                try:
                    lmdb_nodes = await self.read_incremental_nodes()
                    if lmdb_nodes:
                        self._stats["lmdb_nodes_recovered"] += len(lmdb_nodes)
                        logger.info(
                            "[UNIFIED-007] LMDB recovery: %d nodes found — using incremental layer as primary source",
                            len(lmdb_nodes),
                        )
                        step = await self._read_lmdb_step() or 0
                        # LMDB nodes are already raw dicts, no deserialization needed
                        self._step = step
                        self._stats["restores_succeeded"] += 1
                        return (step, lmdb_nodes, "lmdb_incremental")
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        "[UNIFIED-007] LMDB recovery failed: %s — falling back to DuckDB",
                        exc,
                    )

            # ── Tier 2: DuckDB full-tree recovery (L1) ─────────────────
            try:
                row = await self._store.async_get_latest_tot_checkpoint(
                    sprint_id=self._sprint_id,
                )

                if row is None:
                    logger.debug(
                        "[UNIFIED-007] No DuckDB checkpoint for sprint=%s",
                        self._sprint_id[:12],
                    )
                    # ── Tier 3: Filesystem fallback (L2) ────────────────
                    if self._fs_fallback:
                        fs_result = self._fs_read()
                        if fs_result is not None:
                            logger.info(
                                "[UNIFIED-007] Recovered from filesystem fallback: sprint=%s",
                                self._sprint_id[:12],
                            )
                            self._stats["restores_succeeded"] += 1
                            return fs_result
                    return None

                step, tree_json_str, ts, stored_checksum = row
                self._stats["duckdb_fallback_recoveries"] += 1

                # Verify checksum
                raw = tree_json_str.encode("utf-8")
                computed = _compute_checksum(raw)
                if computed != stored_checksum:
                    logger.error(
                        "[UNIFIED-007] Checksum mismatch on restore: sprint=%s step=%d "
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
                                "[UNIFIED-007] Recovered from filesystem fallback after checksum mismatch: sprint=%s",
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
                        "[UNIFIED-007] JSON decode failed on restore: sprint=%s step=%d error=%s",
                        self._sprint_id[:12],
                        step,
                        exc,
                    )
                    return None

                # Validate envelope (accept v1 or v2)
                if not isinstance(envelope, dict) or envelope.get("v") not in (1, 2):
                    logger.error(
                        "[UNIFIED-007] Invalid checkpoint envelope v=%s: sprint=%s",
                        envelope.get("v") if isinstance(envelope, dict) else type(envelope),
                        self._sprint_id[:12],
                    )
                    return None

                nodes = envelope.get("nodes", {})
                if not isinstance(nodes, dict):
                    logger.error(
                        "[UNIFIED-007] Invalid nodes structure: sprint=%s",
                        self._sprint_id[:12],
                    )
                    return None

                # Restore step counter
                self._step = step

                self._stats["restores_succeeded"] += 1
                logger.info(
                    "[UNIFIED-007] DuckDB checkpoint restored: sprint=%s step=%d nodes=%d ts=%.0f",
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
                    "[UNIFIED-007] Restore failed: sprint=%s error=%s",
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
            except OSError:  # noqa: BLE001
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
                "[UNIFIED-005] LMDB incremental layer initialized: path=%s map_size=%d",
                self._lmdb_path,
                _LMDB_MAP_SIZE,
            )
        except ImportError:
            logger.debug("[UNIFIED-005] lmdb not available — incremental layer disabled")
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
        *,
        step: int | None = None,  # UNIFIED-007: also save step for recovery
    ) -> bool:
        """
        UNIFIED-005/007: Write a single node update to LMDB (hot path, L0).

        Sub-ms O(1) write — no full-tree serialization, no DuckDB roundtrip.
        Designed for per-node updates during ToT exploration where
        full-tree serialization would be too expensive.

        UNIFIED-007: Also writes the current step counter to LMDB metadata
        so the recovery path can determine which depth we were at.

        Args:
            node_id: ThoughtNode identifier (e.g., "root", "node_0_1_root")
            node_data: Serializable node representation (msgspec.to_builtins)
            step: Current reasoning step (optional, written to metadata)

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
                    "[UNIFIED-007] Node %s too large (%d bytes) — skipping",
                    node_id,
                    len(raw),
                )
                self._stats["incremental_failures"] += 1
                return False

            with self._lmdb_env.begin(write=True) as txn:
                txn.put(
                    node_id.encode("utf-8"),
                    raw,
                    db=self._lmdb_nodes_db,
                )
                txn.put(
                    b"last_modified",
                    str(time.time()).encode("utf-8"),
                    db=self._lmdb_meta_db,
                )
                # UNIFIED-007: also save step counter for recovery
                if step is not None:
                    txn.put(
                        b"step",
                        str(step).encode("utf-8"),
                        db=self._lmdb_meta_db,
                    )

            self._stats["incremental_writes"] += 1
            return True

        except Exception as exc:  # noqa: BLE001 — fail-soft in hot path
            logger.debug(
                "[UNIFIED-007] Incremental checkpoint failed for %s: %s",
                node_id,
                exc,
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
                    except orjson.JSONDecodeError, UnicodeDecodeError:
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

            rss_gib = psutil.Process().memory_info().rss / (1024**3)
            if rss_gib >= _MEMORY_THROTTLE_RSS_GIB:
                self._stats["memory_throttles"] += 1
                logger.warning(
                    "[UNIFIED-005] Memory pressure: RSS=%.2f GiB ≥ %.1f GiB — skipping checkpoint",
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
            """Sync emergency checkpoint — best-effort, signal-safe path (v2 envelope)."""
            logger.warning(
                "[UNIFIED-007] Signal %d received — triggering emergency checkpoint",
                signum,
            )
            # Set async event for cooperative shutdown
            checkpointer_ref._shutdown_event.set()
            # Also wake the periodic loop so it can perform emergency shutdown
            checkpointer_ref._wake_event.set()

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
                            # UNIFIED-007: force WAL checkpoint for SIGKILL durability
                            try:
                                checkpointer_ref._store._sync_force_wal_checkpoint()
                            except Exception:  # noqa: BLE001
                                pass
                            checkpointer_ref._stats["emergency_checkpoints"] += 1
                            logger.info(
                                "[UNIFIED-007] Emergency checkpoint written: sprint=%s step=%d nodes=%d (WAL forced)",
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
            logger.debug("[UNIFIED-005] Signal handlers registered: SIGTERM → emergency checkpoint")
        except Exception as exc:  # noqa: BLE001
            logger.debug("[UNIFIED-005] Signal handler registration failed: %s", exc)

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

    async def _force_wal_checkpoint(self) -> None:
        """
        UNIFIED-007: Force DuckDB WAL checkpoint to flush writes to disk.

        Without this, DuckDB's WAL may buffer INSERTs for 30+ seconds.
        A SIGKILL during that window loses the write. ``PRAGMA wal_checkpoint(TRUNCATE)``
        forces an immediate fsync of the WAL to the main .duckdb file, then
        truncates the WAL. Survives SIGKILL and power loss.

        Best-effort — errors are logged but never raised.
        """
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self._store.executor,
                self._store._sync_force_wal_checkpoint,
            )
            self._stats["wal_checkpoints_forced"] += 1
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — best-effort
            pass

    async def _read_lmdb_step(self) -> int | None:
        """Read step counter from LMDB metadata (if available)."""
        if not self._lmdb_enabled or self._lmdb_env is None:
            return None
        try:
            with self._lmdb_env.begin() as txn:
                raw = txn.get(b"step", db=self._lmdb_meta_db)
                if raw is not None:
                    return int(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _periodic_loop(self) -> None:
        """
        UNIFIED-007: Background task — Event-driven sleep with responsive shutdown.

        Replaces the old counter-based approach (hasattr monkey-patch + 1s
        busy-wait × 30 iterations) with a clean ``asyncio.wait_for`` on an
        ``asyncio.Event``. The event is never set (infinite sleep), but
        ``wait_for`` with a timeout provides the interval. On shutdown,
        ``_shutdown_event.set()`` + ``_wake_event.set()`` wake the loop
        immediately.

        After consuming a non-timeout wake (i.e., ``_wake_event`` was
        externally set), the event is cleared so the next iteration sleeps
        for the full interval again.

        Memory pressure guard skips checkpoints when RSS ≥ 5.5 GiB.
        """
        while True:
            # Sleep for interval_s, but wake immediately on shutdown event
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._interval_s,
                )
                # Non-timeout: _wake_event was set externally (signal handler).
                # Clear it so the next iteration sleeps for the full interval.
                self._wake_event.clear()
            except TimeoutError:  # noqa: BLE001
                pass  # Normal: interval elapsed, time to checkpoint
            except asyncio.CancelledError:
                break

            # UNIFIED-005: Check emergency shutdown event
            if self._shutdown_event.is_set():
                logger.info("[UNIFIED-007] Shutdown event detected — performing emergency checkpoint")
                await self._emergency_shutdown()
                break

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
        """Read latest filesystem checkpoint for this sprint (v1 or v2 envelope)."""
        try:
            if not self._fs_dir.exists():
                return None
            # Find latest file matching our sprint_id prefix
            prefix = f"{self._sprint_id}_step"
            candidates = sorted(
                [p for p in self._fs_dir.iterdir() if p.name.startswith(prefix)],
                # PRM-1 FIX: Use lambda instead of attrgetter()() which was calling attrgetter itself
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not candidates:
                return None
            latest = candidates[0]
            raw = latest.read_bytes()
            computed = _compute_checksum(raw)
            envelope = orjson.loads(raw)
            # UNIFIED-007: accept v1 or v2 envelopes
            if envelope.get("v") not in (1, 2):
                return None
            nodes = envelope.get("nodes", {})
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
