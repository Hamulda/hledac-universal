"""Public pipeline stages — extracted from live_public_pipeline.py.

This package contains the stage implementations for the public OSINT pipeline.
Each stage is a self-contained module with a single responsibility.

Stages (in execution order):
    1. discovery   — URL generation (bootstrap, rescue, keyword)
    2. fetch       — Per-URL HTTP fetch (delegated to public_fetch.py)
    3. extract     — Text extraction + quality scoring
    4. match       — PatternMatcher dispatch
    5. build       — CanonicalFinding construction
    6. export      — Markdown/HTML graph export

For backwards compatibility, live_public_pipeline.py re-exports all symbols.

F360-REFACTOR: This module now serves as the stable public API contract surface.

Note: This module uses deferred imports via __getattr__ to avoid circular
dependency issues with the hledac.universal namespace.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

# Public API version — semantic versioning for contract surface
public_api_version: str = "1.0"


def __getattr__(name: str) -> Any:
    """Deferred imports to avoid circular dependency with hledac.universal namespace."""
    # Import types from _phases (stable contract surface)
    if name in ("PipelinePageResult", "PipelineRunResult", "DiscoveryPhaseResult", "PipelineContext"):
        from pipeline.public import _phases as _m
        return getattr(_m, name)
    
    # Import main entry point lazily
    if name == "async_run_live_public_pipeline":
        from pipeline.live_public_pipeline import async_run_live_public_pipeline as _fn
        return _fn
    
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore


# ----------------------------------------------------------------------
# ContractSurface Protocol — stable public API contract
# ----------------------------------------------------------------------


class ContractSurface(Protocol):
    """Protocol defining the stable public pipeline contract.

    F360-REFACTOR: This Protocol documents the stable interface that
    callers can rely on. Internal implementation details may change,
    but this surface is guaranteed stable.

    Implemented by: async_run_live_public_pipeline
    """

    async def __call__(
        self,
        query: str,
        store: "DuckDBShadowStore | None" = None,
        *,
        max_results: int = 10,
        fetch_timeout_s: float = 35.0,
        fetch_max_bytes: int = 2000000,
        fetch_concurrency: int = 8,
        hermes_engine: Any | None = None,
        graph: Any | None = None,
        memory_manager: Any | None = None,
        session_id: str | None = None,
        vector_store: Any | None = None,
        run_loop: bool = False,
        rl_steps: int = 0,
        enqueue_hypothesis_pivot: Any | None = None,
        public_bootstrap_enabled: bool = False,
        seed_context: Any | None = None,
        fetch_fn: Any | None = None,
        match_fn: Any | None = None,
        discovery_fn: Any | None = None,
        ct_subdomains_fn: Any | None = None,
        clear_query_cache_fn: Any | None = None,
        export_dir: str | None = None,
        _sprint_id: str = "",
    ) -> "PipelineRunResult":
        """Run the live public OSINT pipeline.

        Args:
            query: Research query string
            store: DuckDBShadowStore for persistence
            max_results: Max discovery results
            fetch_timeout_s: Fetch timeout per page
            fetch_max_bytes: Max bytes per page
            fetch_concurrency: Fetch concurrency limit
            hermes_engine: Optional Hermes inference engine
            graph: Optional graph manager
            memory_manager: Optional memory manager
            session_id: Optional session identifier
            vector_store: Optional vector store for RAG
            run_loop: Enable RL loop
            rl_steps: Max RL steps
            enqueue_hypothesis_pivot: Hypothesis pivot callback
            public_bootstrap_enabled: Enable public bootstrap
            seed_context: Seed context for bootstrap
            fetch_fn: Override fetch function
            match_fn: Override match function
            discovery_fn: Override discovery function
            ct_subdomains_fn: Override CT subdomain function
            clear_query_cache_fn: Custom cache clear function
            export_dir: Export directory
            _sprint_id: Internal sprint ID

        Returns:
            PipelineRunResult with all telemetry and findings
        """
        ...


__all__ = [
    # Version
    "public_api_version",
    # Contract
    "ContractSurface",
    # Types (imported from _phases.py to avoid circular imports)
    "PipelinePageResult",
    "PipelineRunResult",
    "DiscoveryPhaseResult",
    "PipelineContext",
    # Main entry point
    "async_run_live_public_pipeline",
]
