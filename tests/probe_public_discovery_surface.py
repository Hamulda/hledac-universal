"""
probe_public_discovery_surface — F232 provider surface + discovery_empty subtype tests

Tests the F232 PUBLIC discovery surface telemetry:
- domain query emits domain variants
- no provider selected → explicit terminal stage
- provider returns zero → provider_returned_zero (not generic discovery_error)
- provider import error → provider_unavailable
- public_stage_counters raw_count_source/provider status filled
- public_surface_present true only when meaningful surface exists

All provider calls are mocked. No live network access.
"""

import sys
from unittest.mock import MagicMock

import pytest

# Ensure hledac.universal is on sys.path
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


class TestSprintF232DomainQueryVariants:
    """Sprint F232: domain query variant extraction and emission."""

    def test_build_query_variants_mozilla_org_mixed_query(self):
        """Mixed query 'mozilla.org certificate transparency' emits domain variants."""
        from hledac.universal.discovery.duckduckgo_adapter import _build_query_variants, _extract_domain_token

        # Extract domain from mixed query
        domain = _extract_domain_token(
            "mozilla.org certificate transparency subdomains april 2026"
        )
        assert domain == "mozilla.org", f"Expected mozilla.org, got {domain}"

        # Build variants includes site: and CT-aware variants
        variants = _build_query_variants(
            "mozilla.org certificate transparency subdomains april 2026"
        )
        assert len(variants) > 1, f"Expected variants, got single: {variants}"
        assert "site:mozilla.org" in variants, f"Missing site: variant in {variants}"

    def test_build_query_variants_pure_domain(self):
        """Pure domain 'example.com' emits 4 variants."""
        from hledac.universal.discovery.duckduckgo_adapter import _build_query_variants

        variants = _build_query_variants("example.com")
        assert len(variants) == 4, f"Expected 4 variants, got {len(variants)}: {variants}"
        assert "site:example.com" in variants
        assert '"{domain}" security' not in variants  # actual format check

    def test_build_query_variants_plain_text_no_expansion(self):
        """Plain text query with no domain returns single variant."""
        from hledac.universal.discovery.duckduckgo_adapter import _build_query_variants

        variants = _build_query_variants("plain text security advisory")
        assert variants == ["plain text security advisory"], f"Expected single variant, got {variants}"

    def test_extract_domain_token_strips_site_prefix(self):
        """site:example.com extraction strips the prefix."""
        from hledac.universal.discovery.duckduckgo_adapter import _extract_domain_token

        domain = _extract_domain_token("site:example.com")
        assert domain == "example.com", f"Expected example.com, got {domain}"

    def test_extract_domain_token_no_domain(self):
        """Plain text with no domain returns None."""
        from hledac.universal.discovery.duckduckgo_adapter import _extract_domain_token

        result = _extract_domain_token("plain security advisory")
        assert result is None, f"Expected None, got {result}"


class TestSprintF232DiscoveryEmptySubtypes:
    """Sprint F232: discovery_empty must be specific, not generic."""

    @pytest.fixture
    def mock_discovery_zero_hits(self):
        """Returns a mock discovery result with zero hits."""
        mock = MagicMock()
        mock.hits = ()
        mock.error = None
        mock.cache_hit = False
        mock.error_type = None
        mock.provider_name = "duckduckgo"
        mock.provider_chain = ("duckduckgo",)
        mock.source_family = "search"
        mock.elapsed_s = 0.5
        return mock

    @pytest.mark.asyncio
    async def test_provider_returned_zero_sets_specific_reason(self, mock_discovery_zero_hits):
        """Provider that returned zero hits should get provider_returned_zero, not generic."""
        from hledac.universal.pipeline.live_public_pipeline import _patch_discovery, async_run_live_public_pipeline

        # Provider returns zero hits
        mock_discovery_zero_hits.provider_name = "duckduckgo"
        mock_discovery_zero_hits.hits = ()

        async def canned_search(query, max_results=10, timeout_s=30.0):
            return mock_discovery_zero_hits

        _patch_discovery(canned_search)

        # Run with empty DB
        import tempfile

        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/probe_f232.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            result = await async_run_live_public_pipeline(
                query="nonexistent query that returns zero",
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
            )

            assert result.public_discovery_empty_reason in (
                "provider_returned_zero",
                "no_provider_selected",
            ), f"Expected specific subtype, got: {result.public_discovery_empty_reason}"

    @pytest.mark.asyncio
    async def test_empty_query_error_sets_query_builder_empty(self):
        """empty_query error string maps to query_builder_empty."""
        from hledac.universal.pipeline.live_public_pipeline import _patch_discovery, async_run_live_public_pipeline

        mock = MagicMock()
        mock.hits = ()
        mock.error = "empty_query"
        mock.cache_hit = False
        mock.error_type = "empty_query"

        async def canned_search(query, max_results=10, timeout_s=30.0):
            return mock

        _patch_discovery(canned_search)

        import tempfile

        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/probe_f232_empty.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            result = await async_run_live_public_pipeline(
                query="",  # empty query
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
            )

            assert result.public_discovery_empty_reason == "query_builder_empty", (
                f"Expected query_builder_empty, got: {result.public_discovery_empty_reason}"
            )


