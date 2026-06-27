"""
Pipeline package — public OSINT discovery pipeline components.

Split from the monolithic live_public_pipeline.py (5737L) into focused modules.
"""
from __future__ import annotations

__all__ = [
    "PipelinePageResult",
    "PipelineRunResult",
]


def __getattr__(name: str):
    """Lazy import to avoid msgspec dependency at package load time."""
    if name in ("PipelinePageResult", "PipelineRunResult"):
        from .public_stages import PipelinePageResult, PipelineRunResult
        return (PipelinePageResult, PipelineRunResult)[name == "PipelineRunResult"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
