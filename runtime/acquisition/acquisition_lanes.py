"""
runtime/acquisition/acquisition_lanes.py

Async acquisition lane runners — run_enabled_acquisition_lanes() and variants.

Canonical implementation moved to runtime/scheduler/lanes/__init__.py (correct: each
lane calls store.async_ingest_findings_batch directly and populates accepted_findings).

This module delegates to runtime.scheduler.lanes (not acquisition_strategy.py, which
has the broken pattern where the outer caller does the ingest and backfills
accepted_findings after the gather).
"""

from collections.abc import AsyncIterator
from typing import Any

_scheduler_lanes = None


def _get_scheduler_lanes():
    global _scheduler_lanes
    if _scheduler_lanes is None:
        import hledac.universal.runtime.scheduler.lanes as _scheduler_lanes
    return _scheduler_lanes


async def run_enabled_acquisition_lanes(
    snapshot: Any,
    query: str,
    store: Any,  # DuckDBShadowStore | None
    uma_state: str = "ok",
    seed_context: Any = None,  # NonfeedSeedContext | None
    graph_accumulator: Any = None,
) -> tuple:
    """
    Run all enabled optional acquisition lanes.

    Delegates to runtime/scheduler/lanes/__init__.py::run_enabled_acquisition_lanes
    where each lane runner calls store.async_ingest_findings_batch directly and
    populates accepted_findings in the returned AcquisitionLaneOutcome.
    """
    lanes = _get_scheduler_lanes()
    return await lanes.run_enabled_acquisition_lanes(
        snapshot=snapshot,
        query=query,
        store=store,
        uma_state=uma_state,
        seed_context=seed_context,
        graph_accumulator=graph_accumulator,
    )


async def run_enabled_acquisition_lanes_streaming(
    snapshot: Any,
    query: str,
    store: Any,
    uma_state: str = "ok",
    clearnet_max: int = 4,
    seed_context: Any = None,
    graph_accumulator: Any = None,
    min_finished: int = 0,
    on_lane_complete: Any = None,  # Callable[[AcquisitionLaneOutcome], None] | None
) -> AsyncIterator[tuple]:
    """
    Run enabled acquisition lanes with streaming results.

    Delegates to runtime/scheduler/lanes/__init__.py::run_enabled_acquisition_lanes_streaming
    (AsyncGenerator). Uses `yield from` to proxy the async generator correctly — the caller
    iterates this shim directly with `async for`.
    """
    lanes = _get_scheduler_lanes()
    async for outcome in lanes.run_enabled_acquisition_lanes_streaming(
        snapshot=snapshot,
        query=query,
        store=store,
        uma_state=uma_state,
        clearnet_max=clearnet_max,
        seed_context=seed_context,
        graph_accumulator=graph_accumulator,
        min_finished=min_finished,
        on_lane_complete=on_lane_complete,
    ):
        yield outcome
