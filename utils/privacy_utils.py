"""
Differential privacy utilities for OSINT reporting.

Provides DP noise for aggregate statistics publishing — prevents exact counts
from being derived from reported aggregates.

Usage:
    from utils.privacy_utils import DPNoise, RDPCalculator
    dp = DPNoise(epsilon=1.0, delta=1e-5)
    noisy_counts = dp.add_noise({'entity_count': 42, 'finding_count': 17})
"""
from __future__ import annotations

import math
import random
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


class DPNoise:
    """Differential noise for aggregate statistics in OSINT reports."""

    def __init__(self, epsilon: float = 1.0, delta: float = 1e-5, sensitivity: float = 1.0):
        self.epsilon = epsilon
        self.delta = delta
        self.sensitivity = sensitivity
        # Gaussian noise scale: sigma >= sensitivity * sqrt(2*ln(1.25/delta)) / epsilon
        self.noise_scale = sensitivity * math.sqrt(2 * math.log(1.25 / delta)) / epsilon
        logger.info(f"DPNoise: epsilon={epsilon}, delta={delta}, noise_scale={self.noise_scale:.4f}")

    def clip_update(self, weights: Dict[str, Any], max_norm: float = 1.0) -> Dict[str, Any]:
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
                # Array-like: compute L2 norm
                norm = math.sqrt(sum(x * x for x in v))
                if norm > max_norm:
                    scale = max_norm / norm
                    clipped[k] = [x * scale for x in v]
                else:
                    clipped[k] = v
            else:
                clipped[k] = v
        return clipped

    def add_noise(self, weights: Dict[str, Any]) -> Dict[str, Any]:
        """Add Gaussian noise to weights/counts using stdlib random."""
        noisy = {}
        for k, v in weights.items():
            if isinstance(v, (int, float)):
                noise = random.gauss(0, self.noise_scale)
                noisy[k] = v + noise
            elif isinstance(v, (list, tuple)):
                noise = [random.gauss(0, self.noise_scale) for _ in v]
                noisy[k] = [a + b for a, b in zip(v, noise)]
            else:
                noisy[k] = v
        return noisy


class RDPCalculator:
    """Rényi Differential Privacy calculator for composition."""

    def __init__(self, noise_scale: float, delta: float = 1e-5):
        self.noise_scale = noise_scale
        self.delta = delta

    def get_epsilon(self, q: float, steps: int, alpha: float = 10.0) -> float:
        """
        Compute epsilon from Rényi DP.

        Args:
            q: sampling ratio
            steps: number of composition steps
            alpha: Rényi parameter (order)
        """
        # Simplified RDP -> DP conversion for Gaussian mechanism
        rdp = (alpha * q * q) / (2 * self.noise_scale * self.noise_scale)
        epsilon = rdp + math.log(1 / self.delta) / (alpha - 1)
        return epsilon * steps  # Multi-step composition