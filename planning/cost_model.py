"""
Dvoustupňový cost model: online ridge baseline + Mamba residual.
Umožňuje predikci cost (time, ram, network) a value (přínos) včetně uncertainty.




Lazy MLX loading — MLX modules are imported only when Mamba SSM is first used,
not at module import time.
"""
import logging
from collections import deque
from dataclasses import dataclass

from typing import Any

import msgspec
import numpy as np
from core import aclose
logger = logging.getLogger(__name__)
EvidenceLog = None

class OnlineRidge:
    """Online ridge regrese přes Sherman-Morrison."""
    __slots__ = tuple(('n_features', 'alpha', 'A', 'A_inv', 'b', 'coef_', 'n_samples'))

    def __init__(self, n_features: int, alpha: float = 1.0) -> None:
        self.n_features = n_features
        self.alpha = alpha
        self.A = np.eye(n_features) * alpha
        self.A_inv = np.eye(n_features) / alpha
        self.b = np.zeros(n_features)
        self.coef_ = np.zeros(n_features)
        self.n_samples = 0

    def update(self, x: np.ndarray, y: float):
        x = x.reshape(-1, 1)
        A_inv_x = self.A_inv @ x
        denominator = 1 + (x.T @ A_inv_x).item()
        self.A_inv -= A_inv_x @ A_inv_x.T / denominator
        self.b += y * x.flatten()
        self.coef_ = self.A_inv @ self.b
        self.n_samples += 1

    def predict(self, x: np.ndarray) -> float:
        return float(x @ self.coef_)

