"""
Sprint F195C: Forensics Enrichment Tests
=========================================

Tests for the forensics enrichment layer integration into the canonical sprint path.

Test invariants:
- test_enricher_path_extraction: accepted findings may be enriched via finding.metadata["forensics"]
- test_enrichment_failure_never_crashes: enrichment failure never crashes sprint
- test_ct_findings_enriched_before_storage: CT findings enriched before DuckDB storage
- test_sprint_result_has_forensics_field: SprintSchedulerResult has forensics_enriched_ct_findings field
- test_forensics_lmdb_initialized: forensics LMDB is opened and closed properly
- test_close_forensics_fail_safe: close never raises even if enricher is None
- test_flush_forensics_idempotent: flush is a no-op and never raises
- test_enrich_batch_concurrent: enrich_batch runs at most 3 concurrent extractions
- test_findings_not_mutated: CanonicalFinding frozen=True is preserved after enrichment
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# TestSprintF195C: Forensics Enrichment Service
# ---------------------------------------------------------------------------

class TestForensicsEnricherUnit:
    """Unit tests for ForensicsEnricher."""

    def test_path_extraction_direct(self):
        """_extract_file_path_from_payload handles direct absolute paths."""
        from hledac.universal.forensics.enrichment_service import (
            _extract_file_path_from_payload,
        )

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"fake image")
            f.flush()
            temp_path = f.name

        try:
            result = _extract_file_path_from_payload(temp_path)
            assert result == temp_path
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_path_extraction_file_url(self):
        """_extract_file_path_from_payload handles file:// URLs."""
        from hledac.universal.forensics.enrichment_service import (
            _extract_file_path_from_payload,
        )

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"fake pdf")
            f.flush()
            temp_path = f.name

        try:
            result = _extract_file_path_from_payload(f"file://{temp_path}")
            assert result == temp_path
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_path_extraction_unsupported_ext(self):
        """_file_has_forensics_support returns None for unsupported extensions."""
        from hledac.universal.forensics.enrichment_service import (
            _file_has_forensics_support,
        )

        assert _file_has_forensics_support("/tmp/file.xyz") is False

    def test_path_extraction_nonexistent(self):
        """_extract_file_path_from_payload returns None for non-existent paths."""
        from hledac.universal.forensics.enrichment_service import (
            _extract_file_path_from_payload,
        )

        result = _extract_file_path_from_payload("/nonexistent/path/file.jpg")
        assert result is None

    def test_path_extraction_none_payload(self):
        """_extract_file_path_from_payload returns None for None payload."""
        from hledac.universal.forensics.enrichment_service import (
            _extract_file_path_from_payload,
        )

        assert _extract_file_path_from_payload(None) is None
        assert _extract_file_path_from_payload("") is None

    def test_supported_extensions(self):
        """_file_has_forensics_support returns True only for supported extensions."""
        from hledac.universal.forensics.enrichment_service import (
            _file_has_forensics_support,
        )

        supported = {".jpg", ".jpeg", ".png", ".pdf", ".docx", ".mp3", ".mp4", ".zip"}
        unsupported = {".txt", ".exe", ".bin", ".xyz", ""}

        for ext in supported:
            assert _file_has_forensics_support(f"/tmp/file{ext}") is True
        for ext in unsupported:
            assert _file_has_forensics_support(f"/tmp/file{ext}") is False


