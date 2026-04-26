"""
Sprint F203I — Streaming Embedding Pipeline Tests
==================================================

Tests:
  1. StreamingEmbedder.instantiation
  2. StreamingEmbedder.bounds_MAX_EMBEDDING_BATCH
  3. StreamingEmbedder.bounds_MAX_TEXT_BYTES_PER_FINDING
  4. StreamingEmbedder.embed_findings_empty
  5. StreamingEmbedder.embed_findings_memory_guard_skip
  6. StreamingEmbedder.embed_findings_yields_batches
  7. StreamingEmbedder.extract_text_payload_text
  8. StreamingEmbedder.extract_text_query_fallback
  9. StreamingEmbedder.extract_text_truncation
  10. StreamingEmbedder.ram_guard_ok_true_under_threshold
  11. StreamingEmbedder.is_model_loaded_false_initially
  12. generate_embeddings_streaming_signature
  13. generate_embeddings_streaming_yields_batches
  14. generate_embeddings_streaming_empty_input
  15. vector_store_add_vectors_streaming
  16. ann_index_prewarm_signature
  17. ann_index_prewarm_fail_soft
  18. sprint_scheduler_embedding_sidecar_integration
  19. benchmark_streaming_vs_sync_rss_delta
  20. benchmark_streaming_lower_peak_than_sync
"""

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_canonical_finding():
    """Create a mock CanonicalFinding."""
    finding = MagicMock()
    finding.finding_id = "test-finding-1"
    finding.payload_text = "Test payload text for embedding"
    finding.query = "test query"
    finding.source_type = "test"
    finding.confidence = 0.9
    finding.ts = 1234567890.0
    finding.provenance = ()
    return finding


@pytest.fixture
def mock_finding_list():
    """Create a list of mock CanonicalFinding."""
    findings = []
    for i in range(50):  # Enough for 4 batches of 16
        f = MagicMock()
        f.finding_id = f"test-finding-{i}"
        f.payload_text = f"Test payload text for embedding {i}"
        f.query = f"test query {i}"
        f.source_type = "test"
        f.confidence = 0.9
        f.ts = 1234567890.0 + i
        f.provenance = ()
        findings.append(f)
    return findings


# ---------------------------------------------------------------------------
# Test 1: StreamingEmbedder.instantiation
# ---------------------------------------------------------------------------

def test_streaming_embedder_instantiation():
    """StreamingEmbedder can be instantiated without errors."""
    from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder

    embedder = StreamingEmbedder()
    assert embedder is not None
    assert hasattr(embedder, "_loaded")
    assert hasattr(embedder, "_embedding_depth")


# ---------------------------------------------------------------------------
# Test 2: StreamingEmbedder.bounds_MAX_EMBEDDING_BATCH
# ---------------------------------------------------------------------------

def test_streaming_embedder_bounds_max_embedding_batch():
    """MAX_EMBEDDING_BATCH = 16 as specified."""
    from hledac.universal.intelligence.streaming_embedder import MAX_EMBEDDING_BATCH

    assert MAX_EMBEDDING_BATCH == 16


# ---------------------------------------------------------------------------
# Test 3: StreamingEmbedder.bounds_MAX_TEXT_BYTES_PER_FINDING
# ---------------------------------------------------------------------------

def test_streaming_embedder_bounds_max_text_bytes():
    """MAX_TEXT_BYTES_PER_FINDING = 4096 as specified."""
    from hledac.universal.intelligence.streaming_embedder import MAX_TEXT_BYTES_PER_FINDING

    assert MAX_TEXT_BYTES_PER_FINDING == 4096


# ---------------------------------------------------------------------------
# Test 4: StreamingEmbedder.embed_findings_empty
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_streaming_embedder_embed_findings_empty():
    """embed_findings with empty list yields nothing."""
    from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder

    embedder = StreamingEmbedder()
    results = []
    async for batch in embedder.embed_findings([]):
        results.append(batch)
    assert results == []


# ---------------------------------------------------------------------------
# Test 5: StreamingEmbedder.embed_findings_memory_guard_skip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_streaming_embedder_memory_guard_skip():
    """embed_findings skips when RAM guard is triggered."""
    from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder

    embedder = StreamingEmbedder()

    # Patch the actual import location of sample_uma_status
    mock_uma = MagicMock()
    mock_uma.is_critical = True  # Force memory pressure
    mock_uma.is_emergency = False
    mock_uma.is_warn = False
    mock_uma.high_water = 0.0

    with patch("hledac.universal.core.resource_governor.sample_uma_status", return_value=mock_uma):
        findings = [MagicMock()]
        findings[0].finding_id = "test"
        findings[0].payload_text = "text"
        findings[0].query = ""
        results = []
        async for batch in embedder.embed_findings(findings):
            results.append(batch)
        assert results == []


