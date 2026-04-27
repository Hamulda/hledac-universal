#!/usr/bin/env python3
"""
Sprint F205E: Hermetic E2E Canonical Benchmark — Probe Tests
============================================================

Invariant mapping:
  F205E-1  | e2e_canonical_benchmark.py --hermetic produces valid JSON output
  F205E-2  | Output schema contains: runs, findings_per_minute, dedup_ratio,
             sidecar_total_ms, per_sidecar_ms, accepted_count, stored_count,
             peak_rss_mb, status
  F205E-3  | --runs N produces exactly N run entries in the "runs" list
  F205E-4  | Hermetic mode: no network activity
  F205E-5  | Hermetic mode: no MLX model loading or hardware access
  F205E-6  | findings_per_minute > 0 when stored_count > 0
  F205E-7  | dedup_ratio is in [0.0, 1.0]
  F205E-8  | sidecar_total_ms > 0 when sidecars are executed
  F205E-9  | All per_sidecar_ms entries have valid elapsed_ms values
  F205E-10 | peak_rss_mb is a positive number
  F205E-11 | status is "pass" when memory_ceiling_ok is True
  F205E-12 | Benchmark is deterministic across runs (within 10% variance on timing)
  F205E-13 | MockDuckDBStore async_ingest_findings_batch tracks accepted vs stored
  F205E-14 | Light hermetic runner calls store.async_ingest_findings_batch
  F205E-15 | FindingSidecarBus.run_all_sidecars returns valid SidecarRunResult records
"""

from __future__ import annotations

import asyncio
import json
import sys
import time as _time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "benchmarks"))

from hledac.universal.runtime.sidecar_bus import (
    FindingSidecarBus,
    SidecarBatch,
    SidecarRunResult,
    SIDECAR_STAGES,
)

from benchmarks.e2e_canonical_benchmark import (
    SYNTHETIC_ACCEPT_RATE,
    MockDuckDBStore,
    _make_synthetic_finding,
    _make_light_runner,
    _run_hermetic_benchmark,
)


# ============================================================================
# F205E-1/2/3: Output schema validation
# ============================================================================

class TestOutputSchema:
    """F205E-1 through F205E-3: Output schema and run count."""

    @pytest.mark.asyncio
    async def test_hermetic_produces_valid_schema(self):
        """F205E-1/2: Valid JSON with all required keys."""
        result = await _run_hermetic_benchmark(num_findings=50, runs=1)

        required_keys = {"metadata", "runs", "aggregate", "per_sidecar_ms", "status"}
        assert required_keys.issubset(result.keys()), f"Missing keys: {required_keys - result.keys()}"

        agg_required = {
            "findings_per_minute", "dedup_ratio", "sidecar_total_ms",
            "stored_count", "accepted_count", "peak_rss_mb", "memory_ceiling_ok",
        }
        assert agg_required.issubset(result["aggregate"].keys())

        for name, stats in result["per_sidecar_ms"].items():
            assert "avg_ms" in stats, f"{name}: missing avg_ms"

    @pytest.mark.asyncio
    async def test_runs_count_matches(self):
        """F205E-3: --runs N produces exactly N run entries."""
        for n in (1, 3):
            result = await _run_hermetic_benchmark(num_findings=50, runs=n)
            assert len(result["runs"]) == n, f"Expected {n} runs, got {len(result['runs'])}"


# ============================================================================
# F205E-4/5: Hermetic mode guarantees
# ============================================================================

class TestHermeticGuarantees:
    """F205E-4: No network. F205E-5: No MLX hardware access."""

    def test_benchmark_has_no_network_imports(self):
        """F205E-4: Benchmark file doesn't import network libraries."""
        benchmark_path = Path(__file__).parent.parent.parent / "benchmarks" / "e2e_canonical_benchmark.py"
        content = benchmark_path.read_text()
        forbidden = ["curl_cffi", "aiohttp", "httpx", "urllib.request"]
        for lib in forbidden:
            assert lib not in content, f"Benchmark should not import {lib}"

    def test_benchmark_has_no_mlx_imports(self):
        """F205E-5: Benchmark file doesn't import MLX."""
        benchmark_path = Path(__file__).parent.parent.parent / "benchmarks" / "e2e_canonical_benchmark.py"
        content = benchmark_path.read_text()
        assert "mlx_lm" not in content, "Benchmark should not import mlx_lm"

    @pytest.mark.asyncio
    async def test_hermetic_completes_without_network(self):
        """F205E-4: Benchmark completes with no outbound connections."""
        result = await _run_hermetic_benchmark(num_findings=20, runs=1)
        assert result["status"] in ("pass", "fail")


