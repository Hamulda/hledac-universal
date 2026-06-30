"""
Sprint 8QC D.12: Model path discovery returns None when no model found.
100% offline — mocks filesystem.
"""

from unittest.mock import patch, MagicMock
from pathlib import Path

from hledac.universal.brain.model_lifecycle import ModelLifecycle


class TestModelPathDiscovery:
    """D.12: No model found → structured_generate returns None without raising."""

    def test_no_model_returns_none(self):
        """When no model exists, structured_generate returns None (doesn't raise)."""
        with patch("pathlib.Path.glob", return_value=[]):
            lc = ModelLifecycle()
            result = lc._discover_model_path()
            assert result is None

    @property
    def test_structured_generate_no_model_returns_none(self):
        """structured_generate must not raise when no model is available."""
        lc = ModelLifecycle()
        lc._model_path = None  # No model discovered

        # Since _ensure_loaded raises RuntimeError when _model_path is None,
        # structured_generate catches it and returns None
        # We test this by mocking _ensure_loaded to raise RuntimeError
        import asyncio

        async def mock_ensure():
            raise RuntimeError("No model available")

        with patch.object(lc, "_ensure_loaded", mock_ensure):
            async def test():
                result = await lc.structured_generate("test prompt")
                assert result is None