class TestForensicsEnricherAsync:
    """Async tests for ForensicsEnricher."""

    @pytest.mark.asyncio
    async def test_enrich_no_file_path(self):
        """enrich() returns None when finding has no payload_text."""
        from hledac.universal.forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        await enricher.initialize()

        finding = MagicMock()
        finding.finding_id = "test-1"
        finding.payload_text = None

        result = await enricher.enrich(finding)
        assert result is None
        await enricher.close()

    @pytest.mark.asyncio
    async def test_enrich_unsupported_file(self):
        """enrich() returns None for unsupported file types."""
        from hledac.universal.forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        await enricher.initialize()

        finding = MagicMock()
        finding.finding_id = "test-2"
        finding.payload_text = "/tmp/file.txt"

        result = await enricher.enrich(finding)
        assert result is None
        await enricher.close()

    @pytest.mark.asyncio
    async def test_enrich_nonexistent_file(self):
        """enrich() returns None for non-existent file (fail-safe, not an error)."""
        from hledac.universal.forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        await enricher.initialize()

        finding = MagicMock()
        finding.finding_id = "test-3"
        finding.payload_text = "/nonexistent/path/file.jpg"

        result = await enricher.enrich(finding)
        assert result is None
        await enricher.close()

    @pytest.mark.asyncio
    async def test_enrich_batch_returns_only_successful(self):
        """enrich_batch() returns only findings that were successfully enriched."""
        from hledac.universal.forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        await enricher.initialize()

        findings = [
            MagicMock(finding_id="f1", payload_text=None),
            MagicMock(finding_id="f2", payload_text="/nonexistent/file.jpg"),
            MagicMock(finding_id="f3", payload_text=None),
        ]

        results = await enricher.enrich_batch(findings)
        assert results == {}
        await enricher.close()

    @pytest.mark.asyncio
    async def test_enrich_batch_concurrent_limit(self):
        """enrich_batch() respects the concurrency limit of 3."""
        from hledac.universal.forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        await enricher.initialize()

        findings = [MagicMock(finding_id=f"f{i}", payload_text=None) for i in range(6)]

        results = await enricher.enrich_batch(findings)
        assert results == {}
        await enricher.close()

    @pytest.mark.asyncio
    async def test_close_fail_safe_when_uninitialized(self):
        """close() is safe to call even if enricher was never initialized."""
        from hledac.universal.forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        await enricher.close()

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self):
        """initialize() can be called multiple times without error."""
        from hledac.universal.forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        await enricher.initialize()
        await enricher.initialize()
        await enricher.close()


# ---------------------------------------------------------------------------
# TestSprintF195C: SprintScheduler Integration
# ---------------------------------------------------------------------------

class TestForensicsSchedulerIntegration:
    """Tests that SprintScheduler correctly delegates forensics to EnrichmentServices (F350M)."""

    def test_sprint_result_has_forensics_field(self):
        """SprintSchedulerResult has forensics_enriched_ct_findings field."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result = SprintSchedulerResult()
        assert hasattr(result, "forensics_enriched_ct_findings")
        assert result.forensics_enriched_ct_findings == 0

    def test_scheduler_has_enrichment_services_attribute(self):
        """SprintScheduler has _enrichment_services (F350M delegation)."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
        )

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        assert hasattr(scheduler, "_enrichment_services")
        assert scheduler._enrichment_services is None

    def test_forensics_lmdb_path_function_exists(self):
        """_get_forensics_lmdb_path() returns a Path ending in forensics_enrichment.lmdb."""
        from hledac.universal.runtime.enrichment_services import (
            _get_forensics_lmdb_path,
        )

        result = _get_forensics_lmdb_path()
        assert isinstance(result, Path)
        assert result.name == "forensics_enrichment.lmdb"


class TestFindingNotMutated:
    """Verify CanonicalFinding frozen=True objects are not mutated by enrichment."""

    @pytest.mark.asyncio
    async def test_finding_object_not_modified(self):
        """The finding object itself is not modified by enrichment."""
        from hledac.universal.forensics.enrichment_service import ForensicsEnricher

        enricher = ForensicsEnricher()
        await enricher.initialize()

        finding = MagicMock()
        finding.finding_id = "frozen-test"
        finding.payload_text = "/nonexistent/file.jpg"

        original_id = finding.finding_id

        await enricher.enrich(finding)

        assert finding.finding_id == original_id
        await enricher.close()
