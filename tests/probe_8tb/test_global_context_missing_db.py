"""
Sprint 8TB probe tests — _load_global_context missing DB.
Sprint: 8TB
Area: Ghost Global Context
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hledac.universal.brain.synthesis_runner import SynthesisRunner


class TestGlobalContextMissingDB:
    """ghost_global.duckdb does not exist → returns empty string."""

    @pytest.mark.asyncio
    async def test_missing_db_returns_empty_string(self):
        """DB path doesn't exist → _load_global_context() returns '' without exception."""
        from unittest.mock import MagicMock

        mock_lifecycle = MagicMock()
        runner = SynthesisRunner(mock_lifecycle)

        # Patch Path.exists to return False for the ghost_global path
        with patch("pathlib.Path.exists", return_value=False):
            result = await runner._load_global_context()

        assert result == ""

    @pytest.mark.asyncio
    async def test_connect_error_returns_empty_string(self):
        """duckdb.connect raises → returns '' without propagating exception."""
        from unittest.mock import MagicMock

        mock_lifecycle = MagicMock()
        runner = SynthesisRunner(mock_lifecycle)

        # Patch duckdb.connect to raise
        with patch("duckdb.connect", side_effect=OSError("no such file")):
            result = await runner._load_global_context()

        assert result == ""
