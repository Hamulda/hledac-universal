"""
Tests for StorageTrinity — Unified write boundary for DuckDB + LMDB + LanceDB.

ARCH-STR-001: Storage Trinity synchronization gaps.

Tests verify:
    1. Phase ordering: DuckDB → LMDB → LanceDB (enforced).
    2. DuckDB failure rolls back nothing (no prior phases) but LanceDB never runs.
    3. LanceDB failure preserves DuckDB + LMDB (canonical path intact).
    4. Ghost entity prevention: LanceDB writes only after DuckDB confirmed.
    5. Bounded queue: MAX_LANCE_QUEUE limits pending embeddings.
    6. Fail-open: LanceDB failures never propagate to caller.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.knowledge.storage_trinity import (
    LANCE_FLUSH_INTERVAL_S,
    MAX_LANCE_QUEUE,
    StorageTrinity,
    TrinityPhaseError,
    TrinityPhaseResult,
    TrinityRollbackError,
    TrinityWriteResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_duckdb_store():
    """Mock DuckDBShadowStore."""
    store = MagicMock()
    store.async_ingest_findings_batch = AsyncMock(return_value=[])
    return store


@pytest.fixture
def mock_semantic_store():
    """Mock SemanticStore."""
    store = MagicMock()
    store.add_text = MagicMock()
    store.flush = AsyncMock(return_value=10)
    store.initialize = AsyncMock()
    return store


@pytest.fixture
def trinity(mock_duckdb_store):
    """StorageTrinity instance with mock stores."""
    return StorageTrinity(duckdb_store=mock_duckdb_store)


# ---------------------------------------------------------------------------
# Invariant tests
# ---------------------------------------------------------------------------


class TestTrinityPhaseOrder:
    """ARCH-STR-001: Phase ordering must be enforced."""

    @pytest.mark.asyncio
    async def test_duckdb_first_lance_last(self, trinity, mock_duckdb_store, mock_semantic_store):
        """DuckDB must be called before LanceDB."""
        # Setup
        trinity.inject_semantic_store(mock_semantic_store)

        findings = [MagicMock(payload_text="test", finding_id="f1", source_type="test")]

        call_order = []
        mock_duckdb_store.async_ingest_findings_batch = AsyncMock(
            side_effect=lambda _: (call_order.append("duckdb"), [])
        )
        mock_semantic_store.add_text = MagicMock(
            side_effect=lambda **_: call_order.append("lance")
        )

        # Run
        await trinity.upsert_findings_batch(findings)

        # Verify order: duckdb must come before lance
        assert call_order == ["duckdb", "lance"], (
            f"Phase order violated: {call_order}. Expected ['duckdb', 'lance']"
        )


class TestTrinityDuckDBFailure:
    """DuckDB failure must prevent LanceDB from running."""

    @pytest.mark.asyncio
    async def test_duckdb_fail_keeps_lance_intact(self, mock_duckdb_store, mock_semantic_store):
        """When DuckDB fails, LanceDB must NOT be called."""
        trinity = StorageTrinity(duckdb_store=mock_duckdb_store)
        trinity.inject_semantic_store(mock_semantic_store)

        findings = [MagicMock(payload_text="test")]
        mock_duckdb_store.async_ingest_findings_batch = AsyncMock(
            side_effect=RuntimeError("DuckDB connection failed")
        )

        # Run
        result = await trinity.upsert_findings_batch(findings)

        # Verify: DuckDB failed, LanceDB not called
        assert result.duckdb.success is False
        assert "DuckDB connection failed" in str(result.duckdb.error)
        mock_semantic_store.add_text.assert_not_called()


class TestTrinityLanceFailure:
    """LanceDB failure must NOT affect DuckDB (canonical path intact)."""

    @pytest.mark.asyncio
    async def test_lance_fail_preserves_duckdb(self, mock_duckdb_store, mock_semantic_store):
        """LanceDB failure must not roll back DuckDB writes."""
        trinity = StorageTrinity(duckdb_store=mock_duckdb_store)
        trinity.inject_semantic_store(mock_semantic_store)

        findings = [MagicMock(payload_text="test", finding_id="f1")]
        mock_duckdb_store.async_ingest_findings_batch = AsyncMock(return_value=[
            MagicMock(accepted=True)
        ])
        mock_semantic_store.add_text = MagicMock(
            side_effect=RuntimeError("LanceDB write failed")
        )

        # Run
        result = await trinity.upsert_findings_batch(findings)

        # Verify: DuckDB succeeded, LanceDB failed, but no exception propagates
        assert result.duckdb.success is True
        assert result.lance is not None
        assert result.lance.success is False
        assert "LanceDB write failed" in str(result.lance.error)


class TestTrinityGhostEntityPrevention:
    """Ghost entities: DuckDB record exists without LanceDB embedding."""

    @pytest.mark.asyncio
    async def test_no_ghost_without_lance(self, mock_duckdb_store):
        """When SemanticStore not injected, DuckDB must still succeed."""
        trinity = StorageTrinity(duckdb_store=mock_duckdb_store)
        # NOTE: SemanticStore NOT injected

        findings = [MagicMock(payload_text="test")]
        mock_duckdb_store.async_ingest_findings_batch = AsyncMock(return_value=[
            MagicMock(accepted=True)
        ])

        # Run
        result = await trinity.upsert_findings_batch(findings)

        # Verify: DuckDB succeeds even without LanceDB
        assert result.duckdb.success is True
        assert result.lance is not None
        assert result.lance.error == "semantic_store_not_injected"

    @pytest.mark.asyncio
    async def test_lance_fail_schedules_rebuild(self, mock_duckdb_store, mock_semantic_store):
        """LanceDB failure must schedule entity for rebuild."""
        trinity = StorageTrinity(duckdb_store=mock_duckdb_store)
        trinity.inject_semantic_store(mock_semantic_store)

        findings = [MagicMock(payload_text="test", finding_id="entity-123")]
        mock_duckdb_store.async_ingest_findings_batch = AsyncMock(return_value=[
            MagicMock(accepted=True)
        ])
        mock_semantic_store.add_text = MagicMock(
            side_effect=RuntimeError("LanceDB error")
        )

        # Run
        await trinity.upsert_findings_batch(findings)

        # Verify: Entity scheduled for rebuild
        assert trinity.rebuild_pending_count == 1


class TestTrinityBoundedQueue:
    """M1 8GB: LanceDB queue must be bounded."""

    def test_max_lance_queue_constant(self):
        """MAX_LANCE_QUEUE must be bounded for M1 8GB safety."""
        assert MAX_LANCE_QUEUE == 8192, (
            f"MAX_LANCE_QUEUE={MAX_LANCE_QUEUE} too high for M1 8GB"
        )

    def test_lance_flush_interval_constant(self):
        """LANCE_FLUSH_INTERVAL_S must be reasonable."""
        assert LANCE_FLUSH_INTERVAL_S == 5.0, (
            f"LANCE_FLUSH_INTERVAL_S={LANCE_FLUSH_INTERVAL_S} too high"
        )


class TestTrinityFailOpen:
    """LanceDB failures must be fail-open (never propagate)."""

    @pytest.mark.asyncio
    async def test_lance_exception_not_propagated(self, mock_duckdb_store, mock_semantic_store):
        """LanceDB exceptions must not reach caller."""
        trinity = StorageTrinity(duckdb_store=mock_duckdb_store)
        trinity.inject_semantic_store(mock_semantic_store)

        findings = [MagicMock(payload_text="test")]
        mock_duckdb_store.async_ingest_findings_batch = AsyncMock(return_value=[
            MagicMock(accepted=True)
        ])
        mock_semantic_store.add_text = MagicMock(
            side_effect=RuntimeError("LanceDB disaster")
        )

        # Run — must NOT raise
        result = await trinity.upsert_findings_batch(findings)

        # Verify: Returns result, doesn't raise
        assert isinstance(result, TrinityWriteResult)
        assert result.duckdb.success is True


class TestTrinityDataClasses:
    """TrinityPhaseResult and TrinityWriteResult invariants."""

    def test_trinity_phase_result_defaults(self):
        """TrinityPhaseResult must have correct defaults."""
        result = TrinityPhaseResult(phase="duckdb", success=True)

        assert result.phase == "duckdb"
        assert result.success is True
        assert result.records == 0
        assert result.error is None
        assert result.duration_ms == 0.0

    def test_trinity_write_result_accepted_property(self):
        """TrinityWriteResult.accepted must reflect DuckDB success."""
        success_result = TrinityWriteResult(
            duckdb=TrinityPhaseResult(phase="duckdb", success=True)
        )
        assert success_result.accepted is True

        failure_result = TrinityWriteResult(
            duckdb=TrinityPhaseResult(phase="duckdb", success=False)
        )
        assert failure_result.accepted is False


class TestTrinityExtractors:
    """Payload extraction helpers must handle various finding shapes."""

    def test_extract_payload_text(self, trinity):
        """_extract_payload_text must join multiple findings."""
        f1 = MagicMock(payload_text="hello")
        f2 = MagicMock(payload_text="world")

        result = trinity._extract_payload_text([f1, f2])

        assert result == "hello\nworld"

    def test_extract_payload_text_empty(self, trinity):
        """_extract_payload_text must handle empty payload."""
        f1 = MagicMock(payload_text="")

        result = trinity._extract_payload_text([f1])

        assert result == ""

    def test_extract_ioc_types(self, trinity):
        """_extract_ioc_types must extract from pattern_matches."""
        f = MagicMock()
        f.pattern_matches = [
            ("ip", "192.168.1.1"),
            {"label": "domain", "value": "evil.com"},
        ]

        result = trinity._extract_ioc_types([f])

        assert set(result) == {"ip", "domain"}

    def test_extract_source_type(self, trinity):
        """_extract_source_type must return first non-empty."""
        f1 = MagicMock(source_type=None)
        f2 = MagicMock(source_type="osint")

        result = trinity._extract_source_type([f1, f2])

        assert result == "osint"

    def test_extract_ts(self, trinity):
        """_extract_ts must handle float timestamps."""
        f = MagicMock(ts=1234567890.5)

        result = trinity._extract_ts([f])

        assert result == 1234567890.5


class TestTrinityLifecycle:
    """StorageTrinity lifecycle management."""

    @pytest.mark.asyncio
    async def test_close_flushes_lance(self, mock_duckdb_store, mock_semantic_store):
        """close() must flush pending LanceDB writes."""
        trinity = StorageTrinity(duckdb_store=mock_duckdb_store)
        trinity.inject_semantic_store(mock_semantic_store)

        # Buffer something
        findings = [MagicMock(payload_text="test")]
        mock_duckdb_store.async_ingest_findings_batch = AsyncMock(return_value=[
            MagicMock(accepted=True)
        ])
        await trinity.upsert_findings_batch(findings)

        # Close
        await trinity.close()

        # Verify: SemanticStore flushed
        mock_semantic_store.flush.assert_called()

    @pytest.mark.asyncio
    async def test_double_close_idempotent(self, mock_duckdb_store, mock_semantic_store):
        """close() must be idempotent."""
        trinity = StorageTrinity(duckdb_store=mock_duckdb_store)
        trinity.inject_semantic_store(mock_semantic_store)

        # Close twice
        await trinity.close()
        await trinity.close()  # Must not raise

    def test_repr_includes_pending(self, trinity, mock_semantic_store):
        """repr must show pending counts."""
        trinity.inject_semantic_store(mock_semantic_store)

        r = repr(trinity)

        assert "StorageTrinity" in r
        assert "duckdb" in r.lower()


# ---------------------------------------------------------------------------
# TestTrinitySprint8 (integration with sprint lifecycle)
# ---------------------------------------------------------------------------


class TestTrinitySprint8:
    """Sprint 8W integration: StorageTrinity must wire into existing paths."""

    @pytest.mark.asyncio
    async def test_inject_semantic_store(self, trinity, mock_semantic_store):
        """inject_semantic_store must wire SemanticStore into Trinity."""
        trinity.inject_semantic_store(mock_semantic_store)

        assert trinity._semantic_store is mock_semantic_store

    @pytest.mark.asyncio
    async def test_lance_flush_scheduled_on_write(self, mock_duckdb_store, mock_semantic_store):
        """LanceDB flush must be scheduled after write."""
        trinity = StorageTrinity(duckdb_store=mock_duckdb_store)
        trinity.inject_semantic_store(mock_semantic_store)

        findings = [MagicMock(payload_text="test")]
        mock_duckdb_store.async_ingest_findings_batch = AsyncMock(return_value=[
            MagicMock(accepted=True)
        ])

        await trinity.upsert_findings_batch(findings)

        # Flush task scheduled
        assert trinity._lance_flush_task is not None
        # But not yet executed
        mock_semantic_store.flush.assert_not_called()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestTrinityEdgeCases:
    """Edge case handling."""

    @pytest.mark.asyncio
    async def test_empty_findings_list(self, trinity):
        """Empty findings list must return failure result, not raise."""
        result = await trinity.upsert_findings_batch([])

        assert result.duckdb.success is False
        assert result.duckdb.records == 0

    @pytest.mark.asyncio
    async def test_none_finding_id(self, trinity, mock_duckdb_store, mock_semantic_store):
        """None finding_id must not crash rebuild tracking."""
        trinity.inject_semantic_store(mock_semantic_store)

        findings = [MagicMock(payload_text="test", finding_id=None)]
        mock_duckdb_store.async_ingest_findings_batch = AsyncMock(return_value=[
            MagicMock(accepted=True)
        ])
        mock_semantic_store.add_text = MagicMock(
            side_effect=RuntimeError("fail")
        )

        # Must not raise
        await trinity.upsert_findings_batch(findings)

        # Rebuild count unchanged (None not added)
        assert trinity.rebuild_pending_count == 0

    def test_trinity_phase_error_repr(self):
        """TrinityPhaseError must have useful repr."""
        err = TrinityPhaseError("duckdb", "Write failed", records=5)

        assert "[TRINITY:duckdb]" in repr(err)
        assert "Write failed" in repr(err)
        assert err.phase == "duckdb"
        assert err.records == 5
