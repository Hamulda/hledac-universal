# Document Intelligence

<cite>
**Referenced Files in This Document**
- [document_intelligence.py](file://intelligence/document_intelligence.py)
- [streaming_embedder.py](file://intelligence/streaming_embedder.py)
- [content_layer.py](file://layers/content_layer.py)
- [embedding_pipeline.py](file://embedding_pipeline.py)
- [__init__.py](file://intelligence/__init__.py)
- [multimodal/analyzer.py](file://multimodal/analyzer.py)
- [graph_builder.py](file://knowledge/graph_builder.py)
- [graph_layer.py](file://knowledge/graph_layer.py)
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
This document describes the document intelligence module responsible for extracting, analyzing, and enriching textual and multimedia content from diverse document formats. It covers:
- Text extraction and content analysis for PDFs, Microsoft Office/OpenDocument formats, and images
- Forensic image analysis and suspicious content detection
- Progressive parsing with heuristic and optional semantic scoring
- Structured data extraction and content layer conversion for web content
- Real-time streaming embedding for scalable, memory-safe document processing
- Integration with the broader knowledge graph for semantic enrichment and retrieval

## Project Structure
The document intelligence system is organized around three primary areas:
- Document analysis engine: specialized analyzers for PDFs, Office/OpenDocument, and images
- Content processing: HTML-to-Markdown/JSON conversion for structured web content
- Embedding pipeline: streaming, memory-guarded embedding generation for semantic indexing

```mermaid
graph TB
subgraph "Document Intelligence"
DI["DocumentIntelligenceEngine"]
PDF["PDFAnalyzer"]
OFFICE["OfficeDocumentAnalyzer"]
IMG["ImageAnalyzer"]
DF["DeepForensicsAnalyzer"]
MLXLC["MLXLongContextAnalyzer"]
end
subgraph "Content Processing"
CC["ContentCleaner<br/>HTML→Markdown/JSON"]
end
subgraph "Embedding"
SE["StreamingEmbedder"]
EP["embedding_pipeline"]
end
subgraph "Knowledge Graph"
GB["GraphBuilder"]
GL["GraphLayer"]
end
DI --> PDF
DI --> OFFICE
DI --> IMG
IMG --> DF
DI --> MLXLC
CC --> DI
SE --> EP
DI --> SE
DI --> GB
GB --> GL
```

**Diagram sources**
- [document_intelligence.py:1277-1366](file://intelligence/document_intelligence.py#L1277-L1366)
- [content_layer.py:327-449](file://layers/content_layer.py#L327-L449)
- [streaming_embedder.py:60-204](file://intelligence/streaming_embedder.py#L60-L204)
- [embedding_pipeline.py:127-181](file://embedding_pipeline.py#L127-L181)
- [graph_builder.py:117-203](file://knowledge/graph_builder.py#L117-L203)
- [graph_layer.py:62-98](file://knowledge/graph_layer.py#L62-L98)

**Section sources**
- [__init__.py:186-208](file://intelligence/__init__.py#L186-L208)
- [document_intelligence.py:1277-1366](file://intelligence/document_intelligence.py#L1277-L1366)
- [content_layer.py:327-449](file://layers/content_layer.py#L327-L449)
- [streaming_embedder.py:60-204](file://intelligence/streaming_embedder.py#L60-L204)
- [embedding_pipeline.py:127-181](file://embedding_pipeline.py#L127-L181)
- [graph_builder.py:117-203](file://knowledge/graph_builder.py#L117-L203)
- [graph_layer.py:62-98](file://knowledge/graph_layer.py#L62-L98)

## Core Components
- DocumentIntelligenceEngine: Unified entry point for document analysis across formats, with progressive parsing and optional semantic scoring.
- PDFAnalyzer: Heuristic and deep parsing of PDFs, metadata extraction, embedded object discovery, and suspicious content detection.
- OfficeDocumentAnalyzer: ZIP-based OOXML analysis for modern Office formats and legacy OLE handling.
- ImageAnalyzer: EXIF/GPS extraction and image forensics integration.
- DeepForensicsAnalyzer: ELA and steganalysis for suspicious images.
- MLXLongContextAnalyzer: Memory-efficient, MLX-accelerated analysis of large documents with entity extraction, cross-document linking, and timeline reconstruction.
- ContentCleaner: HTML-to-Markdown/JSON conversion for structured web content.
- StreamingEmbedder: Async, chunked embedding pipeline with memory guards and fail-open behavior.
- embedding_pipeline: Singleton MLX-based embedder with MRL 256-dimension outputs and memory-pressure safeguards.

**Section sources**
- [document_intelligence.py:1277-1366](file://intelligence/document_intelligence.py#L1277-L1366)
- [document_intelligence.py:259-598](file://intelligence/document_intelligence.py#L259-L598)
- [document_intelligence.py:601-768](file://intelligence/document_intelligence.py#L601-L768)
- [document_intelligence.py:771-954](file://intelligence/document_intelligence.py#L771-L954)
- [document_intelligence.py:958-1275](file://intelligence/document_intelligence.py#L958-L1275)
- [document_intelligence.py:1593-2104](file://intelligence/document_intelligence.py#L1593-L2104)
- [content_layer.py:327-449](file://layers/content_layer.py#L327-L449)
- [streaming_embedder.py:60-204](file://intelligence/streaming_embedder.py#L60-L204)
- [embedding_pipeline.py:127-181](file://embedding_pipeline.py#L127-L181)

## Architecture Overview
The system integrates document ingestion, analysis, enrichment, and embedding into a cohesive pipeline. Progressive parsing determines whether to perform deep analysis based on content heuristics, optionally augmented by semantic similarity to a research query. Embedded findings are streamed to maintain memory safety on constrained hardware.

```mermaid
sequenceDiagram
participant Client as "Client"
participant Engine as "DocumentIntelligenceEngine"
participant Analyzer as "Analyzers"
participant Cleaner as "ContentCleaner"
participant Embedder as "StreamingEmbedder"
participant Pipeline as "embedding_pipeline"
participant KG as "GraphBuilder/GraphLayer"
Client->>Engine : analyze(file_path)
Engine->>Analyzer : dispatch by type
Analyzer-->>Engine : DocumentAnalysis
Engine->>Cleaner : clean_html(payload_text)
Cleaner-->>Engine : normalized text/markdown/json
Engine->>Embedder : embed_findings(CanonicalFinding[])
Embedder->>Pipeline : generate_embeddings()
Pipeline-->>Embedder : embeddings
Embedder-->>Engine : (ids, embeddings)
Engine->>KG : process_document()/add_entry()
KG-->>Engine : node/edge IDs
Engine-->>Client : enriched analysis + embeddings
```

**Diagram sources**
- [document_intelligence.py:1291-1365](file://intelligence/document_intelligence.py#L1291-L1365)
- [content_layer.py:420-448](file://layers/content_layer.py#L420-L448)
- [streaming_embedder.py:150-204](file://intelligence/streaming_embedder.py#L150-L204)
- [embedding_pipeline.py:425-497](file://embedding_pipeline.py#L425-L497)
- [graph_builder.py:117-203](file://knowledge/graph_builder.py#L117-L203)
- [graph_layer.py:62-98](file://knowledge/graph_layer.py#L62-L98)

## Detailed Component Analysis

### DocumentIntelligenceEngine
- Probes document previews to estimate value for progressive parsing.
- Supports heuristic scoring and optional semantic scoring using MLX when available.
- Routes to appropriate analyzer based on extension or magic bytes.
- Integrates image forensics and attaches raw metadata to analysis results.

```mermaid
flowchart TD
Start(["Probe Preview"]) --> Decode["Decode preview bytes"]
Decode --> Heuristic["Compute heuristic score"]
Heuristic --> QueryProvided{"Query provided<br/>and MLX available?"}
QueryProvided --> |Yes| Semantic["Compute semantic similarity"]
QueryProvided --> |No| Final["Final score = heuristic"]
Semantic --> Blend["Blend scores (50/50)"]
Blend --> Final
Final --> Keywords["Extract keywords"]
Keywords --> End(["Return probe result"])
```

**Diagram sources**
- [document_intelligence.py:1382-1432](file://intelligence/document_intelligence.py#L1382-L1432)

**Section sources**
- [document_intelligence.py:1382-1432](file://intelligence/document_intelligence.py#L1382-L1432)
- [document_intelligence.py:1291-1365](file://intelligence/document_intelligence.py#L1291-L1365)

### PDFAnalyzer
- Heuristic probing selects candidate pages; deep parsing performed only when signal is strong.
- Extracts metadata, embedded objects, hyperlinks, emails, IPs, and suspicious indicators.
- Falls back to basic analysis without PyMuPDF.

```mermaid
classDiagram
class PDFAnalyzer {
+analyze(file_path) DocumentAnalysis
-_probe_pdf(doc) dict
-_deep_parse_pages(doc, pages) str[]
-_extract_pdf_metadata(doc, path) DocumentMetadata
-_extract_pdf_objects(doc) EmbeddedObject[]
-_detect_suspicious_content(text) str[]
-_basic_pdf_analysis(file_path) DocumentAnalysis
}
```

**Diagram sources**
- [document_intelligence.py:259-598](file://intelligence/document_intelligence.py#L259-L598)

**Section sources**
- [document_intelligence.py:259-598](file://intelligence/document_intelligence.py#L259-L598)

### OfficeDocumentAnalyzer
- Detects OOXML vs legacy OLE via ZIP signature.
- Extracts core properties, comments, hyperlinks, and media attachments.

**Section sources**
- [document_intelligence.py:601-768](file://intelligence/document_intelligence.py#L601-L768)

### ImageAnalyzer and DeepForensicsAnalyzer
- ImageAnalyzer: EXIF/GPS extraction and metadata normalization.
- DeepForensicsAnalyzer: ELA and steganalysis with MPS acceleration and fallback to CPU; integrates with graph for flagged anomalies.

```mermaid
sequenceDiagram
participant Engine as "DocumentIntelligenceEngine"
participant Img as "ImageAnalyzer"
participant DF as "DeepForensicsAnalyzer"
Engine->>Img : analyze(image_path)
Img-->>Engine : DocumentAnalysis(exif_data)
Engine->>DF : analyze_image(bytes)
DF-->>Engine : forensics report (ela_score, suspicious)
Engine-->>Engine : attach forensics to metadata
```

**Diagram sources**
- [document_intelligence.py:771-954](file://intelligence/document_intelligence.py#L771-L954)
- [document_intelligence.py:958-1275](file://intelligence/document_intelligence.py#L958-L1275)

**Section sources**
- [document_intelligence.py:771-954](file://intelligence/document_intelligence.py#L771-L954)
- [document_intelligence.py:958-1275](file://intelligence/document_intelligence.py#L958-L1275)

### MLXLongContextAnalyzer
- Chunks large texts with overlap, computes embeddings (MLX when available), extracts entities, cross-references across documents, and reconstructs timelines.
- Designed for M1 8GB memory constraints with streaming and lazy evaluation.

```mermaid
flowchart TD
A["Input text"] --> B["Chunk text with overlap"]
B --> C{"MLX available?"}
C --> |Yes| D["Compute embeddings (MLX)"]
C --> |No| E["Skip embeddings"]
D --> F["Extract entities"]
E --> F
F --> G["Cross-reference entities"]
F --> H["Reconstruct timeline"]
G --> I["Aggregate LongContextAnalysis"]
H --> I
```

**Diagram sources**
- [document_intelligence.py:1930-2104](file://intelligence/document_intelligence.py#L1930-L2104)

**Section sources**
- [document_intelligence.py:1593-2104](file://intelligence/document_intelligence.py#L1593-L2104)

### ContentCleaner (Content Layer)
- Converts HTML to Markdown/JSON using BeautifulSoup fallback; includes lightweight URL cleaning and search result parsers for DuckDuckGo/Google.

```mermaid
classDiagram
class ContentCleaner {
+clean_html(raw_html, output_format) CleaningResult
+clean_html_batch(html_list, output_format) CleaningResult[]
-_simple_cleaner SimpleHTMLCleaner
}
class SimpleHTMLCleaner {
+clean(html, output_format) CleaningResult
-_to_markdown(soup) str
-_to_json(soup) str
}
ContentCleaner --> SimpleHTMLCleaner : "fallback"
```

**Diagram sources**
- [content_layer.py:327-449](file://layers/content_layer.py#L327-L449)
- [content_layer.py:54-213](file://layers/content_layer.py#L54-L213)

**Section sources**
- [content_layer.py:327-449](file://layers/content_layer.py#L327-L449)
- [content_layer.py:54-213](file://layers/content_layer.py#L54-L213)

### StreamingEmbedder and embedding_pipeline
- StreamingEmbedder: Async, chunked embedding with memory guards, fetch semaphore limits, and fail-open behavior.
- embedding_pipeline: Singleton MLX embedder with MRL 256-d embeddings, memory-pressure checks, and streaming batch support.

```mermaid
sequenceDiagram
participant SE as "StreamingEmbedder"
participant EP as "embedding_pipeline"
participant Model as "MLXEmbeddingManager"
SE->>SE : _ram_guard_ok()
SE->>EP : load_embedding_model()
EP->>Model : _load_model()
SE->>SE : _embed_chunked(findings, batch_size)
SE->>EP : generate_embeddings(texts)
EP->>Model : encode(..., truncate_dim=256)
Model-->>EP : embeddings
EP-->>SE : embeddings
SE-->>SE : yield (ids, embeddings)
SE->>EP : unload_embedding_model()
```

**Diagram sources**
- [streaming_embedder.py:150-204](file://intelligence/streaming_embedder.py#L150-L204)
- [embedding_pipeline.py:425-497](file://embedding_pipeline.py#L425-L497)
- [embedding_pipeline.py:333-413](file://embedding_pipeline.py#L333-L413)

**Section sources**
- [streaming_embedder.py:60-204](file://intelligence/streaming_embedder.py#L60-L204)
- [embedding_pipeline.py:127-181](file://embedding_pipeline.py#L127-L181)
- [embedding_pipeline.py:425-497](file://embedding_pipeline.py#L425-L497)

### Knowledge Graph Integration
- GraphBuilder processes content and stores facts with nodes and edges.
- GraphLayer exposes add_entry mapped to knowledge graph storage.

```mermaid
sequenceDiagram
participant DI as "DocumentIntelligenceEngine"
participant GB as "GraphBuilder"
participant GL as "GraphLayer"
DI->>GB : process_document(document, url, author)
GB-->>DI : node_ids
DI->>GL : add_entry(url, content, title, keywords, metadata)
GL-->>DI : success/failure
```

**Diagram sources**
- [graph_builder.py:117-203](file://knowledge/graph_builder.py#L117-L203)
- [graph_layer.py:62-98](file://knowledge/graph_layer.py#L62-L98)

**Section sources**
- [graph_builder.py:117-203](file://knowledge/graph_builder.py#L117-L203)
- [graph_layer.py:62-98](file://knowledge/graph_layer.py#L62-L98)

## Dependency Analysis
- DocumentIntelligenceEngine depends on analyzers and optional MLX/forensics modules.
- ContentCleaner is decoupled and used by downstream processors.
- StreamingEmbedder relies on embedding_pipeline for model lifecycle and embeddings.
- GraphBuilder/GraphLayer integrate extracted content into the knowledge graph.

```mermaid
graph LR
DI["DocumentIntelligenceEngine"] --> PDF["PDFAnalyzer"]
DI --> OFFICE["OfficeDocumentAnalyzer"]
DI --> IMG["ImageAnalyzer"]
IMG --> DF["DeepForensicsAnalyzer"]
DI --> CC["ContentCleaner"]
DI --> SE["StreamingEmbedder"]
SE --> EP["embedding_pipeline"]
DI --> GB["GraphBuilder"]
GB --> GL["GraphLayer"]
```

**Diagram sources**
- [document_intelligence.py:1277-1366](file://intelligence/document_intelligence.py#L1277-L1366)
- [content_layer.py:327-449](file://layers/content_layer.py#L327-L449)
- [streaming_embedder.py:60-204](file://intelligence/streaming_embedder.py#L60-L204)
- [embedding_pipeline.py:127-181](file://embedding_pipeline.py#L127-L181)
- [graph_builder.py:117-203](file://knowledge/graph_builder.py#L117-L203)
- [graph_layer.py:62-98](file://knowledge/graph_layer.py#L62-L98)

**Section sources**
- [__init__.py:186-208](file://intelligence/__init__.py#L186-L208)
- [document_intelligence.py:1277-1366](file://intelligence/document_intelligence.py#L1277-L1366)

## Performance Considerations
- Progressive parsing: Heuristic probing minimizes deep parsing costs for low-signal documents.
- Memory guards: StreamingEmbedder and embedding_pipeline enforce strict RSS thresholds to prevent OOM on M1 8GB systems.
- Streaming batches: Yielding embeddings per chunk reduces peak memory usage.
- MLX acceleration: Where available, MLXLongContextAnalyzer and forensics leverage GPU/CPU acceleration with MPS fallback.
- Concurrency limits: Document extraction limits concurrent operations to maintain stability on constrained devices.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
- Missing optional dependencies:
  - PIL/piexif/PDF support: If unavailable, analyzers fall back to basic modes; image and PDF analysis may be limited.
  - MLX: If unavailable, semantic scoring and MLX accelerations are disabled; the system continues with CPU-only paths.
- Memory pressure:
  - StreamingEmbedder and embedding_pipeline skip embedding when RSS exceeds thresholds; consider reducing batch sizes or freeing memory elsewhere.
- Forensics failures:
  - ELA/stegdetect may fall back to CPU or return conservative results; verify external binaries and permissions.
- Content cleaning:
  - BeautifulSoup fallback is used when MLX-based cleaners are not available; expect slower performance but functional cleaning.

**Section sources**
- [document_intelligence.py:44-112](file://intelligence/document_intelligence.py#L44-L112)
- [streaming_embedder.py:131-144](file://intelligence/streaming_embedder.py#L131-L144)
- [embedding_pipeline.py:90-114](file://embedding_pipeline.py#L90-L114)
- [content_layer.py:66-73](file://layers/content_layer.py#L66-L73)

## Conclusion
The document intelligence module provides a robust, memory-conscious pipeline for analyzing heterogeneous documents, extracting structured insights, and generating embeddings for semantic search and knowledge graph integration. Its progressive parsing, streaming embedder, and content layer ensure scalability and reliability across varied workloads and hardware constraints.