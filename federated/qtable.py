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
__all__ = ['FederatedQTable']
MAX_QTABLE_ENTRIES: int = 1024
'Hard cap on state-action pairs. Past this, lowest-Q entries are evicted.'

class FederatedQTable:
    """
    Bounded in-memory Q-table for the federated coordinator.

    State is a hashable tuple, action is a string. Each (state, action)
    pair has a single Q-value. When MAX_QTABLE_ENTRIES is exceeded,
    the entry with the lowest Q-value is evicted.
    """
    __slots__ = tuple(('_alpha', '_gamma', '_max_entries', '_q'))

    def __init__(self, alpha: float=0.1, gamma: float=0.9, max_entries: int=MAX_QTABLE_ENTRIES) -> None:
        self._alpha: float = float(alpha)
        self._gamma: float = float(gamma)
        self._max_entries: int = max(1, int(max_entries))
        self._q: dict[tuple[Any, str], float] = {}

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
        Bounded: evicts the lowest-Q entry when MAX_QTABLE_ENTRIES is exceeded.
        """
        try:
            key = (state, action)
            current_q = self._q.get(key, 0.0)
            next_max_q = 0.0
            for (st, _), q in self._q.items():
                if st == next_state and q > next_max_q:
                    next_max_q = q
            target = float(reward) + self._gamma * next_max_q
            new_q = current_q + self._alpha * (target - current_q)
            self._q[key] = new_q
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
            self._q.pop(min_key, None)
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
            return qt
        except Exception:
            return cls(alpha=alpha, gamma=gamma, max_entries=max_entries)

    def __len__(self) -> int:
        return len(self._q)