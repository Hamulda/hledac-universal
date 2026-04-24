"""
Sprint F202C: Asset Exposure Correlator Probe Tests

Verifies:
  - Signal extraction from ct_log, open_storage, jarm, passive_dns findings
  - Correlation: exposed_host, open_bucket, cert_domain_relation, infra_cluster
  - Evidence envelope format in payload_text
  - Bounded degradation at MAX_ASSETS, MAX_SIGNALS_PER_ASSET, MAX_FINDINGS
  - Stats tracking and reset
  - Fail-soft on malformed inputs

All tests use MagicMock — no real DB/LMDB dependencies.
"""

import json
from unittest.mock import MagicMock


# ============================================================================
# F202C-1: Signal Extraction
# ============================================================================

class TestSignalExtraction:
    """Invariant: extract_signals() produces AssetSignal from ct_log, open_storage, jarm, passive_dns."""

    def test_extract_ct_log_signal(self):
        """CT log finding produces ct_cert signal."""
        from hledac.universal.intelligence.exposure_correlator import (
            extract_signals,
            SIGNAL_TYPE_CT_CERT,
        )

        mock_finding = MagicMock()
        mock_finding.finding_id = "ct_abc123def456"
        mock_finding.source_type = "ct_log"
        mock_finding.confidence = 0.75
        mock_finding.payload_text = json.dumps({
            "issuer": "DigiCert",
            "cert_count": 5,
            "domain": "example.com",
        })

        signals = extract_signals([mock_finding])
        assert len(signals) == 1
        assert signals[0].signal_type == SIGNAL_TYPE_CT_CERT
        assert signals[0].asset_key == "abc123def456"
        assert signals[0].confidence == 0.75
        assert signals[0].metadata["issuer"] == "DigiCert"

    def test_extract_open_storage_signal(self):
        """Open storage finding produces open_bucket signal."""
        from hledac.universal.intelligence.exposure_correlator import (
            extract_signals,
            SIGNAL_TYPE_OPEN_BUCKET,
        )

        mock_finding = MagicMock()
        mock_finding.finding_id = "bucket_001"
        mock_finding.source_type = "open_storage"
        mock_finding.confidence = 0.9
        mock_finding.payload_text = json.dumps({
            "url": "https://mybucket.s3.amazonaws.com",
            "type": "s3",
            "status": 200,
        })

        signals = extract_signals([mock_finding])
        assert len(signals) == 1
        assert signals[0].signal_type == SIGNAL_TYPE_OPEN_BUCKET
        assert signals[0].asset_key == "mybucket.s3.amazonaws.com"
        assert signals[0].metadata["bucket_type"] == "s3"

    def test_extract_jarm_signal(self):
        """JARM finding produces jarm_fp signal."""
        from hledac.universal.intelligence.exposure_correlator import (
            extract_signals,
            SIGNAL_TYPE_JARM,
        )

        mock_finding = MagicMock()
        mock_finding.finding_id = "jarm_example.com"
        mock_finding.source_type = "jarm"
        mock_finding.confidence = 0.8
        mock_finding.payload_text = json.dumps({
            "jarm_hash": "2a" * 31,
        })

        signals = extract_signals([mock_finding])
        assert len(signals) == 1
        assert signals[0].signal_type == SIGNAL_TYPE_JARM
        assert signals[0].asset_key == "example.com"
        assert signals[0].metadata["jarm_hash"] == "2a" * 31

    def test_extract_passive_dns_signal(self):
        """Passive DNS finding produces passive_dns signal."""
        from hledac.universal.intelligence.exposure_correlator import (
            extract_signals,
            SIGNAL_TYPE_PASSIVE_DNS,
        )

        mock_finding = MagicMock()
        mock_finding.finding_id = "pdns_001"
        mock_finding.source_type = "passive_dns"
        mock_finding.confidence = 0.7
        mock_finding.payload_text = json.dumps({
            "domain": "evil.com",
            "ip": "1.2.3.4",
            "record_type": "A",
        })

        signals = extract_signals([mock_finding])
        assert len(signals) == 1
        assert signals[0].signal_type == SIGNAL_TYPE_PASSIVE_DNS
        assert signals[0].asset_key == "evil.com"
        assert signals[0].metadata["ip"] == "1.2.3.4"

    def test_extract_unknown_source_type_skipped(self):
        """Unknown source_type is skipped."""
        from hledac.universal.intelligence.exposure_correlator import extract_signals

        mock_finding = MagicMock()
        mock_finding.finding_id = "unknown_001"
        mock_finding.source_type = "some_unknown_source"
        mock_finding.confidence = 0.5
        mock_finding.payload_text = "{}"

        signals = extract_signals([mock_finding])
        assert len(signals) == 0

    def test_extract_empty_findings(self):
        """Empty findings list returns empty signals."""
        from hledac.universal.intelligence.exposure_correlator import extract_signals
        signals = extract_signals([])
        assert signals == []

    def test_extract_malformed_payload(self):
        """Malformed payload_text is handled gracefully — no crash."""
        from hledac.universal.intelligence.exposure_correlator import extract_signals

        mock_finding = MagicMock()
        mock_finding.finding_id = "ct_malformed"
        mock_finding.source_type = "ct_log"
        mock_finding.confidence = 0.5
        # Malformed JSON — json.loads raises, data = {}
        # ct_log still extracts signal (san from finding_id) but with empty metadata
        mock_finding.payload_text = "not valid json {"

        signals = extract_signals([mock_finding])
        # Malformed json is caught; ct_log uses finding_id as san fallback
        assert len(signals) == 1
        # Issuer/cert_count empty due to malformed JSON
        assert signals[0].metadata.get("issuer") == ""


