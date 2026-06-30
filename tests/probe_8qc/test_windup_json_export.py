"""
Sprint 8QC D.11: WINDUP JSON export to ~/.hledac/reports/.
100% offline — mocks synthesis, tests export path.
"""

import tempfile
from pathlib import Path

import pytest

from hledac.universal.brain.synthesis_runner import OSINTReport, IOCEntity, export_report


class TestWindupExport:
    """D.11: export_report exports valid JSON to reports_dir."""

    @pytest.mark.asyncio
    async def test_export_report_creates_json_file(self):
        """export_report must create a JSON file parseable as OSINTReport."""
        import asyncio
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir)

            report = OSINTReport(
                query="ransomware attack",
                ioc_entities=[
                    IOCEntity(value="5.5.5.5", ioc_type="ip", severity="high", context="C2 server"),
                ],
                threat_summary="LockBit 3.0 ransomware attack detected.",
                threat_actors=["LockBit", "APT29"],
                confidence=0.93,
                sources_count=8,
                timestamp=1743500000.0,
            )

            out_path = await export_report(report, "ransomware attack", reports_dir=reports_dir)

            assert out_path.exists(), "Report file was not created"
            content = out_path.read_text(encoding="utf-8")
            assert content  # Not empty

            # Parse back — must be valid OSINTReport
            import msgspec
            decoded = msgspec.json.decode(content.encode(), type=OSINTReport)
            assert decoded.query == "ransomware attack"
            assert decoded.confidence == 0.93
            assert decoded.threat_actors == ["LockBit", "APT29"]
            assert len(decoded.ioc_entities) == 1
            assert decoded.ioc_entities[0].value == "5.5.5.5"

    @pytest.mark.asyncio
    async def test_export_report_filename_contains_timestamp(self):
        """Filename must start with Unix timestamp (integer)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            reports_dir = Path(tmpdir)

            report = OSINTReport(
                query="test",
                ioc_entities=[],
                threat_summary="test",
                threat_actors=[],
                confidence=0.5,
                sources_count=1,
                timestamp=1743500000.0,
            )

            out_path = await export_report(report, "test", reports_dir=reports_dir)

            filename = out_path.name
            # Filename format: {ts}_{slug}_report.json
            ts_part = filename.split("_")[0]
            assert ts_part.isdigit(), f"Timestamp part is not digit: {ts_part}"

    def test_slugify_produces_safe_filename(self):
        """slugify must produce lowercase hyphenated names."""
        from hledac.universal.brain.synthesis_runner import slugify

        assert slugify("Ransomware Attack 2026") == "ransomware-attack-2026"
        assert slugify("APT28 / Sandworm") == "apt28-sandworm"
        assert slugify("  Multiple   Spaces  ") == "multiple-spaces"
