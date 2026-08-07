"""
TaskPrioritizer – MLP pro predikci gain + duration s perzistencí.
Implementováno v MLX s online učením a ukládáním parametrů.




ISSUE-037 решения:
1. A/B testing wrapper TaskPrioritizationRouter pro porovnání MLX vs CPU heuristic
2. Proper mx.eval() před mx.metal.clear_cache() pro M1 Metal cache management
3. mx.eval() po optimizer.update() pro správné vyhodnocení gradientů
"""
from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import mlx.core as _mlx_module
    import mlx.nn as _nn_module

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Optional MLX — fail-soft
# --------------------------------------------------------------------------- #
try:
    import mlx.core as mx
    import mlx.nn as nn

    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]

# Crypto-safe RNG — F350M-R
_RNG = secrets.SystemRandom()

# --------------------------------------------------------------------------- #
# Module-level helpers — type-narrowed access za MLX_AVAILABLE guard
# --------------------------------------------------------------------------- #
def _mx() -> "_mlx_module":
    """Return typed mlx.core — only call when MLX_AVAILABLE is True."""
    assert MLX_AVAILABLE
    return mx  # type: ignore[return-value]


def _nn() -> "_nn_module":
    """Return typed mlx.nn — only call when MLX_AVAILABLE is True."""
    assert MLX_AVAILABLE
    return nn  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# MLX MLP Model — jen když je MLX dostupné
# --------------------------------------------------------------------------- #
if MLX_AVAILABLE:

    class TaskPrioritizer(nn.Module):
        """
        MLP pro predikci přínosu a doby trvání úlohy.
        Vstup: 10-dim feature vector (task metadata)
        Výstup: [gain, duration]
        """

        __slots__ = tuple(("fc1", "fc2"))

        def __init__(self, input_dim: int = 10, hidden_dim: int = 32) -> None:
            super().__init__()
            self.fc1 = nn.Linear(input_dim, hidden_dim)
            self.fc2 = nn.Linear(hidden_dim, 2)

        def __call__(self, x: Any) -> Any:
            x = nn.relu(self.fc1(x))
            return self.fc2(x)

