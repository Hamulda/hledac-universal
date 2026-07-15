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


def __getattr__(name: str):
    # SprintRunContext and context helpers live in sprint_scheduler_v1_archived.
    # Import from the archived module directly to avoid circular import:
    # sprint_scheduler (stub) → sprint_scheduler_v1_archived → runtime.context → sprint_scheduler (stub)
    if name in ("SprintRunContext", "get_sprint_ctx", "reset_sprint_ctx"):
        from runtime import sprint_scheduler_v1_archived as _v1

        return getattr(_v1, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
