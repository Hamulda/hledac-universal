"""Public Pipeline Orchestrator — wires public/ stages into a StageOrchestrator.

This module provides the orchestrated version of the public OSINT pipeline
using the StageOrchestrator framework. It composes:

    discovery → fetch → extract → match → build → export

Usage:
    orch = PublicPipelineOrchestrator(
        store=duckdb_store,
        graph=graph_service,
        export_dir="/tmp/export",
    )
    results = await orch.run("example.com")
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from hledac.universal.pipeline._soa_types import (
    FetchedBatch,
    FindingBatch,
    MatchedBatch,
    PageBatch,
    ScoredBatch,
)
from hledac.universal.pipeline._stage_graph import StageOrchestrator, StageResult
from hledac.universal.pipeline.public._build_stage import BuildStage
from hledac.universal.pipeline.public._discovery_stage import DiscoveryStage
from hledac.universal.pipeline.public._export_stage import ExportStage
from hledac.universal.pipeline.public._extract_stage import ExtractStage
from hledac.universal.pipeline.public._fetch_stage import FetchStage
from hledac.universal.pipeline.public._match_stage import MatchStage
from core import aclose

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class PublicPipelineOrchestrator:
    """Orchestrates the public OSINT pipeline using StageOrchestrator.

    Pipeline stages (in order):
        1. discovery  — URL generation (bootstrap, rescue, keyword, live)
        2. fetch      — Per-URL HTTP fetch
        3. extract    — Quality scoring + text analysis
        4. match      — PatternMatcher dispatch
        5. build      — CanonicalFinding construction
        6. export     — Markdown/HTML graph export (terminal)

    M1 8GB safe:
    - Bounded batch sizes (max 256 per stage)
    - Fail-safe: stage errors don't crash pipeline
    - All exceptions caught per stage
    - Telemetry accumulated for observability
    """

    __slots__ = (
        "_orchestrator",
        "_store",
        "_graph_service",
        "_export_dir",
        "_query",
    )

    def __init__(
        self,
        store: Any | None = None,
        graph_service: Any | None = None,
        export_dir: str | None = None,
        max_batch_size: int = 256,
    ) -> None:
        """Initialize the public pipeline orchestrator.

        Args:
            store: DuckDBShadowStore for canonical write (optional).
            graph_service: DuckPGQGraph for entity graph (optional).
            export_dir: Directory for Markdown/HTML export (optional).
            max_batch_size: Upper bound on batch sizes (default 256).

        """
        self._store = store
        self._graph_service = graph_service
        self._export_dir = export_dir
        self._query = ""

        # Wire up stages in execution order
        stages = [
            ("discovery", DiscoveryStage()),
            ("fetch", FetchStage(
                fetch_timeout_s=35.0,
                fetch_max_bytes=2_000_000,
                fetch_concurrency=8,
            )),
            ("extract", ExtractStage()),
            ("match", MatchStage(match_concurrency=8)),
            ("build", BuildStage(source_type="live_public_pipeline")),
            ("export", ExportStage(export_dir=export_dir)),
        ]

        self._orchestrator = StageOrchestrator(stages)

    @property
    def name(self) -> str:
        return "public_pipeline"

    async def run(
        self,
        query: str,
        **kwargs: Any,
    ) -> tuple[StageResult, ...]:
        """Run the public pipeline for a query.

        Args:
            query: The OSINT query string.
            **kwargs: Additional arguments passed to stages.

        Returns:
            Tuple of StageResult, one per stage.

        """
        self._query = query

        # Run orchestrator with query as initial input
        results = await self._orchestrator.run(
            initial_input=query,
            max_batch_size=kwargs.get("max_batch_size", 256),
        )

        return results

    def get_stats(self) -> dict[str, Any]:
        """Return per-stage statistics."""
        return {
            name: {
                "invocations": stats.invocations,
                "total_time_ms": stats.total_time_ms,
                "items_in_total": stats.items_in_total,
                "items_out_total": stats.items_out_total,
                "errors": stats.errors,
            }
            for name, stats in self._orchestrator.get_stats().items()
        }


# Convenience function for direct use
async def run_public_pipeline_orchestrated(
    query: str,
    store: Any | None = None,
    graph_service: Any | None = None,
    export_dir: str | None = None,
) -> tuple[StageResult, ...]:
    """Run the public pipeline in orchestrated mode.

    Args:
        query: The OSINT query string.
        store: DuckDBShadowStore instance (optional).
        graph_service: DuckPGQGraph instance (optional).
        export_dir: Directory for exports (optional).

    Returns:
        Tuple of StageResult from each stage.

    """
    orch = PublicPipelineOrchestrator(
        store=store,
        graph_service=graph_service,
        export_dir=export_dir,
    )
    return await orch.run(query)
