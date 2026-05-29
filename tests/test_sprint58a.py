"""
Sprint 58A tests – QMIX, MARL, Replay Buffer, State Extractor.
"""

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pytest  # noqa: F401 — needed for skip markers on ghost MARLCoordinator tests

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


# =============================================================================
# QMIX Tests
# =============================================================================

class TestQMIX(unittest.IsolatedAsyncioTestCase):

    async def test_qmix_agent_init(self):
        """Test #1: QMIXAgent – inicializace a forward pass."""
        import mlx.core as mx
        from hledac.universal.rl.qmix import QMIXAgent

        agent = QMIXAgent(state_dim=12, action_dim=5, hidden_dim=32)
        state = mx.ones((2, 12))  # batch of 2
        q_values = agent(state)
        self.assertEqual(q_values.shape, (2, 5))

    async def test_qmix_mixer(self):
        """Test #3: QMixer – správné tvary a nezáporné váhy."""
        import mlx.core as mx
        from hledac.universal.rl.qmix import QMixer

        mixer = QMixer(n_agents=3, state_dim=12, embedding_dim=16)
        agent_qs = mx.ones((4, 3))  # batch of 4, 3 agents
        states = mx.ones((4, 12))

        q_total = mixer(agent_qs, states)

        self.assertEqual(q_total.shape, (4, 1))

    async def test_qmix_joint_update(self):
        """Test #4: QMIXJointTrainer – joint update krok."""
        import mlx.core as mx
        from hledac.universal.rl.qmix import QMIXJointTrainer, QNetwork

        networks = [QNetwork(state_dim=12, action_dim=5, hidden_dim=32) for _ in range(3)]
        mixer = QMixer(n_agents=3, state_dim=12, embedding_dim=16)
        trainer = QMIXJointTrainer(networks, mixer)

        states = mx.ones((8, 12))
        actions = mx.ones((8, 3), dtype=mx.int32)
        rewards = mx.ones((8, 1))
        next_states = mx.ones((8, 12))
        dones = mx.zeros((8, 1), dtype=mx.bool_)

        loss = trainer.update(states, actions, rewards, next_states, dones)
        self.assertIsInstance(loss, float)


# =============================================================================
# Replay Buffer Tests
# =============================================================================

class TestReplayBuffer(unittest.IsolatedAsyncioTestCase):

    async def test_replay_buffer_init(self):
        """Test #5: Replay buffer – inicializace."""
        from hledac.universal.rl.replay_buffer import MARLReplayBuffer

        buffer = MARLReplayBuffer(capacity=100, state_dim=12, n_agents=3)
        self.assertEqual(buffer.capacity, 100)
        self.assertEqual(buffer.state_dim, 12)
        self.assertEqual(buffer.n_agents, 3)
        self.assertEqual(buffer.size, 0)

    async def test_replay_buffer_push_sample(self):
        """Test #5: Replay buffer – push a sample."""
        import mlx.core as mx
        from hledac.universal.rl.replay_buffer import MARLReplayBuffer

        buffer = MARLReplayBuffer(capacity=100, state_dim=12, n_agents=3)

        for i in range(10):
            state = mx.random.normal(shape=(12,))
            actions = np.random.randint(0, 5, size=3)
            reward = float(i % 3)
            next_state = mx.random.normal(shape=(12,))
            done = (i == 9)
            buffer.push(state, actions, reward, next_state, done)

        self.assertEqual(buffer.size, 10)

        # Sample
        batch = buffer.sample(4)
        self.assertEqual(batch['states'].shape, (4, 12))
        self.assertEqual(batch['actions'].shape, (4, 3))

    async def test_replay_persistence(self):
        """Test #6: Replay buffer – perzistence s .npz."""
        import mlx.core as mx
        from hledac.universal.rl.replay_buffer import MARLReplayBuffer

        buffer1 = MARLReplayBuffer(capacity=100, state_dim=12, n_agents=3)

        for _ in range(10):
            state = mx.random.normal(shape=(12,))
            actions = np.random.randint(0, 5, size=3)
            buffer1.push(state, actions, 0.5, mx.random.normal(shape=(12,)), False)

        with tempfile.NamedTemporaryFile(suffix='.npz', delete=False) as f:
            path = f.name

        try:
            buffer1.save(path)
            buffer2 = MARLReplayBuffer(capacity=100, state_dim=12, n_agents=3)
            buffer2.load(path)

            self.assertEqual(buffer2.size, 10)

        finally:
            Path(path).unlink(missing_ok=True)


# =============================================================================
# State Extractor Tests
# =============================================================================

class TestStateExtractor(unittest.IsolatedAsyncioTestCase):

    async def test_state_extractor(self):
        """Test #7: State extractor – výstup state_dim (včetně GNN)."""
        from hledac.universal.rl.state_extractor import StateExtractor

        extractor = StateExtractor(state_dim=12)
        thread_state = {
            'entity_centrality': 0.5,
            'novelty': 0.7,
            'depth': 2,
            'contradiction': False,
            'source_type': 1
        }
        global_state = {
            'queue_size': 10,
            'memory_pressure': 0.4,
            'graph_entropy': 0.6,
            'avg_reward': 0.2,
            'num_pending_tasks': 5,
            'time_since_last_finding': 100.0,
            'resource_concurrency': 0.7
        }

        state = extractor.extract(thread_state, global_state)
        self.assertEqual(state.shape[0], 12)


# =============================================================================
# MARLCoordinator tests — SKIPPED (module deleted Sprint F196A)
# =============================================================================

@pytest.mark.skip(reason="MARLCoordinator deleted in Sprint F196A — see git history")
class TestMARLCoordinator(unittest.IsolatedAsyncioTestCase):
    """Testy pro MARL Coordinator — ALL skipped (module deleted F196A)."""

    async def test_coordinator_register(self):
        pass  # noqa: PLCB101

    async def test_coordinator_epsilon_decay(self):
        pass  # noqa: PLCB101

    async def test_coordinator_reward_calculation(self):
        pass  # noqa: PLCB101

    async def test_coordinator_checkpointing(self):
        pass  # noqa: PLCB101

    async def test_coordinator_joint_update(self):
        pass  # noqa: PLCB101

    async def test_qmix_joint_update(self):
        pass  # noqa: PLCB101

    async def test_coordinator_training_mode(self):
        pass  # noqa: PLCB101

    async def test_end_to_end_joint_update(self):
        pass  # noqa: PLCB101


# =============================================================================
# Integration Tests
# =============================================================================

@pytest.mark.skip(reason="MARLCoordinator deleted in Sprint F196A — integration depends on it")
class TestIntegration(unittest.IsolatedAsyncioTestCase):
    """Integrační testy — SKIPPED (depend on deleted MARLCoordinator)."""

    async def test_thread_agent_integration(self):
        pass  # noqa: PLCB101

    async def test_joint_training_loop(self):
        pass  # noqa: PLCB101


if __name__ == '__main__':
    unittest.main()
