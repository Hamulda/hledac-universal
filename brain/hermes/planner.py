"""
brain/hermes/planner.py — Planner Execution
======================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- Planner request execution
- Runtime result handling
- Chunked parallel execution

M1 8GB: Chunked execution to respect memory constraints.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import msgspec

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Chunk size for batch execution
BRIDGE_CHUNK_SIZE = 4


class PlannerRuntimeResult(msgspec.Struct, frozen=True, kw_only=True):
    """Result of a planner runtime execution."""

    task_id: str
    executed: bool
    skipped_panic: bool
    hermes_output: str | None = None
    error: str | None = None
    elapsed_s: float = 0.0


class GenericResult(msgspec.Struct, kw_only=True):
    """Generic structured result for planner requests."""

    result: str = ""
    confidence: float = 0.5


class FetchResult(GenericResult):
    """Result for fetch operations."""

    url: str = ""


class DeepReadResult(GenericResult):
    """Result for deep read operations."""

    url: str = ""
    depth: int = 1


class AnalyseResult(GenericResult):
    """Result for analysis operations."""

    source: str = ""


class SynthesizeResult(GenericResult):
    """Result for synthesis operations."""

    sources: list[str] = msgspec.field(default_factory=list)


class BranchResult(GenericResult):
    """Result for branching operations."""

    branches: int = 1


class ExplainResult(GenericResult):
    """Result for explanation operations."""

    topic: str = ""


class HypothesisResult(GenericResult):
    """Result for hypothesis generation."""

    hypothesis: str = ""


# Registry for model resolution
MODEL_REGISTRY = {
    "FetchResult": FetchResult,
    "DeepReadResult": DeepReadResult,
    "AnalyseResult": AnalyseResult,
    "SynthesizeResult": SynthesizeResult,
    "BranchResult": BranchResult,
    "ExplainResult": ExplainResult,
    "HypothesisResult": HypothesisResult,
    "GenericResult": GenericResult,
}


async def execute_single_planner_request(
    engine,
    req,
    response_models: dict[str, type] | None = None,
) -> PlannerRuntimeResult:
    """
    Execute a single planner request via generate_structured.

    Args:
        engine: DeepHermes3Engine instance
        req: PlannerRuntimeRequest
        response_models: Optional model registry

    Returns:
        PlannerRuntimeResult
    """
    response_models = response_models or MODEL_REGISTRY

    if getattr(req, "is_panic_deprioritized", False):
        return PlannerRuntimeResult(
            task_id=req.task_id,
            executed=False,
            skipped_panic=True,
            hermes_output=None,
            error=None,
        )

    model_cls = response_models.get(
        getattr(req, "response_model_name", "GenericResult"),
        GenericResult,
    )

    t0 = time.monotonic_ns()

    try:
        result = await engine.generate_structured(
            prompt=req.prompt,
            response_model=model_cls,
            priority=getattr(req, "priority", 1.0),
            system_msg="You are a helpful research assistant.",
            max_tokens=1024,
        )

        elapsed_s = (time.monotonic_ns() - t0) / 1_000_000_000.0

        output_text = result.result if hasattr(result, "result") else str(result)

        return PlannerRuntimeResult(
            task_id=req.task_id,
            executed=True,
            skipped_panic=False,
            hermes_output=output_text,
            error=None,
            elapsed_s=elapsed_s,
        )

    except Exception as exc:
        elapsed_s = (time.monotonic_ns() - t0) / 1_000_000_000.0

        return PlannerRuntimeResult(
            task_id=req.task_id,
            executed=False,
            skipped_panic=False,
            hermes_output=None,
            error=str(exc),
            elapsed_s=elapsed_s,
        )


async def execute_planner_requests(
    engine,
    requests,
    response_models: dict[str, type] | None = None,
) -> list[PlannerRuntimeResult]:
    """
    Execute multiple planner requests in chunks.

    Args:
        engine: DeepHermes3Engine instance
        requests: List of PlannerRuntimeRequest
        response_models: Optional model registry

    Returns:
        List of PlannerRuntimeResult
    """
    from hledac.universal.utils.asyncx import parallel

    response_models = response_models or MODEL_REGISTRY
    results: list[PlannerRuntimeResult] = []

    for i in range(0, len(requests), BRIDGE_CHUNK_SIZE):
        chunk = requests[i : i + BRIDGE_CHUNK_SIZE]

        chunk_tasks = [execute_single_planner_request(engine, req, response_models) for req in chunk]

        chunk_results = await parallel(
            chunk_tasks,
            policy="log",
            ctx="hermes:planner",
        )

        for req, result in zip(chunk, chunk_results, strict=False):
            if isinstance(result, Exception):
                results.append(
                    PlannerRuntimeResult(
                        task_id=getattr(req, "task_id", "unknown"),
                        executed=False,
                        skipped_panic=False,
                        hermes_output=None,
                        error=f"bridge_exception:{result}",
                    )
                )
            else:
                results.append(result)

        # Yield to event loop between chunks
        if i + BRIDGE_CHUNK_SIZE < len(requests):
            await asyncio.sleep(0)

    return results


def create_result_from_output(
    output: str,
    result_type: str = "GenericResult",
) -> GenericResult:
    """
    Create structured result from text output.

    Args:
        output: Text output
        result_type: Result type name

    Returns:
        Structured result instance
    """
    model_cls = MODEL_REGISTRY.get(result_type, GenericResult)

    try:
        return model_cls(result=output)
    except Exception:
        return GenericResult(result=output)