# ============================================================================
# F202C-2: Correlation — open_bucket
# ============================================================================

class TestCorrelationOpenBucket:
    """Invariant: single open_bucket signal produces CORR_OPEN_BUCKET finding."""

    def test_open_bucket_correlation(self):
        """Asset with only bucket signal produces open_bucket finding."""
        from hledac.universal.intelligence.exposure_correlator import (
            Asset,
            AssetSignal,
            _correlate_signals,
            CORR_OPEN_BUCKET,
            SIGNAL_TYPE_OPEN_BUCKET,
        )

        sig = AssetSignal(
            signal_type=SIGNAL_TYPE_OPEN_BUCKET,
            asset_key="test-bucket.s3.amazonaws.com",
            confidence=0.95,
            metadata={"url": "https://test-bucket.s3.amazonaws.com", "bucket_type": "s3", "status": 200},
            finding_id="bucket_001",
        )
        asset = Asset(key="test-bucket.s3.amazonaws.com", signals=[sig])

        signals = [sig]
        findings = _correlate_signals(signals)

        assert any(f.corr_type == CORR_OPEN_BUCKET for f in findings)
        bucket_finding = next(f for f in findings if f.corr_type == CORR_OPEN_BUCKET)
        assert bucket_finding.asset_key == "test-bucket.s3.amazonaws.com"
        assert bucket_finding.confidence == 0.95
        assert "bucket_001" in bucket_finding.evidence_pointers


# ============================================================================
# F202C-3: Correlation — exposed_host
# ============================================================================

