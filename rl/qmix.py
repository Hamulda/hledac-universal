"""
QMIX: Value Decomposition Networks for Multi-Agent Reinforcement Learning.
Implementace v MLX s joint loss a správným tokem gradientů.
"""
try:
    import mlx.core as mx
    import mlx.nn as nn
    import mlx.optimizers as optim
    from mlx.utils import tree_flatten, tree_map, tree_unflatten
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None
    nn = None
    optim = None
    tree_map = None
    tree_flatten = None
    tree_unflatten = None
from rl.actions import ACTION_DIM, ACTION_FETCH_MORE
if MLX_AVAILABLE:

    class QMixer(nn.Module):
        """Centrální mixing síť – kombinuje Q‑hodnoty agentů do globální Q."""
        __slots__ = tuple(('hyper_b1', 'hyper_b2', 'hyper_w1', 'hyper_w2', 'n_agents'))

        def __init__(self, n_agents: int, state_dim: int, embedding_dim: int=32):
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
            w1 = mx.abs(self.hyper_w1(states)).reshape(batch_size, -1, self.n_agents)
            b1 = self.hyper_b1(states).reshape(batch_size, -1, 1)
            w2 = mx.abs(self.hyper_w2(states)).reshape(batch_size, 1, -1)
            b2 = self.hyper_b2(states)
            hidden = mx.maximum(0, w1 @ mx.expand_dims(agent_qs, -1) + b1)
            return (w2 @ hidden).squeeze(-1) + b2

    class QNetwork(nn.Module):
        """Q‑síť pro jednoho agenta."""
        __slots__ = tuple(('fc1', 'fc2', 'q_out'))

        def __init__(self, state_dim: int, hidden_dim: int=64):
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
        __slots__ = tuple(('agent_id', 'optimizer', 'q_net', 'target_q_net'))

        def __init__(self, agent_id: str, state_dim: int, hidden_dim: int=64):
            self.agent_id = agent_id
            self.q_net = QNetwork(state_dim, hidden_dim)
            self.target_q_net = QNetwork(state_dim, hidden_dim)
            self.target_q_net.update(self.q_net.parameters())
            self.optimizer = optim.Adam(learning_rate=0.001)

        def act(self, state: mx.array, epsilon: float=0.1, fallback: bool=False) -> int:
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

        def __init__(self, mixer: QMixer, agent_nets: list[QNetwork]):
            super().__init__()
            self.mixer = mixer
            self._n_agents = len(agent_nets)
            for i, net in enumerate(agent_nets):
                setattr(self, f'agent_{i}', net)

        def get_agent_nets(self) -> list[QNetwork]:
            """Vrátí seznam agent sítí."""
            return [getattr(self, f'agent_{i}') for i in range(self._n_agents)]

    class QMIXJointTrainer:
        """
        Provádí joint update všech agentů podle QMIX algoritmu.
        Gradienty tečou přes mixer zpět do agent sítí.
        """
        __slots__ = tuple(('agents', 'gamma', 'joint_model', 'mixer', 'optimizer', 'target_mixer', 'tau'))

        def __init__(self, agents: dict[str, QMIXAgent], mixer: QMixer, target_mixer: QMixer, gamma: float=0.99, tau: float=0.005):
            self.agents = agents
            self.mixer = mixer
            self.target_mixer = target_mixer
            self.gamma = gamma
            self.tau = tau
            agent_nets = [agent.q_net for agent in agents.values()]
            self.joint_model = JointModel(mixer, agent_nets)
            self.optimizer = optim.Adam(learning_rate=0.001)

        def update(self, batch: dict[str, mx.array]) -> dict[str, float]:
            """
            batch obsahuje: 'states', 'actions', 'rewards', 'next_states', 'dones'
            states, next_states: (batch, state_dim)
            actions: (batch, n_agents) – int32
            rewards: (batch,)
            dones: (batch,)
            """
            states = batch['states']
            actions = batch['actions']
            rewards = batch['rewards']
            next_states = batch['next_states']
            dones = batch['dones']
            len(self.agents)
            agent_nets = self.joint_model.get_agent_nets()
            all_qs = mx.stack([net(states) for net in agent_nets], axis=1)
            chosen_qs = mx.take_along_axis(all_qs, mx.expand_dims(actions, -1), axis=2).squeeze(-1)
            self.mixer(chosen_qs, states)
            next_qs_current = mx.stack([net(next_states) for net in agent_nets], axis=1)
            next_actions = mx.argmax(next_qs_current, axis=2)
            next_target_qs = mx.stack([self.agents[aid].target_q_net(next_states) for aid in sorted(self.agents.keys())], axis=1)
            next_target_chosen = mx.take_along_axis(next_target_qs, mx.expand_dims(next_actions, -1), axis=2).squeeze(-1)
            next_q_total = mx.stop_gradient(self.target_mixer(next_target_chosen, next_states))
            targets = rewards.reshape(-1, 1) + self.gamma * (1 - dones.reshape(-1, 1)) * next_q_total
            targets = mx.stop_gradient(targets)

            def joint_loss_fn(model):
                agent_nets = model.get_agent_nets()
                all_qs_current = mx.stack([net(states) for net in agent_nets], axis=1)
                chosen_qs_current = mx.take_along_axis(all_qs_current, mx.expand_dims(actions, -1), axis=2).squeeze(-1)
                q_total_current = model.mixer(chosen_qs_current, states)
                return mx.mean((q_total_current - targets) ** 2)
            loss_and_grad = nn.value_and_grad(self.joint_model, joint_loss_fn)
            loss, grads = loss_and_grad(self.joint_model)
            self.optimizer.update(self.joint_model, grads)

            def polyak_update(p, tp):
                return self.tau * p + (1 - self.tau) * tp
            new_mixer_params = tree_map(polyak_update, self.mixer.parameters(), self.target_mixer.parameters())
            self.target_mixer.update(new_mixer_params)
            for _aid, agent in self.agents.items():
                new_target_params = tree_map(polyak_update, agent.q_net.parameters(), agent.target_q_net.parameters())
                agent.target_q_net.update(new_target_params)
            mx.eval(self.joint_model.parameters(), self.optimizer.state)
            return {'loss': float(loss)}
else:

    class QMixer:

        def __init__(self, *args, **kwargs):
            raise ImportError('QMixer requires MLX (not available)')

    class QNetwork:

        def __init__(self, *args, **kwargs):
            raise ImportError('QNetwork requires MLX (not available)')

    class QMIXAgent:

        def __init__(self, *args, **kwargs):
            raise ImportError('QMIXAgent requires MLX (not available)')

    class JointModel:

        def __init__(self, *args, **kwargs):
            raise ImportError('JointModel requires MLX (not available)')

    class QMIXJointTrainer:

        def __init__(self, *args, **kwargs):
            raise ImportError('QMIXJointTrainer requires MLX (not available)')