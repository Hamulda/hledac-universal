# Plugin System Architecture

<cite>
**Referenced Files in This Document**
- [plugin_manager.py](file://infrastructure/plugin_manager.py)
- [coordinator_registry.py](file://coordinators/coordinator_registry.py)
- [base.py](file://coordinators/base.py)
- [research_coordinator.py](file://coordinators/research_coordinator.py)
- [transport/base.py](file://transport/base.py)
- [transport_resolver.py](file://transport/transport_resolver.py)
- [vector_store.py](file://knowledge/vector_store.py)
- [academic_search.py](file://intelligence/academic_search.py)
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
10. [Appendices](#appendices)

## Introduction
This document describes the plugin system architecture in Hledac Universal, focusing on the dynamic plugin loading framework, coordinator registry mechanisms, and extensibility patterns. It explains the plugin lifecycle, registration processes, and dependency injection patterns. It also provides practical guidance for developing custom plugins for transport layers, storage backends, and intelligence modules, along with configuration management, runtime loading, isolation, error handling, and performance considerations.

## Project Structure
The plugin system spans several subsystems:
- Infrastructure: Dynamic plugin loading and lifecycle management
- Coordinators: Extensible coordinator registry and base interfaces
- Transport: Pluggable transport abstractions and resolver
- Intelligence: Modular intelligence modules (e.g., academic search)
- Knowledge: Storage backends (e.g., vector stores)

```mermaid
graph TB
subgraph "Infrastructure"
PM["PluginManager<br/>Dynamic Loading"]
end
subgraph "Coordinators"
CR["CoordinatorRegistry<br/>Registration & Routing"]
UC["UniversalCoordinator<br/>Base Interface"]
end
subgraph "Transport"
TR["TransportResolver<br/>Autonomous Selection"]
TIF["Transport Interface<br/>Base"]
end
subgraph "Intelligence"
AS["AcademicSearchEngine<br/>Multi-Source Adapters"]
end
subgraph "Knowledge"
VS["VectorStore<br/>LanceDB Backend"]
end
PM --> CR
CR --> UC
TR --> TIF
AS --> VS
```

**Diagram sources**
- [plugin_manager.py:91-462](file://infrastructure/plugin_manager.py#L91-L462)
- [coordinator_registry.py:49-602](file://coordinators/coordinator_registry.py#L49-L602)
- [base.py:88-553](file://coordinators/base.py#L88-L553)
- [transport_resolver.py:95-361](file://transport/transport_resolver.py#L95-L361)
- [transport/base.py:4-24](file://transport/base.py#L4-L24)
- [vector_store.py:44-308](file://knowledge/vector_store.py#L44-L308)
- [academic_search.py:797-1379](file://intelligence/academic_search.py#L797-L1379)

**Section sources**
- [plugin_manager.py:1-462](file://infrastructure/plugin_manager.py#L1-L462)
- [coordinator_registry.py:1-602](file://coordinators/coordinator_registry.py#L1-L602)
- [base.py:1-553](file://coordinators/base.py#L1-L553)
- [transport_resolver.py:1-361](file://transport/transport_resolver.py#L1-L361)
- [transport/base.py:1-24](file://transport/base.py#L1-L24)
- [vector_store.py:1-308](file://knowledge/vector_store.py#L1-L308)
- [academic_search.py:1-1379](file://intelligence/academic_search.py#L1-L1379)

## Core Components
This section outlines the central building blocks of the plugin system.

- PluginManager: Centralized dynamic plugin loader with discovery, instantiation, lifecycle hooks, and hot-reload support.
- CoordinatorRegistry: Manages UniversalCoordinator instances, supports registration, routing strategies, health monitoring, and statistics.
- UniversalCoordinator: Base interface for coordinators with operation lifecycle, load management, memory awareness, and metrics.
- TransportResolver: Autonomous transport selection based on URL classification and runtime context.
- VectorStore: Pluggable storage backend with lazy initialization and streaming batch operations.
- AcademicSearchEngine: Example intelligence module with pluggable adapters and query expansion.

**Section sources**
- [plugin_manager.py:91-462](file://infrastructure/plugin_manager.py#L91-L462)
- [coordinator_registry.py:49-602](file://coordinators/coordinator_registry.py#L49-L602)
- [base.py:88-553](file://coordinators/base.py#L88-L553)
- [transport_resolver.py:95-361](file://transport/transport_resolver.py#L95-L361)
- [vector_store.py:44-308](file://knowledge/vector_store.py#L44-L308)
- [academic_search.py:797-1379](file://intelligence/academic_search.py#L797-L1379)

## Architecture Overview
The plugin system integrates three pillars:
- Dynamic Plugin Loading: Loads external Python modules, validates signatures, instantiates plugins, and triggers lifecycle hooks.
- Coordinator Registry: Provides a centralized registry for UniversalCoordinator implementations with routing strategies and health monitoring.
- Extensibility Patterns: Interfaces for transport, intelligence, and storage enable modular development and runtime composition.

```mermaid
sequenceDiagram
participant App as "Application"
participant PM as "PluginManager"
participant Mod as "Plugin Module"
participant Inst as "Plugin Instance"
App->>PM : discover_plugins()
PM->>PM : scan plugin_dir for metadata
PM-->>App : discovered metadata list
App->>PM : load_plugin(metadata)
PM->>PM : _load_module(metadata)
PM->>Mod : importlib.util.spec_from_file_location(...)
Mod-->>PM : module object
PM->>PM : _instantiate_plugin(module, metadata)
PM->>Inst : instantiate Plugin() or return module
PM->>Inst : on_load() hook (if available)
PM-->>App : LoadedPlugin registered
```

**Diagram sources**
- [plugin_manager.py:120-278](file://infrastructure/plugin_manager.py#L120-L278)

**Section sources**
- [plugin_manager.py:120-278](file://infrastructure/plugin_manager.py#L120-L278)

## Detailed Component Analysis

### PluginManager: Dynamic Plugin Loading
The PluginManager provides:
- Plugin discovery from directory-based or single-file plugins
- Metadata extraction from plugin.json or module-level hints
- Secure module loading with optional signature validation
- Instantiation of plugin classes or modules
- Lifecycle hooks (on_load, on_unload)
- Hot-reload support and statistics

```mermaid
classDiagram
class PluginStatus {
<<enum>>
LOADING
LOADED
ERROR
DISABLED
UNLOADING
}
class PluginType {
<<enum>>
AGENT
DRIVER
SERVICE
UTILITY
INTEGRATION
}
class PluginMetadata {
+string name
+string version
+string description
+string author
+PluginType plugin_type
+string entry_point
+string[] dependencies
+string signature
+string[] permissions
+Dict config_schema
}
class LoadedPlugin {
+PluginMetadata metadata
+Any module
+Any instance
+PluginStatus status
+float load_time
+string error_message
}
class PluginManager {
+Dict~string,LoadedPlugin~ plugins
+Dict~string,Callable[]~ hooks
+discover_plugins() PluginMetadata[]
+load_plugin(metadata) bool
+unload_plugin(name) bool
+reload_plugin(name) bool
+get_plugin(name) LoadedPlugin
+list_plugins() PluginMetadata[]
+register_hook(event, callback) void
+get_stats() Dict~string,Any~
-_load_module(metadata) Any
-_instantiate_plugin(module, metadata) Any
-_validate_signature(module, signature) bool
-_trigger_hooks(event, ...)
}
PluginManager --> PluginMetadata : "manages"
PluginManager --> LoadedPlugin : "stores"
PluginMetadata --> PluginType : "uses"
LoadedPlugin --> PluginStatus : "has"
```

**Diagram sources**
- [plugin_manager.py:47-462](file://infrastructure/plugin_manager.py#L47-L462)

**Section sources**
- [plugin_manager.py:91-462](file://infrastructure/plugin_manager.py#L91-L462)

### CoordinatorRegistry: Coordinator Management
The CoordinatorRegistry:
- Registers UniversalCoordinator instances with priority and weight
- Routes operations to appropriate coordinators using multiple strategies
- Monitors health and maintains statistics
- Supports default coordinator assignment per operation type

```mermaid
sequenceDiagram
participant Client as "Client"
participant CR as "CoordinatorRegistry"
participant Coord as "UniversalCoordinator"
Client->>CR : register(coordinator, priority, weight)
CR->>Coord : initialize() (if needed)
CR-->>Client : registration confirmed
Client->>CR : route_operation(op_type, op_ref, decision, strategy)
CR->>CR : select coordinator (priority/load/weighted/auto)
CR->>Coord : handle_request(op_ref, decision)
Coord-->>CR : OperationResult
CR-->>Client : OperationResult
```

**Diagram sources**
- [coordinator_registry.py:79-231](file://coordinators/coordinator_registry.py#L79-L231)
- [base.py:149-164](file://coordinators/base.py#L149-L164)

**Section sources**
- [coordinator_registry.py:49-602](file://coordinators/coordinator_registry.py#L49-L602)
- [base.py:88-553](file://coordinators/base.py#L88-L553)

### UniversalCoordinator: Base Interface
The UniversalCoordinator defines:
- Operation lifecycle: generate_operation_id, track_operation, untrack_operation
- Load management: get_load_factor, can_accept_operation, capacity info
- Memory awareness: update_memory_pressure, check_memory_pressure
- Metrics: record_operation_result, get_metrics
- Stable spine interface: start, step, shutdown

```mermaid
classDiagram
class OperationType {
<<enum>>
RESEARCH
EXECUTION
SECURITY
MONITORING
SYNTHESIS
OPTIMIZATION
}
class UniversalCoordinator {
-string _name
-int _max_concurrent
-bool _memory_aware
-Dict~string,Dict~string,Any~~ _active_operations
-int _operation_counter
-OrderedDict~string,Dict~string,Any~~ _operation_history
+initialize() bool
+cleanup() void
+generate_operation_id() string
+track_operation(id, data) void
+untrack_operation(id) void
+get_load_factor() float
+can_accept_operation(priority) bool
+update_memory_pressure(level) void
+record_operation_result(result) void
+get_metrics() Dict~string,Any~
+start(ctx) void
+step(ctx) Dict~string,Any~
+shutdown(ctx) void
#_do_initialize() bool
#_do_cleanup() void
#_do_start(ctx) void
#_do_step(ctx) Dict~string,Any~
#_do_shutdown(ctx) void
}
class DecisionResponse {
+string decision_id
+string chosen_option
+float confidence
+string reasoning
+float estimated_duration
+int priority
+Dict metadata
}
class OperationResult {
+string operation_id
+string status
+string result_summary
+float execution_time
+bool success
+string error_message
+Dict metadata
+float timestamp
}
UniversalCoordinator --> OperationType : "supports"
UniversalCoordinator --> DecisionResponse : "receives"
UniversalCoordinator --> OperationResult : "returns"
```

**Diagram sources**
- [base.py:33-553](file://coordinators/base.py#L33-L553)

**Section sources**
- [base.py:88-553](file://coordinators/base.py#L88-L553)

### TransportResolver: Pluggable Transport Selection
The TransportResolver:
- Classifies URLs into transport domains (.onion, .i2p, .freenet, direct)
- Autonomously selects transports based on context (anonymity, risk)
- Imports transport implementations lazily
- Provides policy gates for URL classification

```mermaid
flowchart TD
Start(["Resolve Transport"]) --> CheckSuffix["Extract Host Suffix"]
CheckSuffix --> IsOnion{".onion?"}
IsOnion --> |.onion| UseTor["Use TOR Transport"]
IsOnion --> |.i2p| UseI2P["Use I2P Transport"]
IsOnion --> |Other| UseDirect["Use DIRECT Transport"]
UseTor --> CheckAvailability["Check Transport Availability"]
UseI2P --> CheckAvailability
UseDirect --> CheckAvailability
CheckAvailability --> HasTransport{"Transport Available?"}
HasTransport --> |Yes| ReturnTransport["Return Transport"]
HasTransport --> |No| Fallback["Log Warning / Fallback Behavior"]
Fallback --> ReturnNone["Return None"]
```

**Diagram sources**
- [transport_resolver.py:152-239](file://transport/transport_resolver.py#L152-L239)

**Section sources**
- [transport_resolver.py:95-361](file://transport/transport_resolver.py#L95-L361)
- [transport/base.py:4-24](file://transport/base.py#L4-L24)

### VectorStore: Pluggable Storage Backend
The VectorStore:
- Provides a singleton interface backed by LanceDB
- Supports separate indices for text and image embeddings
- Implements lazy initialization and streaming batch operations
- Validates dimensions and normalizes data types

```mermaid
classDiagram
class VectorStore {
-Any _db
-Any _text_table
-Any _image_table
-bool _initialized
+add_vectors(ids, vectors, index_type) void
+add_vectors_streaming(ids, vectors, index_type, batch_size) void
+query(vector, k, index_type) Tuple[]string,float~~
+close() void
}
class Singleton {
+get_vector_store() VectorStore
}
VectorStore <.. Singleton : "uses"
```

**Diagram sources**
- [vector_store.py:44-308](file://knowledge/vector_store.py#L44-L308)

**Section sources**
- [vector_store.py:44-308](file://knowledge/vector_store.py#L44-L308)

### AcademicSearchEngine: Intelligence Module with Adapters
The AcademicSearchEngine demonstrates:
- Pluggable source adapters (ArXiv, Crossref, Semantic Scholar)
- Query expansion and deduplication
- Performance tracking per source
- Shared session management for HTTP requests

```mermaid
classDiagram
class AcademicSearchEngine {
+search(query, max_results) AcademicSearchResult
+execute_multi_source_search(...) AcademicSearchResult
}
class BaseSourceAdapter {
<<abstract>>
+search(query, max_results, analysis) SearchResult[]
+execute_search(query, ...) Tuple~SearchResult[],float,bool~
+get_performance() SourcePerformance
}
class ArxivAdapter
class CrossrefAdapter
class SemanticScholarAdapter
AcademicSearchEngine --> BaseSourceAdapter : "composes"
BaseSourceAdapter <|-- ArxivAdapter
BaseSourceAdapter <|-- CrossrefAdapter
BaseSourceAdapter <|-- SemanticScholarAdapter
```

**Diagram sources**
- [academic_search.py:797-1379](file://intelligence/academic_search.py#L797-L1379)

**Section sources**
- [academic_search.py:797-1379](file://intelligence/academic_search.py#L797-L1379)

## Dependency Analysis
The plugin system exhibits low coupling and high cohesion:
- PluginManager depends on importlib and filesystem scanning
- CoordinatorRegistry depends on UniversalCoordinator interface
- TransportResolver depends on optional transport implementations
- VectorStore depends on LanceDB/pyarrow
- Intelligence modules depend on shared utilities and HTTP sessions

```mermaid
graph TB
PM["PluginManager"] --> FS["Filesystem"]
PM --> IM["importlib"]
CR["CoordinatorRegistry"] --> UC["UniversalCoordinator"]
TR["TransportResolver"] --> TImpl["Transport Implementations"]
VS["VectorStore"] --> LDB["LanceDB"]
AS["AcademicSearchEngine"] --> HTTP["aiohttp Sessions"]
```

**Diagram sources**
- [plugin_manager.py:29-42](file://infrastructure/plugin_manager.py#L29-L42)
- [coordinator_registry.py:27-34](file://coordinators/coordinator_registry.py#L27-L34)
- [transport_resolver.py:134-150](file://transport/transport_resolver.py#L134-L150)
- [vector_store.py:70-120](file://knowledge/vector_store.py#L70-L120)
- [academic_search.py:35-48](file://intelligence/academic_search.py#L35-L48)

**Section sources**
- [plugin_manager.py:29-42](file://infrastructure/plugin_manager.py#L29-L42)
- [coordinator_registry.py:27-34](file://coordinators/coordinator_registry.py#L27-L34)
- [transport_resolver.py:134-150](file://transport/transport_resolver.py#L134-L150)
- [vector_store.py:70-120](file://knowledge/vector_store.py#L70-L120)
- [academic_search.py:35-48](file://intelligence/academic_search.py#L35-L48)

## Performance Considerations
- PluginManager
  - Uses importlib for efficient module loading
  - Thread-safe locking around registry operations
  - Hot-reload minimizes downtime during updates
- CoordinatorRegistry
  - Asynchronous locks for concurrent access
  - Weighted and priority-based selection reduces contention
  - Memory-aware load factor prevents overload on constrained systems
- TransportResolver
  - Fast suffix-based classification avoids network calls
  - Lazy import of transport implementations reduces startup overhead
- VectorStore
  - Streaming batch adds reduce peak memory usage on M1 systems
  - Lazy initialization defers expensive operations until needed
- AcademicSearchEngine
  - Shared HTTP sessions reduce connection overhead
  - Performance tracking per source enables adaptive routing

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Plugin loading failures
  - Verify plugin.json or module-level metadata
  - Check entry_point path and module availability
  - Review signature validation and permissions
- Coordinator registration errors
  - Ensure initialize() succeeds and coordinator is available
  - Confirm supported operations match routing expectations
- Transport selection problems
  - Validate URL suffix classification
  - Confirm transport implementations are importable
- Storage backend issues
  - Ensure LanceDB installation and write permissions
  - Check dimension mismatches and data normalization
- Intelligence module errors
  - Verify API keys and rate limits
  - Monitor adapter-specific timeouts and error logs

**Section sources**
- [plugin_manager.py:238-277](file://infrastructure/plugin_manager.py#L238-L277)
- [coordinator_registry.py:98-130](file://coordinators/coordinator_registry.py#L98-L130)
- [transport_resolver.py:134-150](file://transport/transport_resolver.py#L134-L150)
- [vector_store.py:115-120](file://knowledge/vector_store.py#L115-L120)
- [academic_search.py:345-351](file://intelligence/academic_search.py#L345-L351)

## Conclusion
Hledac Universal’s plugin system combines dynamic loading, coordinator orchestration, and pluggable interfaces to enable flexible, extensible architectures. The PluginManager, CoordinatorRegistry, TransportResolver, VectorStore, and intelligence modules collectively provide a robust foundation for building custom plugins across transport, storage, and intelligence domains while maintaining isolation, reliability, and performance.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Step-by-Step: Creating a Custom Plugin (Transport Layer)
1. Define a transport class implementing the Transport interface.
2. Implement start, stop, wait_ready, register_handler, and send_message.
3. Optionally integrate with session management and lifecycle controls.
4. Package the transport as a standalone module or directory with plugin.json metadata.
5. Place the plugin in the configured plugin directory.
6. Use PluginManager to discover and load the plugin.
7. Register lifecycle hooks (on_load/on_unload) for initialization and cleanup.

**Section sources**
- [transport/base.py:4-24](file://transport/base.py#L4-L24)
- [plugin_manager.py:120-278](file://infrastructure/plugin_manager.py#L120-L278)

### Step-by-Step: Creating a Custom Plugin (Storage Backend)
1. Implement a storage interface compatible with the expected contract.
2. Add lazy initialization and streaming operations for large datasets.
3. Validate dimensions and normalize data types.
4. Expose a singleton accessor for global use.
5. Integrate with configuration and environment variables.
6. Test with representative datasets and monitor memory usage.

**Section sources**
- [vector_store.py:44-308](file://knowledge/vector_store.py#L44-L308)

### Step-by-Step: Creating a Custom Plugin (Intelligence Module)
1. Define an adapter interface for the intelligence source.
2. Implement search, parsing, and performance tracking methods.
3. Compose adapters into a main engine with query expansion and deduplication.
4. Integrate shared HTTP sessions and rate limiting.
5. Add metrics and error handling for resilience.
6. Package as a plugin and load via PluginManager.

**Section sources**
- [academic_search.py:797-1379](file://intelligence/academic_search.py#L797-L1379)
- [plugin_manager.py:120-278](file://infrastructure/plugin_manager.py#L120-L278)

### Configuration Management
- PluginManager supports plugin.json metadata for dependencies, permissions, and schema.
- TransportResolver uses URL classification and runtime context for selection.
- VectorStore relies on environment-driven configuration for paths and dimensions.
- Intelligence modules use environment variables for API keys and rate limits.

**Section sources**
- [plugin_manager.py:154-207](file://infrastructure/plugin_manager.py#L154-L207)
- [transport_resolver.py:268-318](file://transport/transport_resolver.py#L268-L318)
- [vector_store.py:31-42](file://knowledge/vector_store.py#L31-L42)
- [academic_search.py:89-94](file://intelligence/academic_search.py#L89-L94)

### Runtime Plugin Loading
- Discover plugins from directory or single-file modules.
- Load modules dynamically using importlib.
- Instantiate plugin classes or modules.
- Trigger on_load hooks and maintain plugin registry.
- Support hot-reload by unloading and reloading plugins.

**Section sources**
- [plugin_manager.py:120-417](file://infrastructure/plugin_manager.py#L120-L417)

### Plugin Isolation and Error Handling
- Thread-safe registry operations with locks.
- Signature validation and permission checks (placeholder).
- Graceful degradation in coordinator initialization.
- Dedicated error messages and status tracking in LoadedPlugin.
- Logging for warnings and failures across subsystems.

**Section sources**
- [plugin_manager.py:113-118](file://infrastructure/plugin_manager.py#L113-L118)
- [plugin_manager.py:238-277](file://infrastructure/plugin_manager.py#L238-L277)
- [base.py:180-227](file://coordinators/base.py#L180-L227)

### Testing Strategies and Debugging Techniques
- Unit tests for individual adapters and engines.
- Integration tests for end-to-end flows (e.g., AcademicSearchEngine).
- Mock external APIs and HTTP sessions for deterministic testing.
- Use logging and metrics to trace plugin lifecycle and performance.
- Validate plugin metadata and dependencies before loading.

**Section sources**
- [academic_search.py:249-278](file://intelligence/academic_search.py#L249-L278)
- [plugin_manager.py:257-263](file://infrastructure/plugin_manager.py#L257-L263)

### Deployment Considerations
- Ensure dependencies are installed (e.g., LanceDB for VectorStore).
- Configure environment variables for API keys and paths.
- Use hot-reload in development; prefer restarts in production.
- Monitor coordinator health and load factors.
- Validate transport availability and URL classification logic.

**Section sources**
- [vector_store.py:115-120](file://knowledge/vector_store.py#L115-L120)
- [transport_resolver.py:134-150](file://transport/transport_resolver.py#L134-L150)
- [coordinator_registry.py:370-416](file://coordinators/coordinator_registry.py#L370-L416)