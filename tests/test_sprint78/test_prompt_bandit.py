"""Tests for prompt bandit - LinUCB, persistence, context vector, cold-start, A/B testing."""

import pytest


class TestPromptBanditInit:
    """Test bandit initialization."""

    def test_bandit_init_defaults(self):
        """Test bandit initializes with defaults."""
        from hledac.universal.brain.prompt_bandit import PromptBandit

        bandit = PromptBandit()
        assert bandit._alpha == 1.0
        assert bandit._lambda == 0.01
        assert bandit._d == 9


class TestPromptBanditContext:
    """Test context vector generation."""

    def test_context_vector_dimensions(self):
        """Test context vector has 9 dimensions."""
        from hledac.universal.brain.prompt_bandit import PromptBandit

        bandit = PromptBandit()
        ctx = bandit._get_context_vector()

        assert len(ctx) == 9


class TestPromptBanditLinUCB:
    """Test LinUCB algorithm."""

    @pytest.mark.asyncio
    async def test_select_returns_valid_index(self):
        """Test select returns valid variant index."""
        from hledac.universal.brain.prompt_bandit import PromptBandit

        bandit = PromptBandit()
        variants = ["v1", "v2", "v3", "v4"]

        idx = await bandit.select(variants)

        assert 0 <= idx < len(variants)

    @pytest.mark.asyncio
    async def test_select_cold_start(self):
        """Test cold start uses random selection."""
        from hledac.universal.brain.prompt_bandit import PromptBandit

        bandit = PromptBandit()
        variants = ["v1", "v2", "v3"]

        idx = await bandit.select(variants)

        assert idx in [0, 1, 2]

    @pytest.mark.asyncio
    async def test_update_counts(self):
        """Test update increments counts."""
        from hledac.universal.brain.prompt_bandit import PromptBandit

        bandit = PromptBandit()

        await bandit.update(0, 1.0)
        await bandit.update(0, 0.8)

        assert bandit._counts[0] == 2

    @pytest.mark.asyncio
    async def test_update_clip_rewards(self):
        """Test rewards are clipped to [0, 1]."""
        from hledac.universal.brain.prompt_bandit import PromptBandit

        bandit = PromptBandit()

        await bandit.update(0, 1.5)
        await bandit.update(1, -0.5)

        assert bandit._rewards[0] == 1.0
        assert bandit._rewards[1] == 0.0


class TestPromptBanditABTest:
    """Test A/B testing methods."""

    def test_start_ab_test(self):
        """Test starting A/B test."""
        from hledac.universal.brain.prompt_bandit import PromptBandit

        bandit = PromptBandit()
        bandit.start_ab_test([1, 2, 3], duration_hours=24)

        assert bandit._ab_test_active is True
        assert len(bandit._ab_test_variants) == 3

    def test_record_impression(self):
        """Test recording impression."""
        from hledac.universal.brain.prompt_bandit import PromptBandit

        bandit = PromptBandit()
        bandit.start_ab_test([1, 2])
        bandit.record_ab_impression(1)

        assert bandit._ab_test_variants[1]['impressions'] == 1


class TestPromptBanditIntegration:
    """Integration tests."""

    @pytest.mark.asyncio
    async def test_full_bandit_flow(self):
        """Test complete bandit flow."""
        from hledac.universal.brain.prompt_bandit import PromptBandit
        bandit = PromptBandit()

        variants = ["prompt_a", "prompt_b", "prompt_c"]

        idx = await bandit.select(variants)
        await bandit.update(idx, 0.8)

        assert bandit._counts[idx] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
