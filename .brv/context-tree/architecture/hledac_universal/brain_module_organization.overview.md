**Key Points:**
- PEP 562 facade with lazy imports reduced cold import time from ~9.7s to ~150ms
- 12 non-circular engines use lazy imports via `__getattr__`, deferred until first attribute access
- Hermes3Engine is designated as the L1 canonical engine
- ModelManager handles M1 lifecycle management
- Some engines remain eager-loaded: HypothesisEngine (circular dependency), NEREngine (large RAM requirement)
- MoE and Distillation engines are currently DORMANT

**Structure / Sections:**
- Core Engines: deephermes3, MLX dispatcher, batch scheduler
- Supporting Engines: causal, distillation, DSPy
- Embedding: CoreML, ANE, MLX
- Utilities section

**Notable Entities, Patterns, or Decisions:**
- DecisionType enum has 7 variants: RESEARCH, EXECUTION, ANALYSIS, PLANNING, SYNTHESIS, ERROR, COMPLETE
- Sprint LoRA-1: added mlx_lm.lora deferred import
- Sprint P0-2: introduced MLXBatchedExecutor
- Sprint P0-3: introduced MLXWorkerThread
- Promotion Gate uses FACADE pattern (export-only)
- Import flow: brain/__init__.py → lazy `__getattr__` → deferred engine load on first access