class RunningNormalizer:
    """Online normalizace features (z-score)."""
    __slots__ = tuple(('count', 'decay', 'dim', 'mean', 'var'))

    def __init__(self, dim: int, decay: float=0.99):
        self.dim = dim
        self.decay = decay
        self.mean = np.zeros(dim)
        self.var = np.ones(dim)
        self.count = 1e-06

    def update(self, x: np.ndarray):
        self.count += 1
        delta = x - self.mean
        self.mean += delta / self.count
        delta2 = x - self.mean
        self.var = self.decay * self.var + (1 - self.decay) * delta2 ** 2

    def normalize(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / np.sqrt(self.var + 1e-08)

class AdaptiveCostModel:
    __slots__ = tuple(('_history', '_mlx_loaded', '_model', '_optimizer', '_prev_loss', '_prev_params', '_sprint_remaining_actions', '_sprint_remaining_s', '_sprint_total_s', 'baseline', 'baseline_ready', 'evidence_log', 'feature_dim', 'governor', 'grad_clip', 'hidden_dim', 'lr', 'normalizer', 'ssm_min_samples', 'ssm_ready'))

    def __init__(self, governor, evidence_log, feature_dim: int=64, hidden_dim: int=32, lr: float=0.001):
        self.governor = governor
        self.evidence_log = evidence_log
        self.feature_dim = feature_dim
        self.hidden_dim = hidden_dim
        self.lr = lr
        self.baseline = [OnlineRidge(feature_dim) for _ in range(4)]
        self.baseline_ready = False
        self.normalizer = RunningNormalizer(feature_dim)
        self._model = None
        self._optimizer = None
        self._mlx_loaded = False
        self.ssm_ready = False
        self.ssm_min_samples = 50
        self._history = deque(maxlen=1000)
        self.grad_clip = 1.0
        self._prev_params = None
        self._prev_loss = None
        # BLITZ-05: Sprint time context for overrun risk prediction
        self._sprint_remaining_s: float = float('inf')
        self._sprint_total_s: float = 300.0
        self._sprint_remaining_actions: int = 10

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
        try:
            from mlx.nn import Mamba
            has_mamba = True
        except ImportError:
            has_mamba = False
        feature_dim = self.feature_dim
        hidden_dim = self.hidden_dim
        if has_mamba:

            class _MambaBlock(nn.Module):
                __slots__ = tuple(('_mamba', 'out_proj'))

                def __init__(self, d_model, d_state, d_conv, expand_factor):
                    super().__init__()
                    self._mamba = Mamba(d_model=d_model, d_state=d_state, d_conv=d_conv, expand_factor=expand_factor)
                    self.out_proj = nn.Linear(d_model, 4)

                def __call__(self, x):
                    x = x[:, None, :]
                    h = self._mamba(x)
                    h = h[:, 0, :]
                    return self.out_proj(h)
            self._model = _MambaBlock(feature_dim, 16, 4, 2)
        else:
            self._model = nn.Sequential(nn.Linear(feature_dim, hidden_dim), nn.ReLU(), nn.Linear(hidden_dim, hidden_dim), nn.ReLU(), nn.Linear(hidden_dim, 4))
        self._mlx_loaded = True

    def _load_mlx_optimizer(self):
        """Load MLX optimizer lazily."""
        import mlx.optimizers as optim
        self._optimizer = optim.Adam(learning_rate=self.lr)

    def set_sprint_context(self, remaining_time_s: float, total_duration_s: float = 300.0,
                           remaining_actions: int = 10) -> None:
        """BLITZ-05: Set sprint time context for overrun risk prediction.

        Injects remaining time, total duration, and estimated remaining actions
        into the cost model so predict_overrun_risk() can compare predicted
        time_cost against the per-action time budget.

        Args:
            remaining_time_s: Seconds remaining in the sprint (SprintClock.remaining_s).
            total_duration_s: Total sprint duration for feature normalization.
            remaining_actions: Estimated number of actions still to run.
        """
        self._sprint_remaining_s = max(remaining_time_s, 0.0)
        self._sprint_total_s = max(total_duration_s, 1.0)
        self._sprint_remaining_actions = max(remaining_actions, 1)

    def _build_features(self, task_type: str, params: dict, system_state: dict) -> np.ndarray:
        """
        Sestaví feature vector z:
        - one‑hot task type (fetch, deep_read, branch, atd.)
        - normalizované parametry (např. odhad velikosti URL)
        - aktuální stav systému (počet úloh, RSS, průměrná latence)
        - BLITZ-05: sprint time context (remaining ratio, per-action budget)
        """
        feat = np.zeros(self.feature_dim, dtype=np.float32)
        type_map = {'fetch': 0, 'deep_read': 1, 'branch': 2, 'analyse': 3, 'synthesize': 4, 'hypothesis': 5, 'explain': 6, 'other': 7}
        idx = type_map.get(task_type, 7)
        if idx < self.feature_dim:
            feat[idx] = 1.0
        if 'url' in params:
            feat[8] = min(len(params['url']), 100) / 100.0
        if 'depth' in params:
            feat[9] = params['depth'] / 10.0
        feat[10] = system_state.get('active_tasks', 0) / 10.0
        feat[11] = system_state.get('rss_gb', 2) / 8.0
        feat[12] = system_state.get('avg_latency', 0.1) / 2.0
        # BLITZ-05: Sprint time context features
        # Feature 13: remaining time ratio (1.0 = full time, 0.0 = deadline)
        if self._sprint_total_s > 0:
            feat[13] = min(max(self._sprint_remaining_s / self._sprint_total_s, 0.0), 1.0)
        # Feature 14: normalized per-action time budget (minutes, capped at 5)
        per_action_s = self._sprint_remaining_s / max(self._sprint_remaining_actions, 1)
        feat[14] = min(per_action_s / 300.0, 1.0)  # normalized to 5 min max
        return feat

    def predict(self, task_type: str, params: dict, system_state: dict) -> tuple[float, float, float, float, float | None]:
        """Synchronous predict — use predict_async for async contexts."""
        # PRM-1 FIX: For async contexts, use predict_async instead to avoid blocking
        return self._predict_impl(task_type, params, system_state)

    async def predict_async(self, task_type: str, params: dict, system_state: dict) -> tuple[float, float, float, float, float | None]:
        """PRM-1: Async predict — runs MLX inference in thread pool to avoid blocking event loop."""
        import asyncio
        try:
            return await asyncio.to_thread(
                self._predict_impl, task_type, params, system_state
            )
        except Exception:
            # Fallback to sync on error
            return self._predict_impl(task_type, params, system_state)

    def _predict_impl(self, task_type: str, params: dict, system_state: dict) -> tuple[float, float, float, float, float | None]:
        x_raw = self._build_features(task_type, params, system_state)
        x_norm = self.normalizer.normalize(x_raw)
        if self.baseline_ready:
            base = np.array([b.predict(x_norm) for b in self.baseline])
        else:
            base = np.zeros(4)
        total = base
        uncertainty = None
        if self.ssm_ready:
            import mlx.core as mx
            x_mlx = mx.array(x_norm)[None, :]
            out = self.model(x_mlx).squeeze(0)
            resid = np.array(out)
            total = base + resid
        if len(self._history) > 10:
            recent = np.array([t[1] for t in list(self._history)[-10:]])
            var = np.var(recent, axis=0)
            uncertainty = float(np.mean(var))
        return (float(total[0]), float(total[1]), float(total[2]), float(total[3]), uncertainty)

    def predict_overrun_risk(self, cost_estimate: dict) -> float:
        """BLITZ-05: Predikce rizika překročení budgetu pomocí existujícího Mamba SSM.

        Používá predict() pro odhad time_cost a porovnává ho s per-action
        time budgetem (remaining_time / remaining_actions). Vrací skóre v [0, 1].

        Risk mapping:
            ratio (time_cost / budget) → risk
            <= 0.5  → risk < 0.15   (dostatek času, projde)
            = 1.0   → risk = 0.30   (na hraně budgetu, mírně znepokojivé)
            = 1.5   → risk = 0.50   (překročení o 50 %)
            = 2.0   → risk = 0.70   (2x budget, pravděpodobný overrun)
            >= 3.0  → risk → 1.0    (jistý overrun)

        Uncertainty z predict() přidává až +0.25 k risk skóre.

        Pokud není nastaven deadline (remaining_s = inf), vrací 0.0.
        """
        # If no deadline is set, there's no overrun risk
        if self._sprint_remaining_s == float('inf') or self._sprint_remaining_s <= 0.0:
            return 0.0

        # Extract cost estimate info for feature construction
        ram_mb = cost_estimate.get('ram_mb', 0)
        gpu = cost_estimate.get('gpu', False)

        params: dict[str, Any] = {'ram_mb': ram_mb}
        if gpu:
            params['gpu'] = True

        # Build system state snapshot
        sys_state: dict[str, Any] = {
            'active_tasks': getattr(self.governor, '_active_tasks', 0) if self.governor else 0,
            'rss_gb': ram_mb / 1024.0,
            'avg_latency': 0.1,
        }

        # Use the existing Mamba SSM + ridge baseline to predict cost
        time_cost, _ram_cost, _net_cost, _value, uncertainty = self.predict(
            'other', params, sys_state
        )

        # Per-action time budget: how much time each remaining action gets
        per_action_budget_s = self._sprint_remaining_s / max(self._sprint_remaining_actions, 1)

        if per_action_budget_s <= 0.0:
            return 1.0  # zero budget → certain overrun

        # Ratio of predicted cost to per-action budget
        ratio = time_cost / per_action_budget_s

        # Piecewise-linear risk mapping
        if ratio <= 1.0:
            risk = ratio * 0.3  # 0.0 → 0.3
        else:
            risk = 0.3 + (ratio - 1.0) * 0.4  # 0.3 → 0.7 at ratio=2.0, → 1.0 at ratio=2.75

        risk = min(risk, 1.0)

        # Uncertainty bonus: higher uncertainty → higher risk (max +0.25)
        if uncertainty is not None and uncertainty > 0.0:
            risk = min(1.0, risk + uncertainty * 0.25)

        return float(risk)

    async def update(self, task_type: str, params: dict, system_state: dict, actual: tuple[float, float, float, float]):
        x_raw = self._build_features(task_type, params, system_state)
        self.normalizer.update(x_raw)
        x_norm = self.normalizer.normalize(x_raw)
        y = np.array(actual)
        self._history.append((x_norm, y))
        for i, b in enumerate(self.baseline):
            b.update(x_norm, y[i])
        self.baseline_ready = True
        if len(self._history) >= self.ssm_min_samples:
            import mlx.core as mx
            import mlx.nn as nn
            import mlx.utils as mutils
            self.ssm_ready = True
            X = np.array([h[0] for h in list(self._history)[-100:]])
            Y = np.array([h[1] for h in list(self._history)[-100:]])
            X_mlx = mx.array(X)
            Y_mlx = mx.array(Y)

            def loss_fn(model):
                pred = model(X_mlx)
                return nn.losses.mse_loss(pred, Y_mlx)
            loss, grads = nn.value_and_grad(self.model, loss_fn)(self.model)
            total_norm = mx.sqrt(mx.sum(mx.concatenate([mx.reshape(g, (-1,)) for g in mx.flatten(grads)]) ** 2))
            if total_norm > self.grad_clip:
                scale = self.grad_clip / total_norm
                grads = mutils.tree_map(lambda g: g * scale, grads)
            self.optimizer.update(self.model, grads)
            mx.eval(self.model.parameters(), self.optimizer.state)
            if self._prev_loss is not None and loss.item() > self._prev_loss * 1.5:
                logger.warning('SSM diverged, reverting to previous parameters')
                self.model.update(self._prev_params)
            else:
                self._prev_params = {k: mx.array(v) for k, v in self.model.parameters().items()}
                self._prev_loss = loss.item()