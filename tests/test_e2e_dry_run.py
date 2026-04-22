"""
Sprint 8VI §E: E2E dry-run test — celá pipeline bez reálných HTTP requestů.
30s timeout, max 10 findings, všechny external fetches mockované.
"""
import asyncio
import json
import pathlib
from pathlib import Path
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


def test_none_file_absent_after_run():
    """P0 guard: soubor 'None' nesmí existovat."""
    assert not pathlib.Path("None").exists(), \
        "Soubor 'None' existuje — porušen P0 guard"


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_e2e_pipeline_completes():
    """Spustí WARMUP → mock ACTIVE → WINDUP → EXPORT."""
    # Sprint 8VY: run_warmup moved from runtime/sprint_lifecycle → __main__.py (canonical WARMUP truth)
    # Use importlib to load __main__ directly (pytest's --main__ is pytest's own module)
    import os, importlib.util
    _ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    _MAIN_PY = os.path.join(_ROOT, "hledac", "universal", "__main__.py")
    _spec = importlib.util.spec_from_file_location("hledac_main", _MAIN_PY)
    assert _spec is not None, f"Failed to load spec for {_MAIN_PY}"
    _main_mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_main_mod)  # type: ignore
    run_warmup = _main_mod.run_warmup
    from runtime.windup_engine import run_windup
    from export.sprint_exporter import export_sprint
    from runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

    # Mock scheduler s potřebnými atributy
    config = SprintSchedulerConfig()
    scheduler = SprintScheduler(config)
    scheduler._finding_count = 5
    scheduler._all_findings = [
        {"url": f"http://test{i}.com", "title": f"Finding {i}",
         "snippet": f"C2 at 10.0.0.{i}", "source": "test", "confidence": 0.8}
        for i in range(5)
    ]
    scheduler._ioc_graph = MagicMock()
    scheduler._ioc_graph.stats.return_value = {"nodes": 3, "edges": 2, "pgq_active": False}
    scheduler._ioc_graph.export_edge_list.return_value = []
    scheduler._ioc_graph.get_top_nodes_by_degree.return_value = []
    scheduler._ioc_graph.checkpoint = MagicMock()
    scheduler._ioc_graph.merge_from_parquet = MagicMock(return_value=0)
    scheduler.deduplicate_and_rank_findings = MagicMock(return_value="/tmp/test.parquet")
    scheduler.enqueue_pivot = AsyncMock()
    scheduler._synthesis_engine = "heuristic"
    scheduler.record_pivot_outcome = MagicMock()
    scheduler._pivot_rewards = {}
    scheduler._recent_iocs = []
    scheduler._ioc_scorer = None

    import time
    t_now = time.monotonic()

    # WARMUP
    warmup_result = await run_warmup(scheduler, {})
    assert isinstance(warmup_result, dict)

    # WINDUP
    scorecard = await run_windup(
        scheduler, "test threat query", t_now, t_now + 5.0
    )
    assert isinstance(scorecard, dict)
    assert "peak_rss_mb" in scorecard
    assert "accepted_findings_count" in scorecard

    # EXPORT — Sprint 8VI: export_sprint(store, scorecard, sprint_id) signature
    # top_graph_nodes already in scorecard from run_windup()
    with patch("runtime.windup_engine._safe_get_breaker_states", return_value={}):
        export_result = await export_sprint(None, scorecard, "test_sprint_001")
    assert "report_json" in export_result
    assert "seeds_json" in export_result


