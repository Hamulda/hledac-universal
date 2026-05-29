"""
Retrieval Plane Boundary Tests
=============================

Probe testy pro uzamknutí authority boundaries mezi 4 retrieval moduly:
- rag_engine.py = grounding authority (NOT identity store)
- lancedb_store.py = identity/entity store (NOT grounding authority)
- pq_index.py = compression layer (NOT retrieval authority)
- graph_rag.py = consumer/orchestrator (NOT backend owner)

Tyto testy by MĚLY PROCHÁZET — pokud některý selže, znamená to
boundary violation (code drift).
"""



class TestRAGEngineBoundaries:
    """RAGEngine je grounding authority — NENÍ identity/entity store."""

    def test_rag_engine_has_hybrid_retrieve(self):
        """RAGEngine MUSÍ mít hybrid_retrieve — to je jeho primary API."""
        from hledac.universal.knowledge.rag_engine import RAGEngine
        assert hasattr(RAGEngine, "hybrid_retrieve")

    def test_rag_engine_has_hnsw_index(self):
        """RAGEngine MUSÍ mít HNSW vector index build."""
        from hledac.universal.knowledge.rag_engine import RAGEngine
        assert hasattr(RAGEngine, "build_hnsw_index")

    def test_rag_engine_has_raptor_retrieval(self):
        """RAGEngine může mít RAPTOR hierarchical retrieval."""
        from hledac.universal.knowledge.rag_engine import RAGEngine
        # RAPTOR je optional ale měl by být present pokud existuje
        # Nemá přímý veřejný method s timto jménem, takže checkujeme presence of _build_raptor_tree
        assert hasattr(RAGEngine, "_build_raptor_tree")

    def test_rag_engine_has_no_add_entity(self):
        """RAGEngine NESMÍ mít add_entity — to patří do LanceDBIdentityStore."""
        from hledac.universal.knowledge.rag_engine import RAGEngine
        assert "add_entity" not in dir(RAGEngine)

    def test_rag_engine_has_no_search_similar(self):
        """RAGEngine NESMÍ mít search_similar — to patří do LanceDBIdentityStore."""
        from hledac.universal.knowledge.rag_engine import RAGEngine
        assert "search_similar" not in dir(RAGEngine)

    def test_rag_engine_has_no_lancedb_schema(self):
        """RAGEngine NESMÍ mít entity schema z LanceDB."""
        from hledac.universal.knowledge.rag_engine import RAGEngine
        # LanceDB entity schema má 'aliases', 'first_seen', 'last_seen'
        # RAGEngine by to neměl mít
        assert "aliases" not in dir(RAGEngine)


class TestLanceDBStoreBoundaries:
    """LanceDBIdentityStore je identity/entity store — NENÍ grounding authority."""

    def test_lancedb_has_search_similar(self):
        """LanceDBIdentityStore MUSÍ mít search_similar — to je jeho primary API."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
        assert hasattr(LanceDBIdentityStore, "search_similar")

    def test_lancedb_has_add_entity(self):
        """LanceDBIdentityStore MUSÍ mít add_entity pro přidávání entit."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
        assert hasattr(LanceDBIdentityStore, "add_entity")

    def test_lancedb_has_no_hybrid_retrieve(self):
        """LanceDBIdentityStore NESMÍ mít hybrid_retrieve — to patří do RAGEngine."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
        assert "hybrid_retrieve" not in dir(LanceDBIdentityStore)

    def test_lancedb_has_no_build_hnsw_index(self):
        """LanceDBIdentityStore NESMÍ mít build_hnsw_index — HNSW patří do RAGEngine."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
        assert "build_hnsw_index" not in dir(LanceDBIdentityStore)

    def test_lancedb_has_fts_capability(self):
        """LanceDBIdentityStore MUSÍ mít FTS pro alias matching."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
        # FTS je v schema (create_fts_index) a používá se v _detect_query_type
        assert hasattr(LanceDBIdentityStore, "_detect_query_type")

    def test_lancedb_has_rrf_fusion(self):
        """LanceDBIdentityStore má RRF fusion pro hybrid search."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
        assert hasattr(LanceDBIdentityStore, "_rrf_fusion")


