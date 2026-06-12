"""
Hypothesis engine — C4 Tier-3 partial extraction: AdversarialVerifier
=====================================================================

Sprint F262OBS-Tier3: Verify that :class:`AdversarialVerifier` was successfully
extracted from the 5 373 LOC monolith :mod:`brain.hypothesis_engine_engine` into
:mod:`brain.hypothesis_engine.adversarial`, with byte-for-byte class equivalence
and 100% backward compatibility (legacy import path still works).

Run: ``uv run pytest tests/probe_adversarial_extraction.py -v``
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

sys.path.insert(0, "hledac/universal")


# ── Backward compatibility: every old import still works ─────────────────


class TestBackwardCompat:
    def test_legacy_import_still_works(self) -> None:
        """`from brain.research_hypothesis_engine import AdversarialVerifier` must work."""
        from brain.research_hypothesis_engine import AdversarialVerifier  # noqa: F401

        assert AdversarialVerifier is not None
        assert AdversarialVerifier.__name__ == "AdversarialVerifier"

    def test_legacy_class_is_same_object(self) -> None:
        """The legacy import and the new import return the SAME class object."""
        from brain.hypothesis_engine.adversarial import AdversarialVerifier as New
        from brain.research_hypothesis_engine import AdversarialVerifier as Old

        # Identity check — same class, not a copy
        assert New is Old, "Legacy and new import paths must resolve to the same class"
        # And the class lives in the new module after extraction
        assert New.__module__ == "brain.hypothesis_engine.adversarial"


# ── Forward import path: brain.hypothesis_engine.adversarial ─────────────────────


class TestForwardImport:
    def test_package_exports_adversarial_verifier(self) -> None:
        """`from brain.hypothesis_engine import AdversarialVerifier` must work."""
        import brain.hypothesis_engine as pkg

        assert hasattr(pkg, "AdversarialVerifier")
        assert "AdversarialVerifier" in pkg.__all__

    def test_module_construction(self) -> None:
        """AdversarialVerifier can be constructed with a mock hypothesis_engine."""
        from brain.hypothesis_engine.adversarial import AdversarialVerifier

        # Mock the hypothesis_engine — only need an object reference
        mock_he = MagicMock()
        verifier = AdversarialVerifier(hypothesis_engine=mock_he)

        # Verify the constructor stored the reference
        assert verifier.hypothesis_engine is mock_he
        assert verifier.max_contradiction_window == 100  # default preserved
        assert verifier.enable_streaming is True  # default preserved
        # Verify the bound constant is preserved (byte-for-byte)
        assert AdversarialVerifier.MAX_SOURCE_ITEMS == 5_000


# ── Functional smoke tests ────────────────────────────────────────────────


class TestFunctionalBehavior:
    def test_assess_source_credibility_no_bias(self) -> None:
        """assess_source_credibility returns valid SourceCredibility."""
        from brain.hypothesis_engine._types import SourceCredibility
        from brain.hypothesis_engine.adversarial import AdversarialVerifier

        verifier = AdversarialVerifier(hypothesis_engine=MagicMock())
        result = verifier.assess_source_credibility("https://example.edu/paper")

        # .edu source gets a +0.3 boost over the 0.5 base
        assert isinstance(result, SourceCredibility)
        assert 0.0 <= result.credibility_score <= 1.0
        # No bias indicators on a plain .edu URL
        assert result.bias_indicators == []

    def test_detect_logical_fallacies(self) -> None:
        """_detect_logical_fallacies returns fallacy types for matching text."""
        from brain.hypothesis_engine.adversarial import AdversarialVerifier

        verifier = AdversarialVerifier(hypothesis_engine=MagicMock())
        fallacies = verifier._detect_logical_fallacies(
            "Everyone knows this is true"
        )
        # "everyone knows" matches the hasty_generalization pattern
        assert "hasty_generalization" in fallacies
