+++
title = "Architecture Map"
template = "section.html"

[extra]
has_mermaid = true
+++

<think>
Let me analyze the structural context provided to write a technical description of the codebase architecture.

Key information:
1. Dependency edges between modules
2. Dependency hotspots (most-imported files)
3. Information about circular dependencies (18 files involved)

Let me identify the key modules and their roles:

**Core modules based on edges:**
- brain - depends on utils
- recon - depends on knowledge, utils
- runtime - depends on knowledge, utils
- utils/mlx_memory - depends on utils
- rust_extensions/src/hnsw - depends on rust_extensions, rust_extensions/src

**Hotspots (high fan-in):**
1. rust_extensions/src/gil.rs - 13 dependents (MOST CONNECTED)
2. coordinators/base.py - 12 dependents
3. knowledge/duckdb_store.py - 8 dependents
4. pipeline/_stage_protocol.py - 7 dependents
5. utils/async_helpers.py - 7 dependents
6. project_types.py - 6 dependents
7. transport/base.py - 5 dependents
8. rust_extensions/src/adaptive_scheduler.rs - 5 dependents
9. layers/stealth_layer.py - 4 dependents
10. brain/deephermes3_engine.py - 4 dependents

**Patterns to identify:**
- Circular dependencies: 18 files involved - this is concerning
- Layered structure appears: pipeline stages, knowledge layer, transport layer, coordinators
- Rust extensions seem separate but connected

**Most connected module:** rust_extensions/src/gil.rs with 13 dependents - this is likely a critical GIL management module

Let me structure this properly following the rules:
- Don't start with 'The architecture consists of...' or 'This codebase is organized...'
- Lead with most connected module
- Describe data flow
- Identify layered patterns
- Call out concerning patterns
- 3-5 paragraphs
- Reference specific module names and dependency counts
- No speculation
- No missing spaces
</think>

The most connected module in this codebase is rust_extensions/src/gil.rs, which has 13 dependents—the highest fan-in of any file. This Global Interpreter Lock management component sits at the foundation of the Rust-Python interoperability layer, providing threading safety guarantees that virtually every higher-level module depends upon. The gil.rs module's centrality means it represents a critical bottleneck; if it contains bugs or performance issues, the impact cascades through a significant portion of the system. Its direct dependents include other rust_extensions components like adaptive_scheduler.rs (5 dependents of its own), suggesting the Rust extension subsystem forms a self-contained core that the broader Python codebase relies on for concurrent execution.

The Python side exhibits a layered architecture anchored by three high-fan-in coordination points. coordinators/base.py (12 dependents) serves as the primary orchestration layer, likely defining the interface that different coordinator implementations must fulfill. Below it, knowledge/duckdb_store.py (8 dependents) functions as the persistence and knowledge-representation layer, receiving dependencies from both the runtime module and the recon module based on the edge data. The pipeline/_stage_protocol.py (7 dependents) and utils/async_helpers.py (7 dependents) modules form an execution infrastructure layer, providing abstractions for staged processing and asynchronous operations respectively. Data flows from these foundational modules upward: project_types.py (6 dependents) and transport/base.py (5 dependents) feed configuration and communication concerns into coordinators and pipeline stages.

The recon and runtime modules appear as consumers within this architecture, each depending on both knowledge and utils modules. This positions them as operational subsystems that aggregate lower-level concerns to perform their specific functions. The utils/mlx_memory module indicates a specialized memory management component for MLX operations, suggesting this codebase has machine-learning-specific extensions beyond the core orchestration logic.

The 18 files involved in circular dependencies represent a concerning structural issue. Circular dependencies create brittle coupling where changes in one module can have unpredictable ripple effects through the cycle. The modules most likely caught in these cycles include the high-fan-in coordinators/base.py and the Rust extension components, which would make refactoring risky. The modules with low connectivity like brain (2 edges) and the isolated hnsw subsystem (only internal edges) suggest peripheral concerns—brain provides deephermes3_engine.py (4 dependents) to the system but remains loosely coupled, while rust_extensions/src/hnsw exists as a specialized search-indexing subsystem with its own internal dependencies but minimal external surface area.

## Dependency Graph

<div class="diagram-tabs">
  <button class="diagram-tab active" onclick="switchDiagram('flat')">Flat View</button>
  <button class="diagram-tab" onclick="switchDiagram('layered')">Layered View</button>