class TestCorrelationExposedHost:
    """Invariant: bucket + cert/dns signals produce CORR_EXPOSED_HOST finding."""

    def test_exposed_host_bucket_plus_cert(self):
        """Asset with bucket + cert signals produces exposed_host finding."""
        from hledac.universal.intelligence.exposure_correlator import (
            Asset,
            AssetSignal,
            _correlate_signals,
            CORR_EXPOSED_HOST,
            SIGNAL_TYPE_OPEN_BUCKET,
            SIGNAL_TYPE_CT_CERT,
        )

        bucket_sig = AssetSignal(
            signal_type=SIGNAL_TYPE_OPEN_BUCKET,
            asset_key="exposed.example.com",
            confidence=0.95,
            metadata={"url": "https://exposed.example.com", "bucket_type": "s3", "status": 200},
            finding_id="bucket_001",
        )
        cert_sig = AssetSignal(
            signal_type=SIGNAL_TYPE_CT_CERT,
            asset_key="exposed.example.com",
            confidence=0.75,
            metadata={"issuer": "Let's Encrypt", "cert_count": 3, "domain": "example.com", "san": "exposed.example.com"},
            finding_id="ct_abc123",
        )

        # Same asset key → same Asset
        signals = [bucket_sig, cert_sig]
        findings = _correlate_signals(signals)

        assert any(f.corr_type == CORR_EXPOSED_HOST for f in findings)
        host_finding = next(f for f in findings if f.corr_type == CORR_EXPOSED_HOST)
        assert "bucket_001" in host_finding.evidence_pointers
        assert "ct_abc123" in host_finding.evidence_pointers

    def test_exposed_host_bucket_plus_dns(self):
        """Asset with bucket + DNS signals produces exposed_host finding."""
        from hledac.universal.intelligence.exposure_correlator import (
            Asset,
            AssetSignal,
            _correlate_signals,
            CORR_EXPOSED_HOST,
            SIGNAL_TYPE_OPEN_BUCKET,
            SIGNAL_TYPE_PASSIVE_DNS,
        )

        bucket_sig = AssetSignal(
            signal_type=SIGNAL_TYPE_OPEN_BUCKET,
            asset_key="cdn.example.com",
            confidence=0.9,
            metadata={"url": "https://cdn.example.com.s3.amazonaws.com", "bucket_type": "s3", "status": 200},
            finding_id="bucket_002",
        )
        dns_sig = AssetSignal(
            signal_type=SIGNAL_TYPE_PASSIVE_DNS,
            asset_key="cdn.example.com",
            confidence=0.7,
            metadata={"domain": "cdn.example.com", "ip": "5.6.7.8", "record_type": "A"},
            finding_id="pdns_001",
        )

        signals = [bucket_sig, dns_sig]
        findings = _correlate_signals(signals)

        assert any(f.corr_type == CORR_EXPOSED_HOST for f in findings)


# ============================================================================
# F202C-4: Correlation — cert_domain_relation
# ============================================================================

class TestCorrelationCertDomain:
    """Invariant: cert signal alone produces CORR_CERT_DOMAIN finding."""

    def test_cert_domain_relation(self):
        """Asset with only cert signal produces cert_domain_relation finding."""
        from hledac.universal.intelligence.exposure_correlator import (
            Asset,
            AssetSignal,
            _correlate_signals,
            CORR_CERT_DOMAIN,
            SIGNAL_TYPE_CT_CERT,
        )

        cert_sig = AssetSignal(
            signal_type=SIGNAL_TYPE_CT_CERT,
            asset_key="sub.example.com",
            confidence=0.75,
            metadata={"issuer": "DigiCert", "cert_count": 2, "domain": "example.com", "san": "sub.example.com"},
            finding_id="ct_def456",
        )
        signals = [cert_sig]
        findings = _correlate_signals(signals)

        assert any(f.corr_type == CORR_CERT_DOMAIN for f in findings)
        cert_finding = next(f for f in findings if f.corr_type == CORR_CERT_DOMAIN)
        assert cert_finding.asset_key == "sub.example.com"


# ============================================================================
# F202C-5: Correlation — infra_cluster (JARM)
# ============================================================================

