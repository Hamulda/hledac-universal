"""
Beta-Binomial confidence estimator for dynamic belief updating.

Used in enrichment confidence policy — updates confidence after each new
evidence piece using Bayesian inference.

Usage:
    from brain.confidence_utils import BetaBinomial
    bb = BetaBinomial(alpha=successes+1, beta=failures+1)
    confidence = bb.belief()
"""
from __future__ import annotations

import math
from typing import Tuple


class BetaBinomial:
    """
    Beta-Binomial Bayesian confidence estimator.

    After each enrichment result:
    - success: bb.add_support(weight)
    - contradiction: bb.add_contradict(weight)
    - confidence = bb.belief()
    """

    def __init__(self, alpha: float = 1.0, beta: float = 1.0):
        self.alpha = alpha
        self.beta = beta

    def add_support(self, weight: float = 1.0):
        """Add supporting evidence for current belief."""
        self.alpha += weight

    def add_contradict(self, weight: float = 1.0):
        """Add contradicting evidence against current belief."""
        self.beta += weight

    def mean(self) -> float:
        """Posterior mean."""
        s = self.alpha + self.beta
        return self.alpha / s if s > 0 else 0.5

    def variance(self) -> float:
        """Posterior variance."""
        s = self.alpha + self.beta
        if s <= 0:
            return 0.25
        return (self.alpha * self.beta) / (s * s * (s + 1))

    def belief(self) -> float:
        """Return belief as posterior mean (0..1)."""
        return self.mean()

    def credible_interval(self, p: float = 0.95) -> Tuple[float, float]:
        """Return credible interval (mean ± 2 std by default)."""
        std = math.sqrt(self.variance())
        lo = max(0.0, self.mean() - 2 * std)
        hi = min(1.0, self.mean() + 2 * std)
        return lo, hi

    def conflict(self) -> float:
        """Return conflict score (0..1) based on variance."""
        return min(1.0, self.variance() * 4)

    def to_dict(self) -> dict:
        """Serialize state."""
        return {'alpha': self.alpha, 'beta': self.beta}

    @classmethod
    def from_dict(cls, d: dict) -> 'BetaBinomial':
        """Restore from dict."""
        return cls(alpha=d.get('alpha', 1.0), beta=d.get('beta', 1.0))