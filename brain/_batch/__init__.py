"""
_batch — D-SPINE private batch layer (ISSUE #16)
================================================

D-SPINE layout: underscore-prefixed private subfolders under ``brain/``
(``_batch``, ``_cache``, ``_inference``, ``_metal``) are PEP 698 extraction
targets from the ``DeepHermes3Engine`` god-module. Their ROLE:

  - ``_batch``  : request scheduling / batching around the single MLX lock.
  - ``_cache``  : KV / prefix / session / warmup cache management.
  - ``_inference`` : generation facade + token streaming.
  - ``_metal``  : Metal GPU memory + model-loader abstractions.

LIVE STATUS (read this before editing):
  The 6232-LOC ``brain.deephermes3_engine.DeepHermes3Engine`` is still the
  LIVE implementation imported across ``_core/``, ``runtime/`` and tests.
  ``brain.hermes.*`` is the parallel PEP-698 *target* package. The ``_batch``
  modules are real (not stubs) but mostly serve as facades/adapters over the
  engine's own inline code; ``dispatcher.py`` is the NEW Round-Robin scheduler
  that sits ON TOP of the single MLX inference lock (M1 single Metal queue ⇒
  one decode stream; "batching" = queue + prefix-cache clustering, not parallel
  decode).

Architecture:
- batch_processor.py: Priority-queue batch execution (structured outputs).
- dispatcher.py:      Round-Robin ``asyncio.Queue[GenerateJob]`` scheduler.
"""

# Use absolute imports within the hledac.universal package
from hledac.universal.brain._batch.batch_processor import BatchItem, BatchProcessor
from hledac.universal.brain._batch.dispatcher import GenerateJob, GenerateJobDispatcher

__all__ = [
    "BatchProcessor",
    "BatchItem",
    "GenerateJobDispatcher",
    "GenerateJob",
]
