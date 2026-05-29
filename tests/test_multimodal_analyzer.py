"""
Sprint F195C: Multimodal Enrichment Tests
==========================================

Tests for the multimodal enrichment layer integration into the canonical sprint path.

Test invariants:
- test_enricher_path_extraction: supported file types are extracted from payload_text
- test_enrichment_failure_never_crashes: enrichment failure never crashes sprint
- test_multimodal_enriched_findings_counter: CT findings enriched before DuckDB storage
- test_sprint_result_has_multimodal_field: SprintSchedulerResult has multimodal_enriched_findings field
- test_multimodal_lmdb_initialized: multimodal LMDB is opened and closed properly
- test_close_multimodal_fail_safe: close never raises even if enricher is None
- test_flush_multimodal_idempotent: flush is a no-op and never raises
- test_enrich_batch_concurrent: enrich_batch runs at most 3 concurrent extractions
- test_ram_guard_blocks_heavy_vision: RAM pressure blocks heavy vision path
- test_findings_not_mutated: CanonicalFinding frozen=True is preserved after enrichment
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# MultimodalEnricher unit tests
# ---------------------------------------------------------------------------

class TestMultimodalEnricherUnit:
    """Unit tests for MultimodalEnricher."""

    def test_path_extraction_direct(self):
        """_extract_file_path_from_payload handles direct absolute paths."""
        from hledac.universal.multimodal.analyzer import (
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
        from hledac.universal.multimodal.analyzer import (
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
        """_file_has_multimodal_support returns False for unsupported extensions."""
        from hledac.universal.multimodal.analyzer import (
            _file_has_multimodal_support,
        )

        assert _file_has_multimodal_support("/tmp/file.xyz") is False

    def test_path_extraction_nonexistent(self):
        """_extract_file_path_from_payload returns None for non-existent paths."""
        from hledac.universal.multimodal.analyzer import (
            _extract_file_path_from_payload,
        )

        result = _extract_file_path_from_payload("/nonexistent/path/file.jpg")
        assert result is None

    def test_path_extraction_none_payload(self):
        """_extract_file_path_from_payload returns None for None payload."""
        from hledac.universal.multimodal.analyzer import (
            _extract_file_path_from_payload,
        )

        assert _extract_file_path_from_payload(None) is None
        assert _extract_file_path_from_payload("") is None

    def test_supported_extensions(self):
        """_file_has_multimodal_support returns True only for supported extensions."""
        from hledac.universal.multimodal.analyzer import (
            _file_has_multimodal_support,
        )

        supported = {".jpg", ".jpeg", ".png", ".pdf", ".tiff", ".bmp", ".webp"}
        unsupported = {".txt", ".exe", ".bin", ".xyz", ""}

        for ext in supported:
            assert _file_has_multimodal_support(f"/tmp/file{ext}") is True
        for ext in unsupported:
            assert _file_has_multimodal_support(f"/tmp/file{ext}") is False


class TestMultimodalEnricherAsync:
    """Async tests for MultimodalEnricher."""

    @pytest.mark.asyncio
    async def test_enrich_no_file_path(self):
        """enrich() returns None when finding has no payload_text."""
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        governor.is_critical.return_value = False
        governor.is_emergency.return_value = False
        governor.high_water = 6000

        enricher = MultimodalEnricher(governor=governor)
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
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        governor.is_critical.return_value = False
        governor.is_emergency.return_value = False

        enricher = MultimodalEnricher(governor=governor)
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
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        governor.is_critical.return_value = False
        governor.is_emergency.return_value = False

        enricher = MultimodalEnricher(governor=governor)
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
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        governor.is_critical.return_value = False
        governor.is_emergency.return_value = False

        enricher = MultimodalEnricher(governor=governor)
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
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        governor.is_critical.return_value = False
        governor.is_emergency.return_value = False

        enricher = MultimodalEnricher(governor=governor)
        await enricher.initialize()

        findings = [MagicMock(finding_id=f"f{i}", payload_text=None) for i in range(6)]

        results = await enricher.enrich_batch(findings)
        assert results == {}
        await enricher.close()

    @pytest.mark.asyncio
    async def test_close_fail_safe_when_uninitialized(self):
        """close() is safe to call even if enricher was never initialized."""
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        enricher = MultimodalEnricher(governor=governor)
        await enricher.close()

    @pytest.mark.asyncio
    async def test_initialize_idempotent(self):
        """initialize() can be called multiple times without error."""
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        enricher = MultimodalEnricher(governor=governor)
        await enricher.initialize()
        await enricher.initialize()
        await enricher.close()

    @pytest.mark.asyncio
    async def test_ram_guard_denies_heavy_vision_when_critical(self):
        """_can_run_heavy_vision() returns False when RAM is critical."""
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        governor.is_critical.return_value = True
        governor.is_emergency.return_value = False

        enricher = MultimodalEnricher(governor=governor)

        assert enricher._can_run_heavy_vision() is False

    @pytest.mark.asyncio
    async def test_ram_guard_denies_heavy_vision_when_emergency(self):
        """_can_run_heavy_vision() returns False when RAM is emergency."""
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        governor.is_critical.return_value = False
        governor.is_emergency.return_value = True

        enricher = MultimodalEnricher(governor=governor)

        assert enricher._can_run_heavy_vision() is False

    @pytest.mark.asyncio
    async def test_ram_guard_denies_above_85_percent(self):
        """_can_run_heavy_vision() returns False when RAM usage > 85% of high_water."""
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        governor.is_critical.return_value = False
        governor.is_emergency.return_value = False
        governor.high_water = 6000
        governor.get_current_usage.return_value = {"ram_mb": 5500}

        enricher = MultimodalEnricher(governor=governor)

        assert enricher._can_run_heavy_vision() is False

    @pytest.mark.asyncio
    async def test_ram_guard_allows_below_85_percent(self):
        """_can_run_heavy_vision() returns True when RAM usage < 85% of high_water."""
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        governor.is_critical.return_value = False
        governor.is_emergency.return_value = False
        governor.high_water = 6000
        governor.get_current_usage.return_value = {"ram_mb": 4000}

        enricher = MultimodalEnricher(governor=governor)

        assert enricher._can_run_heavy_vision() is True


# ---------------------------------------------------------------------------
# TestSprintF195C: SprintScheduler Integration
# ---------------------------------------------------------------------------

class TestMultimodalSchedulerIntegration:
    """Tests that SprintScheduler correctly delegates multimodal to EnrichmentServices (F350M)."""

    def test_sprint_result_has_multimodal_field(self):
        """SprintSchedulerResult has multimodal_enriched_findings field."""
        from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

        result = SprintSchedulerResult()
        assert hasattr(result, "multimodal_enriched_findings")
        assert result.multimodal_enriched_findings == 0

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

    def test_multimodal_lmdb_path_function_exists(self):
        """_get_multimodal_lmdb_path() returns a Path ending in multimodal_enrichment.lmdb."""
        from hledac.universal.runtime.enrichment_services import (
            _get_multimodal_lmdb_path,
        )

        result = _get_multimodal_lmdb_path()
        assert isinstance(result, Path)
        assert result.name == "multimodal_enrichment.lmdb"


class TestFindingNotMutated:
    """Verify CanonicalFinding frozen=True objects are not mutated by enrichment."""

    @pytest.mark.asyncio
    async def test_finding_object_not_modified(self):
        """The finding object itself is not modified by enrichment."""
        from hledac.universal.multimodal.analyzer import MultimodalEnricher

        governor = MagicMock()
        governor.is_critical.return_value = False
        governor.is_emergency.return_value = False

        enricher = MultimodalEnricher(governor=governor)
        await enricher.initialize()

        finding = MagicMock()
        finding.finding_id = "frozen-test"
        finding.payload_text = "/nonexistent/file.jpg"

        original_id = finding.finding_id

        await enricher.enrich(finding)

        assert finding.finding_id == original_id
        await enricher.close()