class TestCorrelationInfraCluster:
    """Invariant: 2+ assets sharing same JARM hash produce infra_cluster finding."""

    def test_infra_cluster_two_hosts(self):
        """Two assets with same JARM hash produce infra_cluster finding."""
        from hledac.universal.intelligence.exposure_correlator import (
            Asset,
            AssetSignal,
            _correlate_signals,
            CORR_INFRA_CLUSTER,
            SIGNAL_TYPE_JARM,
        )

        jarm_hash = "1d3f0a0d0d2a0a1d3f0a0d0d2a0a1d3f0a0d0d2a0a1d3f0a0d0d2a0a1d3f"

        sig1 = AssetSignal(
            signal_type=SIGNAL_TYPE_JARM,
            asset_key="host1.example.com",
            confidence=0.85,
            metadata={"jarm_hash": jarm_hash},
            finding_id="jarm_host1",
        )
        sig2 = AssetSignal(
            signal_type=SIGNAL_TYPE_JARM,
            asset_key="host2.example.com",
            confidence=0.85,
            metadata={"jarm_hash": jarm_hash},
            finding_id="jarm_host2",
        )

        signals = [sig1, sig2]
        findings = _correlate_signals(signals)

        assert any(f.corr_type == CORR_INFRA_CLUSTER for f in findings)
        cluster_finding = next(f for f in findings if f.corr_type == CORR_INFRA_CLUSTER)
        assert cluster_finding.asset_key.startswith("cluster:")
        assert cluster_finding.confidence == 0.85
        assert "jarm_host1" in cluster_finding.evidence_pointers
        assert "jarm_host2" in cluster_finding.evidence_pointers

    def test_single_host_no_cluster(self):
        """Single host with JARM hash does NOT produce infra_cluster."""
        from hledac.universal.intelligence.exposure_correlator import (
            AssetSignal,
            _correlate_signals,
            CORR_INFRA_CLUSTER,
            SIGNAL_TYPE_JARM,
        )

        sig = AssetSignal(
            signal_type=SIGNAL_TYPE_JARM,
            asset_key="lonely.example.com",
            confidence=0.85,
            metadata={"jarm_hash": "1d3f0a0d0d2a0a1d3f0a0d0d2a0a1d3f0a0d0d2a0a1d3f0a0d0d2a0a1d3f"},
            finding_id="jarm_lonely",
        )
        signals = [sig]
        findings = _correlate_signals(signals)

        assert not any(f.corr_type == CORR_INFRA_CLUSTER for f in findings)


# ============================================================================
# F202C-6: CanonicalFinding Conversion
# ============================================================================

