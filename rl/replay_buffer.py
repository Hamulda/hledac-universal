"""
Replay buffer pro MARL s ukládáním do numpy polí (bezpečné, serializovatelné).
"""

from __future__ import annotations

import numpy as np
from pathlib import Path
from typing import Dict

# Lazy MLX accessor — defer mlx.core to first use (M1 8GB import-time savings)
_mlx_core_mod = None
_MLX_CORE_AVAILABLE = False


def _get_mlx_core():
    """Lazily import mlx.core, returning None if unavailable."""
    global _mlx_core_mod, _MLX_CORE_AVAILABLE
    if _mlx_core_mod is None:
        try:
            import mlx.core as _mlx_core_mod
            _MLX_CORE_AVAILABLE = True
        except ImportError:
            _mlx_core_mod = None
            _MLX_CORE_AVAILABLE = False
    return _mlx_core_mod


class MARLReplayBuffer:
    def __init__(self, capacity: int = 50000, state_dim: int = 12, n_agents: int = 5):
        self.capacity = capacity
        self.state_dim = state_dim
        self.n_agents = n_agents
        self.states = np.zeros((capacity, state_dim), dtype=np.float32)
        self.actions = np.zeros((capacity, n_agents), dtype=np.int32)
        self.rewards = np.zeros(capacity, dtype=np.float32)
        self.next_states = np.zeros((capacity, state_dim), dtype=np.float32)
        self.dones = np.zeros(capacity, dtype=np.bool_)
        self.pos = 0
        self.size = 0

    def push(self, state, actions: np.ndarray, reward: float, next_state, done: bool):
        if _MLX_CORE_AVAILABLE:
            mx = _get_mlx_core()
            if mx is not None:
                mx.eval(state, next_state)
        self.states[self.pos] = np.array(state)
        self.actions[self.pos] = actions
        self.rewards[self.pos] = reward
        self.next_states[self.pos] = np.array(next_state)
        self.dones[self.pos] = done
        self.pos = (self.pos + 1) % self.capacity
        self.size = min(self.size + 1, self.capacity)

    def sample(self, batch_size: int) -> Dict:
        idx = np.random.randint(0, self.size, batch_size)
        if _MLX_CORE_AVAILABLE:
            mx = _get_mlx_core()
            if mx is not None:
                return {
                    'states': mx.array(self.states[idx]),
                    'actions': mx.array(self.actions[idx]),
                    'rewards': mx.array(self.rewards[idx]),
                    'next_states': mx.array(self.next_states[idx]),
                    'dones': mx.array(self.dones[idx])
                }
        return {
            'states': self.states[idx],
            'actions': self.actions[idx],
            'rewards': self.rewards[idx],
            'next_states': self.next_states[idx],
            'dones': self.dones[idx]
        }

    def save(self, path: Path):
        np.savez_compressed(
            path.with_suffix('.npz'),
            states=self.states[:self.size],
            actions=self.actions[:self.size],
            rewards=self.rewards[:self.size],
            next_states=self.next_states[:self.size],
            dones=self.dones[:self.size]
        )

    def load(self, path: Path):
        data = np.load(path.with_suffix('.npz'))
        n = data['states'].shape[0]
        self.states[:n] = data['states']
        self.actions[:n] = data['actions']
        self.rewards[:n] = data['rewards']
        self.next_states[:n] = data['next_states']
        self.dones[:n] = data['dones']
        self.size = n
        self.pos = n % self.capacity
