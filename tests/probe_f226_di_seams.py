"""
probe_f226_di_seams — F226 dependency injection seam tests

Tests that async_run_live_public_pipeline accepts explicit DI params
and uses them without global patching:

- fetch_fn is used when provided (no global patch needed)
- match_fn is used when provided
- discovery_fn is used when provided
- ct_subdomains_fn is used when provided
- clear_query_cache_fn is used when provided
- default behavior (None params) still works

No live network, no MLX, no browser, no model load.
M1/Python 3.14+ safe.
"""

import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac")


class TestSprintF226DISeams:
    """Sprint F226: explicit DI params bypass global patching."""

    @pytest.mark.asyncio
    async def test_fetch_fn_is_used_without_global_patch(self):
        """Explicit fetch_fn is called without patching _ASYNC_FETCH_PUBLIC_TEXT."""
        import tempfile
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        # Build a canned discovery result with one hit
        canned_discovery = MagicMock()
        canned_discovery.hits = (
            MagicMock(url="https://example.com", title="Example", snippet="Example", rank=0, score=0.9, reason="test"),
        )
        canned_discovery.cache_hit = False

        # Track whether fetch_fn was called
        fetch_called_with = []

        async def canned_fetch(url, timeout, max_bytes, use_stealth=False, use_js=False, use_doh=False):
            fetch_called_with.append(url)
            # Return a canned fetch result
            result = MagicMock()
            result.fetched_text = "<html>test content</html>"
            result.elapsed_s = 0.1
            result.status_code = 200
            result.used_stealth = False
            result.used_js = False
            result.used_doh = False
            return result

        async def canned_discovery_fn(query, max_results=10, timeout_s=30.0):
            return canned_discovery

        async def canned_match(text):
            return []  # no pattern matches

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/f226_fetch.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            result = await async_run_live_public_pipeline(
                query="test fetch di",
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
                fetch_fn=canned_fetch,
                match_fn=canned_match,
                discovery_fn=canned_discovery_fn,
            )

            assert len(fetch_called_with) >= 1, f"fetch_fn was never called: {fetch_called_with}"
            assert "example.com" in fetch_called_with[0]

    @pytest.mark.asyncio
    async def test_match_fn_is_used_without_global_patch(self):
        """Explicit match_fn is called without patching _SYNC_MATCH_TEXT."""
        import tempfile
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        canned_discovery = MagicMock()
        canned_discovery.hits = (
            MagicMock(url="https://test.example.com", title="Test", snippet="Test", rank=0, score=0.9, reason="test"),
        )
        canned_discovery.cache_hit = False

        match_calls = []

        async def canned_fetch(url, timeout, max_bytes, use_stealth=False, use_js=False, use_doh=False):
            result = MagicMock()
            result.fetched_text = "<html>hello world</html>"
            result.elapsed_s = 0.1
            result.status_code = 200
            result.used_stealth = False
            result.used_js = False
            result.used_doh = False
            return result

        def canned_match(text):
            match_calls.append(text)
            return [MagicMock(label="test_pattern", value="test_value", pattern="test")]

        async def canned_discovery_fn(query, max_results=10, timeout_s=30.0):
            return canned_discovery

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/f226_match.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            result = await async_run_live_public_pipeline(
                query="test match di",
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
                fetch_fn=canned_fetch,
                match_fn=canned_match,
                discovery_fn=canned_discovery_fn,
            )

            assert len(match_calls) >= 1, f"match_fn was never called: {match_calls}"
            assert "hello world" in match_calls[0]

    @pytest.mark.asyncio
    async def test_discovery_fn_is_used_without_patch(self):
        """Explicit discovery_fn is called without _patch_discovery()."""
        import tempfile
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        discovery_calls = []

        async def canned_discovery(query, max_results=10, timeout_s=30.0):
            discovery_calls.append(query)
            result = MagicMock()
            result.hits = (
                MagicMock(url="https://di.test", title="DI Test", snippet="Test", rank=0, score=0.9, reason="di"),
            )
            result.cache_hit = False
            return result

        async def canned_fetch(url, timeout, max_bytes, use_stealth=False, use_js=False, use_doh=False):
            result = MagicMock()
            result.fetched_text = "<html>di test</html>"
            result.elapsed_s = 0.1
            result.status_code = 200
            result.used_stealth = False
            result.used_js = False
            result.used_doh = False
            return result

        def canned_match(text):
            return []

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/f226_discovery.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            result = await async_run_live_public_pipeline(
                query="test discovery di param",
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
                fetch_fn=canned_fetch,
                match_fn=canned_match,
                discovery_fn=canned_discovery,
            )

            assert "test discovery di param" in discovery_calls[0]
            assert result.discovered >= 1

    @pytest.mark.asyncio
    async def test_ct_subdomains_fn_is_used(self):
        """Explicit ct_subdomains_fn is called for domain queries."""
        import tempfile
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        ct_calls = []

        async def canned_ct_scanner(domain, async_session=None):
            ct_calls.append(domain)
            return [f"sub.{domain}", f"api.{domain}"]

        canned_discovery = MagicMock()
        canned_discovery.hits = (
            MagicMock(url="https://example.com", title="Example", snippet="Example", rank=0, score=0.9, reason="test"),
        )
        canned_discovery.cache_hit = False

        async def canned_fetch(url, timeout, max_bytes, use_stealth=False, use_js=False, use_doh=False):
            result = MagicMock()
            result.fetched_text = "<html>test</html>"
            result.elapsed_s = 0.1
            result.status_code = 200
            result.used_stealth = False
            result.used_js = False
            result.used_doh = False
            return result

        def canned_match(text):
            return []

        async def canned_discovery_fn(query, max_results=10, timeout_s=30.0):
            return canned_discovery

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/f226_ct.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            result = await async_run_live_public_pipeline(
                query="example.com",
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
                fetch_fn=canned_fetch,
                match_fn=canned_match,
                discovery_fn=canned_discovery_fn,
                ct_subdomains_fn=canned_ct_scanner,
            )

            # CT scanner is called for domain queries
            assert len(ct_calls) >= 1, f"ct_subdomains_fn was never called: {ct_calls}"
            assert "example.com" in ct_calls[0]

    @pytest.mark.asyncio
    async def test_clear_query_cache_fn_is_used(self):
        """Explicit clear_query_cache_fn is called at pipeline start."""
        import tempfile
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        cache_clear_calls = 0

        def tracking_clear_cache():
            nonlocal cache_clear_calls
            cache_clear_calls += 1

        canned_discovery = MagicMock()
        canned_discovery.hits = ()
        canned_discovery.cache_hit = False

        async def canned_discovery_fn(query, max_results=10, timeout_s=30.0):
            return canned_discovery

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/f226_cache.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            result = await async_run_live_public_pipeline(
                query="test cache clear",
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
                discovery_fn=canned_discovery_fn,
                clear_query_cache_fn=tracking_clear_cache,
            )

            assert cache_clear_calls >= 1, "clear_query_cache_fn was never called"

    @pytest.mark.asyncio
    async def test_default_behavior_unchanged_when_params_none(self):
        """When all DI params are None, pipeline uses existing globals/defaults."""
        import tempfile
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline

        # Empty discovery → pipeline runs but finds nothing
        empty_discovery = MagicMock()
        empty_discovery.hits = ()
        empty_discovery.cache_hit = False

        async def canned_discovery_fn(query, max_results=10, timeout_s=30.0):
            return empty_discovery

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/f226_default.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            # This should work without any DI params (uses _patch_discovery / _ensure_patched paths)
            result = await async_run_live_public_pipeline(
                query="test default params",
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
            )

            # Pipeline ran to completion with no findings (default behavior)
            assert result is not None
            assert hasattr(result, "discovered")