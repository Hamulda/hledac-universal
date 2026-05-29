"""
Tests for structured output generation with retry (Sprint 75).
"""

import asyncio
from unittest.mock import AsyncMock

import pytest


class TestStructuredOutput:
    """Test structured output with retry."""

    def test_generate_structured_exists(self):
        """Test generate_structured method exists."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        engine = Hermes3Engine()
        assert hasattr(engine, 'generate_structured')
        assert asyncio.iscoroutinefunction(engine.generate_structured)

    @pytest.mark.asyncio
    async def test_generate_structured_fallback(self):
        """Test structured output fallback on failure."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            name: str = "test"
            value: int = 0

        engine = Hermes3Engine()
        engine._model = None  # Force fallback path
        engine._outlines_model = None

        # Mock generate to avoid RuntimeError
        engine.generate = AsyncMock(return_value='{"name": "test", "value": 1}')

        result = await engine.generate_structured(
            "test prompt",
            TestSchema,
            max_retries=1
        )

        assert isinstance(result, TestSchema)

    @pytest.mark.asyncio
    async def test_generate_structured_max_retries_param(self):
        """Test max_retries parameter is accepted."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        from pydantic import BaseModel

        class TestSchema(BaseModel):
            name: str

        engine = Hermes3Engine()
        engine._model = None
        engine._outlines_model = None

        # Mock generate to avoid RuntimeError
        engine.generate = AsyncMock(return_value='{"name": "test"}')

        # Should accept max_retries
        result = await engine.generate_structured(
            "test prompt",
            TestSchema,
            max_retries=3
        )

        assert isinstance(result, TestSchema)


class TestJSONRetry:
    """Test JSON parsing retry logic."""

    def test_retry_param_exists(self):
        """Test max_retries parameter is in signature."""
        import inspect

        from hledac.universal.brain.hermes3_engine import Hermes3Engine

        sig = inspect.signature(Hermes3Engine.generate_structured)
        params = list(sig.parameters.keys())
        assert 'max_retries' in params


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
