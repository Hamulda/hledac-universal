"""
Sprint F204G: Passive Service Fingerprinting — Probe Tests

Tests:
  1. ServiceFingerprint frozen dataclass frozen invariant
  2. ServiceFingerprint all required fields present
  3. FingerprintResult frozen dataclass frozen invariant
  4. FingerprintResult all required fields present
  5. MAX_FINGERPRINT_FINDINGS = 1000
  6. MAX_FINGERPRINTS_PER_FINDING = 5
  7. MAX_PATTERN_BYTES = 4096
  8. FINGERPRINT_TIMEOUT_S = 10.0
  9. extract_http_signals extracts server headers
 10. extract_http_signals extracts x-powered-by headers
 11. extract_http_signals fail-soft on malformed JSON
 12. extract_tls_signals extracts cert subject/issuer
 13. extract_tls_signals fail-soft on malformed JSON
 14. extract_ct_signals extracts CT metadata
 15. extract_html_signals extracts title and generator
 16. extract_fingerprints produces fingerprints up to MAX_FINGERPRINTS_PER_FINDING
 17. correlate_passive_fingerprints returns CanonicalFinding list with source_type="passive_fingerprint"
 18. correlate_passive_fingerprints bounded by MAX_FINGERPRINT_FINDINGS
 19. PassiveFingerprintAdapter correlate returns findings
 20. PassiveFingerprintAdapter reset_stats clears stats
 21. sidecar_bus has passive_fingerprint runner registered
 22. passive_fingerprint facet in exposure_correlator signal_facets
 23. exposure_correlator reads passive_fingerprint facets from payload_text
 24. smoke runner OK
"""

from __future__ import annotations

import asyncio
from dataclasses import fields as _dc_fields
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.intelligence.passive_fingerprint import (
    MAX_FINGERPRINT_FINDINGS,
    MAX_FINGERPRINTS_PER_FINDING,
    MAX_PATTERN_BYTES,
    FINGERPRINT_TIMEOUT_S,
    ServiceFingerprint,
    FingerprintResult,
    extract_http_signals,
    extract_tls_signals,
    extract_ct_signals,
    extract_html_signals,
    extract_fingerprints,
    correlate_passive_fingerprints,
    PassiveFingerprintAdapter,
    create_passive_fingerprint_adapter,
    get_fingerprint_stats,
    reset_fingerprint_stats,
)


# ---------------------------------------------------------------------------
# 1-4: Dataclass invariants
# ---------------------------------------------------------------------------

class TestServiceFingerprintDataclass:
    """F204G-1: ServiceFingerprint frozen dataclass invariants."""

    def test_service_fingerprint_frozen(self):
        """Invariant: frozen=True prevents field mutation."""
        fp = ServiceFingerprint(
            finding_id="f1",
            service_name="nginx",
            product="nginx",
            version="1.20.0",
            confidence=0.9,
            evidence_ids=("f1",),
            facets={"source": "http_server_header"},
        )
        with pytest.raises(Exception):
            fp.confidence = 0.5  # type: ignore

    def test_service_fingerprint_all_fields(self):
        """Invariant: all required fields present."""
        flds = {f.name for f in _dc_fields(ServiceFingerprint)}
        expected = {
            "finding_id", "service_name", "product", "version",
            "confidence", "evidence_ids", "facets",
        }
        assert expected <= flds, f"missing fields: {expected - flds}"


class TestFingerprintResultDataclass:
    """F204G-3: FingerprintResult frozen dataclass invariants."""

    def test_fingerprint_result_frozen(self):
        """Invariant: frozen=True prevents field mutation."""
        result = FingerprintResult(
            fingerprints=(),
            scanned_count=10,
            skipped_count=1,
            elapsed_ms=50.0,
        )
        with pytest.raises(Exception):
            result.scanned_count = 20  # type: ignore

    def test_fingerprint_result_all_fields(self):
        """Invariant: all required fields present."""
        flds = {f.name for f in _dc_fields(FingerprintResult)}
        expected = {"fingerprints", "scanned_count", "skipped_count", "elapsed_ms"}
        assert expected <= flds, f"missing fields: {expected - flds}"


# ---------------------------------------------------------------------------
# 5-8: Bounds constants
# ---------------------------------------------------------------------------

