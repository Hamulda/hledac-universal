"""
Expected Information Gain (EIG) calculator for research prioritization.

Used to decide which entities to enrich next — picks the action with
highest expected information gain given current belief state.

Usage:
    from hledac.universal.utils.eig import EIGCalculator
    calc = EIGCalculator(bandit_arms={'entity_a': DempsterShafer(...)})
    eig = calc.compute_eig(hypothesis_set, candidate_action)
    if eig > EIG_THRESHOLD:
        await enrichment_queue.add(task)
"""
import math
from typing import Any
try:
    from hledac.universal.brain.evidence_fusion import DempsterShafer
    _DS_AVAILABLE = True
except ImportError:
    _DS_AVAILABLE = False
    DempsterShafer = None

class EIGCalculator:
    """Expected Information Gain calculator for action selection."""
    EIG_THRESHOLD = 0.1
    __slots__ = tuple(('bandit_arms',))

    def __init__(self, bandit_arms: dict | None=None):
        self.bandit_arms = bandit_arms or {}

    def compute_eig(self, hypothesis_set: list, action: dict[str, Any]) -> float:
        """
        Compute EIG for a given action and hypothesis set.

        Returns:
            Expected Information Gain (higher = more valuable to explore)
        """
        if not _DS_AVAILABLE or not hypothesis_set:
            return 0.0
        current_entropy = self._entropy(hypothesis_set)
        expected_entropy = self._expected_entropy_after_action(hypothesis_set, action)
        return max(0.0, current_entropy - expected_entropy)

    def _entropy(self, hypothesis_set: list) -> float:
        """Compute Shannon entropy for hypothesis set using stdlib math."""
        beliefs = []
        for h in hypothesis_set:
            if hasattr(h, 'belief'):
                b = h.belief()
            else:
                b = float(h)
            beliefs.append(max(0.0, min(1.0, b)))
        total = sum(beliefs) + 1e-08
        probs = [b / total for b in beliefs]
        entropy = -sum((p * math.log(p + 1e-08) for p in probs if p > 0))
        return float(entropy)

    def _expected_entropy_after_action(self, hypothesis_set: list, action: dict) -> float:
        """
        Expected entropy after taking action.

        Simplified: assumes ~20% entropy reduction on average.
        Real implementation would simulate possible outcomes.
        """
        current = self._entropy(hypothesis_set)
        reduction_factor = action.get('expected_reduction', 0.2)
        return current * (1.0 - reduction_factor)

    def rank_actions(self, hypothesis_set: list, candidates: list[dict]) -> list[tuple]:
        """
        Rank candidate actions by EIG (highest first).

        Returns:
            List of (action, eig_score) tuples sorted by EIG descending
        """
        scored = []
        for action in candidates:
            eig = self.compute_eig(hypothesis_set, action)
            scored.append((action, eig))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored