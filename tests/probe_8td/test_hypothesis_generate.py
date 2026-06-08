"""Sprint 8TD: HypothesisEngine generate_sprint_hypotheses tests."""
from unittest.mock import MagicMock


class TestHypothesisGenerate:
    """Test generate_sprint_hypotheses."""

    def test_generate_returns_list(self):
        """generate_sprint_hypotheses returns a list."""
        from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine.__new__(HypothesisEngine)
        engine._hypotheses = {}
        engine._evidence = MagicMock()

        result = engine.generate_sprint_hypotheses(["finding1", "finding2"], None, max_hypotheses=3)

        assert isinstance(result, list)

    def test_max_3_hypotheses(self):
        """generate_sprint_hypotheses returns max 3 hypotheses."""
        from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine.__new__(HypothesisEngine)
        engine._hypotheses = {}
        engine._evidence = MagicMock()

        findings = ["finding1", "finding2", "finding3", "finding4", "finding5"]
        result = engine.generate_sprint_hypotheses(findings, None, max_hypotheses=3)

        assert len(result) <= 3

    def test_hypothesis_format(self):
        """Each hypothesis contains IF or confidence substring."""
        from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine.__new__(HypothesisEngine)
        engine._hypotheses = {}
        engine._evidence = MagicMock()

        findings = ["malware detected on server"]
        result = engine.generate_sprint_hypotheses(findings, None, max_hypotheses=3)

        for h in result:
            assert "IF" in h or "confidence" in h or "THEN" in h

    def test_empty_findings_returns_empty(self):
        """Empty findings list returns empty list (no exception)."""
        from hledac.universal.brain.research_hypothesis_engine import HypothesisEngine

        engine = HypothesisEngine.__new__(HypothesisEngine)
        engine._hypotheses = {}
        engine._evidence = MagicMock()

        result = engine.generate_sprint_hypotheses([], None, max_hypotheses=3)

        assert result == []
