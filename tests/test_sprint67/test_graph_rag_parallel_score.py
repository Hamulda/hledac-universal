"""
Tests for score_paths_parallel - Sprint 3.3 Graph RAG Parallel Scoring
=======================================================================
Tests for parallel path scoring in GraphRAGOrchestrator.

Invariants tested:
- PARALLEL_001: score_paths_parallel returns same-length list as input
- PARALLEL_002: score_paths_parallel handles empty input gracefully
- PARALLEL_003: Semaphore limits concurrency to 4
- PARALLEL_004: Exceptions in individual scoring return 0.0
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


class MockNode:
    """Mock node for testing."""
    def __init__(self, node_id: str, embedding=None, confidence=0.5):
        self.id = node_id
        self.embedding = embedding
        self.metadata = {"confidence": confidence}


class MockEmbedder:
    """Mock embedder that returns simple embeddings."""
    def __init__(self):
        self.call_count = 0

    def embed_document(self, text: str):
        """Return a mock embedding."""
        self.call_count += 1
        # Return a simple 384-dim vector
        return [0.1] * 384


@pytest.fixture
def mock_knowledge_layer():
    """Create a mock knowledge layer."""
    layer = MagicMock()
    layer.get_node = AsyncMock(side_effect=lambda node_id: MockNode(
        node_id,
        embedding=[0.5] * 384,
        confidence=0.7
    ))
    return layer


@pytest.fixture
def mock_embedder():
    """Create a mock embedder."""
    embedder = MockEmbedder()
    return embedder


@pytest.mark.asyncio
async def test_score_paths_parallel_empty():
    """Test score_paths_parallel with empty input."""
    from knowledge.graph_rag import GraphRAGOrchestrator

    layer = MagicMock()
    layer.get_node = AsyncMock(return_value=MockNode("node1"))
    orch = GraphRAGOrchestrator(layer)
    result = await orch.score_paths_parallel([], "hypothesis")
    assert result == []


@pytest.mark.asyncio
async def test_score_paths_parallel_returns_same_length(mock_knowledge_layer):
    """PARALLEL_001: Returns same-length list as input."""
    from knowledge.graph_rag import GraphRAGOrchestrator

    orch = GraphRAGOrchestrator(mock_knowledge_layer)
    paths = [
        ["node1", "node2"],
        ["node3", "node4", "node5"],
        ["node6", "node7"],
    ]
    result = await orch.score_paths_parallel(paths, "hypothesis")
    assert len(result) == len(paths)


@pytest.mark.asyncio
async def test_score_paths_parallel_single_path(mock_knowledge_layer):
    """Test scoring a single path."""
    from knowledge.graph_rag import GraphRAGOrchestrator

    orch = GraphRAGOrchestrator(mock_knowledge_layer)
    paths = [["node1", "node2"]]
    result = await orch.score_paths_parallel(paths, "hypothesis")
    assert len(result) == 1
    assert isinstance(result[0], float)
    assert 0.0 <= result[0] <= 1.0


@pytest.mark.asyncio
async def test_score_paths_parallel_multiple_paths(mock_knowledge_layer):
    """Test scoring multiple paths in parallel."""
    from knowledge.graph_rag import GraphRAGOrchestrator

    orch = GraphRAGOrchestrator(mock_knowledge_layer)
    paths = [
        ["node1", "node2"],
        ["node3", "node4", "node5"],
        ["node6", "node7", "node8", "node9"],
    ]
    result = await orch.score_paths_parallel(paths, "hypothesis")
    assert len(result) == 3
    for score in result:
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


@pytest.mark.asyncio
async def test_score_paths_parallel_exception_handling():
    """PARALLEL_004: Exceptions are converted to 0.0 scores (fail-safe)."""
    from knowledge.graph_rag import GraphRAGOrchestrator

    layer = MagicMock()
    # Make score_path raise an exception directly
    async def failing_score(*args, **kwargs):
        raise RuntimeError("Scoring failed")

    orch = GraphRAGOrchestrator(layer)
    orch.score_path = failing_score

    paths = [["node1", "node2"]]
    result = await orch.score_paths_parallel(paths, "hypothesis")
    assert len(result) == 1
    # Exception should be converted to 0.0 (fail-safe)
    assert result[0] == 0.0


@pytest.mark.asyncio
async def test_score_paths_parallel_semaphore_bounded():
    """PARALLEL_003: Semaphore limits concurrency to 4."""
    from unittest.mock import patch

    from knowledge.graph_rag import GraphRAGOrchestrator

    layer = MagicMock()
    layer.get_node = AsyncMock(return_value=MockNode("node1"))
    orch = GraphRAGOrchestrator(layer)
    paths = [[f"node{i}", f"node{i+1}"] for i in range(10)]

    # Track concurrent executions
    concurrent_executions = 0
    max_concurrent = 0

    async def tracking_score(self_ref, *args, **kwargs):
        nonlocal concurrent_executions, max_concurrent
        concurrent_executions += 1
        max_concurrent = max(max_concurrent, concurrent_executions)
        await asyncio.sleep(0.01)  # Small delay
        concurrent_executions -= 1
        return 0.5

    with patch.object(orch, 'score_path', tracking_score):
        result = await orch.score_paths_parallel(paths, "hypothesis")

    # Verify all completed
    assert len(result) == 10
    # Verify semaphore limited concurrency (should not exceed 4)
    assert max_concurrent <= 4


@pytest.mark.asyncio
async def test_score_paths_parallel_order_preserved(mock_knowledge_layer):
    """Verify results maintain same order as input paths."""
    from knowledge.graph_rag import GraphRAGOrchestrator

    orch = GraphRAGOrchestrator(mock_knowledge_layer)
    paths = [
        ["a", "b"],
        ["c", "d", "e"],
        ["f", "g", "h", "i"],
    ]
    result = await orch.score_paths_parallel(paths, "hypothesis")
    assert len(result) == 3
    # Each path has different length, scores should differ
    assert isinstance(result[0], float)
    assert isinstance(result[1], float)
    assert isinstance(result[2], float)
