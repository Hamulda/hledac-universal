"""
Sprint 8TB probe tests — MalwareBazaarClient error handling.
Sprint: 8TB
Area: MalwareBazaar Client
"""

from unittest.mock import AsyncMock

import pytest

from hledac.universal.intelligence.exposure_clients import MalwareBazaarClient


class TestMBErrorResponse:
    """session.post raises ClientError → query_hash returns error dict."""

    @pytest.mark.asyncio
    async def test_client_error_returns_error_dict(self, tmp_path):
        """aiohttp.ClientError → returns {"query_status": "error", "data": []}."""
        import aiohttp

        client = MalwareBazaarClient(cache_dir=tmp_path)
        mock_session = AsyncMock()
        mock_session.post = AsyncMock(side_effect=aiohttp.ClientError("connection refused"))

        result = await client.query_hash("abc123", mock_session)

        assert result == {"query_status": "error", "data": []}
