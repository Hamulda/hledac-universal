# federated_qtable.py — Federated Q-Learning domain
"""
Rust-backed Federated Q-Learning table with rayon parallel batch updates.

Q(s,a) += alpha * (reward + gamma * max(Q(s',a')) - Q(s,a))

Features:
- parking_lot::RwLock — PyO3 GIL safe, no DashMap segfaults
- Rayon parallel batch update (adaptive threshold: 16/32/64 by memory pressure)
- Auto-eviction: every 100 updates when table is 50% full
- Atomic bincode persistence (2 MiB cap, rename(2) on Darwin)

Use in acquisition: source prioritization via Q-value argmax.
"""

from __future__ import annotations

from typing import Any


def get_domain() -> "FederatedQTableDomain":
    from hledac.universal.rust_extensions import hledac_rust_extensions as _ext

    _probe = getattr(_ext, "FederatedQLearning", None)
    if _probe is None:
        msg = "hledac_rust_extensions.FederatedQLearning not available"
        raise ImportError(msg)
    return FederatedQTableDomain(_ext)


class FederatedQTableDomain:
    """Rust-backed Q-learning table for acquisition source prioritization."""

    __slots__ = ("_cls", "_ext")

    def __init__(self, ext: Any) -> None:
        self._cls = ext
        self._ext = ext

    def from_config(
        self,
        *,
        alpha: float = 0.1,
        gamma: float = 0.9,
        max_entries: int = 3072,
    ) -> Any:
        """Create a new Q-table with given hyperparameters."""
        return self._cls(alpha, gamma, max_entries)

    def load(self, path: str) -> Any:
        """Load a Q-table from a bincode/JSON file."""
        inst = self._cls(0.1, 0.9, 3072)
        inst.load_from_file(path)
        return inst

    def update(
        self,
        inst: Any,
        lane: str,
        state_key: str,
        action: str,
        reward: float,
        next_state_key: str,
    ) -> None:
        """Single Q-learning update.

        Args:
            inst: FederatedQLearning instance
            lane: acquisition lane (e.g. "feeds", "ct", "public")
            state_key: source state descriptor
            action: action taken ("fetch_now", "defer_1h", "skip")
            reward: finding quality score
            next_state_key: resulting state after action
        """
        inst.update(lane, state_key, action, reward, next_state_key)

    def update_batch(
        self,
        inst: Any,
        items: list[tuple[str, str, str, float, str]],
    ) -> int:
        """Batch Q-learning update (rayon parallel when n >= adaptive threshold).

        Args:
            inst: FederatedQLearning instance
            items: list of (lane, state_key, action, reward, next_state_key)

        Returns:
            Number of entries updated.
        """
        return inst.update_batch(items)

    def get_q(
        self, inst: Any, lane: str, state_key: str, action: str
    ) -> float:
        """Get Q-value for (lane, state, action)."""
        return inst.get_q(lane, state_key, action)

    def get_best_action(
        self,
        inst: Any,
        lane: str,
        state_key: str,
        actions: list[str],
    ) -> str:
        """Argmax action selection (epsilon-greedy).

        Args:
            inst: FederatedQLearning instance
            lane: acquisition lane
            state_key: source state descriptor
            actions: candidate action names

        Returns:
            Action with highest Q-value (or first action if all Q=0).
        """
        return inst.get_best_action(lane, state_key, actions)

    def persist(self, inst: Any, path: str) -> bool:
        """Atomically persist Q-table to file via rename(2)."""
        return inst.persist_to_file(path)

    def len(self, inst: Any) -> int:
        """Current number of Q-entries."""
        return inst.len()

    def is_empty(self, inst: Any) -> bool:
        """True if Q-table has no entries."""
        return inst.is_empty()


# ---------------------------------------------------------------------------
# Module-level batch update (no instance needed)
# ---------------------------------------------------------------------------

def batch_update_module_level(
    items: list[tuple[str, str, str, float, str]],
) -> int:
    """Module-level rayon parallel batch update (no instance required).

    Uses shared MODULE_QTABLE with alpha=0.1, gamma=0.9.
    For use when you want batch updates without a persistent table instance.
    """
    from hledac.universal.rust_extensions import hledac_rust_extensions as _ext

    func = getattr(_ext, "rust_federated_qtable_batch_update", None)
    if func is None:
        msg = "rust_federated_qtable_batch_update not available"
        raise ImportError(msg)
    return func(items)


# ---------------------------------------------------------------------------
# Python fallback — pure Python Q-table
# ---------------------------------------------------------------------------

class PythonFallbackQTableDomain:
    """Pure-Python Q-table fallback when Rust is unavailable."""

    __slots__ = ("_table", "_alpha", "_gamma")

    def __init__(
        self,
        alpha: float = 0.1,
        gamma: float = 0.9,
        max_entries: int = 3072,
    ) -> None:
        self._table: dict[str, float] = {}
        self._alpha = alpha
        self._gamma = gamma
        self._max_entries = max_entries

    def from_config(self, **kwargs: Any) -> "PythonFallbackQTableDomain":
        return PythonFallbackQTableDomain(**kwargs)

    def load(self, path: str) -> "PythonFallbackQTableDomain":
        import json

        with open(path) as f:
            self._table = json.load(f)
        return self

    def _make_key(
        self, lane: str, state_key: str, action: str
    ) -> str:
        return f"{lane}::{state_key}|{action}"

    def update(
        self,
        lane: str,
        state_key: str,
        action: str,
        reward: float,
        next_state_key: str,
    ) -> None:
        key = self._make_key(lane, state_key, action)
        next_key_prefix = f"{lane}::{next_state_key}|"
        next_max = max(
            (v for k, v in self._table.items() if k.startswith(next_key_prefix)),
            default=0.0,
        )
        old = self._table.get(key, 0.0)
        self._table[key] = old + self._alpha * (
            reward + self._gamma * next_max - old
        )
        if len(self._table) > self._max_entries:
            # evict 10 lowest
            sorted_entries = sorted(self._table.items(), key=lambda x: x[1])
            for k, _ in sorted_entries[:10]:
                del self._table[k]

    def update_batch(
        self,
        items: list[tuple[str, str, str, float, str]],
    ) -> int:
        for item in items:
            self.update(*item)
        return len(items)

    def get_q(
        self, lane: str, state_key: str, action: str
    ) -> float:
        return self._table.get(self._make_key(lane, state_key, action), 0.0)

    def get_best_action(
        self, lane: str, state_key: str, actions: list[str]
    ) -> str:
        if not actions:
            return ""
        best = actions[0]
        best_q = self.get_q(lane, state_key, best)
        for a in actions[1:]:
            q = self.get_q(lane, state_key, a)
            if q > best_q:
                best_q = q
                best = a
        return best

    def persist(self, path: str) -> bool:
        import json

        with open(path, "w") as f:
            json.dump(self._table, f)
        return True

    def len(self) -> int:
        return len(self._table)

    def is_empty(self) -> bool:
        return len(self._table) == 0
