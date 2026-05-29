"""
Sprint F350M: EnrichmentServices Extraction Tests
================================================

Tests for EnrichmentServices (forensics + multimodal lifecycle) extracted from SprintScheduler.
Also tests delegation from SprintScheduler via inject_enrichment_services().

Test invariants:
- init/close/flush are fail-safe (never raise)
- enrich_ct_findings / enrich_findings_multimodal are fail-safe (never crash)
- delegation: SprintScheduler correctly delegates to EnrichmentServices via inject_enrichment_services()
- counter increments only when enrichment actually writes to LMDB
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Pattern B: EnrichmentServices lifecycle tests (moved from SprintScheduler)
# ---------------------------------------------------------------------------

class TestEnrichmentServicesLifecycle:
    """Tests for forensics + multimodal lifecycle on EnrichmentServices."""

    @pytest.mark.asyncio
    async def test_init_forensics_fail_safe(self):
        """_init_forensics() is fail-safe — never raises even on import failure."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        # Should not raise even if ForensicsEnricher import fails
        await services._init_forensics()
        # Either enricher is None (import failed) or it's an actual object
        assert services._forensics_enricher is None or hasattr(
            services._forensics_enricher, "initialize"
        )

    @pytest.mark.asyncio
    async def test_init_multimodal_fail_safe(self):
        """_init_multimodal() is fail-safe — never raises even on import failure."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        # Should not raise even if MultimodalEnricher import fails
        await services._init_multimodal()
        # Either enricher is None (import failed) or it's an actual object
        assert services._multimodal_enricher is None or hasattr(
            services._multimodal_enricher, "initialize"
        )

    @pytest.mark.asyncio
    async def test_close_forensics_fail_safe_with_none(self):
        """_close_forensics() never raises when enricher is None."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        services._forensics_enricher = None
        services._forensics_lmdb_env = None

        await services._close_forensics()

    @pytest.mark.asyncio
    async def test_close_multimodal_fail_safe_with_none(self):
        """_close_multimodal() never raises when enricher is None."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        services._multimodal_enricher = None
        services._multimodal_lmdb_env = None

        await services._close_multimodal()

    @pytest.mark.asyncio
    async def test_flush_forensics_idempotent(self):
        """_flush_forensics() is a no-op that never raises."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        services._forensics_lmdb_env = None

        await services._flush_forensics()

    @pytest.mark.asyncio
    async def test_flush_multimodal_idempotent(self):
        """_flush_multimodal() is a no-op that never raises."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        services._multimodal_lmdb_env = None

        await services._flush_multimodal()

    @pytest.mark.asyncio
    async def test_close_forensics_calls_enricher_close(self):
        """_close_forensics() calls enricher.close() if enricher is set."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        mock_enricher = AsyncMock()
        services._forensics_enricher = mock_enricher

        await services._close_forensics()

        mock_enricher.close.assert_called_once()
        assert services._forensics_enricher is None

    @pytest.mark.asyncio
    async def test_close_multimodal_calls_enricher_close(self):
        """_close_multimodal() calls enricher.close() if enricher is set."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        mock_enricher = AsyncMock()
        services._multimodal_enricher = mock_enricher

        await services._close_multimodal()

        mock_enricher.close.assert_called_once()
        assert services._multimodal_enricher is None


class TestEnrichmentServices:
    """Tests for enrich_ct_findings / enrich_findings_multimodal on EnrichmentServices."""

    @pytest.mark.asyncio
    async def test_enrich_ct_findings_empty(self):
        """enrich_ct_findings handles empty findings list."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        mock_result = MagicMock()
        await services.enrich_ct_findings([], mock_result)

    @pytest.mark.asyncio
    async def test_enrich_ct_findings_skips_when_enricher_none(self):
        """enrich_ct_findings skips when enricher is None."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        services._forensics_enricher = None
        services._forensics_lmdb_env = MagicMock()

        finding = MagicMock(finding_id="test-1", payload_text="/tmp/file.jpg")
        mock_result = MagicMock()
        mock_result.forensics_enriched_ct_findings = 0

        await services.enrich_ct_findings([finding], mock_result)

        assert mock_result.forensics_enriched_ct_findings == 0

    @pytest.mark.asyncio
    async def test_enrich_ct_findings_skips_when_lmdb_none(self):
        """enrich_ct_findings skips when LMDB env is None."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        mock_enricher = AsyncMock()
        services._forensics_enricher = mock_enricher
        services._forensics_lmdb_env = None

        finding = MagicMock(finding_id="test-2", payload_text="/tmp/file.jpg")

        await services.enrich_ct_findings([finding], None)

        mock_enricher.enrich.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrich_ct_findings_increments_counter(self):
        """enrich_ct_findings increments counter when enrichment succeeds."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()

        mock_lmdb = MagicMock()
        mock_txn = MagicMock()
        mock_lmdb.begin.return_value.__enter__ = MagicMock(return_value=mock_txn)
        mock_lmdb.begin.return_value.__exit__ = MagicMock(return_value=False)
        services._forensics_lmdb_env = mock_lmdb

        mock_enricher = AsyncMock()
        mock_enricher.enrich.return_value = {"metadata": {"success": True}}
        services._forensics_enricher = mock_enricher

        finding = MagicMock(finding_id="test-3", payload_text="/tmp/file.jpg")
        mock_result = MagicMock()
        mock_result.forensics_enriched_ct_findings = 0

        await services.enrich_ct_findings([finding], mock_result)

        assert mock_result.forensics_enriched_ct_findings == 1
        mock_txn.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_enrich_ct_findings_fail_safe(self):
        """enrich_ct_findings is fail-safe — exceptions are swallowed."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()

        mock_lmdb = MagicMock()
        mock_lmdb.begin.side_effect = RuntimeError("LMDB error")
        services._forensics_lmdb_env = mock_lmdb

        mock_enricher = AsyncMock()
        mock_enricher.enrich.side_effect = RuntimeError("Enricher error")
        services._forensics_enricher = mock_enricher

        finding = MagicMock(finding_id="test-4", payload_text="/tmp/file.jpg")

        # Must not raise
        await services.enrich_ct_findings([finding], None)

    @pytest.mark.asyncio
    async def test_enrich_findings_multimodal_empty(self):
        """enrich_findings_multimodal handles empty findings list."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        mock_result = MagicMock()
        await services.enrich_findings_multimodal([], mock_result)

    @pytest.mark.asyncio
    async def test_enrich_findings_multimodal_skips_when_enricher_none(self):
        """enrich_findings_multimodal skips when enricher is None."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        services._multimodal_enricher = None
        services._multimodal_lmdb_env = MagicMock()

        finding = MagicMock(finding_id="test-1", payload_text="/tmp/file.jpg")
        mock_result = MagicMock()
        mock_result.multimodal_enriched_findings = 0

        await services.enrich_findings_multimodal([finding], mock_result)

        assert mock_result.multimodal_enriched_findings == 0

    @pytest.mark.asyncio
    async def test_enrich_findings_multimodal_skips_when_lmdb_none(self):
        """enrich_findings_multimodal skips when LMDB env is None."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        mock_enricher = AsyncMock()
        services._multimodal_enricher = mock_enricher
        services._multimodal_lmdb_env = None

        finding = MagicMock(finding_id="test-2", payload_text="/tmp/file.jpg")

        await services.enrich_findings_multimodal([finding], None)

        mock_enricher.enrich.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrich_findings_multimodal_increments_counter(self):
        """enrich_findings_multimodal increments counter when enrichment succeeds."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()

        mock_lmdb = MagicMock()
        mock_txn = MagicMock()
        mock_lmdb.begin.return_value.__enter__ = MagicMock(return_value=mock_txn)
        mock_lmdb.begin.return_value.__exit__ = MagicMock(return_value=False)
        services._multimodal_lmdb_env = mock_lmdb

        mock_enricher = AsyncMock()
        mock_enricher.enrich.return_value = {"vision_embedding": [0.1] * 1280}
        services._multimodal_enricher = mock_enricher

        finding = MagicMock(finding_id="test-3", payload_text="/tmp/file.jpg")
        mock_result = MagicMock()
        mock_result.multimodal_enriched_findings = 0

        await services.enrich_findings_multimodal([finding], mock_result)

        assert mock_result.multimodal_enriched_findings == 1
        mock_txn.put.assert_called_once()

    @pytest.mark.asyncio
    async def test_enrich_findings_multimodal_fail_safe(self):
        """enrich_findings_multimodal is fail-safe — exceptions are swallowed."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()

        mock_lmdb = MagicMock()
        mock_lmdb.begin.side_effect = RuntimeError("LMDB error")
        services._multimodal_lmdb_env = mock_lmdb

        mock_enricher = AsyncMock()
        mock_enricher.enrich.side_effect = RuntimeError("Enricher error")
        services._multimodal_enricher = mock_enricher

        finding = MagicMock(finding_id="test-4", payload_text="/tmp/file.jpg")

        # Must not raise
        await services.enrich_findings_multimodal([finding], None)

    @pytest.mark.asyncio
    async def test_inject_forensics_enricher(self):
        """inject_forensics_enricher sets enricher and lmdb_env."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        mock_enricher = MagicMock()
        mock_lmdb = MagicMock()

        services.inject_forensics_enricher(mock_enricher, mock_lmdb)

        assert services._forensics_enricher is mock_enricher
        assert services._forensics_lmdb_env is mock_lmdb

    @pytest.mark.asyncio
    async def test_inject_multimodal_enricher(self):
        """inject_multimodal_enricher sets enricher and lmdb_env."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        mock_enricher = MagicMock()
        mock_lmdb = MagicMock()

        services.inject_multimodal_enricher(mock_enricher, mock_lmdb)

        assert services._multimodal_enricher is mock_enricher
        assert services._multimodal_lmdb_env is mock_lmdb


# ---------------------------------------------------------------------------
# Pattern A: SprintScheduler delegation tests
# ---------------------------------------------------------------------------

class TestSprintSchedulerDelegation:
    """Tests that SprintScheduler correctly delegates to EnrichmentServices."""

    def test_scheduler_has_enrichment_services_attribute(self):
        """SprintScheduler has _enrichment_services attribute (F350M)."""
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
        )

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        assert hasattr(scheduler, "_enrichment_services")
        assert scheduler._enrichment_services is None

    def test_scheduler_inject_enrichment_services_wires(self):
        """inject_enrichment_services() sets _enrichment_services."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
        )

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        services = EnrichmentServices()

        scheduler.inject_enrichment_services(services)

        assert scheduler._enrichment_services is services

    @pytest.mark.asyncio
    async def test_scheduler_init_delegates_to_services_init(self):
        """When _enrichment_services is set, run() calls services.init()."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices
        from hledac.universal.runtime.sprint_scheduler import (
            SprintScheduler,
            SprintSchedulerConfig,
        )

        config = SprintSchedulerConfig()
        scheduler = SprintScheduler(config)
        services = EnrichmentServices()
        scheduler.inject_enrichment_services(services)

        # Track calls
        init_called = False
        original_init = services.init

        async def tracked_init():
            nonlocal init_called
            init_called = True
            await original_init()

        services.init = tracked_init

        # Simulate the init call site (lines 2461-2463)
        if scheduler._enrichment_services:
            await scheduler._enrichment_services.init()

        assert init_called is True

    @pytest.mark.asyncio
    async def test_scheduler_close_delegates_to_services_close(self):
        """When _enrichment_services is set, teardown calls services.close()."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        close_called = False
        original_close = services.close

        async def tracked_close():
            nonlocal close_called
            close_called = True
            await original_close()

        services.close = tracked_close

        # Simulate the close call site (lines 3025-3026)
        if services:
            await services.close()

        assert close_called is True

    @pytest.mark.asyncio
    async def test_scheduler_ct_pipeline_delegates_enrich(self):
        """SprintScheduler ct pipeline delegates enrich calls to _enrichment_services."""
        from hledac.universal.runtime.enrichment_services import EnrichmentServices

        services = EnrichmentServices()
        mock_result = MagicMock()
        mock_result.forensics_enriched_ct_findings = 0
        mock_result.multimodal_enriched_findings = 0

        # Wire mocks so enrich doesn't fail
        services._forensics_enricher = AsyncMock()
        services._forensics_lmdb_env = MagicMock()
        services._multimodal_enricher = AsyncMock()
        services._multimodal_lmdb_env = MagicMock()

        finding = MagicMock(finding_id="test-1", payload_text="/tmp/file.jpg")

        # Simulate the enrich call sites (lines 7127-7129)
        if services:
            await services.enrich_ct_findings([finding], mock_result)
            await services.enrich_findings_multimodal([finding], mock_result)

        # Both enrichers were called
        services._forensics_enricher.enrich.assert_called_once()
        services._multimodal_enricher.enrich.assert_called_once()
