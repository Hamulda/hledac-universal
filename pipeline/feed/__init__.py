"""Feed pipeline stages — extracted from live_feed_pipeline.py.

This package contains the stage implementations for the RSS/Atom feed pipeline.
Each stage is a self-contained module with a single responsibility.

Stages (in execution order):
    1. fetch_feed   — Fetch + parse RSS/Atom feed
    2. assemble     — Text assembly from feed entries
    3. scan         — Pattern scan on assembled text
    4. dedup        — Per-entry dedup + run-level dedup
    5. build_feed   — CanonicalFinding construction
    6. export       — Markdown/HTML graph export

For backwards compatibility, live_feed_pipeline.py re-exports all symbols.
"""

from __future__ import annotations

# Re-export lazily to avoid circular import
# Use `from pipeline.feed import FeedPipelineEntryResult` instead of direct import


def __getattr__(name: str):
    """Lazy re-export from live_feed_pipeline or _feed_orchestrator to avoid circular import."""
    if name in ("FeedPipelineEntryResult", "FeedPipelineRunResult", "async_run_live_feed_pipeline"):
        from hledac.universal.pipeline.live_feed_pipeline import (
            FeedPipelineEntryResult,
            FeedPipelineRunResult,
            async_run_live_feed_pipeline,
        )

        return locals()[name]
    if name == "FeedPipelineOrchestrator":
        from hledac.universal.pipeline._feed_orchestrator import FeedPipelineOrchestrator

        return FeedPipelineOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FeedPipelineEntryResult",
    "FeedPipelineRunResult",
    "async_run_live_feed_pipeline",
]
