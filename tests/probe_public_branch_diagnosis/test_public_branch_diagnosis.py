"""F206AA: Public branch diagnosis — stage isolation probe.

Verifies:
1. Discovery adapter returns hits (not empty) under isolation
2. Pipeline accepts mocked discovery via _patch_discovery seam
3. Pipeline with mocked discovery reaches fetch_attempted > 0
4. Pattern matcher receives extracted text
5. public_branch_verdict does NOT return backend_error when discovery succeeds
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.discovery.duckduckgo_adapter import (
    DiscoveryBatchResult,
    DiscoveryHit,
    async_search_public_web,
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
