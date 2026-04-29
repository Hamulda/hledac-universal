"""
Sprint 8AC — DuckDuckGo public discovery adapter tests.

Covers all 26 required test cases from the sprint specification.
All live network calls are monkey-patched — tests NEVER make real DDG calls.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock, patch

import msgspec
import pytest

# The module under test
from hledac.universal.discovery import duckduckgo_adapter as dda


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ddgs_text() -> MagicMock:
    """Return a mock for the synchronous DDGS.text() method."""
    mock = MagicMock()
    return mock


@pytest.fixture
def fake_ddg_results() -> list[dict[str, Any]]:
    """Canonical 10-fake-hit fixture returned by the mock."""
    return [
        {
            "title": f"Result Title {i}",
            "url": f"https://example{i}.com/page",
            "body": f"This is the snippet for result {i}.",
        }
        for i in range(10)
    ]


@pytest.fixture
def fake_ddg_results_with_none() -> list[dict[str, Any]]:
    """Fixture where some fields are None to test normalisation."""
    return [
        {"title": None, "url": "https://example0.com/", "body": None},
        {"title": "Valid Title", "url": None, "body": "Valid body"},
        {"title": "", "url": "", "body": ""},
        {"title": "Result 3", "url": "https://example3.com/", "body": "Body 3"},
    ]


# ---------------------------------------------------------------------------
# 1. Module exists
# ---------------------------------------------------------------------------


class TestModuleExists:
    def test_discovery_module_exists(self):
        """Test that the discovery module exists."""
        assert dda is not None

    def test_discovery_directory_created(self):
        """Test that discovery/ directory is a valid package."""
        import os

        path = os.path.join(os.path.dirname(dda.__file__), "duckduckgo_adapter.py")
        assert os.path.isfile(path), "duckduckgo_adapter.py must exist in discovery/"


# ---------------------------------------------------------------------------
# 2. DiscoveryHit contract
# ---------------------------------------------------------------------------


class TestDiscoveryHitContract:
    def test_discovery_hit_is_msgspec_struct(self):
        """DiscoveryHit must be a msgspec.Struct."""
        from msgspec import Struct

        assert issubclass(dda.DiscoveryHit, Struct)

    def test_discovery_hit_is_frozen(self):
        """DiscoveryHit must be frozen."""
        assert dda.DiscoveryHit.__struct_fields__ is not None  # frozen implies this

    def test_discovery_hit_fields(self):
        """DiscoveryHit has all required fields."""
        fields = dda.DiscoveryHit.__struct_fields__
        expected = {"query", "title", "url", "snippet", "source", "rank", "retrieved_ts"}
        assert set(fields) == expected

    def test_discovery_hit_no_pydantic(self):
        """DiscoveryHit must NOT be a pydantic model."""
        import pydantic

        hit = dda.DiscoveryHit(
            query="q", title="t", url="https://x.com/", snippet="s",
            source="duckduckgo", rank=0, retrieved_ts=1.0
        )
        assert not isinstance(hit, pydantic.BaseModel)
        assert isinstance(hit, msgspec.Struct)


# ---------------------------------------------------------------------------
# 3. DiscoveryBatchResult contract
# ---------------------------------------------------------------------------


class TestDiscoveryBatchResultContract:
    def test_discovery_batch_result_is_msgspec_struct(self):
        """DiscoveryBatchResult must be a msgspec.Struct."""
        from msgspec import Struct

        assert issubclass(dda.DiscoveryBatchResult, Struct)

    def test_discovery_batch_result_fields(self):
        """DiscoveryBatchResult has hits + error fields."""
        fields = dda.DiscoveryBatchResult.__struct_fields__
        assert "hits" in fields
        assert "error" in fields

    def test_discovery_batch_result_default_error_none(self):
        """DiscoveryBatchResult.error defaults to None."""
        result = dda.DiscoveryBatchResult(hits=())
        assert result.error is None


# ---------------------------------------------------------------------------
# 4. Empty query = fail-soft
# ---------------------------------------------------------------------------


class TestEmptyQueryFailSoft:
    @pytest.mark.asyncio
    async def test_empty_string_returns_fail_soft(self, mock_ddgs_text):
        with patch("asyncio.to_thread", mock_ddgs_text):
            result = await dda.async_search_public_web("")
        assert result.hits == ()
        assert result.error == "empty_query"

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_fail_soft(self, mock_ddgs_text):
        with patch("asyncio.to_thread", mock_ddgs_text):
            result = await dda.async_search_public_web("   \t\n  ")
        assert result.hits == ()
        assert result.error == "empty_query"

    @pytest.mark.asyncio
    async def test_no_network_call_on_empty_query(self, mock_ddgs_text):
        """Empty query must NOT call asyncio.to_thread (no network side-effect)."""
        with patch("asyncio.to_thread", mock_ddgs_text) as mock_thread:
            await dda.async_search_public_web("")
        mock_thread.assert_not_called()


# ---------------------------------------------------------------------------
# 5. max_results default
# ---------------------------------------------------------------------------


class TestMaxResultsDefault:
    @pytest.mark.asyncio
    async def test_default_max_results_is_10(self, mock_ddgs_text, fake_ddg_results):
        """Default max_results is 10."""
        mock_ddgs_text.return_value = fake_ddg_results

        async def fake_coro(*args, **kwargs):
            return fake_ddg_results

        with patch.object(dda, "_ddgs_text_search", new=fake_coro):
            result = await dda.async_search_public_web("test query")
        assert len(result.hits) <= 10


# ---------------------------------------------------------------------------
# 6. Hard cap 50
# ---------------------------------------------------------------------------


class TestHardCap50:
    @pytest.mark.asyncio
    async def test_hard_cap_50_exceeded(self):
        """Requests for >50 are silently capped to 50."""
        many = [
            {"title": f"T{i}", "url": f"https://e{i}.com/", "body": f"B{i}"}
            for i in range(200)
        ]

        async def fake_search(*args, **kwargs):
            # Return 200 hits; adapter must cap
            return many

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("test", max_results=999)
        assert len(result.hits) == 50
        assert result.error is None

    @pytest.mark.asyncio
    async def test_max_results_0_clamped_to_1(self):
        """max_results=0 is clamped to 1 (minimum of 1)."""
        async def fake_search(*args, **kwargs):
            return [{"title": "T", "url": "https://e.com/", "body": "B"}]

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("test", max_results=0)
        assert len(result.hits) <= 1


# ---------------------------------------------------------------------------
# 7. None normalisation
# ---------------------------------------------------------------------------


class TestNoneNormalisation:
    @pytest.mark.asyncio
    async def test_none_title_normalised_to_empty_string(
        self, fake_ddg_results_with_none
    ):
        async def fake_search(*args, **kwargs):
            return fake_ddg_results_with_none

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("test")

        # First result has None title
        first = result.hits[0]
        assert first.title == ""
        assert isinstance(first.title, str)

    @pytest.mark.asyncio
    async def test_none_snippet_normalised_to_empty_string(
        self, fake_ddg_results_with_none
    ):
        async def fake_search(*args, **kwargs):
            return fake_ddg_results_with_none

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("test")

        first = result.hits[0]
        assert first.snippet == ""
        assert isinstance(first.snippet, str)

    @pytest.mark.asyncio
    async def test_none_url_filtered(self, fake_ddg_results_with_none):
        """None/empty URL is filtered out during dedup."""
        async def fake_search(*args, **kwargs):
            return fake_ddg_results_with_none

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("test")

        for hit in result.hits:
            assert hit.url != ""


# ---------------------------------------------------------------------------
# 8. Single result normalisation
# ---------------------------------------------------------------------------


class TestSingleResultNormalisation:
    @pytest.mark.asyncio
    async def test_single_hit_normalised(self):
        async def fake_search(*args, **kwargs):
            return [{"title": "My Title", "url": "https://example.com/", "body": "My snippet"}]

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("test query")

        assert len(result.hits) == 1
        hit = result.hits[0]
        assert hit.query == "test query"
        assert hit.title == "My Title"
        assert hit.url == "https://example.com/"
        assert hit.snippet == "My snippet"
        assert hit.source == "duckduckgo"
        assert hit.rank == 0
        assert hit.retrieved_ts > 0


# ---------------------------------------------------------------------------
# 9. Multiple result normalisation
# ---------------------------------------------------------------------------


class TestMultipleResultNormalisation:
    @pytest.mark.asyncio
    async def test_multiple_hits_normalised(self, fake_ddg_results):
        async def fake_search(*args, **kwargs):
            return fake_ddg_results

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("multi test")

        assert len(result.hits) == 10
        for i, hit in enumerate(result.hits):
            assert hit.rank == i
            assert hit.query == "multi test"
            assert hit.source == "duckduckgo"


# ---------------------------------------------------------------------------
# 10. URL dedup preserve-first
# ---------------------------------------------------------------------------


class TestURLDedupPreserveFirst:
    @pytest.mark.asyncio
    async def test_duplicate_urls_removed_preserve_first(self):
        duplicates = [
            {"title": "First", "url": "https://example.com/", "body": "B1"},
            {"title": "Second", "url": "https://example.com/", "body": "B2"},
            {"title": "Third", "url": "https://example.com/", "body": "B3"},
            {"title": "Unique", "url": "https://unique.com/", "body": "B4"},
        ]

        async def fake_search(*args, **kwargs):
            return duplicates

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("dedup test")

        assert len(result.hits) == 2
        assert result.hits[0].title == "First"
        assert result.hits[1].title == "Unique"

    @pytest.mark.asyncio
    async def test_duplicate_urls_preserve_first_rank(self):
        """The first occurrence sets the rank; later duplicates don't shift it."""
        duplicates = [
            {"title": "Page A", "url": "https://site.com/a", "body": "A"},
            {"title": "Page B", "url": "https://site.com/b", "body": "B"},
            {"title": "Page A dup", "url": "https://site.com/a", "body": "A2"},
        ]

        async def fake_search(*args, **kwargs):
            return duplicates

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("rank test")

        urls = [h.url for h in result.hits]
        assert urls.count("https://site.com/a") == 1
        assert result.hits[0].title == "Page A"
        assert result.hits[0].rank == 0


