"""
brain/hermes/ — Modular DeepHermes3 Engine Package
===================================================

PEP 698: God-module refactoring of brain/deephermes3_engine.py (5560 LOC → modular package).

Package Structure:
    hermes/
    ├── __init__.py      # Re-exports (backward compatibility)
    ├── engine.py        # DeepHermes3Engine orchestrator (≤400 LOC)
    ├── chatml.py        # ChatML formatting, template methods
    ├── decisions.py      # Decision engine, triage_mode, decide_next_action
    ├── synthesis.py     # Synthesis methods (report, sprint_plan, findings)
    ├── batch.py         # PriorityQueueAdapter, batch submission logic
    ├── kv_cache.py      # KV cache management, warmup logic
    ├── structured.py     # Structured output generation (outlines)
    ├── security.py      # Prompt security validation, injection detection
    ├── lora.py          # LoRA adapter management
    ├── lifecycle.py     # Model lifecycle (initialize, unload, load_model)
    ├── stream.py        # Streaming generation
    └── planner.py       # Planner execution, runtime results

Backward Compatibility:
    from brain.deephermes3_engine import DeepHermes3Engine
    # OR (new preferred):
    from brain.hermes import DeepHermes3Engine

M1 8GB: Unified memory architecture - no GPU transfer overhead.
"""

from __future__ import annotations

# Use lazy loading to avoid circular imports
def __getattr__(name: str):
    if name == "DeepHermes3Engine":
        from brain.hermes.engine import DeepHermes3Engine
        return DeepHermes3Engine
    if name == "format_chatml":
        from brain.hermes.chatml import format_chatml
        return format_chatml
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# Also expose submodules for direct import if needed
__all__ = [
    "DeepHermes3Engine",
    "format_chatml",
    "chatml",
    "decisions", 
    "synthesis",
    "batch",
    "kv_cache",
    "structured",
    "security",
    "lora",
    "lifecycle",
    "stream",
    "planner",
]

# Module version for introspection
__version__ = "2.0.0"
__refactor__ = "PEP-698-god-module-split"
