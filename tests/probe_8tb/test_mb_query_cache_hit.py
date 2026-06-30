"""
Sprint 8TB probe tests — MalwareBazaarClient cache hit.
Sprint: 8TB
Area: MalwareBazaar Client
"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hledac.universal.intelligence.exposure_clients import MalwareBazaarClient


class TestMBQueryCacheHit:
    """query_hash returns cached result without HTTP call."""

    @pytest.mark.asyncio
    async def test_cache_hit_no_http(self, tmp_path: Path):
        """Cache exists + within TTL → returns cached data, no POST."""
        client = MalwareBazaarClient(cache_dir=tmp_path)

        # Pre-write cache file (correct xxhash)
        import xxhash
        cache_key = xxhash.xxh64(b"mb_abc123").hexdigest()
        cached_resp = {"query_status": "ok", "data": [{"sha256_hash": "abc123"}]}
        cache_file = tmp_path / f"{cache_key}.json"
        cache_file.write_bytes(json.dumps(cached_resp).encode())

        import os
        old_mtime = time.time() - 60  # 60s ago — within 1h TTL
        os.utime(cache_file, (old_mtime, old_mtime))

        mock_session = AsyncMock()
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value=cached_resp)
        mock_response.raise_for_status = MagicMock()
        mock_session.post = AsyncMock(return_value=mock_response)

        result = await client.query_hash("abc123", mock_session)

        assert result == cached_resp
        mock_session.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_response_structure(self, tmp_path: Path):
        """On exception, query_hash returns error dict with query_status=error."""
        client = MalwareBazaarClient(cache_dir=tmp_path)

        mock_session = AsyncMock()
        mock_session.post = AsyncMock(side_effect=Exception("network error"))

        result = await client.query_hash("abc123", mock_session)

        assert result == {"query_status": "error", "data": []}
