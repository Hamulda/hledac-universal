"""
Sprint 8TB probe tests — decompose_query identity fallback.
Sprint: 8TB
Area: Query Decomposer
"""

import pytest

from hledac.universal.brain.synthesis_runner import SynthesisRunner


class TestDecomposeQueryNoModel:
    """Without model/token, decompose_query returns [query] identity."""

    @pytest.mark.asyncio
    async def test_identity_fallback_returns_query_list(self):
        """model=None, tokenizer=None → [query]."""
        # Create a dummy lifecycle (not used in identity fallback path)
        from unittest.mock import MagicMock
        mock_lifecycle = MagicMock()
        runner = SynthesisRunner(mock_lifecycle)

        result = await runner.decompose_query("LockBit ransomware IOCs", None, None)

        assert result == ["LockBit ransomware IOCs"]

    @pytest.mark.asyncio
    async def test_empty_query_still_returns_list(self):
        """Empty query string → returns [''] (not empty list)."""
        from unittest.mock import MagicMock
        mock_lifecycle = MagicMock()
        runner = SynthesisRunner(mock_lifecycle)

        result = await runner.decompose_query("", None, None)

        assert result == [""]
