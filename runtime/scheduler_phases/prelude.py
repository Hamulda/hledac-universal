"""STEP 3 — Prelude phase: _run_public_prelude_lane.

Extracted from runtime/sprint_scheduler.py (33 449 LOC → modular package).
F350M-R / Issue #P2.

This module holds prelude-phase helpers that can be unit-tested in isolation
and eventually run as standalone async tasks in the phase pipeline.
"""


import asyncio
from typing import Any


async def run_public_prelude_lane(_sched: Any, query: str) -> dict:
    """Run PUBLIC prelude lane.

    Returns result dict, never raises.
    Bounded: 10s asyncio.timeout, max 3 results, concurrency 2.
    """
    from hledac.universal.pipeline.live_public_pipeline import (
        async_run_live_public_pipeline,
    )
    from hledac.universal.runtime.acquisition_strategy import (
        AcquisitionLane,
        build_lane_query,
    )

    try:
        _shaped = build_lane_query(query, AcquisitionLane.PUBLIC)
        if isinstance(_shaped, dict) or not _shaped:
            return {
                "lane": "PUBLIC",
                "attempted": False,
                "skipped": True,
                "skip_reason": "empty_public_query",
                "raw_count": 0,
                "built_count": 0,
                "accepted_count": 0,
                "error": None,
                "timeout": False,
                "duration_s": None,
            }
        async with asyncio.timeout(10.0):
            _pipeline_result = await async_run_live_public_pipeline(
                query=_shaped,
                store=None,
                max_results=3,
                fetch_timeout_s=10.0,
                fetch_concurrency=2,
                hermes_engine=None,
                memory_manager=None,
                enqueue_hypothesis_pivot=None,
            )
        return {
            "lane": "PUBLIC",
            "attempted": True,
            "skipped": False,
            "skip_reason": None,
            "raw_count": getattr(_pipeline_result, "discovered", 0) or 0,
            "built_count": getattr(_pipeline_result, "fetched", 0) or 0,
            "accepted_count": getattr(_pipeline_result, "accepted_findings", 0) or 0,
            "error": getattr(_pipeline_result, "error", None),
            "timeout": getattr(_pipeline_result, "timed_out", False),
            "duration_s": getattr(_pipeline_result, "elapsed_s", None),
        }
    except TimeoutError:
        return {
            "lane": "PUBLIC",
            "attempted": True,
            "skipped": False,
            "timeout": True,
            "error": None,
            "duration_s": 10.0,
        }
    except Exception as exc:
        return {
            "lane": "PUBLIC",
            "attempted": True,
            "skipped": False,
            "error": f"{type(exc).__name__}:{exc}",
            "timeout": False,
            "duration_s": None,
        }
