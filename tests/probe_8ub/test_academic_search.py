"""
Sprint 8UB: SemanticScholarClient tests
"""

import asyncio
import json
import time
import xxhash
from unittest.mock import MagicMock

import pytest


class TestSemanticScholarClient:
    """Test SemanticScholarClient search_ss and search_arxiv."""

    @pytest.fixture
    def client(self, tmp_path):
        from hledac.universal.intelligence.academic_search import SemanticScholarClient
        return SemanticScholarClient(cache_dir=tmp_path)

    def test_cache_hit(self, client, tmp_path):
        """Cache hit returns data without HTTP call."""
        query = "CVE-2024"
        key = xxhash.xxh64(f"ss_{query}".encode()).hexdigest()
        cache_file = tmp_path / f"{key}.json"
        cache_file.write_text(json.dumps([{"title": "Test Paper"}]))

        async def run():
            return await client.search_ss(query, MagicMock())

        result = asyncio.run(run())
        assert result == [{"title": "Test Paper"}]

    def test_throttle(self, client):
        """Throttle enforces minimum interval."""
        client._last_req = time.time()

        async def run():
            await client._throttle()

        start = time.time()
        asyncio.run(run())
        assert time.time() - start >= 0.4  # Rate is 0.5s


class TestAcademicDispatchExists:
    """Verify cve_to_academic task type exists in dispatch."""

    def test_dispatch_has_cve_to_academic(self):
        """cve_to_academic present in enqueue_pivot task_types."""
        from pathlib import Path
        source = Path(__file__).parent.parent.parent / "runtime" / "sprint_scheduler.py"
        content = source.read_text()
        assert "cve_to_academic" in content
        assert '"cve": ["cve_to_github", "cve_to_academic"]' in content
