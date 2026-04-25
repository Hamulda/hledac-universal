"""
Sprint F203D: Evidence Chain Tracker

Tracks reasoning path from raw finding through entity extraction, attribution,
kill-chain tagging, to pivot suggestion. Analytik must explain "proč tomu věříme".

Chain is derived at sprint teardown from in-memory sidecar results and finding
metadata — NOT stored as a separate persistent entity. Chain refs are serialized
in envelope/payload_text. Full derived-chain findings go through
async_ingest_findings_batch() only.

No new storage path. No new LMDB databases.

Bounds:
  MAX_CHAIN_DEPTH=10
  MAX_CHAINS_PER_SPRINT=100
  MAX_CHAIN_JSON_BYTES=4098

M1 safe: pure Python, no model load, no JS renderer.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "ChainStep",
    "EvidenceChain",
    "EvidenceChainBuilder",
    "MAX_CHAIN_DEPTH",
    "MAX_CHAINS_PER_SPRINT",
    "MAX_CHAIN_JSON_BYTES",
    "serialize_chain",
    "deserialize_chain",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bounds
# ---------------------------------------------------------------------------

MAX_CHAIN_DEPTH: int = 10
MAX_CHAINS_PER_SPRINT: int = 100
MAX_CHAIN_JSON_BYTES: int = 4098

# Step types emitted by each sidecar
STEP_TYPE_INGEST = "finding_ingest"
STEP_TYPE_IDENTITY = "identity_stitching"
STEP_TYPE_EXPOSURE = "exposure_correlation"
STEP_TYPE_LEAK = "leak_sentinel"
STEP_TYPE_TEMPORAL = "temporal_archaeology"
STEP_TYPE_DIFF = "sprint_diff"
STEP_TYPE_KILLCHAIN = "kill_chain_tagging"
STEP_TYPE_EVIDENCE_TRIAGE = "evidence_triage"
STEP_TYPE_ATTRIBUTION = "attribution_scoring"
STEP_TYPE_PIVOT = "pivot_planning"


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ChainStep:
    """
    Single step in an evidence chain.

    Fields:
        step_type:   Semantic label for the processing step that produced this step.
                     Values: finding_ingest | identity_stitching | exposure_correlation |
                             leak_sentinel | temporal_archaeology | sprint_diff |
                             kill_chain_tagging | evidence_triage | attribution_scoring |
                             pivot_planning
        input_ids:   List of finding_id strings that fed into this step.
        output_id:   Single finding_id produced by this step.
        confidence:  Confidence score [0.0, 1.0] for this step's output.
        reason:      Human-readable explanation of WHY this step produced its output.
    """
    step_type: str
    input_ids: list[str]
    output_id: str
    confidence: float
    reason: str


@dataclass
class EvidenceChain:
    """
    Complete reasoning chain for a root finding.

    Fields:
        root_finding_id:  The original raw finding that started this chain.
        steps:            Ordered list of ChainStep from root to conclusion.
                          steps[0].output_id == root_finding_id.
        conclusion:       Optional human-readable summary of the chain's conclusion,
                          or None if chain ends at a derived finding with no conclusion.
    """
    root_finding_id: str
    steps: list[ChainStep] = field(default_factory=list)
    conclusion: Optional[str] = None

    def add_step(self, step: ChainStep) -> None:
        """Add a step to the chain. Silently drops if MAX_CHAIN_DEPTH reached."""
        if len(self.steps) < MAX_CHAIN_DEPTH:
            self.steps.append(step)

    @property
    def depth(self) -> int:
        """Number of steps in the chain."""
        return len(self.steps)

    @property
    def is_empty(self) -> bool:
        """True if chain has no steps."""
        return len(self.steps) == 0


# ---------------------------------------------------------------------------
# Builder — accumulates chain steps during sprint teardown
# ---------------------------------------------------------------------------

class EvidenceChainBuilder:
    """
    Accumulates chain steps from sidecar runs into EvidenceChain objects.

    Usage:
        builder = EvidenceChainBuilder()
        builder.record_step(root_finding_id, STEP_TYPE_IDENTITY, ["f1", "f2"], "f3-id", 0.85, "linked via email+username")
        chain = builder.build(root_finding_id)
    """

    def __init__(self) -> None:
        # finding_id → EvidenceChain
        self._chains: dict[str, EvidenceChain] = {}
        # Stats
        self._total_steps: int = 0

    # ── Recording ──────────────────────────────────────────────────────────

    def record_step(
        self,
        root_finding_id: str,
        step_type: str,
        input_ids: list[str],
        output_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        """
        Record a processing step into the chain for root_finding_id.

        If no chain exists for root_finding_id, one is created with the root
        as the first (ingest) step. Subsequent calls add derivative steps.

        Silently drops steps once MAX_CHAIN_DEPTH or MAX_CHAINS_PER_SPRINT is reached.
        """
        if self._total_steps >= MAX_CHAINS_PER_SPRINT * MAX_CHAIN_DEPTH:
            return  # Hard cap on total work

        if len(self._chains) >= MAX_CHAINS_PER_SPRINT:
            return  # Hard cap on chains

        chain = self._chains.get(root_finding_id)
        if chain is None:
            chain = EvidenceChain(root_finding_id=root_finding_id)
            self._chains[root_finding_id] = chain

        if len(chain.steps) >= MAX_CHAIN_DEPTH:
            return  # Depth cap

        step = ChainStep(
            step_type=step_type,
            input_ids=list(input_ids),
            output_id=output_id,
            confidence=confidence,
            reason=reason,
        )
        chain.add_step(step)
        self._total_steps += 1

    def record_ingest(self, finding_id: str, confidence: float, reason: str) -> None:
        """Convenience: record the ingest step for a root finding."""
        self.record_step(
            root_finding_id=finding_id,
            step_type=STEP_TYPE_INGEST,
            input_ids=[],
            output_id=finding_id,
            confidence=confidence,
            reason=reason,
        )

    def record_identity(
        self,
        root_finding_id: str,
        input_ids: list[str],
        output_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Convenience: record an identity stitching step."""
        self.record_step(
            root_finding_id=root_finding_id,
            step_type=STEP_TYPE_IDENTITY,
            input_ids=input_ids,
            output_id=output_id,
            confidence=confidence,
            reason=reason,
        )

    def record_attribution(
        self,
        root_finding_id: str,
        input_ids: list[str],
        output_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Convenience: record an attribution scoring step."""
        self.record_step(
            root_finding_id=root_finding_id,
            step_type=STEP_TYPE_ATTRIBUTION,
            input_ids=input_ids,
            output_id=output_id,
            confidence=confidence,
            reason=reason,
        )

    def record_exposure(
        self,
        root_finding_id: str,
        input_ids: list[str],
        output_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Convenience: record an exposure correlation step."""
        self.record_step(
            root_finding_id=root_finding_id,
            step_type=STEP_TYPE_EXPOSURE,
            input_ids=input_ids,
            output_id=output_id,
            confidence=confidence,
            reason=reason,
        )

    def record_leak(
        self,
        root_finding_id: str,
        input_ids: list[str],
        output_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Convenience: record a leak sentinel step."""
        self.record_step(
            root_finding_id=root_finding_id,
            step_type=STEP_TYPE_LEAK,
            input_ids=input_ids,
            output_id=output_id,
            confidence=confidence,
            reason=reason,
        )

    def record_temporal(
        self,
        root_finding_id: str,
        input_ids: list[str],
        output_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Convenience: record a temporal archaeology step."""
        self.record_step(
            root_finding_id=root_finding_id,
            step_type=STEP_TYPE_TEMPORAL,
            input_ids=input_ids,
            output_id=output_id,
            confidence=confidence,
            reason=reason,
        )

    def record_diff(
        self,
        root_finding_id: str,
        input_ids: list[str],
        output_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Convenience: record a sprint diff step."""
        self.record_step(
            root_finding_id=root_finding_id,
            step_type=STEP_TYPE_DIFF,
            input_ids=input_ids,
            output_id=output_id,
            confidence=confidence,
            reason=reason,
        )

    def record_killchain(
        self,
        root_finding_id: str,
        input_ids: list[str],
        output_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Convenience: record a kill chain tagging step."""
        self.record_step(
            root_finding_id=root_finding_id,
            step_type=STEP_TYPE_KILLCHAIN,
            input_ids=input_ids,
            output_id=output_id,
            confidence=confidence,
            reason=reason,
        )

    def record_evidence_triage(
        self,
        root_finding_id: str,
        input_ids: list[str],
        output_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Convenience: record an evidence triage step."""
        self.record_step(
            root_finding_id=root_finding_id,
            step_type=STEP_TYPE_EVIDENCE_TRIAGE,
            input_ids=input_ids,
            output_id=output_id,
            confidence=confidence,
            reason=reason,
        )

    def record_pivot(
        self,
        root_finding_id: str,
        input_ids: list[str],
        output_id: str,
        confidence: float,
        reason: str,
    ) -> None:
        """Convenience: record a pivot planning step."""
        self.record_step(
            root_finding_id=root_finding_id,
            step_type=STEP_TYPE_PIVOT,
            input_ids=input_ids,
            output_id=output_id,
            confidence=confidence,
            reason=reason,
        )

    # ── Building ───────────────────────────────────────────────────────────

    def build(self, root_finding_id: str) -> EvidenceChain | None:
        """Return the chain for root_finding_id, or None if not tracked."""
        return self._chains.get(root_finding_id)

    def build_all(self) -> list[EvidenceChain]:
        """Return all chains, newest-first by root_finding_id sort."""
        return list(self._chains.values())

    def get_chain_count(self) -> int:
        """Number of chains currently tracked."""
        return len(self._chains)

    def get_total_steps(self) -> int:
        """Total steps recorded across all chains."""
        return self._total_steps


# ---------------------------------------------------------------------------
# Module-level singleton registry — set by SprintScheduler at teardown
# ---------------------------------------------------------------------------

# Global builder instance — populated during sprint teardown
_global_builder: EvidenceChainBuilder | None = None


def get_global_builder() -> EvidenceChainBuilder:
    """Get or create the global EvidenceChainBuilder singleton."""
    global _global_builder
    if _global_builder is None:
        _global_builder = EvidenceChainBuilder()
    return _global_builder


def set_global_builder(builder: EvidenceChainBuilder) -> None:
    """Set the global EvidenceChainBuilder (called at sprint teardown)."""
    global _global_builder
    _global_builder = builder


def _get_chain_for_finding(finding_id: str) -> EvidenceChain | None:
    """
    Retrieve the chain containing the given finding_id.

    Searches all chains in the global builder for one where the finding_id
    appears as root_finding_id or as any step's output_id.
    """
    global _global_builder
    if _global_builder is None:
        return None

    # Direct lookup by root
    chain = _global_builder.build(finding_id)
    if chain is not None:
        return chain

    # Search through all chains for a step that produces this finding
    for chain in _global_builder.build_all():
        for step in chain.steps:
            if step.output_id == finding_id:
                return chain

    return None


def get_all_chains() -> list[EvidenceChain]:
    """Return all chains from the global builder."""
    global _global_builder
    if _global_builder is None:
        return []
    return _global_builder.build_all()


def reset_global_builder() -> None:
    """Reset the global builder (called at sprint start)."""
    global _global_builder
    _global_builder = None


# ---------------------------------------------------------------------------
# Serialization helpers — stored in envelope/payload_text
# ---------------------------------------------------------------------------

def _chain_to_dict(chain: EvidenceChain) -> dict:
    return {
        "root_finding_id": chain.root_finding_id,
        "steps": [
            {
                "step_type": s.step_type,
                "input_ids": s.input_ids,
                "output_id": s.output_id,
                "confidence": s.confidence,
                "reason": s.reason,
            }
            for s in chain.steps
        ],
        "conclusion": chain.conclusion,
    }


def _dict_to_chain(d: dict) -> EvidenceChain:
    steps = [
        ChainStep(
            step_type=s["step_type"],
            input_ids=s["input_ids"],
            output_id=s["output_id"],
            confidence=s["confidence"],
            reason=s["reason"],
        )
        for s in d.get("steps", [])
    ]
    chain = EvidenceChain(
        root_finding_id=d["root_finding_id"],
        steps=steps,
        conclusion=d.get("conclusion"),
    )
    return chain


def serialize_chain(chain: EvidenceChain) -> str | None:
    """
    Serialize EvidenceChain to JSON string for storage in payload_text/envelope.

    Returns None if serialization fails OR if result exceeds MAX_CHAIN_JSON_BYTES.
    """
    if chain.is_empty:
        return None
    try:
        import orjson
        raw = orjson.dumps(_chain_to_dict(chain))
    except Exception:
        try:
            raw = json.dumps(
                _chain_to_dict(chain),
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        except Exception:
            logger.warning("[EVIDENCE_CHAIN] serialize failed")
            return None

    if len(raw) > MAX_CHAIN_JSON_BYTES:
        logger.warning(
            "[EVIDENCE_CHAIN] chain size %d exceeds MAX_CHAIN_JSON_BYTES %d",
            len(raw),
            MAX_CHAIN_JSON_BYTES,
        )
        return None

    try:
        return raw.decode("utf-8")
    except Exception:
        return None


def deserialize_chain(payload_text: str | None) -> EvidenceChain | None:
    """
    Deserialize EvidenceChain from JSON string in payload_text.

    Returns None if payload_text is None/empty or parsing fails.
    """
    if not payload_text:
        return None
    try:
        import orjson
        d = orjson.loads(payload_text)
    except Exception:
        try:
            d = json.loads(payload_text)
        except Exception:
            return None

    if not isinstance(d, dict):
        return None
    if "root_finding_id" not in d:
        return None

    try:
        return _dict_to_chain(d)
    except Exception:
        logger.warning("[EVIDENCE_CHAIN] deserialize failed for payload_text")
        return None
