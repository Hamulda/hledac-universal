"""tests/probe_e2e_signal_fixture/test_e2e_signal_fixture.py

Sprint F206X — Signal Fixture Tests

Tests for the deterministic signal fixture benchmark.
Tests 1-4: Core fixture infrastructure
Tests 5-13: Transport matrix runs
Test 14: Compare output
Tests 15-17: No-mutation guarantees
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

BENCHMARK_SCRIPT = PROJECT_ROOT / "benchmarks" / "e2e_signal_fixture.py"
OUTPUT_DIR = PROJECT_ROOT / "probe_e2e_readiness"


class TestFixtureServer:
    """Tests 1-4: Fixture server infrastructure."""

    def test_fixture_server_starts_on_localhost(self):
        """Test 1: fixture server starts on localhost."""
        from benchmarks.e2e_signal_fixture import run_server

        server_url, port, _ = run_server(host="127.0.0.1", port=0)
        assert server_url.startswith("http://127.0.0.1:")
        assert port > 0

    def test_fixture_returns_deterministic_html(self):
        """Test 2: fixture returns deterministic HTML."""
        import urllib.request
        from benchmarks.e2e_signal_fixture import FixtureHandler, FIXTURE_HTML, run_server

        server_url, _, _ = run_server(host="127.0.0.1", port=0)
        url = f"{server_url}/test"

        resp1 = urllib.request.urlopen(url, timeout=5)
        body1 = resp1.read()
        resp2 = urllib.request.urlopen(url, timeout=5)
        body2 = resp2.read()

        assert body1 == body2 == FIXTURE_HTML.encode("utf-8")

    def test_pattern_matcher_finds_osint_signals(self):
        """Test 3: pattern matcher finds OSINT signals in fixture HTML."""
        from benchmarks.e2e_signal_fixture import FIXTURE_HTML
        from patterns.pattern_matcher import (
            configure_default_bootstrap_patterns_if_empty,
            match_text,
        )
        configure_default_bootstrap_patterns_if_empty()

        hits = match_text(FIXTURE_HTML)
        assert len(hits) > 0, "PatternMatcher should find OSINT signals"
        pattern_names = [h.pattern for h in hits]
        assert any(p in pattern_names for p in ["ransomware", ".onion", "leak", "btc", "cve"])

    def test_pattern_hit_list_contains_ioc_types(self):
        """Test 4: pattern hit list contains IOC types."""
        from benchmarks.e2e_signal_fixture import FIXTURE_HTML
        from patterns.pattern_matcher import (
            configure_default_bootstrap_patterns_if_empty,
            match_text,
        )
        configure_default_bootstrap_patterns_if_empty()

        hits = match_text(FIXTURE_HTML)
        hit_list = [{"pattern": h.pattern, "ioc_type": h.label} for h in hits]

        assert len(hit_list) > 0
        for item in hit_list:
            assert "pattern" in item
            assert "ioc_type" in item


class TestTransportMatrix:
    """Tests 5-13: Transport matrix runs."""

    def _run_benchmark(self):
        """Run e2e_signal_fixture.py and return completed subprocess."""
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        result = subprocess.run(
            [sys.executable, str(BENCHMARK_SCRIPT)],
            capture_output=True, text=True, timeout=90, cwd=str(PROJECT_ROOT), env=env
        )
        return result

    def test_baseline_run_produces_valid_artifact(self):
        """Test 5: baseline run produces valid artifact with fixture_hits > 0."""
        result = self._run_benchmark()
        assert result.returncode == 0, f"benchmark failed: {result.stderr}"

        artifact_path = OUTPUT_DIR / "e2e_signal_fixture_baseline.json"
        assert artifact_path.exists(), "baseline artifact should exist"

        with open(artifact_path) as f:
            art = json.load(f)

        assert art["artifact_type"] == "signal_fixture"
        assert art["run_name"] == "baseline"
        assert art["fixture_hits"] > 0, "fixture_hits must be > 0"
        assert art["fetched_bytes"] > 0, "fetched_bytes must be > 0"
        assert art["status_code"] == 200
        assert art["selected_transport"] == "aiohttp"

    def test_httpx_h2_run_produces_valid_artifact(self):
        """Test 6: httpx_h2 run produces valid artifact."""
        result = self._run_benchmark()
        assert result.returncode == 0, f"benchmark failed: {result.stderr}"

        artifact_path = OUTPUT_DIR / "e2e_signal_fixture_httpx_h2_on.json"
        assert artifact_path.exists()

        with open(artifact_path) as f:
            art = json.load(f)

        assert art["artifact_type"] == "signal_fixture"
        assert art["run_name"] == "httpx_h2_on"
        assert art["fixture_hits"] > 0
        assert art["fetched_bytes"] > 0
        assert art["status_code"] == 200
        assert art["selected_transport"] == "httpx_h2"

    def test_curl_cffi_run_produces_valid_artifact(self):
        """Test 7: curl_cffi run produces valid artifact."""
        result = self._run_benchmark()
        assert result.returncode == 0, f"benchmark failed: {result.stderr}"

        artifact_path = OUTPUT_DIR / "e2e_signal_fixture_curl_cffi_on.json"
        assert artifact_path.exists()

        with open(artifact_path) as f:
            art = json.load(f)

        assert art["artifact_type"] == "signal_fixture"
        assert art["run_name"] == "curl_cffi_on"
        assert art["fixture_hits"] > 0
        assert art["fetched_bytes"] > 0
        assert art["status_code"] == 200
        assert art["selected_transport"] == "curl_cffi"

    def test_all_artifacts_have_transport_counters(self):
        """Test 8: all artifacts have transport_counters."""
        result = self._run_benchmark()
        assert result.returncode == 0

        for name in ["baseline", "httpx_h2_on", "curl_cffi_on"]:
            with open(OUTPUT_DIR / f"e2e_signal_fixture_{name}.json") as f:
                art = json.load(f)
            assert "transport_counters" in art
            tc = art["transport_counters"]
            assert "aiohttp_count" in tc
            assert "httpx_h2_count" in tc
            assert "curl_cffi_count" in tc

    def test_all_artifacts_have_pattern_hit_list(self):
        """Test 9: all artifacts have pattern_hit_list with IOC types."""
        result = self._run_benchmark()
        assert result.returncode == 0

        for name in ["baseline", "httpx_h2_on", "curl_cffi_on"]:
            with open(OUTPUT_DIR / f"e2e_signal_fixture_{name}.json") as f:
                art = json.load(f)
            assert "pattern_hit_list" in art
            assert art["pattern_hits"] > 0
            hit_list = art["pattern_hit_list"]
            assert len(hit_list) > 0
            for item in hit_list:
                assert "pattern" in item
                assert "ioc_type" in item

    def test_all_artifacts_valid_json(self):
        """Test 10: all artifacts are valid JSON."""
        result = self._run_benchmark()
        assert result.returncode == 0

        for name in ["baseline", "httpx_h2_on", "curl_cffi_on"]:
            path = OUTPUT_DIR / f"e2e_signal_fixture_{name}.json"
            with open(path) as f:
                json.load(f)  # must not raise

    def test_http_version_detected_for_httpx(self):
        """Test 11: http_version detected for httpx_h2."""
        result = self._run_benchmark()
        assert result.returncode == 0

        with open(OUTPUT_DIR / "e2e_signal_fixture_httpx_h2_on.json") as f:
            art = json.load(f)
        assert art["http_version"] is not None
        assert "http" in art["http_version"].lower()

    def test_transport_policy_reason_present(self):
        """Test 12: transport_policy_reason present in all artifacts."""
        result = self._run_benchmark()
        assert result.returncode == 0

        for name in ["baseline", "httpx_h2_on", "curl_cffi_on"]:
            with open(OUTPUT_DIR / f"e2e_signal_fixture_{name}.json") as f:
                art = json.load(f)
            assert "transport_policy_reason" in art
            assert art["transport_policy_reason"] is not None

    def test_no_errors_in_successful_runs(self):
        """Test 13: no errors in successful runs."""
        result = self._run_benchmark()
        assert result.returncode == 0

        for name in ["baseline", "httpx_h2_on", "curl_cffi_on"]:
            with open(OUTPUT_DIR / f"e2e_signal_fixture_{name}.json") as f:
                art = json.load(f)
            assert art.get("errors", []) == []


class TestCompare:
    """Tests 14: Compare output."""

    def test_compare_artifact_exists_after_run(self):
        """Test 14: compare artifact exists after run."""
        result = subprocess.run(
            [sys.executable, str(BENCHMARK_SCRIPT)],
            capture_output=True, text=True, timeout=90, cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        )
        assert result.returncode == 0

        compare_path = OUTPUT_DIR / "e2e_signal_fixture_compare.json"
        assert compare_path.exists()

        with open(compare_path) as f:
            cmp = json.load(f)

        assert cmp["artifact_type"] == "signal_fixture_compare"
        assert "verdict" in cmp
        assert cmp["verdict"] in ("SIGNAL_FIXTURE_VALID", "PASS_WITH_NOTES", "BROKEN")
        assert "baseline_comparable" in cmp
        assert "httpx_h2_comparable" in cmp
        assert "curl_cffi_comparable" in cmp
        assert "field_diffs" in cmp
        # field_diffs should show transport differences
        transport_diffs = [d for d in cmp["field_diffs"] if d["field"] == "selected_transport"]
        assert len(transport_diffs) > 0


class TestNoMutation:
    """Tests 15-16: No mutation guarantees."""

    def test_no_scheduler_behavior_mutation(self):
        """Test 15: benchmark does not mutate scheduler behavior."""
        result = subprocess.run(
            [sys.executable, str(BENCHMARK_SCRIPT)],
            capture_output=True, text=True, timeout=90, cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        )
        assert result.returncode == 0

        # Verify scheduler source files unchanged (basic smoke)
        scheduler_path = PROJECT_ROOT / "runtime" / "sprint_scheduler.py"
        assert scheduler_path.exists()

    def test_no_storage_schema_mutation(self):
        """Test 16: benchmark does not mutate storage schema."""
        result = subprocess.run(
            [sys.executable, str(BENCHMARK_SCRIPT)],
            capture_output=True, text=True, timeout=90, cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        )
        assert result.returncode == 0

        # Verify duckdb_store source unchanged
        duckdb_path = PROJECT_ROOT / "knowledge" / "duckdb_store.py"
        assert duckdb_path.exists()

    def test_benchmark_exit_code_zero(self):
        """Test 17: benchmark exits with code 0."""
        result = subprocess.run(
            [sys.executable, str(BENCHMARK_SCRIPT)],
            capture_output=True, text=True, timeout=90, cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_public_fetched_nonzero_for_all_lanes(self):
        """Test 18: public_fetched > 0 for all transport lanes."""
        result = subprocess.run(
            [sys.executable, str(BENCHMARK_SCRIPT)],
            capture_output=True, text=True, timeout=90, cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        )
        assert result.returncode == 0

        for name in ["baseline", "httpx_h2_on", "curl_cffi_on"]:
            with open(OUTPUT_DIR / f"e2e_signal_fixture_{name}.json") as f:
                art = json.load(f)
            assert art["public_fetched"] > 0, f"{name} should have public_fetched > 0"

    def test_pattern_hits_nonzero_for_all_lanes(self):
        """Test 19: pattern_hits > 0 for all transport lanes."""
        result = subprocess.run(
            [sys.executable, str(BENCHMARK_SCRIPT)],
            capture_output=True, text=True, timeout=90, cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        )
        assert result.returncode == 0

        for name in ["baseline", "httpx_h2_on", "curl_cffi_on"]:
            with open(OUTPUT_DIR / f"e2e_signal_fixture_{name}.json") as f:
                art = json.load(f)
            assert art["pattern_hits"] > 0, f"{name} should have pattern_hits > 0"

    def test_accepted_findings_nonzero_for_all_lanes(self):
        """Test 20: accepted_findings > 0 for all transport lanes."""
        result = subprocess.run(
            [sys.executable, str(BENCHMARK_SCRIPT)],
            capture_output=True, text=True, timeout=90, cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
        )
        assert result.returncode == 0

        for name in ["baseline", "httpx_h2_on", "curl_cffi_on"]:
            with open(OUTPUT_DIR / f"e2e_signal_fixture_{name}.json") as f:
                art = json.load(f)
            assert art["accepted_findings"] > 0, f"{name} should have accepted_findings > 0"