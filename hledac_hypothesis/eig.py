"""
hledac_hypothesis/eig.py — Expected Information Gain calculator.

Cutting-edge: pure-Python entropy estimation over Dempster-Shafer belief

masses. No numpy — M1-8GB-safe. Used in OSINT hypothesis selection
(entropy-reduction maximisation across candidate action sets).

API:
    EIGCalculator()
    .compute_eig(hypotheses, action) -> float

`hypotheses` is a list of DempsterShafer instances representing the prior
belief state and a posterior after a hypothetical action. The returned EIG
is the expected entropy reduction in nats.
"""


import math
from typing import Any

from hledac_hypothesis.dempster_shafer import DempsterShafer


class EIGCalculator:
    """Expected Information Gain (Kullback-Leibler divergence estimator).

    Computes H(prior) - H(posterior) where entropy is taken over the
    hypothesis frame of a Dempster-Shafer mass distribution.

    For `compute_eig(hypotheses, action)`:
        hypotheses[0] = prior, hypotheses[1] = posterior (post-action)
        The action context dict is currently unused (placeholder for future
        action-conditioned models). It is accepted to keep API stable with
        callers that pass experiment metadata.
    """

    __slots__ = ("_enabled",)

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = bool(enabled)

    @staticmethod
    def _entropy(ds: DempsterShafer) -> float:
        """Shannon entropy of a DS mass distribution, in nats.

        Treats each focal element as a discrete symbol weighted by its mass.
        """
        if not ds.masses:
            return 0.0
        h = 0.0
        total = 0.0
        for m in ds.masses.values():
            total += m
        if total <= 0.0:
            return 0.0
        for m in ds.masses.values():
            if m > 0.0:
                p = m / total
                h -= p * math.log(p)
        return h

    def compute_eig(
        self,
        hypotheses: list[DempsterShafer],
        action: dict[str, Any] | None = None,
    ) -> float:
        """Return Expected Information Gain for a 2-element hypothesis list.

        If `hypotheses` has length < 2, falls back to entropy of the single
        prior (interpreted as "information needed to confirm prior" — i.e.
        its own entropy, fail-soft). The `action` dict is reserved for
        future action-conditioned scoring and currently ignored.
        """
        if not self._enabled:
            return 0.0
        if not hypotheses:
            return 0.0
        if len(hypotheses) == 1:
            return self._entropy(hypotheses[0])
        prior, posterior = hypotheses[0], hypotheses[1]
        return max(0.0, self._entropy(prior) - self._entropy(posterior))


__all__ = ["EIGCalculator"]
