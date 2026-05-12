"""
Dempster-Shafer evidence fusion for multi-source contradiction detection.

Used in brain/hypothesis_engine.py to merge findings from multiple sources
and detect contradictions when combined belief < 0.3.

Usage:
    from brain.evidence_fusion import DempsterShafer
    ds = DempsterShafer(hypotheses={'entity_present', 'entity_absent'})
    ds.add_evidence('entity_present', mass=0.7, source_weight=1.0)
    ds.add_evidence('entity_present', mass=0.6, source_weight=0.8)  # another source
    belief = ds.belief('entity_present')
    conflict = ds.conflict_mass()
    if conflict > 0.5:
        # High conflict — contradictory evidence
"""
from __future__ import annotations

from typing import Set, Dict, Optional


class DempsterShafer:
    """Dempster-Shafer theory implementation for hypothesis management."""

    def __init__(self, hypotheses: Optional[Set[str]] = None):
        self.hypotheses = hypotheses or set()
        self.masses: Dict[str, float] = {h: 0.0 for h in self.hypotheses}
        self.unknown = 1.0
        self.conflict = 0.0

    def add_hypothesis(self, hypothesis: str) -> None:
        """Add a new hypothesis to the frame."""
        if hypothesis not in self.hypotheses:
            self.hypotheses.add(hypothesis)
            self.masses[hypothesis] = 0.0

    def add_evidence(self, hypothesis: str, mass: float, source_weight: float = 1.0) -> None:
        """
        Add evidence for a hypothesis with source weight.

        Args:
            hypothesis: Target hypothesis
            mass: Evidence mass (0..1)
            source_weight: Source reliability weight (0..1), defaults to 1.0
        """
        weighted_mass = mass * source_weight
        K = self.masses.get(hypothesis, 0.0) * weighted_mass
        self.conflict += K
        norm = 1 - K + 1e-8
        for h in self.hypotheses:
            if h == hypothesis:
                self.masses[h] = (self.masses[h] * (1 - weighted_mass) + weighted_mass * self.unknown) / norm
            else:
                self.masses[h] = self.masses[h] * (1 - weighted_mass) / norm
        self.unknown = self.unknown * (1 - weighted_mass) / norm

    def belief(self, hypothesis: Optional[str] = None) -> float:
        """Return belief for hypothesis or total belief if None."""
        if hypothesis is None:
            return sum(self.masses.values())
        return self.masses.get(hypothesis, 0.0)

    def plausibility(self, hypothesis: str) -> float:
        """Return plausibility of hypothesis (1 - sum of masses of other hypotheses)."""
        neg_mass = sum(v for k, v in self.masses.items() if k != hypothesis)
        return 1.0 - neg_mass - self.conflict

    def conflict_mass(self) -> float:
        """Return conflict mass (higher = more contradictory evidence)."""
        return self.conflict

    def detect_contradiction(self, threshold: float = 0.5) -> bool:
        """Return True if evidence is highly contradictory (conflict > threshold)."""
        return self.conflict > threshold

    def to_dict(self) -> dict:
        """Serialize state."""
        return {
            'hypotheses': list(self.hypotheses),
            'masses': self.masses,
            'unknown': self.unknown,
            'conflict': self.conflict,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'DempsterShafer':
        """Restore from dict."""
        ds = cls(hypotheses=set(d.get('hypotheses', [])))
        ds.masses = dict(d.get('masses', {}))
        ds.unknown = d.get('unknown', 1.0)
        ds.conflict = d.get('conflict', 0.0)
        return ds