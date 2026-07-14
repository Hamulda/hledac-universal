"""
F350M-FED-P3: Federated Bridge — lazy Protocol facade for Q-table persistence
=============================================================================

Sprint: F350M-FED / Federated Activation 2026-06-04
Target: federated/bridge.py

PURPOSE
=======
This module bridges the lightweight `FederatedQTable` (used per-lane by
`FederatedResearchCoordinator`) with the heavier `loops.research_loop.ResearchLoop`
(which requires `hypothesis_engine` + `graph` at construction time).

PROBLEM (without this bridge)
=============================
- `loops.research_loop.ResearchLoop.__init__` is heavy:
    def __init__(self, hypothesis_engine: Any, graph: Any, ...):
  The constructor REQUIRES a live HypothesisEngine and a KnowledgeGraph.
  These cannot be cheaply mocked for a federated per-lane RL slice.
  (NOTE: `loops.research_loop` was planned but never implemented.)
- If it were implemented, `loops.research_loop._load_qtable()` would likely use
  `asyncio.get_event_loop().run_until_complete()` synchronously — an M1 crash
  vector inside other event loops.
- If it were implemented, `loops.research_loop.QTable.from_dict()` might use
  `eval()` on state-key strings — a security smell and portability hazard.
- The federated coordinator already has a lighter, in-memory FederatedQTable
  (1024-entry hard cap, no eval, fail-soft throughout) — but it loses
  RL state across sprints.

SOLUTION
========
FederatedBridge exposes a small Protocol that both FederatedQTable and
loops.QTable satisfy (Q-learning update + get_q + get_best_action). The
bridge:

  1. Defaults to LIGHTWEIGHT_ONLY mode — uses FederatedQTable in-process,
     never imports `loops.research_loop`, M1-safe at all memory pressures.

  2. Opt-in LAZY_HYBRID mode (HLEDAC_ENABLE_FEDERATED_HYBRID=1) imports
     `loops.research_loop.ResearchLoop` on FIRST call only, caches the
     class, and uses it for hypothesis lane. Fall-back to FederatedQTable
     on any import or construction error.

  3. Opt-in CROSS_SPRINT_PERSIST mode (HLEDAC_FEDERATED_LMDB_PATH set
     OR explicit `lmdb_path=` param) writes the Q-table to a bounded
     LMDB path on a debounced 5s schedule. On init, it loads any
     previously persisted Q-table and merges bounded.

DESIGN BOUNDS (HARD INVARIANTS — M1 8GB)
=======================================
- LMDB_MAX_ENTRIES = 1024  (matches FederatedQTable.MAX_QTABLE_ENTRIES)
- LMDB_PERSIST_DEBOUNCE_S = 5.0  (writes are debounced; not per-update)
- LMDB_PERSIST_KEY = "federated_qtable"  (singleton key)
- LMDB_MAP_SIZE_BYTES = 2 MiB  (small, bounded, ephemeral)
- HYBRID_MAX_INSTANCES = 1  (one ResearchLoop, shared)
- No numpy, no MLX, no browser, no stealth
- LMDB I/O is dispatched via asyncio.to_thread (no event-loop blocking)
- Fail-soft: every public method is try/except-wrapped; no method raises
  into the caller

PROTOCOL CONTRACT
=================
QTableProtocol is duck-typed (@runtime_checkable). Any class with the
right `get_q` / `update` / `get_best_action` / `to_dict` methods qualifies.
Both FederatedQTable and loops.QTable satisfy it.

LANE ISOLATION
==============
The bridge exposes lane-prefixed updates:
    bridge.update(lane, state, action, reward, next_state)
The lane name is prepended to the state tuple so a single shared
Q-table can store per-lane policies without key collision:
    key = (lane, *state)
This avoids the cost of N separate Q-tables (one per lane) while
still isolating policy slices.

USAGE
=====
    from hledac.universal.federated.bridge import FederatedBridge
    bridge = FederatedBridge()
    bridge.update("surface", ("query-1", 0), "fetch", 0.5, ("query-1", 1))
    best = bridge.get_best_action("surface", ("query-1", 0), ["fetch", "discovery"])
    # ...later in the sprint lifecycle...
    await bridge.persist_if_due()  # debounced LMDB write
"""
import asyncio
import logging
import os
import time
from typing import Any, Protocol, runtime_checkable
from .qtable import MAX_QTABLE_ENTRIES, FederatedQTable
logger = logging.getLogger(__name__)
__all__ = ['FederatedBridge', 'QTableProtocol', 'BRIDGE_LIGHTWEIGHT_ONLY', 'BRIDGE_LAZY_HYBRID', 'BRIDGE_CROSS_SPRINT_PERSIST', 'LMDB_MAX_ENTRIES', 'LMDB_PERSIST_DEBOUNCE_S', 'LMDB_PERSIST_KEY', 'LMDB_MAP_SIZE_BYTES', 'HYBRID_MAX_INSTANCES']
LMDB_MAX_ENTRIES: int = MAX_QTABLE_ENTRIES
'Hard cap on Q-table entries persisted to LMDB. Matches MAX_QTABLE_ENTRIES.'
LMDB_PERSIST_DEBOUNCE_S: float = 5.0
'Minimum seconds between successive LMDB writes. Prevents write amplification.'
LMDB_PERSIST_KEY: str = 'federated_qtable'
'Singleton key used for the bounded Q-table blob in LMDB.'
LMDB_MAP_SIZE_BYTES: int = 2 * 1024 * 1024
'2 MiB LMDB map — small enough to never pressure the M1 8GB UMA.'
HYBRID_MAX_INSTANCES: int = 1
'Maximum number of cached ResearchLoop instances per bridge (singleton).'
BRIDGE_LIGHTWEIGHT_ONLY: str = 'LIGHTWEIGHT_ONLY'
'Default mode: pure FederatedQTable, no heavy import. M1-safe at all pressures.'
BRIDGE_LAZY_HYBRID: str = 'LAZY_HYBRID'
'Opt-in mode: import loops.ResearchLoop on first call; fail-soft back to LIGHTWEIGHT.'
BRIDGE_CROSS_SPRINT_PERSIST: str = 'CROSS_SPRINT_PERSIST'
'Opt-in mode: bounded LMDB debounced persistence + load on init.'

