"""
Sprint 8TB probe tests — decompose_query caps at 5 sub-queries.
Sprint: 8TB
Area: Query Decomposer
"""

from unittest.mock import MagicMock, patch

import pytest

from hledac.universal.brain.synthesis_runner import SynthesisRunner


class TestDecomposeQueryMax5:
    """MLX returns 10 items → result is sliced to max 5."""

    @pytest.mark.asyncio
    async def test_max_5_items_returned(self):
        """MLX returns 10 items → output is first 5."""
        from unittest.mock import MagicMock
        mock_lifecycle = MagicMock()
        runner = SynthesisRunner(mock_lifecycle)

        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template = MagicMock(return_value="prompt")

        many_items = ["q" + str(i) for i in range(10)]
        json_output = '["' + '","'.join(many_items) + '"]'

        with patch("mlx_lm.generate", return_value=json_output) as mock_gen:
            result = await runner.decompose_query("test query", mock_model, mock_tokenizer)

            assert len(result) == 5
            assert result == ["q0", "q1", "q2", "q3", "q4"]
