"""Public pipeline stages — extracted from live_public_pipeline.py.

This package contains the stage implementations for the public OSINT pipeline.
Each stage is a self-contained module with a single responsibility.

Stages (in execution order):
    1. discovery   — URL generation (bootstrap, rescue, keyword)
    2. fetch       — Per-URL HTTP fetch (delegated to public_fetch.py)
    3. extract     — Text extraction + quality scoring
    4. match       — PatternMatcher dispatch
    5. build       — CanonicalFinding construction
    6. export      — Markdown/HTML graph export

For backwards compatibility, live_public_pipeline.py re-exports all symbols.
"""
from __future__ import annotations

# Public API — re-export from live_public_pipeline for backwards compatibility
from hledac.universal.pipeline.live_public_pipeline import (
    PipelinePageResult,
    PipelineRunResult,
    async_run_live_public_pipeline,
)
from _core import aclose

__all__ = [
    "PipelinePageResult",
    "PipelineRunResult",
    "async_run_live_public_pipeline",
]
