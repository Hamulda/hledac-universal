"""
MARLCoordinator – spravuje agenty, replay buffer, trénink a interakci se schedulerem.

PROMOTION GATE — DORMANT / HEAVY / NOT PROMOTED
================================================
QMIX multi-agent reinforcement learning koordinátor.

STATUS: DORMANT
  - training_enabled = False (hardcoded, řádek 40)
  - start_training() async method existuje, ale NENÍ VOLÁNA z žádného canonical surface
  - replay buffer limit 1000 items před training_enabled
  - žádné skutečné call sites mimo testy

M1 8GB MEMORY CEILING:
  - QMIXAgent (mlx.nn.Module) + QMixer + target_mixer: ~50-200MB per agent
  - Replay buffer: n_agents * state_dim * batch_size (konfigurovatelné)
  - NUMPY interop: np.array pro reward computation (float64, mimo MLX)
  - training loop: asyncio.Task, 60s interval, 64-batch updates
  - Epsilon decay: 0.9995 per step, min 0.05

ALLOWED PURPOSE: Research/experiment only — QMIX algoritmus je paper-impl
  bez reálného RL reward signálu v OSINT kontextu.
  _compute_reward() je dummy: new_entities*2 + confidence*1.5 - time_penalty

PROMOTION ELIGIBILITY: NO
  - Žádné production call sites (grep -r "marl_coordinator" --include="*.py" | grep -v test)
  - training_enabled hardcoded False = training nikdy nezačne
  - ETHEREINCI žádného reálného reward feedback loop
  - M1 8GB: parallel QMIX training by SWAPOVALO

SECURITY: Žádná. Tento kód nepracuje se senzitivními daty.
STEALTH: Žádná. RL training traffic by byl plně auditovatelný.
"""

import asyncio
import logging
import time
import numpy as np
import mlx.core as mx
from mlx.utils import tree_flatten, tree_unflatten
from pathlib import Path
from typing import Dict, List, Optional

from hledac.universal.rl.actions import ACTION_NAMES, ACTION_DIM, ACTION_FETCH_MORE, ACTION_CONTINUE
from hledac.universal.rl.qmix import QMIXAgent, QMixer, QMIXJointTrainer
from hledac.universal.rl.replay_buffer import MARLReplayBuffer
from hledac.universal.rl.state_extractor import StateExtractor

logger = logging.getLogger(__name__)


class MARLCoordinator:
    def __init__(self, n_agents: int, state_dim: int, hidden_dim: int = 64,
                 metrics_registry: Optional['MetricsRegistry'] = None):
        self.n_agents = n_agents
        self.state_dim = state_dim
        self.agents: Dict[str, QMIXAgent] = {}
        self.mixer = QMixer(n_agents, state_dim)
        self.target_mixer = QMixer(n_agents, state_dim)
        self.target_mixer.update(self.mixer.parameters())
        self.replay_buffer = MARLReplayBuffer(state_dim=state_dim, n_agents=n_agents)
        self.extractor = StateExtractor(state_dim)
        self.trainer: Optional[QMIXJointTrainer] = None
        self.metrics = metrics_registry
        self.training_task: Optional[asyncio.Task] = None
        self.step = 0
        self.epsilon = 1.0
        self.epsilon_decay = 0.9995
        self.epsilon_min = 0.05
        self.training_enabled = False

    def register_agent(self, agent_id: str):
        self.agents[agent_id] = QMIXAgent(agent_id, self.state_dim)
        # Re-create trainer with updated agents
        if len(self.agents) >= self.n_agents:
            self.trainer = QMIXJointTrainer(self.agents, self.mixer, self.target_mixer)

    def get_agent(self, agent_id: str) -> Optional[QMIXAgent]:
        return self.agents.get(agent_id)

    def extract_state(self, thread_state: Dict, global_state: Dict) -> mx.array:
        return self.extractor.extract(thread_state, global_state)

    def act(self, agent_id: str, state: mx.array, fallback: bool = False) -> int:
        agent = self.agents.get(agent_id)
        if agent is None:
            return ACTION_FETCH_MORE
        action = agent.act(state, self.epsilon, fallback)
        self.step += 1
        self.epsilon = max(self.epsilon_min, self.epsilon * self.epsilon_decay)
        if self.metrics:
            try:
                self.metrics.gauge('rl_epsilon', self.epsilon)
            except Exception:
                pass
        return action

    def _compute_reward(self, result: Dict) -> float:
        reward = result.get('new_entities', 0) * 2.0
        reward += result.get('confidence', 0.5) * 1.5
        reward -= result.get('time_spent', 0) / 60.0 * 0.1
        if result.get('duplicate', False):
            reward -= 0.5
        return max(reward, -10.0)

    def push_transition(self, thread_id: str, state: mx.array, actions: np.ndarray,
                        results: List[Dict], next_state: mx.array, done: bool):
        total_reward = sum(self._compute_reward(r) for r in results) / len(results) if results else 0.0
        self.replay_buffer.push(state, actions, total_reward, next_state, done)
        if not self.training_enabled and self.replay_buffer.size >= 1000:
            self.training_enabled = True
            logger.info("RL training enabled (buffer size >= 1000)")

    async def start_training(self, interval: float = 60.0):
        self.training_task = asyncio.create_task(self._training_loop(interval))

    async def _training_loop(self, interval: float):
        while True:
            await asyncio.sleep(interval)
            if not self.training_enabled or self.replay_buffer.size < 1000 or self.trainer is None:
                continue
            batch = self.replay_buffer.sample(64)
            losses = self.trainer.update(batch)
            if self.metrics:
                try:
                    self.metrics.gauge('rl_loss', losses['loss'])
                    self.metrics.gauge('rl_buffer_size', self.replay_buffer.size)
                    self.metrics.gauge('rl_steps', self.step)
                except Exception:
                    pass

    async def stop(self):
        if self.training_task:
            self.training_task.cancel()
            try:
                await self.training_task
            except asyncio.CancelledError:
                pass

    def save_models(self, path: Path):
        flat = {}
        for aid, agent in self.agents.items():
            for k, v in tree_flatten(agent.q_net.parameters()):
                flat[f"agent_{aid}.{k}"] = v
        for k, v in tree_flatten(self.mixer.parameters()):
            flat[f"mixer.{k}"] = v
        mx.savez(str(path.with_suffix('.npz')), **flat)

    def load_models(self, path: Path):
        flat = mx.load(str(path.with_suffix('.npz')))
        agent_params = {}
        mixer_params = []  # list (key, value) párů

        for key, val in flat.items():
            if key.startswith('agent_'):
                parts = key.split('.', 1)
                agent_id = parts[0][6:]
                subkey = parts[1]
                agent_params.setdefault(agent_id, []).append((subkey, val))
            elif key.startswith('mixer.'):
                subkey = key[6:]
                mixer_params.append((subkey, val))

        for aid, params_list in agent_params.items():
            if aid in self.agents:
                self.agents[aid].q_net.update(tree_unflatten(params_list))
                self.agents[aid].target_q_net.update(tree_unflatten(params_list))

        self.mixer.update(tree_unflatten(mixer_params))
        self.target_mixer.update(tree_unflatten(mixer_params))
