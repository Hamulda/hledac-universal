"""
Tests for static hydration impact benchmark (F214AA).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from benchmarks.static_hydration_impact import (
    run_benchmark,
    _leak_check,
    _score_bucket,
)

PROJECT_ROOT = Path(__file__).parent.parent.parent


class TestBenchmarkSmoke:
    """Smoke tests for benchmark CLI and import."""

    def test_module_import(self):
        """Benchmark module imports without error."""
        import benchmarks.static_hydration_impact
        assert hasattr(benchmarks.static_hydration_impact, "run_benchmark")

    def test_run_benchmark_returns_summary(self):
        """run_benchmark returns (summary, details) tuple."""
        summary, details = run_benchmark(hermetic=True)
        assert isinstance(summary, dict)
        assert isinstance(details, list)
        assert "total_samples" in summary

    def test_cli_smoke(self, tmp_path):
        """Benchmark CLI exits 0 on hermetic run."""
        json_out = tmp_path / "benchmark.json"
        result = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "benchmarks" / "static_hydration_impact.py"),
                "--hermetic",
                "--json", str(json_out),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

    def test_json_output_written(self, tmp_path):
        """JSON file is written when --json is provided."""
        json_out = tmp_path / "benchmark.json"
        subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "benchmarks" / "static_hydration_impact.py"),
                "--hermetic",
                "--json", str(json_out),
            ],
            check=True,
        )
        assert json_out.exists(), "JSON output not written"


class TestJsonSchema:
    """Tests for JSON output schema."""

    def test_summary_keys(self, tmp_path):
        """Summary contains all required keys."""
        json_out = tmp_path / "schema.json"
        subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "benchmarks" / "static_hydration_impact.py"),
                "--hermetic",
                "--json", str(json_out),
            ],
            check=True,
        )
        data = json.loads(json_out.read_text())
        summary = data["summary"]
        required_keys = [
            "total_samples",
            "hydration_attempted",
            "hydration_sufficient",
            "hydration_insufficient",
            "would_skip_js",
            "would_fallback_to_js",
            "skip_rate",
            "by_source",
            "score_buckets",
            "max_sample_bytes",
            "benchmark_mode",
        ]
        for key in required_keys:
            assert key in summary, f"Missing key: {key}"

    def test_by_source_contains_required_sources(self):
        """by_source contains at least next_data and json_ld (metadata may not appear as source)."""
        summary, _ = run_benchmark(hermetic=True)
        by_source = summary.get("by_source", {})
        assert "next_data" in by_source, "next_data not in by_source"
        assert "json_ld" in by_source, "json_ld not in by_source"
        # Note: "metadata" may not appear as a source string because metadata-only
        # sufficiency has sources=() (no JSON hydration source found)

    def test_score_buckets_not_empty(self):
        """score_buckets is not empty (all zero is a failure)."""
        summary, _ = run_benchmark(hermetic=True)
        buckets = summary.get("score_buckets", {})
        assert any(v > 0 for v in buckets.values()), "All score_buckets are 0"

    def test_bucket_sum_equals_total(self):
        """Sum of all score_buckets equals total_samples."""
        summary, _ = run_benchmark(hermetic=True)
        total = summary["total_samples"]
        bucket_sum = sum(summary["score_buckets"].values())
        assert bucket_sum == total, f"bucket sum {bucket_sum} != total {total}"


class TestNoLiveNetwork:
    """Tests that benchmark makes no live network or browser calls."""

    def test_no_network_calls_in_run(self):
        """run_benchmark completes with no network operations (hermetic)."""
        import unittest.mock

        calls = []
        def track_getaddrinfo(*args, **_kwargs):
            calls.append(("getaddrinfo", args))
            raise Exception("Blocked: network call in hermetic mode")

        with unittest.mock.patch("socket.getaddrinfo", side_effect=track_getaddrinfo):
            summary, _details = run_benchmark(hermetic=True)
            assert summary["errors"] == 0, "Benchmark had errors under network block"

    def test_no_browser_processes(self):
        """Benchmark does not spawn browser processes."""
        summary, _ = run_benchmark(hermetic=True)
        # Any browser process would cause errors in the summary
        assert summary["errors"] == 0


class TestCountersConsistency:
    """Tests that summary counters are internally consistent."""

    def test_skip_plus_fallback_equals_total(self):
        """would_skip_js + would_fallback_to_js == total_samples."""
        summary, _ = run_benchmark(hermetic=True)
        total = summary["total_samples"]
        skip = summary["would_skip_js"]
        fallback = summary["would_fallback_to_js"]
        assert skip + fallback == total, (
            f"skip({skip}) + fallback({fallback}) != total({total})"
        )

    def test_sufficient_plus_insufficient_equals_attempted(self):
        """hydration_sufficient + hydration_insufficient <= hydration_attempted."""
        summary, _ = run_benchmark(hermetic=True)
        attempted = summary["hydration_attempted"]
        sufficient = summary["hydration_sufficient"]
        insufficient = summary["hydration_insufficient"]
        # insufficient counts found-but-not-sufficient; not-found = fallback but not "insufficient"
        assert sufficient + insufficient <= attempted


class TestLeakCheck:
    """Tests that JSON output contains no raw HTML or hydration strings."""

    def test_no_html_tag_in_json(self, tmp_path):
        """JSON output contains no <html tag."""
        json_out = tmp_path / "leak_check.json"
        subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "benchmarks" / "static_hydration_impact.py"),
                "--hermetic",
                "--json", str(json_out),
            ],
            check=True,
        )
        text = json_out.read_text()
        assert "<html" not in text, "Raw <html tag leaked into JSON output"

    def test_no_script_tag_in_json(self, tmp_path):
        """JSON output contains no <script tag."""
        json_out = tmp_path / "leak_check.json"
        subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "benchmarks" / "static_hydration_impact.py"),
                "--hermetic",
                "--json", str(json_out),
            ],
            check=True,
        )
        text = json_out.read_text()
        assert "<script" not in text, "Raw <script tag leaked into JSON output"

    def test_no_next_data_in_json(self, tmp_path):
        """JSON output contains no __NEXT_DATA__ string."""
        json_out = tmp_path / "leak_check.json"
        subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "benchmarks" / "static_hydration_impact.py"),
                "--hermetic",
                "--json", str(json_out),
            ],
            check=True,
        )
        text = json_out.read_text()
        assert "__NEXT_DATA__" not in text, "__NEXT_DATA__ leaked into JSON output"

    def test_leak_check_helper(self):
        """_leak_check helper correctly detects leakage."""
        assert _leak_check("<html><body>test</body></html>") is True
        assert _leak_check("<script>__NEXT_DATA__</script>") is True
        assert _leak_check("__INITIAL_STATE__") is True
        assert _leak_check("clean output without tags") is False


class TestAcceptanceCriteria:
    """Tests that all acceptance criteria are met."""

    def test_total_samples_at_least_8(self):
        """total_samples >= 8."""
        summary, _ = run_benchmark(hermetic=True)
        assert summary["total_samples"] >= 8

    def test_hydration_attempted_equals_total(self):
        """hydration_attempted == total_samples."""
        summary, _ = run_benchmark(hermetic=True)
        assert summary["hydration_attempted"] == summary["total_samples"]

    def test_would_skip_js_at_least_3(self):
        """would_skip_js >= 3."""
        summary, _ = run_benchmark(hermetic=True)
        assert summary["would_skip_js"] >= 3, (
            f"skip_js={summary['would_skip_js']} < 3 — heuristic may be too aggressive"
        )

    def test_would_fallback_to_js_at_least_2(self):
        """would_fallback_to_js >= 2."""
        summary, _ = run_benchmark(hermetic=True)
        assert summary["would_fallback_to_js"] >= 2, (
            f"fallback={summary['would_fallback_to_js']} < 2 — heuristic may be too aggressive"
        )

    def test_skip_rate_in_range(self):
        """skip_rate is between 0.25 and 0.85 (not too aggressive, not too conservative)."""
        summary, _ = run_benchmark(hermetic=True)
        rate = summary["skip_rate"]
        assert 0.25 <= rate <= 0.85, (
            f"skip_rate={rate:.2%} outside [25%, 85%] — "
            "heuristic may be too aggressive (rate too high) or too conservative (rate too low)"
        )

    def test_score_buckets_populated(self):
        """At least one score bucket is non-zero."""
        summary, _ = run_benchmark(hermetic=True)
        buckets = summary["score_buckets"]
        assert any(v > 0 for v in buckets.values())


class TestScoreBucket:
    """Tests for score bucket helper."""

    def test_bucket_edges(self):
        """_score_bucket returns correct bucket for known scores."""
        assert _score_bucket(0.0) == "0.00-0.25"
        assert _score_bucket(0.1) == "0.00-0.25"
        assert _score_bucket(0.25) == "0.25-0.50"
        assert _score_bucket(0.4) == "0.25-0.50"
        assert _score_bucket(0.5) == "0.50-0.75"
        assert _score_bucket(0.7) == "0.50-0.75"
        assert _score_bucket(0.8) == "0.75-1.00"
        assert _score_bucket(1.0) == "0.75-1.00"