"""
Sprint 8TB probe tests — GitHubCodeSearchClient 403 handling.
Sprint: 8TB
Area: GitHub Code Search Client
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from hledac.universal.intelligence.exposure_clients import GitHubCodeSearchClient


class TestGitHubClient403ReturnsEmpty:
    """403 response from GitHub returns empty list, no exception."""

    @pytest.mark.asyncio
    async def test_403_returns_empty_list(self, tmp_path):
        """GitHub returns 403 → search_cve() returns [] without raising."""
        client = GitHubCodeSearchClient(cache_dir=tmp_path)

        mock_session = AsyncMock()
        mock_response = MagicMock()
        mock_response.status = 403
        mock_response.raise_for_status = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_response)

        result = await client.search_cve("CVE-2024-1", mock_session)

        assert result == []
        mock_response.raise_for_status.assert_not_called()
