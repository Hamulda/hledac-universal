---
title: issue_5_2_rag_hnsw_quality_fix
summary: 'ISSUE 5.2: RAG Engine HNSW quality fix - adaptive expansion_add (200/300) and native batch_search API'
tags: []
related: [facts/project/technology_stack.md, facts/project/rust_extensions_overview.md]
keywords: []
createdAt: '2026-07-24T20:48:52.429Z'
updatedAt: '2026-07-24T20:48:52.429Z'
---
## Reason
Document RAG Engine HNSW quality fix from 2026-07-24

## Raw Concept
**Task:**
Fix RAG Engine HNSW index quality degradation

**Changes:**
- Changed expansion_add from hard-coded 100 to adaptive 200/300 based on index size
- Replaced loop-based batch_search with native usearch v2.26+ batch API

**Files:**
- knowledge/rag_engine.py
- tools/hnsw_builder.py

**Flow:**
Root cause analysis -> Adaptive expansion_add -> Native batch API -> Verification

**Timestamp:** 2026-07-24

**Author:** System

**Patterns:**
- `expansion_add\s*=\s*200` - expansion_add for ≤100k vectors
- `expansion_add\s*=\s*300` - expansion_add for >100k vectors

## Narrative
### Structure
Two-part fix: (1) HNSW index quality via adaptive expansion_add, (2) batch search performance via native API

### Dependencies
Requires usearch v2.26+ for native VectorOrVectorsLike batch search support

### Highlights
expansion_add increased from 100 to 200-300, batch_search now uses self._index.search() directly instead of loop

### Rules
Rule 1: expansion_add ≤ 1024 (usearch maximum)
Rule 2: expansion_add = 200 for indices ≤100k elements
Rule 3: expansion_add = 300 for indices >100k elements

### Examples
Files modified: knowledge/rag_engine.py:216-238 (_init_index), knowledge/rag_engine.py:345-388 (batch_search), tools/hnsw_builder.py:34-45 (IncrementalHNSW)