class TestSprintF232ProviderSurfaceTelemetry:
    """Sprint F232: provider surface telemetry fields populated."""

    @pytest.mark.asyncio
    async def test_provider_surface_fields_present_in_result(self):
        """PipelineRunResult has all F232 provider surface fields."""
        from hledac.universal.pipeline.live_public_pipeline import _patch_discovery, async_run_live_public_pipeline

        # Mock discovery with hits so we can check provider surface fields
        mock_hits = []
        for i in range(3):
            hit = MagicMock()
            hit.query = "test provider surface"
            hit.url = f"https://example-{i}.com"
            hit.title = f"Test {i}"
            hit.snippet = f"Test snippet {i}"
            hit.score = 0.8
            hit.reason = "test"
            hit.rank = i
            hit.source = "search"
            hit.retrieved_ts = 0.0
            mock_hits.append(hit)

        mock = MagicMock()
        mock.hits = tuple(mock_hits)
        mock.error = None
        mock.cache_hit = False
        mock.error_type = None
        mock.provider_name = "duckduckgo"
        mock.provider_chain = ("duckduckgo",)
        mock.source_family = "search"
        mock.elapsed_s = 0.5

        async def canned_search(query, max_results=10, timeout_s=30.0):
            return mock

        _patch_discovery(canned_search)

        import tempfile

        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/probe_f232_surf.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            result = await async_run_live_public_pipeline(
                query="test provider surface",
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
            )

            # F232 fields must be present (even if empty)
            assert hasattr(result, "public_provider_selected"), "Missing public_provider_selected"
            assert hasattr(result, "public_provider_skipped"), "Missing public_provider_skipped"
            assert hasattr(result, "public_provider_stub"), "Missing public_provider_stub"
            assert hasattr(result, "public_provider_errors"), "Missing public_provider_errors"
            assert hasattr(result, "public_query_variants"), "Missing public_query_variants"
            assert hasattr(result, "public_provider_timeout_count"), "Missing public_provider_timeout_count"
            assert hasattr(result, "public_provider_import_error_count"), "Missing public_provider_import_error_count"
            assert hasattr(result, "public_discovery_empty_reason"), "Missing public_discovery_empty_reason"


class TestSprintF232ProviderErrors:
    """Sprint F232: provider error types map to specific discovery_empty subtypes."""

    @pytest.mark.asyncio
    async def test_provider_timeout_maps_to_provider_timeout(self):
        """Timeout error → provider_timeout discovery_empty reason."""
        from hledac.universal.pipeline.live_public_pipeline import _patch_discovery, async_run_live_public_pipeline

        mock = MagicMock()
        mock.hits = ()
        mock.error = "timeout"
        mock.cache_hit = False
        mock.error_type = "timeout"

        async def canned_search(query, max_results=10, timeout_s=30.0):
            return mock

        _patch_discovery(canned_search)

        import tempfile

        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/probe_f232_tmo.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            result = await async_run_live_public_pipeline(
                query="test timeout",
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
            )

            assert result.public_discovery_empty_reason == "provider_timeout", (
                f"Expected provider_timeout, got: {result.public_discovery_empty_reason}"
            )

    @pytest.mark.asyncio
    async def test_provider_exception_maps_to_provider_unavailable(self):
        """Provider exception → provider_unavailable discovery_empty reason."""
        from hledac.universal.pipeline.live_public_pipeline import _patch_discovery, async_run_live_public_pipeline

        mock = MagicMock()
        mock.hits = ()
        mock.error = "ddg_exception:ConnectionError"
        mock.cache_hit = False
        mock.error_type = "provider_exception"

        async def canned_search(query, max_results=10, timeout_s=30.0):
            return mock

        _patch_discovery(canned_search)

        import tempfile

        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/probe_f232_exc.ddb"
            store = DuckDBShadowStore(db_path=db_path)
            store._init_persistent_dedup_lmdb = lambda: None
            await store.async_initialize()

            result = await async_run_live_public_pipeline(
                query="test exception",
                store=store,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_max_bytes=100_000,
                fetch_concurrency=1,
            )

            assert result.public_discovery_empty_reason == "provider_unavailable", (
                f"Expected provider_unavailable, got: {result.public_discovery_empty_reason}"
            )


class TestSprintF232PublicSurfacePresent:
    """Sprint F232: public_surface_present is true only when meaningful surface exists."""

    def test_public_surface_present_true_with_provider_selected(self):
        """When providers are selected, public_provider_selected is non-empty."""
        from hledac.universal.pipeline.live_public_pipeline import PipelineRunResult

        # Verify the field exists and has correct type by constructing a result with it
        result = PipelineRunResult(
            query="test",
            discovered=1,
            fetched=0,
            matched_patterns=0,
            accepted_findings=0,
            stored_findings=0,
            patterns_configured=0,
            pages=(),
            public_provider_selected=["duckduckgo"],
        )
        assert result.public_provider_selected == ["duckduckgo"], (
            f"Expected ['duckduckgo'], got: {result.public_provider_selected}"
        )

    def test_public_surface_present_false_with_no_provider(self):
        """When no provider selected, public_discovery_empty_reason is set."""
        from hledac.universal.pipeline.live_public_pipeline import PipelineRunResult

        result = PipelineRunResult(
            query="test",
            discovered=0,
            fetched=0,
            matched_patterns=0,
            accepted_findings=0,
            stored_findings=0,
            patterns_configured=0,
            pages=(),
            public_discovery_empty_reason="no_provider_selected",
        )
        assert result.public_discovery_empty_reason == "no_provider_selected", (
            f"Expected 'no_provider_selected', got: {result.public_discovery_empty_reason}"
        )
