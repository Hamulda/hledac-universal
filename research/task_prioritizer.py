"""
TaskPrioritizer – MLP pro predikci gain + duration s perzistencí.
Implementováno v MLX s online učením a ukládáním parametrů.
"""

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# MLX import s fallback
try:
    import mlx.core as mx
    import mlx.nn as nn

    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None
    nn = None


if MLX_AVAILABLE:

    class TaskPrioritizer(nn.Module):
        """
        MLP pro predikci přínosu a doby trvání úlohy.
        Vstup: 10-dim feature vector (task metadata)
        Výstup: [gain, duration]
        """
        def __init__(self, input_dim: int = 10, hidden_dim: int = 32):
            super().__init__()
            self.fc1 = nn.Linear(input_dim, hidden_dim)
            self.fc2 = nn.Linear(hidden_dim, 2)  # [gain, duration]

        def __call__(self, x):
            x = nn.relu(self.fc1(x))
            return self.fc2(x)

else:
    # Stub when MLX unavailable — fail-soft so callers handle unavailability
    class TaskPrioritizer:  # type: ignore[no-redef]
        """Stub when MLX unavailable."""
        def __init__(self, *args, **kwargs):
            raise ImportError("TaskPrioritizer requires MLX (not available)")

        def __call__(self, *args, **kwargs):
            raise ImportError("TaskPrioritizer requires MLX (not available)")


