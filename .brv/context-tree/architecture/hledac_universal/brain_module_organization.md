---
title: Brain Module Organization
summary: Brain module with PEP 562 facade reducing cold import from ~9.7s to ~150ms, featuring 12 lazy-loaded engines and MLX/Apple Silicon support
tags: []
related: [architecture/hledac_universal/lazy-loading-reduces-cold-import-by-98.md]
keywords: []
createdAt: '2026-07-16T11:01:02.222Z'
updatedAt: '2026-07-16T11:01:02.222Z'
---
## Reason
Document brain module structure, facade pattern, and lazy import optimization

## Raw Concept
**Task:**
Document brain module organization in hledac/universal/brain/

**Files:**
- brain/__init__.py
- brain/deephermes3_engine.py
- brain/_mlx_dispatcher.py
- brain/batch_scheduler.py
- brain/model_manager.py

**Flow:**
Import brain -> lazy __getattr__ -> deferred engine load on first access

**Timestamp:** 2025-07-16

## Narrative
### Structure
Core Engines (deephermes3, MLX dispatcher, batch scheduler), Supporting Engines (causal, distillation, DSPy), Embedding (CoreML, ANE, MLX), Utilities

### Highlights
PEP 562 facade with lazy imports reduces cold import from ~9.7s to ~150ms. Hermes3Engine is L1 canonical. ModelManager handles M1 lifecycle. 12 engines defer import until first attribute access.

### Rules
Promotion Gate: FACADE (export-only). HypothesisEngine stays eager (circular dep). NEREngine requires large RAM. MoE and Distillation are DORMANT.

## Facts
- **cold_import_cost**: Cold import cost reduced from ~9.7s to ~150ms via lazy loading [other]
- **lazy_import_pattern**: brain/__init__.py uses PEP 562 __getattr__ for lazy imports [project]
- **canonical_engine**: Hermes3Engine is L1 canonical engine [project]
- **decision_types**: DecisionType enum has 7 variants: RESEARCH, EXECUTION, ANALYSIS, PLANNING, SYNTHESIS, ERROR, COMPLETE [project]
- **sprint_loRA-1**: Sprint LoRA-1 added mlx_lm.lora deferred import [convention]
- **sprint_P0-2**: Sprint P0-2 introduced MLXBatchedExecutor [convention]
- **sprint_P0-3**: Sprint P0-3 introduced MLXWorkerThread [convention]
- **model_manager**: ModelManager handles M1 lifecycle management [project]
- **lazy_engines_count**: 12 non-circular engines use lazy imports via __getattr__ [project]
