"""
Malá spiking neuronová síť (LIF) implementovaná v MLX pro impulzivní změny priorit.

ISSUE-037 решения:



1. Benchmark threshold 100ms → CPU fallback pokud MLX inference > 100ms
2. Pre-compiled .mlpackage ANE model support (CoreML)
3. Proper mx.eval() + mx.metal.clear_cache() pro M1 Metal cache management
4. Batched forward pass místo per-neuron loop
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import mlx.core as _mlx_module
    import coremltools as _ct_module

# --------------------------------------------------------------------------- #
# Optional MLX — fail-soft
# --------------------------------------------------------------------------- #
try:
    import mlx.core as mx

    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
# Optional CoreML — fail-soft
# --------------------------------------------------------------------------- #
try:
    import coremltools as ct

    COREML_AVAILABLE = True
except ImportError:
    COREML_AVAILABLE = False
    ct: Any = None  # type: ignore[assignment]

# --------------------------------------------------------------------------- #
# Module-level helpers for type-narrowed access (MLX_AVAILABLE guards)
# --------------------------------------------------------------------------- #
def _mx_arrays() -> Any:
    """Return typed mlx module — only call when MLX_AVAILABLE is True."""
    assert MLX_AVAILABLE
    return mx


def _ct_models() -> Any:
    """Return typed coremltools module — only call when COREML_AVAILABLE is True."""
    assert COREML_AVAILABLE
    return ct


# Benchmark threshold
_SPIKE_BENCHMARK_THRESHOLD_MS: float = 100.0


# --------------------------------------------------------------------------- #
# CPU Fallback — vždy dostupný, bez MLX závislosti
# --------------------------------------------------------------------------- #
class LIFNeuron:
    """Leaky Integrate-and-Fire neuron pro CPU fallback."""

    __slots__ = tuple(("last_spike", "potential", "tau", "threshold"))

    def __init__(self, threshold: float = 0.7, tau: float = 0.1) -> None:
        self.threshold = threshold
        self.tau = tau
        self.potential = 0.0
        self.last_spike = 0.0

    def forward(self, input_current: float, dt: float = 0.01) -> float:
        self.potential = (
            self.potential * (1 - dt / self.tau) + input_current * dt
        )
        if self.potential > self.threshold:
            spike = self.potential
            self.potential = 0.0
            self.last_spike = time.time()
            return spike
        return 0.0

    def reset(self) -> None:
        self.potential = 0.0
        self.last_spike = 0.0


class SpikePriorityNetwork:
    """Síť LIF neuronů pro CPU fallback (pokud MLX > 100ms)."""

    __slots__ = tuple(("n_neurons", "neurons"))

    def __init__(self, n_neurons: int = 8) -> None:
        self.n_neurons = n_neurons
        self.neurons = [
            LIFNeuron(threshold=0.5 + i * 0.1, tau=0.05 + i * 0.02)
            for i in range(n_neurons)
        ]

    def forward(self, input_val: float) -> list[float]:
        return [n.forward(input_val) for n in self.neurons]

    def reset(self) -> None:
        for n in self.neurons:
            n.reset()

    def get_spike_count(self) -> int:
        return sum(
            1 for n in self.neurons if n.potential == 0 and n.last_spike > 0
        )


# --------------------------------------------------------------------------- #
# MLX Spike Network — hlavní implementace s benchmark + ANE support
# --------------------------------------------------------------------------- #
class MLXSpikeNetwork:
    """
    MLX-akcelerovaná spiking síť.

    ISSUE-037:
    - Benchmark 100ms → CPU fallback
    - Pre-compiled .mlpackage ANE model support
    - mx.eval() + mx.metal.clear_cache() pro M1 Metal
    - Batched vectorized forward pass (žádný Python loop)
    """

    __slots__ = tuple(
        (
            "_ane_model",
            "_ane_model_path",
            "_benchmarked",
            "_cpu_net",
            "_fallback_mode",
            "_inference_cache",
            "_n_neurons",
            "_potentials",
            "_taus",
            "_thresholds",
        )
    )

    def __init__(
        self,
        n_neurons: int = 8,
        ane_model_path: str | Path | None = None,
    ) -> None:
        if not MLX_AVAILABLE:
            raise RuntimeError("MLX not available")

        self._n_neurons = n_neurons
        self._ane_model_path: Path | None = (
            Path(ane_model_path) if ane_model_path else None
        )
        self._ane_model: Any = None
        self._benchmarked = False
        self._fallback_mode = False
        # ISSUE-037 FIX: instance-level cache (was module-level, shared across instances)
        self._inference_cache: dict[str, Any] = {"last_ms": 0.0, "warm": False}
        # ISSUE-037 FIX: cached CPU fallback instance (was created per-call)
        self._cpu_net: SpikePriorityNetwork | None = None

        # Use helper for type-narrowed access
        _mlx = _mx_arrays()
        self._thresholds = _mlx.array(
            [0.5 + i * 0.1 for i in range(n_neurons)]
        )
        self._taus = _mlx.array([0.05 + i * 0.02 for i in range(n_neurons)])
        self._potentials = _mlx.zeros(n_neurons)

        # Lazy ANE load
        if self._ane_model_path and self._ane_model_path.exists():
            self._load_ane_model()

    def _load_ane_model(self) -> None:
        """CoreML ANE loader — lazy, fail-soft."""
        if not COREML_AVAILABLE or not self._ane_model_path:
            return
        try:
            _ct = _ct_models()
            self._ane_model = _ct.models.MLModel(
                str(self._ane_model_path)
            )
            # ANE lives on Neural Engine, not GPU — clean Metal cache
            if MLX_AVAILABLE:
                try:
                    _mlx = _mx_arrays()
                    _mlx.eval(self._potentials)
                    _mlx.metal.clear_cache()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:
            self._ane_model = None

    def _ensure_metal_cache(self) -> None:
        """
        Metal cache cleanup před inference.
        INVARIANT: mx.eval() PŘED mx.metal.clear_cache()
        """
        if not MLX_AVAILABLE:
            return
        try:
            _mlx = _mx_arrays()
            _mlx.eval(self._potentials)
            _mlx.metal.clear_cache()
        except Exception:  # noqa: BLE001
            pass

    def _benchmark_once(self) -> bool:
        """
        Jednorázový benchmark. Přepne do CPU fallback pokud > 100ms.
        """
        if self._benchmarked:
            return not self._fallback_mode
        self._benchmarked = True

        if not MLX_AVAILABLE:
            self._fallback_mode = True
            return False

        try:
            _mlx = _mx_arrays()
            start = time.perf_counter()
            # Full forward pass including spike detection + reset (matches _forward_mlx)
            dummy = _mlx.full(self._n_neurons, 0.5)
            dt = 0.01
            pot = self._potentials * (1 - dt / self._taus) + dummy * dt
            spikes = _mlx.where(pot > self._thresholds, pot, _mlx.zeros(self._n_neurons))
            self._potentials = _mlx.where(spikes > 0, _mlx.zeros(self._n_neurons), self._potentials)
            _mlx.eval(spikes)
            elapsed_ms = (time.perf_counter() - start) * 1000

            self._inference_cache["last_ms"] = elapsed_ms
            self._inference_cache["warm"] = True

            if elapsed_ms > _SPIKE_BENCHMARK_THRESHOLD_MS:
                self._fallback_mode = True
                return False
            return True
        except Exception:
            self._fallback_mode = True
            return False

    def _forward_mlx(self, input_val: float) -> list[float]:
        """Vectorized MLX forward — žádný Python loop."""
        _mlx = _mx_arrays()
        inputs = _mlx.full(self._n_neurons, input_val)
        dt = 0.01

        self._potentials = (
            self._potentials * (1 - dt / self._taus) + inputs * dt
        )

        spikes = _mlx.where(
            self._potentials > self._thresholds,
            self._potentials,
            _mlx.zeros(self._n_neurons),
        )
        self._potentials = _mlx.where(
            spikes > 0,
            _mlx.zeros(self._n_neurons),
            self._potentials,
        )

        return list(spikes)

    def _forward_ane(self, input_val: float) -> list[float] | None:
        """ANE forward přes pre-compiled .mlpackage CoreML model."""
        if self._ane_model is None:
            return None
        try:
            features = [float(input_val) for _ in range(self._n_neurons)]
            result = self._ane_model.predict({"features": features})
            return list(result.get("spikes", []))
        except Exception:
            return None

    def forward(self, input_val: float) -> list[float]:
        """
        Forward pass s benchmark-controlled mode switching.

        A/B testing-ready: loguje timing pro každou inference.
        Priority: ANE (.mlpackage) > MLX GPU > CPU fallback
        """
        if not self._benchmarked:
            self._benchmark_once()

        # 1) ANE — most efficient on M1 (Neural Engine)
        if self._ane_model is not None and not self._fallback_mode:
            ane_result = self._forward_ane(input_val)
            if ane_result is not None:
                return ane_result

        # 2) CPU fallback (reuse cached instance)
        if self._fallback_mode or not MLX_AVAILABLE:
            if self._cpu_net is None:
                self._cpu_net = SpikePriorityNetwork(n_neurons=self._n_neurons)
            return self._cpu_net.forward(input_val)

        # 3) MLX GPU
        self._ensure_metal_cache()
        start = time.perf_counter()
        result = self._forward_mlx(input_val)
        elapsed_ms = (time.perf_counter() - start) * 1000

        self._inference_cache["last_ms"] = elapsed_ms

        # Adaptive fallback: cold start může threshold překročit jednorázově
        if (
            elapsed_ms > _SPIKE_BENCHMARK_THRESHOLD_MS
            and not self._fallback_mode
        ):
            self._fallback_mode = True

        return result

    def reset(self) -> None:
        self._potentials = _mx_arrays().zeros(self._n_neurons)

    # ---- A/B testing + introspection ----
    @property
    def fallback_mode(self) -> bool:
        """True pokud používá CPU fallback."""
        return self._fallback_mode

    @property
    def last_inference_ms(self) -> float:
        """Čas poslední inference v ms."""
        return self._inference_cache.get("last_ms", 0.0)

    @property
    def ane_available(self) -> bool:
        """True pokud je načtený ANE .mlpackage model."""
        return self._ane_model is not None
