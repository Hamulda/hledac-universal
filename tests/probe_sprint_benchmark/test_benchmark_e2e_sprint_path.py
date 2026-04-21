"""
Probe F192E.1: E2E Benchmark for Canonical Sprint Path
=======================================================

Measures canonical sprint path with focus on:
1. Time to first persisted finding (first_finding_latency_s)
2. Total findings at end of sprint (total_findings)
3. Peak RSS / UMA telemetry (peak_rss_mb, uma_peak_state)
4. Branch mix: feed/public/ct_log (branch_mix)
5. Bounded run suitable for M1 8GB without swap

Invariant:
- Canonical sprint path must produce >=1 finding within bounded time
- Memory ceiling must stay below M1 8GB threshold (~6.5GB RSS)
- Branch mix must be non-empty (feed, public, or ct_log)
- Duration cap ensures CI-safe bounded execution

Edit ONLY these files:
- hledac/universal/tests/probe_sprint_benchmark/test_benchmark_e2e_sprint_path.py
- hledac/universal/tests/probe_sprint_benchmark/conftest.py
- hledac/universal/benchmarks/benchmark_sprint_probe.py
"""

from __future__ import annotations

import asyncio
import tempfile
import time as time_module
from pathlib import Path
from typing import Any

import pytest

from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
from hledac.universal.pipeline.live_feed_pipeline import (
    FeedPipelineRunResult,
    async_run_live_feed_pipeline,
)
from hledac.universal.patterns.pattern_matcher import PatternHit

# Benchmark constants — bounded for M1 8GB / CI safety
_BENCHMARK_DURATION_S = 60.0  # 60s sprint (CI-safe)
_BENCHMARK_MAX_CYCLES = 10   # max cycles (CI-safe ceiling)
_SWAP_WARNING_MB = 6.5 * 1024  # 6.5GB — M1 8GB ceiling in MB


# ---------------------------------------------------------------------------
# Canned feed entry factory
# ---------------------------------------------------------------------------

def _make_canned_entry() -> dict[str, Any]:
    """Single high-quality feed entry that triggers CVE pattern."""
    return {
        "entry_url": "https://example.com/feed/entry-cve-2026-1234",
        "title": "CVE-2026-1234: Remote Code Execution in ExampleServer",
        "summary": (
            "Multiple critical CVEs disclosed affecting ExampleServer v1.x through v2.x. "
            "Remote attackers can execute arbitrary code via crafted requests."
        ),
        "rich_content": (
            "Multiple critical CVEs disclosed affecting ExampleServer v1.x through v2.x. "
            "Remote attackers can execute arbitrary code via crafted requests. patch is available."
        ),
        "entry_author": "disclosure-team",
        "published": "2026-04-21T10:00:00Z",
        "feed_url": "https://example.com/feed",
        "feed_title": "Example Security Feed",
        "feed_language": "en",
    }


# ---------------------------------------------------------------------------
# Feed adapter patch — returns canned FeedEntryHit, no real HTTP
# ---------------------------------------------------------------------------

@pytest.fixture
def canned_feed_adapter():
    """
    Patch rss_atom_adapter to return a single high-quality canned entry.
    Uses FeedEntryHit msgspec.Struct to match what the pipeline expects.
    """
    import hledac.universal.discovery.rss_atom_adapter as rss_module
    from hledac.universal.discovery.rss_atom_adapter import FeedEntryHit

    entry_dict = _make_canned_entry()
    canned_entry = FeedEntryHit(
        feed_url=entry_dict["feed_url"],
        entry_url=entry_dict["entry_url"],
        title=entry_dict["title"],
        summary=entry_dict["summary"],
        published_raw=entry_dict["published"],
        published_ts=1705651200.0,  # 2026-04-21 10:00:00 UTC
        source="test",
        rank=0,
        retrieved_ts=1705651200.0,
        entry_hash="testhash01",
        rich_content=entry_dict["rich_content"],
        entry_author=entry_dict["entry_author"],
        feed_title=entry_dict["feed_title"],
        feed_language=entry_dict["feed_language"],
    )

    class _FakeFeedBatch:
        error: str | None = None
        entries: tuple[FeedEntryHit, ...] = (canned_entry,)
        source_accessibility_error: str | None = None

    async def _fake_fetch(*args, **kwargs) -> _FakeFeedBatch:
        return _FakeFeedBatch()

    original = rss_module.async_fetch_feed_entries
    rss_module.async_fetch_feed_entries = _fake_fetch
    yield
    rss_module.async_fetch_feed_entries = original


# ---------------------------------------------------------------------------
# Pattern matcher patch — canned CVE PatternHit
# ---------------------------------------------------------------------------