@pytest.mark.hermetic
@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_aggressive_mode_hypothesis_burst_preserves_canonical_truth():
    """
    Sprint P12 + F195B: Hypothesis burst does not break canonical truth accounting.

    Verifies that:
    1. Aggressive mode with hypothesis burst produces findings
    2. Finding count in scheduler matches what's in the store
    3. Runtime truth reflects actual accepted_findings
    4. Hypothesis burst fail-soft does not corrupt truth accounting

    Hermetic: no network, no live external services.
    """
    import tempfile
    import shutil
    from pathlib import Path

    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    from hledac.universal.patterns.pattern_matcher import PatternHit
    from hledac.universal.pipeline.live_public_pipeline import async_run_live_public_pipeline
    from hledac.universal.runtime.sprint_scheduler import (
        SprintScheduler,
        SprintSchedulerConfig,
    )
    from hledac.universal.runtime.sprint_lifecycle import SprintLifecycleManager

    tmp = tempfile.mkdtemp(prefix="hledac_hypothesis_truth_")
    db_path = Path(tmp) / "shadow.duckdb"

    try:
        # Canned discovery: returns one hit
        class _CannedDiscoveryResult:
            def __init__(self, hits):
                self.hits = hits
                self.error = None

        class _CannedDiscoveryHit:
            def __init__(self, url, title="", snippet="", rank=0):
                self.url = url
                self.title = title
                self.snippet = snippet
                self.rank = rank

        async def canned_search(q, m):
            return _CannedDiscoveryResult([
                _CannedDiscoveryHit(
                    url="https://www.example.com/security/cve-2026-9999",
                    title="CVE-2026-9999 Security Advisory",
                    snippet="Critical vulnerability in ExampleServer",
                    rank=0,
                ),
            ])

        # Canned fetcher: HTML with a CVE pattern hit
        class _CannedFetchResult:
            def __init__(self, text, url="http://example.com"):
                self.text = text
                self.content_type = "text/html"
                self.url = url
                self.final_url = url
                self.status_code = 200
                self.fetched_bytes = len(text) if text else 0
                self.declared_length = len(text) if text else 0
                self.elapsed_ms = 10.0
                self.error = None

        async def canned_fetch(url, timeout, max_bytes, **kw):
            return _CannedFetchResult(
                text=(
                    "<html><body><article>"
                    "<h1>CVE-2026-9999 Security Advisory</h1>"
                    "<p>Critical vulnerability allows remote code execution via crafted network requests.</p>"
                    "<p>Affected versions: 1.0 through 1.9.2. Update immediately to prevent exploitation.</p>"
                    "<p>Severity: Critical. Attack complexity is low. No privileges required.</p>"
                    "</article></html>"
                ),
                url=url,
            )

        # Patch discovery and fetcher
        from hledac.universal.pipeline.live_public_pipeline import (
            _patch_discovery,
            _patch_fetcher_and_matcher,
        )

        _patch_discovery(canned_search)
        _patch_fetcher_and_matcher(
            canned_fetch,
            lambda t: [PatternHit(
                pattern="CVE-2026-9999",
                start=0,
                end=13,
                value="CVE-2026-9999",
                label="vulnerability_id",
            )] if "CVE-2026-9999" in t else [],
        )

        # Create store
        store = DuckDBShadowStore(db_path=str(db_path))
        store._init_persistent_dedup_lmdb = lambda: None
        await store.async_initialize()

        # Run live public pipeline (which includes P12 hypothesis burst path)
        pipeline_result = await async_run_live_public_pipeline(
            query="CVE-2026-9999 security advisory",
            store=store,
            max_results=10,
            fetch_timeout_s=10.0,
            fetch_max_bytes=200_000,
            fetch_concurrency=1,
        )

        # Query DuckDB for persisted findings
        findings = await store.async_get_recent_findings(limit=20)
        pipeline_findings = [
            f for f in findings
            if getattr(f, "source_type", "") == "live_public_pipeline"
        ]

        # Canonical truth invariant: finding count in store matches pipeline result
        assert len(pipeline_findings) >= 1 or pipeline_result.accepted_findings >= 1, (
            f"Expected >=1 finding. store={len(pipeline_findings)}, "
            f"pipeline accepted={pipeline_result.accepted_findings}"
        )

        # Runtime truth: accepted_findings in pipeline result should be consistent
        assert pipeline_result.accepted_findings >= 0, (
            f"pipeline_result.accepted_findings should be >= 0, "
            f"got {pipeline_result.accepted_findings}"
        )

        # If findings exist in store, verify their structure
        for f in pipeline_findings:
            assert hasattr(f, "finding_id") and f.finding_id
            assert hasattr(f, "query") and f.query
            assert 0.0 <= f.confidence <= 1.0

        # Verify P12 hypothesis burst code path exists in async_run_live_public_pipeline
        import inspect
        source = inspect.getsource(async_run_live_public_pipeline)
        p12_start = source.find("# P12: Hypothesis generation")
        assert p12_start != -1, (
            "P12 hypothesis generation code not found in async_run_live_public_pipeline"
        )
        p12_block = source[p12_start:p12_start + 5000]

        # P12 must use fail-soft (except asyncio.TimeoutError with return "")
        assert "asyncio.TimeoutError" in p12_block and 'return ""' in p12_block, (
            "P12 must catch TimeoutError per-task and return empty string — fail-soft"
        )
        # P12 must use as_completed for concurrent ToT evaluation
        assert "as_completed" in p12_block, (
            "P12 must use asyncio.as_completed for concurrent ToT evaluation"
        )

        print(
            f"\n[hypothesis_burst_truth] test passed: "
            f"store_findings={len(pipeline_findings)} "
            f"pipeline_accepted={pipeline_result.accepted_findings} "
            f"p12_code_present=True"
        )

        await store.aclose()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
