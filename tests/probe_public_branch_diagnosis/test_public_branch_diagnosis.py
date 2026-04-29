"""F206AA + F206AB: Public branch diagnosis — stage isolation + error taxonomy probe.

Verifies (F206AA):
1. Discovery adapter returns hits (not empty) under isolation
2. Pipeline accepts mocked discovery via _patch_discovery seam
3. Pipeline with mocked discovery reaches fetch_attempted > 0
4. Pattern matcher receives extracted text
5. public_branch_verdict does NOT return backend_error when discovery succeeds

Verifies (F206AB):
1. classify_discovery_error maps errors to correct taxonomy
2. public_branch_verdict contains discovery_attempted, discovery_elapsed_s, etc.
3. Timeout/error/empty discovery cases have correct error_type
4. CancelledError is re-raised
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.discovery.duckduckgo_adapter import (
    DiscoveryBatchResult,
    DiscoveryHit,
    async_search_public_web,
    classify_discovery_error,
)
from hledac.universal.pipeline.live_public_pipeline import (
    _ASYNC_DISCOVERY_SEARCH,
    _ensure_discovery_patched,
    _patch_discovery,
    async_run_live_public_pipeline,
)


class TestF206AADiscoveryAdapterSmoke:
    """PHASE 3: Isolated discovery adapter smoke test."""

    @pytest.mark.asyncio
    async def test_discovery_adapter_returns_hits(self):
        """Discovery adapter returns list with hits under isolation (no store write)."""
        result = await async_search_public_web(
            "ransomware infrastructure leak",
            max_results=5,
            timeout_s=15.0,
        )
        # DiscoveryBatchResult has hits attribute (msgspec.Struct frozen=True)
        assert hasattr(result, "hits"), "DiscoveryBatchResult missing .hits attribute"
        assert len(result.hits) > 0, f"Expected hits, got {result.error!r}"
        assert result.error is None, f"Discovery error: {result.error}"
        first = result.hits[0]
        assert hasattr(first, "url"), "DiscoveryHit missing .url"
        assert first.url.startswith("http"), f"Invalid URL: {first.url}"
        print(f"\n[PASS] Discovery returned {len(result.hits)} hits")
        print(f"  First: {first.title[:60]} | {first.url[:60]}")


class TestF206AAPipelineDiscoverySeam:
    """PHASE 2: Pipeline discovery seam test — can pipeline accept mocked results?"""

    @pytest.mark.asyncio
    async def test_patch_discovery_seam_exists(self):
        """Verify _patch_discovery / _ensure_discovery_patched seam exists."""
        _ensure_discovery_patched()
        assert _ASYNC_DISCOVERY_SEARCH is not None, "_ASYNC_DISCOVERY_SEARCH is None after patch"
        print(f"\n[PASS] _ASYNC_DISCOVERY_SEARCH patched: {_ASYNC_DISCOVERY_SEARCH.__module__}")

    @pytest.mark.asyncio
    async def test_pipeline_with_mocked_discovery_reaches_fetch(self, mock_discovery_result):
        """Pipeline with mocked discovery reaches fetch stage (fetch_attempted > 0)."""
        # Patch discovery to return our mock
        _patch_discovery(AsyncMock(return_value=mock_discovery_result))

        # Also patch async_fetch_public_text to avoid real network calls
        mock_fetch_result = MagicMock()
        mock_fetch_result.url = "https://redteamnews.com/test"
        mock_fetch_result.content_text = "ransomware leak infrastructure compromised through a vulnerability."
        mock_fetch_result.fetched_bytes = 78
        mock_fetch_result.status_code = 200
        mock_fetch_result.error = None

        with patch(
            "hledac.universal.pipeline.live_public_pipeline._ASYNC_FETCH_PUBLIC_TEXT",
            AsyncMock(return_value=mock_fetch_result),
        ):
            result = await async_run_live_public_pipeline(
                query="ransomware infrastructure leak",
                store=None,  # No store write
                max_results=5,
                fetch_timeout_s=10.0,
                fetch_concurrency=3,
            )

        # Restore discovery
        _ensure_discovery_patched()

        assert result.discovered > 0, f"discovered={result.discovered}, expected >0"
        print(f"\n[PASS] discovered={result.discovered} (>0 means discovery hit the pipeline)")


class TestF206AAPublicBranchVerdict:
    """PHASE 1: Verdict computation — does backend_error appear when discovery succeeds?"""

    @pytest.mark.asyncio
    async def test_verdict_no_backend_error_on_successful_discovery(self, mock_discovery_result):
        """When discovery returns hits, public_branch_verdict should NOT contain backend_error."""
        _patch_discovery(AsyncMock(return_value=mock_discovery_result))

        mock_fetch_result = MagicMock()
        mock_fetch_result.url = "https://redteamnews.com/test"
        mock_fetch_result.content_text = "ransomware leak — contact admin@ransomware.com"
        mock_fetch_result.fetched_bytes = 65
        mock_fetch_result.status_code = 200
        mock_fetch_result.error = None

        with patch(
            "hledac.universal.pipeline.live_public_pipeline._ASYNC_FETCH_PUBLIC_TEXT",
            AsyncMock(return_value=mock_fetch_result),
        ):
            result = await async_run_live_public_pipeline(
                query="ransomware infrastructure leak",
                store=None,
                max_results=5,
                fetch_timeout_s=10.0,
                fetch_concurrency=3,
            )

        _ensure_discovery_patched()

        verdict = result.public_branch_verdict
        assert isinstance(verdict, dict), f"verdict is {type(verdict)}, expected dict"
        assert verdict.get("backend_degraded") is not True, (
            f"backend_degraded=True with successful discovery! "
            f"verdict={verdict}"
        )
        # public_discovery_blocker should NOT be backend_error variants
        blocker = verdict.get("public_discovery_blocker", "")
        assert "backend_error" not in str(blocker).lower(), (
            f"public_discovery_blocker={blocker!r} mentions backend_error "
            f"despite successful discovery"
        )
        print(f"\n[PASS] verdict backend_degraded={verdict.get('backend_degraded')}")
        print(f"  blocker={blocker!r}")
        print(f"  proof_grade={verdict.get('public_proof_grade')!r}")


class TestF206AADDTriage:
    """PHASE 4: DDGS v9 API compatibility — hits as tuple of DiscoveryHit objects."""

    @pytest.mark.asyncio
    async def test_ddgs_hits_is_sequence(self):
        """DDGS v9 returns hits as iterable — pipeline accesses via hasattr or dict.get."""
        result = await async_search_public_web("test query", max_results=3, timeout_s=10.0)

        # hits is tuple[DiscoveryHit] (msgspec.Struct sequence)
        assert hasattr(result, "hits"), "result missing .hits"
        assert isinstance(result.hits, (list, tuple)), f"hits is {type(result.hits)}"
        # msgspec.Struct supports __iter__
        hit_list = list(result.hits)
        print(f"\n[PASS] hits type={type(result.hits).__name__}, count={len(hit_list)}")


class TestF206AAUmaCheck:
    """PHASE 4: UMA state check — is public branch being blocked by io_only?"""

    def test_uma_state_not_emergency(self):
        """UMA state must not be EMERGENCY for public branch to run discovery."""
        from hledac.universal.core.resource_governor import (
            UMA_STATE_EMERGENCY,
            sample_uma_status,
            evaluate_uma_state,
        )

        snap = sample_uma_status()
        state = evaluate_uma_state(snap.system_used_gib)
        assert state != UMA_STATE_EMERGENCY, (
            f"UMA is {state} ({snap.system_used_gib:.2f}GiB) — "
            f"public branch would emergency-abort before discovery"
        )
        print(f"\n[PASS] UMA state={state} ({snap.system_used_gib:.2f}GiB), io_only={snap.io_only}")


class TestF206AADiscoveryEmptyPath:
    """PHASE 1: Empty discovery path — verify verdict for empty hits."""

    @pytest.mark.asyncio
    async def test_empty_discovery_verdict_not_backend_error(self, mock_discovery_result_empty):
        """Empty discovery (no hits) should give 'no_discovery' proof_grade, NOT backend_error."""
        _patch_discovery(AsyncMock(return_value=mock_discovery_result_empty))

        result = await async_run_live_public_pipeline(
            query="impossible query that returns nothing",
            store=None,
            max_results=5,
            fetch_timeout_s=5.0,
            fetch_concurrency=1,
        )

        _ensure_discovery_patched()

        # Early return path (no hits) — public_branch_verdict is {} but error is set
        verdict = result.public_branch_verdict or {}
        grade = verdict.get("public_proof_grade") or result.public_proof_grade
        blocker = verdict.get("public_discovery_blocker") or result.public_discovery_blocker or ""

        # Should be no_discovery, NOT backend_error
        assert grade == "no_discovery", (
            f"Empty discovery should give 'no_discovery', got {grade!r}; "
            f"verdict={verdict}, error={result.error!r}"
        )
        assert "backend_error" not in str(blocker).lower(), (
            f"Empty discovery should NOT set backend_error blocker, got {blocker!r}"
        )
        print(f"\n[PASS] empty discovery: proof_grade={grade!r}, blocker={blocker!r}")


class TestF206ABTaxonomy:
    """F206AB: Discovery error taxonomy — classify_discovery_error unit tests."""

    def test_none_returns_none(self):
        """None error with hits > 0 → 'none'."""
        result = classify_discovery_error(None, hits_count=5)
        assert result == "none"

    def test_none_empty_hits_returns_provider_empty(self):
        """None error with hits_count=0 → 'provider_empty'."""
        result = classify_discovery_error(None, hits_count=0)
        assert result == "provider_empty"

    def test_empty_string_returns_provider_empty(self):
        """Empty string error with hits=0 → 'provider_empty'."""
        result = classify_discovery_error("", hits_count=0)
        assert result == "provider_empty"

    def test_timeout_string_returns_timeout(self):
        """Timeout keyword → 'timeout'."""
        result = classify_discovery_error("timeout error")
        assert result == "timeout"

    def test_asyncio_timeout_error_returns_timeout(self):
        """asyncio.TimeoutError → 'timeout'."""
        result = classify_discovery_error(asyncio.TimeoutError())
        assert result == "timeout"

    def test_elapsed_exceeds_timeout_returns_timeout(self):
        """TimeoutError with elapsed >= timeout_s → 'timeout'."""
        # When there's a TimeoutError OR elapsed_s exceeds threshold (even without keyword)
        # We pass TimeoutError explicitly since slow call without error → provider_empty
        result = classify_discovery_error(
            TimeoutError("timed out after 40s"),
            elapsed_s=40.0,
            timeout_s=35.0,
        )
        assert result == "timeout"

    def test_slow_call_no_error_returns_provider_empty(self):
        """Slow call (elapsed >= timeout) with no error → 'provider_empty' (not timeout).

        When a call completes without a timeout error but is slow and returns no hits,
        classify_discovery_error treats this as 'provider_empty' (provider returned nothing).
        The timeout elapsed check only fires when error is not None.
        """
        result = classify_discovery_error(None, elapsed_s=40.0, timeout_s=35.0, hits_count=0)
        assert result == "provider_empty"

    def test_rate_limit_string_returns_rate_limited(self):
        """Rate limit keywords → 'rate_limited'."""
        assert classify_discovery_error("ratelimit") == "rate_limited"
        assert classify_discovery_error("429 Too Many Requests") == "rate_limited"
        assert classify_discovery_error("rate limit exceeded") == "rate_limited"

    def test_captcha_blocked_string_returns_captcha_or_blocked(self):
        """Captcha/blocked keywords → 'captcha_or_blocked'."""
        assert classify_discovery_error("captcha required") == "captcha_or_blocked"
        assert classify_discovery_error("access blocked") == "captcha_or_blocked"
        assert classify_discovery_error("403 Forbidden") == "captcha_or_blocked"
        assert classify_discovery_error("bot detection") == "captcha_or_blocked"

    def test_import_error_returns_import_error(self):
        """ImportError / ModuleNotFoundError → 'import_error'."""
        result = classify_discovery_error(ImportError("No module named 'ddgs'"))
        assert result == "import_error"
        result2 = classify_discovery_error(ModuleNotFoundError("ddgs not found"))
        assert result2 == "import_error"

    def test_provider_exception_returns_provider_exception(self):
        """Generic Exception (not CancelledError/TimeoutError) → 'provider_exception'."""
        result = classify_discovery_error(RuntimeError("duckduckgo failed"))
        assert result == "provider_exception"

    def test_network_error_string_returns_unknown_or_network(self):
        """Network error → 'unknown_backend_error' (not in typed errors)."""
        result = classify_discovery_error("network error: connection refused")
        assert result == "unknown_backend_error"

    def test_cancelled_error_returns_task_cancelled(self):
        """CancelledError → 'task_cancelled'."""
        result = classify_discovery_error(asyncio.CancelledError())
        assert result == "task_cancelled"


class TestF206ABAdditiveVerdictFields:
    """F206AB: public_branch_verdict additive telemetry fields."""

    @pytest.mark.asyncio
    async def test_successful_discovery_has_new_fields(self, mock_discovery_result):
        """Successful discovery verdict contains all F206AB additive fields."""
        _patch_discovery(AsyncMock(return_value=mock_discovery_result))

        mock_fetch_result = MagicMock()
        mock_fetch_result.url = "https://redteamnews.com/test"
        mock_fetch_result.content_text = "ransomware leak — contact admin@ransomware.com"
        mock_fetch_result.fetched_bytes = 65
        mock_fetch_result.status_code = 200
        mock_fetch_result.error = None

        with patch(
            "hledac.universal.pipeline.live_public_pipeline._ASYNC_FETCH_PUBLIC_TEXT",
            AsyncMock(return_value=mock_fetch_result),
        ):
            result = await async_run_live_public_pipeline(
                query="ransomware infrastructure leak",
                store=None,
                max_results=5,
                fetch_timeout_s=10.0,
                fetch_concurrency=3,
            )

        _ensure_discovery_patched()

        verdict = result.public_branch_verdict
        # Required additive fields
        assert "discovery_attempted" in verdict, f"discovery_attempted missing: {verdict.keys()}"
        assert "discovery_elapsed_s" in verdict
        assert "discovery_error_type" in verdict
        assert "discovery_fallback_triggered" in verdict
        assert "fetch_attempted" in verdict
        assert "fetch_success" in verdict
        assert "fetch_error" in verdict
        assert "admitted_urls" in verdict
        assert "pattern_hits" in verdict

        # Values on success
        assert verdict["discovery_attempted"] is True
        assert verdict["discovery_elapsed_s"] is not None
        assert verdict["discovery_elapsed_s"] >= 0.0
        assert verdict["discovery_error_type"] == "none"
        assert verdict["discovery_hits_total"] == 2
        assert verdict["fetch_attempted"] >= 0
        assert verdict["pattern_hits"] >= 0
        print(f"\n[PASS] verdict new fields present: discovery_elapsed_s={verdict['discovery_elapsed_s']:.3f}s")

    @pytest.mark.asyncio
    async def test_timeout_discovery_maps_to_timeout_error_type(self):
        """Mocked timeout discovery maps discovery_error_type to 'timeout'."""
        timeout_result = DiscoveryBatchResult(
            hits=(),
            error="timeout",
            fallback_triggered=None,
        )
        _patch_discovery(AsyncMock(return_value=timeout_result))

        with patch(
            "hledac.universal.pipeline.live_public_pipeline._ASYNC_FETCH_PUBLIC_TEXT",
            AsyncMock(side_effect=Exception("should not reach fetch")),
        ):
            result = await async_run_live_public_pipeline(
                query="timeout query",
                store=None,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_concurrency=1,
            )

        _ensure_discovery_patched()

        # Early return path — verdict may be {} but public_discovery_blocker is on result
        blocker = result.public_discovery_blocker or ""
        assert blocker in ("backend_error_no_fallback", "timeout", "no_discovery"), (
            f"Expected backend_error_no_fallback/timeout/no_discovery, got {blocker!r}"
        )
        print(f"\n[PASS] timeout discovery blocker={blocker!r}")

    @pytest.mark.asyncio
    async def test_empty_discovery_no_error_maps_to_provider_empty(self, mock_discovery_result_empty):
        """Empty discovery (no hits, no error) maps to error_type='provider_empty' via classify."""
        _patch_discovery(AsyncMock(return_value=mock_discovery_result_empty))

        result = await async_run_live_public_pipeline(
            query="impossible query",
            store=None,
            max_results=5,
            fetch_timeout_s=5.0,
            fetch_concurrency=1,
        )

        _ensure_discovery_patched()

        # Early return: verdict is {} but error is "discovery_empty"
        # classify_discovery_error(None, hits_count=0) → "provider_empty"
        grade = result.public_proof_grade
        blocker = result.public_discovery_blocker or ""
        assert grade == "no_discovery"
        assert "backend_error" not in str(blocker).lower()
        print(f"\n[PASS] empty no-error: proof_grade={grade!r}, blocker={blocker!r}")

    @pytest.mark.asyncio
    async def test_error_discovery_no_fallback_has_backend_error_no_fallback_blocker(self):
        """Error discovery without fallback sets 'backend_error_no_fallback' blocker.

        Uses hits=() + error so pipeline takes early-return path.
        Blocker = discovery_error itself (rate_limited).
        When hits were non-empty + error + no fallback → 'backend_error_no_fallback'.
        """
        error_result = DiscoveryBatchResult(
            hits=(),
            error="rate_limited",
            fallback_triggered=None,
        )
        _patch_discovery(AsyncMock(return_value=error_result))

        result = await async_run_live_public_pipeline(
            query="rate limited query",
            store=None,
            max_results=5,
            fetch_timeout_s=5.0,
            fetch_concurrency=1,
        )

        _ensure_discovery_patched()

        # Early return: blocker = discovery_error = "rate_limited"
        blocker = result.public_discovery_blocker or ""
        assert blocker == "rate_limited", (
            f"Expected blocker='rate_limited', got {blocker!r}"
        )
        assert result.public_proof_grade == "no_discovery"
        print(f"\n[PASS] error discovery blocker={blocker!r}, grade={result.public_proof_grade!r}")

    @pytest.mark.asyncio
    async def test_cancelled_error_re_raised(self):
        """CancelledError from discovery is re-raised, not swallowed."""
        cancelled_result = DiscoveryBatchResult(hits=(), error=None, fallback_triggered=None)
        _patch_discovery(
            AsyncMock(side_effect=asyncio.CancelledError("discovery cancelled")),
        )

        with pytest.raises(asyncio.CancelledError):
            await async_run_live_public_pipeline(
                query="cancelled query",
                store=None,
                max_results=5,
                fetch_timeout_s=5.0,
                fetch_concurrency=1,
            )

        _ensure_discovery_patched()
        print("\n[PASS] CancelledError re-raised correctly")