class TestCanonicalFindingConversion:
    """Invariant: to_canonical_findings produces valid CanonicalFinding list."""

    def test_conversion_produces_source_type(self):
        """Converted findings have source_type='exposure_correlation'."""
        from hledac.universal.intelligence.exposure_correlator import (
            ExposureFinding,
            to_canonical_findings,
            CORR_OPEN_BUCKET,
        )

        exp_finding = ExposureFinding(
            corr_type=CORR_OPEN_BUCKET,
            asset_key="test.s3.amazonaws.com",
            confidence=0.95,
            summary="Open S3 bucket: https://test.s3.amazonaws.com",
            evidence_pointers=["bucket_001"],
            signal_facets={"open_bucket": 0.95},
            suggested_pivots=[{"type": "bucket_enum", "query": "test.s3.amazonaws.com"}],
            payload={"bucket_type": "s3", "url": "https://test.s3.amazonaws.com"},
        )

        canonical = to_canonical_findings([exp_finding], "test query")

        assert len(canonical) == 1
        assert canonical[0].source_type == "exposure_correlation"
        assert canonical[0].confidence == 0.95
        assert canonical[0].query == "test query"

    def test_conversion_payload_has_evidence_envelope(self):
        """payload_text contains evidence_pointers, signal_facets, suggested_pivots."""
        from hledac.universal.intelligence.exposure_correlator import (
            ExposureFinding,
            to_canonical_findings,
            CORR_EXPOSED_HOST,
        )

        exp_finding = ExposureFinding(
            corr_type=CORR_EXPOSED_HOST,
            asset_key="host.example.com",
            confidence=0.85,
            summary="Exposed host with bucket + cert",
            evidence_pointers=["bucket_001", "ct_abc123"],
            signal_facets={"open_bucket": 0.95, "ct_cert": 0.75},
            suggested_pivots=[{"type": "ct_log", "query": "host.example.com"}],
            payload={"has_bucket": True, "has_cert": True},
        )

        canonical = to_canonical_findings([exp_finding], "host.example.com")

        payload = json.loads(canonical[0].payload_text)
        assert "evidence_pointers" in payload
        assert "signal_facets" in payload
        assert "suggested_pivots" in payload
        assert payload["evidence_pointers"] == ["bucket_001", "ct_abc123"]
        assert payload["signal_facets"]["open_bucket"] == 0.95
        assert payload["corr_type"] == CORR_EXPOSED_HOST

    def test_conversion_max_findings_cap(self):
        """to_canonical_findings respects MAX_FINDINGS cap."""
        from hledac.universal.intelligence.exposure_correlator import (
            ExposureFinding,
            to_canonical_findings,
            CORR_OPEN_BUCKET,
            MAX_FINDINGS,
        )

        findings = [
            ExposureFinding(
                corr_type=CORR_OPEN_BUCKET,
                asset_key=f"bucket_{i}",
                confidence=0.9,
                summary=f"Bucket {i}",
                evidence_pointers=[f"fid_{i}"],
                signal_facets={},
                suggested_pivots=[],
                payload={},
            )
            for i in range(MAX_FINDINGS + 100)
        ]

        canonical = to_canonical_findings(findings, "test")
        assert len(canonical) == MAX_FINDINGS


# ============================================================================
# F202C-7: Bounds Degradation
# ============================================================================

class TestBoundsDegradation:
    """Invariant: correlator degrades gracefully at MAX_ASSETS and MAX_SIGNALS_PER_ASSET limits."""

    def test_max_assets_cap(self):
        """Signals beyond MAX_ASSETS are skipped."""
        from hledac.universal.intelligence.exposure_correlator import (
            AssetSignal,
            _correlate_signals,
            SIGNAL_TYPE_CT_CERT,
            MAX_ASSETS,
        )

        signals = [
            AssetSignal(
                signal_type=SIGNAL_TYPE_CT_CERT,
                asset_key=f"san_{i}.example.com",
                confidence=0.75,
                metadata={"issuer": "Test", "cert_count": 1, "domain": "example.com", "san": f"san_{i}.example.com"},
                finding_id=f"ct_{i:04d}",
            )
            for i in range(MAX_ASSETS + 500)
        ]

        findings = _correlate_signals(signals)
        # Each asset produces at least one cert_domain finding
        # Total should be bounded by MAX_ASSETS
        cert_findings = [f for f in findings if f.corr_type == "cert_domain_relation"]
        assert len(cert_findings) <= MAX_ASSETS

    def test_max_signals_per_asset_cap(self):
        """More than MAX_SIGNALS_PER_ASSET signals are truncated per asset."""
        from hledac.universal.intelligence.exposure_correlator import (
            Asset,
            AssetSignal,
            _correlate_signals,
            SIGNAL_TYPE_CT_CERT,
            MAX_SIGNALS_PER_ASSET,
        )

        signals = [
            AssetSignal(
                signal_type=SIGNAL_TYPE_CT_CERT,
                asset_key="same.example.com",
                confidence=0.75,
                metadata={"issuer": "Test", "cert_count": 1, "domain": "example.com", "san": f"san_{i}"},
                finding_id=f"ct_{i:04d}",
            )
            for i in range(MAX_SIGNALS_PER_ASSET + 5)
        ]

        findings = _correlate_signals(signals)
        cert_findings = [f for f in findings if f.corr_type == "cert_domain_relation"]
        assert len(cert_findings) == 1  # only one asset

    def test_correlate_empty_signals(self):
        """Empty signals list returns empty findings."""
        from hledac.universal.intelligence.exposure_correlator import _correlate_signals
        findings = _correlate_signals([])
        assert findings == []


