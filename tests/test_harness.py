# tests/test_harness.py
"""Unit tests for BenchmarkHarness and benchmarks/migrate_schema.py."""

import asyncio
import json
import sys
from pathlib import Path
from unittest import mock

import psutil
import pytest

# Ensure benchmarks/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))
from benchmarks.harness import BenchmarkHarness, _percentile, _run_single_sprint_unsafe
from benchmarks.migrate_schema import migrate_record
from _core import aclose

# ---------------------------------------------------------------------------
# _percentile
# ---------------------------------------------------------------------------


class TestPercentile:
    def test_empty(self):
        assert _percentile([], 50) == 0.0

    def test_single(self):
        assert _percentile([3.0], 50) == 3.0

    def test_p50_median(self):
        vals = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert _percentile(vals, 50) == 3.0

    def test_p95(self):
        vals = [float(x) for x in range(1, 101)]  # 1.0..100.0
        p = _percentile(vals, 95)
        assert 94.0 < p < 96.0

    def test_p99(self):
        vals = [float(x) for x in range(1, 1001)]  # 1.0..1000.0
        p = _percentile(vals, 99)
        assert 989.0 < p < 991.0


# ---------------------------------------------------------------------------
# BenchmarkHarness — validation
# ---------------------------------------------------------------------------


class TestBenchmarkHarnessValidation:
    """Validation errors are raised synchronously before the event loop is touched."""

    def setup_method(self):
        self.harness = BenchmarkHarness()

    @pytest.mark.asyncio
    async def test_warmup_negative_raises(self):
        with pytest.raises(ValueError, match="warmup"):
            await self.harness.run(warmup=-1, iterations=5, query="test", output_path=Path("/tmp/x"))

    @pytest.mark.asyncio
    async def test_iterations_zero_raises(self):
        with pytest.raises(ValueError, match="iterations"):
            await self.harness.run(warmup=0, iterations=0, query="test", output_path=Path("/tmp/x"))

    @pytest.mark.asyncio
    async def test_warmup_ge_iterations_raises(self):
        with pytest.raises(ValueError, match="warmup"):
            await self.harness.run(warmup=5, iterations=3, query="test", output_path=Path("/tmp/x"))


# ---------------------------------------------------------------------------
# BenchmarkHarness.run — mock integration
# ---------------------------------------------------------------------------


class TestBenchmarkHarnessRun:
    @pytest.mark.asyncio
    async def test_run_writes_schema_v2(self, tmp_path):
        harness = BenchmarkHarness()
        out = tmp_path / "result.json"

        # Mock the sprint runner so we don't hit the network
        with mock.patch("benchmarks.harness._run_single_sprint") as mock_run:
            mock_run.return_value = {"latency_s": 1.0, "findings_count": 0}
            with mock.patch("benchmarks.harness._read_findings_count_from_latest_export", return_value=0):
                await harness.run(warmup=1, iterations=2, query="test query", output_path=out)

        assert out.exists()
        data = json.loads(out.read_text())
        assert data["schema_version"] == "2.0"

    @pytest.mark.asyncio
    async def test_warmup_excluded_from_percentiles(self, tmp_path):
        harness = BenchmarkHarness(seed=42)
        out = tmp_path / "result.json"

        call_times: list = []

        async def mock_sprint(query):
            call_times.append(query)
            await asyncio.sleep(0.001)
            return {"latency_s": 1.0, "findings_count": 0}

        with mock.patch("benchmarks.harness._run_single_sprint_unsafe", side_effect=mock_sprint):
            with mock.patch("benchmarks.harness._read_findings_count_from_latest_export", return_value=0):
                await harness.run(warmup=2, iterations=3, query="test", output_path=out)

        # Warmup calls should happen before measured calls
        # 2 warmup + 3 measured = 5 total
        assert len(call_times) == 5

        data = json.loads(out.read_text())
        # warmup=2, iterations=3 → 3 measured rows
        detail = data["iterations_detail"]
        assert len(detail) == 3
        # All should have valid latency (warmup rows not included)
        assert all(r["latency_s"] is not None for r in detail)

    @pytest.mark.asyncio
    async def test_error_iteration_continues(self, tmp_path):
        harness = BenchmarkHarness()
        out = tmp_path / "result.json"

        call_count = 0

        async def mock_sprint_error(query):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("sprint error")
            return {"latency_s": 1.0, "findings_count": 0}

        # _run() calls _run_single_sprint, so patch that
        with mock.patch("benchmarks.harness._run_single_sprint", side_effect=mock_sprint_error):
            with mock.patch("benchmarks.harness._read_findings_count_from_latest_export", return_value=0):
                await harness.run(warmup=0, iterations=3, query="test", output_path=out)

        data = json.loads(out.read_text())
        assert data["error_count"] == 1
        assert len(data["iterations_detail"]) == 3

    @pytest.mark.asyncio
    async def test_latency_stats_present(self, tmp_path):
        harness = BenchmarkHarness()
        out = tmp_path / "result.json"

        with mock.patch("benchmarks.harness._run_single_sprint") as mock_run:
            mock_run.return_value = {"latency_s": 0.5, "findings_count": 0}
            with mock.patch("benchmarks.harness._read_findings_count_from_latest_export", return_value=0):
                await harness.run(warmup=0, iterations=5, query="test", output_path=out)

        data = json.loads(out.read_text())
        lat = data["latency_s"]
        assert "p50" in lat
        assert "p95" in lat
        assert "p99" in lat
        assert "mean" in lat
        assert "std" in lat
        assert "min" in lat
        assert "max" in lat

    @pytest.mark.asyncio
    async def test_seed_reproducible(self, tmp_path):
        out1 = tmp_path / "r1.json"
        out2 = tmp_path / "r2.json"

        mock_time = 100.0
        mock_memory = mock.MagicMock()

        async def mock_sprint(_query):
            return {"latency_s": 1.0, "findings_count": 0}

        for out, seed in [(out1, 12345), (out2, 12345)]:
            h = BenchmarkHarness(seed=seed)
            with mock.patch("benchmarks.harness._run_single_sprint_unsafe", side_effect=mock_sprint):
                with mock.patch("benchmarks.harness._read_findings_count_from_latest_export", return_value=0):
                    with mock.patch("time.monotonic", return_value=mock_time):
                        with mock.patch.object(psutil.Process, "memory_info", return_value=mock_memory):
                            mock_memory.rss = 100 * 1024 * 1024
                            await h.run(warmup=0, iterations=3, query="test", output_path=out)

        d1 = json.loads(out1.read_text())
        d2 = json.loads(out2.read_text())
        # Same seed → same internal state → identical iteration detail
        assert d1["iterations_detail"] == d2["iterations_detail"]


