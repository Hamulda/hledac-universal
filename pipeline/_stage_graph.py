"""Stage Graph Orchestrator — data-oriented pipeline framework.

Python orchestrates stages; CPU stages use Rust pipeline_compose / rayon;
GPU stages use MLX; IO stages use async DuckDB/LMDB.




Pattern: Structure of Arrays (SoA) batches between stages, not AoS dict soup.
Each stage receives a typed batch and returns a typed batch + telemetry.

Architecture:
    StageOrchestrator
        │
        ├── Stage1 (CPU, Rust/rayon) ── SoA batch ──►
        ├── Stage2 (CPU, Rust/rayon) ── SoA batch ──►
        ├── Stage3 (GPU, MLX)          ── SoA batch ──►
        └── Stage4 (IO, async)         ── findings ──► DuckDB/LMDB

Usage:
    orch = StageOrchestrator([
        ("discovery", DiscoveryStage()),
        ("fetch", FetchStage()),
        ("extract", ExtractStage()),
        ("match", MatchStage()),
        ("build", BuildStage()),
    ])
    result = await orch.run(initial_batch)
"""
from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct

from hledac.universal.utils.async_helpers import safe_create_task

# Compiled once at module level for O(1) reuse in topological_sort
_DEP_PATTERN = re.compile(r"^_?(\w+)_to_(\w+)$")

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Protocol

    class StageLike(Protocol):
        @property
        def name(self) -> str: ...
        async def process(self, input_batch: Any) -> tuple[Any, dict[str, Any]]: ...

logger = logging.getLogger(__name__)


class StageResult(Struct, frozen=True):
    """Typed result from a single stage run.

    All fields are explicitly typed for mypy/catch mismatches.
    """

    ok: bool
    stage_name: str
    output_batch: Any | None  # stage-specific type
    telemetry: dict[str, Any]  # stage counters/metrics
    error: str | None  # None = success
    items_in: int  # batch size input
    items_out: int  # batch size output


class StageStats(Struct):
    """Per-stage statistics accumulated during a pipeline run."""

    name: str
    invocations: int = 0
    total_time_ms: float = 0.0
    items_in_total: int = 0
    items_out_total: int = 0
    errors: int = 0