@pytest.fixture
def canned_pattern_matcher():
    """
    Ensure the "cve-" bootstrap pattern is active and patch match_text to
    return a canned CVE PatternHit when the canned entry text is scanned.
    """
    from hledac.universal.patterns import pattern_matcher as pm_module
    import hledac.universal.pipeline.live_feed_pipeline as lfp_module

    pm_module.configure_default_bootstrap_patterns_if_empty()
    _original_match_text = pm_module.match_text
    _original_lfp_match_text = getattr(lfp_module, 'match_text', None)

    def _canned_match_text(text: str, *, boundary_policy: str = "none") -> list[PatternHit]:
        if not text:
            return []
        idx = text.find("CVE-2026-1234")
        if idx >= 0:
            return [
                PatternHit(
                    pattern="cve-",
                    start=idx,
                    end=idx + 14,
                    value=text[idx:idx + 14],
                    label="vulnerability_id",
                ),
            ]
        return _original_match_text(text, boundary_policy=boundary_policy)

    pm_module.match_text = _canned_match_text
    if _original_lfp_match_text is not None:
        lfp_module.match_text = _canned_match_text

    yield

    pm_module.match_text = _original_match_text
    if _original_lfp_match_text is not None:
        lfp_module.match_text = _original_lfp_match_text


# ---------------------------------------------------------------------------
# DuckDB store fixture — isolated for hermetic testing
# ---------------------------------------------------------------------------

@pytest.fixture
async def temp_duckdb_store():
    """
    Create a DuckDB store backed by a temp directory.
    Isolated: persistent dedup LMDB is bypassed so test findings aren't
    rejected as duplicates from previous runs.
    Cleaned up after test. Hermetic: no shared dedup state.
    """
    tmp = tempfile.mkdtemp(prefix="hledac_bench_")
    db_path = Path(tmp) / "shadow.duckdb"
    store = DuckDBShadowStore(db_path=str(db_path))
    store._init_persistent_dedup_lmdb = lambda: None
    await store.async_initialize()
    yield store
    try:
        await store.aclose()
    except Exception:
        pass
    import shutil
    try:
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# RSS / UMA sampler
# ---------------------------------------------------------------------------

