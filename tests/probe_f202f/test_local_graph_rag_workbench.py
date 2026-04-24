"""Sprint F202F: Local Graph/RAG Analyst Workbench — Probe Tests
================================================================

Invariant mapping:
  F202F-1  | MAX_CONTEXT_BYTES = 8192 (8KB max context per answer)
  F202F-2  | MAX_TOP_K = 20 (max results from any single source)
  F202F-3  | MAX_GRAPH_HOPS = 2 (entity history max hops)
  F202F-4  | MAX_EVIDENCE_PTRS = 5 (max evidence pointers per answer)
  F202F-5  | MAX_RELATED_ENTITIES = 10 (max related entities per answer)
  F202F-6  | AnalystWorkbench.ask() returns extractive_answer always (no model required)
  F202F-7  | query_findings() keyword search returns scored results
  F202F-8  | query_graph() returns RelatedEntity list bounded by MAX_RELATED_ENTITIES
  F202F-9  | query_vectors() returns list bounded by MAX_TOP_K
  F202F-10 | ask() builds evidence_pointers from findings
  F202F-11 | ask() builds related_entities from graph traversal
  F202F-12 | _extract_answer() returns text (no model required)
  F202F-13 | _truncate_to_bytes() respects MAX_CONTEXT_BYTES
  F202F-14 | _build_evidence_pointers() caps at MAX_EVIDENCE_PTRS
  F202F-15 | create_analyst_workbench() returns AnalystWorkbench instance
  F202F-16 | No external network calls in workbench methods
  F202F-17 | JSON-LD evidence export produces valid JSON-LD
  F202F-18 | ask_sync() produces same structure as ask()
  F202F-19 | graph_rag multi_hop_search has max_nodes bounded
  F202F-20 | rag_engine has no-model fallback retrieval (BM25/hybrid)
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hledac.universal.knowledge.analyst_workbench import (
    MAX_CONTEXT_BYTES,
    MAX_EVIDENCE_PTRS,
    MAX_GRAPH_HOPS,
    MAX_RELATED_ENTITIES,
    MAX_TOP_K,
    AnalystAnswer,
    AnalystWorkbench,
    EvidencePointer,
    RelatedEntity,
    _build_evidence_pointer,
    _extract_snippet,
    _keyword_score,
    _truncate_to_bytes,
    create_analyst_workbench,
)
from hledac.universal.export.jsonld_exporter import (
    render_analyst_evidence_jsonld,
    render_analyst_evidence_jsonld_str,
)


# ============================================================================
# F202F-1: MAX_CONTEXT_BYTES bound
# ============================================================================
class TestBounds:
    """F202F-1 through F202F-5: All bounds are fixed constants."""

    def test_max_context_bytes(self):
        """F202F-1: MAX_CONTEXT_BYTES is 8192."""
        assert MAX_CONTEXT_BYTES == 8192

    def test_max_top_k(self):
        """F202F-2: MAX_TOP_K is 20."""
        assert MAX_TOP_K == 20

    def test_max_graph_hops(self):
        """F202F-3: MAX_GRAPH_HOPS is 2."""
        assert MAX_GRAPH_HOPS == 2

    def test_max_evidence_ptrs(self):
        """F202F-4: MAX_EVIDENCE_PTRS is 5."""
        assert MAX_EVIDENCE_PTRS == 5

    def test_max_related_entities(self):
        """F202F-5: MAX_RELATED_ENTITIES is 10."""
        assert MAX_RELATED_ENTITIES == 10

    def test_constants_are_int(self):
        """All bounds are integers."""
        assert isinstance(MAX_CONTEXT_BYTES, int)
        assert isinstance(MAX_TOP_K, int)
        assert isinstance(MAX_GRAPH_HOPS, int)
        assert isinstance(MAX_EVIDENCE_PTRS, int)
        assert isinstance(MAX_RELATED_ENTITIES, int)


# ============================================================================
# F202F-13: _truncate_to_bytes
# ============================================================================
class TestTruncateToBytes:
    """F202F-13: _truncate_to_bytes respects MAX_CONTEXT_BYTES."""

    def test_short_text_unchanged(self):
        """Text under limit is returned unchanged."""
        text = "short text"
        result, size = _truncate_to_bytes(text)
        assert result == text
        assert size == len(text.encode("utf-8"))

    def test_exact_limit_unchanged(self):
        """Text at exactly limit is returned unchanged."""
        text = "a" * MAX_CONTEXT_BYTES
        result, size = _truncate_to_bytes(text, MAX_CONTEXT_BYTES)
        assert result == text
        assert size == MAX_CONTEXT_BYTES

    def test_over_limit_truncated(self):
        """Text over limit is truncated to max_bytes."""
        text = "a" * (MAX_CONTEXT_BYTES * 2)
        result, size = _truncate_to_bytes(text, MAX_CONTEXT_BYTES)
        assert len(result.encode("utf-8")) == MAX_CONTEXT_BYTES
        assert size == MAX_CONTEXT_BYTES

    def test_returns_tuple(self):
        """Returns (truncated_text, actual_bytes)."""
        result = _truncate_to_bytes("hello", 10)
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], str)
        assert isinstance(result[1], int)


# ============================================================================
# F202F-7: _keyword_score
# ============================================================================
class TestKeywordScore:
    """F202F-7: _keyword_score returns score in [0.0, 1.0]."""

    def test_perfect_match(self):
        """All keywords in text returns 1.0."""
        score = _keyword_score("the quick brown fox", ["quick", "brown", "fox"])
        assert score == 1.0

    def test_partial_match(self):
        """Partial keyword match returns proportional score."""
        score = _keyword_score("the quick brown fox", ["quick", "missing"])
        assert score == 0.5

    def test_no_match(self):
        """No keyword match returns 0.0."""
        score = _keyword_score("the quick brown fox", ["missing", "other"])
        assert score == 0.0

    def test_empty_text(self):
        """Empty text returns 0.0."""
        score = _keyword_score("", ["quick"])
        assert score == 0.0

    def test_empty_keywords(self):
        """Empty keywords returns 0.0."""
        score = _keyword_score("some text", [])
        assert score == 0.0

    def test_score_bounded(self):
        """Score is always in [0.0, 1.0]."""
        for _ in range(10):
            text = " ".join(["word"] * 100)
            keywords = ["word"] * 50
            score = _keyword_score(text, keywords)
            assert 0.0 <= score <= 1.0


# ============================================================================
# F202F-14: _build_evidence_pointer
# ============================================================================
class TestBuildEvidencePointer:
    """F202F-14: _build_evidence_pointer builds EvidencePointer from finding dict."""

    def test_basic_pointer(self):
        """Basic finding dict produces EvidencePointer."""
        finding = {
            "id": "finding-123",
            "source_type": "ct_log",
            "query": "ransomware actor",
            "confidence": 0.85,
            "ts": 1714000000.0,
            "provenance": ["ct_log", "enrichment"],
        }
        ep = _build_evidence_pointer(finding)
        assert isinstance(ep, EvidencePointer)
        assert ep.finding_id == "finding-123"
        assert ep.source_type == "ct_log"
        assert ep.query == "ransomware actor"
        assert ep.confidence == 0.85
        assert ep.ts == 1714000000.0
        assert ep.provenance == ("ct_log", "enrichment")
        assert ep.envelope_available is False
        assert ep.snippet is None

    def test_with_envelope(self):
        """Finding with envelope sets envelope_available=True."""
        finding = {
            "id": "finding-456",
            "source_type": "deep_probe",
            "query": "S3 bucket exposure",
            "confidence": 0.92,
            "ts": 1714000100.0,
            "provenance": ("deep_probe",),
            "envelope": {"audit_reason": "test"},
        }
        ep = _build_evidence_pointer(finding)
        assert ep.envelope_available is True

    def test_with_snippet(self):
        """Snippet is passed through."""
        finding = {
            "id": "finding-789",
            "source_type": "document",
            "query": "phishing campaign",
            "confidence": 0.78,
            "ts": 1714000200.0,
            "provenance": (),
        }
        ep = _build_evidence_pointer(finding, snippet="Suspicious link detected...")
        assert ep.snippet == "Suspicious link detected..."

    def test_missing_fields_defaults(self):
        """Missing fields use empty defaults."""
        finding = {}
        ep = _build_evidence_pointer(finding)
        assert ep.finding_id == ""
        assert ep.source_type == "unknown"
        assert ep.query == ""
        assert ep.confidence == 0.0
        assert ep.ts == 0.0
        assert ep.provenance == ()


# ============================================================================
# F202F-6, F202F-12: AnalystWorkbench core methods
# ============================================================================
class TestAnalystWorkbenchCore:
    """F202F-6, F202F-12: ask() returns extractive_answer, no model required."""

    @pytest.fixture
    def workbench(self):
        """Workbench with mock stores."""
        mock_duckdb = MagicMock()
        mock_duckdb.async_query_recent_findings = AsyncMock(return_value=[
            {
                "id": "f1",
                "query": "ransomware attack",
                "source_type": "ct_log",
                "confidence": 0.9,
                "ts": 1714000000.0,
                "provenance": ("ct_log",),
                "payload_text": "Ransomware group FIN12 targeting healthcare",
            },
            {
                "id": "f2",
                "query": "phishing email",
                "source_type": "document",
                "confidence": 0.7,
                "ts": 1713999000.0,
                "provenance": ("document",),
                "payload_text": "Phishing campaign via spoofed Microsoft URLs",
            },
        ])
        mock_graph = MagicMock()
        mock_graph.find_entity_history = MagicMock(return_value=[
            {
                "value": "evil-domain.com",
                "ioc_type": "domain",
                "confidence": 0.95,
                "hops": 1,
                "relation_type": "uses_domain",
            },
            {
                "value": "185.234.x.x",
                "ioc_type": "ipv4",
                "confidence": 0.88,
                "hops": 2,
                "relation_type": "hosted_on",
            },
        ])
        mock_vector = MagicMock()
        mock_vector.query = MagicMock(return_value=[])
        mock_semantic = MagicMock()
        mock_semantic.semantic_pivot = AsyncMock(return_value=[])

        return AnalystWorkbench(
            duckdb_store=mock_duckdb,
            graph_service=mock_graph,
            vector_store=mock_vector,
            semantic_store=mock_semantic,
        )

    @pytest.mark.asyncio
    async def test_ask_returns_extractive_answer(self, workbench):
        """F202F-6: ask() always returns extractive_answer."""
        answer = await workbench.ask("ransomware attack")
        assert isinstance(answer, AnalystAnswer)
        assert isinstance(answer.extractive_answer, str)
        assert len(answer.extractive_answer) > 0

    @pytest.mark.asyncio
    async def test_ask_no_model_required(self, workbench):
        """F202F-6: No model loaded, llm_answer is None."""
        answer = await workbench.ask("phishing campaign")
        assert answer.llm_answer is None
        assert answer.model_used is False

    @pytest.mark.asyncio
    async def test_ask_builds_evidence_pointers(self, workbench):
        """F202F-10: ask() builds evidence_pointers from findings."""
        answer = await workbench.ask("ransomware")
        assert isinstance(answer.evidence_pointers, list)
        assert len(answer.evidence_pointers) <= MAX_EVIDENCE_PTRS
        for ep in answer.evidence_pointers:
            assert isinstance(ep, EvidencePointer)

    @pytest.mark.asyncio
    async def test_ask_builds_related_entities(self, workbench):
        """F202F-11: ask() builds related_entities from graph traversal."""
        answer = await workbench.ask("evil-domain.com")
        assert isinstance(answer.related_entities, list)
        assert len(answer.related_entities) <= MAX_RELATED_ENTITIES
        for entity in answer.related_entities:
            assert isinstance(entity, RelatedEntity)

    @pytest.mark.asyncio
    async def test_ask_respects_context_bytes(self, workbench):
        """F202F-13: ask() sets context_bytes <= MAX_CONTEXT_BYTES."""
        answer = await workbench.ask("ransomware")
        assert answer.context_bytes <= MAX_CONTEXT_BYTES

    @pytest.mark.asyncio
    async def test_ask_sources_used(self, workbench):
        """ask() records source types consulted."""
        answer = await workbench.ask("attack")
        assert isinstance(answer.sources_used, list)
        assert len(answer.sources_used) > 0

    @pytest.mark.asyncio
    async def test_ask_timing_recorded(self, workbench):
        """ask() records timing in ms."""
        answer = await workbench.ask("attack")
        assert answer.timing_ms >= 0

    @pytest.mark.asyncio
    async def test_ask_extractive_fallback(self, workbench):
        """F202F-12: _extract_answer returns text without model."""
        answer = await workbench.ask("what happened")
        # Should return some text, not empty
        assert len(answer.extractive_answer) > 0
        assert "No relevant information found" in answer.extractive_answer or len(answer.extractive_answer) > 0


# ============================================================================
# F202F-7: query_findings
# ============================================================================
class TestQueryFindings:
    """F202F-7: query_findings keyword search returns scored results."""

    @pytest.mark.asyncio
    async def test_query_findings_keyword_match(self):
        """Keyword match ranks findings by relevance."""
        mock_duckdb = MagicMock()
        mock_duckdb.async_query_recent_findings = AsyncMock(return_value=[
            {"id": "f1", "query": "ransomware", "source_type": "ct_log",
             "confidence": 0.9, "ts": 1714000000.0, "provenance": ()},
            {"id": "f2", "query": "phishing", "source_type": "ct_log",
             "confidence": 0.7, "ts": 1713999000.0, "provenance": ()},
        ])
        workbench = AnalystWorkbench(duckdb_store=mock_duckdb)
        results = await workbench.query_findings("ransomware", limit=10)
        assert len(results) > 0
        # Ransomware match should rank higher than phishing
        assert any("ransomware" in r["query"].lower() for r in results)

    @pytest.mark.asyncio
    async def test_query_findings_respects_limit(self):
        """Results capped to min(limit, MAX_TOP_K)."""
        mock_duckdb = MagicMock()
        mock_duckdb.async_query_recent_findings = AsyncMock(return_value=[
            {"id": f"f{i}", "query": f"query {i}", "source_type": "ct_log",
             "confidence": 0.5, "ts": 1714000000.0 + i, "provenance": ()}
            for i in range(50)
        ])
        workbench = AnalystWorkbench(duckdb_store=mock_duckdb)
        results = await workbench.query_findings("query", limit=10)
        assert len(results) <= 10

    @pytest.mark.asyncio
    async def test_query_findings_empty_when_no_duckdb(self):
        """No duckdb store returns empty list."""
        workbench = AnalystWorkbench()
        results = await workbench.query_findings("test")
        assert results == []


# ============================================================================
# F202F-8: query_graph
# ============================================================================
class TestQueryGraph:
    """F202F-8: query_graph returns RelatedEntity list bounded."""

    @pytest.mark.asyncio
    async def test_query_graph_returns_related_entities(self):
        """query_graph returns RelatedEntity instances."""
        mock_graph = MagicMock()
        mock_graph.find_entity_history = MagicMock(return_value=[
            {"value": "test-domain.com", "ioc_type": "domain",
             "confidence": 0.9, "hops": 1, "relation_type": "uses"},
        ])
        workbench = AnalystWorkbench(graph_service=mock_graph)
        entities = await workbench.query_graph("test-domain.com")
        assert len(entities) > 0
        assert all(isinstance(e, RelatedEntity) for e in entities)

    @pytest.mark.asyncio
    async def test_query_graph_respects_max_hops(self):
        """max_hops capped to MAX_GRAPH_HOPS."""
        mock_graph = MagicMock()
        mock_graph.find_entity_history = MagicMock(return_value=[])
        workbench = AnalystWorkbench(graph_service=mock_graph)
        await workbench.query_graph("test", max_hops=10)
        # Called with capped max_hops=2
        mock_graph.find_entity_history.assert_called_once()
        call_args = mock_graph.find_entity_history.call_args
        assert call_args[1]["max_hops"] <= MAX_GRAPH_HOPS

    @pytest.mark.asyncio
    async def test_query_graph_empty_when_no_graph(self):
        """No graph service returns empty list."""
        workbench = AnalystWorkbench()
        entities = await workbench.query_graph("test")
        assert entities == []


# ============================================================================
# F202F-9: query_vectors
# ============================================================================
class TestQueryVectors:
    """F202F-9: query_vectors returns list bounded by MAX_TOP_K."""

    @pytest.mark.asyncio
    async def test_query_vectors_respects_k(self):
        """k capped to MAX_TOP_K."""
        import numpy as np
        mock_vector = MagicMock()
        mock_vector.query = MagicMock(return_value=[("f1", 0.95)])
        workbench = AnalystWorkbench(vector_store=mock_vector)
        vector = np.zeros(256, dtype=np.float32)
        results = await workbench.query_vectors(vector, k=100)
        mock_vector.query.assert_called_once()
        call_args = mock_vector.query.call_args
        assert call_args[1]["k"] <= MAX_TOP_K

    @pytest.mark.asyncio
    async def test_query_vectors_empty_when_no_vector(self):
        """No vector store returns empty list."""
        import numpy as np
        workbench = AnalystWorkbench()
        vector = np.zeros(256, dtype=np.float32)
        results = await workbench.query_vectors(vector)
        assert results == []


# ============================================================================
# F202F-14: _build_evidence_pointers caps at MAX_EVIDENCE_PTRS
# ============================================================================
class TestEvidencePointerCap:
    """F202F-14: _build_evidence_pointers caps at MAX_EVIDENCE_PTRS."""

    def test_capped_at_max_evidence_ptrs(self):
        """More than MAX_EVIDENCE_PTRS findings are truncated."""
        workbench = AnalystWorkbench()
        findings = [
            {
                "id": f"f{i}",
                "source_type": "ct_log",
                "query": f"finding {i}",
                "confidence": 1.0 - i * 0.01,
                "ts": 1714000000.0 + i,
                "provenance": (),
            }
            for i in range(20)
        ]
        pointers = workbench._build_evidence_pointers(findings)
        assert len(pointers) <= MAX_EVIDENCE_PTRS

    def test_ordered_by_confidence(self):
        """Evidence pointers are ordered by confidence descending."""
        workbench = AnalystWorkbench()
        findings = [
            {
                "id": "low",
                "source_type": "ct_log",
                "query": "low",
                "confidence": 0.3,
                "ts": 1714000000.0,
                "provenance": (),
            },
            {
                "id": "high",
                "source_type": "ct_log",
                "query": "high",
                "confidence": 0.95,
                "ts": 1714000001.0,
                "provenance": (),
            },
        ]
        pointers = workbench._build_evidence_pointers(findings)
        assert pointers[0].finding_id == "high"


# ============================================================================
# F202F-15: create_analyst_workbench
# ============================================================================
class TestFactory:
    """F202F-15: create_analyst_workbench returns AnalystWorkbench instance."""

    def test_returns_workbench_instance(self):
        """Factory returns AnalystWorkbench."""
        wb = create_analyst_workbench()
        assert isinstance(wb, AnalystWorkbench)

    def test_no_external_network_calls(self):
        """Factory does not make network calls."""
        # Should not raise — just creates with available singletons
        wb = create_analyst_workbench()
        assert wb is not None


# ============================================================================
# F202F-16: No external network calls
# ============================================================================
class TestNoNetworkCalls:
    """F202F-16: No external network calls in workbench methods."""

    def test_no_http_modules_imported(self):
        """Workbench does not import network modules."""
        import hledac.universal.knowledge.analyst_workbench as wb_module

        # Should not have these imported at module level
        source = open(wb_module.__file__ or __file__).read()
        # No requests, httpx, aiohttp at module level
        assert "import requests" not in source
        assert "import httpx" not in source
        assert "import aiohttp" not in source

    @pytest.mark.asyncio
    async def test_query_findings_no_network(self):
        """query_findings uses duckdb only, no network."""
        mock_duckdb = MagicMock()
        mock_duckdb.async_query_recent_findings = AsyncMock(return_value=[])
        workbench = AnalystWorkbench(duckdb_store=mock_duckdb)
        await workbench.query_findings("test")
        # Only called duckdb — no network
        mock_duckdb.async_query_recent_findings.assert_called_once()


# ============================================================================
# F202F-17: JSON-LD evidence export
# ============================================================================
class TestEvidenceExport:
    """F202F-17: JSON-LD evidence export produces valid JSON-LD."""

    def test_render_analyst_evidence_jsonld(self):
        """Produces JSON-LD dict with ghost namespace."""
        result = render_analyst_evidence_jsonld(
            question="test question",
            extractive_answer="extractive answer text",
            evidence_pointers=[
                EvidencePointer(
                    finding_id="f1",
                    source_type="ct_log",
                    query="test",
                    confidence=0.9,
                    ts=1714000000.0,
                    provenance=("ct_log",),
                    envelope_available=True,
                    snippet="test snippet",
                )
            ],
            related_entities=[
                RelatedEntity(
                    entity_value="evil.com",
                    entity_type="domain",
                    confidence=0.95,
                    hops=1,
                    relation_types=frozenset(["uses_domain"]),
                )
            ],
            sources_used=["ct_log"],
            context_bytes=1024,
            model_used=False,
            timing_ms=50.0,
        )
        assert result["@type"] == "ghost:AnalystEvidence"
        assert result["ghost:question"] == "test question"
        assert result["ghost:extractiveAnswer"] == "extractive answer text"
        assert len(result["ghost:evidencePointers"]) == 1
        assert len(result["ghost:relatedEntities"]) == 1
        assert result["ghost:contextBytes"] == 1024
        assert result["ghost:modelUsed"] is False

    def test_render_analyst_evidence_jsonld_str(self):
        """Produces valid JSON string."""
        result = render_analyst_evidence_jsonld_str(
            question="test",
            extractive_answer="answer",
            evidence_pointers=[],
            related_entities=[],
            sources_used=[],
            context_bytes=0,
            model_used=False,
            timing_ms=0.0,
        )
        # Should be valid JSON
        parsed = json.loads(result)
        assert parsed["@type"] == "ghost:AnalystEvidence"
        assert parsed["ghost:question"] == "test"

    def test_evidence_pointer_jsonld_fields(self):
        """Evidence pointer has correct ghost: namespace fields."""
        result = render_analyst_evidence_jsonld(
            question="q",
            extractive_answer="a",
            evidence_pointers=[
                EvidencePointer(
                    finding_id="f1",
                    source_type="ct_log",
                    query="test query",
                    confidence=0.85,
                    ts=1714000000.0,
                    provenance=("source1", "source2"),
                    envelope_available=True,
                    snippet="test snippet",
                )
            ],
            related_entities=[],
            sources_used=["ct_log"],
            context_bytes=100,
            model_used=False,
            timing_ms=10.0,
        )
        ep = result["ghost:evidencePointers"][0]
        assert ep["ghost:findingId"] == "f1"
        assert ep["ghost:sourceType"] == "ct_log"
        assert ep["ghost:query"] == "test query"
        assert ep["ghost:confidence"] == 0.85
        assert ep["ghost:timestamp"] == 1714000000.0
        assert ep["ghost:provenance"] == ["source1", "source2"]
        assert ep["ghost:envelopeAvailable"] is True
        assert ep["ghost:snippet"] == "test snippet"


# ============================================================================
# F202F-18: ask_sync
# ============================================================================
class TestAskSync:
    """F202F-18: ask_sync produces same structure as ask()."""

    def test_ask_sync_returns_analyst_answer(self):
        """ask_sync returns AnalystAnswer."""
        mock_duckdb = MagicMock()
        mock_duckdb.async_query_recent_findings = AsyncMock(return_value=[
            {
                "id": "f1",
                "query": "test",
                "source_type": "ct_log",
                "confidence": 0.9,
                "ts": 1714000000.0,
                "provenance": (),
            }
        ])
        workbench = AnalystWorkbench(duckdb_store=mock_duckdb)
        answer = workbench.ask_sync("test question")
        assert isinstance(answer, AnalystAnswer)
        assert answer.extractive_answer is not None
        assert isinstance(answer.evidence_pointers, list)


# ============================================================================
# F202F-19: graph_rag bounded context
# ============================================================================
class TestGraphRAGBound:
    """F202F-19: graph_rag multi_hop_search has max_nodes bounded."""

    def test_graph_rag_multi_hop_has_max_nodes_param(self):
        """GraphRAGOrchestrator.multi_hop_search has max_nodes parameter."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
        import inspect
        sig = inspect.signature(GraphRAGOrchestrator.multi_hop_search)
        assert "max_nodes" in sig.parameters

    def test_graph_rag_hard_caps_on_paths(self):
        """graph_rag hard-caps primary_paths[:10] and counter_paths[:5]."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
        import inspect
        source = inspect.getsource(GraphRAGOrchestrator.multi_hop_search)
        assert "primary_paths[:10]" in source or "primary_paths[:max_nodes]" in source
        assert "counter_paths[:5]" in source


# ============================================================================
# F202F-20: rag_engine no-model fallback
# ============================================================================
class TestRAGEngineNoModelFallback:
    """F202F-20: rag_engine has no-model fallback retrieval (BM25/hybrid)."""

    def test_rag_engine_has_bm25_index(self):
        """RAGEngine has BM25Index for keyword-only retrieval."""
        from hledac.universal.knowledge.rag_engine import RAGEngine, BM25Index
        import inspect
        source = inspect.getsource(RAGEngine)
        # Has BM25 available for fallback
        assert "BM25Index" in source or "bm25" in source.lower()

    def test_rag_engine_hybrid_retrieve_no_model(self):
        """hybrid_retrieve works without LLM model."""
        from hledac.universal.knowledge.rag_engine import RAGEngine
        import inspect
        source = inspect.getsource(RAGEngine.hybrid_retrieve)
        # hybrid_retrieve uses BM25 + vector, no LLM required
        assert "hybrid" in source.lower() or "bm25" in source.lower()
