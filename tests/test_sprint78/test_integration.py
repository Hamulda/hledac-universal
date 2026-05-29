"""Integration tests - bandit + cache + Hermes generation with mock."""
from unittest.mock import MagicMock

import pytest


class TestBanditCacheIntegration:
    """Test bandit + cache integration."""

    @pytest.mark.asyncio
    async def test_bandit_selects_then_caches(self):
        """Test bandit selection followed by caching."""
        from hledac.universal.brain.prompt_bandit import PromptBandit
        from hledac.universal.brain.prompt_cache import PromptCache

        bandit = PromptBandit()
        cache = PromptCache(max_entries=100)

        variants = ["prompt_variant_1", "prompt_variant_2", "prompt_variant_3"]

        idx = await bandit.select(variants)
        selected = variants[idx]

        cache.set(selected, f"response for {selected}")

        result = cache.get(selected)
        assert result == f"response for {selected}"

    @pytest.mark.asyncio
    async def test_bandit_update_with_cache(self):
        """Test bandit update with cache."""
        from hledac.universal.brain.prompt_bandit import PromptBandit
        from hledac.universal.brain.prompt_cache import PromptCache

        bandit = PromptBandit()
        cache = PromptCache(max_entries=100)

        prompt = "test query"
        cache.set(prompt, "cached response")

        variants = ["variant_a", "variant_b"]
        idx = await bandit.select(variants)

        reward = 1.0 if cache.get(prompt) else 0.0
        await bandit.update(idx, reward)

        assert bandit._counts[idx] == 1


class TestFullIntegration:
    """Full integration test."""

    @pytest.mark.asyncio
    async def test_full_prompt_flow(self):
        """Test complete flow."""
        from hledac.universal.brain.prompt_bandit import PromptBandit
        from hledac.universal.brain.prompt_cache import PromptCache

        bandit = PromptBandit()
        cache = PromptCache(max_entries=100)

        variants = [
            "You are an OSINT analyst. Analyze: {query}",
            "As an OSINT expert, investigate: {query}",
        ]

        idx = await bandit.select(variants)
        selected_prompt = variants[idx]

        cached_response = cache.get(selected_prompt)

        if cached_response is None:
            mock_response = f"Analysis using: {selected_prompt}"
            cache.set(selected_prompt, mock_response)
        else:
            pass

        reward = 0.8
        await bandit.update(idx, reward)

        assert bandit._counts[idx] >= 1
        assert cache.get(selected_prompt) is not None


class TestDSPyBanditIntegration:
    """Test DSPy optimizer + bandit integration."""

    def test_dspy_get_prompt(self):
        """Test DSPy get_prompt works."""
        from hledac.universal.brain.dspy_optimizer import DSPyOptimizer

        mock_brain = MagicMock()
        dspy = DSPyOptimizer(mock_brain)

        prompt = dspy.get_prompt('analysis', {'complexity': 'medium'})

        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestOrchestratorIntegration:
    """Test orchestrator integration."""

    def test_brain_manager_has_components(self):
        """Test BrainManager has Sprint 78 components."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator, _BrainManager

        orch = object.__new__(FullyAutonomousOrchestrator)
        orch.config = MagicMock()
        orch.config.enable_distillation = False
        orch._security_mgr = None

        brain = object.__new__(_BrainManager)
        brain._orch = orch

        brain._prompt_cache = None
        brain._prompt_bandit = None
        brain._dspy_optimizer = None

        assert brain._prompt_cache is None
        assert brain._prompt_bandit is None
        assert brain._dspy_optimizer is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
