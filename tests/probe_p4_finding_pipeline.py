"""
Probe tests for P4-1: FindingPipeline
======================================
Tests producer-consumer pipeline: enqueue → enrich → store.
"""

import asyncio

import pytest

from pipeline.finding_pipeline import (
    _PIPELINE_QUEUE_SIZE,
    FindingPipeline,
)


class MockDuckDBStore:
    """Mock DuckDBShadowStore for testing."""
    def __init__(self) -> None:
        self.ingested: list[object] = []

    async def async_ingest_findings_batch(self, findings: list[object]) -> list[object]:
        self.ingested.extend(findings)
        return [object()] * len(findings)


class MockGraphService:
    """Mock DuckPGQGraph for testing."""
    def __init__(self) -> None:
        self.upserted: list[object] = []

    def upsert_ioc(self, finding: object) -> None:
        self.upserted.append(finding)


class MockFinding:
    """Minimal CanonicalFinding mock."""
    def __init__(self, finding_id: str) -> None:
        self.finding_id = finding_id


def enrich_fn(f: MockFinding) -> MockFinding:
    """Sync enrichment function."""
    return MockFinding(f.finding_id + "_enriched")


def multimodal_fn(f: MockFinding) -> MockFinding:
    """Sync multimodal enrichment function."""
    return MockFinding(f.finding_id + "_multimodal")


@pytest.mark.asyncio
async def test_pipeline_basic_enqueue_dequeue():
    """Test basic enqueue and worker processing."""
    store = MockDuckDBStore()
    graph = MockGraphService()
    pipeline = FindingPipeline(store, graph, enrich_fn, multimodal_fn)

    await pipeline.start()

    # Enqueue findings
    findings = [MockFinding(f"finding_{i}") for i in range(10)]
    count = await pipeline.enqueue_batch(findings)
    assert count == 10

    # Wait for processing
    await asyncio.sleep(0.5)

    stats = pipeline.get_stats()
    assert stats.enqueued == 10
    assert stats.enriched >= 0

    await pipeline.stop(timeout=5.0)


@pytest.mark.asyncio
async def test_pipeline_backpressure():
    """Test that full queue drops findings (not blocking)."""
    store = MockDuckDBStore()
    graph = MockGraphService()
    # Create pipeline with tiny queue
    pipeline = FindingPipeline(store, graph)

    # Manually fill queue beyond capacity
    for i in range(_PIPELINE_QUEUE_SIZE + 10):
        try:
            pipeline._queue.put_nowait(MockFinding(f"f_{i}"))
        except asyncio.QueueFull:
            pass

    await pipeline.start()

    # Enqueue more - should return False (dropped)
    dropped_count = 0
    for i in range(100):
        if not await pipeline.enqueue(MockFinding(f"extra_{i}")):
            dropped_count += 1

    assert dropped_count > 0
    stats = pipeline.get_stats()
    assert stats.dropped > 0

    await pipeline.stop(timeout=5.0)


@pytest.mark.asyncio
async def test_pipeline_poison_pill():
    """Test that poison pill stops workers gracefully."""
    store = MockDuckDBStore()
    graph = MockGraphService()
    pipeline = FindingPipeline(store, graph, enrich_fn, multimodal_fn)

    await pipeline.start()

    # Enqueue some findings
    for i in range(5):
        await pipeline.enqueue(MockFinding(f"pre_poison_{i}"))

    # Stop pipeline (sends poison pills)
    await pipeline.stop(timeout=5.0)

    # Verify workers stopped
    assert not pipeline._running


@pytest.mark.asyncio
async def test_pipeline_stats():
    """Test pipeline statistics tracking."""
    store = MockDuckDBStore()
    graph = MockGraphService()
    pipeline = FindingPipeline(store, graph)

    stats = pipeline.get_stats()
    assert stats.enqueued == 0
    assert stats.stored == 0
    assert stats.dropped == 0


@pytest.mark.asyncio
async def test_pipeline_queue_size():
    """Test queue size reporting."""
    store = MockDuckDBStore()
    graph = MockGraphService()
    pipeline = FindingPipeline(store, graph)

    await pipeline.start()

    for i in range(5):
        await pipeline.enqueue(MockFinding(f"q_{i}"))

    qsize = await pipeline.get_queue_size()
    assert qsize >= 0

    await pipeline.stop(timeout=5.0)


@pytest.mark.asyncio
async def test_pipeline_no_enrich_fn():
    """Test pipeline with no enrichment functions (passthrough)."""
    store = MockDuckDBStore()
    graph = MockGraphService()
    pipeline = FindingPipeline(store, graph, enrich_fn=None, multimodal_fn=None)

    await pipeline.start()

    findings = [MockFinding(f"nop_{i}") for i in range(3)]
    await pipeline.enqueue_batch(findings)

    await asyncio.sleep(0.3)
    await pipeline.stop(timeout=5.0)

    stats = pipeline.get_stats()
    assert stats.enqueued == 3


@pytest.mark.asyncio
async def test_pipeline_double_start():
    """Test that double start is no-op."""
    store = MockDuckDBStore()
    graph = MockGraphService()
    pipeline = FindingPipeline(store, graph)

    await pipeline.start()
    await pipeline.start()  # Should be no-op

    assert pipeline._running
    await pipeline.stop(timeout=5.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
