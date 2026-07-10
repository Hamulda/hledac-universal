"""
runtime/acquisition/acquisition_lanes.py

Async acquisition lane runners — run_enabled_acquisition_lanes() and variants.
Extracted from acquisition_strategy.py (original L3764-5282).

NOTE: This module is a STUB for the refactoring. The actual lane runner
implementation remains in acquisition_strategy.py during the transition period.

MODERNIZATION (Issue #18):
  - Lane runners stay in acquisition_strategy.py during migration (complex async closures)
  - This module provides the public interface and delegates to the original

MIGRATION STATUS:
  - Full implementation pending: lane runners are closures that capture complex state
  - Will be migrated in Issue #19 after acquisition_strategy.py is cleaned up
"""


from typing import Any

# Re-export the actual implementation from acquisition_strategy.py (during transition)
# This is a temporary shim - real implementation will be extracted in Issue #19
_acquisition_strategy = None


def _get_original():
    global _acquisition_strategy
    if _acquisition_strategy is None:
        import hledac.universal.runtime.acquisition_strategy as _acquisition_strategy
    return _acquisition_strategy


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

    NOTE: Delegates to acquisition_strategy.run_enabled_acquisition_lanes()
    during the transition period. Will be fully extracted in Issue #19.
    """
    orig = _get_original()
    return await orig.run_enabled_acquisition_lanes(
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
):
    """
    Run enabled acquisition lanes with streaming results.

    NOTE: Delegates to acquisition_strategy.run_enabled_acquisition_lanes_streaming()
    during the transition period.
    """
    orig = _get_original()
    return await orig.run_enabled_acquisition_lanes_streaming(
        snapshot=snapshot,
        query=query,
        store=store,
        uma_state=uma_state,
        clearnet_max=clearnet_max,
        seed_context=seed_context,
        graph_accumulator=graph_accumulator,
        min_finished=min_finished,
        on_lane_complete=on_lane_complete,
    )
