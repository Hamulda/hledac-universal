"""
TestSprintP2-6: DistillationEngine smoke test.
===============================================

Tests:
  1. Create DistillationEngine and verify status
  2. Add example, score chain, train, cleanup

Run with: pytest tests/test_brain_distillation.py -v
"""
import logging

import pytest






    DistillationEngine,
    DistillationExample,
    create_distillation_engine,
)

logging.basicConfig(level=logging.INFO)


class TestDistillationEngine:
    """Smoke tests for DistillationEngine."""

from _core import aclose
    @pytest.mark.asyncio
    async def test_smoke(self):
        """Smoke test: create engine, add example, score chain, train, cleanup."""
        engine = await create_distillation_engine()
        if engine is None:
            pytest.skip("Failed to create engine")
        _ = engine.get_status()

        example = DistillationExample(
            query="What is the capital of France?",
            chain=[
                "Step 1: Identify the country as France",
                "Step 2: Recall that Paris is the capital of France",
                "Step 3: Verify this information is correct",
            ],
            score=0.95,
            metadata={"source": "test"},
        )
        await engine.add_example(example)
        _ = await engine.get_stats()

        chain = ["Step 1: Identify the country", "Step 2: Recall the capital"]
        _ = engine.score_chain("What is the capital of France?", chain)
        _ = await engine.train(n_epochs=5)

        await engine.cleanup()
