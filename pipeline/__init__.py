"""Pipeline package — public OSINT discovery pipeline components.

Split from the monolithic live_public_pipeline.py (5737L) into focused modules.
"""

from __future__ import annotations
from core import aclose

__all__ = [
    "FeedPipelineEntryResult",
    "FeedPipelineRunResult",
    "FeedSignalTelemetry",
    "PipelinePageResult",
    "PipelineRunResult",
]


def __getattr__(name: str) -> object | None:
    """Lazy import to avoid msgspec dependency at package load time."""
    if name in ("PipelinePageResult", "PipelineRunResult"):
        from .public_stages import PipelinePageResult, PipelineRunResult  # noqa: PLC0415

        return (PipelinePageResult, PipelineRunResult)[name == "PipelineRunResult"]
    if name == "FeedSignalTelemetry":
        from .live_feed_pipeline import FeedSignalTelemetry  # noqa: PLC0415

        return FeedSignalTelemetry
    if name == "live_public_pipeline":
        import importlib  # noqa: PLC0415

        return importlib.import_module("hledac.universal.pipeline.live_public_pipeline")
    # fail-soft: undefined attributes return None (pipeline/ uses error swallowing)
    return None
