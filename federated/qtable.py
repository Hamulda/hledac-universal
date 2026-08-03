"""
F350M-FED: Lightweight in-memory FederatedQTable.

Sprint: F350M-FED / Federated Activation 2026-06-04
Target: federated/qtable.py

PURPOSE
=======
Per-lane RL slice used by FederatedResearchCoordinator. Deliberately
NOT the loops.research_loop.QTable — that one is coupled to a memory_manager
for LMDB persistence and is sized for full research sessions. The
federated QTable is a tiny in-memory policy slice that lives only for
the coordinator's lifetime.

DESIGN BOUNDS (HARD INVARIANTS — M1 8GB)
=======================================
- MAX_QTABLE_ENTRIES = 1024  (hard cap on state-action pairs)
- alpha, gamma: Q-learning parameters, fixed defaults
- Pure Python, no numpy, no MLX
- No LMDB persistence (intentional — the federated pattern is per-coordinator)

FAIL-SOFT
=========
All methods return safe defaults on any error. They never raise.
"""
import logging
from typing import Any
logger = logging.getLogger(__name__)
__all__ = ['FederatedQTable', 'RustFederatedQTable', 'MAX_QTABLE_ENTRIES']
MAX_QTABLE_ENTRIES: int = 1024
'Hard cap on state-action pairs. Past this, lowest-Q entries are evicted.'

class FederatedQTable:
    """
    Bounded in-memory Q-table for the federated coordinator.

    State is a hashable tuple, action is a string. Each (state, action)
    pair has a single Q-value. When MAX_QTABLE_ENTRIES is exceeded,
    the entry with the lowest Q-value is evicted.

    ISSUE 4.4 D-24 FIX: Secondary max-Q index dict[state, float] for O(1)
    next_max_q lookup instead of O(n) scan. Update is O(1) amortized.
    """
    __slots__ = tuple(('_alpha', '_gamma', '_max_entries', '_q', '_max_q_per_state'))

    def __init__(self, alpha: float=0.1, gamma: float=0.9, max_entries: int=MAX_QTABLE_ENTRIES) -> None:
        self._alpha: float = float(alpha)
        self._gamma: float = float(gamma)
        self._max_entries: int = max(1, int(max_entries))
        self._q: dict[tuple[Any, str], float] = {}
        # ISSUE 4.4 D-24 FIX: secondary index for O(1) max-Q per state
        self._max_q_per_state: dict[tuple, float] = {}

    def get_q(self, state: tuple, action: str) -> float:
        """Return Q(state, action), or 0.0 if unseen. Never raises."""
        try:
            return self._q.get((state, action), 0.0)
        except Exception as e:
            logger.debug(f'[FED-Q] get_q failed: {e}')
            return 0.0

    def get_best_action(self, state: tuple, actions: list[str]) -> str:
        """Return the action with the highest Q-value, or the first action if all zero."""
        if not actions:
            return ''
        try:
            best_action = actions[0]
            best_q = self.get_q(state, best_action)
            for a in actions[1:]:
                q = self.get_q(state, a)
                if q > best_q:
                    best_q = q
                    best_action = a
            return best_action
        except Exception as e:
            logger.debug(f'[FED-Q] get_best_action failed: {e}')
            return actions[0] if actions else ''

    def update(self, state: tuple, action: str, reward: float, next_state: tuple) -> None:
        """
        Q-learning update. Q(s,a) <- Q(s,a) + alpha * (reward + gamma*max_a' Q(s',a') - Q(s,a))

        ISSUE 4.4 D-24 FIX: O(1) next_max_q via _max_q_per_state index
        instead of O(n) scan over all entries.
        Bounded: evicts the lowest-Q entry when MAX_QTABLE_ENTRIES is exceeded.
        """
        try:
            key = (state, action)
            current_q = self._q.get(key, 0.0)
            # ISSUE 4.4 D-24 FIX: O(1) lookup via secondary index
            next_max_q = self._max_q_per_state.get(next_state, 0.0)
            target = float(reward) + self._gamma * next_max_q
            new_q = current_q + self._alpha * (target - current_q)
            self._q[key] = new_q
            # ISSUE 4.4 D-24 FIX: update secondary max-Q index
            if new_q > self._max_q_per_state.get(state, 0.0):
                self._max_q_per_state[state] = new_q
            if len(self._q) > self._max_entries:
                self._evict_lowest()
        except Exception as e:
            logger.debug(f'[FED-Q] update failed: {e}')

    def _evict_lowest(self) -> None:
        """Evict the entry with the lowest Q-value. Ties broken by insertion order."""
        if not self._q:
            return
        try:
            min_key = min(self._q, key=lambda k: self._q[k])
            evicted_state = min_key[0]
            self._q.pop(min_key, None)
            # ISSUE 4.4 D-24 FIX: rebuild max-Q index for evicted state if needed
            if evicted_state in self._max_q_per_state:
                remaining_qs = [q for (s, _), q in self._q.items() if s == evicted_state]
                if remaining_qs:
                    self._max_q_per_state[evicted_state] = max(remaining_qs)
                else:
                    self._max_q_per_state.pop(evicted_state, None)
        except Exception as e:
            logger.debug(f'[FED-Q] evict failed: {e}')

    def to_dict(self) -> dict[str, float]:
        """Serialize to a JSON-safe dict (keys as 'state|action' strings)."""
        try:
            return {f'{state}|{action}': q for (state, action), q in self._q.items()}
        except Exception:
            return {}

    @classmethod
    def from_dict(cls, data: dict[str, float], alpha: float=0.1, gamma: float=0.9, max_entries: int=MAX_QTABLE_ENTRIES) -> FederatedQTable:
        """Deserialize from the to_dict() format. Best-effort, never raises."""
        try:
            qt = cls(alpha=alpha, gamma=gamma, max_entries=max_entries)
            for k, v in (data or {}).items():
                if '|' in k:
                    state_str, action = k.rsplit('|', 1)
                    state = (state_str,)
                    qt._q[state, action] = float(v)
            # ISSUE 4.4 D-24 FIX: rebuild max-Q index after loading
            for (state, _), q in qt._q.items():
                if q > qt._max_q_per_state.get(state, 0.0):
                    qt._max_q_per_state[state] = q
            return qt
        except Exception:
            return cls(alpha=alpha, gamma=gamma, max_entries=max_entries)

    def __len__(self) -> int:
        return len(self._q)

    def reset(self) -> None:
        """Reset all state including max-Q index."""
        self._q.clear()
        self._max_q_per_state.clear()