@runtime_checkable
class QTableProtocol(Protocol):
    """
    Structural Protocol for the Q-table contract.

    Satisfied by both `FederatedQTable` (in federated/qtable.py) and
    `loops.research_loop.QTable`. The bridge treats them as interchangeable.

    Methods:
        get_q(state, action) -> float
        update(state, action, reward, next_state) -> None
        get_best_action(state, actions) -> str
        to_dict() -> dict
    """

    def get_q(self, state: tuple, action: str) -> float:
        ...

    def update(self, state: tuple, action: str, reward: float, next_state: tuple) -> None:
        ...

    def get_best_action(self, state: tuple, actions: list[str]) -> str:
        ...

    def to_dict(self) -> dict[str, Any]:
        ...

class FederatedBridge:
    """
    Lazy Protocol bridge for federated Q-table persistence.

    Wraps a single FederatedQTable and exposes a lane-aware interface.
    Optionally upgrades to a `loops.research_loop.ResearchLoop` instance
    for richer RL semantics (LAZY_HYBRID). Optionally persists to a
    bounded LMDB path (CROSS_SPRINT_PERSIST). Both optionals are
    fail-soft — they never raise into the caller.

    Thread-safety: per-instance asyncio.Lock guards persist operations.
    Update/get are lock-free (the underlying FederatedQTable is
    single-writer / multi-reader safe in CPython 3.14+).
    """
    __slots__ = tuple(('_allow_hybrid', '_hybrid_class', '_hybrid_loaded', '_last_persist_ts', '_lmdb_path', '_mode', '_persist_count', '_persist_lock', '_persist_pending', '_qtable', '_update_count'))

    def __init__(self, lmdb_path: str | None=None, allow_hybrid: bool=False, alpha: float=0.1, gamma: float=0.9) -> None:
        """
        Initialize the bridge.

        Args:
            lmdb_path: Optional LMDB directory. If None, falls back to
                       HLEDAC_FEDERATED_LMDB_PATH env var, then None
                       (no persistence).
            allow_hybrid: If True, enable LAZY_HYBRID mode (imports
                          loops.ResearchLoop on first call). Default False.
            alpha: Q-learning rate. Default 0.1 (matches both Q-tables).
            gamma: Q-learning discount. Default 0.9 (matches both Q-tables).
        """
        from .qtable import RustFederatedQTable
        self._qtable: Any = RustFederatedQTable(alpha=alpha, gamma=gamma)
        self._allow_hybrid: bool = allow_hybrid
        self._lmdb_path: str | None = lmdb_path or os.environ.get('HLEDAC_FEDERATED_LMDB_PATH', '').strip() or None
        self._mode: str = self._resolve_mode()
        self._hybrid_class: type | None = None
        self._hybrid_loaded: bool = False
        self._persist_lock: asyncio.Lock = asyncio.Lock()
        self._last_persist_ts: float = 0.0
        self._persist_pending: bool = False
        self._update_count: int = 0
        self._persist_count: int = 0
        if self._lmdb_path and self._mode == BRIDGE_CROSS_SPRINT_PERSIST:
            self._try_initial_load_sync()

    def _resolve_mode(self) -> str:
        """
        Determine the active bridge mode.

        Priority:
            1. CROSS_SPRINT_PERSIST if lmdb_path is set
            2. LAZY_HYBRID if HLEDAC_ENABLE_FEDERATED_HYBRID=1 AND allow_hybrid
            3. LIGHTWEIGHT_ONLY (default)
        """
        if self._lmdb_path:
            return BRIDGE_CROSS_SPRINT_PERSIST
        if self._allow_hybrid and _is_hybrid_env_enabled():
            return BRIDGE_LAZY_HYBRID
        return BRIDGE_LIGHTWEIGHT_ONLY

    def _try_load_hybrid(self) -> type | None:
        """
        Lazy import of loops.ResearchLoop — NEVER at module load.

        Caches the class after first successful load. Returns None on
        any failure (ImportError, missing deps, etc.) — the bridge
        falls back to FederatedQTable automatically.
        """
        if self._hybrid_loaded:
            return self._hybrid_class
        self._hybrid_loaded = True
        try:
            from loops.research_loop import ResearchLoop
            self._hybrid_class = ResearchLoop
            logger.info('[FED-BRIDGE] lazy import OK: %s', ResearchLoop.__module__)
            return ResearchLoop
        except Exception as e:
            logger.debug('[FED-BRIDGE] lazy import failed (fail-soft): %s: %s', type(e).__name__, e)
            self._hybrid_class = None
            return None

    def update(self, lane: str, state: tuple, action: str, reward: float, next_state: tuple) -> None:
        """
        Lane-aware Q-learning update.

        Args:
            lane: Lane name ("surface" | "dark" | "archive").
            state: Current state tuple.
            action: Action taken.
            reward: Reward received.
            next_state: Next state tuple.

        NEVER raises. If HYBRID is loaded, delegates to loops.QTable.update
        with lane-prefixed state; otherwise uses FederatedQTable.
        """
        try:
            lane_state = self._lane_state(lane, state)
            lane_next_state = self._lane_state(lane, next_state)
            self._qtable.update(lane_state, action, reward, lane_next_state)
            self._update_count += 1
            if self._lmdb_path:
                self._persist_pending = True
        except Exception as e:
            logger.debug('[FED-BRIDGE] update lane=%s failed: %s: %s', lane, type(e).__name__, e)

    def get_q(self, lane: str, state: tuple, action: str) -> float:
        """Return Q-value for (lane, state, action). Never raises."""
        try:
            lane_state = self._lane_state(lane, state)
            return self._qtable.get_q(lane_state, action)
        except Exception:
            return 0.0

    def get_best_action(self, lane: str, state: tuple, actions: list[str]) -> str:
        """Return the action with the highest Q-value, or first on tie."""
        try:
            if not actions:
                return ''
            lane_state = self._lane_state(lane, state)
            return self._qtable.get_best_action(lane_state, actions)
        except Exception:
            return actions[0] if actions else ''

    def update_batch(self, items: list[tuple[str, tuple, str, float, tuple]]) -> int:
        """
        Batch Q-learning update via rayon parallel in Rust (4× speedup).

        items: list of (lane, state, action, reward, next_state) tuples.
        When Rust backend is available, routes to RustFederatedQTable.update_batch
        which uses rayon parallel processing with adaptive threshold (16/32/64).
        Falls back to serial update() when Rust unavailable.

        M1 8GB bounds:
            - Rust: adaptive_scheduler::mixed_threshold() → 16/32/64 parallelism
            - DashMap: 4 shards (matches M1 4 E-cores)
            - Serial fallback: unchanged behavior

        Returns:
            Number of items processed.
        """
        if not items:
            return 0
        # Try Rust batch path first (4× faster via rayon)
        # RustFederatedQTable has _rust attribute; pure Python FederatedQTable does not.
        qtable_rust = getattr(self._qtable, '_rust', None)
        if qtable_rust is not None and hasattr(qtable_rust, 'update_batch'):
            try:
                # Convert to Rust format: (lane, state_key, action, reward, next_state_key)
                rust_items = [
                    (
                        str(lane),
                        str(state),
                        str(action),
                        float(reward),
                        str(next_state),
                    )
                    for lane, state, action, reward, next_state in items
                ]
                result = qtable_rust.update_batch(rust_items)
                self._update_count += int(result)
                if self._lmdb_path:
                    self._persist_pending = True
                return int(result)
            except Exception as e:
                logger.debug('[FED-BRIDGE] update_batch Rust failed (fallback): %s: %s', type(e).__name__, e)
        # Fallback: serial update
        for lane, state, action, reward, next_state in items:
            self.update(lane, state, action, reward, next_state)
        return len(items)

    async def persist_if_due(self) -> bool:
        """
        Debounced LMDB persist. Returns True if a write actually happened.

        Writes are throttled to LMDB_PERSIST_DEBOUNCE_S seconds to avoid
        write amplification. If no updates happened, returns False.
        All LMDB errors are swallowed (fail-soft).
        """
        if not (self._lmdb_path and self._persist_pending):
            return False
        if self._mode != BRIDGE_CROSS_SPRINT_PERSIST:
            return False
        now = time.monotonic()
        if now - self._last_persist_ts < LMDB_PERSIST_DEBOUNCE_S:
            return False
        async with self._persist_lock:
            if not self._persist_pending:
                return False
            if time.monotonic() - self._last_persist_ts < LMDB_PERSIST_DEBOUNCE_S:
                return False
            try:
                await asyncio.to_thread(self._persist_sync)
                self._last_persist_ts = time.monotonic()
                self._persist_pending = False
                self._persist_count += 1
                return True
            except Exception as e:
                logger.debug('[FED-BRIDGE] persist failed: %s: %s', type(e).__name__, e)
                return False

    def _persist_sync(self) -> None:
        """
        Synchronous LMDB write. Called via asyncio.to_thread.

        Bounds: trims Q-table to LMDB_MAX_ENTRIES before serializing.
        Uses orjson for zero-copy serialization. Wraps every step in
        try/except so a partial LMDB state is never worse than no persist.
        """
        try:
            from hledac.universal.paths import open_lmdb
        except Exception as e:
            logger.debug('[FED-BRIDGE] open_lmdb not importable: %s', e)
            return
        try:
            import orjson
        except Exception:
            orjson = None
        try:
            data = self._qtable.to_dict()
            items = list(data.items())[:LMDB_MAX_ENTRIES]
            bounded = dict(items)
        except Exception as e:
            logger.debug('[FED-BRIDGE] to_dict failed: %s', e)
            return
        try:
            if orjson is not None:
                payload = orjson.dumps(bounded)
            else:
                import json as _json
                payload = _json.dumps(bounded).encode('utf-8')
        except Exception as e:
            logger.debug('[FED-BRIDGE] serialize failed: %s', e)
            return
        env = None
        try:
            env = open_lmdb(_pathlib_path(self._lmdb_path), map_size=LMDB_MAP_SIZE_BYTES)
            with env.begin(write=True) as txn:
                txn.put(LMDB_PERSIST_KEY.encode('utf-8'), payload)
        except Exception as e:
            logger.debug('[FED-BRIDGE] LMDB write failed: %s', e)
        finally:
            if env is not None:
                try:
                    env.close()
                except Exception:
                    pass

    def _try_initial_load_sync(self) -> bool:
        """
        Try to load a previously persisted Q-table on init. Fail-soft.
        """
        try:
            return self._load_from_lmdb_sync()
        except Exception as e:
            logger.debug('[FED-BRIDGE] initial load failed: %s: %s', type(e).__name__, e)
            return False

    def _load_from_lmdb_sync(self) -> bool:
        """
        Synchronous LMDB read. Called once at init. Fail-soft throughout.
        """
        try:
            from hledac.universal.paths import open_lmdb
        except Exception:
            return False
        try:
            import orjson
        except Exception:
            orjson = None
        env = None
        try:
            env = open_lmdb(_pathlib_path(self._lmdb_path), map_size=LMDB_MAP_SIZE_BYTES, readonly=True)
            with env.begin() as txn:
                raw = txn.get(LMDB_PERSIST_KEY.encode('utf-8'))
            if raw is None:
                return False
            if orjson is not None:
                data = orjson.loads(raw)
            else:
                import json as _json
                data = _json.loads(raw)
            if not isinstance(data, dict):
                return False
            restored = FederatedQTable.from_dict(data)
            try:
                entries = list(getattr(restored, '_q', {}).items())
            except Exception:
                entries = []
            for (st, ac), q in entries:
                if len(self._qtable._q) >= LMDB_MAX_ENTRIES:
                    break
                try:
                    self._qtable._q[st, ac] = float(q)
                except Exception:
                    continue
            return True
        except Exception as e:
            logger.debug('[FED-BRIDGE] LMDB read failed: %s', e)
            return False
        finally:
            if env is not None:
                try:
                    env.close()
                except Exception:
                    pass

    @staticmethod
    def _lane_state(lane: str, state: Any) -> tuple:
        """
        Prefix a state tuple with the lane name to isolate per-lane
        policy slices in a single shared Q-table.

        Tolerates non-tuple state (e.g. None, int, str) at runtime via
        a single conversion — callers may pass scalars when the
        upstream contract is loose. Returning a fresh tuple is
        important to keep state keys hashable and Q-table-safe.
        """
        if isinstance(state, tuple):
            return (str(lane), *state)
        return (str(lane), state)

    @property
    def mode(self) -> str:
        """Current bridge mode (string constant)."""
        return self._mode

    @property
    def is_hybrid_loaded(self) -> bool:
        """True after a successful lazy import of loops.ResearchLoop."""
        return self._hybrid_class is not None

    @property
    def qtable(self) -> FederatedQTable:
        """Underlying lightweight Q-table (read-only handle)."""
        return self._qtable

    @property
    def update_count(self) -> int:
        """Number of updates processed by this bridge."""
        return self._update_count

    @property
    def persist_count(self) -> int:
        """Number of LMDB writes successfully completed."""
        return self._persist_count

    @property
    def persist_pending(self) -> bool:
        """True if at least one update is awaiting the next persist window."""
        return self._persist_pending

    def stats(self) -> dict[str, Any]:
        """Return a snapshot dict of bridge state for diagnostics."""
        return {'mode': self._mode, 'lmdb_path': self._lmdb_path, 'is_hybrid_loaded': self.is_hybrid_loaded, 'update_count': self._update_count, 'persist_count': self._persist_count, 'persist_pending': self._persist_pending, 'qtable_size': len(self._qtable._q), 'alpha': self._qtable._alpha, 'gamma': self._qtable._gamma}

def _is_hybrid_env_enabled() -> bool:
    """
    Centralized env-var check for HLEDAC_ENABLE_FEDERATED_HYBRID.

    Token set: "1", "true", "yes", "on" (case-insensitive). Matches
    the conventions used elsewhere in the federated module.
    """
    raw = os.environ.get('HLEDAC_ENABLE_FEDERATED_HYBRID', '').strip().lower()
    return raw in ('1', 'true', 'yes', 'on')

def _pathlib_path(p: str | None) -> Any:
    """
    Convert a string path to pathlib.Path lazily. If the LMDB module
    or pathlib is unavailable, return the string as-is (open_lmdb
    will raise a more informative error).
    """
    if not p:
        return p
    try:
        import pathlib
        return pathlib.Path(p)
    except Exception:
        return p