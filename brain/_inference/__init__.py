"""
_inference — Generation Module
==============================

PEP 698: Extracted from DeepHermes3Engine generation methods.
Handles streaming, MLX generate orchestration, and structured output.

Architecture:
- stream_handler.py: Token streaming abstraction
- generate.py: GenerationFacade (MLX token generation)

NOTE: This is NOT brain.inference_engine (abductive reasoning / evidence chaining).
Independence: brain._inference is MLX-generate-only; brain.inference_engine is symbolic.
"""

from hledac.universal.brain._inference.stream_handler import StreamHandler
from hledac.universal.brain._inference.generate import GenerationFacade

__all__ = [
    "StreamHandler",
    "GenerationFacade",
]
