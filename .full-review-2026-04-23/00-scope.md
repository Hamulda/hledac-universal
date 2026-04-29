# Review Scope

## Target

Modified files from sprint F195 integration in `hledac/universal/` - an autonomous OSINT orchestrator optimized for M1 MacBook 8GB RAM with MLX-based LLM inference.

## Files

### Core Orchestration
- `autonomous_orchestrator.py` (9.5KB) - Main orchestrator v6.2

### Brain Module
- `brain/model_manager.py` (48KB) - Model lifecycle management
- `brain/prompt_bandit.py` (13KB) - Prompt optimization
- `brain/prompt_cache.py` (8.7KB) - MLX prompt caching

### Coordinators
- `coordinators/fetch_coordinator.py` - HTTP transport seam (curl_cffi)
- `coordinators/memory_coordinator.py` - Memory management coordination

### Intelligence Module
- `intelligence/cryptographic_intelligence.py`
- `intelligence/document_intelligence.py`
- `intelligence/exposed_service_hunter.py`
- `intelligence/network_reconnaissance.py`

### Layers
- `layers/ghost_layer.py` - Ghost operations
- `layers/memory_layer.py` - Memory persistence
- `layers/stealth_layer.py` - Stealth capabilities

### Legacy
- `legacy/persistent_layer.py`

### Security
- `security/deep_research_security.py`
- `security/digital_ghost_detector.py`

### Tools
- `tools/api_doc_generator.py`
- `tools/content_miner.py`

### Utils
- `utils/filtering.py`
- `utils/mlx_cache.py` - MLX cache management
- `utils/mlx_prompt_cache.py`
- `utils/simple_bottleneck_profiler.py`
- `utils/uma_budget.py` - M1 UMA memory budget

### New Files (Untracked)
- `forensics/enrichment_service.py`
- `tests/probe_f195c/`
- `GHOST_INVARIANTS.md`
- `REAL_ARCHITECTURE.md`

## Flags

- Security Focus: no (default review)
- Performance Critical: **yes** (M1 8GB RAM constraints, MLX optimization)
- Strict Mode: no
- Framework: Python 3.x with MLX (Apple Silicon)

## Review Phases

1. Code Quality & Architecture
2. Security & Performance
3. Testing & Documentation
4. Best Practices & Standards
5. Consolidated Report

## Key Context from Memory

- Sprint F195C fixes applied: MLX cache 4GB→8GB, circuit breaker write path, GHOST_INVARIANTS.md created
- M1 8GB UMA constraints: <5.5GB active, no parallel models, chunked processing
- Recent issues identified: _domain_failures non-functional, MLX prompt cache miscount, get_total_memory() doesn't exist
