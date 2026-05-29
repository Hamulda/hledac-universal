"""
Retrieval Plane Seam Assertions
===============================

Malé runtime assertions pro ověření authority boundaries.
Tyto assertions jsou VOLITELNÉ — pouze pro debugging a development.

POUŽITÍ:
    from hledac.universal.knowledge.assertions import (
        assert_rag_engine_is_not_identity_store,
        assert_lancedb_is_not_grounding_authority,
        assert_pq_index_is_compression_only,
        assert_graph_rag_is_consumer_not_owner,
    )

VŠECHNY FUNKCE VRACEJÍ None — raise AssertionError pokud selžou.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator
    from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore
    from hledac.universal.knowledge.pq_index import PQIndex
    from hledac.universal.knowledge.rag_engine import RAGEngine


def assert_rag_engine_is_not_identity_store(rag_engine: RAGEngine) -> None:
    """
    RAGEngine NENÍ identity/entity store.

    RAGEngine je grounding authority — hybrid retrieval (dense + sparse)
    pro context augmentation LLM.

    RAGEngine NESMÍ mít:
    - add_entity() method
    - search_similar() method (LanceDB-style)
    - entity identity resolution
    """
    attrs = dir(rag_engine)
    assert "add_entity" not in attrs, (
        "RAGEngine má add_entity() — NENÍ identity store! "
        "add_entity() patří do LanceDBIdentityStore."
    )
    assert "search_similar" not in attrs, (
        "RAGEngine má search_similar() — NENÍ identity store! "
        "search_similar() patří do LanceDBIdentityStore."
    )
    # RAGEngine má hybrid_retrieve — to je v pořádku
    assert hasattr(rag_engine, "hybrid_retrieve"), (
        "RAGEngine postrádá hybrid_retrieve() — nemusí být grounding authority!"
    )


def assert_lancedb_is_not_grounding_authority(store: LanceDBIdentityStore) -> None:
    """
    LanceDBIdentityStore NENÍ grounding authority.

    LanceDBIdentityStore je identity/entity store — entity resolution
    pomocí vector similarity + FTS alias matching.

    LanceDBIdentityStore NESMÍ mít:
    - hybrid_retrieve() method
    - HNSWVectorIndex
    - RAPTOR tree
    - BM25Index
    """
    attrs = dir(store)
    assert "hybrid_retrieve" not in attrs, (
        "LanceDBIdentityStore má hybrid_retrieve() — NENÍ grounding authority! "
        "hybrid_retrieve() patří do RAGEngine."
    )
    assert "build_hnsw_index" not in attrs, (
        "LanceDBIdentityStore má build_hnsw_index() — NENÍ grounding authority! "
        "HNSW patří do RAGEngine."
    )
    # LanceDB má search_similar — to je v pořádku
    assert hasattr(store, "search_similar"), (
        "LanceDBIdentityStore postrádá search_similar() — nemusí být identity store!"
    )


def assert_pq_index_is_compression_only(pq_index: PQIndex) -> None:
    """
    PQIndex NENÍ primární retrieval authority.

    PQIndex je compression/acceleration layer — produkt quantization
    pro snížení memory footprintu embeddingů.

    PQIndex NESMÍ mít:
    - search() na celé kolekce dokumentů
    - hybrid retrieval
    - FTS index
    """
    dir(pq_index)
    # PQIndex má search() — ale jen na trained indexu, ne na kolekci
    assert hasattr(pq_index, "encode"), (
        "PQIndex postrádá encode() — není compression layer!"
    )
    assert hasattr(pq_index, "train"), (
        "PQIndex postrádá train() — musí být trained před použitím!"
    )


def assert_graph_rag_is_consumer_not_owner(orchestrator: GraphRAGOrchestrator) -> None:
    """
    GraphRAGOrchestrator NENÍ backend owner.

    GraphRAGOrchestrator je consumer/orchestrator — pracuje NAD
    knowledge_layer backend, nevlastní ho.

    GraphRAGOrchestrator MUSÍ:
    - mít knowledge_layer atribut (init parameter)
    - volat get_related() / get_related_sync() pro traversal
    - NEvlastnit backend storage
    """
    import inspect
    sig = inspect.signature(orchestrator.__class__.__init__)
    params = list(sig.parameters.keys())
    assert "knowledge_layer" in params, (
        f"GraphRAGOrchestrator.__init__ postrádá knowledge_layer param: {params}"
    )


# =============================================================================
# Module-level convenience function
# =============================================================================

def assert_all_boundaries(
    rag_engine: RAGEngine,
    lancedb_store: LanceDBIdentityStore,
    pq_index: PQIndex,
    graph_rag: GraphRAGOrchestrator,
) -> None:
    """
    Spustit všechny boundary assertions.

    Použití při initializaci pro ověření že všechny moduly
    jsou na správných místech.
    """
    assert_rag_engine_is_not_identity_store(rag_engine)
    assert_lancedb_is_not_grounding_authority(lancedb_store)
    assert_pq_index_is_compression_only(pq_index)
    assert_graph_rag_is_consumer_not_owner(graph_rag)
