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

    _DSPY_AVAILABLE = True

except ImportError:
    AnalysisSignature = None  # type: ignore
    AnalysisChainOfThought = None  # type: ignore
    ExtractionSignature = None  # type: ignore
    SummarizationSignature = None  # type: ignore
    DarkQuerySignature = None  # type: ignore
    HypothesisSignature = None  # type: ignore
    _DSPY_AVAILABLE = False


def is_dspy_available() -> bool:
    """Check if DSPy runtime is available."""
    return _DSPY_AVAILABLE
