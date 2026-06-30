"""
Sprint 8TB probe tests — GitHubCodeSearchClient unauthenticated header.
Sprint: 8TB
Area: GitHub Code Search Client
"""

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from hledac.universal.intelligence.exposure_clients import GitHubCodeSearchClient


class TestGitHubClientUnauthHeader:
    """Without GITHUB_TOKEN, headers do not include Authorization."""

    @pytest.mark.asyncio
    async def test_no_auth_header_without_token(self, tmp_path):
        """No GITHUB_TOKEN set → headers do not contain Authorization."""
        # Ensure token is not set
        token_backup = os.environ.get("GITHUB_TOKEN")
        try:
            os.environ.pop("GITHUB_TOKEN", None)
            client = GitHubCodeSearchClient(cache_dir=tmp_path)

            mock_session = AsyncMock()
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"items": []})
            mock_response.raise_for_status = MagicMock()
            mock_session.get = AsyncMock(return_value=mock_response)

            await client.search_cve("CVE-2024-1", mock_session)

            # Check the headers passed to get()
            call_args = mock_session.get.call_args
            headers = call_args.kwargs.get("headers", {})
            assert "Authorization" not in headers
        finally:
            if token_backup:
                os.environ["GITHUB_TOKEN"] = token_backup

    @pytest.mark.asyncio
    async def test_auth_header_with_token(self, tmp_path):
        """With GITHUB_TOKEN set → headers include Authorization Bearer."""
        os.environ["GITHUB_TOKEN"] = "test_token_123"

        try:
            client = GitHubCodeSearchClient(cache_dir=tmp_path)

            mock_session = AsyncMock()
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"items": []})
            mock_response.raise_for_status = MagicMock()
            mock_session.get = AsyncMock(return_value=mock_response)

            await client.search_cve("CVE-2024-1", mock_session)

            call_args = mock_session.get.call_args
            headers = call_args.kwargs.get("headers", {})
            assert "Authorization" in headers
            assert headers["Authorization"] == "Bearer test_token_123"
        finally:
            os.environ.pop("GITHUB_TOKEN", None)
