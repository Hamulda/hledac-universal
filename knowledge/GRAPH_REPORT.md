# knowledge/ Graph Report

## Summary
- **Files:** 36 (34 code + 2 explainer)
- **Nodes:** 1749 (symbols, functions, classes)
- **Edges:** 3564 (import/call/ref relationships)
- **Communities:** 148 (Leiden, resolution=0.5)

## Node Types
- 1667 unknown

## Top Files by Symbol Count
- 423 `duckdb_store.py`
- 120 `graph_rag.py`
- 111 `rag_engine.py`
-  94 `lancedb_store.py`
-  84 `entity_linker.py`
-  79 `analyst_workbench.py`
-  71 `evidence_chain.py`
-  59 `ioc_graph.py`
-  55 `graph_service.py`
-  52 `quality_assessment.py`

## Top Cross-File Edges
- 852 `duckdb_store.py` <> `duckdb_store.py`
- 280 `graph_rag.py` <> `graph_rag.py`
- 237 `rag_engine.py` <> `rag_engine.py`
- 182 `lancedb_store.py` <> `lancedb_store.py`
- 170 `analyst_workbench.py` <> `analyst_workbench.py`
- 158 `entity_linker.py` <> `entity_linker.py`
- 131 `evidence_chain.py` <> `evidence_chain.py`
- 104 `ioc_graph.py` <> `ioc_graph.py`
- 100 `graph_service.py` <> `graph_service.py`
-  83 `duckdb_store.py` <> `quality_assessment.py`

## Top Communities
### Cluster 0 -- 58 nodes, files: 
`typing` `logging` `rag_engine.py` `lancedb_store.py` `graph_rag.py`

### Cluster 1 -- 57 nodes, files: 
`graph_service.py` `GraphService` `str` `_get_graph()` `float`

### Cluster 2 -- 55 nodes, files: 
`IOCGraph` `str` `float` `.flush_buffers()` `int`

### Cluster 3 -- 41 nodes, files: 
`GraphAttachmentStore` `callable` `Any` `.get_connected_iocs_batch()` `_check_graph_capability()`

### Cluster 4 -- 41 nodes, files: 
`BM25Index` `MetadataStore` `str` `._score_bm25()` `.search()`

### Cluster 5 -- 37 nodes, files: 
`str` `AnalystWorkbench` `Any` `.build_sprint_brief()` `float`

### Cluster 6 -- 35 nodes, files: 
`analytics_hook.py` `_ShadowRecorder` `vector_store.py` `shadow_record_finding()` `.enqueue()`

### Cluster 7 -- 34 nodes, files: 
`EvidenceChain` `evidence_chain.py` `_get_chain_for_finding()` `summarize_chain_support()` `ChainStep`

### Cluster 8 -- 34 nodes, files: 
`Any` `int` `.multi_hop_search()` `.multi_hop_search_sync()` `._traverse_hop_with_paths()`

### Cluster 9 -- 33 nodes, files: 
`ann_index.py` `_ANNIndex` `check_ann_duplicate()` `.upsert()` `.ann_search()`

### Cluster 10 -- 33 nodes, files: 
`str` `bool` `.wal_write_pending_sync_marker()` `.wal_write_deadletter_marker()` `.wal_write_finding()`

### Cluster 11 -- 32 nodes, files: 
`WALManager` `DedupManager` `QualityAssessmentState` `FindingEnvelope` `SemanticStoreBuffer`

### Cluster 12 -- 30 nodes, files: 
`.async_initialize()` `.close()` `._init_connection()` `.initialize()` `_get_duckdb()`

### Cluster 13 -- 30 nodes, files: 
`RAGEngine` `.query()` `.initialize()` `bool` `._get_random_chunks()`

### Cluster 14 -- 29 nodes, files: 
`EvidenceChainBuilder` `str` `.record_step()` `float` `.record_attribution()`
