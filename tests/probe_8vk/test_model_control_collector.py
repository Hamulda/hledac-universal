"""
Probe: Model/control facts can be collected from AnalyzerResult.

Sprint 8VK §Invariant: model/control facts can be collected from AnalyzerResult
(typed) or AutoResearchProfile.to_dict() (compat) without scheduler dependency.
"""

import pytest
from unittest.mock import MagicMock


class TestModelControlFactsCollector:
    """Verify model/control facts collection works independently of scheduler."""

    def test_analyzer_result_typed_path(self):
        """AnalyzerResult (typed) can be collected."""
        from hledac.universal.project_types import AnalyzerResult
        from hledac.universal.runtime.shadow_inputs import collect_model_control_facts

        result = AnalyzerResult(
            tools={"web_search", "stealth_crawler"},
            sources={"surface", "archive"},
            privacy_level="HIGH",
            use_tor=True,
            models_needed={"hermes", "modernbert"},
            depth="DEEP",
            use_tot=True,
            tot_mode="hybrid",
            reasoning="Complex query needs ToT",
        )

        bundle = collect_model_control_facts(analyzer_result=result)

        assert set(bundle.tools) == {"web_search", "stealth_crawler"}
        assert set(bundle.sources) == {"surface", "archive"}
        assert bundle.privacy_level == "HIGH"
        assert bundle.use_tor is True
        assert bundle.depth == "DEEP"
        assert bundle.use_tot is True
        assert bundle.tot_mode == "hybrid"
        assert "hermes" in bundle.models_needed
        assert "modernbert" in bundle.models_needed

    def test_raw_profile_compat_path(self):
        """AutoResearchProfile.to_dict() (compat) can be collected."""
        from hledac.universal.runtime.shadow_inputs import collect_model_control_facts

        raw_profile = {
            "tools": ["google_search", "dns_lookup"],
            "sources": ["dark"],
            "privacy_level": "MAXIMUM",
            "use_tor": False,
            "depth": "EXHAUSTIVE",
            "use_tot": False,
            "tot_mode": "standard",
            "models_needed": ["hermes"],
        }

        bundle = collect_model_control_facts(raw_profile=raw_profile)

        assert set(bundle.tools) == {"google_search", "dns_lookup"}
        assert bundle.privacy_level == "MAXIMUM"
        assert bundle.use_tot is False
        assert bundle.models_needed == ["hermes"]

    def test_none_inputs_returns_empty_bundle(self):
        """None inputs return empty bundle without crashing."""
        from hledac.universal.runtime.shadow_inputs import collect_model_control_facts

        bundle = collect_model_control_facts()

        assert bundle.tools == []
        assert bundle.sources == []
        assert bundle.privacy_level == "STANDARD"
        assert bundle.use_tot is False
        assert bundle.models_needed == []

    def test_analyzer_result_to_capability_signal(self):
        """AnalyzerResult.to_capability_signal() is called correctly."""
        from hledac.universal.project_types import AnalyzerResult
        from hledac.universal.runtime.shadow_inputs import collect_model_control_facts

        result = AnalyzerResult(
            tools={"modernbert", "gliner"},
            sources=set(),
            privacy_level="STANDARD",
            use_tor=False,
            models_needed={"modernbert", "gliner"},
            depth="STANDARD",
            use_tot=False,
            tot_mode="standard",
        )

        bundle = collect_model_control_facts(analyzer_result=result)

        # capability signal should indicate embedding and NER needs
        assert bundle.requires_embeddings is True
        assert bundle.requires_ner is True
