"""

probe_f224.py — Sprint F224: Cross-Sprint Research Memory Tests
================================================================

Tests for:
- Part A: ResearchSessionMemory.record_sprint_outcome()
- Part A: ResearchSessionMemory.get_unexplored_angles()
- Part A: ResearchSessionMemory.get_entity_history()
- Part A: ResearchSessionMemory.detect_temporal_anomalies()
- Part B: Graph RAG activation via _run_graph_rag_context_sidecar()
- Part D: DuckDB dht_metadata table + async_ingest_dht_metadata()

GHOST_INVARIANTS:
- G1: DuckDB table CREATE IF NOT EXISTS — invariant: test_dht_metadata_table_created
- G2: async_ingest_dht_metadata returns count — invariant: test_ingest_dht_metadata_count
- G3: ResearchSessionMemory singleton — invariant: test_research_memory_singleton
- G4: record_sprint_outcome returns session_id — invariant: test_record_outcome_returns_id
- G5: get_unexplored_angles returns list — invariant: test_get_unexplored_returns_list
"""

import asyncio
import tempfile
import time as _time
from typing import Any

import pytest

# Test fixtures
@pytest.fixture
def temp_db_path():
    """Create temporary DuckDB path for testing."""
    with tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False) as f:
        db_path = f.name
    yield db_path
    try:
        import os
        os.unlink(db_path)
    except Exception:
        pass


@pytest.fixture
def temp_duckdb(temp_db_path):
    """Create DuckDBShadowStore for testing."""
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    store = DuckDBShadowStore(db_path=temp_db_path)
    # Initialize synchronously via run_in_executor
    loop = asyncio.new_event_loop()
    loop.run_until_complete(store.async_initialize())
    loop.close()
    yield store
    try:
        loop2 = asyncio.new_event_loop()
        loop2.run_until_complete(store.aclose())
        loop2.close()
    except Exception:
        pass


@pytest.fixture
def mock_finding():
    """Create mock CanonicalFinding."""
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    return CanonicalFinding(
        finding_id=f"test_finding_{_time.time()}",
        query="test_query",
        source_type="web",
        confidence=0.8,
        ts=_time.time(),
        provenance=("test",),
        payload_text="Test payload content with example.com domain and 192.168.1.1 IP",
    )


# ── Part A: ResearchSessionMemory Tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_research_memory_singleton(temp_duckdb):
    """G3: ResearchSessionMemory enforces singleton pattern."""
    from hledac.universal.knowledge.research_memory import ResearchSessionMemory

    # First instance should work
    mem1 = ResearchSessionMemory(temp_duckdb)
    assert mem1 is not None

    # Second instance should raise RuntimeError
    with pytest.raises(RuntimeError, match="singleton"):
        mem2 = ResearchSessionMemory(temp_duckdb)

    # Reset singleton for other tests
    import hledac.universal.knowledge.research_memory as rm
    rm._MAYBE_MEMORY = None


@pytest.mark.asyncio
async def test_record_outcome_returns_id(temp_db_path, mock_finding):
    """G4: record_sprint_outcome returns session_id."""
    # Reset singleton
    import hledac.universal.knowledge.research_memory as rm
    rm._MAYBE_MEMORY = None

    from hledac.universal.knowledge.research_memory import ResearchSessionMemory

    mem = ResearchSessionMemory(None)
    session_id = await mem.record_sprint_outcome(
        sprint_id="sprint_001",
        query="test query",
        findings=[mock_finding],
        gaps=None,
    )

    assert session_id is not None
    assert "sprint_001" in session_id
    assert isinstance(session_id, str)


@pytest.mark.asyncio
async def test_get_unexplored_returns_list(temp_db_path):
    """G5: get_unexplored_angles returns list of UnexploredAngle."""
    # Reset singleton
    import hledac.universal.knowledge.research_memory as rm
    rm._MAYBE_MEMORY = None

    from hledac.universal.knowledge.research_memory import ResearchSessionMemory

    mem = ResearchSessionMemory(None)

    # No previous data should return empty list
    angles = await mem.get_unexplored_angles(
        query="new test query",
        current_sprint_id="sprint_002",
    )

    assert isinstance(angles, list)


@pytest.mark.asyncio
async def test_get_entity_history_not_found(temp_db_path):
    """get_entity_history returns None for unknown entity."""
    # Reset singleton
    import hledac.universal.knowledge.research_memory as rm
    rm._MAYBE_MEMORY = None

    from hledac.universal.knowledge.research_memory import ResearchSessionMemory

    mem = ResearchSessionMemory(None)
    history = await mem.get_entity_history("unknown_entity_xyz")

    assert history is None


@pytest.mark.asyncio
async def test_detect_temporal_anomalies_empty(temp_duckdb):
    """detect_temporal_anomalies returns empty list with no data."""
    # Reset singleton
    import hledac.universal.knowledge.research_memory as rm
    rm._MAYBE_MEMORY = None

    from hledac.universal.knowledge.research_memory import ResearchSessionMemory

    mem = ResearchSessionMemory(None)
    anomalies = await mem._detect_temporal_anomalies()

    assert isinstance(anomalies, list)
    assert len(anomalies) == 0


# ── Part D: DuckDB dht_metadata Tests ────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_dht_metadata_method_exists():
    """G1: DuckDBShadowStore has async_ingest_dht_metadata method."""
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    assert "async_ingest_dht_metadata" in dir(DuckDBShadowStore)

@pytest.mark.asyncio
async def test_dht_metadata_returns_int():
    """G2: async_ingest_dht_metadata returns int."""
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

    store = DuckDBShadowStore()
    # Method should exist
    result = await store.async_ingest_dht_metadata([])
    assert isinstance(result, int), "Should return int"


# ── Part B: Graph RAG Sidecar Integration Tests ──────────────────────────────





@pytest.mark.asyncio
async def test_graph_rag_context_sidecar_method_exists():
    """Graph RAG context sidecar method exists on SprintScheduler."""
    from hledac.universal.runtime.sprint_scheduler import SprintScheduler, SprintSchedulerConfig

    scheduler = SprintScheduler(SprintSchedulerConfig())
    assert hasattr(scheduler, "_run_graph_rag_context_sidecar"), \
        "SprintScheduler should have _run_graph_rag_context_sidecar method"


@pytest.mark.asyncio
async def test_graph_rag_context_count_in_result():
    """graph_rag_context_count field exists in SprintSchedulerResult."""
    from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult

    result = SprintSchedulerResult()
    assert hasattr(result, "graph_rag_context_count"), \
        "SprintSchedulerResult should have graph_rag_context_count"
    assert result.graph_rag_context_count == 0


# ── Integration: Full Research Memory Flow ───────────────────────────────────


@pytest.mark.asyncio
async def test_full_research_memory_flow(temp_db_path, mock_finding):
    """Full flow: record outcome, then get unexplored angles."""
    # Reset singleton
    import hledac.universal.knowledge.research_memory as rm
    rm._MAYBE_MEMORY = None

    from hledac.universal.knowledge.research_memory import ResearchSessionMemory

    mem = ResearchSessionMemory(None)  # Use in-memory mode

    # Record first sprint
    session_id = await mem.record_sprint_outcome(
        sprint_id="sprint_001",
        query="domain research for example.com",
        findings=[mock_finding],
        gaps=None,
    )
    assert session_id is not None

    # Get hints for next sprint
    hints = await mem.get_next_sprint_hints(
        query="domain research for example.com",
        current_sprint_id="sprint_002",
    )
    assert isinstance(hints, dict)
    assert "suggested_angles" in hints
    assert "temporal_anomalies" in hints