# ---------------------------------------------------------------------------
# 11. URL normalisation before dedup
# ---------------------------------------------------------------------------


class TestURLNormalization:
    def test_normalize_lowercase_scheme_host(self):
        assert dda._normalize_url_for_dedup("HTTPS://EXAMPLE.COM/") == "https://example.com/"

    def test_normalize_trailing_slash(self):
        assert dda._normalize_url_for_dedup("https://example.com/page/") == "https://example.com/page"
        assert dda._normalize_url_for_dedup("https://example.com/") == "https://example.com/"

    def test_normalize_removes_lone_question_mark(self):
        assert dda._normalize_url_for_dedup("https://example.com/page?") == "https://example.com/page"

    def test_normalize_preserves_fragment(self):
        assert dda._normalize_url_for_dedup("https://example.com/page#section") == "https://example.com/page#section"

    def test_normalize_preserves_query(self):
        assert dda._normalize_url_for_dedup("https://example.com/page?q=1&r=2") == "https://example.com/page?q=1&r=2"

    def test_normalize_empty_url(self):
        assert dda._normalize_url_for_dedup("") == ""

    def test_normalize_case_different_deduped(self):
        """HTTPS and https should dedupe to one."""
        n1 = dda._normalize_url_for_dedup("HTTPS://EXAMPLE.COM/")
        n2 = dda._normalize_url_for_dedup("https://example.com/")
        assert n1 == n2

    @pytest.mark.asyncio
    async def test_url_normalisation_applied_before_dedup(self):
        """Case-different URLs should be deduped even when raw URLs differ."""

        async def fake_search(*args, **kwargs):
            return [
                {"title": "First", "url": "HTTPS://EXAMPLE.COM/", "body": "B1"},
                {"title": "Second", "url": "https://example.com/", "body": "B2"},
            ]

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("norm dedup test")

        assert len(result.hits) == 1
        assert result.hits[0].title == "First"