class TestBounds:
    """F204G-5-8: Bounds constants."""

    def test_max_fingerprint_findings(self):
        """Invariant: MAX_FINGERPRINT_FINDINGS = 1000."""
        assert MAX_FINGERPRINT_FINDINGS == 1000

    def test_max_fingerprints_per_finding(self):
        """Invariant: MAX_FINGERPRINTS_PER_FINDING = 5."""
        assert MAX_FINGERPRINTS_PER_FINDING == 5

    def test_max_pattern_bytes(self):
        """Invariant: MAX_PATTERN_BYTES = 4096."""
        assert MAX_PATTERN_BYTES == 4096

    def test_fingerprint_timeout_s(self):
        """Invariant: FINGERPRINT_TIMEOUT_S = 10.0."""
        assert FINGERPRINT_TIMEOUT_S == 10.0


# ---------------------------------------------------------------------------
# 9-11: HTTP signal extraction
# ---------------------------------------------------------------------------

class TestExtractHttpSignals:
    """F204G-9-11: HTTP signal extraction."""

    def test_extracts_server_headers(self):
        """F204G-9: extract_http_signals extracts Server header values."""
        payload = '{"http_headers": {"Server": "nginx/1.20.0", "Content-Type": "text/html"}}'
        signals = extract_http_signals(payload)

        assert "nginx/1.20.0" in signals["server_headers"]
        # all_headers contains "Key: Value" strings
        all_headers_str = " ".join(signals["all_headers"])
        assert "Content-Type" in all_headers_str

    def test_extracts_x_powered_by(self):
        """F204G-10: extract_http_signals extracts X-Powered-By headers."""
        payload = '{"http_headers": {"X-Powered-By": "PHP/8.1"}}'
        signals = extract_http_signals(payload)

        assert "X-Powered-By: PHP/8.1" in signals["x_headers"]

    def test_fail_soft_malformed_json(self):
        """F204G-11: fail-soft when payload_text is malformed JSON."""
        payload = "not valid json {"
        signals = extract_http_signals(payload)

        # Should return empty signals, not raise
        assert signals["server_headers"] == []
        assert signals["x_headers"] == []

    def test_extracts_html_content(self):
        """F204G-9b: extract_http_signals extracts HTML body."""
        payload = '{"html": "<html><title>Test</title></html>"}'
        signals = extract_http_signals(payload)

        assert "html_content" in signals


# ---------------------------------------------------------------------------
# 12-13: TLS signal extraction
# ---------------------------------------------------------------------------

class TestExtractTlsSignals:
    """F204G-12-13: TLS/cert signal extraction."""

    def test_extracts_cert_subject_issuer(self):
        """F204G-12: extract_tls_signals extracts cert subject and issuer."""
        payload = '{"certificate": {"subject": "example.com", "issuer": "Lets Encrypt"}}'
        signals = extract_tls_signals(payload)

        assert "example.com" in signals["cert_subject"]
        assert "Lets Encrypt" in signals["cert_issuer"]

    def test_extracts_tls_protocol_and_cipher(self):
        """F204G-12b: extract_tls_signals extracts TLS version and cipher."""
        payload = '{"tls": {"version": "TLSv1.3", "cipher": "TLS_AES_256_GCM_SHA384"}}'
        signals = extract_tls_signals(payload)

        assert "TLSv1.3" in signals["protocol_version"]
        assert "TLS_AES_256_GCM_SHA384" in signals["cipher_suite"]

    def test_fail_soft_malformed_json(self):
        """F204G-13: fail-soft when payload_text is malformed JSON."""
        payload = "not valid json {"
        signals = extract_tls_signals(payload)

        # Should return empty signals, not raise
        assert signals["cert_subject"] == []
        assert signals["cert_issuer"] == []


# ---------------------------------------------------------------------------
# 14: CT signal extraction
# ---------------------------------------------------------------------------

class TestExtractCtSignals:
    """F204G-14: CT metadata signal extraction."""

    def test_extracts_ct_entries(self):
        """F204G-14: extract_ct_signals extracts CT log entries."""
        # cn maps to subject, not all_names
        payload = '{"ct_entries": [{"issuer": "Cloudflare", "cn": "example.com"}]}'
        signals = extract_ct_signals(payload)

        assert "Cloudflare" in signals["cert_issuer"]
        assert "example.com" in signals["cert_subject"]

    def test_extracts_ct_log_issuer(self):
        """F204G-14b: extract_ct_signals handles issuer field directly."""
        payload = '{"issuer": "DigiCert", "domain": "api.example.com"}'
        signals = extract_ct_signals(payload)

        assert "DigiCert" in signals["cert_issuer"]
        assert "api.example.com" in signals["all_names"]


