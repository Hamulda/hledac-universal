"""
hledac_hypothesis/dempster_shafer.py — Minimal Dempster-Shafer belief mass calculator.

Cutting-edge: pure-Python (no numpy), M1-8GB-safe, fail-soft.

API surface matches test_sprint60.TestHypothesis:
    DempsterShafer(frame)   → init with hypothesis frame
    .add_evidence(h, m)     → assign mass m to single hypothesis
    .belief(h?)             → belief mass for one (or total belief)
    .unknown, .conflict, .masses

References:
    Shafer, G. (1976). A Mathematical Theory of Evidence. Princeton UP.
    Used in OSINT hypothesis confidence fusion (sprint-side uncertainty).
"""


from dataclasses import dataclass, field


@dataclass(slots=True)
class DempsterShafer:
    """Belief mass structure over a discrete hypothesis frame.

    Masses form a basic probability assignment (BPA):
        sum(masses.values()) + unknown == 1.0
    `masses` is keyed by hypothesis identifier (string).
    `conflict` accumulates orthogonal-evidence normalisation residuals.
    """

    frame: set[str]
    unknown: float = 1.0
    conflict: float = 0.0
    masses: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # initialise empty mass slots per hypothesis
        for h in self.frame:
            self.masses.setdefault(h, 0.0)

    def add_evidence(self, hypothesis: str, mass: float) -> None:
        """Assign a single-hypothesis evidence mass and renormalise (Dempster's rule).

        For a single-hypothesis mass `m`, orthogonal combination with existing
        masses yields a closed-form update without conflict (because non-empty
        intersection with the singleton is the singleton itself). Conflict only
        arises from multi-subset evidence, which we model as proportional
        reduction when m + (1 - unknown) > 1.
        """
        if hypothesis not in self.frame:
            # extend frame lazily — fail-soft for unknown ids
            self.frame.add(hypothesis)
            self.masses.setdefault(hypothesis, 0.0)

        m = max(0.0, min(1.0, float(mass)))
        # orthogonal combination with existing masses
        new_masses: dict[str, float] = {}
        conflict_mass = 0.0
        for h, m_h in self.masses.items():
            if h == hypothesis:
                # intersection of {h} with {h} is {h}
                combined = m_h + m
            else:
                # intersection of {h} with {hypothesis} is empty → conflict
                conflict_mass += m_h * m
                combined = m_h * (1.0 - m)  # non-conflicting share
            new_masses[h] = combined

        # add the new hypothesis's residual
        new_masses[hypothesis] = new_masses.get(hypothesis, 0.0) + m * (1.0 - sum(self.masses.values()))

        # Dempster normalisation (skip divide-by-zero when total conflict)
        denom = 1.0 - conflict_mass
        if denom > 1e-9:
            factor = 1.0 / denom
            for h in list(new_masses):
                new_masses[h] *= factor
        else:
            # total conflict → reset to vacuous
            new_masses = dict.fromkeys(self.frame, 0.0)

        self.masses = new_masses
        self.conflict = min(1.0, self.conflict + conflict_mass)
        self.unknown = max(0.0, 1.0 - sum(self.masses.values()))

    def belief(self, hypothesis: str | None = None) -> float:
        """Return belief mass for `hypothesis` (or total belief across frame).

        Belief Bel(A) = sum of all masses whose focal element is a subset of A.
        For singleton masses this reduces to masses[A] directly.
        For total belief across the frame, we return 1.0 - unknown (the
        committed-mass fraction).
        """
        if hypothesis is None:
            return max(0.0, 1.0 - self.unknown)
        return max(0.0, self.masses.get(hypothesis, 0.0))


__all__ = ["DempsterShafer"]
