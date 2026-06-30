"""
Sprint 8TB probe tests — _execute_pivot CVE dispatch.
Sprint: 8TB
Area: Agentic Pivot Loop
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.runtime.sprint_scheduler import PivotTask, SprintScheduler
from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig


class TestExecutePivotCVEDispatch:
    """_execute_pivot with cve_to_github task dispatches to GitHubCodeSearchClient."""

    @pytest.mark.asyncio
    async def test_cve_to_github_calls_search_cve(self):
        """PivotTask(task_type='cve_to_github') → GitHubCodeSearchClient.search_cve called."""
        config = SprintSchedulerConfig()
        sched = SprintScheduler(config)

        task = PivotTask(
            priority=-0.8,
            ioc_type="cve",
            ioc_value="CVE-2024-1",
            task_type="cve_to_github",
        )

        mock_gh = MagicMock()
        mock_gh.search_cve = AsyncMock(return_value=[
            {"repo": "test/repo", "url": "https://github.com/test/repo", "path": "poc.py", "stars": 10}
        ])
        mock_gh.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session_ctx)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

        # GitHubCodeSearchClient is imported INSIDE _execute_pivot via:
        #   from hledac.universal.intelligence.exposure_clients import GitHubCodeSearchClient
        # So patch at the import source
        with patch("aiohttp.ClientSession", return_value=mock_session_ctx):
            with patch("hledac.universal.intelligence.exposure_clients.GitHubCodeSearchClient", return_value=mock_gh):
                await sched._execute_pivot(task)

        mock_gh.search_cve.assert_called_once()
        mock_gh.close.assert_called_once()
