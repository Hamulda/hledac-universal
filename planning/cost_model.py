"""
Dvoustupňový cost model: online ridge baseline + Mamba residual.
Umožňuje predikci cost (time, ram, network) a value (přínos) včetně uncertainty.

Lazy MLX loading — MLX modules are imported only when Mamba SSM is first used,
not at module import time.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# Dummy placeholder - will be wired at runtime
EvidenceLog = None


@dataclass
class OnlineRidge:
    """Online ridge regrese přes Sherman-Morrison."""
    n_features: int
    alpha: float = 1.0

    def __post_init__(self):
        self.A = np.eye(self.n_features) * self.alpha
        self.A_inv = np.eye(self.n_features) / self.alpha
        self.b = np.zeros(self.n_features)
        self.coef_ = np.zeros(self.n_features)
        self.n_samples = 0

    def update(self, x: np.ndarray, y: float):
        x = x.reshape(-1, 1)
        A_inv_x = self.A_inv @ x
        denominator = 1 + (x.T @ A_inv_x).item()
        self.A_inv -= (A_inv_x @ A_inv_x.T) / denominator
        self.b += y * x.flatten()
        self.coef_ = self.A_inv @ self.b
        self.n_samples += 1

    def predict(self, x: np.ndarray) -> float:
        return float(x @ self.coef_)


class RunningNormalizer:
    """Online normalizace features (z-score)."""
    def __init__(self, dim: int, decay: float = 0.99):
        self.dim = dim
        self.decay = decay
        self.mean = np.zeros(dim)
        self.var = np.ones(dim)
        self.count = 1e-6

    def update(self, x: np.ndarray):
        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        delta2 = x - self.mean
        self.var = self.decay * self.var + (1 - self.decay) * delta2**2

    def normalize(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / np.sqrt(self.var + 1e-8)


class AdaptiveCostModel:
    def __init__(self, governor, evidence_log,
                 feature_dim: int = 64, hidden_dim: int = 32, lr: float = 1e-3):
        self.governor = governor
        self.evidence_log = evidence_log
        self.feature_dim = feature_dim
        self.hidden_dim = hidden_dim
        self.lr = lr  # store for lazy optimizer creation

        # Online ridge pro každý výstup (4)
        self.baseline = [OnlineRidge(feature_dim) for _ in range(4)]
        self.baseline_ready = False

        # Normalizátor features
        self.normalizer = RunningNormalizer(feature_dim)

        # MLX model and optimizer — lazy loaded (set to None, loaded on first use)
        self._model = None
        self._optimizer = None
        self._mlx_loaded = False
        self.ssm_ready = False
        self.ssm_min_samples = 50

        self._history = deque(maxlen=1000)  # (features, targets)
        self.grad_clip = 1.0
        self._prev_params = None
        self._prev_loss = None

    @property
    def model(self):
        """Lazy-load MLX model on first access."""
        if self._model is None:
            self._load_mlx_model()
        return self._model

    @property
    def optimizer(self):
        """Lazy-load MLX optimizer on first access."""
        if self._optimizer is None:
            self._load_mlx_optimizer()
        return self._optimizer

    def _load_mlx_model(self):
        """Load MLX modules and create model. Called lazily on first predict if SSM is ready."""
        import mlx.nn as nn
        # Import Mamba here so it's only loaded when actually needed
        try:
            from mlx.nn import Mamba
            has_mamba = True
        except ImportError:
            has_mamba = False

        feature_dim = self.feature_dim
        hidden_dim = self.hidden_dim

        if has_mamba:
            class _MambaBlock(nn.Module):
                def __init__(self, d_model, d_state, d_conv, expand_factor):
                    super().__init__()
                    self._mamba = Mamba(
                        d_model=d_model,
                        d_state=d_state,
                        d_conv=d_conv,
                        expand_factor=expand_factor,
                    )
                    self.out_proj = nn.Linear(d_model, 4)

                def __call__(self, x):
                    x = x[:, None, :]
                    h = self._mamba(x)
                    h = h[:, 0, :]
                    return self.out_proj(h)

            self._model = _MambaBlock(feature_dim, 16, 4, 2)
        else:
            self._model = nn.Sequential(
                nn.Linear(feature_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, 4),
            )
        self._mlx_loaded = True

    def _load_mlx_optimizer(self):
        """Load MLX optimizer lazily."""
        import mlx.optimizers as optim
        self._optimizer = optim.Adam(learning_rate=self.lr)

    def _build_features(self, task_type: str, params: dict, system_state: dict) -> np.ndarray:
        """
        Sestaví feature vector z:
        - one‑hot task type (fetch, deep_read, branch, atd.)
        - normalizované parametry (např. odhad velikosti URL)
        - aktuální stav systému (počet úloh, RSS, průměrná latence)
        """
        feat = np.zeros(self.feature_dim, dtype=np.float32)

        # Task type one-hot (max 8 typů)
        type_map = {'fetch': 0, 'deep_read': 1, 'branch': 2, 'analyse': 3,
                    'synthesize': 4, 'hypothesis': 5, 'explain': 6, 'other': 7}
        idx = type_map.get(task_type, 7)
        if idx < self.feature_dim:
            feat[idx] = 1.0

        # Parametry (příklad)
        if 'url' in params:
            feat[8] = min(len(params['url']), 100) / 100.0
        if 'depth' in params:
            feat[9] = params['depth'] / 10.0

        # System state
        feat[10] = system_state.get('active_tasks', 0) / 10.0
        feat[11] = system_state.get('rss_gb', 2) / 8.0
        feat[12] = system_state.get('avg_latency', 0.1) / 2.0

        return feat

    def predict(self, task_type: str, params: dict, system_state: dict) -> tuple[float, float, float, float, float | None]:
        x_raw = self._build_features(task_type, params, system_state)
        # Normalizace
        x_norm = self.normalizer.normalize(x_raw)

        # Baseline predikce
        if self.baseline_ready:
            base = np.array([b.predict(x_norm) for b in self.baseline])
        else:
            base = np.zeros(4)

        # Residual — only import MLX if SSM is actually ready
        total = base
        uncertainty = None
        if self.ssm_ready:
            import mlx.core as mx
            x_mlx = mx.array(x_norm)[None, :]  # (1, dim)
            out = self.model(x_mlx).squeeze(0)  # (4,)
            resid = np.array(out)
            total = base + resid

        # Jednoduchá uncertainty: rozptyl posledních 10 cílů
        if len(self._history) > 10:
            recent = np.array([t[1] for t in list(self._history)[-10:]])
            var = np.var(recent, axis=0)
            uncertainty = float(np.mean(var))

        return (float(total[0]), float(total[1]), float(total[2]), float(total[3]), uncertainty)

    def predict_overrun_risk(self, cost_estimate: dict) -> float:
        """Predikce rizika překročení budgetu – placeholder."""
        return 0.1

    async def update(self, task_type: str, params: dict, system_state: dict,
                     actual: tuple[float, float, float, float]):
        x_raw = self._build_features(task_type, params, system_state)

        # Nejdřív update normalizátoru raw daty
        self.normalizer.update(x_raw)
        x_norm = self.normalizer.normalize(x_raw)
        y = np.array(actual)

        # Uložit historii
        self._history.append((x_norm, y))

        # Update baseline (online ridge) – pro každý výstup zvlášť
        for i, b in enumerate(self.baseline):
            b.update(x_norm, y[i])
        self.baseline_ready = True

        # Update SSM, pokud máme dost vzorků
        if len(self._history) >= self.ssm_min_samples:
            import mlx.core as mx
            import mlx.nn as nn
            import mlx.utils as mutils

            self.ssm_ready = True

            # Sestavíme batch (posledních 100)
            X = np.array([h[0] for h in list(self._history)[-100:]])
            Y = np.array([h[1] for h in list(self._history)[-100:]])

            X_mlx = mx.array(X)
            Y_mlx = mx.array(Y)

            def loss_fn(model):
                pred = model(X_mlx)
                return nn.losses.mse_loss(pred, Y_mlx)

            loss, grads = nn.value_and_grad(self.model, loss_fn)(self.model)

            # Gradient clipping – přes tree leaves
            leaves, treedef = mutils.tree_flatten(grads)
            flat_grads = [mx.reshape(g, (-1,)) for g in leaves]
            total_norm = mx.sqrt(mx.sum(mx.concatenate(flat_grads) ** 2))
            if total_norm > self.grad_clip:
                scale = self.grad_clip / total_norm
                scaled_leaves = [g * scale for g in leaves]
                grads = mutils.tree_unflatten(treedef, scaled_leaves)

            self.optimizer.update(self.model, grads)
            mx.eval(self.model.parameters(), self.optimizer.state)

            # Kontrola divergence – hluboká kopie parametrů
            if self._prev_loss is not None and loss.item() > self._prev_loss * 1.5:
                logger.warning("SSM diverged, reverting to previous parameters")
                self.model.update(self._prev_params)
            else:
                self._prev_params = {k: mx.array(v) for k, v in self.model.parameters().items()}
                self._prev_loss = loss.item()
