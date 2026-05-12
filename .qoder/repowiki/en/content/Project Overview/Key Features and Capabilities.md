# Key Features and Capabilities

<cite>
**Referenced Files in This Document**
- [README.md](file://README.md)
- [M1_8GB_MEMORY_BUDGET.md](file://M1_8GB_MEMORY_BUDGET.md)
- [research_coordinator.py](file://coordinators/research_coordinator.py)
- [multimodal_coordinator.py](file://coordinators/multimodal_coordinator.py)
- [transport_resolver.py](file://transport/transport_resolver.py)
- [transport_tor.py](file://federated/transport_tor.py)
- [public_fetcher.py](file://fetching/public_fetcher.py)
- [darknet.py](file://tools/darknet.py)
- [synthesis_runner.py](file://brain/synthesis_runner.py)
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [stix_exporter.py](file://export/stix_exporter.py)
- [graph_layer.py](file://knowledge/graph_layer.py)
- [graph_rag.py](file://knowledge/graph_rag.py)
- [persistent_layer.py](file://legacy/persistent_layer.py)
- [stealth_layer.py](file://layers/stealth_layer.py)
- [stealth_manager.py](file://stealth/stealth_manager.py)
- [memory_layer.py](file://layers/memory_layer.py)
- [memory_coordinator.py](file://coordinators/memory_coordinator.py)
- [memory_pressure_broker.py](file://orchestrator/memory_pressure_broker.py)
- [identity_stitching_canonical.py](file://intelligence/identity_stitching_canonical.py)
- [REAL_ARCHITECTURE.md](file://REAL_ARCHITECTURE.md)
- [platform_info.py](file://utils/platform_info.py)
- [ane_pipelines.py](file://utils/ane_pipelines.py)
- [export/__init__.py](file://export/__init__.py)
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
This document presents Hledac Universal’s key autonomous research capabilities and operational strengths. It covers:
- Pattern-based content discovery and theory generation
- Memory-constrained operations optimized for 8 GB RAM on Apple Silicon
- Multi-protocol transport support (Tor, I2P, direct connections)
- AI/ML inference powered by MLX for Apple Silicon acceleration
- Knowledge graph construction and semantic search
- Multi-modal content processing (text, vision, audio)
- Export capabilities supporting STIX, JSON-LD, Markdown
- Canonical ownership model ensuring single-source-of-truth data consistency
- Memory management optimizations and stealth browsing capabilities

Practical examples illustrate what users can accomplish with each feature.

## Project Structure
Hledac Universal organizes capabilities across coordinated layers and specialized engines:
- Autonomous research orchestration and theory synthesis
- Transport resolution and fetching over Tor/I2P/direct
- Multimodal processing with MLX acceleration
- Knowledge graph construction and retrieval
- Export pipeline for diagnostics and CTI
- Memory governance and stealth browsing

```mermaid
graph TB
subgraph "Autonomy"
RC["ResearchCoordinator"]
SR["SynthesisRunner"]
end
subgraph "Transport"
TR["TransportResolver"]
TF["TorTransport"]
PF["PublicFetcher"]
DN["DarkNet Tools"]
end
subgraph "AI/ML"
MLX["MLX Acceleration"]
ANE["ANE Pipelines"]
end
subgraph "Knowledge Graph"
GL["GraphLayer"]
GR["GraphRAG"]
PL["PersistentLayer"]
end
subgraph "Export"
ES["ExportScheduler"]
SE["STIXExporter"]
end
subgraph "Runtime"
ML["MemoryLayer"]
MC["MemoryCoordinator"]
MPB["MemoryPressureBroker"]
end
subgraph "Stealth"
SL["StealthLayer"]
SM["StealthManager"]
end
RC --> SR
RC --> TR
TR --> TF
TR --> PF
TR --> DN
SR --> ES
ES --> SE
MLX --> GL
MLX --> GR
ANE --> GL
GL --> GR
GR --> SR
ML --> MC
ML --> MPB
SL --> SM
```

**Diagram sources**
- [research_coordinator.py](file://coordinators/research_coordinator.py)
- [synthesis_runner.py](file://brain/synthesis_runner.py)
- [transport_resolver.py](file://transport/transport_resolver.py)
- [transport_tor.py](file://federated/transport_tor.py)
- [public_fetcher.py](file://fetching/public_fetcher.py)
- [darknet.py](file://tools/darknet.py)
- [graph_layer.py](file://knowledge/graph_layer.py)
- [graph_rag.py](file://knowledge/graph_rag.py)
- [persistent_layer.py](file://legacy/persistent_layer.py)
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [stix_exporter.py](file://export/stix_exporter.py)
- [memory_layer.py](file://layers/memory_layer.py)
- [memory_coordinator.py](file://coordinators/memory_coordinator.py)
- [memory_pressure_broker.py](file://orchestrator/memory_pressure_broker.py)
- [stealth_layer.py](file://layers/stealth_layer.py)
- [stealth_manager.py](file://stealth/stealth_manager.py)

**Section sources**
- [README.md](file://README.md)

## Core Components
- Pattern-based discovery and theory generation: Detects meta-patterns from diverse sources and generates research theories.
- Multi-protocol transport: Resolves transports by URL suffix (.onion, .i2p) and selects anonymity-grade channels.
- MLX-accelerated AI/ML: Uses MLX and ANE for Apple Silicon–optimized inference and embeddings.
- Knowledge graph and semantic search: Builds and queries graphs with GraphRAG and semantic filters.
- Multi-modal processing: Encodes text, images, audio with MLX-backed encoders and fuses modalities.
- Export pipeline: Produces Markdown, JSON-LD, and STIX bundles for diagnostics and CTI.
- Canonical ownership model: Enforces single-source-of-truth data paths and bounded operations.
- Memory management: Strict memory limits, unload routines, and pressure-aware orchestration.
- Stealth browsing: Anti-detection headers, evasion scripts, and CAPTCHA solving.

**Section sources**
- [research_coordinator.py](file://coordinators/research_coordinator.py)
- [transport_resolver.py](file://transport/transport_resolver.py)
- [transport_tor.py](file://federated/transport_tor.py)
- [public_fetcher.py](file://fetching/public_fetcher.py)
- [darknet.py](file://tools/darknet.py)
- [multimodal_coordinator.py](file://coordinators/multimodal_coordinator.py)
- [graph_layer.py](file://knowledge/graph_layer.py)
- [graph_rag.py](file://knowledge/graph_rag.py)
- [persistent_layer.py](file://legacy/persistent_layer.py)
- [synthesis_runner.py](file://brain/synthesis_runner.py)
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [stix_exporter.py](file://export/stix_exporter.py)
- [identity_stitching_canonical.py](file://intelligence/identity_stitching_canonical.py)
- [M1_8GB_MEMORY_BUDGET.md](file://M1_8GB_MEMORY_BUDGET.md)
- [memory_layer.py](file://layers/memory_layer.py)
- [memory_coordinator.py](file://coordinators/memory_coordinator.py)
- [memory_pressure_broker.py](file://orchestrator/memory_pressure_broker.py)
- [stealth_layer.py](file://layers/stealth_layer.py)
- [stealth_manager.py](file://stealth/stealth_manager.py)
- [platform_info.py](file://utils/platform_info.py)
- [ane_pipelines.py](file://utils/ane_pipelines.py)

## Architecture Overview
The system integrates autonomous research, transport, AI/ML, knowledge graphs, and export into a cohesive pipeline. Canonical ownership ensures data integrity and deterministic behavior across modules.

```mermaid
sequenceDiagram
participant User as "User"
participant RC as "ResearchCoordinator"
participant TR as "TransportResolver"
participant PF as "PublicFetcher/Tor/I2P"
participant GL as "GraphLayer/GraphRAG"
participant SR as "SynthesisRunner"
participant ES as "ExportScheduler"
participant SE as "STIXExporter"
User->>RC : "Submit research query"
RC->>TR : "Resolve transport for URLs"
TR-->>RC : "TOR/I2P/DIRECT"
RC->>PF : "Fetch content via resolved transport"
PF-->>RC : "Content"
RC->>GL : "Build/Query knowledge graph"
GL-->>RC : "Entities/Relationships"
RC->>SR : "Aggregate findings"
SR->>ES : "Trigger export"
ES->>SE : "Render STIX/JSON-LD/Markdown"
SE-->>User : "Exported artifacts"
```

**Diagram sources**
- [research_coordinator.py](file://coordinators/research_coordinator.py)
- [transport_resolver.py](file://transport/transport_resolver.py)
- [public_fetcher.py](file://fetching/public_fetcher.py)
- [graph_layer.py](file://knowledge/graph_layer.py)
- [graph_rag.py](file://knowledge/graph_rag.py)
- [synthesis_runner.py](file://brain/synthesis_runner.py)
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [stix_exporter.py](file://export/stix_exporter.py)

## Detailed Component Analysis

### Pattern-Based Content Discovery and Theory Generation
- Detects meta-patterns from multiple sources and generates research theories.
- Enables deeper insights by combining pattern detection with theory synthesis.

```mermaid
flowchart TD
Start(["Start Research"]) --> Detect["Detect Patterns from Sources"]
Detect --> Generate["Generate Research Theories"]
Generate --> Refine["Refine and Rank Theories"]
Refine --> End(["Return Theories"])
```

**Diagram sources**
- [research_coordinator.py](file://coordinators/research_coordinator.py)

**Section sources**
- [research_coordinator.py](file://coordinators/research_coordinator.py)

### Multi-Protocol Transport Support (Tor, I2P, Direct)
- TransportResolver classifies URLs by suffix and selects TOR/I2P/DIRECT.
- PublicFetcher manages Tor/I2P sessions and closes them deterministically.
- DarkNet tools provide .onion and .i2p fetch helpers with fallbacks.

```mermaid
sequenceDiagram
participant TR as "TransportResolver"
participant PF as "PublicFetcher"
participant DN as "DarkNet Tools"
participant Site as "Target Site"
TR->>TR : "resolve_url(url)"
alt ".onion"
TR-->>PF : "TOR"
PF->>Site : "SOCKS5 via Tor"
else ".i2p"
TR-->>PF : "I2P"
PF->>Site : "SOCKS5 via I2P"
else "other"
TR-->>PF : "DIRECT"
PF->>Site : "Direct HTTP"
end
Note over PF,Site : "Session managed and closed safely"
```

**Diagram sources**
- [transport_resolver.py](file://transport/transport_resolver.py)
- [public_fetcher.py](file://fetching/public_fetcher.py)
- [darknet.py](file://tools/darknet.py)
- [transport_tor.py](file://federated/transport_tor.py)

**Section sources**
- [transport_resolver.py](file://transport/transport_resolver.py)
- [public_fetcher.py](file://fetching/public_fetcher.py)
- [darknet.py](file://tools/darknet.py)
- [transport_tor.py](file://federated/transport_tor.py)

### AI/ML Inference with MLX Acceleration (Apple Silicon)
- MLX availability is probed and used for accelerated operations.
- ANE pipelines compute safe batch sizes and manage SRAM budgets.
- MultimodalCoordinator uses MLX encoders for text, audio, and vision when available.

```mermaid
classDiagram
class PlatformInfo {
+probe_mlx() AccelerationStatus
}
class ANEPipelines {
+compute_safe_batch_size(seq_len, hidden, dtype_bytes) int
+get_hidden_size_from_model(model) int
}
class MultimodalCoordinator {
+encode_vision(image) ndarray
+encode_audio(audio) ndarray
+encode_text(text) ndarray
}
PlatformInfo --> MultimodalCoordinator : "availability"
ANEPipelines --> MultimodalCoordinator : "batch sizing"
```

**Diagram sources**
- [platform_info.py](file://utils/platform_info.py)
- [ane_pipelines.py](file://utils/ane_pipelines.py)
- [multimodal_coordinator.py](file://coordinators/multimodal_coordinator.py)

**Section sources**
- [platform_info.py](file://utils/platform_info.py)
- [ane_pipelines.py](file://utils/ane_pipelines.py)
- [multimodal_coordinator.py](file://coordinators/multimodal_coordinator.py)

### Knowledge Graph Construction and Semantic Search
- GraphLayer supports adding nodes and querying via GraphRAG multi-hop search.
- PersistentLayer offers semantic search using Model2Vec with memory-efficient top-K selection.
- GraphRAG orchestrates traversal and yields discovered nodes asynchronously.

```mermaid
sequenceDiagram
participant GL as "GraphLayer"
participant GR as "GraphRAG"
participant PL as "PersistentLayer"
participant User as "User"
User->>GL : "Add nodes/edges"
GL-->>User : "Success/Failure"
User->>GL : "Query"
GL->>GR : "multi_hop_search(query)"
GR-->>User : "Discovered nodes"
User->>PL : "Semantic search"
PL-->>User : "Top-K results"
```

**Diagram sources**
- [graph_layer.py](file://knowledge/graph_layer.py)
- [graph_rag.py](file://knowledge/graph_rag.py)
- [persistent_layer.py](file://legacy/persistent_layer.py)

**Section sources**
- [graph_layer.py](file://knowledge/graph_layer.py)
- [graph_rag.py](file://knowledge/graph_rag.py)
- [persistent_layer.py](file://legacy/persistent_layer.py)

### Multi-Modal Content Processing (Text, Vision, Audio)
- MultimodalCoordinator detects modalities automatically and processes them with MLX-backed encoders when available.
- Supports fusion of multiple modalities into a unified representation.

```mermaid
flowchart TD
A["Input Content"] --> B{"Modality Detected?"}
B -- "Text" --> T["Text Encoder (MLX)"]
B -- "Image" --> V["Vision Encoder (MLX)"]
B -- "Audio" --> A2["Audio Encoder (MLX)"]
B -- "Mixed" --> F["Cross-Modal Fusion"]
T --> F
V --> F
A2 --> F
F --> O["Unified Embedding"]
```

**Diagram sources**
- [multimodal_coordinator.py](file://coordinators/multimodal_coordinator.py)

**Section sources**
- [multimodal_coordinator.py](file://coordinators/multimodal_coordinator.py)

### Export Capabilities (STIX, JSON, Markdown)
- Export pipeline renders Markdown, JSON-LD, and STIX bundles.
- STIX exporter builds deterministic bundles and supports CTI exports.
- Sprint scheduler coordinates export runs and records outcomes.

```mermaid
sequenceDiagram
participant SR as "SynthesisRunner"
participant SS as "SprintScheduler"
participant EXP as "ExportManager"
participant MD as "MarkdownReporter"
participant JSONLD as "JSONLDExporter"
participant STIX as "STIXExporter"
SR->>SS : "_run_export()"
SS->>EXP : "_import_exporters()"
EXP-->>SS : "render_md, render_jsonld, render_stix"
SS->>MD : "report.md"
SS->>JSONLD : "report.jsonld"
SS->>STIX : "report.stix.json"
STIX-->>SS : "CTI STIX bundle"
```

**Diagram sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [export/__init__.py](file://export/__init__.py)
- [stix_exporter.py](file://export/stix_exporter.py)
- [synthesis_runner.py](file://brain/synthesis_runner.py)

**Section sources**
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [export/__init__.py](file://export/__init__.py)
- [stix_exporter.py](file://export/stix_exporter.py)
- [synthesis_runner.py](file://brain/synthesis_runner.py)

### Canonical Ownership Model and Single-Source-of-Truth Consistency
- IdentityStitchingCanonical adapter wraps the stitching engine with bounded comparisons and fail-soft behavior.
- Converts derived identities into CanonicalFinding objects and upserts advisory edges via graph_service.
- Ensures deterministic sidecar role and safe memory-bound operations.

```mermaid
flowchart TD
ESP["EntitySignalProfile list"] --> IS["IdentityStitchingEngine"]
IS --> IC["IdentityCandidate list"]
IC --> CF["CanonicalFinding list"]
CF --> AIB["async_ingest_findings_batch()"]
IC --> UE["upsert_identity_edges()"]
UE --> GS["graph_service"]
```

**Diagram sources**
- [identity_stitching_canonical.py](file://intelligence/identity_stitching_canonical.py)
- [REAL_ARCHITECTURE.md](file://REAL_ARCHITECTURE.md)

**Section sources**
- [identity_stitching_canonical.py](file://intelligence/identity_stitching_canonical.py)
- [REAL_ARCHITECTURE.md](file://REAL_ARCHITECTURE.md)

### Memory-Constrained Operations (8 GB RAM on Apple Silicon)
- Memory waterfall and bounds protect against macOS compression thresholds.
- Model lifecycle includes proper unload sequences with garbage collection and Metal cache clearing.
- MemoryLayer, MemoryCoordinator, and MemoryPressureBroker coordinate pressure-aware operations.

```mermaid
flowchart TD
Import["Import & Init"] --> LLM["Load LLM Weights"]
LLM --> NER["Load NER/ANE"]
NER --> Active["Active Scan"]
Active --> KV["KV Cache Quantization"]
KV --> Cleanup["Aggressive Cleanup (if needed)"]
Cleanup --> Import
```

**Diagram sources**
- [M1_8GB_MEMORY_BUDGET.md](file://M1_8GB_MEMORY_BUDGET.md)
- [memory_layer.py](file://layers/memory_layer.py)
- [memory_coordinator.py](file://coordinators/memory_coordinator.py)
- [memory_pressure_broker.py](file://orchestrator/memory_pressure_broker.py)

**Section sources**
- [M1_8GB_MEMORY_BUDGET.md](file://M1_8GB_MEMORY_BUDGET.md)
- [memory_layer.py](file://layers/memory_layer.py)
- [memory_coordinator.py](file://coordinators/memory_coordinator.py)
- [memory_pressure_broker.py](file://orchestrator/memory_pressure_broker.py)

### Stealth Browsing Capabilities
- StealthLayer initializes evasion scripts, CAPTCHA solving, and fingerprint randomization.
- StealthManager generates stealth headers and rotates them to reduce detection risk.

```mermaid
sequenceDiagram
participant SL as "StealthLayer"
participant SM as "StealthManager"
participant Site as "Target Site"
SL->>SL : "initialize()"
SL->>SM : "get_headers()"
SM-->>SL : "stealth headers"
SL->>Site : "Browse with evasion"
Site-->>SL : "CAPTCHA?"
SL->>SL : "solve_captcha()"
```

**Diagram sources**
- [stealth_layer.py](file://layers/stealth_layer.py)
- [stealth_manager.py](file://stealth/stealth_manager.py)

**Section sources**
- [stealth_layer.py](file://layers/stealth_layer.py)
- [stealth_manager.py](file://stealth/stealth_manager.py)

## Dependency Analysis
Key dependencies and integration points:
- TransportResolver depends on URL suffixes to choose transports.
- PublicFetcher and DarkNet tools depend on aiohttp_socks and Tor/I2P availability.
- GraphLayer/GraphRAG depend on knowledge graph backends.
- Export pipeline depends on renderers and STIX exporter.
- MemoryLayer and MemoryCoordinator depend on system memory metrics and pressure signals.

```mermaid
graph LR
TR["TransportResolver"] --> PF["PublicFetcher"]
TR --> DN["DarkNet Tools"]
PF --> GL["GraphLayer"]
GL --> GR["GraphRAG"]
SR["SynthesisRunner"] --> ES["ExportScheduler"]
ES --> SE["STIXExporter"]
ML["MemoryLayer"] --> MC["MemoryCoordinator"]
ML --> MPB["MemoryPressureBroker"]
SL["StealthLayer"] --> SM["StealthManager"]
```

**Diagram sources**
- [transport_resolver.py](file://transport/transport_resolver.py)
- [public_fetcher.py](file://fetching/public_fetcher.py)
- [darknet.py](file://tools/darknet.py)
- [graph_layer.py](file://knowledge/graph_layer.py)
- [graph_rag.py](file://knowledge/graph_rag.py)
- [synthesis_runner.py](file://brain/synthesis_runner.py)
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [stix_exporter.py](file://export/stix_exporter.py)
- [memory_layer.py](file://layers/memory_layer.py)
- [memory_coordinator.py](file://coordinators/memory_coordinator.py)
- [memory_pressure_broker.py](file://orchestrator/memory_pressure_broker.py)
- [stealth_layer.py](file://layers/stealth_layer.py)
- [stealth_manager.py](file://stealth/stealth_manager.py)

**Section sources**
- [transport_resolver.py](file://transport/transport_resolver.py)
- [public_fetcher.py](file://fetching/public_fetcher.py)
- [darknet.py](file://tools/darknet.py)
- [graph_layer.py](file://knowledge/graph_layer.py)
- [graph_rag.py](file://knowledge/graph_rag.py)
- [synthesis_runner.py](file://brain/synthesis_runner.py)
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
- [stix_exporter.py](file://export/stix_exporter.py)
- [memory_layer.py](file://layers/memory_layer.py)
- [memory_coordinator.py](file://coordinators/memory_coordinator.py)
- [memory_pressure_broker.py](file://orchestrator/memory_pressure_broker.py)
- [stealth_layer.py](file://layers/stealth_layer.py)
- [stealth_manager.py](file://stealth/stealth_manager.py)

## Performance Considerations
- MLX and ANE acceleration reduce latency and improve throughput on Apple Silicon.
- Memory bounds and quantized KV caches keep RSS within macOS compression thresholds.
- Async graph traversal and memory-efficient top-K selection minimize memory footprint.
- Deterministic export rendering and fail-soft export scheduling ensure robustness.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and mitigations:
- Tor/I2P sessions failing: Check availability of aiohttp_socks and Tor/I2P processes; fallback to localhost is logged.
- STIX export unavailable: Verify presence of export_stix_bundle on the injected graph; status and reason are recorded.
- Memory pressure warnings: Monitor pressure levels and trigger cleanup; ensure bounded allocations and unload routines are active.
- Stealth detection: Rotate headers, apply evasion scripts, and solve CAPTCHAs when prompted.

**Section sources**
- [public_fetcher.py](file://fetching/public_fetcher.py)
- [transport_tor.py](file://federated/transport_tor.py)
- [synthesis_runner.py](file://brain/synthesis_runner.py)
- [memory_pressure_broker.py](file://orchestrator/memory_pressure_broker.py)
- [stealth_layer.py](file://layers/stealth_layer.py)
- [stealth_manager.py](file://stealth/stealth_manager.py)

## Conclusion
Hledac Universal delivers a comprehensive autonomous research platform tailored for Apple Silicon with strict memory discipline. Its multi-protocol transport stack, MLX-accelerated AI/ML, robust knowledge graph, and multi-modal processing enable powerful discovery workflows. The canonical ownership model and memory governance ensure reliability and single-source-of-truth consistency, while stealth browsing and export capabilities support privacy and interoperability.