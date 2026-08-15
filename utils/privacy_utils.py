"""
Differential privacy utilities for OSINT reporting.

Provides DP noise for aggregate statistics publishing — prevents exact counts


from being derived from reported aggregates.

Usage:
    from hledac.universal.utils.privacy_utils import DPNoise, RDPCalculator
    dp = DPNoise(epsilon=1.0, delta=1e-5)
    noisy_counts = dp.add_noise({'entity_count': 42, 'finding_count': 17})

SECURITY NOTE
=============
This module uses stdlib `random` module (Mersenne Twister) for noise generation,
NOT cryptographically secure sources like `secrets` or `numpy.random.Generator`.

This is intentional for OSINT aggregate reporting because:
  1. Performance: `random.gauss()` is ~10x faster than cryptographic alternatives
  2. Use case: Noise hides exact counts in aggregates, not protect secrets
  3. Reproducibility: Seedable RNG enables reproducible reports for audits

For cryptographic random number generation, use:
  - `secrets.randbits()`, `secrets.choice()` for general crypto
  - `numpy.random.Generator` with `np.random.default_rng()` for statistical needs

M1 8GB: This module is CPU-light — noise generation is O(1) per value.
"""
import logging
import math
import random
from typing import Any
from core import aclose
logger = logging.getLogger(__name__)

class DPNoise:
    """Differential noise for aggregate statistics in OSINT reports.

    Note: Uses stdlib `random.gauss()` for noise generation — intentional
    for OSINT reporting (fast, reproducible, not cryptographic).
    """
    __slots__ = tuple(('delta', 'epsilon', 'noise_scale', 'sensitivity'))

    def __init__(self, epsilon: float=1.0, delta: float=1e-05, sensitivity: float=1.0):
        self.epsilon = epsilon
        self.delta = delta
        self.sensitivity = sensitivity
        self.noise_scale = sensitivity * math.sqrt(2 * math.log(1.25 / delta)) / epsilon
        logger.info(f'DPNoise: epsilon={epsilon}, delta={delta}, noise_scale={self.noise_scale:.4f}')

    def clip_update(self, weights: dict[str, Any], max_norm: float=1.0) -> dict[str, Any]:
        """Clip gradient/model update to max L2 norm."""
        clipped = {}
        for k, v in weights.items():
            if isinstance(v, (int, float)):
                norm = abs(float(v))
                if norm > max_norm:
                    clipped[k] = v * (max_norm / norm)
                else:
                    clipped[k] = v
            elif isinstance(v, (list, tuple)):
                norm = math.sqrt(sum((x * x for x in v)))
                if norm > max_norm:
                    scale = max_norm / norm
                    clipped[k] = [x * scale for x in v]
                else:
                    clipped[k] = v
            else:
                clipped[k] = v
        return clipped

    def add_noise(self, weights: dict[str, Any]) -> dict[str, Any]:
        """Add Gaussian noise to weights/counts using stdlib random.

        Note: Uses `random.gauss()` (Mersenne Twister) — see module docstring
        for rationale on why stdlib random is used instead of crypto RNG.
        """
        noisy = {}
        for k, v in weights.items():
            if isinstance(v, (int, float)):
                noise = random.gauss(0, self.noise_scale)
                noisy[k] = v + noise
            elif isinstance(v, (list, tuple)):
                noise = [random.gauss(0, self.noise_scale) for _ in v]
                noisy[k] = [a + b for a, b in zip(v, noise, strict=False)]
            else:
                noisy[k] = v
        return noisy

class RDPCalculator:
    """Rényi Differential Privacy calculator for composition."""
    __slots__ = tuple(('delta', 'noise_scale'))

    def __init__(self, noise_scale: float, delta: float=1e-05):
        self.noise_scale = noise_scale
        self.delta = delta

    def get_epsilon(self, q: float, steps: int, alpha: float=10.0) -> float:
        """
        Compute epsilon from Rényi DP.

        Args:
            q: sampling ratio
            steps: number of composition steps
            alpha: Rényi parameter (order)
        """
        rdp = alpha * q * q / (2 * self.noise_scale * self.noise_scale)
        epsilon = rdp + math.log(1 / self.delta) / (alpha - 1)
        return epsilon * steps