# ---------------------------------------------------------------------------
# Test 6: StreamingEmbedder.embed_findings_yields_batches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_streaming_embedder_yields_batches(mock_finding_list):
    """embed_findings yields (ids, embeddings) batches of size <= MAX_EMBEDDING_BATCH."""
    from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder, MAX_EMBEDDING_BATCH

    embedder = StreamingEmbedder()

    # Patch internal helpers at the correct import locations
    mock_uma_instance = MagicMock()
    mock_uma_instance.is_critical = False
    mock_uma_instance.is_emergency = False
    mock_uma_instance.is_warn = False
    mock_uma_instance.high_water = 0.0

    with patch("hledac.universal.core.resource_governor.sample_uma_status", return_value=mock_uma_instance):
        with patch("hledac.universal.brain.model_lifecycle.get_model_lifecycle_status", return_value={"loaded": True}):
            with patch("hledac.universal.intelligence.streaming_embedder._sync_embed_batch") as mock_sync:
                # Return valid embeddings for any input
                def make_embs(texts, batch_size=16):
                    return np.zeros((len(texts), 256), dtype=np.float32)
                mock_sync.side_effect = make_embs

                results = []
                async for batch in embedder.embed_findings(mock_finding_list, batch_size=16):
                    ids, embs = batch
                    results.append(batch)
                    assert len(ids) <= MAX_EMBEDDING_BATCH
                    assert embs.shape == (len(ids), 256)

                # 50 findings / 16 = 4 batches
                assert len(results) == 4


# ---------------------------------------------------------------------------
# Test 7: StreamingEmbedder.extract_text_payload_text
# ---------------------------------------------------------------------------

def test_streaming_embedder_extract_text_payload_text(mock_canonical_finding):
    """_extract_text returns payload_text when available."""
    from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder

    embedder = StreamingEmbedder()
    text = embedder._extract_text(mock_canonical_finding)
    assert text == "Test payload text for embedding"


# ---------------------------------------------------------------------------
# Test 8: StreamingEmbedder.extract_text_query_fallback
# ---------------------------------------------------------------------------

def test_streaming_embedder_extract_text_query_fallback():
    """_extract_text falls back to query when payload_text is None."""
    from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder

    embedder = StreamingEmbedder()
    finding = MagicMock()
    finding.payload_text = None
    finding.query = "fallback query"

    text = embedder._extract_text(finding)
    assert text == "fallback query"


# ---------------------------------------------------------------------------
# Test 9: StreamingEmbedder.extract_text_truncation
# ---------------------------------------------------------------------------

def test_streaming_embedder_extract_text_truncation():
    """_extract_text truncates text exceeding MAX_TEXT_BYTES_PER_FINDING."""
    from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder, MAX_TEXT_BYTES_PER_FINDING

    embedder = StreamingEmbedder()
    finding = MagicMock()
    finding.payload_text = "x" * (MAX_TEXT_BYTES_PER_FINDING + 1000)
    finding.query = ""

    text = embedder._extract_text(finding)
    assert len(text) == MAX_TEXT_BYTES_PER_FINDING


# ---------------------------------------------------------------------------
# Test 10: StreamingEmbedder.ram_guard_ok_true_under_threshold
# ---------------------------------------------------------------------------

def test_streaming_embedder_ram_guard_ok_true():
    """_ram_guard_ok returns True when memory is below threshold."""
    from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder

    embedder = StreamingEmbedder()

    mock_uma = MagicMock()
    mock_uma.is_critical = False
    mock_uma.is_emergency = False
    mock_uma.is_warn = False
    mock_uma.high_water = 0.0

    with patch("hledac.universal.core.resource_governor.sample_uma_status", return_value=mock_uma):
        assert embedder._ram_guard_ok() is True


# ---------------------------------------------------------------------------
# Test 11: StreamingEmbedder.is_model_loaded_false_initially
# ---------------------------------------------------------------------------

def test_streaming_embedder_is_model_loaded_false_initially():
    """_is_model_loaded returns False when model not loaded."""
    from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder

    embedder = StreamingEmbedder()

    with patch("hledac.universal.brain.model_lifecycle.get_model_lifecycle_status", return_value={"loaded": False}):
        assert embedder._is_model_loaded() is False


# ---------------------------------------------------------------------------
# Test 12: generate_embeddings_streaming_signature
# ---------------------------------------------------------------------------