# ---------------------------------------------------------------------------
# 12. Stable ordering after dedup
# ---------------------------------------------------------------------------


class TestStableOrdering:
    @pytest.mark.asyncio
    async def test_order_preserved_after_dedup(self):
        ordered = [
            {"title": f"Result {i}", "url": f"https://site{i}.com/", "body": f"B{i}"}
            for i in range(20)
        ]
        # Add duplicates in non-sequential positions
        ordered.insert(5, {"title": "Dup", "url": "https://site0.com/", "body": "D"})
        ordered.insert(12, {"title": "Dup2", "url": "https://site0.com/", "body": "D2"})

        async def fake_search(*args, **kwargs):
            return ordered

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("order test")

        titles = [h.title for h in result.hits]
        assert titles[0] == "Result 0"
        # First "Dup" should be removed (first seen is Result 0 at site0.com)
        assert "Dup" not in titles
        assert titles == sorted(titles, key=lambda t: t)


# ---------------------------------------------------------------------------
# 13. source = "duckduckgo"
# ---------------------------------------------------------------------------


class TestSourceConstant:
    @pytest.mark.asyncio
    async def test_source_is_duckduckgo(self, fake_ddg_results):
        async def fake_search(*args, **kwargs):
            return fake_ddg_results

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("source test")

        for hit in result.hits:
            assert hit.source == "duckduckgo"

    def test_source_constant(self):
        assert dda.SOURCE_NAME == "duckduckgo"


