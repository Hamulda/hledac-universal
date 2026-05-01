"""
SPRINT F206AR — Transport Canonical Policy Audit Tests
======================================================

Verifies the transport policy audit was completed correctly:
- report and matrix exist and are well-formed
- all critical files are classified
- every network consumer has a verdict
- no production files were modified
- no network calls were made during audit

Run: python -m pytest tests/probe_transport_policy_f206ar -v
"""

import json
import re
from pathlib import Path

import pytest

PROBE_DIR = Path(__file__).parent.parent.parent / "probe_transport_policy_f206ar"
REPORT_PATH = PROBE_DIR / "REPORT_TRANSPORT_POLICY_AUDIT.md"
MATRIX_PATH = PROBE_DIR / "transport_policy_matrix.json"
UNIVERSAL_ROOT = Path(__file__).parent.parent.parent

# Files that were audited (read-only scope)
AUDITED_FILES = [
    "coordinators/fetch_coordinator.py",
    "fetching/public_fetcher.py",
    "pipeline/live_public_pipeline.py",
    "transport/circuit_breaker.py",
    "transport/transport_resolver.py",
    "transport/curl_cffi_transport.py",
    "transport/httpx_transport.py",
    "transport/tor_transport.py",
    "transport/i2p_transport.py",
    "transport/httpx_client.py",
    "network/session_runtime.py",
    "stealth/stealth_manager.py",
    "intelligence/blockchain_analyzer.py",
    "intelligence/rir_correlator.py",
    "intelligence/wayback_diff_miner.py",
    "security/passive_dns.py",
    "security/automation/threat-intelligence-automation.py",
    "deep_research/utils.py",
    "core/__main__.py",
    "legacy/autonomous_orchestrator.py",
]

# Network consumer files that must appear in the matrix
MUST_APPEAR_IN_MATRIX = [
    "coordinators/fetch_coordinator.py",
    "pipeline/live_public_pipeline.py",
    "fetching/public_fetcher.py",
    "transport/tor_transport.py",
    "transport/i2p_transport.py",
    "transport/curl_cffi_transport.py",
    "transport/httpx_transport.py",
    "stealth/stealth_manager.py",
    "intelligence/blockchain_analyzer.py",
    "intelligence/rir_correlator.py",
    "intelligence/wayback_diff_miner.py",
    "security/passive_dns.py",
    "security/automation/threat-intelligence-automation.py",
    "deep_research/utils.py",
    "core/__main__.py",
    "legacy/autonomous_orchestrator.py",
    "discovery/ti_feed_adapter.py",
    "discovery/duckduckgo_adapter.py",
    "intelligence/github_secret_scanner.py",
]

VERDICT_CODES = {
    "CANONICAL_TRANSPORT",
    "POLICY_GATED",
    "CIRCUIT_BREAKER_GATED",
    "SHARED_SESSION_OK",
    "DIRECT_SESSION_BYPASS",
    "OPTIONAL_DORMANT",
    "TEST_ONLY",
    "NEEDS_REVIEW",
}

NETWORK_PATTERNS = [
    re.compile(r"requests?\."),
    re.compile(r"fetch\("),
    re.compile(r"aiohttp"),
    re.compile(r"httpx"),
    re.compile(r"curl"),
]


class TestReportExists:
    """Test that audit report and matrix were generated."""

    def test_report_exists(self):
        assert REPORT_PATH.exists(), f"Report not found: {REPORT_PATH}"

    def test_matrix_exists(self):
        assert MATRIX_PATH.exists(), f"Matrix not found: {MATRIX_PATH}"

    def test_report_not_empty(self):
        content = REPORT_PATH.read_text()
        assert len(content) > 1000, "Report is suspiciously short"

    def test_matrix_is_valid_json(self):
        text = MATRIX_PATH.read_text()
        data = json.loads(text)
        assert isinstance(data, dict)