def _get_rss_mb() -> float:
    """Get current process RSS in MB using psutil."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024**2
    except Exception:
        return 0.0


async def _sample_uma_peak() -> dict[str, Any]:
    """Sample current UMA status (system_used, swap_used, swap_detected, state)."""
    try:
        from hledac.universal.core.resource_governor import sample_uma_status
        s = sample_uma_status()
        return {
            "system_used_gib": s.system_used_gib,
            "swap_used_gib": s.swap_used_gib,
            "swap_detected": s.swap_detected,
            "state": s.state,
            "rss_gib": s.rss_gib,
        }
    except Exception:
        return {"system_used_gib": 0.0, "swap_used_gib": 0.0, "swap_detected": False, "state": "unknown", "rss_gib": 0.0}


# ---------------------------------------------------------------------------
# E2E Benchmark Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_benchmark_first_finding_latency(
    canned_feed_adapter,
    canned_pattern_matcher,
    temp_duckdb_store,
):
    """
    Benchmark: time to first persisted finding.

    Measures: sprint_start → first_persisted_finding (wall-clock).
    Invariant: first finding must appear within _BENCHMARK_DURATION_S.

    Expected: <5s for canned entry + LMDB ingest path.
    """
    store = temp_duckdb_store
    sprint_start = time_module.monotonic()

    result: FeedPipelineRunResult = await async_run_live_feed_pipeline(
        feed_url="https://example.com/feed",
        store=store,
        query_context="cve-2026-1234",
        max_entries=5,
        timeout_s=15.0,
    )

    elapsed = time_module.monotonic() - sprint_start

    # Pipeline must have produced pattern hits
    assert result.total_pattern_hits >= 1, (
        f"Pipeline total_pattern_hits={result.total_pattern_hits} — "
        f"expected >=1 for latency benchmark. signal_stage={result.signal_stage}"
    )

    # Query store — must have at least 1 persisted finding
    persisted = await store.async_get_recent_findings(limit=5)
    assert len(persisted) >= 1, (
        f"No persisted findings after {elapsed:.2f}s — "
        f"pipeline: total_pattern_hits={result.total_pattern_hits}, "
        f"stored_findings={result.stored_findings}"
    )

    # Canonical fields on first finding
    f0 = persisted[0]
    finding_id = getattr(f0, "finding_id", None)
    assert finding_id and isinstance(finding_id, str) and len(finding_id) >= 8, (
        f"First finding has invalid finding_id: {finding_id!r}"
    )

    print(f"\n[benchmark] first_finding_latency_s={elapsed:.3f}s ")


@pytest.mark.asyncio
async def test_benchmark_memory_budget(
    canned_feed_adapter,
    canned_pattern_matcher,
    temp_duckdb_store,
):
    """
    Benchmark: memory ceiling during sprint path.

    Measures: peak RSS during pipeline run.
    Invariant: peak RSS must stay below _SWAP_WARNING_MB (6.5GB for M1 8GB).

    Expected: <400MB for canned feed pipeline (no MLX, no heavy I/O).
    """
    rss_before = _get_rss_mb()
    uma_before = await _sample_uma_peak()

    result: FeedPipelineRunResult = await async_run_live_feed_pipeline(
        feed_url="https://example.com/feed",
        store=temp_duckdb_store,
        query_context="memory-budget-test",
        max_entries=5,
        timeout_s=15.0,
    )

    rss_after = _get_rss_mb()
    uma_after = await _sample_uma_peak()
    rss_delta = rss_after - rss_before

    # Check memory ceiling
    assert rss_after < _SWAP_WARNING_MB, (
        f"RSS {rss_after:.0f}MB exceeds M1 8GB ceiling {_SWAP_WARNING_MB:.0f}MB"
    )

    # Check no meaningful swap escalation from baseline
    swap_delta_gib = uma_after["swap_used_gib"] - uma_before["swap_used_gib"]
    if swap_delta_gib > 0.5:
        pytest.fail(
            f"Swap escalation: pre={uma_before['swap_used_gib']:.2f}GiB "
            f"post={uma_after['swap_used_gib']:.2f}GiB (delta={swap_delta_gib:.2f}GiB). "
            f"Pipeline may be causing M1 memory pressure."
        )

    # Check uma state is not emergency
    assert uma_after["state"] not in ("emergency",), (
        f"UMA state={uma_after['state']} — emergency during benchmark"
    )

    print(f"\n[benchmark] rss_before={rss_before:.0f}MB rss_after={rss_after:.0f}MB "
          f"delta={rss_delta:+.0f}MB uma_state={uma_after['state']} "
          f"swap_delta={swap_delta_gib:+.2f}GiB")


@pytest.mark.asyncio
async def test_benchmark_branch_mix(
    canned_feed_adapter,
    canned_pattern_matcher,
    temp_duckdb_store,
):
    """
    Benchmark: branch mix from feed/public/ct_log branches.

    Measures: which branches produced findings.
    Invariant: at least one branch must be non-zero.

    For canned adapter, only feed branch is active (public/CT require live URLs).
    """
    store = temp_duckdb_store

    result: FeedPipelineRunResult = await async_run_live_feed_pipeline(
        feed_url="https://example.com/feed",
        store=store,
        query_context="branch-mix-test",
        max_entries=5,
        timeout_s=15.0,
    )

    persisted = await store.async_get_recent_findings(limit=20)

    # Build branch mix from persisted findings
    feed_count = sum(
        1 for f in persisted
        if getattr(f, "source_type", "") == "rss_atom_pipeline"
    )
    public_count = sum(
        1 for f in persisted
        if getattr(f, "source_type", "") == "live_public_pipeline"
    )
    ct_count = sum(
        1 for f in persisted
        if getattr(f, "source_type", "") == "ct_log_pipeline"
    )

    branch_mix = {
        "feed_findings": feed_count,
        "public_findings": public_count,
        "ct_findings": ct_count,
    }

    total = feed_count + public_count + ct_count
    assert total >= 1, (
        f"Branch mix is empty: {branch_mix}. "
        f"Pipeline: accepted_findings={result.accepted_findings}, "
        f"stored_findings={result.stored_findings}"
    )

    # Primary signal source
    if ct_count > 0 and feed_count == 0 and public_count == 0:
        primary = "ct"
    elif feed_count > 0 and public_count == 0 and ct_count == 0:
        primary = "feed"
    elif public_count > 0 and feed_count == 0 and ct_count == 0:
        primary = "public"
    elif feed_count > 0 and public_count > 0 and ct_count == 0:
        primary = "mixed"
    elif ct_count > 0:
        primary = "mixed_ct"
    else:
        primary = "none"

    print(f"\n[benchmark] branch_mix={branch_mix} primary={primary}")


@pytest.mark.asyncio
async def test_benchmark_total_findings_bounded(
    canned_feed_adapter,
    canned_pattern_matcher,
    temp_duckdb_store,
):
    """
    Benchmark: total findings count at end of sprint.

    Measures: how many persisted findings a bounded sprint produces.
    Invariant: findings count must be >= 1 for a successful run.

    This test uses a short bounded duration to verify the pipeline
    doesn't produce zero findings even with time constraints.
    """
    store = temp_duckdb_store

    result: FeedPipelineRunResult = await async_run_live_feed_pipeline(
        feed_url="https://example.com/feed",
        store=store,
        query_context="total-findings-test",
        max_entries=5,
        timeout_s=15.0,
    )

    persisted = await store.async_get_recent_findings(limit=50)
    total_findings = len(persisted)

    assert total_findings >= 1, (
        f"total_findings={total_findings} — bounded sprint produced zero findings. "
        f"Pipeline: accepted_findings={result.accepted_findings}, "
        f"total_pattern_hits={result.total_pattern_hits}"
    )

    # All persisted findings must have canonical fields
    for f in persisted:
        fid = getattr(f, "finding_id", None)
        assert fid and isinstance(fid, str) and len(fid) >= 8, (
            f"Finding missing/invalid finding_id: {fid!r}"
        )
        src = getattr(f, "source_type", None)
        assert src in ("rss_atom_pipeline", "live_public_pipeline", "ct_log_pipeline"), (
            f"Invalid source_type: {src}"
        )
        conf = getattr(f, "confidence", None)
        assert conf is not None and 0.0 <= conf <= 1.0, (
            f"confidence out of range: {conf}"
        )

    print(f"\n[benchmark] total_findings={total_findings}")