"""
Sprint 8TB probe tests — _execute_pivot hash dispatch.
Sprint: 8TB
Area: Agentic Pivot Loop
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.runtime.sprint_scheduler import PivotTask, SprintScheduler
from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig


class TestExecutePivotHashDispatch:
    """_execute_pivot with hash_to_mb task dispatches to MalwareBazaarClient."""

    @pytest.mark.asyncio
    async def test_hash_to_mb_calls_query_hash(self):
        """PivotTask(task_type='hash_to_mb') → MalwareBazaarClient.query_hash called."""
        config = SprintSchedulerConfig()
        sched = SprintScheduler(config)

        task = PivotTask(
            priority=-0.9,
            ioc_type="sha256",
            ioc_value="abc123def456",
            task_type="hash_to_mb",
        )

        mock_mb = MagicMock()
        mock_mb.query_hash = AsyncMock(return_value={
            "query_status": "ok",
            "data": [{"sha256_hash": "abc123def456"}],
        })
        mock_mb.extract_iocs = MagicMock(return_value=[
            ("abc123def456", "sha256"),
            ("lockbit", "malware_family"),
        ])
        mock_mb.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session_ctx)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
            with patch("hledac.universal.intelligence.exposure_clients.MalwareBazaarClient", return_value=mock_mb):
                await sched._execute_pivot(task)

        mock_mb.query_hash.assert_called_once()
        mock_mb.extract_iocs.assert_called_once()
        mock_mb.close.assert_called_once()