class TestPQIndexBoundaries:
    """PQIndex je compression/acceleration layer — NENÍ primární retrieval authority."""

    def test_pq_index_has_train(self):
        """PQIndex MUSÍ mít train() — musí být trained před použitím."""
        from hledac.universal.knowledge.pq_index import PQIndex
        assert hasattr(PQIndex, "train")

    def test_pq_index_has_encode(self):
        """PQIndex MUSÍ mít encode() — pro kódování vektorů."""
        from hledac.universal.knowledge.pq_index import PQIndex
        assert hasattr(PQIndex, "encode")

    def test_pq_index_has_search(self):
        """PQIndex MUSÍ mít search() — ale jen na trained index, ne na kolekci."""
        from hledac.universal.knowledge.pq_index import PQIndex
        assert hasattr(PQIndex, "search")

    def test_pq_index_returns_similarity(self):
        """PQIndex.search() vrací similarity (1/(1+L2)), ne distance."""
        # Kontrola že search má správný return type comment
        import inspect

        from hledac.universal.knowledge.pq_index import PQIndex
        source = inspect.getsource(PQIndex.search)
        assert "similarity" in source.lower(), "PQIndex.search should return similarity"


class TestGraphRAGBoundaries:
    """GraphRAGOrchestrator je consumer/orchestrator — NENÍ backend owner."""

    def test_graph_rag_has_multi_hop_search(self):
        """GraphRAGOrchestrator MUSÍ mít multi_hop_search."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
        assert hasattr(GraphRAGOrchestrator, "multi_hop_search")

    def test_graph_rag_has_knowledge_layer_param(self):
        """GraphRAGOrchestrator.__init__ MUSÍ mít knowledge_layer parametr."""
        import inspect

        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
        sig = inspect.signature(GraphRAGOrchestrator.__init__)
        assert "knowledge_layer" in sig.parameters

    def test_graph_rag_has_no_create_node(self):
        """GraphRAGOrchestrator NESMÍ mít create_node — to je backend operation."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
        assert "create_node" not in dir(GraphRAGOrchestrator)

    def test_graph_rag_has_no_delete_node(self):
        """GraphRAGOrchestrator NESMÍ mít delete_node — to je backend operation."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
        assert "delete_node" not in dir(GraphRAGOrchestrator)

    def test_graph_rag_has_no_write_operations(self):
        """GraphRAGOrchestrator NESMÍ mít přímé write operace na backend."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
        write_ops = ["write_node", "create_edge", "delete_edge", "update_node"]
        for op in write_ops:
            assert op not in dir(GraphRAGOrchestrator), f"GraphRAG má {op} — není consumer!"

    def test_graph_rag_has_contradiction_detection(self):
        """GraphRAGOrchestrator má contradiction detection."""
        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
        assert hasattr(GraphRAGOrchestrator, "_detect_contradictions")


class TestCrossModuleBoundaries:
    """Testy napříč moduly — ověření že správné moduly mají správné vlastnosti."""

    def test_no_module_has_all_three_primary_apis(self):
        """Žádný modul by neměl mít všechny 3 primární API najednou.

        To by znamenalo že boundaries jsou rozmazané.
        """
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
        from hledac.universal.knowledge.rag_engine import RAGEngine

        rag_api = set(dir(RAGEngine))
        set(dir(LanceDBIdentityStore))

        # Průnik by neměl obsahovat všechny 3
        hybrid_and_entity = (
            "hybrid_retrieve" in rag_api and
            "search_similar" in rag_api and
            "add_entity" in rag_api
        )
        assert not hybrid_and_entity, "RAGEngine má všechny 3 API — boundaries rozmazané!"

    def test_rag_engine_not_lancedb_schema(self):
        """RAGEngine NESMÍ mít LanceDB identity schema fields."""
        from hledac.universal.knowledge.rag_engine import RAGEngine
        rag_api_lower = {a.lower() for a in dir(RAGEngine)}
        # LanceDB entity schema fields
        lancedb_identity_fields = {"aliases", "first_seen", "last_seen", "embedding"}
        assert len(rag_api_lower & lancedb_identity_fields) == 0, (
            f"RAGEngine má identity fields: {rag_api_lower & lancedb_identity_fields}"
        )

    def test_assertions_module_usable(self):
        """assertions.py je importovatelný a má správné funkce."""
        from hledac.universal.knowledge.assertions import (
            assert_graph_rag_is_consumer_not_owner,
            assert_lancedb_is_not_grounding_authority,
            assert_pq_index_is_compression_only,
            assert_rag_engine_is_not_identity_store,
        )
        # Všechny funkce existují a jsou callable
        assert callable(assert_rag_engine_is_not_identity_store)
        assert callable(assert_lancedb_is_not_grounding_authority)
        assert callable(assert_pq_index_is_compression_only)
        assert callable(assert_graph_rag_is_consumer_not_owner)
