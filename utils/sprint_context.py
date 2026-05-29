"""
Sprint Context — ContextVar-based sprint metadata for Hledac.

Provides thread-safe, async-safe sprint context propagation using msgspec.Struct
(frozen=True, gc=False) for minimal overhead.

Canonical keys used in maybe_resume():
  - b"sprint:last_phase"
  - b"sprint:current_id"

Usage:
    ctx = SprintContext(sprint_id="s7a", target="osint", start_time=time.time(),
                        phase="active", transport="curl_cffi")
    with sprint_scope(ctx):
        # current context is set
        pass
    # context is reset
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar, Token

import msgspec

# =============================================================================
# SprintContext — frozen struct for hot-path performance
# =============================================================================


class SprintContext(msgspec.Struct, frozen=True, gc=False):
    """
    Sprint metadata container.

    frozen=True  : immutable, hashable, comparable by identity
    gc=False     : no cyclic GC overhead — no refs back to managed objects

    Fields:
        sprint_id  : unique sprint identifier (e.g. "7a", "12b")
        target      : research target / focus area
        start_time  : monotonic seconds when sprint began
        phase       : current phase (boot/warmup/active/windup/export/teardown)
        transport   : HTTP transport in use (e.g. "curl_cffi", "aiohttp")
    """

    sprint_id: str = ""
    target: str = ""
    start_time: float = 0.0
    phase: str = "boot"
    transport: str = "curl_cffi"

    def is_unfinished(self) -> bool:
        """True when phase is not EXPORT or TEARDOWN."""
        return self.phase not in ("export", "teardown")


# =============================================================================
# ContextVar — module-level, safe for async
# =============================================================================

# NOTE: ContextVar automatically propagates to async tasks (child tasks inherit).
# For thread-pool executors or ProcessPoolExecutor, explicit context
# propagation is needed via copy_context().run(fn). ContextVar does NOT
# automatically flow across thread boundaries.

_sprint_ctx: ContextVar[SprintContext | None] = ContextVar(
    "sprint_context", default=None
)


# =============================================================================
# Helpers
# =============================================================================


def get_current_context() -> SprintContext | None:
    """Return the current sprint context (or None)."""
    return _sprint_ctx.get()


# Alias for naming consistency with sprint plan §1.10
get_sprint_context = get_current_context


def set_sprint_context(ctx: SprintContext) -> None:
    """Set the current sprint context."""
    _sprint_ctx.set(ctx)


def reset_current_sprint_context(token: Token) -> None:
    """Reset to the context that was active when the token was obtained."""
    _sprint_ctx.reset(token)


def clear_sprint_context() -> None:
    """Clear the current sprint context (reset to default None)."""
    _sprint_ctx.set(None)


@contextlib.contextmanager
def sprint_scope(ctx: SprintContext):
    """
    Context manager that sets ctx as current for the duration of `with`.

    Uses ContextVar.reset() for guaranteed cleanup even on exception.

    Example:
        ctx = SprintContext(sprint_id="7a", target="osint", ...)
        with sprint_scope(ctx):
            assert get_current_context() is ctx
        assert get_current_context() is None
    """
    _sprint_ctx.set(ctx)
    try:
        yield ctx
    finally:
        _sprint_ctx.set(None)


def update_phase(ctx: SprintContext, new_phase: str) -> SprintContext:
    """
    Return a new SprintContext with phase updated.

    Uses msgspec.structs.replace() — the only correct way to update a frozen struct.

    Args:
        ctx: existing SprintContext
        new_phase: new phase string

    Returns:
        new SprintContext instance with phase=new_phase

    Canonical LMDB keys written by caller:
        b"sprint:last_phase"  -> new_phase.encode()
        b"sprint:current_id" -> ctx.sprint_id.encode()
    """
    return msgspec.structs.replace(ctx, phase=new_phase)


__all__ = [
    "SprintContext",
    "get_current_context",
    "get_sprint_context",
    "set_sprint_context",
    "reset_current_sprint_context",
    "clear_sprint_context",
    "sprint_scope",
    "update_phase",
]