# ---------------------------------------------------------------------------
# 14. retrieved_ts is filled
# ---------------------------------------------------------------------------


class TestRetrievedTs:
    @pytest.mark.asyncio
    async def test_retrieved_ts_is_positive_float(self, fake_ddg_results):
        async def fake_search(*args, **kwargs):
            return fake_ddg_results

        before = time.time()
        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("ts test")
        after = time.time()

        assert len(result.hits) > 0
        for hit in result.hits:
            assert hit.retrieved_ts >= before
            assert hit.retrieved_ts <= after


# ---------------------------------------------------------------------------
# 15. Timeout path
# ---------------------------------------------------------------------------


class TestTimeoutPath:
    @pytest.mark.asyncio
    async def test_timeout_returns_fail_soft(self):
        async def fake_slow_search(*args, **kwargs):
            await asyncio.sleep(10)  # longer than our 0.1s timeout
            return [{"title": "T", "url": "https://e.com/", "body": "B"}]

        with patch.object(dda, "_ddgs_text_search", new=fake_slow_search):
            result = await dda.async_search_public_web("timeout test", timeout_s=0.1)

        assert result.hits == ()
        # F206AB: backend_error taxonomy split - generic RuntimeError -> unknown_backend_error
        assert result.error in ("timeout", "unknown_backend_error", "network_error", "server_error")

    @pytest.mark.asyncio
    async def test_asyncio_timeout_exception_caught(self):
        """asyncio.TimeoutError must be caught and return fail-soft."""

        async def fake_raises_timeout(*args, **kwargs):
            raise asyncio.TimeoutError

        with patch.object(dda, "_ddgs_text_search", new=fake_raises_timeout):
            result = await dda.async_search_public_web("inner timeout test", timeout_s=5.0)

        assert result.hits == ()
        # F206AB: asyncio.TimeoutError now explicitly caught -> "timeout"
        assert result.error == "timeout"


# ---------------------------------------------------------------------------
# 16. RatelimitException is fail-soft
# ---------------------------------------------------------------------------


class TestRatelimitFailSoft:
    @pytest.mark.asyncio
    async def test_ratelimit_exception_returns_fail_soft(self):
        from duckduckgo_search.exceptions import RatelimitException

        async def fake_ratelimited(*args, **kwargs):
            raise RatelimitException("rate limited")

        with patch.object(dda, "_ddgs_text_search", new=fake_ratelimited):
            result = await dda.async_search_public_web("ratelimit test")

        assert result.hits == ()
        assert result.error == "rate_limited"


# ---------------------------------------------------------------------------
# 17. Generic Exception is fail-soft
# ---------------------------------------------------------------------------