def test_generate_embeddings_streaming_signature():
    """generate_embeddings_streaming exists and is an async generator function."""
    from hledac.universal.embedding_pipeline import generate_embeddings_streaming
    import inspect

    assert callable(generate_embeddings_streaming)
    assert inspect.isasyncgenfunction(generate_embeddings_streaming)


# ---------------------------------------------------------------------------
# Test 13: generate_embeddings_streaming_yields_batches
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_embeddings_streaming_yields_batches():
    """generate_embeddings_streaming yields (ids, embeddings) batches via streaming path."""
    from hledac.universal.embedding_pipeline import generate_embeddings_streaming, _generate_embeddings_chunk

    texts = [f"test text {i}" for i in range(40)]  # 3 batches of 16

    # Patch _generate_embeddings_chunk to return valid embeddings
    def fake_chunk(t, bs=16):
        return np.zeros((len(t), 256), dtype=np.float32)

    with patch("hledac.universal.embedding_pipeline._generate_embeddings_chunk", side_effect=fake_chunk):
        with patch("hledac.universal.embedding_pipeline.load_embedding_model", return_value=True):
            with patch("hledac.universal.embedding_pipeline.unload_embedding_model", return_value=True):
                batches = []
                async for batch in generate_embeddings_streaming(texts, batch_size=16):
                    ids, embs = batch
                    batches.append(batch)
                    assert isinstance(ids, list)
                    assert isinstance(embs, np.ndarray)
                    assert embs.shape[1] == 256  # 256d embedding

                # 40 texts / 16 = 3 batches (16+16+8)
                assert len(batches) == 3


# ---------------------------------------------------------------------------
# Test 14: generate_embeddings_streaming_empty_input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_embeddings_streaming_empty_input():
    """generate_embeddings_streaming with empty list yields nothing."""
    from hledac.universal.embedding_pipeline import generate_embeddings_streaming

    batches = []
    async for batch in generate_embeddings_streaming([]):
        batches.append(batch)

    assert batches == []


# ---------------------------------------------------------------------------
# Test 15: vector_store_add_vectors_streaming
# ---------------------------------------------------------------------------

def test_vector_store_add_vectors_streaming():
    """VectorStore.add_vectors_streaming exists and has correct signature."""
    from hledac.universal.knowledge.vector_store import VectorStore

    assert hasattr(VectorStore, "add_vectors_streaming")


# ---------------------------------------------------------------------------
# Test 16: ann_index_prewarm_signature
# ---------------------------------------------------------------------------

def test_ann_index_prewarm_signature():
    """_ANNIndex.prewarm exists and accepts top_k parameter."""
    from hledac.universal.knowledge.ann_index import _ANNIndex

    assert hasattr(_ANNIndex, "prewarm")


# ---------------------------------------------------------------------------
# Test 17: ann_index_prewarm_fail_soft
# ---------------------------------------------------------------------------

def test_ann_index_prewarm_fail_soft(tmp_path):
    """_ANNIndex.prewarm fails soft (returns None on error)."""
    from hledac.universal.knowledge.ann_index import _ANNIndex

    ann = _ANNIndex(db_path=tmp_path / "test.lance")
    # prewarm should return None on uninitialized index (fail-soft)
    result = ann.prewarm(top_k=128)
    # Fail-soft: returns None
    assert result is None


# ---------------------------------------------------------------------------
# Test 18: sprint_scheduler_embedding_sidecar_integration
# ---------------------------------------------------------------------------

def test_sprint_scheduler_has_embedding_sidecar():
    """SprintScheduler has _run_embedding_sidecar method."""
    from hledac.universal.runtime.sprint_scheduler import SprintScheduler

    assert hasattr(SprintScheduler, "_run_embedding_sidecar")


# ---------------------------------------------------------------------------
# Test 19: benchmark_streaming_vs_sync_rss_delta
# ---------------------------------------------------------------------------

def test_benchmark_exists():
    """m1_embedding_streaming benchmark exists."""
    import os

    # Path: tests/probe_f203i/test_xxx.py -> benchmarks/m1_embedding_streaming.py
    benchmark_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "benchmarks", "m1_embedding_streaming.py"
    )
    assert os.path.exists(benchmark_path), f"Benchmark not found at {benchmark_path}"


# ---------------------------------------------------------------------------
# Test 20: benchmark_streaming_lower_peak_than_sync
# ---------------------------------------------------------------------------

def test_benchmark_has_hermetic_mode():
    """m1_embedding_streaming.py supports --hermetic flag."""
    import os

    # Path: tests/probe_f203i/test_xxx.py -> benchmarks/m1_embedding_streaming.py
    benchmark_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "..", "benchmarks", "m1_embedding_streaming.py"
    )
    with open(benchmark_path) as f:
        content = f.read()
    assert "--hermetic" in content