# --- ISSUE-23: Rust-backed Q-table with rayon parallel batch updates ---
_rust_qtable_class: Any = None


def _get_rust_qtable() -> Any | None:
    """
    Lazy import of RustFederatedQTable from hledac_rust_extensions.

    Returns None if:
    - hledac_rust_extensions not installed
    - RustFederatedQTable not available (PyO3 build failed)
    - Any ImportError

    This is the canonical opt-in path for Issue #23.
    """
    global _rust_qtable_class
    if _rust_qtable_class is not None:
        return _rust_qtable_class
    try:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal.core.rust_backend import rust
        _cls = rust.raw.RustFederatedQTable  # type: ignore[assignment]
        _rust_qtable_class = _cls
        return _cls
    except Exception:
        return None


class RustFederatedQTable:
    """
    Python shim for RustFederatedQTable — transparent fallback to pure-Python
    FederatedQTable when the Rust extension is unavailable.

    API is identical to FederatedQTable but routes all Q-learning updates
    through Rust's rayon-parallel batch path when available. Lane isolation
    is handled inside Rust.

    Usage:
        qtable = RustFederatedQTable(alpha=0.1, gamma=0.9)
        qtable.update("surface", ("query-1", 0), "fetch", 0.5, ("query-1", 1))
        best = qtable.get_best_action("surface", ("query-1", 0), ["fetch", "discovery"])

    M1 8GB bounds:
        - max_entries = 1024 per lane (hard cap)
        - Rust batch update uses adaptive_scheduler::mixed_threshold()
          for rayon thread count (1-4 threads)
        - Persistence: bincode file (2 MiB cap), or JSON fallback
    """

    __slots__ = tuple(
        ('_rust', '_python', '_alpha', '_gamma', '_max_entries')
    )

    def __init__(
        self,
        alpha: float = 0.1,
        gamma: float = 0.9,
        max_entries: int = MAX_QTABLE_ENTRIES,
    ) -> None:
        rust_cls = _get_rust_qtable()
        if rust_cls is not None:
            self._rust: Any = rust_cls(alpha=alpha, gamma=gamma, max_entries=max_entries)
            self._python: Any = None
        else:
            # Transparent fallback to pure-Python
            self._rust: Any = None
            self._python: FederatedQTable = FederatedQTable(
                alpha=alpha, gamma=gamma, max_entries=max_entries
            )
        self._alpha: float = float(alpha)
        self._gamma: float = float(gamma)
        self._max_entries: int = max_entries

    @property
    def is_rust(self) -> bool:
        """True if Rust backend is active."""
        return self._rust is not None

    def get_q(self, state: tuple, action: str) -> float:
        """Return Q(state, action), or 0.0 if unseen. Never raises."""
        if self._rust is not None:
            try:
                # FederatedBridge.update pre-embeds lane in state tuple.
                # We pass the full state_key (which contains lane) as-is.
                state_key = str(state)
                action_key = str(action)
                return float(self._rust.get_q(state_key, action_key))
            except Exception:
                pass
        return self._python.get_q(state, action)

    def get_best_action(self, state: tuple, actions: list[str]) -> str:
        """Return the action with the highest Q-value, or first on tie."""
        if self._rust is not None:
            try:
                state_key = str(state)
                return str(self._rust.get_best_action(state_key, actions))
            except Exception:
                pass
        return self._python.get_best_action(state, actions)

    def update(
        self, state: tuple, action: str, reward: float, next_state: tuple
    ) -> None:
        """
        Q-learning update. Routes to Rust batch path if available.

        Note: FederatedBridge.update already embeds lane in the state tuple
        via _lane_state(), so state here already contains lane isolation.
        We pass state_key (str of full tuple) and action as-is to Rust.
        """
        if self._rust is not None:
            try:
                state_key = str(state)
                next_key = str(next_state)
                action_key = str(action)
                self._rust.update(state_key, action_key, float(reward), next_key)
                return
            except Exception:
                pass
        self._python.update(state, action, reward, next_state)

    def update_batch(
        self,
        items: list[tuple[str, tuple, str, float, tuple]],
    ) -> int:
        """
        Batch Q-learning update via rayon parallel in Rust.

        items: list of (lane, state, action, reward, next_state)
        Returns number of items processed.
        """
        if self._rust is not None and items:
            try:
                # Convert to Rust format: (lane, state_key, action, reward, next_state_key)
                rust_items = [
                    (str(lane), str(state), str(action), float(reward), str(next_state))
                    for lane, state, action, reward, next_state in items
                ]
                return int(self._rust.update_batch(rust_items))
            except Exception:
                pass
        # Fallback: serial Python
        for _lane, state, action, reward, next_state in items:
            self.update(state, action, reward, next_state)
        return len(items)

    def to_dict(self) -> dict[str, float]:
        """Serialize to JSON-safe dict."""
        if self._rust is not None:
            try:
                return dict(self._rust.to_dict())
            except Exception:
                pass
        return self._python.to_dict()

    def persist_to_file(self, path: str) -> bool:
        """Persist to bincode file. Returns True on success."""
        if self._rust is not None:
            try:
                return bool(self._rust.persist_to_file(path))
            except Exception:
                pass
        # Python fallback: serialize to dict then write JSON
        try:
            import os
            os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
            import json
            with open(path, 'w') as f:
                json.dump(self.to_dict(), f)
            return True
        except Exception:
            return False

    def load_from_file(self, path: str) -> bool:
        """Load from bincode or JSON file. Returns True on success."""
        if self._rust is not None:
            try:
                return bool(self._rust.load_from_file(path))
            except Exception:
                pass
        # Python fallback
        try:
            import json
            with open(path) as f:
                data = json.load(f)
            restored = FederatedQTable.from_dict(data, alpha=self._alpha, gamma=self._gamma)
            self._python = restored
            return True
        except Exception:
            return False

    def __len__(self) -> int:
        if self._rust is not None:
            try:
                return int(self._rust.len())
            except Exception:
                pass
        return len(self._python)

    def is_empty(self) -> bool:
        """Return True if the Q-table has no entries."""
        if self._rust is not None:
            try:
                return bool(self._rust.is_empty())
            except Exception:
                pass
        return len(self._python) == 0