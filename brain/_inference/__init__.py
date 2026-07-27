"""
_inference — Inference Module
=============================

PEP 698: Extracted from DeepHermes3Engine inference methods.
Handles streaming, generate orchestration, and structured output.

Architecture:
- stream_handler.py: Token streaming abstraction
- generate.py: Generate orchestration
"""

from brain._inference.stream_handler import StreamHandler
from brain._inference.generate import InferenceEngine

__all__ = [
    "StreamHandler",
    "InferenceEngine",
]