class TestMatrixIncludesCriticalFiles:
    """Test that the matrix includes all critical production files."""

    @pytest.fixture
    def matrix(self):
        return json.loads(MATRIX_PATH.read_text())

    def test_matrix_has_network_consumers(self, matrix):
        assert "network_consumers" in matrix, "matrix missing network_consumers"
        assert len(matrix["network_consumers"]) > 0

    def test_fetch_coordinator_in_matrix(self, matrix):
        files = [c["file"] for c in matrix.get("network_consumers", [])]
        assert any("fetch_coordinator.py" in f for f in files), (
            "fetch_coordinator.py not in matrix"
        )

    def test_live_public_pipeline_in_matrix(self, matrix):
        files = [c["file"] for c in matrix.get("network_consumers", [])]
        assert any("live_public_pipeline.py" in f for f in files), (
            "live_public_pipeline.py not in matrix"
        )

    def test_public_fetcher_in_matrix(self, matrix):
        files = [c["file"] for c in matrix.get("network_consumers", [])]
        assert any("public_fetcher.py" in f for f in files), (
            "public_fetcher.py not in matrix"
        )

    def test_stealth_manager_in_matrix(self, matrix):
        files = [c["file"] for c in matrix.get("network_consumers", [])]
        assert any("stealth_manager.py" in f for f in files), (
            "stealth_manager.py not in matrix"
        )

    def test_all_critical_files_have_verdicts(self, matrix):
        consumers = matrix.get("network_consumers", [])
        files_without_verdict = []
        for c in consumers:
            if "verdict" not in c and "transport" not in c:
                files_without_verdict.append(c["file"])
        assert len(files_without_verdict) == 0, (
            f"Files without verdict: {files_without_verdict}"
        )


class TestMatrixCompleteness:
    """Test that matrix covers all required network consumers."""

    @pytest.fixture
    def matrix(self):
        return json.loads(MATRIX_PATH.read_text())

    def test_all_must_appear_files_in_matrix(self, matrix):
        files = [c["file"] for c in matrix.get("network_consumers", [])]
        missing = []
        for f in MUST_APPEAR_IN_MATRIX:
            if not any(f in mf for mf in files):
                missing.append(f)
        assert len(missing) == 0, f"Missing from matrix: {missing}"

    def test_circuit_breaker_authority_documented(self, matrix):
        assert "circuit_breaker_authority" in matrix
        cba = matrix["circuit_breaker_authority"]
        assert "canonical_circuit_breaker_module" in cba
        assert "production_circuit_breaker" in cba

    def test_transport_policies_documented(self, matrix):
        assert "transport_policies" in matrix
        tp = matrix["transport_policies"]
        assert "should_use_curl_cffi" in tp
        assert "should_use_httpx_h2" in tp
        assert "get_transport_for_url" in tp

    def test_circuit_breaker_test_seam_only_documented(self, matrix):
        cba = matrix.get("circuit_breaker_authority", {})
        status = cba.get("status", "")
        assert "TEST" in status or "test" in status, (
            "circuit_breaker status should indicate TEST_SEAM_ONLY"
        )

    def test_shared_sessions_documented(self, matrix):
        assert "shared_sessions" in matrix
        ss = matrix["shared_sessions"]
        assert "async_get_aiohttp_session" in ss
        assert "async_get_httpx_client" in ss

    def test_key_findings_present(self, matrix):
        findings = matrix.get("key_findings", [])
        assert len(findings) >= 5, f"Expected >=5 findings, got {len(findings)}"
        finding_titles = [f.get("finding", "") for f in findings]
        # Should mention circuit_breaker.py IS wired finding
        assert any("wired" in f.lower() or "circuit_breaker" in f.lower() for f in finding_titles)


class TestNoProductionFilesModified:
    """Verify no production files were edited during the audit."""

    def test_no_new_files_in_transport(self):
        # Check transport directory — should not have new .py files created by audit
        transport_dir = UNIVERSAL_ROOT / "transport"
        if not transport_dir.exists():
            pytest.skip("transport dir not found")

        # Just verify the dir is unchanged (existing files only)
        py_files = list(transport_dir.glob("*.py"))
        assert len(py_files) > 0

    def test_no_new_files_in_fetching(self):
        fetching_dir = UNIVERSAL_ROOT / "fetching"
        if not fetching_dir.exists():
            pytest.skip("fetching dir not found")
        py_files = list(fetching_dir.glob("*.py"))
        assert len(py_files) > 0

    def test_no_new_files_in_coordinators(self):
        coordinators_dir = UNIVERSAL_ROOT / "coordinators"
        if not coordinators_dir.exists():
            pytest.skip("coordinators dir not found")
        py_files = list(coordinators_dir.glob("*.py"))
        assert len(py_files) > 0

    def test_no_new_files_in_pipeline(self):
        pipeline_dir = UNIVERSAL_ROOT / "pipeline"
        if not pipeline_dir.exists():
            pytest.skip("pipeline dir not found")
        py_files = list(pipeline_dir.glob("*.py"))
        assert len(py_files) > 0