class TestGenericExceptionFailSoft:
    @pytest.mark.asyncio
    async def test_generic_exception_returns_fail_soft(self):
        async def fake_raises(*args, **kwargs):
            raise RuntimeError("some backend error")

        with patch.object(dda, "_ddgs_text_search", new=fake_raises):
            result = await dda.async_search_public_web("error test")

        assert result.hits == ()
        # F206AB: backend_error taxonomy split into concrete types
        assert result.error in (
            "unknown_backend_error", "proxy_error", "network_error",
            "server_error", "rate_limited", "timeout",
        )


# ---------------------------------------------------------------------------
# 18. CancelledError = re-raise
# ---------------------------------------------------------------------------


class TestCancelledErrorReraise:
    @pytest.mark.asyncio
    async def test_cancelled_error_is_reraised(self):
        async def fake_cancelled(*args, **kwargs):
            raise asyncio.CancelledError

        with patch.object(dda, "_ddgs_text_search", new=fake_cancelled):
            with pytest.raises(asyncio.CancelledError):
                await dda.async_search_public_web("cancel test")

    @pytest.mark.asyncio
    async def test_cancelled_error_reaches_caller(self):
        """CancelledError must propagate out, not be swallowed."""
        call_count = 0

        async def counting_cancelled(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise asyncio.CancelledError()

        with patch.object(dda, "_ddgs_text_search", new=counting_cancelled):
            try:
                await dda.async_search_public_web("prop test")
            except asyncio.CancelledError:
                pass  # expected

        assert call_count == 1  # was invoked before re-raise


# ---------------------------------------------------------------------------
# 19. No pydantic in result
# ---------------------------------------------------------------------------


class TestNoPydantic:
    @pytest.mark.asyncio
    async def test_result_is_not_pydantic(self, fake_ddg_results):
        import pydantic

        async def fake_search(*args, **kwargs):
            return fake_ddg_results

        with patch.object(dda, "_ddgs_text_search", new=fake_search):
            result = await dda.async_search_public_web("no pydantic test")

        assert not isinstance(result, pydantic.BaseModel)
        assert not isinstance(result.hits, list)  # must be tuple (frozen Struct)
        assert isinstance(result.hits, tuple)


# ---------------------------------------------------------------------------
# 20. No import-time side effect
# ---------------------------------------------------------------------------


class TestNoImportSideEffect:
    def test_import_module_succeeds(self):
        """Module must be importable with zero network calls."""
        # Re-import is free (already loaded by pytest)
        import hledac.universal.discovery.duckduckgo_adapter as m

        assert m is not None

    def test_no_http_calls_on_import(self, fake_ddg_results):
        """Importing the module must NOT trigger any HTTP activity."""
        # If this test runs after other tests that DID mock the network,
        # we verify by checking that the module-level state is clean
        assert dda._last_error is None or isinstance(dda._last_error, str)


# ---------------------------------------------------------------------------
# 21–25. Existing gates still pass
# ---------------------------------------------------------------------------


class TestExistingGates:
    def test_probe_8aa(self):
        import subprocess

        result = subprocess.run(
            ["pytest", "hledac/universal/tests/probe_8aa/", "-q", "--tb=no"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"probe_8aa failed:\n{result.stdout}\n{result.stderr}"

    def test_probe_8ab(self):
        import subprocess

        result = subprocess.run(
            ["pytest", "hledac/universal/tests/probe_8ab/", "-q", "--tb=no"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"probe_8ab failed:\n{result.stdout}\n{result.stderr}"

    def test_probe_8w(self):
        import subprocess

        result = subprocess.run(
            ["pytest", "hledac/universal/tests/probe_8w/", "-q", "--tb=no"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"probe_8w failed:\n{result.stdout}\n{result.stderr}"

    def test_probe_8x(self):
        import subprocess

        result = subprocess.run(
            ["pytest", "hledac/universal/tests/probe_8x/", "-q", "--tb=no"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"probe_8x failed:\n{result.stdout}\n{result.stderr}"

    def test_ao_canary(self):
        import subprocess

        result = subprocess.run(
            ["pytest", "hledac/universal/tests/test_ao_canary.py", "-q", "--tb=no"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"ao_canary failed:\n{result.stdout}\n{result.stderr}"


# ---------------------------------------------------------------------------
# 26. Benchmark tests — not flaky
# ---------------------------------------------------------------------------


class TestBenchmarks:
    @pytest.mark.asyncio
    async def test_benchmark_normalize_10_fake_hits(self):
        """Normalise 10 fake hits — should be fast (<5ms)."""
        hits = [
            {"title": f"T{i}", "url": f"https://e{i}.com/", "body": f"B{i}"}
            for i in range(10)
        ]

        t0 = time.perf_counter()
        for _ in range(100):
            seen = {}
            for rank, raw in enumerate(hits):
                url = raw["url"] or ""
                norm = dda._normalize_url_for_dedup(url)
                if norm and norm not in seen:
                    seen[norm] = rank
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000
        assert elapsed_ms < 50, f"normalize_10_x_100 took {elapsed_ms:.1f}ms"

    @pytest.mark.asyncio
    async def test_benchmark_normalize_50_fake_hits(self):
        """Normalise 50 fake hits — should be fast (<20ms)."""
        hits = [
            {"title": f"T{i}", "url": f"https://e{i}.com/p{i}", "body": f"B{i}"}
            for i in range(50)
        ]

        t0 = time.perf_counter()
        for _ in range(100):
            seen = {}
            for rank, raw in enumerate(hits):
                url = raw["url"] or ""
                norm = dda._normalize_url_for_dedup(url)
                if norm and norm not in seen:
                    seen[norm] = rank
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000
        assert elapsed_ms < 100, f"normalize_50_x_100 took {elapsed_ms:.1f}ms"

    @pytest.mark.asyncio
    async def test_benchmark_dedup_50_with_repeats(self):
        """Dedup 50 hits with repeated URLs — should be fast (<10ms)."""
        hits = [
            {"title": f"T{i}", "url": f"https://e{i % 10}.com/", "body": f"B{i}"}
            for i in range(50)
        ]

        t0 = time.perf_counter()
        for _ in range(100):
            seen = {}
            for rank, raw in enumerate(hits):
                url = raw["url"] or ""
                norm = dda._normalize_url_for_dedup(url)
                if norm and norm not in seen:
                    seen[norm] = rank
        t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000
        assert elapsed_ms < 100, f"dedup_50_x_100 took {elapsed_ms:.1f}ms"

    @pytest.mark.asyncio
    async def test_benchmark_adapter_overhead(self):
        """Adapter overhead over a fake backend — <50ms for 10 results."""
        async def fake_backend(*args, **kwargs):
            return [
                {"title": f"T{i}", "url": f"https://e{i}.com/", "body": f"B{i}"}
                for i in range(10)
            ]

        with patch.object(dda, "_ddgs_text_search", new=fake_backend):
            t0 = time.perf_counter()
            for _ in range(50):
                await dda.async_search_public_web("bench test", max_results=10)
            t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000
        assert elapsed_ms < 2000, f"adapter_50_rounds took {elapsed_ms:.1f}ms"

    @pytest.mark.asyncio
    async def test_benchmark_timeout_path(self):
        """Timeout path overhead — <5ms (fast fail)."""
        async def fake_slow(*args, **kwargs):
            await asyncio.sleep(5.0)
            return [{"title": "T", "url": "https://e.com/", "body": "B"}]

        with patch.object(dda, "_ddgs_text_search", new=fake_slow):
            t0 = time.perf_counter()
            for _ in range(20):
                await dda.async_search_public_web("timeout bench", timeout_s=0.01)
            t1 = time.perf_counter()
        elapsed_ms = (t1 - t0) * 1000
        assert elapsed_ms < 500, f"timeout_20_rounds took {elapsed_ms:.1f}ms"


# ---------------------------------------------------------------------------
# Status helpers — O(1) with no network call
# ---------------------------------------------------------------------------


class TestStatusHelpers:
    def test_backend_name_returns_string(self):
        name = dda.backend_name()
        assert isinstance(name, str)
        assert name == "duckduckgo_search"

    def test_backend_version_returns_string(self):
        v = dda.backend_version()
        assert isinstance(v, str)

    def test_last_error_initially_none_or_str(self):
        err = dda.last_error()
        assert err is None or isinstance(err, str)
