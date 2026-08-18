"""
_batch — Batch Processing Module
===============================

PEP 698: Extracted from DeepHermes3Engine batch processing logic.
Handles structured batch execution and response model processing.

Architecture:
- batch_processor.py: Batch queue management and execution
"""

# Use absolute imports within the hledac.universal package
from hledac.universal.brain._batch.batch_processor import BatchProcessor, BatchItem
from hledac.universal._core import aclose

__all__ = [
    "BatchProcessor",
    "BatchItem",
]