else:

    class TaskPrioritizer:  # type: ignore[no-redef]
        """Stub when MLX unavailable."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError("TaskPrioritizer requires MLX (not available)")

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            raise ImportError("TaskPrioritizer requires MLX (not available)")


# --------------------------------------------------------------------------- #
# CPU Heuristic — baseline pro A/B testing
# --------------------------------------------------------------------------- #
def cpu_heuristic_predict(task_metadata: dict) -> tuple[float, float]:
    """
    Jednoduchá CPU heuristic bez MLX — baseline pro A/B testing.

    Vrací (predicted_gain, predicted_duration).
    Založeno na source_type a priority z metadata.
    """
    priority = max(0.0, min(1.0, task_metadata.get("priority", 0.5)))
    source_type_map = {
        "feed": (0.3, 0.5),
        "cert": (0.6, 1.0),
        "dns": (0.4, 0.8),
        "scrape": (0.5, 2.0),
        "archive": (0.4, 1.5),
        "api": (0.7, 0.3),
        "ct": (0.8, 0.5),
        "pubsub": (0.5, 1.0),
        "leak": (0.9, 0.8),
        "breach": (0.95, 1.0),
    }
    source_type = task_metadata.get("source_type", "feed")
    if isinstance(source_type, str):
        base_gain, base_duration = source_type_map.get(
            source_type.lower(), (0.5, 1.0)
        )
    else:
        base_gain, base_duration = (0.5, 1.0)

    entity_count = task_metadata.get("entity_count", 0)
    novelty = max(0.0, min(1.0, task_metadata.get("novelty", 0.5)))

    predicted_gain = base_gain * (0.7 + 0.3 * priority) * (0.8 + 0.2 * novelty)
    predicted_duration = base_duration * (0.5 + 0.5 * priority)

    return (
        max(0.0, min(1.0, predicted_gain)),
        max(0.1, min(10.0, predicted_duration)),
    )


# --------------------------------------------------------------------------- #
# TaskPrioritizerWrapper — MLX model s perzistencí a online učením
# --------------------------------------------------------------------------- #
class TaskPrioritizerWrapper:
    """
    Wrapper pro TaskPrioritizer s perzistencí a online učením.

    ISSUE-037: Přidán mx.eval() po optimizer.update() a mx.eval() + clear_cache()
    pro správné M1 Metal cache management.
    """

    __slots__ = tuple(
        (
            "model",
            "model_path",
            "optimizer",
            "trained",
            "update_counter",
        )
    )

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self.model: TaskPrioritizer | None = (
            TaskPrioritizer() if MLX_AVAILABLE else None
        )
        if MLX_AVAILABLE:
            try:
                import mlx.optimizers as optim

                self.optimizer = optim.Adam(learning_rate=0.001)
            except (ImportError, AttributeError):
                self.optimizer = None
        else:
            self.optimizer = None
        self.trained = False
        self.update_counter = 0
        if MLX_AVAILABLE:
            self._load()

    def _flatten_params(self, params: Any, prefix: str = "") -> dict:
        """Převede vnořené parametry na plochý slovník."""
        flat: dict = {}
        for k, v in params.items():
            if isinstance(v, dict):
                flat.update(self._flatten_params(v, prefix + k + "."))
            else:
                flat[prefix + k] = v
        return flat

    def _unflatten_params(self, flat: dict) -> dict:
        """Převede plochý slovník zpět na vnořený."""
        nested: dict = {}
        for key, value in flat.items():
            parts = key.split(".")
            d = nested
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = value
        return nested

    def _load(self) -> None:
        """Načte model ze souboru."""
        if not MLX_AVAILABLE or self.model is None:
            return
        if not self.model_path.exists():
            return
        try:
            loaded = mx.load(str(self.model_path))  # type: ignore[union-attr]
            if isinstance(loaded, dict):
                flat = dict(loaded.items())
                nested = self._unflatten_params(flat)
                self.model.update(nested)
                self.trained = True
                logger.info(
                    "Loaded TaskPrioritizer from %s", self.model_path
                )
        except Exception as e:
            logger.warning("Failed to load TaskPrioritizer: %s", e)

    async def save(self) -> None:
        """Uloží model do souboru."""
        if not MLX_AVAILABLE or self.model is None:
            return
        try:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            flat = self._flatten_params(dict(self.model.parameters()))
            mx.savez(str(self.model_path), **flat)  # type: ignore[union-attr]
            logger.info("Saved TaskPrioritizer to %s", self.model_path)
        except Exception as e:
            logger.error("Failed to save TaskPrioritizer: %s", e)

    def extract_features(self, task_metadata: dict) -> Any:
        """
        Extrahuje 10-dim feature vector z task metadata.
        Všechny features normalizovány do [0.0, 1.0] range.
        """
        if not MLX_AVAILABLE:
            return None
        priority = max(0.0, min(1.0, task_metadata.get("priority", 0.5)))
        estimated_duration_raw = task_metadata.get("estimated_duration", 1.0)
        estimated_duration = max(
            0.0, min(1.0, (estimated_duration_raw - 0.1) / 119.9)
        )
        complexity = max(0.0, min(1.0, task_metadata.get("complexity", 0.5)))
        source_type_map = {
            "feed": 0.1,
            "cert": 0.3,
            "dns": 0.2,
            "scrape": 0.4,
            "archive": 0.5,
            "api": 0.6,
            "ct": 0.7,
            "pubsub": 0.8,
            "leak": 0.9,
            "breach": 0.85,
        }
        source_type_raw = task_metadata.get("source_type", "feed")
        if isinstance(source_type_raw, str):
            source_type = source_type_map.get(source_type_raw.lower(), 0.5)
        else:
            source_type = (
                float(source_type_raw) if source_type_raw else 0.5
            )
        entity_count_raw = task_metadata.get("entity_count", 0)
        entity_count = max(0.0, min(1.0, entity_count_raw / 1000.0))
        novelty = max(0.0, min(1.0, task_metadata.get("novelty", 0.5)))
        contradiction_score = max(
            0.0, min(1.0, task_metadata.get("contradiction_score", 0.0))
        )
        centrality = max(0.0, min(1.0, task_metadata.get("centrality", 0.0)))
        historical_gain = max(
            0.0, min(1.0, task_metadata.get("historical_gain", 0.5))
        )
        historical_duration_raw = task_metadata.get("historical_duration", 1.0)
        historical_duration = max(
            0.0, min(1.0, (historical_duration_raw - 0.1) / 119.9)
        )
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
        _mlx = _mx()
        return _mlx.array(features, dtype=_mlx.float32)

    async def predict(self, task_metadata: dict) -> tuple[float, float]:
        """
        Predikuje gain a duration pro danou úlohu.
        Vrací (predicted_gain, predicted_duration).
        """
        if not MLX_AVAILABLE or self.model is None:
            return (0.5, 1.0)
        if not self.trained:
            return (0.5, 1.0)
        features = self.extract_features(task_metadata)
        if features is None:
            return (0.5, 1.0)
        out = self.model(features)
        return (float(out[0]), float(out[1]))

    async def update(
        self,
        task_metadata: dict,
        actual_gain: float,
        actual_duration: float,
    ) -> None:
        """
        Provede online update modelu na základě skutečných výsledků.

        ISSUE-037: Přidán mx.eval() pro správné M1 Metal cache management.
        """
        if (
            not MLX_AVAILABLE
            or self.model is None
            or self.optimizer is None
        ):
            return
        features = self.extract_features(task_metadata)
        if features is None:
            return
        _mlx = _mx()
        _nn_mod = _nn()
        target = _mlx.array(
            [actual_gain, actual_duration], dtype=_mlx.float32
        )

        def loss_fn(m: Any) -> Any:
            return _nn_mod.losses.mse_loss(m(features), target)

        loss_and_grad_fn = _nn_mod.value_and_grad(self.model, loss_fn)
        loss, grads = loss_and_grad_fn(self.model)
        self.optimizer.update(self.model, grads)

        # ISSUE-037 INVARIANT: mx.eval() před mx.metal.clear_cache()
        try:
            _mlx.eval(self.model.parameters(), self.optimizer.state)
            _mlx.metal.clear_cache()
        except Exception:  # noqa: BLE001
            pass  # fail-soft: Metal cache cleanup is best-effort

        self.trained = True
        self.update_counter += 1
        if self.update_counter % 10 == 0:
            await self.save()
        logger.debug("TaskPrioritizer updated, loss: %.4f", loss.item())

    def is_available(self) -> bool:
        """Kontroluje dostupnost MLX."""
        return MLX_AVAILABLE and self.model is not None


# --------------------------------------------------------------------------- #
# TaskPrioritizationRouter — A/B testing wrapper pro porovnání MLX vs CPU
# --------------------------------------------------------------------------- #
class TaskPrioritizationRouter:
    """
    A/B testing wrapper pro porovnání MLX modelu vs CPU heuristic.

    ISSUE-037: Umožňuje porovnání přes A/B testování — traffic split
    mezi MLX modelem a CPU heuristic pro vyhodnocení modelu v produkci.

    Usage:
        router = TaskPrioritizationRouter(model_path=Path("model.npz"))
        gain, duration = await router.predict(task_metadata, strategy="mlx")
        # nebo "cpu" pro CPU heuristic, "random" pro 50/50 split
    """

    __slots__ = tuple(
        (
            "_ab_mode",
            "_cpu_counter",
            "_mlx_counter",
            "_mlx_wrapper",
            "_random_counter",
            "_switch_count",
        )
    )

    def __init__(
        self,
        model_path: Path,
        default_strategy: str = "mlx",
    ) -> None:
        self._mlx_wrapper = TaskPrioritizerWrapper(model_path)
        self._ab_mode = default_strategy
        # A/B tracking counters
        self._mlx_counter = 0
        self._cpu_counter = 0
        self._random_counter = 0
        self._switch_count = 0

    async def predict(
        self,
        task_metadata: dict,
        strategy: str | None = None,
    ) -> tuple[float, float]:
        """
        Predikuje gain a duration podle zvolené strategie.

        Args:
            task_metadata: Metadata úlohy (priority, source_type, atd.)
            strategy: "mlx" (default MLX model), "cpu" (CPU heuristic),
                      "random" (50/50 random split), None (use router default)

        Returns:
            (predicted_gain, predicted_duration)
        """
        strat = strategy or self._ab_mode

        if strat == "cpu":
            self._cpu_counter += 1
            return cpu_heuristic_predict(task_metadata)

        if strat == "random":
            self._random_counter += 1
            if _RNG.random() < 0.5:
                self._mlx_counter += 1
                return await self._mlx_wrapper.predict(task_metadata)
            else:
                self._cpu_counter += 1
                return cpu_heuristic_predict(task_metadata)

        # Default: MLX model
        if self._mlx_wrapper.is_available():
            self._mlx_counter += 1
            return await self._mlx_wrapper.predict(task_metadata)
        else:
            self._cpu_counter += 1
            return cpu_heuristic_predict(task_metadata)

    async def update(
        self,
        task_metadata: dict,
        actual_gain: float,
        actual_duration: float,
    ) -> None:
        """Online update MLX modelu."""
        await self._mlx_wrapper.update(
            task_metadata, actual_gain, actual_duration
        )

    @property
    def ab_stats(self) -> dict:
        """
        Vrací A/B test statistiky.

        Returns:
            dict s počty predikcí pro každou strategii a počtem switchů.
        """
        return {
            "mlx_predictions": self._mlx_counter,
            "cpu_predictions": self._cpu_counter,
            "random_predictions": self._random_counter,
            "total": (
                self._mlx_counter
                + self._cpu_counter
                + self._random_counter
            ),
            "mlx_ratio": (
                self._mlx_counter
                / max(
                    1,
                    self._mlx_counter
                    + self._cpu_counter
                    + self._random_counter,
                )
            ),
        }

    def set_strategy(self, strategy: str) -> None:
        """Nastaví defaultní strategii pro A/B routing."""
        if strategy not in ("mlx", "cpu", "random"):
            raise ValueError(
                f"Unknown strategy: {strategy!r}. "
                "Must be 'mlx', 'cpu', or 'random'."
            )
        self._ab_mode = strategy

    @property
    def mlx_wrapper(self) -> TaskPrioritizerWrapper:
        """Přístup k MLX wrapperu pro přímé volání."""
        return self._mlx_wrapper
