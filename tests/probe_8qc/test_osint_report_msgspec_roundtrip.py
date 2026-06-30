"""
Sprint 8QC D.2: OSINTReport msgspec roundtrip encode→decode.
100% offline — no MLX, no network.
"""

import msgspec
from hledac.universal.brain.synthesis_runner import OSINTReport, IOCEntity


class TestOSINTReportRoundtrip:
    """D.2: Create → encode → decode → fields must match."""

    def test_roundtrip_full_report(self):
        """Full OSINTReport roundtrip must preserve all fields."""
        original = OSINTReport(
            query="ransomware campaign Q1 2026",
            ioc_entities=[
                IOCEntity(value="1.2.3.4", ioc_type="ip", severity="high", context="C2 server"),
                IOCEntity(value="CVE-2026-1234", ioc_type="cve", severity="critical", context="RCE in VPN"),
            ],
            threat_summary="LockBit 3.0 ransomware targeting healthcare.",
            threat_actors=["LockBit", "APT29"],
            confidence=0.92,
            sources_count=7,
            timestamp=1743500000.0,
        )

        # Encode → decode
        encoded = msgspec.json.encode(original)
        decoded = msgspec.json.decode(encoded, type=OSINTReport)

        assert decoded.query == original.query
        assert decoded.confidence == original.confidence
        assert decoded.sources_count == original.sources_count
        assert decoded.timestamp == original.timestamp
        assert decoded.threat_summary == original.threat_summary
        assert decoded.threat_actors == original.threat_actors
        assert len(decoded.ioc_entities) == len(original.ioc_entities)
        assert decoded.ioc_entities[0].value == "1.2.3.4"
        assert decoded.ioc_entities[1].ioc_type == "cve"

    def test_roundtrip_empty_iocs(self):
        """Empty ioc_entities list must roundtrip correctly."""
        original = OSINTReport(
            query="test query",
            ioc_entities=[],
            threat_summary="No IOCs found.",
            threat_actors=[],
            confidence=0.0,
            sources_count=0,
            timestamp=0.0,
        )
        encoded = msgspec.json.encode(original)
        decoded = msgspec.json.decode(encoded, type=OSINTReport)
        assert decoded.ioc_entities == []
        assert decoded.threat_actors == []