# ---------------------------------------------------------------------------
# _run_single_sprint_unsafe
# ---------------------------------------------------------------------------


class TestRunSingleSprintUnsafe:
    @pytest.mark.asyncio
    async def test_returns_error_on_exception(self):
        async def broken(query):
            raise ValueError("boom")

        with mock.patch("benchmarks.harness._run_single_sprint", side_effect=broken):
            result = await _run_single_sprint_unsafe("query")
        assert result["latency_s"] is None
        assert "ValueError" in result["error"]
        assert "boom" in result["error"]

    @pytest.mark.asyncio
    async def test_passes_through_success(self):
        async def ok(query):
            return {"latency_s": 0.5, "findings_count": 3}

        with mock.patch("benchmarks.harness._run_single_sprint", side_effect=ok):
            result = await _run_single_sprint_unsafe("query")
        assert result["latency_s"] == 0.5
        assert result["findings_count"] == 3
        assert "error" not in result


# ---------------------------------------------------------------------------
# migrate_schema
# ---------------------------------------------------------------------------


class TestMigrateSchema:
    def test_rename_wall_clock(self):
        data = {"total_wall_clock_seconds": 1.5, "schema_version": "1.0"}
        new_data, renames = migrate_record(data)
        assert "wall_clock_s" in new_data
        assert "total_wall_clock_seconds" not in new_data
        # renames contains "new_key ← old_key"
        assert any("wall_clock_s" in r for r in renames)

    def test_rename_all_time_fields(self):
        data = {
            "total_wall_clock_seconds": 10.0,
            "research_runtime_seconds": 2.0,
            "time_to_first_finding_seconds": 1.0,
            "time_to_first_high_confidence_seconds": 3.0,
            "time_to_first_deep_read_seconds": 4.0,
            "final_synthesis_duration_seconds": 5.0,
            "schema_version": "1.0",
        }
        new_data, renames = migrate_record(data)
        assert "wall_clock_s" in new_data
        assert "research_runtime_s" in new_data
        assert "time_to_first_finding_s" in new_data
        assert "time_to_first_high_confidence_s" in new_data
        assert "time_to_first_deep_read_s" in new_data
        assert "final_synthesis_duration_s" in new_data
        assert len(renames) == 6

    def test_preserves_other_fields(self):
        data = {
            "total_wall_clock_seconds": 1.0,
            "findings_count": 5,
            "iterations": 10,
            "schema_version": "1.0",
        }
        new_data, _ = migrate_record(data)
        assert new_data["findings_count"] == 5
        assert new_data["iterations"] == 10

    def test_already_v2_unchanged(self):
        data = {"wall_clock_s": 1.0, "schema_version": "2.0"}
        new_data, renames = migrate_record(data)
        assert new_data is data
        assert renames == []


# ---------------------------------------------------------------------------
# asyncio fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("bench")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-q"])
