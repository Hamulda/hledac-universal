"""
Sprint 8TB probe tests — decompose_query parses MLX output.
Sprint: 8TB
Area: Query Decomposer
"""

from unittest.mock import MagicMock, patch

import pytest

from hledac.universal.brain.synthesis_runner import SynthesisRunner


class TestDecomposeQueryParsesArray:
    """MLX generate returns JSON array → decompose_query parses it correctly."""

    @pytest.mark.asyncio
    async def test_parses_json_array_response(self):
        """mlx_lm.generate returns '["q1","q2","q3"]' → list of 3 strings."""
        from unittest.mock import MagicMock
        mock_lifecycle = MagicMock()
        runner = SynthesisRunner(mock_lifecycle)

        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template = MagicMock(return_value="prompt")

        json_output = '["LockBit IOCs 2026","LockBit C2 servers","LockBit victims list"]'

        # Patch mlx_lm in the function's global scope
        with patch("mlx_lm.generate", return_value=json_output) as mock_gen:
            result = await runner.decompose_query(
                "LockBit ransomware",
                mock_model,
                mock_tokenizer,
            )

            assert result == ["LockBit IOCs 2026", "LockBit C2 servers", "LockBit victims list"]

    @pytest.mark.asyncio
    async def test_parses_array_with_extra_text(self):
        """MLX returns text with JSON array embedded → extracts correctly."""
        from unittest.mock import MagicMock
        mock_lifecycle = MagicMock()
        runner = SynthesisRunner(mock_lifecycle)

        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template = MagicMock(return_value="prompt")

        full_output = 'Here are search queries:\n["CVE-2024-1 PoC","CVE-2024-1 exploit","CVE-2024-1 malware analysis"]\nGood luck!'

        with patch("mlx_lm.generate", return_value=full_output) as mock_gen:
            result = await runner.decompose_query("CVE-2024-1", mock_model, mock_tokenizer)

            assert len(result) == 3
            assert "CVE-2024-1 PoC" in result
