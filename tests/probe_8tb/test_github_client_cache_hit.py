"""
Sprint 8TB probe tests — GitHubCodeSearchClient cache hit.
Sprint: 8TB
Area: GitHub Code Search Client
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.intelligence.exposure_clients import GitHubCodeSearchClient


class TestGitHubClientCacheHit:
    """search_cve returns cached result without HTTP call."""

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached(self, tmp_path: Path):
        """Cache file exists + TTL valid → returns cached data, no HTTP call."""
        import xxhash

        client = GitHubCodeSearchClient(cache_dir=tmp_path)

        # Pre-write cache file with correct xxhash key
        cve_id = "CVE-2024-1"
        cache_key = xxhash.xxh64(f"ghcs_{cve_id}".encode()).hexdigest()
        cached_data = [{"repo": "test/repo", "url": "https://github.com/test/repo", "path": "poc.py", "stars": 10}]
        cache_file = tmp_path / f"{cache_key}.json"
        cache_file.write_bytes(json.dumps(cached_data).encode())

        # Set mtime to be recent (within TTL)
        old_mtime = time.time() - 60  # 60 seconds ago
        import os
        os.utime(cache_file, (old_mtime, old_mtime))

        # Don't need to mock session at all if cache hits first
        result = await client.search_cve(cve_id, MagicMock())

        assert result == cached_data

    @pytest.mark.asyncio
    async def test_no_http_call_on_cache_hit(self, tmp_path: Path):
        """Cache hit means session.get is never called."""
        import xxhash

        client = GitHubCodeSearchClient(cache_dir=tmp_path)

        cve_id = "CVE-2024-1"
        cache_key = xxhash.xxh64(f"ghcs_{cve_id}".encode()).hexdigest()
        cache_file = tmp_path / f"{cache_key}.json"
        cache_file.write_bytes(json.dumps([{"repo": "r", "url": "u", "path": "p", "stars": 0}]).encode())

        mock_session = AsyncMock()
        result = await client.search_cve(cve_id, mock_session)

        assert len(result) == 1
        mock_session.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_calls_http(self, tmp_path: Path):
        """No cache → HTTP is called."""
        import xxhash

        client = GitHubCodeSearchClient(cache_dir=tmp_path)

        # No cache file for this CVE
        mock_session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"items": []})
        mock_response.raise_for_status = MagicMock()
        mock_session.get = AsyncMock(return_value=mock_response)

        result = await client.search_cve("CVE-2024-NEW", mock_session)

        assert result == []
        mock_session.get.assert_called_once()
