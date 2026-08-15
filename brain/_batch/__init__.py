"""
_batch — Batch Processing Module
===============================

PEP 698: Extracted from DeepHermes3Engine batch processing logic.
Handles structured batch execution and response model processing.

Architecture:
- batch_processor.py: Batch queue management and execution
"""

from hledac.universal.brain._batch.batch_processor import BatchProcessor, BatchItem
from _core import aclose

__all__ = [
    "BatchProcessor",
    "BatchItem",
]
