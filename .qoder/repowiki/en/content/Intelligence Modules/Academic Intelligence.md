# Academic Intelligence

<cite>
**Referenced Files in This Document**
- [academic_search.py](file://intelligence/academic_search.py)
- [academic_discovery.py](file://intelligence/academic_discovery.py)
- [query_expansion.py](file://utils/query_expansion.py)
- [deduplication.py](file://utils/deduplication.py)
- [live_public_pipeline.py](file://pipeline/live_public_pipeline.py)
- [research_coordinator.py](file://coordinators/research_coordinator.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
This document describes the Academic Intelligence module, which provides multi-source academic search, discovery, and integration capabilities. It covers:
- Multi-source querying across ArXiv, Crossref, and Semantic Scholar
- Query expansion using semantic, syntactic, and domain-specific strategies
- Result deduplication and ranking
- Citation-aware metadata extraction and relevance scoring
- Integration with the broader research pipeline for ingestion and downstream synthesis

The module is designed for M1-optimized performance, robust error handling, and extensibility across academic domains.

## Project Structure
The Academic Intelligence module is organized into focused components:
- Intelligence layer: academic search engine and convenience discovery functions
- Utilities: query expansion and deduplication engines
- Pipeline integration: ingestion of academic findings into the research pipeline
- Research coordination: deep excavation and citation graph building

```mermaid
graph TB
subgraph "Intelligence Layer"
AS["AcademicSearchEngine<br/>academic_search.py"]
AD["AcademicDiscovery<br/>academic_discovery.py"]
end
subgraph "Utilities"
QE["Query Expansion<br/>query_expansion.py"]
DU["Deduplication Engine<br/>deduplication.py"]
end
subgraph "Integration"
LP["Live Public Pipeline<br/>live_public_pipeline.py"]
RC["Research Coordinator<br/>research_coordinator.py"]
end
AS --> QE
AS --> DU
AD --> AS
LP --> AD
RC --> AS
```

**Diagram sources**
- [academic_search.py:787-1232](file://intelligence/academic_search.py#L787-L1232)
- [academic_discovery.py:1-301](file://intelligence/academic_discovery.py#L1-L301)
- [query_expansion.py:340-751](file://utils/query_expansion.py#L340-L751)
- [deduplication.py:904-1067](file://utils/deduplication.py#L904-L1067)
- [live_public_pipeline.py:2067-2092](file://pipeline/live_public_pipeline.py#L2067-L2092)
- [research_coordinator.py:172-1200](file://coordinators/research_coordinator.py#L172-L1200)

**Section sources**
- [academic_search.py:1-1369](file://intelligence/academic_search.py#L1-L1369)
- [academic_discovery.py:1-301](file://intelligence/academic_discovery.py#L1-L301)
- [query_expansion.py:1-940](file://utils/query_expansion.py#L1-L940)
- [deduplication.py:1-1428](file://utils/deduplication.py#L1-L1428)
- [live_public_pipeline.py:2067-2092](file://pipeline/live_public_pipeline.py#L2067-L2092)
- [research_coordinator.py:1-1374](file://coordinators/research_coordinator.py#L1-L1374)

## Core Components
- AcademicSearchEngine: orchestrates multi-source search, query expansion, parallel execution, deduplication, and ranking.
- Source adapters: ArxivAdapter, CrossrefAdapter, SemanticScholarAdapter implement unified search interfaces.
- AcademicDiscovery: convenience functions for single-source searches and concurrent multi-source discovery.
- Query expansion: semantic, syntactic, and domain-specific strategies to broaden coverage.
- Deduplication engine: hybrid semantic/content/metadata deduplication with caching and LSH clustering.
- Pipeline integration: transforms academic results into CanonicalFinding entries for ingestion.
- Research coordinator: supports deep excavation and citation graph construction.

**Section sources**
- [academic_search.py:787-1232](file://intelligence/academic_search.py#L787-L1232)
- [academic_discovery.py:77-261](file://intelligence/academic_discovery.py#L77-L261)
- [query_expansion.py:340-751](file://utils/query_expansion.py#L340-L751)
- [deduplication.py:904-1067](file://utils/deduplication.py#L904-L1067)

## Architecture Overview
The Academic Intelligence architecture follows a layered design:
- Input: user query and configuration
- Query expansion: generate multiple query variants
- Parallel source execution: execute searches across adapters with concurrency control
- Result processing: deduplication, ranking, and metadata enrichment
- Output: structured results and optional integration hooks

```mermaid
sequenceDiagram
participant U as "User"
participant E as "AcademicSearchEngine"
participant X as "QueryExpander"
participant A1 as "ArxivAdapter"
participant A2 as "CrossrefAdapter"
participant A3 as "SemanticScholarAdapter"
participant D as "DeduplicationEngine"
U->>E : search(query, max_results)
E->>E : analyze_query()
E->>X : expand(query) [semantic, syntactic, domain]
X-->>E : variations[]
par Parallel Source Execution
E->>A1 : search(variations[], async_session)
E->>A2 : search(variations[], async_session)
E->>A3 : search(variations[], async_session)
end
A1-->>E : results[]
A2-->>E : results[]
A3-->>E : results[]
E->>D : deduplicate(results)
D-->>E : unique_results[]
E->>E : rank_results(unique_results, query)
E-->>U : AcademicSearchResult
```

**Diagram sources**
- [academic_search.py:873-1176](file://intelligence/academic_search.py#L873-L1176)
- [query_expansion.py:700-751](file://utils/query_expansion.py#L700-L751)
- [deduplication.py:925-1047](file://utils/deduplication.py#L925-L1047)

## Detailed Component Analysis

### AcademicSearchEngine
The engine coordinates the entire academic search lifecycle:
- Query analysis and expansion
- Parallel execution across adapters with throttling and session reuse
- Deduplication using semantic, content, and metadata strategies
- Ranking by match score, source reliability, and citation counts
- Performance tracking and cleanup

Key behaviors:
- Uses a shared aiohttp session when provided to reduce connection overhead.
- Applies a semaphore to cap concurrency across sources.
- Supports disabling expansion or deduplication for specialized workflows.

```mermaid
classDiagram
class AcademicSearchEngine {
+search(query, max_results, sources, async_session) AcademicSearchResult
-_execute_searches(queries, analysis, sources, async_session) Dict
-_deduplicate_results(results) List
-_rank_results(results, query) List
-_normalize_url(url) str
+cleanup() void
}
class BaseSourceAdapter {
<<abstract>>
+search(query, max_results, analysis) SearchResult[]
+execute_search(query, max_results, analysis, async_session) Tuple
+get_performance() SourcePerformance
}
class ArxivAdapter {
+search(query, max_results, analysis, async_session) SearchResult[]
+get_paper_details(arxiv_id) Dict
}
class CrossrefAdapter {
+search(query, max_results, analysis, async_session) SearchResult[]
+get_work_by_doi(doi) Dict
}
class SemanticScholarAdapter {
+search(query, max_results, analysis, async_session) SearchResult[]
+get_paper_details(paper_id) Dict
+get_citations(paper_id, limit) Dict[]
}
AcademicSearchEngine --> BaseSourceAdapter : "manages"
BaseSourceAdapter <|-- ArxivAdapter
BaseSourceAdapter <|-- CrossrefAdapter
BaseSourceAdapter <|-- SemanticScholarAdapter
```

**Diagram sources**
- [academic_search.py:787-1232](file://intelligence/academic_search.py#L787-L1232)

**Section sources**
- [academic_search.py:873-1176](file://intelligence/academic_search.py#L873-L1176)

### Query Expansion Strategies
The system implements three complementary strategies:
- SemanticExpansionStrategy: synonym replacement and academic modifiers
- SyntacticExpansionStrategy: phrase reordering, boolean expressions, and field queries
- DomainSpecificExpansionStrategy: domain indicators and paper-type modifiers

These strategies generate weighted query variations that improve recall across academic databases.

```mermaid
flowchart TD
Start(["Start"]) --> Detect["Detect domain from query/context"]
Detect --> BuildMap["Build synonym map (general + domain)"]
BuildMap --> Replace["Replace terms with synonyms"]
Replace --> AddMods["Add academic modifiers (recent, review, etc.)"]
AddMods --> Phrase["Reorder key terms"]
Phrase --> Boolean["Wrap phrases and add boolean operators"]
Boolean --> Fields["Add field-specific queries (title:, abstract:)"]
Fields --> Exact["Exact phrase match"]
Exact --> DomainMods["Add domain-specific modifiers"]
DomainMods --> PaperTypes["Add paper-type modifiers"]
PaperTypes --> Unique["Remove duplicates"]
Unique --> End(["Return variations"])
```

**Diagram sources**
- [query_expansion.py:368-697](file://utils/query_expansion.py#L368-L697)

**Section sources**
- [query_expansion.py:340-751](file://utils/query_expansion.py#L340-L751)

### Deduplication Engine
The engine applies a hybrid approach:
- Semantic: vector embeddings with caching and SimHash LSH clustering
- Content: exact hash, character hash, MinHash Jaccard similarity
- Metadata: weighted field comparisons with normalization

It tracks statistics, supports batch processing, and provides non-blocking cleanup.

```mermaid
classDiagram
class DeduplicationEngine {
+deduplicate(items) DeduplicationResult
-_process_batch(batch) DeduplicationMatch[]
-_find_duplicates(item, candidates) DeduplicationMatch[]
-_deduplicate_matches(matches) DeduplicationMatch[]
+cleanup() void
}
class SemanticDeduplicator {
+find_duplicates(item, candidates) DeduplicationMatch[]
-_get_embedding(item) ndarray
-_generate_batch_embeddings(contents) ndarray[]
+cleanup() void
}
class ContentDeduplicator {
+find_duplicates(item, candidates) DeduplicationMatch[]
-_get_content_signature(item) Dict
-_compute_minhash(content) int[]
+cleanup() void
}
class MetadataDeduplicator {
+find_duplicates(item, candidates) DeduplicationMatch[]
-_extract_and_normalize_metadata(item) Dict
-_compute_field_similarities(m1, m2) Dict
+cleanup() void
}
DeduplicationEngine --> SemanticDeduplicator
DeduplicationEngine --> ContentDeduplicator
DeduplicationEngine --> MetadataDeduplicator
```

**Diagram sources**
- [deduplication.py:904-1067](file://utils/deduplication.py#L904-L1067)

**Section sources**
- [deduplication.py:904-1067](file://utils/deduplication.py#L904-L1067)

### Academic Discovery Functions
Convenience functions wrap the AcademicSearchEngine for single-source and concurrent multi-source discovery:
- search_arxiv, search_crossref, search_semantic_scholar
- search_academic_all: concurrent execution with rate limiting

These functions convert adapter results into a standardized AcademicPaper structure.

```mermaid
sequenceDiagram
participant C as "Caller"
participant F as "search_arxiv()"
participant E as "AcademicSearchEngine"
participant A as "ArxivAdapter"
C->>F : query, max_results
F->>E : search(query, max_results, sources=["arxiv"])
E->>A : search(query, ...)
A-->>E : results[]
E-->>F : AcademicSearchResult
F->>F : map to AcademicPaper[]
F-->>C : List[Dict]
```

**Diagram sources**
- [academic_discovery.py:77-127](file://intelligence/academic_discovery.py#L77-L127)

**Section sources**
- [academic_discovery.py:77-261](file://intelligence/academic_discovery.py#L77-L261)

### Pipeline Integration
Academic results are transformed into CanonicalFinding entries and ingested into the research pipeline. The pipeline stores findings with provenance and confidence, enabling downstream synthesis and analysis.

```mermaid
flowchart TD
Q["Academic Query"] --> S["Academic Discovery Results"]
S --> M["Map to AcademicPaper"]
M --> F["Create CanonicalFinding"]
F --> I["Ingest Findings Batch"]
I --> P["Pipeline Storage"]
```

**Diagram sources**
- [live_public_pipeline.py:2067-2092](file://pipeline/live_public_pipeline.py#L2067-L2092)
- [academic_discovery.py:77-217](file://intelligence/academic_discovery.py#L77-L217)

**Section sources**
- [live_public_pipeline.py:2067-2092](file://pipeline/live_public_pipeline.py#L2067-L2092)
- [academic_discovery.py:77-217](file://intelligence/academic_discovery.py#L77-L217)

### Research Coordination and Citation Graphs
The Research Coordinator supports deep excavation of academic literature:
- Citation-aware relevance scoring
- Forward/backward citation fetching (placeholder for real APIs)
- Citation graph construction for meta-synthesis

```mermaid
sequenceDiagram
participant RC as "ResearchCoordinator"
participant P as "Paper"
participant API as "Citation API"
RC->>RC : excavate(query, config)
loop For each paper
RC->>API : fetch citations (forward/backward)
API-->>RC : related papers
RC->>RC : calculate relevance (decay + overlap)
RC->>RC : add to thread and citation graph
end
RC-->>RC : return top papers and graph
```

**Diagram sources**
- [research_coordinator.py:1000-1131](file://coordinators/research_coordinator.py#L1000-L1131)

**Section sources**
- [research_coordinator.py:1000-1131](file://coordinators/research_coordinator.py#L1000-L1131)

## Dependency Analysis
The Academic Intelligence module exhibits clean separation of concerns:
- Intelligence depends on utilities for expansion and deduplication
- Discovery functions depend on the engine for execution
- Pipeline integration depends on discovery outputs
- Research coordination depends on engine outputs and citation APIs

```mermaid
graph LR
AS["AcademicSearchEngine"] --> QE["Query Expansion"]
AS --> DU["Deduplication Engine"]
AD["AcademicDiscovery"] --> AS
LP["Pipeline Integration"] --> AD
RC["Research Coordinator"] --> AS
```

**Diagram sources**
- [academic_search.py:800-871](file://intelligence/academic_search.py#L800-L871)
- [academic_discovery.py:34-38](file://intelligence/academic_discovery.py#L34-L38)
- [deduplication.py:904-923](file://utils/deduplication.py#L904-L923)
- [live_public_pipeline.py:2067-2092](file://pipeline/live_public_pipeline.py#L2067-L2092)
- [research_coordinator.py:172-1200](file://coordinators/research_coordinator.py#L172-L1200)

**Section sources**
- [academic_search.py:800-871](file://intelligence/academic_search.py#L800-L871)
- [academic_discovery.py:34-38](file://intelligence/academic_discovery.py#L34-L38)
- [deduplication.py:904-923](file://utils/deduplication.py#L904-L923)
- [live_public_pipeline.py:2067-2092](file://pipeline/live_public_pipeline.py#L2067-L2092)
- [research_coordinator.py:172-1200](file://coordinators/research_coordinator.py#L172-L1200)

## Performance Considerations
- Concurrency control: semaphore-based throttling prevents overload across adapters.
- Session reuse: shared aiohttp sessions reduce connection overhead.
- Deduplication efficiency: SimHash LSH clustering and embedding caches minimize computational cost.
- Memory awareness: bounded caches and thread pools adapt to M1 constraints.
- Rate limiting: per-source rate limits and adaptive delays mitigate API penalties.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Timeout errors: adjust timeouts in SourceConfig and handle asyncio.TimeoutError gracefully.
- Rate limits: respect per-source rate limits; the engine logs warnings and continues.
- Parsing failures: XML/JSON parsing errors are caught and logged; results may be partial.
- Deduplication stalls: ensure cleanup is called to release thread pools and caches.
- Pipeline ingestion errors: verify CanonicalFinding creation and ingest batch operations.

**Section sources**
- [academic_search.py:335-340](file://intelligence/academic_search.py#L335-L340)
- [academic_search.py:499-504](file://intelligence/academic_search.py#L499-L504)
- [academic_search.py:666-671](file://intelligence/academic_search.py#L666-L671)
- [deduplication.py:1052-1057](file://utils/deduplication.py#L1052-L1057)

## Conclusion
The Academic Intelligence module delivers a robust, extensible framework for academic search and discovery. Its multi-source querying, intelligent expansion, and hybrid deduplication produce high-quality, de-duplicated results suitable for integration into broader research workflows. The design emphasizes performance, reliability, and maintainability, enabling seamless pipeline integration and advanced research coordination.