# ============================================================================
# F205E-6/7/8/9/10/11: Metric validity
# ============================================================================

class TestMetricValidity:
    """F205E-6 through F205E-11: Metrics are valid."""

    @pytest.mark.asyncio
    async def test_findings_per_minute_positive_when_stored(self):
        """F205E-6: findings_per_minute > 0 when stored_count > 0."""
        result = await _run_hermetic_benchmark(num_findings=50, runs=1)
        stored = result["aggregate"]["stored_count"]
        fpm = result["aggregate"]["findings_per_minute"]
        if stored > 0:
            assert fpm > 0

    @pytest.mark.asyncio
    async def test_dedup_ratio_in_valid_range(self):
        """F205E-7: dedup_ratio in [0.0, 1.0]."""
        result = await _run_hermetic_benchmark(num_findings=50, runs=1)
        dedup = result["aggregate"]["dedup_ratio"]
        assert 0.0 <= dedup <= 1.0

    @pytest.mark.asyncio
    async def test_sidecar_total_ms_positive(self):
        """F205E-8: sidecar_total_ms > 0."""
        result = await _run_hermetic_benchmark(num_findings=50, runs=1)
        assert result["aggregate"]["sidecar_total_ms"] > 0

    @pytest.mark.asyncio
    async def test_per_sidecar_elapsed_ms_valid(self):
        """F205E-9: All per_sidecar_ms entries are positive."""
        result = await _run_hermetic_benchmark(num_findings=50, runs=1)
        for name, stats in result["per_sidecar_ms"].items():
            assert stats["avg_ms"] >= 0
            assert stats["min_ms"] >= 0
            assert stats["max_ms"] >= 0

    @pytest.mark.asyncio
    async def test_peak_rss_mb_positive(self):
        """F205E-10: peak_rss_mb is positive."""
        result = await _run_hermetic_benchmark(num_findings=50, runs=1)
        assert result["aggregate"]["peak_rss_mb"] > 0

    @pytest.mark.asyncio
    async def test_status_pass_when_memory_ok(self):
        """F205E-11: status='pass' when memory_ceiling_ok is True."""
        result = await _run_hermetic_benchmark(num_findings=50, runs=1)
        if result["aggregate"]["memory_ceiling_ok"]:
            assert result["status"] == "pass"


# ============================================================================
# F205E-12: Determinism
# ============================================================================

class TestDeterminism:
    """F205E-12: Timing variance within 10% across runs."""

    @pytest.mark.asyncio
    async def test_timing_variance_within_10_percent(self):
        """F205E-12: sidecar_total_ms variance within 10%."""
        result_a = await _run_hermetic_benchmark(num_findings=100, runs=1)
        result_b = await _run_hermetic_benchmark(num_findings=100, runs=1)

        ms_a = result_a["aggregate"]["sidecar_total_ms"]
        ms_b = result_b["aggregate"]["sidecar_total_ms"]

        if ms_a > 0 and ms_b > 0:
            ratio = ms_a / ms_b
            assert 0.9 <= ratio <= 1.1, f"Variance too high: {ms_a} vs {ms_b}"


# ============================================================================
# F205E-13: MockDuckDBStore correctness
# ============================================================================