class TaskPrioritizerWrapper:
    """
    Wrapper pro TaskPrioritizer s perzistencí a online učením.
    """
    def __init__(self, model_path: Path):
        self.model_path = model_path
        self.model = TaskPrioritizer() if MLX_AVAILABLE else None

        if MLX_AVAILABLE:
            try:
                import mlx.optimizers as optim
                self.optimizer = optim.Adam(learning_rate=1e-3)
            except (ImportError, AttributeError):
                self.optimizer = None
        else:
            self.optimizer = None

        self.trained = False
        self.update_counter = 0

        # Try to load existing model
        if MLX_AVAILABLE:
            self._load()

    def _flatten_params(self, params, prefix: str = ''):
        """Převede vnořené parametry na plochý slovník."""
        flat = {}
        for k, v in params.items():
            if isinstance(v, dict):
                flat.update(self._flatten_params(v, prefix + k + '.'))
            else:
                flat[prefix + k] = v
        return flat

    def _unflatten_params(self, flat: Dict):
        """Převede plochý slovník zpět na vnořený."""
        nested = {}
        for key, value in flat.items():
            parts = key.split('.')
            d = nested
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = value
        return nested

    def _load(self):
        """Načte model ze souboru."""
        if not MLX_AVAILABLE or self.model is None:
            return
        if not self.model_path.exists():
            return

        try:
            loaded = mx.load(str(self.model_path))
            if isinstance(loaded, dict):
                flat = {k: v for k, v in loaded.items()}
                nested = self._unflatten_params(flat)
                self.model.update(nested)
                self.trained = True
                logger.info(f"Loaded TaskPrioritizer from {self.model_path}")
        except Exception as e:
            logger.warning(f"Failed to load TaskPrioritizer: {e}")

    async def save(self):
        """Uloží model do souboru."""
        if not MLX_AVAILABLE or self.model is None:
            return

        try:
            # Ensure directory exists
            self.model_path.parent.mkdir(parents=True, exist_ok=True)

            flat = self._flatten_params(dict(self.model.parameters()))
            mx.savez(str(self.model_path), **flat)
            logger.info(f"Saved TaskPrioritizer to {self.model_path}")
        except Exception as e:
            logger.error(f"Failed to save TaskPrioritizer: {e}")

    def extract_features(self, task_metadata: Dict):
        """
        Extrahuje 10-dim feature vector z task metadata.
        Všechny features normalizovány do [0.0, 1.0] range.
        """
        if not MLX_AVAILABLE:
            return None

        # Feature bounds for normalization (min, max)
        # Numeric features: scale from [min, max] -> [0.0, 1.0]
        # Out-of-bounds values clamped to 0.0 or 1.0

        # Feature 0: priority [0.0, 1.0] — already normalized
        priority = max(0.0, min(1.0, task_metadata.get('priority', 0.5)))

        # Feature 1: estimated_duration [0.1, 120.0] seconds -> [0.0, 1.0]
        estimated_duration_raw = task_metadata.get('estimated_duration', 1.0)
        estimated_duration = max(0.0, min(1.0, (estimated_duration_raw - 0.1) / 119.9))

        # Feature 2: complexity [0.0, 1.0] — already normalized
        complexity = max(0.0, min(1.0, task_metadata.get('complexity', 0.5)))

        # Feature 3: source_type — ordinal encoding
        # Maps source types to numeric values based on typical reliability/reward
        source_type_map = {
            'feed': 0.1,
            'cert': 0.3,
            'dns': 0.2,
            'scrape': 0.4,
            'archive': 0.5,
            'api': 0.6,
            'ct': 0.7,
            'pubsub': 0.8,
            'leak': 0.9,
            'breach': 0.85,
        }
        source_type_raw = task_metadata.get('source_type', 'feed')
        if isinstance(source_type_raw, str):
            source_type = source_type_map.get(source_type_raw.lower(), 0.5)
        else:
            source_type = float(source_type_raw) if source_type_raw else 0.5

        # Feature 4: entity_count [0, 1000] -> [0.0, 1.0]
        entity_count_raw = task_metadata.get('entity_count', 0)
        entity_count = max(0.0, min(1.0, entity_count_raw / 1000.0))

        # Feature 5: novelty [0.0, 1.0] — already normalized
        novelty = max(0.0, min(1.0, task_metadata.get('novelty', 0.5)))

        # Feature 6: contradiction_score [0.0, 1.0] — already normalized
        contradiction_score = max(0.0, min(1.0, task_metadata.get('contradiction_score', 0.0)))

        # Feature 7: centrality [0.0, 1.0] — already normalized
        centrality = max(0.0, min(1.0, task_metadata.get('centrality', 0.0)))

        # Feature 8: historical_gain [0.0, 1.0] — already normalized
        historical_gain = max(0.0, min(1.0, task_metadata.get('historical_gain', 0.5)))

        # Feature 9: historical_duration [0.1, 120.0] -> [0.0, 1.0]
        historical_duration_raw = task_metadata.get('historical_duration', 1.0)
        historical_duration = max(0.0, min(1.0, (historical_duration_raw - 0.1) / 119.9))

        features = [
            priority,
            estimated_duration,
            complexity,
            source_type,
            entity_count,
            novelty,
            contradiction_score,
            centrality,
            historical_gain,
            historical_duration,
        ]

        return mx.array(features, dtype=mx.float32)

    async def predict(self, task_metadata: Dict) -> Tuple[float, float]:
        """
        Predikuje gain a duration pro danou úlohu.
        Vrací (predicted_gain, predicted_duration).
        """
        if not MLX_AVAILABLE or self.model is None:
            return 0.5, 1.0

        if not self.trained:
            return 0.5, 1.0

        features = self.extract_features(task_metadata)
        if features is None:
            return 0.5, 1.0

        out = self.model(features)
        return float(out[0]), float(out[1])

    async def update(self, task_metadata: Dict, actual_gain: float, actual_duration: float):
        """
        Provede online update modelu na základě skutečných výsledků.
        """
        if not MLX_AVAILABLE or self.model is None or self.optimizer is None:
            return

        features = self.extract_features(task_metadata)
        if features is None:
            return

        target = mx.array([actual_gain, actual_duration], dtype=mx.float32)

        # MSE loss
        def loss_fn(m):
            return nn.losses.mse_loss(m(features), target)

        # Compute gradients
        loss_and_grad_fn = nn.value_and_grad(self.model, loss_fn)
        loss, grads = loss_and_grad_fn(self.model)

        # Update weights
        self.optimizer.update(self.model, grads)

        # Evaluate to apply updates
        mx.eval(self.model.parameters(), self.optimizer.state)

        self.trained = True
        self.update_counter += 1

        # Save every 10 updates
        if self.update_counter % 10 == 0:
            await self.save()

        logger.debug(f"TaskPrioritizer updated, loss: {loss.item():.4f}")

    def is_available(self) -> bool:
        """Kontroluje dostupnost MLX."""
        return MLX_AVAILABLE and self.model is not None