class StageOrchestrator:
    """Orchestrates typed stages in topological order.

    Each stage receives an input batch, produces an output batch + telemetry,
    and passes the output to the next stage.

    M1 8GB safe:
    - Bounded batch sizes (max 256 per stage)
    - Fail-safe: stage errors don't crash the pipeline
    - All exceptions caught and logged per stage
    - Telemetry accumulated for observability

    Invariants:
    - Always-on, no feature flags
    - Fail-safe: each stage wrapped in try/except
    - Bounded: max batch size enforced
    """

    __slots__ = (
        "_stages",
        "_stats",
        "_running",
    )

    def __init__(
        self,
        stages: list[tuple[str, "StageLike"]],
    ) -> None:
        """Initialize orchestrator with ordered stages.

        Args:
            stages: list of (name, stage_instance) tuples.
                   Order matters — stages run in the order given.
                   Use topological_sort() if stages have dependencies.

        """
        self._stages = stages
        self._stats: dict[str, StageStats] = {
            name: StageStats(name=name) for name, _ in stages
        }
        self._running = False

    @property
    def stage_names(self) -> list[str]:
        """Return ordered list of stage names."""
        return [name for name, _ in self._stages]

    def get_stats(self) -> dict[str, StageStats]:
        """Return per-stage statistics."""
        return dict(self._stats)

    async def run(
        self,
        initial_input: Any,
        *,
        max_batch_size: int = 256,
    ) -> tuple[StageResult, ...]:
        """Run all stages in order.

        Args:
            initial_input: Input to the first stage.
            max_batch_size: Upper bound on batch sizes (default 256).

        Returns:
            Tuple of StageResult, one per stage.
            Results are in stage order.

        """
        if self._running:
            raise RuntimeError("StageOrchestrator.run() is not reentrant")
        self._running = True

        try:
            ctx: Any = initial_input
            results: list[StageResult] = []

            for stage_name, stage in self._stages:
                stats = self._stats[stage_name]
                t0 = time.monotonic()

                try:
                    output, telemetry = await stage.process(ctx)
                    dt_ms = (time.monotonic() - t0) * 1000

                    items_in = _batch_len(ctx)
                    items_out = _batch_len(output) if output is not None else 0

                    stats.invocations += 1
                    stats.total_time_ms += dt_ms
                    stats.items_in_total += items_in
                    stats.items_out_total += items_out

                    results.append(StageResult(
                        ok=True,
                        stage_name=stage_name,
                        output_batch=output,
                        telemetry=dict(telemetry),
                        error=None,
                        items_in=items_in,
                        items_out=items_out,
                    ))

                    ctx = output

                except Exception as exc:
                    dt_ms = (time.monotonic() - t0) * 1000
                    stats.errors += 1
                    stats.total_time_ms += dt_ms

                    logger.exception(f"StageOrchestrator: stage '{stage_name}' failed: {exc}")

                    results.append(StageResult(
                        ok=False,
                        stage_name=stage_name,
                        output_batch=None,
                        telemetry={},
                        error=f"{type(exc).__name__}: {exc}",
                        items_in=_batch_len(ctx) if ctx is not None else 0,
                        items_out=0,
                    ))

                    # Fail-fast: stop pipeline on first stage failure
                    break

            return tuple(results)

        finally:
            self._running = False

    async def run_with_bypass(
        self,
        initial_input: Any,
        *,
        bypass_stages: set[str] | None = None,
    ) -> tuple[StageResult, ...]:
        """Run stages, skipping any stages in bypass_stages.

        Args:
            initial_input: Input to the first non-bypassed stage.
            bypass_stages: Set of stage names to skip.
                          Useful for testing individual stages in isolation.

        """
        bypass = bypass_stages or set()
        if self._running:
            raise RuntimeError("StageOrchestrator.run() is not reentrant")
        self._running = True

        try:
            ctx: Any = initial_input
            results: list[StageResult] = []

            for stage_name, stage in self._stages:
                if stage_name in bypass:
                    continue

                stats = self._stats[stage_name]
                t0 = time.monotonic()

                try:
                    output, telemetry = await stage.process(ctx)
                    dt_ms = (time.monotonic() - t0) * 1000

                    items_in = _batch_len(ctx)
                    items_out = _batch_len(output) if output is not None else 0

                    stats.invocations += 1
                    stats.total_time_ms += dt_ms
                    stats.items_in_total += items_in
                    stats.items_out_total += items_out

                    results.append(StageResult(
                        ok=True,
                        stage_name=stage_name,
                        output_batch=output,
                        telemetry=dict(telemetry),
                        error=None,
                        items_in=items_in,
                        items_out=items_out,
                    ))

                    ctx = output

                except Exception as exc:
                    dt_ms = (time.monotonic() - t0) * 1000
                    stats.errors += 1
                    stats.total_time_ms += dt_ms

                    logger.exception(f"StageOrchestrator: stage '{stage_name}' failed: {exc}")

                    results.append(StageResult(
                        ok=False,
                        stage_name=stage_name,
                        output_batch=None,
                        telemetry={},
                        error=f"{type(exc).__name__}: {exc}",
                        items_in=_batch_len(ctx) if ctx is not None else 0,
                        items_out=0,
                    ))
                    break

            return tuple(results)

        finally:
            self._running = False


def topological_sort(stages: list[tuple[str, "StageLike"]]) -> list[tuple[str, "StageLike"]]:
    """Sort stages topologically by name dependency.

    Convention: stage name ending with '_x_to_y' declares dependency on 'x'.
    Example: 'build' depends on 'match', 'match' depends on 'fetch'.
    """
    name_to_stage = {name: stage for name, stage in stages}
    after: dict[str, set[str]] = {name: set() for name in name_to_stage}

    # Parse dependencies: 'x_to_y' means x must come before y
    for name in name_to_stage:
        m = _DEP_PATTERN.match(name)
        if m:
            before, after_stage = m.group(1), m.group(2)
            if before in after:
                after[before].add(after_stage)

    # Kahn's algorithm
    in_degree = {name: 0 for name in name_to_stage}
    for deps in after.values():
        for d in deps:
            if d in in_degree:
                in_degree[d] += 1

    queue = [name for name, deg in in_degree.items() if deg == 0]
    sorted_names: list[str] = []

    while queue:
        node = queue.pop(0)
        sorted_names.append(node)
        for dependent in after[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(sorted_names) != len(name_to_stage):
        # Cycle detected — return original order
        logger.warning("StageOrchestrator: topological sort found cycle, using original order")
        return stages

    return [(name, name_to_stage[name]) for name in sorted_names]


def _batch_len(obj: Any) -> int:
    """Get the batch length of a stage object."""
    if obj is None:
        return 0
    if isinstance(obj, msgspec.Struct):
        # Find the first list field
        for name in obj.__struct_fields__:
            val = getattr(obj, name, None)
            if isinstance(val, list):
                return len(val)
        return 0
    if isinstance(obj, list):
        return len(obj)
    if hasattr(obj, "__len__"):
        try:
            return len(obj)
        except Exception:
            return 0
    return 0