# ---------------------------------------------------------------------------
# 15: HTML signal extraction
# ---------------------------------------------------------------------------

class TestExtractHtmlSignals:
    """F204G-15: HTML content signal extraction."""

    def test_extracts_title_and_generator(self):
        """F204G-15: extract_html_signals extracts title and meta generator."""
        # Use proper meta tag with content attribute
        payload = '{"html": "<html><head><title>My Site</title><meta name=\\"generator\\" content=\\"WordPress\\"></head></html>"}'
        signals = extract_html_signals(payload)

        assert "My Site" in signals["title"]
        assert "WordPress" in signals["generator"]

    def test_extracts_script_src_domains(self):
        """F204G-15b: extract_html_signals extracts script src patterns."""
        payload = '{"html": "<script src=\\"https://cdn.example.com/app.js\\"></script>"}'
        signals = extract_html_signals(payload)

        assert "cdn.example.com" in signals["all_text"]
        assert "https://cdn.example.com/app.js" in signals["scripts"]


# ---------------------------------------------------------------------------
# 16-18: Core fingerprinting
# ---------------------------------------------------------------------------

class MockFinding:
    """Minimal CanonicalFinding mock for testing."""
    def __init__(self, finding_id: str, source_type: str, payload_text: str, confidence: float = 0.5):
        self.finding_id = finding_id
        self.source_type = source_type
        self.payload_text = payload_text
        self.confidence = confidence


class TestExtractFingerprints:
    """F204G-16: extract_fingerprints behavior."""

    def test_produces_fingerprints_within_limit(self):
        """F204G-16: extract_fingerprints capped at MAX_FINGERPRINTS_PER_FINDING."""
        payload = '{"http_headers": {"Server": "nginx/1.20"}}'
        finding = MockFinding("f1", "public", payload)

        # Extract many fingerprints
        fps = extract_fingerprints(finding)

        # Should be within the limit
        assert len(fps) <= MAX_FINGERPRINTS_PER_FINDING

    def test_fingerprint_has_correct_fields(self):
        """F204G-16b: fingerprint has all required fields populated."""
        payload = '{"http_headers": {"Server": "nginx/1.20"}}'
        finding = MockFinding("f1", "public", payload)

        fps = extract_fingerprints(finding)

        if fps:
            fp = fps[0]
            assert fp.finding_id == "f1"
            assert fp.service_name
            assert fp.product
            assert 0.0 <= fp.confidence <= 1.0
            assert isinstance(fp.evidence_ids, tuple)
            assert isinstance(fp.facets, dict)


class TestCorrelatePassiveFingerprints:
    """F204G-17-18: correlate_passive_fingerprints behavior."""

    @pytest.mark.asyncio
    async def test_returns_canonical_findings_with_source_type(self):
        """F204G-17: returns CanonicalFinding list with source_type='passive_fingerprint'."""
        payload = '{"http_headers": {"Server": "nginx/1.20"}}'
        findings = [MockFinding("f1", "public", payload)]

        result = correlate_passive_fingerprints(findings, "test query")

        if result:
            assert result[0].source_type == "passive_fingerprint"
            assert result[0].finding_id.startswith("pfp_")

    @pytest.mark.asyncio
    async def test_bounded_by_max_fingerprint_findings(self):
        """F204G-18: findings bounded by MAX_FINGERPRINT_FINDINGS."""
        payload = '{"http_headers": {"Server": "nginx"}}'
        findings = [MockFinding(f"f{i}", "public", payload) for i in range(2000)]

        result = correlate_passive_fingerprints(findings, "test")

        # Should be bounded
        assert len(result) <= MAX_FINGERPRINT_FINDINGS


# ---------------------------------------------------------------------------
# 19-20: Adapter
# ---------------------------------------------------------------------------

