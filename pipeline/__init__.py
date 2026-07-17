"""
Pipeline package — public OSINT discovery pipeline components.

Split from the monolithic live_public_pipeline.py (5737L) into focused modules.
"""


__all__ = [
    "PipelinePageResult",
    "PipelineRunResult",
    "FeedPipelineRunResult",
    "FeedPipelineEntryResult",
    "FeedSignalTelemetry",
]


def __getattr__(name: str):
    """Lazy import to avoid msgspec dependency at package load time."""
    if name in ("PipelinePageResult", "PipelineRunResult"):
        from .public_stages import PipelinePageResult, PipelineRunResult
        return (PipelinePageResult, PipelineRunResult)[name == "PipelineRunResult"]
    if name == "FeedSignalTelemetry":
        from .live_feed_pipeline import FeedSignalTelemetry
        return FeedSignalTelemetry
    if name == "live_public_pipeline":
        import importlib
        return importlib.import_module("hledac.universal.pipeline.live_public_pipeline")
    # fail-soft: undefined attributes return None (pipeline/ uses error swallowing)
    return None
