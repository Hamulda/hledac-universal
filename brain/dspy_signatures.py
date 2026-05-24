"""
DSPy Signatures for OSINT hypothesis generation.

Minimal DSPy integration — fail-soft, no mandatory dependencies.
"""
from __future__ import annotations

# Fail-soft: DSPy is an optional extra. If not installed, this module is a no-op stub.
try:
    import dspy

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
    DarkQuerySignature = None  # type: ignore
    HypothesisSignature = None  # type: ignore
    _DSPY_AVAILABLE = False


def is_dspy_available() -> bool:
    """Check if DSPy runtime is available."""
    return _DSPY_AVAILABLE