# ============================================================================
# F202C-8: Public API — correlate_exposure_signals
# ============================================================================

class TestPublicAPI:
    """Invariant: correlate_exposure_signals() returns canonical findings or []. """

    def test_empty_findings_returns_empty(self):
        """Empty findings input returns empty list."""
        from hledac.universal.intelligence.exposure_correlator import correlate_exposure_signals
        result = correlate_exposure_signals([], "test query")
        assert result == []

    def test_mixed_source_findings_correlation(self):
        """Mixed ct_log + open_storage findings produce correlated findings."""
        from hledac.universal.intelligence.exposure_correlator import correlate_exposure_signals

        ct_finding = MagicMock()
        ct_finding.finding_id = "ct_corr001"
        ct_finding.source_type = "ct_log"
        ct_finding.confidence = 0.75
        ct_finding.payload_text = json.dumps({
            "issuer": "DigiCert",
            "cert_count": 10,
            "domain": "target.com",
        })

        bucket_finding = MagicMock()
        bucket_finding.finding_id = "bucket_corr001"
        bucket_finding.source_type = "open_storage"
        bucket_finding.confidence = 0.9
        bucket_finding.payload_text = json.dumps({
            "url": "https://target-assets.s3.amazonaws.com",
            "type": "s3",
            "status": 200,
        })

        result = correlate_exposure_signals(
            [ct_finding, bucket_finding],
            "target.com",
        )

        # Should produce open_bucket + cert_domain_relation
        source_types = [f.source_type for f in result]
        assert "exposure_correlation" in source_types

    def test_failsoft_on_malformed_finding(self):
        """Malformed finding does not crash correlate_exposure_signals."""
        from hledac.universal.intelligence.exposure_correlator import correlate_exposure_signals

        bad_finding = MagicMock()
        bad_finding.finding_id = "bad"
        bad_finding.source_type = "ct_log"
        bad_finding.confidence = 0.5
        # payload_text throws during access
        del bad_finding.payload_text  # AttributeError on access

        result = correlate_exposure_signals([bad_finding], "test")
        # Should not raise, should return list (possibly empty)
        assert isinstance(result, list)


# ============================================================================
# F202C-9: Adapter
# ============================================================================

class TestExposureCorrelatorAdapter:
    """Invariant: ExposureCorrelatorAdapter wraps correlation with stats tracking."""

    def test_adapter_reset(self):
        """reset() clears internal state."""
        from hledac.universal.intelligence.exposure_correlator import (
            ExposureCorrelatorAdapter,
            reset_correlator_stats,
        )

        adapter = ExposureCorrelatorAdapter()
        adapter._stats_snapshot = {"assets_registered": 10}
        adapter.reset()

        assert adapter._stats_snapshot == {}

    def test_adapter_correlate_updates_stats(self):
        """correlate() updates stats snapshot."""
        from hledac.universal.intelligence.exposure_correlator import (
            ExposureCorrelatorAdapter,
            reset_correlator_stats,
        )

        reset_correlator_stats()

        adapter = ExposureCorrelatorAdapter()

        # Empty input
        result = adapter.correlate([], "test")
        assert result == []

        stats = adapter.get_stats()
        assert "findings_produced" in stats
        assert stats["findings_produced"] == 0


# ============================================================================
# F202C-10: Suspicious JARM Fingerprint
# ============================================================================

