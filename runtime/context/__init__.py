"""runtime.context — bounded SprintRunContext support types.

Exports:
  BoundedLRUDict: LRU dict with hard maxsize cap and eviction telemetry.
  RingBuffer: Rust-backed fixed-capacity ring buffer (recent_iocs).
  SprintRunContext: per-sprint mutable state (contextvars-backed).
  get_sprint_ctx / reset_sprint_ctx: context management.
"""

from runtime.context.bounded_dicts import (
    BoundedLRUDict,
    DEFAULT_ENTRIES_PER_SOURCE_MAXSIZE,
    DEFAULT_FEED_ACCEPTED_MAXSIZE,
    DEFAULT_FETCH_LATENCY_EMA_MAXSIZE,
    DEFAULT_NOVELTY_BONUSES_MAXSIZE,
    DEFAULT_SEEN_HASHES_MAXSIZE,
    DEFAULT_SOURCE_WEIGHTS_MAXSIZE,
)
from runtime.sprint_scheduler import (
    SprintRunContext,
    get_sprint_ctx,
    reset_sprint_ctx,
)

__all__ = [
    "BoundedLRUDict",
    "SprintRunContext",
    "get_sprint_ctx",
    "reset_sprint_ctx",
    "DEFAULT_SEEN_HASHES_MAXSIZE",
    "DEFAULT_ENTRIES_PER_SOURCE_MAXSIZE",
    "DEFAULT_NOVELTY_BONUSES_MAXSIZE",
    "DEFAULT_SOURCE_WEIGHTS_MAXSIZE",
    "DEFAULT_FEED_ACCEPTED_MAXSIZE",
    "DEFAULT_FETCH_LATENCY_EMA_MAXSIZE",
]