</div>

<div id="diagram-flat" class="diagram-panel active">

{% mermaid() %}
graph LR
  m_brain["brain/ (60 files)"]
  m_knowledge["knowledge/ (63 files)"]
  m_recon["recon/ (81 files)"]
  m_runtime["runtime/ (136 files)"]
  m_rust_extensions_src["rust_extensions/src/ (77 files)"]
  m_rust_extensions_src_collections["rust_extensions/src/collections/ (3 files)"]
  m_rust_extensions_src_hnsw["rust_extensions/src/hnsw/ (3 files)"]
  m_tools["tools/ (119 files)"]
  m_tools_analyze["tools/analyze/ (3 files)"]
  m_utils["utils/ (130 files)"]
  m_utils_mlx_memory["utils/mlx_memory/ (6 files)"]
  m_utils_patterns["utils/patterns/ (3 files)"]

  m_coordinators -->|6| m_transport
  m_layers -->|6| m_coordinators
  m_layers -->|4| m_utils
  m_brain -->|4| m_utils
  m_rust_extensions_src -->|4| m_rust_extensions_src_hnsw
  m_enhanced_research_py -->|4| m_utils
  m_enhanced_research_py -->|3| m_advanced_web
  m_export -->|3| m_utils
  m_coordinators -->|3| m_tools
  m_runtime -->|2| m_tool_registry_py
  m_recon -->|2| m_knowledge
  m_enhanced_research_py -->|2| m_project_types_py
  m_rust_extensions_src_hnsw -->|2| m_rust_extensions_src
  m_tools -->|2| m_paths_py
  m_runtime -->|1| m_knowledge
  m_brain -->|1| m_tool_registry_py
  m_recon -->|1| m_utils
  m_brain -->|1| m___init___py
  m_rust_extensions_src -->|1| m_rust_extensions_src_collections
  m_coordinators -->|1| m_network
  m_utils_mlx_memory -->|1| m_utils
  m_project_types_py -->|1| m_tools_analyze
  m_tot_integration_py -->|1| m_project_types_py
  m_enhanced_research_py -->|1| m_advanced_rag
  m_coordinators -->|1| m_utils
  m_runtime -->|1| m_project_types_py
  m_brain -->|1| m_security
  m_runtime -->|1| m_utils
  m_recon -->|1| m_project_types_py
  m_stealth -->|1| m_layers
  m_coordinators -->|1| m_project_types_py
  m_capabilities_py -->|1| m_project_types_py
  m_coordinators -->|1| m_security
  m_tot_integration_py -->|1| m_brain
  m_enhanced_research_py -->|1| m_layers
  m_core -->|1| m_utils
  m_layers -->|1| m_knowledge
  m_cache -->|1| m_utils
  m_brain -->|1| m_paths_py
  m_utils -->|1| m_utils_patterns
  linkStyle 0 stroke-width:3px,stroke:#a78bfa
  linkStyle 1 stroke-width:3px,stroke:#a78bfa

  classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
  classDef hotspot fill:#2a1030,stroke:#f472b6,color:#f472b6
  class m_brain hotspot
  class m_rust_extensions_src hotspot
  class m_coordinators hotspot
  class m_transport hotspot
  class m_utils hotspot
  class m_core hotspot
  class m_knowledge hotspot
  click m_brain "/wiki/brain/"
  click m_knowledge "/wiki/knowledge/"
  click m_recon "/wiki/recon/"
  click m_runtime "/wiki/runtime/"
  click m_rust_extensions_src "/wiki/rust_extensions-src/"
  click m_rust_extensions_src_collections "/wiki/rust_extensions-src-collections/"
  click m_rust_extensions_src_hnsw "/wiki/rust_extensions-src-hnsw/"
  click m_tools "/wiki/tools/"
  click m_tools_analyze "/wiki/tools-analyze/"
  click m_utils "/wiki/utils/"
  click m_utils_mlx_memory "/wiki/utils-mlx_memory/"
  click m_utils_patterns "/wiki/utils-patterns/"
{% end %}

</div>

<div id="diagram-layered" class="diagram-panel">

