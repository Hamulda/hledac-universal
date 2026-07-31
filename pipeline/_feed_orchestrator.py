"""Feed Pipeline Orchestrator — wires feed/ stages into a StageOrchestrator.

This module provides the orchestrated version of the RSS/Atom feed pipeline
using the StageOrchestrator framework. It composes:
    fetch_feed → assemble → scan → dedup → build_feed

Usage:
    orch = FeedPipelineOrchestrator(
        store=duckdb_store,
        graph=graph_service,
    )
    results = await orch.run("https://example.com/feed.xml")
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from hledac.universal.pipeline._soa_types import FindingBatch
from hledac.universal.pipeline._stage_graph import StageOrchestrator, StageResult
from hledac.universal.pipeline.feed._assemble_stage import AssembleStage
from hledac.universal.pipeline.feed._build_feed_stage import BuildFeedStage
from hledac.universal.pipeline.feed._dedup_stage import DedupStage
from hledac.universal.pipeline.feed._fetch_feed_stage import FetchFeedStage
from hledac.universal.pipeline.feed._scan_stage import ScanStage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class FeedPipelineOrchestrator:
    """Orchestrates the RSS/Atom feed pipeline using StageOrchestrator.

    Pipeline stages (in order):
        1. fetch_feed  — Fetch + parse RSS/Atom feed
        2. assemble    — Text assembly from feed entries
        3. scan        — Pattern scan on assembled text
        4. dedup       — Per-entry + run-level dedup
        5. build_feed — CanonicalFinding construction

    M1 8GB safe:
    - Bounded batch sizes (max 256 per stage)
    - Fail-safe: stage errors don't crash pipeline
    - All exceptions caught per stage
    - Telemetry accumulated for observability
    """

    __slots__ = (
        "_orchestrator",
        "_dedup_stage",
        "_store",
        "_graph_service",
    )

    def __init__(
        self,
        store: Any | None = None,
        graph_service: Any | None = None,
        max_batch_size: int = 256,
    ) -> None:
        """Initialize the feed pipeline orchestrator.

        Args:
            store: DuckDBShadowStore for canonical write (optional).
            graph_service: DuckPGQGraph for entity graph (optional).
            max_batch_size: Upper bound on batch sizes (default 256).

        """
        self._store = store
        self._graph_service = graph_service

        # DedupStage needs special handling (reset between runs)
        self._dedup_stage = DedupStage()

        # Wire up stages in execution order
        stages = [
            ("fetch_feed", FetchFeedStage(
                timeout_s=35.0,
                max_bytes=2_000_000,
            )),
            ("assemble", AssembleStage()),
            ("scan", ScanStage()),
            ("dedup", self._dedup_stage),  # shared instance
            ("build_feed", BuildFeedStage(source_type="rss_atom_pipeline")),
        ]

        self._orchestrator = StageOrchestrator(stages)

    @property
    def name(self) -> str:
        return "feed_pipeline"

    async def run(
        self,
        feed_url: str,
        query_context: str = "",
        **kwargs: Any,
    ) -> tuple[StageResult, ...]:
        """Run the feed pipeline for a feed URL.

        Args:
            feed_url: The RSS/Atom feed URL.
            query_context: Optional query context for findings.
            **kwargs: Additional arguments passed to stages.

        Returns:
            Tuple of StageResult, one per stage.

        """
        # Reset dedup state for this run
        self._dedup_stage.reset()

        # Run orchestrator with feed_url as initial input
        results = await self._orchestrator.run(
            initial_input=feed_url,
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
async def run_feed_pipeline_orchestrated(
    feed_url: str,
    store: Any | None = None,
    graph_service: Any | None = None,
    query_context: str = "",
) -> tuple[StageResult, ...]:
    """Run the feed pipeline in orchestrated mode.

    Args:
        feed_url: The RSS/Atom feed URL.
        store: DuckDBShadowStore instance (optional).
        graph_service: DuckPGQGraph instance (optional).
        query_context: Optional query context for findings.

    Returns:
        Tuple of StageResult from each stage.

    """
    orch = FeedPipelineOrchestrator(
        store=store,
        graph_service=graph_service,
    )
    return await orch.run(feed_url, query_context=query_context)
