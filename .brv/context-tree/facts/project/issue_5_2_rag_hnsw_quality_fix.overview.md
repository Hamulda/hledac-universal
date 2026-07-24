- Fixed HNSW index quality degradation in RAG Engine via two-part solution
- Changed expansion_add from hard-coded 100 to adaptive values: 200 for indices ≤100k vectors, 300 for indices >100k vectors
- Replaced loop-based batch_search with native usearch v2.26+ batch API using self._index.search() directly
- Implemented rules: expansion_add ≤ 1024 (usearch maximum), with conditional logic based on index size
- Requires usearch v2.26+ for native VectorOrVectorsLike batch search support

**Structure:**
- Reason section with task context
- Raw Concept with Changes, Files, Flow, Patterns, and Rules
- Narrative with Structure, Dependencies, Highlights, Rules, and Examples

**Files modified:**
- knowledge/rag_engine.py:216-238 (_init_index method for adaptive expansion_add)
- knowledge/rag_engine.py:345-388 (batch_search method with native API)
- tools/hnsw_builder.py:34-45 (IncrementalHNSW class)

**Key patterns:**
- `expansion_add\s*=\s*200` - for ≤100k vectors
- `expansion_add\s*=\s*300` - for >100k vectors

**Flow:** Root cause analysis → Adaptive expansion_add → Native batch API → Verification

**Dependencies:** usearch v2.26+
**Timestamp:** 2026-07-24