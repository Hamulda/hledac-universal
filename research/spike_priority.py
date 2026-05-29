"""
Malá spiking neuronová síť (LIF) implementovaná v MLX pro impulzivní změny priorit.
"""

from __future__ import annotations

import time

# MLX import s fallback
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None


class LIFNeuron:
    """
    Leaky Integrate-and-Fire neuron pro spiking.
    """
    def __init__(self, threshold: float = 0.7, tau: float = 0.1):
        self.threshold = threshold
        self.tau = tau
        self.potential = 0.0
        self.last_spike = 0.0

    def forward(self, input_current: float, dt: float = 0.01) -> float:
        """
        Provede jeden krok LIF neuronu.
        Vrací spike (potenciál) pokud překročí threshold, jinak 0.
        """
        # Leaky integrace
        self.potential = self.potential * (1 - dt / self.tau) + input_current * dt

        if self.potential > self.threshold:
            spike = self.potential
            self.potential = 0.0
            self.last_spike = time.time()
            return spike
        return 0.0

    def reset(self):
        """Resetuje neuron do初始ního stavu."""
        self.potential = 0.0
        self.last_spike = 0.0


class SpikePriorityNetwork:
    """
    Malá síť LIF neuronů pro impulzivní změny priorit.
    """
    def __init__(self, n_neurons: int = 8):
        self.n_neurons = n_neurons
        # Různé threshholdy pro různé neurony
        self.neurons = [
            LIFNeuron(threshold=0.5 + i * 0.1, tau=0.05 + i * 0.02)
            for i in range(n_neurons)
        ]

    def forward(self, input_val: float) -> list[float]:
        """
        Provede forward pass přes všechny neurony.
        Vrací seznam spike hodnot.
        """
        return [n.forward(input_val) for n in self.neurons]

    def reset(self):
        """Resetuje všechny neurony."""
        for n in self.neurons:
            n.reset()

    def get_spike_count(self) -> int:
        """Vrátí počet neuronů, které v posledním kroku vyslaly spike."""
        return sum(1 for n in self.neurons if n.potential == 0 and n.last_spike > 0)


class MLXSpikeNetwork:
    """
    MLX-akcelerovaná verze spiking sítě pro batch zpracování.
    """
    def __init__(self, n_neurons: int = 8):
        if not MLX_AVAILABLE:
            raise RuntimeError("MLX not available")

        self.n_neurons = n_neurons
        # Thresholds a tau parametry jako MLX arrays
        self.thresholds = mx.array([0.5 + i * 0.1 for i in range(n_neurons)])
        self.taus = mx.array([0.05 + i * 0.02 for i in range(n_neurons)])
        self.potentials = mx.zeros(n_neurons)

    def forward(self, input_val: float) -> list[float]:
        """MLX-forward pass."""
        # Broadcast input
        inputs = mx.full(self.n_neurons, input_val)

        # LIF update
        dt = 0.01
        self.potentials = self.potentials * (1 - dt / self.taus) + inputs * dt

        # Spiking
        spikes = mx.where(self.potentials > self.thresholds, self.potentials, mx.zeros(self.n_neurons))

        # Reset spiked neurons
        mask = spikes > 0
        self.potentials = mx.where(mask, mx.zeros(self.n_neurons), self.potentials)

        return list(spikes)

    def reset(self):
        """Resetuje potenciály."""
        self.potentials = mx.zeros(self.n_neurons)
