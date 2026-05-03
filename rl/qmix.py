"""
QMIX: Value Decomposition Networks for Multi-Agent Reinforcement Learning.
Implementace v MLX s joint loss a správným tokem gradientů.
"""

try:
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx.utils import tree_map, tree_flatten, tree_unflatten

    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None
    nn = None
    optim = None
    tree_map = None
    tree_flatten = None
    tree_unflatten = None

from typing import Dict, List, Optional
from hledac.universal.rl.actions import ACTION_DIM, ACTION_FETCH_MORE


if MLX_AVAILABLE:

    class QMixer(nn.Module):
        """Centrální mixing síť – kombinuje Q‑hodnoty agentů do globální Q."""
        def __init__(self, n_agents: int, state_dim: int, embedding_dim: int = 32):
            super().__init__()
            self.n_agents = n_agents
            self.hyper_w1 = nn.Linear(state_dim, embedding_dim * n_agents)
            self.hyper_w2 = nn.Linear(state_dim, embedding_dim)
            self.hyper_b1 = nn.Linear(state_dim, embedding_dim)
            self.hyper_b2 = nn.Linear(state_dim, 1)

        def __call__(self, agent_qs: mx.array, states: mx.array) -> mx.array:
            """
            agent_qs: (batch, n_agents)
            states: (batch, state_dim)
            returns: (batch, 1) globální Q
            """
            batch_size = states.shape[0]

            # QMIX vyžaduje nezáporné váhy pro monotonicitu
            w1 = mx.abs(self.hyper_w1(states)).reshape(batch_size, -1, self.n_agents)
            b1 = self.hyper_b1(states).reshape(batch_size, -1, 1)
            w2 = mx.abs(self.hyper_w2(states)).reshape(batch_size, 1, -1)
            b2 = self.hyper_b2(states)

            # mx.expand_dims místo .unsqueeze()
            hidden = mx.maximum(0, w1 @ mx.expand_dims(agent_qs, -1) + b1)  # (batch, embedding_dim, 1)
            return (w2 @ hidden).squeeze(-1) + b2


    class QNetwork(nn.Module):
        """Q‑síť pro jednoho agenta."""
        def __init__(self, state_dim: int, hidden_dim: int = 64):
            super().__init__()
            self.fc1 = nn.Linear(state_dim, hidden_dim)
            self.fc2 = nn.Linear(hidden_dim, hidden_dim)
            self.q_out = nn.Linear(hidden_dim, ACTION_DIM)

        def __call__(self, state: mx.array) -> mx.array:
            x = mx.maximum(0, self.fc1(state))
            x = mx.maximum(0, self.fc2(x))
            return self.q_out(x)


    class QMIXAgent:
        """Agent s vlastní Q‑sítí a target sítí."""
        def __init__(self, agent_id: str, state_dim: int, hidden_dim: int = 64):
            self.agent_id = agent_id
            self.q_net = QNetwork(state_dim, hidden_dim)
            self.target_q_net = QNetwork(state_dim, hidden_dim)
            self.target_q_net.update(self.q_net.parameters())
            self.optimizer = optim.Adam(learning_rate=1e-3)

        def act(self, state: mx.array, epsilon: float = 0.1, fallback: bool = False) -> int:
            """Epsilon‑greedy policy s fallbackem."""
            if fallback:
                return ACTION_FETCH_MORE
            if mx.random.uniform() < epsilon:
                return mx.random.randint(0, ACTION_DIM).item()
            q_values = self.q_net(state)
            return mx.argmax(q_values).item()


    class JointModel(nn.Module):
        """
        Wrapper pro všechny trénované modely (mixer + agenti).
        Umožňuje nn.value_and_grad na celém modelu.
        """
        def __init__(self, mixer: QMixer, agent_nets: List[QNetwork]):
            super().__init__()
            self.mixer = mixer
            self._n_agents = len(agent_nets)  # uložíme počet pro spolehlivé indexování
            for i, net in enumerate(agent_nets):
                setattr(self, f"agent_{i}", net)

        def get_agent_nets(self) -> List[QNetwork]:
            """Vrátí seznam agent sítí."""
            return [getattr(self, f"agent_{i}") for i in range(self._n_agents)]


    class QMIXJointTrainer:
        """
        Provádí joint update všech agentů podle QMIX algoritmu.
        Gradienty tečou přes mixer zpět do agent sítí.
        """
        def __init__(self, agents: Dict[str, QMIXAgent], mixer: QMixer, target_mixer: QMixer,
                     gamma: float = 0.99, tau: float = 0.005):
            self.agents = agents
            self.mixer = mixer
            self.target_mixer = target_mixer
            self.gamma = gamma
            self.tau = tau
            # Vytvoříme joint model pro trénink
            agent_nets = [agent.q_net for agent in agents.values()]
            self.joint_model = JointModel(mixer, agent_nets)
            self.optimizer = optim.Adam(learning_rate=1e-3)

        def update(self, batch: Dict[str, mx.array]) -> Dict[str, float]:
            """
            batch obsahuje: 'states', 'actions', 'rewards', 'next_states', 'dones'
            states, next_states: (batch, state_dim)
            actions: (batch, n_agents) – int32
            rewards: (batch,)
            dones: (batch,)
            """
            states = batch['states']
            actions = batch['actions']                # (batch, n_agents)
            rewards = batch['rewards']
            next_states = batch['next_states']
            dones = batch['dones']

            n_agents = len(self.agents)

            # Získáme agent sítě z joint modelu
            agent_nets = self.joint_model.get_agent_nets()

            # 1. Q-hodnoty pro aktuální stavy
            all_qs = mx.stack([net(states) for net in agent_nets], axis=1)  # (batch, n_agents, action_dim)

            # 2. Vybrat Q-hodnoty pro provedené akce
            chosen_qs = mx.take_along_axis(
                all_qs,
                mx.expand_dims(actions, -1),          # (batch, n_agents, 1)
                axis=2
            ).squeeze(-1)  # (batch, n_agents)

            # 3. Globální Q přes mixer
            q_total = self.mixer(chosen_qs, states)   # (batch, 1)

            # 4. Double DQN: target hodnoty
            # Akce vybrané CURRENT sítěmi na NEXT stavy
            next_qs_current = mx.stack([net(next_states) for net in agent_nets], axis=1)
            next_actions = mx.argmax(next_qs_current, axis=2)  # (batch, n_agents)

            # Target Q z target sítí
            next_target_qs = mx.stack([
                self.agents[aid].target_q_net(next_states)
                for aid in sorted(self.agents.keys())
            ], axis=1)  # (batch, n_agents, action_dim)
            next_target_chosen = mx.take_along_axis(
                next_target_qs,
                mx.expand_dims(next_actions, -1),
                axis=2
            ).squeeze(-1)

            # Target mixer (zastavíme gradient)
            next_q_total = mx.stop_gradient(self.target_mixer(next_target_chosen, next_states))

            # 5. TD target
            targets = rewards.reshape(-1, 1) + self.gamma * (1 - dones.reshape(-1, 1)) * next_q_total
            targets = mx.stop_gradient(targets)

            # 6. Joint loss funkce
            def joint_loss_fn(model):
                agent_nets = model.get_agent_nets()
                all_qs_current = mx.stack([net(states) for net in agent_nets], axis=1)
                chosen_qs_current = mx.take_along_axis(
                    all_qs_current,
                    mx.expand_dims(actions, -1),
                    axis=2
                ).squeeze(-1)
                q_total_current = model.mixer(chosen_qs_current, states)
                return mx.mean((q_total_current - targets) ** 2)

            # 7. Výpočet gradientů a update
            loss_and_grad = nn.value_and_grad(self.joint_model, joint_loss_fn)
            loss, grads = loss_and_grad(self.joint_model)
            self.optimizer.update(self.joint_model, grads)

            # 8. Polyak averaging pro target sítě
            def polyak_update(p, tp):
                return self.tau * p + (1 - self.tau) * tp

            # Target mixer
            new_mixer_params = tree_map(polyak_update, self.mixer.parameters(), self.target_mixer.parameters())
            self.target_mixer.update(new_mixer_params)

            # Target sítě agentů
            for aid, agent in self.agents.items():
                new_target_params = tree_map(polyak_update, agent.q_net.parameters(), agent.target_q_net.parameters())
                agent.target_q_net.update(new_target_params)

            # Evaluace parametrů
            mx.eval(self.joint_model.parameters(), self.optimizer.state)

            return {'loss': float(loss)}

else:
    # Stubs when MLX unavailable — fail-soft so rl/__init__.py can still import
    class QMixer:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ImportError("QMixer requires MLX (not available)")

    class QNetwork:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ImportError("QNetwork requires MLX (not available)")

    class QMIXAgent:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ImportError("QMIXAgent requires MLX (not available)")

    class JointModel:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ImportError("JointModel requires MLX (not available)")

    class QMIXJointTrainer:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            raise ImportError("QMIXJointTrainer requires MLX (not available)")