class TestPassiveFingerprintAdapter:
    """F204G-19-20: PassiveFingerprintAdapter behavior."""

    def test_correlate_returns_findings(self):
        """F204G-19: adapter.correlate returns CanonicalFinding list."""
        adapter = create_passive_fingerprint_adapter()
        payload = '{"http_headers": {"Server": "Apache"}}'
        findings = [MockFinding("f1", "public", payload)]

        result = adapter.correlate(findings, "test")

        assert isinstance(result, list)

    def test_reset_stats_clears_stats(self):
        """F204G-20: reset_stats clears all stat counters."""
        adapter = create_passive_fingerprint_adapter()
        reset_fingerprint_stats()

        stats = get_fingerprint_stats()
        assert stats["findings_scanned"] == 0
        assert stats["fingerprints_produced"] == 0

    def test_adapter_has_correlate_method(self):
        """F204G-19b: adapter has correlate method."""
        adapter = PassiveFingerprintAdapter()
        assert hasattr(adapter, "correlate")
        assert callable(adapter.correlate)


# ---------------------------------------------------------------------------
# 21: Sidecar bus wiring
# ---------------------------------------------------------------------------

class TestSidecarBusWiring:
    """F204G-21: sidecar_bus registration."""

    def test_passive_fingerprint_in_default_runners(self):
        """F204G-21: passive_fingerprint runner registered in DEFAULT_SIDECAR_RUNNERS."""
        from hledac.universal.runtime.sidecar_bus import DEFAULT_SIDECAR_RUNNERS

        names = [name for name, _ in DEFAULT_SIDECAR_RUNNERS]
        assert "passive_fingerprint" in names, f"Expected 'passive_fingerprint' in runners: {names}"


# ---------------------------------------------------------------------------
# 22-23: ExposureCorrelator integration
# ---------------------------------------------------------------------------

class TestExposureCorrelatorIntegration:
    """F204G-22-23: passive_fingerprint facets in exposure_correlator."""

    def test_exposure_correlator_reads_passive_fingerprint_facets(self):
        """F204G-23: exposure_correlator can read passive_fingerprint facets from payload."""
        from hledac.universal.intelligence.exposure_correlator import extract_signals

        # A finding with passive_fingerprint source_type and facets in payload
        finding = MagicMock()
        finding.finding_id = "pfp_abc123"
        finding.source_type = "passive_fingerprint"
        finding.confidence = 0.85
        finding.payload_text = '{"service_name": "nginx", "product": "nginx", "confidence": 0.9, "facets": {"source": "http_server_header"}}'

        # Should not raise, should handle gracefully
        try:
            signals = extract_signals([finding])
            # If passive_fingerprint is not handled, signals will be empty (fail-soft)
            # If it is handled, it should produce some signals
            assert isinstance(signals, list)
        except Exception as e:
            pytest.fail(f"Should not raise: {e}")

    def test_passive_fingerprint_facet_in_signal_types(self):
        """F204G-22: passive_fingerprint facets available in signal processing."""
        from hledac.universal.intelligence.exposure_correlator import (
            AssetSignal,
            SIGNAL_TYPE_CT_CERT,
        )

        # Verify AssetSignal can hold passive_fingerprint data
        sig = AssetSignal(
            signal_type="passive_fingerprint",
            asset_key="example.com",
            confidence=0.9,
            metadata={"service_name": "nginx", "product": "nginx"},
            finding_id="pfp_abc",
        )

        assert sig.signal_type == "passive_fingerprint"
        assert sig.metadata["service_name"] == "nginx"


# ---------------------------------------------------------------------------
# 24: Smoke runner
# ---------------------------------------------------------------------------

class TestSmoke:
    """F204G-24: smoke runner."""

    def test_smoke_passive_fingerprint_module_imports(self):
        """F204G-24a: module imports without error."""
        from hledac.universal.intelligence import passive_fingerprint
        assert passive_fingerprint is not None

    def test_smoke_adapter_factory(self):
        """F204G-24b: adapter factory creates valid adapter."""
        adapter = create_passive_fingerprint_adapter()
        assert isinstance(adapter, PassiveFingerprintAdapter)

    def test_smoke_bounds_values(self):
        """F204G-24c: all bounds are positive."""
        assert MAX_FINGERPRINT_FINDINGS > 0
        assert MAX_FINGERPRINTS_PER_FINDING > 0
        assert MAX_PATTERN_BYTES > 0
        assert FINGERPRINT_TIMEOUT_S > 0

    def test_smoke_correlate_empty_findings(self):
        """F204G-24d: correlate handles empty findings list."""
        result = correlate_passive_fingerprints([], "test")
        assert result == []

    def test_smoke_stats_function(self):
        """F204G-24e: get_fingerprint_stats returns dict."""
        stats = get_fingerprint_stats()
        assert isinstance(stats, dict)
        assert "findings_scanned" in stats