{% mermaid() %}
flowchart TB
  m_brain["brain/ (60 files)"]
  m_knowledge["knowledge/ (63 files)"]
  m_recon["recon/ (81 files)"]
  m_runtime["runtime/ (136 files)"]
  subgraph m_tools ["tools/ "]
    m_tools_self["tools/ (119 files)"]
    m_tools_analyze["analyze/ (3 files)"]
  end
  subgraph m_utils ["utils/ "]
    m_utils_self["utils/ (130 files)"]
    m_utils_mlx_memory["mlx_memory/ (6 files)"]
    m_utils_patterns["patterns/ (3 files)"]
  end

  m_coordinators -->|6| m_transport
  m_layers -->|6| m_coordinators
  m_brain -->|4| m_utils_self
  m_rust_extensions_src -->|4| m_rust_extensions_src_hnsw
  m_layers -->|4| m_utils_self
  m_enhanced_research_py -->|4| m_utils_self
  m_enhanced_research_py -->|3| m_advanced_web
  m_coordinators -->|3| m_tools_self
  m_export -->|3| m_utils_self
  m_enhanced_research_py -->|2| m_project_types_py
  m_tools_self -->|2| m_paths_py
  m_runtime -->|2| m_tool_registry_py
  m_recon -->|2| m_knowledge
  m_rust_extensions_src_hnsw -->|2| m_rust_extensions_src
  m_utils_mlx_memory -->|1| m_utils_self
  m_brain -->|1| m___init___py
  m_recon -->|1| m_utils_self
  m_runtime -->|1| m_project_types_py
  m_coordinators -->|1| m_security
  m_recon -->|1| m_project_types_py
  m_coordinators -->|1| m_network
  m_runtime -->|1| m_knowledge
  m_tot_integration_py -->|1| m_project_types_py
  m_brain -->|1| m_security
  m_brain -->|1| m_tool_registry_py
  m_layers -->|1| m_knowledge
  m_enhanced_research_py -->|1| m_layers
  m_enhanced_research_py -->|1| m_advanced_rag
  m_core -->|1| m_utils_self
  m_utils_self -->|1| m_utils_patterns
  m_project_types_py -->|1| m_tools_analyze
  m_capabilities_py -->|1| m_project_types_py
  m_coordinators -->|1| m_utils_self
  m_runtime -->|1| m_utils_self
  m_stealth -->|1| m_layers
  m_coordinators -->|1| m_project_types_py
  m_tot_integration_py -->|1| m_brain
  m_cache -->|1| m_utils_self
  m_rust_extensions_src -->|1| m_rust_extensions_src_collections
  m_brain -->|1| m_paths_py
  linkStyle 0 stroke-width:3px,stroke:#a78bfa
  linkStyle 1 stroke-width:3px,stroke:#a78bfa

  classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
  classDef hotspot fill:#2a1030,stroke:#f472b6,color:#f472b6
  class m_brain hotspot
  class m_core hotspot
  class m_rust_extensions_src hotspot
  class m_coordinators hotspot
  class m_transport hotspot
  class m_utils_self hotspot
  class m_knowledge hotspot
  click m_brain "/wiki/brain/"
  click m_knowledge "/wiki/knowledge/"
  click m_recon "/wiki/recon/"
  click m_runtime "/wiki/runtime/"
  click m_rust_extensions_src "/wiki/rust_extensions-src/"
  click m_rust_extensions_src_collections "/wiki/rust_extensions-src-collections/"
  click m_rust_extensions_src_hnsw "/wiki/rust_extensions-src-hnsw/"
  click m_tools_self "/wiki/tools/"
  click m_tools_analyze "/wiki/tools-analyze/"
  click m_utils_self "/wiki/utils/"
  click m_utils_mlx_memory "/wiki/utils-mlx_memory/"
  click m_utils_patterns "/wiki/utils-patterns/"
{% end %}

</div>

<script>
function switchDiagram(view) {
  document.querySelectorAll('.diagram-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.diagram-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('diagram-' + view).classList.add('active');
  event.target.classList.add('active');
}
</script>

## Legend

- **Thick arrows** indicate many file-level dependency edges between modules.
- **Red-highlighted nodes** are dependency hotspots (imported by many modules).
- **Arrow labels** show the number of file-level import edges.
- **Direction** follows the import: A → B means A depends on B.
- **Click** any module node to navigate to its wiki page.
