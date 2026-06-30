"""
Sprint 8QC D.1: OSINTReport schema fields validation.
100% offline — no MLX, no network.
"""

import pytest

from hledac.universal.brain.synthesis_runner import OSINTReport, IOCEntity


class TestOSINTReportSchemaFields:
    """D.1: OSINTReport must have all required fields."""

    def test_osint_report_has_required_fields(self):
        """Verify OSINTReport has: query, ioc_entities, threat_summary, threat_actors, confidence, sources_count, timestamp."""
        fields = {"query", "ioc_entities", "threat_summary", "threat_actors", "confidence", "sources_count", "timestamp"}
        # msgspec 0.20+: __struct_fields__ is tuple of strings
        report_fields = set(getattr(OSINTReport, "__struct_fields__", ()))
        if not report_fields:
            report_fields = set(OSINTReport.__annotations__.keys()) if hasattr(OSINTReport, "__annotations__") else set()
        assert report_fields, "Could not determine OSINTReport fields"
        assert fields.issubset(report_fields), f"Missing fields: {fields - report_fields}"

    def test_ioc_entity_required_fields(self):
        """IOCEntity must have: value, ioc_type, severity, context."""
        fields = {"value", "ioc_type", "severity", "context"}
        entity_fields = set(getattr(IOCEntity, "__struct_fields__", ()))
        if not entity_fields:
            entity_fields = set(IOCEntity.__annotations__.keys()) if hasattr(IOCEntity, "__annotations__") else set()
        assert entity_fields, "Could not determine IOCEntity fields"
        assert fields.issubset(entity_fields), f"Missing fields: {fields - entity_fields}"

    def test_threat_actors_is_list_of_str(self):
        """threat_actors must be list[str]."""
        report = OSINTReport(
            query="ransomware",
            ioc_entities=[],
            threat_summary="test",
            threat_actors=["APT29", "LockBit"],
            confidence=0.9,
            sources_count=5,
            timestamp=123456.0,
        )
        assert isinstance(report.threat_actors, list)
        assert all(isinstance(actor, str) for actor in report.threat_actors)

    def test_timestamp_is_float(self):
        """timestamp must be float (Unix epoch)."""
        report = OSINTReport(
            query="test",
            ioc_entities=[],
            threat_summary="test",
            threat_actors=[],
            confidence=0.5,
            sources_count=1,
            timestamp=123456.789,
        )
        assert isinstance(report.timestamp, float)

    def test_ioc_entities_is_list_of_iocentity(self):
        """ioc_entities must be list[IOCEntity]."""
        entity = IOCEntity(value="1.2.3.4", ioc_type="ip", severity="high", context="C2 server")
        report = OSINTReport(
            query="test",
            ioc_entities=[entity],
            threat_summary="test",
            threat_actors=[],
            confidence=0.5,
            sources_count=1,
            timestamp=123456.0,
        )
        assert isinstance(report.ioc_entities, list)
        assert all(isinstance(e, IOCEntity) for e in report.ioc_entities)

    def test_confidence_range(self):
        """confidence must be float 0.0-1.0."""
        report = OSINTReport(
            query="test",
            ioc_entities=[],
            threat_summary="test",
            threat_actors=[],
            confidence=0.75,
            sources_count=1,
            timestamp=123456.0,
        )
        assert 0.0 <= report.confidence <= 1.0