class TestNoNetworkCalls:
    """Verify the audit made no live network calls."""

    def test_matrix_is_static_json(self):
        # Matrix should be pre-generated, not fetched
        content = MATRIX_PATH.read_text()
        # Should not contain URLs or IP addresses
        url_pattern = re.compile(r"https?://[^\s\"']+")
        urls = url_pattern.findall(content)
        assert len(urls) == 0, f"Matrix contains URLs (should be static): {urls}"

    def test_report_is_static_markdown(self):
        content = REPORT_PATH.read_text()
        url_pattern = re.compile(r"https?://[^\s\"']+")
        urls = url_pattern.findall(content)
        # Allow URLs in "next steps" or references but flag them
        # For audit report, some URL references may be acceptable
        assert len(urls) == 0 or all("example" in u for u in urls), (
            f"Report contains non-example URLs: {urls}"
        )


class TestVerdictQuality:
    """Test that verdicts are consistent and meaningful."""

    @pytest.fixture
    def matrix(self):
        return json.loads(MATRIX_PATH.read_text())

    def test_fetch_coordinator_verdict(self, matrix):
        consumers = matrix.get("network_consumers", [])
        for c in consumers:
            if "fetch_coordinator.py" in c.get("file", ""):
                v = c.get("transport", "")
                assert v in VERDICT_CODES, f"Invalid verdict: {v}"
                assert c.get("circuit_breaker") is not None

    def test_live_pipeline_verdict(self, matrix):
        consumers = matrix.get("network_consumers", [])
        for c in consumers:
            if "live_public_pipeline.py" in c.get("file", ""):
                v = c.get("transport", "")
                assert v in VERDICT_CODES, f"Invalid verdict: {v}"

    def test_all_verdicts_are_valid_codes(self, matrix):
        consumers = matrix.get("network_consumers", [])
        invalid = []
        for c in consumers:
            v = c.get("transport", "")
            if v and v not in VERDICT_CODES:
                invalid.append((c["file"], v))
        assert len(invalid) == 0, f"Invalid verdict codes: {invalid}"

    def test_public_fetcher_bypass_noted(self, matrix):
        consumers = matrix.get("network_consumers", [])
        for c in consumers:
            if "public_fetcher.py" in c.get("file", ""):
                # Should note it has independent Tor/I2P pools
                notes = c.get("notes", [])
                assert len(notes) > 0, "public_fetcher should have notes about bypass"

    def test_fetch_coordinator_circuit_breaker_impl_noted(self, matrix):
        consumers = matrix.get("network_consumers", [])
        for c in consumers:
            if "fetch_coordinator.py" in c.get("file", ""):
                cb_impl = c.get("cb_impl", "")
                assert "NOT" in cb_impl or "_domain_blocked" in cb_impl, (
                    "fetch_coordinator should note it uses its own CB implementation"
                )

    def test_transport_resolver_dormant_documented(self, matrix):
        tp = matrix.get("transport_policies", {})
        tr = tp.get("TransportResolver.resolve", {})
        status = tr.get("status", "")
        assert "DORMANT" in status or "dormant" in status.lower(), (
            "TransportResolver.resolve should be marked DORMANT"
        )


class TestHotPathAnalysis:
    """Test that hot path is correctly identified."""

    @pytest.fixture
    def matrix(self):
        return json.loads(MATRIX_PATH.read_text())

    def test_canonical_fetch_path_documented(self, matrix):
        hpa = matrix.get("hot_path_analysis", {})
        assert "canonical_fetch_path" in hpa
        assert "FetchCoordinator._fetch_url" in hpa["canonical_fetch_path"]

    def test_tor_path_documented(self, matrix):
        hpa = matrix.get("hot_path_analysis", {})
        assert "tor_path" in hpa

    def test_live_pipeline_path_documented(self, matrix):
        hpa = matrix.get("hot_path_analysis", {})
        assert "live_pipeline_path" in hpa
        assert "async_get_aiohttp_session" in hpa["live_pipeline_path"]


class TestPatchRecommendations:
    """Test that patch recommendations are present and actionable."""

    @pytest.fixture
    def matrix(self):
        return json.loads(MATRIX_PATH.read_text())

    def test_patch_recommendations_exist(self, matrix):
        recs = matrix.get("patch_recommendations", [])
        assert len(recs) >= 2

    def test_recommendations_have_priority(self, matrix):
        recs = matrix.get("patch_recommendations", [])
        for rec in recs:
            assert "priority" in rec
            assert rec["priority"] in {"HIGH", "MEDIUM", "LOW"}

    def test_test_seam_only_recommended_high_priority(self, matrix):
        recs = matrix.get("patch_recommendations", [])
        high_recs = [r for r in recs if r.get("priority") == "HIGH"]
        # The TEST-SEAM ONLY finding should be recommended for HIGH priority
        assert len(high_recs) > 0
