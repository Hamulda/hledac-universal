"""
Shared Request Dispatch — Coordinator Routing Pattern
===================================================


Eliminates duplicate handle_request() implementations across coordinators.
Every coordinator follows the same pattern:
1. Track operation
2. Execute decision
3. Build OperationResult
4. Record result
5. Untrack operation

This module provides the shared skeleton so coordinators only override
the decision execution logic.

Canonical import:
    from hledac.universal.coordinators._dispatch import execute_dispatch, DispatchContext

Usage:
    async def handle_request(self, op_ref: str, decision: DecisionResponse) -> OperationResult:
        return await execute_dispatch(
            ctx=DispatchContext(
                coordinator=self,
                operation_ref=op_ref,
                operation_type='execution',
                decision=decision,
            ),
            execute_fn=self._execute_decision,
        )
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from hledac.universal.compat.msgspec_gc_compat import Struct
from typing import TYPE_CHECKING, Any
from _core import aclose

if TYPE_CHECKING:
    from ._dto import DecisionResponse, OperationResult

logger = logging.getLogger(__name__)


class DispatchContext(Struct, frozen=True):
    """Immutable context passed to all dispatch operations. M1 8GB: msgspec.Struct for fast init."""
    coordinator_name: str
    operation_ref: str
    operation_type: str  # 'execution', 'security', 'opsec', 'monitoring', etc.
    decision: DecisionResponse
    start_time: float


def _make_context(
    coordinator_name: str,
    operation_ref: str,
    operation_type: str,
    decision: DecisionResponse,
) -> DispatchContext:
    """Factory — creates DispatchContext with current timestamp."""
    return DispatchContext(
        coordinator_name=coordinator_name,
        operation_ref=operation_ref,
        operation_type=operation_type,
        decision=decision,
        start_time=time.time(),
    )


async def execute_dispatch(
    ctx: DispatchContext,
    execute_fn: Callable[[DecisionResponse], Awaitable[Any]],
    tracker: Any,  # UniversalCoordinator instance
    extra_metadata_fn: Callable[[Any], dict[str, Any]] | None = None,
) -> OperationResult:
    """
    Execute a coordinator dispatch with standard lifecycle.

    Standard lifecycle:
    1. Generate operation_id
    2. Track operation
    3. Execute decision via execute_fn
    4. Build OperationResult
    5. Record result
    6. Untrack operation

    Args:
        ctx: Dispatch context with metadata
        execute_fn: Async function that executes the decision and returns result
        tracker: UniversalCoordinator instance for tracking/recording
        extra_metadata_fn: Optional function to extract extra metadata from result.
            If provided, called with result and returned dict is merged into metadata.

    Returns:
        OperationResult with execution outcome
    """
    operation_id = tracker.generate_operation_id()
    tracker.track_operation(operation_id, {
        'operation_ref': ctx.operation_ref,
        'decision': ctx.decision,
        'type': ctx.operation_type,
    })
    try:
        result = await execute_fn(ctx.decision)
        # Result must have: success (bool), summary (str), and optional metadata
        is_success = getattr(result, 'success', True)
        summary = getattr(result, 'summary', str(result))
        # Support both 'metadata' and 'result_data' attribute names
        if hasattr(result, 'metadata'):
            extra_meta = result.metadata
        elif hasattr(result, 'result_data'):
            extra_meta = result.result_data
        else:
            extra_meta = {}
        # Apply custom metadata extractor if provided
        if extra_metadata_fn is not None:
            extra_meta = {**extra_meta, **extra_metadata_fn(result)}

        operation_result = OperationResult(
            operation_id=operation_id,
            status='completed' if is_success else 'failed',
            result_summary=summary,
            execution_time=time.time() - ctx.start_time,
            success=is_success,
            metadata=extra_meta,
        )
    except Exception as e:
        operation_result = OperationResult(
            operation_id=operation_id,
            status='failed',
            result_summary=f'{ctx.operation_type.capitalize()} operation failed: {str(e)}',
            execution_time=time.time() - ctx.start_time,
            success=False,
            error_message=str(e),
        )
    finally:
        tracker.untrack_operation(operation_id)

    tracker.record_operation_result(operation_result)
    return operation_result


# ─── Routing Helpers ───────────────────────────────────────────────────────────

def match_keyword(chosen: str, keywords: list[str]) -> bool:
    """Check if any keyword appears in chosen option (case-insensitive)."""
    chosen_lower = chosen.lower()
    return any(k in chosen_lower for k in keywords)


def route_by_keyword(
    chosen: str,
    routes: dict[str | tuple[str, ...], str],
    default: str | None = None,
) -> str | None:
    """
    Route based on keyword matching.

    Args:
        chosen: The chosen_option string
        routes: Mapping of keyword(s) to route name
        default: Default route if no match

    Returns:
        Route name or None if no match and no default
    """
    for pattern, route in routes.items():
        keys = (pattern,) if isinstance(pattern, str) else pattern
        if match_keyword(chosen, list(keys)):
            return route
    return default


__all__ = [
    'DispatchContext',
    'execute_dispatch',
    'match_keyword',
    'route_by_keyword',
    '_make_context',
]
