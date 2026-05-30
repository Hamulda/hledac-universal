"""
DSPy Signatures for OSINT hypothesis generation.

Minimal DSPy integration — fail-soft, no mandatory dependencies.
"""
from __future__ import annotations

# Fail-soft: DSPy is an optional extra. If not installed, this module is a no-op stub.
try:
    import dspy

    # -------------------------------------------------------------------------
    # Sprint F234: Typed OSINT signatures (replaces raw string templates)
    # -------------------------------------------------------------------------

    class AnalysisSignature(dspy.Signature):
        """OSINT analysis — extract entities, identify gaps, recommend sources, flag challenges."""
        query: str = dspy.InputField(desc="The OSINT research query or topic")
        entities: list[dict] = dspy.OutputField(desc="Key entities: list of {type, name, confidence}")
        gaps: list[str] = dspy.OutputField(desc="Information gaps and unanswered questions")
        sources: list[dict] = dspy.OutputField(desc="Recommended sources: list of {name, url, credibility}")
        challenges: list[str] = dspy.OutputField(desc="Verification challenges and risks")

    # B5: ChainOfThought augmentation — reasoning trace before structured output
    AnalysisChainOfThought = dspy.ChainOfThought(AnalysisSignature)

    class ExtractionSignature(dspy.Signature):
        """OSINT entity/relation extraction from content."""
        content: str = dspy.InputField(desc="Source text, document, or finding content")
        entities: list[dict] = dspy.OutputField(
            desc="Extracted entities: list of {type (person|org|location|date), name, confidence}"
        )
        relations: list[dict] = dspy.OutputField(
            desc="Extracted relations: list of {source, target, type, confidence}"
        )
        claims: list[dict] = dspy.OutputField(
            desc="Factual claims: list of {statement, source, confidence, contradictions}"
        )

    class SummarizationSignature(dspy.Signature):
        """OSINT summarization — concise synthesis of findings with confidence levels."""
        findings: str = dspy.InputField(desc="Raw OSINT findings, multiple sources")
        summary: str = dspy.OutputField(desc="Concise summary focusing on verified facts")
        confidence: float = dspy.OutputField(desc="Overall confidence 0.0-1.0")
        contested: list[str] = dspy.OutputField(desc="Contested or uncertain claims")

    class DarkQuerySignature(dspy.Signature):
        """OSINT dark surface query generation — produce search queries for unindexed sources."""
        context = dspy.InputField(desc="IOC findings, current sprint state, available transports")
        dark_queries = dspy.OutputField(
            desc="List of dark queries: type (onion|ipfs|paste|i2p), query string, priority 0-1"
        )

    class HypothesisSignature(dspy.Signature):
        """OSINT hypothesis generation — derive testable hypotheses from observation patterns."""
        findings = dspy.InputField(desc="CT findings, entity signals, timeline data")
        context = dspy.InputField(desc="Sprint metadata, profile type, confidence thresholds")
        hypotheses = dspy.OutputField(
            desc="List of hypotheses: type, statement, prior_probability, status"
        )

    # ─────────────────────────────────────────────────────────────────────────────
    # F260: Deep research chain signature for multi-hop reasoning
    # ─────────────────────────────────────────────────────────────────────────────

    class DeepResearchHopSignature(dspy.Signature):
        """Given a query and current evidence, decide what to research next and why."""

        query: str = dspy.InputField(desc="The OSINT research query or topic")
        current_evidence: list[str] = dspy.InputField(
            desc="Findings gathered so far (last 20 max)"
        )
        hop_number: int = dspy.InputField(desc="Current hop number (1 to max_hops)")

        next_query: str = dspy.OutputField(
            desc="Most promising next research direction to explore"
        )
        reasoning: str = dspy.OutputField(
            desc="Why this direction reduces epistemic uncertainty about the query"
        )
        confidence: float = dspy.OutputField(
            desc="Confidence this hop will yield new findings (0.0 to 1.0)"
        )

    # ChainOfThought wrapper for iterative reasoning
    DeepResearchChain = dspy.ChainOfThought(DeepResearchHopSignature)

    # Sprint F260: Epistemic Gap Detector — bridge DS evidence with DSPy
    class EpistemicGapDetector(dspy.Signature):
        """Given OSINT findings and prior gaps, identify what is unknown and must be investigated."""
        findings: list[str] = dspy.InputField(desc="Current sprint findings as text")
        known_gaps: list[str] = dspy.InputField(desc="Previously identified knowledge gaps")
        query: str = dspy.InputField(desc="Research query")
        gaps: list[str] = dspy.OutputField(desc="Prioritized list of unanswered questions")
        evidence_needed: list[str] = dspy.OutputField(
            desc="Specific evidence types needed to fill gaps"
        )
        confidence: float = dspy.OutputField(desc="Confidence that these gaps are real (0-1)")

    _DSPY_AVAILABLE = True

except ImportError:
    AnalysisSignature = None  # type: ignore
    AnalysisChainOfThought = None  # type: ignore
    ExtractionSignature = None  # type: ignore
    SummarizationSignature = None  # type: ignore
    DarkQuerySignature = None  # type: ignore
    HypothesisSignature = None  # type: ignore
    DeepResearchHopSignature = None  # type: ignore
    DeepResearchChain = None  # type: ignore
    EpistemicGapDetector = None  # type: ignore
    _DSPY_AVAILABLE = False


def is_dspy_available() -> bool:
    """Check if DSPy runtime is available."""
    return _DSPY_AVAILABLE