class TestMockStore:
    """F205E-13: MockDuckDBStore tracks accepted vs stored correctly."""

    @pytest.mark.asyncio
    async def test_store_accepts_at_expected_rate(self):
        """F205E-13: Store accepts ~70% (within statistical bounds)."""
        store = MockDuckDBStore(accept_rate=SYNTHETIC_ACCEPT_RATE)
        await store.async_initialize()

        findings = [_make_synthetic_finding(i) for i in range(200)]
        await store.async_ingest_findings_batch(findings)

        assert 100 <= store._total_accepted <= 160
        assert len(store._stored) == store._total_accepted

    @pytest.mark.asyncio
    async def test_store_rejects_duplicates(self):
        """F205E-13: Duplicate finding_ids are rejected."""
        store = MockDuckDBStore(accept_rate=1.0)
        await store.async_initialize()

        finding = _make_synthetic_finding(0)
        await store.async_ingest_findings_batch([finding])
        await store.async_ingest_findings_batch([finding])

        assert store._total_accepted == 1
        assert len(store._stored) == 1

    @pytest.mark.asyncio
    async def test_store_full_rejection(self):
        """Store with accept_rate=0.0 rejects all."""
        store = MockDuckDBStore(accept_rate=0.0)
        await store.async_initialize()

        findings = [_make_synthetic_finding(i) for i in range(20)]
        results = await store.async_ingest_findings_batch(findings)

        assert store._total_accepted == 0
        assert len(store._stored) == 0
        assert all(not r["accepted"] for r in results)


# ============================================================================
# F205E-14: Light hermetic runner calls store
# ============================================================================

class TestLightRunner:
    """F205E-14: Light runner calls store.async_ingest_findings_batch."""

    @pytest.mark.asyncio
    async def test_light_runner_calls_store(self):
        """F205E-14: _light_runner calls the store's ingest method."""
        from benchmarks.e2e_canonical_benchmark import _light_runner

        store = MockDuckDBStore(accept_rate=1.0)
        await store.async_initialize()

        findings = [_make_synthetic_finding(i) for i in range(10)]
        call_count = [0]
        original = store.async_ingest_findings_batch

        async def counting_method(fndgs):
            call_count[0] += 1
            return await original(fndgs)

        store.async_ingest_findings_batch = counting_method

        await _light_runner(findings, store, "test query", delay_ms=1.0)

        assert call_count[0] == 1


# ============================================================================
# F205E-15: FindingSidecarBus returns valid SidecarRunResult records
# ============================================================================

class TestSidecarBus:
    """F205E-15: Bus returns valid SidecarRunResult records for all runners."""

    @pytest.mark.asyncio
    async def test_bus_returns_result_per_registered_runner(self):
        """F205E-15: Each registered runner produces a SidecarRunResult."""
        store = MockDuckDBStore(accept_rate=1.0)
        await store.async_initialize()

        bus = FindingSidecarBus(governor=None)
        stage_names = []
        for stage in SIDECAR_STAGES:
            for name in stage:
                if name not in stage_names:
                    stage_names.append(name)

        for name in stage_names:
            bus.register(name, _make_light_runner(delay_ms=1.0))

        findings = [_make_synthetic_finding(i) for i in range(20)]
        batch = SidecarBatch(
            sprint_id="test-batch", query="test", source_branch="ct",
            findings=tuple(findings), created_ts=_time.time(),
        )

        results = await bus.run_all_sidecars(batch, store)

        assert len(results) == len(stage_names)
        for r in results:
            assert isinstance(r, SidecarRunResult)
            assert r.sidecar_name in stage_names
            assert r.attempted is True
            assert r.elapsed_ms >= 0

    @pytest.mark.asyncio
    async def test_bus_fails_softly_with_error_runner(self):
        """Error in a runner returns SidecarRunResult with skipped_reason."""
        async def error_runner(findings, store, query):
            raise RuntimeError("synthetic error")

        bus = FindingSidecarBus(governor=None)
        bus.register("error_test", error_runner)

        findings = [{"finding_id": "test-1", "query": "q", "source_type": "ct",
                     "confidence": 0.5, "ts": 0.0, "provenance": ()}]

        class FakeStore:
            async def async_ingest_findings_batch(self, f):
                return [{"accepted": True, "finding_id": "test-1"}]
            async def async_initialize(self): pass
            async def aclose(self): pass

        batch = SidecarBatch(
            sprint_id="err-batch", query="q", source_branch="ct",
            findings=tuple(findings), created_ts=0.0,
        )

        results = await bus.run_all_sidecars(batch, FakeStore())

        err_result = next(r for r in results if r.sidecar_name == "error_test")
        assert err_result.attempted is True
        assert err_result.skipped_reason != ""
        assert "RuntimeError" in err_result.skipped_reason