class TestSuspiciousJARM:
    """Invariant: JARM with GREASE/000 prefix produces suspicious_service_fingerprint finding."""

    def test_grease_jarm_flagged(self):
        """JARM hash starting with 2a2a (GREASE) produces suspicious_fp finding."""
        from hledac.universal.intelligence.exposure_correlator import (
            Asset,
            AssetSignal,
            _correlate_signals,
            CORR_SUSPICIOUS_FP,
            SIGNAL_TYPE_JARM,
        )

        grease_hash = "2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a2a"
        sig = AssetSignal(
            signal_type=SIGNAL_TYPE_JARM,
            asset_key="suspicious.example.com",
            confidence=0.85,
            metadata={"jarm_hash": grease_hash},
            finding_id="jarm_suspicious",
        )
        signals = [sig]
        findings = _correlate_signals(signals)

        assert any(f.corr_type == CORR_SUSPICIOUS_FP for f in findings)
        fp_finding = next(f for f in findings if f.corr_type == CORR_SUSPICIOUS_FP)
        assert fp_finding.asset_key == "suspicious.example.com"

    def test_zero_jarm_flagged(self):
        """JARM hash starting with 000 (no cipher) produces suspicious_fp finding."""
        from hledac.universal.intelligence.exposure_correlator import (
            AssetSignal,
            _correlate_signals,
            CORR_SUSPICIOUS_FP,
            SIGNAL_TYPE_JARM,
        )

        zero_hash = "0000000000000000000000000000000000000000000000000000000000000"
        sig = AssetSignal(
            signal_type=SIGNAL_TYPE_JARM,
            asset_key="dead.example.com",
            confidence=0.85,
            metadata={"jarm_hash": zero_hash},
            finding_id="jarm_dead",
        )
        signals = [sig]
        findings = _correlate_signals(signals)

        assert any(f.corr_type == CORR_SUSPICIOUS_FP for f in findings)


# ============================================================================
# F202C-11: Stats Tracking
# ============================================================================

class TestStatsTracking:
    """Invariant: get_correlator_stats() reflects actual processing."""

    def test_stats_reflect_extraction(self):
        """signals_extracted stat is incremented by extract_signals."""
        from hledac.universal.intelligence.exposure_correlator import (
            extract_signals,
            get_correlator_stats,
            reset_correlator_stats,
            SIGNAL_TYPE_CT_CERT,
        )

        reset_correlator_stats()

        mock_finding = MagicMock()
        mock_finding.finding_id = "ct_stats001"
        mock_finding.source_type = "ct_log"
        mock_finding.confidence = 0.75
        mock_finding.payload_text = json.dumps({
            "issuer": "Test", "cert_count": 1, "domain": "x.com", "san": "y.x.com",
        })

        extract_signals([mock_finding])
        stats = get_correlator_stats()
        assert stats["signals_extracted"] >= 1


# ============================================================================
# F202C-12: Evidence Envelope Fields
# ============================================================================

class TestEvidenceEnvelope:
    """Invariant: correlation findings include audit-ready envelope fields."""

    def test_envelope_has_all_required_fields(self):
        """ExposureFinding has all fields required for evidence envelope."""
        from hledac.universal.intelligence.exposure_correlator import (
            ExposureFinding,
            CORR_EXPOSED_HOST,
        )

        finding = ExposureFinding(
            corr_type=CORR_EXPOSED_HOST,
            asset_key="test.example.com",
            confidence=0.88,
            summary="Exposed host with multiple signals",
            evidence_pointers=["fid_a", "fid_b", "fid_c"],
            signal_facets={"open_bucket": 0.95, "ct_cert": 0.75},
            suggested_pivots=[
                {"type": "ct_log", "query": "test.example.com"},
                {"type": "passive_dns", "query": "1.2.3.4"},
            ],
            payload={"has_bucket": True, "has_cert": True},
        )

        # evidence_pointers — audit trail
        assert len(finding.evidence_pointers) == 3
        # signal_facets — confidence rationale
        assert len(finding.signal_facets) == 2
        # suggested_pivots — recommended follow-ups
        assert len(finding.suggested_pivots) == 2
        # summary — human-readable
        assert len(finding.summary) > 0
        # payload — full correlation data
        assert finding.payload["has_bucket"] is True
