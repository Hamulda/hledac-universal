"""
Beta-Binomial confidence estimator for dynamic belief updating.

Used in enrichment confidence policy — updates confidence after each new
evidence piece using Bayesian inference.

TEMPORAL DECAY & RETRACTION (APEX-1003):
    Maintains evidence log with timestamps for temporal decay and source retraction.
    Effective confidence decays exponentially over time: conf * exp(-λ * Δt_hours).

Usage:
    from hledac.universal.brain.confidence_utils import BetaBinomial
    bb = BetaBinomial(alpha=successes+1, beta=failures+1)
    confidence = bb.belief()

    # Add evidence with source tracking
    ev_id = bb.add_support(weight=1.0, source_id="source_a")

    # Retract a source
    bb.retract_source("source_a")

    # Apply temporal decay
    decayed_conf = bb.belief_with_decay(decay_lambda=0.01)
"""


import math
import time
import uuid

from hledac.universal.brain.jtms import BetaEvidenceRecord


class BetaBinomial:
    """
    Beta-Binomial Bayesian confidence estimator.

    After each enrichment result:
    - success: bb.add_support(weight)
    - contradiction: bb.add_contradict(weight)
    - confidence = bb.belief()

    TEMPORAL DECAY & RETRACTION: Maintains evidence log for source retraction
    and temporal decay. Evidence can be retracted by source_id, and confidence
    decays exponentially over time.
    """

    __slots__ = ('alpha', 'beta', '_evidence_log', '_source_index')

    def __init__(self, alpha: float = 1.0, beta: float = 1.0):
        self.alpha = alpha
        self.beta = beta
        # Evidence log for retraction and temporal decay
        self._evidence_log: list[BetaEvidenceRecord] = []
        self._source_index: dict[str, list[str]] = {}  # source_id → [evidence_ids]

    def add_support(self, weight: float = 1.0, source_id: str | None = None) -> str:
        """
        Add supporting evidence for current belief.

        Args:
            weight: Evidence weight (positive)
            source_id: Optional source identifier for retraction tracking

        Returns:
            evidence_id: Unique identifier for this evidence
        """
        evidence_id = f"bb_{uuid.uuid4().hex[:12]}"
        if source_id is None:
            source_id = "anonymous"

        # Log evidence
        record = BetaEvidenceRecord(
            evidence_id=evidence_id,
            weight=weight,
            source_id=source_id,
            timestamp=time.time(),
        )
        self._evidence_log.append(record)

        # Update source index
        if source_id not in self._source_index:
            self._source_index[source_id] = []
        self._source_index[source_id].append(evidence_id)

        # Apply to alpha
        self.alpha += weight
        return evidence_id

    def add_contradict(self, weight: float = 1.0, source_id: str | None = None) -> str:
        """
        Add contradicting evidence against current belief.

        Args:
            weight: Evidence weight (positive, will be added to beta)
            source_id: Optional source identifier for retraction tracking

        Returns:
            evidence_id: Unique identifier for this evidence
        """
        evidence_id = f"bb_{uuid.uuid4().hex[:12]}"
        if source_id is None:
            source_id = "anonymous"

        # Log evidence (store as negative weight for retraction)
        record = BetaEvidenceRecord(
            evidence_id=evidence_id,
            weight=-weight,  # Negative = contradicting
            source_id=source_id,
            timestamp=time.time(),
        )
        self._evidence_log.append(record)

        # Update source index
        if source_id not in self._source_index:
            self._source_index[source_id] = []
        self._source_index[source_id].append(evidence_id)

        # Apply to beta
        self.beta += weight
        return evidence_id

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

    def belief_with_decay(self, decay_lambda: float = 0.01, current_time: float | None = None) -> float:
        """
        Return belief with temporal decay applied.

        Applies exponential decay to each evidence piece based on age:
        effective_weight = weight * exp(-λ * Δt_hours)

        Args:
            decay_lambda: Decay rate per hour (default 0.01 = 1% per hour)
            current_time: Current time (defaults to now)

        Returns:
            Decayed belief score (0..1)
        """
        if current_time is None:
            current_time = time.time()

        # Recompute alpha/beta with decay
        decayed_alpha = 1.0  # Prior
        decayed_beta = 1.0   # Prior

        for evidence in self._evidence_log:
            delta_hours = (current_time - evidence.timestamp) / 3600.0
            if delta_hours < 0:
                delta_hours = 0
            decay_factor = math.exp(-decay_lambda * delta_hours)
            decayed_weight = evidence.weight * decay_factor

            if decayed_weight > 0:
                decayed_alpha += decayed_weight
            else:
                decayed_beta += abs(decayed_weight)

        # Compute mean
        s = decayed_alpha + decayed_beta
        return decayed_alpha / s if s > 0 else 0.5

    def retract_source(self, source_id: str) -> int:
        """
        Retract all evidence from a specific source.

        Finds all evidence pieces from source_id, removes them from the log,
        and recomputes alpha/beta.

        Args:
            source_id: Source identifier to retract

        Returns:
            retracted_count: Number of evidence pieces retracted
        """
        if source_id not in self._source_index:
            return 0

        evidence_ids = self._source_index[source_id].copy()
        retracted_count = 0

        # Remove all evidence from this source
        for evidence_id in evidence_ids:
            for idx, ev in enumerate(self._evidence_log):
                if ev.evidence_id == evidence_id:
                    evidence = self._evidence_log.pop(idx)
                    # Reverse the effect on alpha/beta
                    if evidence.weight > 0:
                        self.alpha -= evidence.weight
                    else:
                        self.beta -= abs(evidence.weight)
                    retracted_count += 1
                    break

        # Clean up source index
        if source_id in self._source_index:
            del self._source_index[source_id]

        # Ensure alpha/beta don't go below prior
        self.alpha = max(1.0, self.alpha)
        self.beta = max(1.0, self.beta)

        return retracted_count

    def retract_evidence(self, evidence_id: str) -> bool:
        """
        Retract a specific evidence piece.

        Args:
            evidence_id: Evidence identifier returned by add_support/add_contradict

        Returns:
            success: True if evidence was found and retracted
        """
        # Find evidence in log
        evidence_idx = None
        for idx, ev in enumerate(self._evidence_log):
            if ev.evidence_id == evidence_id:
                evidence_idx = idx
                break

        if evidence_idx is None:
            return False

        # Remove from log
        evidence = self._evidence_log.pop(evidence_idx)

        # Remove from source index
        if evidence.source_id in self._source_index:
            try:
                self._source_index[evidence.source_id].remove(evidence_id)
            except ValueError:
                pass

        # Reverse the effect on alpha/beta
        if evidence.weight > 0:
            self.alpha -= evidence.weight
        else:
            self.beta -= abs(evidence.weight)

        # Ensure alpha/beta don't go below prior
        self.alpha = max(1.0, self.alpha)
        self.beta = max(1.0, self.beta)

        return True

    def credible_interval(self, p: float = 0.95) -> tuple[float, float]:
        """Return credible interval (mean ± 2 std by default)."""
        std = math.sqrt(self.variance())
        lo = max(0.0, self.mean() - 2 * std)
        hi = min(1.0, self.mean() + 2 * std)
        return lo, hi

    def conflict(self) -> float:
        """Return conflict score (0..1) based on variance."""
        return min(1.0, self.variance() * 4)

    def to_dict(self) -> dict:
        """Serialize state including evidence log for retraction."""
        return {
            'alpha': self.alpha,
            'beta': self.beta,
            'evidence_log': [
                {
                    'evidence_id': ev.evidence_id,
                    'weight': ev.weight,
                    'source_id': ev.source_id,
                    'timestamp': ev.timestamp,
                }
                for ev in self._evidence_log
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> BetaBinomial:
        """Restore from dict including evidence log for retraction."""
        bb = cls(alpha=d.get('alpha', 1.0), beta=d.get('beta', 1.0))

        # Restore evidence log
        evidence_log_data = d.get('evidence_log', [])
        for ev_data in evidence_log_data:
            record = BetaEvidenceRecord(
                evidence_id=ev_data['evidence_id'],
                weight=ev_data['weight'],
                source_id=ev_data['source_id'],
                timestamp=ev_data['timestamp'],
            )
            bb._evidence_log.append(record)
            if record.source_id not in bb._source_index:
                bb._source_index[record.source_id] = []
            bb._source_index[record.source_id].append(record.evidence_id)

        return bb
