"""
DuckDB Read Store — Read-only facade over DuckDBShadowStore
==========================================================

ROLE: Read-only query facade delegating to the canonical DuckDBShadowStore.

DESIGN:
- DuckDBReadStore is a COMPOSITION wrapper — it holds a DuckDBShadowStore
  instance (injected, not owned)
- All read methods delegate to self._store.<method>()
- This class is for READ-ONLY callers that want to avoid coupling to write internals
- For write operations, use DuckDBShadowStore directly

BOUNDARY:
    Read callers (reporters, dashboards, analytics): import DuckDBReadStore
    Write callers (pipeline, coordinators): import DuckDBShadowStore

ASYNC API SURFACE (read-only):
    async_query_recent_findings(limit=10)
    async_query_arrow_batches(sql, params, batch_size)
    async_query_sprint_trend(last_n=10)
    async_query_recent_findings_by_sprint(sprint_id, limit)
    async_query_top_entities_by_sprint(sprint_id, limit)
    async_query_sprint_ioc_summary(sprint_id)
    async_query_top_sources_by_sprint(sprint_id, limit)
    read_target_memory(target_id)
    async_query_source_leaderboard(days=7)
    async_query_sprint_source_stats()
    async_get_recent_findings(limit=10)
    async_get_target_profile(target_id)
    async_get_target_memory(target_id)
    async_get_previous_findings_for_target(target_id, limit)
    async_get_hypothesis_feedback(hypothesis_id)
    async_get_findings_with_envelope(sprint_id, limit)
    async_healthcheck()

SYNC CONVENIENCE (read-only, deprecated async-to-sync wrappers):
    get_sprint_trend(last_n=10)           # → async_query_sprint_trend
    get_source_leaderboard(days=7)        # → async_query_source_leaderboard
    get_sprint_scorecard_trend(last_n=6)  # → _sync_query_scorecard_trend
    get_source_mix_trend(since_ts)        # → _sync_query_source_mix_trend
    get_yield_trend(last_n)               # → _sync_query_yield_trend
    get_scorecard_consistency_check()      # → _sync_query_consistency_check
    get_sprint_delta_comparison(sprint_id, lookback)  # → _sync_query_delta_comparison
    get_high_value_sprint_ranking(last_n) # → _sync_query_high_value_ranking
    get_recent_best_sprints(last_n)       # → _sync_query_best_sprints
    get_recent_worst_sprints(last_n)      # → _sync_query_worst_sprints
"""

from __future__ import annotations

import time as _time
from typing import Any, AsyncIterator

# DTOs — imported from the duckdb_store pre-class block so read callers
# can import DuckDBReadStore without coupling to DuckDBShadowStore internals
from hledac.universal.knowledge.duckdb_store import (
    CanonicalFinding,
    DuckDBShadowStore,
)

__all__ = [
    "DuckDBReadStore",
]


class DuckDBReadStore:
    """
    Read-only facade over DuckDBShadowStore.

    DuckDBReadStore holds a reference to an existing DuckDBShadowStore instance
    (injected via constructor) and exposes only the read/query methods.
    All operations delegate to the underlying store without owning any
    connections, LMDB handles, or executors.

    This class exists so read-only callers (reporters, dashboards, exporters)
    can import DuckDBReadStore without pulling in the full write-path surface area
    or knowing about the canonical write store internals.

    Args:
        store: DuckDBShadowStore instance to wrap. Must be initialized
               and opened (call store.async_initialize() before use).
    """

    __slots__ = ("_store",)

    def __init__(self, store: DuckDBShadowStore) -> None:
        object.__setattr__(self, "_store", store)

    # --------------------------------------------------------------------------
    # Query seams — async read methods (delegates to DuckDBShadowStore)
    # --------------------------------------------------------------------------

    async def async_query_recent_findings(
        self, limit: int = 10
    ) -> list[dict[str, Any]]:
        return await self._store.async_query_recent_findings(limit=limit)

    async def async_query_arrow_batches(
        self,
        sql: str,
        params: list[Any] | None = None,
        batch_size: int = 500,
    ) -> AsyncIterator[Any]:
        async for batch in self._store.async_query_arrow_batches(
            sql=sql, params=params, batch_size=batch_size
        ):
            yield batch

    async def async_query_sprint_trend(
        self, last_n: int = 10
    ) -> list[dict]:
        return await self._store.async_query_sprint_trend(last_n=last_n)

    async def async_query_recent_findings_by_sprint(
        self,
        sprint_id: str,
        limit: int = 20,
    ) -> list[dict]:
        return await self._store.async_query_recent_findings_by_sprint(
            sprint_id=sprint_id, limit=limit
        )

    async def async_query_top_entities_by_sprint(
        self,
        sprint_id: str,
        limit: int = 20,
    ) -> list[dict]:
        return await self._store.async_query_top_entities_by_sprint(
            sprint_id=sprint_id, limit=limit
        )

    async def async_query_sprint_ioc_summary(
        self,
        sprint_id: str,
    ) -> dict:
        return await self._store.async_query_sprint_ioc_summary(sprint_id=sprint_id)

    async def async_query_top_sources_by_sprint(
        self,
        sprint_id: str,
        limit: int = 10,
    ) -> list[dict]:
        return await self._store.async_query_top_sources_by_sprint(
            sprint_id=sprint_id, limit=limit
        )

    async def read_target_memory(
        self, target_id: str
    ):
        return await self._store.read_target_memory(target_id=target_id)

    async def async_query_source_leaderboard(
        self, days: int = 7
    ) -> list[dict]:
        return await self._store.async_query_source_leaderboard(days=days)

    async def async_query_sprint_source_stats(self) -> list[dict]:
        return await self._store.async_query_sprint_source_stats()

    async def async_get_recent_findings(
        self,
        limit: int = 10,
    ) -> list[CanonicalFinding]:
        return await self._store.async_get_recent_findings(limit=limit)

    async def async_get_target_profile(
        self, target_id: str
    ):
        return await self._store.async_get_target_profile(target_id=target_id)

    async def async_get_target_memory(
        self, target_id: str
    ):
        return await self._store.async_get_target_memory(target_id=target_id)

    async def async_get_previous_findings_for_target(
        self,
        target_id: str,
        limit: int = 10,
    ) -> list[CanonicalFinding]:
        return await self._store.async_get_previous_findings_for_target(
            target_id=target_id, limit=limit
        )

    async def async_get_hypothesis_feedback(
        self,
        target_id: str | None = None,
        limit: int = 1000,
    ) -> list[Any]:
        return await self._store.async_get_hypothesis_feedback(
            target_id=target_id, limit=limit
        )

    async def async_get_findings_with_envelope(
        self,
        limit: int = 20,
    ) -> list[dict]:
        return await self._store.async_get_findings_with_envelope(limit=limit)

    async def async_healthcheck(self) -> bool:
        return await self._store.async_healthcheck()

    # --------------------------------------------------------------------------
    # Sync convenience wrappers (DEPRECATED, for backward compat)
    # These wrap the async methods for sync callers (report printing, etc.)
    # --------------------------------------------------------------------------

    def get_sprint_trend(self, last_n: int = 10) -> list[dict]:
        """
        DEPRECATED — use async_query_sprint_trend() instead.

        Convenience sync wrapper — returns last N sprints ordered by ts DESC.
        """
        if not self._store._initialized or self._store._closed:
            return []
        try:
            fut = self._store._executor.submit(
                self._store._sync_query_sprint_trend, last_n
            )
            return fut.result()
        except Exception:
            return []

    def get_source_leaderboard(self, days: int = 7) -> list[dict]:
        """
        DEPRECATED — use async_query_source_leaderboard() instead.

        Convenience sync wrapper — returns top sources by hit rate.
        """
        if not self._store._initialized or self._store._closed:
            return []
        try:
            fut = self._store._executor.submit(
                self._store._sync_query_source_leaderboard,
                _time.time() - days * 86400,
            )
            return fut.result()
        except Exception:
            return []

    def get_sprint_scorecard_trend(self, last_n: int = 6) -> list[dict]:
        """
        Convenience sync wrapper — returns last N scorecards ordered by ts DESC.
        """
        if not self._store._initialized or self._store._closed:
            return []
        try:
            fut = self._store._executor.submit(
                self._store._sync_query_scorecard_trend, last_n
            )
            return fut.result()
        except Exception:
            return []

    def get_source_mix_trend(self, since_ts: float) -> list[dict]:
        """
        Convenience sync wrapper — returns source mix trend since timestamp.
        """
        if not self._store._initialized or self._store._closed:
            return []
        try:
            fut = self._store._executor.submit(
                self._store._sync_query_source_mix_trend, since_ts
            )
            return fut.result()
        except Exception:
            return []

    def get_yield_trend(self, last_n: int) -> list[dict]:
        """
        Convenience sync wrapper — returns yield trend (findings/minute over time).
        """
        if not self._store._initialized or self._store._closed:
            return []
        try:
            fut = self._store._executor.submit(
                self._store._sync_query_yield_trend, last_n
            )
            return fut.result()
        except Exception:
            return []

    def get_scorecard_consistency_check(self) -> dict:
        """
        Convenience sync wrapper — returns consistency check between scorecard and delta.
        """
        if not self._store._initialized or self._store._closed:
            return {}
        try:
            # Get the current sprint_id from the store if available
            sprint_id = getattr(self._store, "_current_sprint_id", None) or ""
            fut = self._store._executor.submit(
                self._store._sync_query_consistency_check, sprint_id
            )
            return fut.result()
        except Exception:
            return {}

    def get_sprint_delta_comparison(
        self, current_sprint_id: str, lookback: int
    ) -> dict:
        """
        Convenience sync wrapper — returns delta comparison between sprints.
        """
        if not self._store._initialized or self._store._closed:
            return {}
        try:
            fut = self._store._executor.submit(
                self._store._sync_query_delta_comparison, current_sprint_id, lookback
            )
            return fut.result()
        except Exception:
            return {}

    def get_high_value_sprint_ranking(self, last_n: int) -> list[dict]:
        """
        Convenience sync wrapper — returns high-value sprint ranking.
        """
        if not self._store._initialized or self._store._closed:
            return []
        try:
            fut = self._store._executor.submit(
                self._store._sync_query_high_value_ranking, last_n
            )
            return fut.result()
        except Exception:
            return []

    def get_recent_best_sprints(self, last_n: int = 5) -> list[dict]:
        """
        Return the top N sprints by yield (new_findings / duration_s).
        """
        if not self._store._initialized or self._store._closed:
            return []
        try:
            fut = self._store._executor.submit(
                self._store._sync_query_best_sprints, last_n
            )
            return fut.result()
        except Exception:
            return []

    def get_recent_worst_sprints(self, last_n: int = 5) -> list[dict]:
        """
        Return the worst N sprints by yield.
        """
        if not self._store._initialized or self._store._closed:
            return []
        try:
            fut = self._store._executor.submit(
                self._store._sync_query_worst_sprints, last_n
            )
            return fut.result()
        except Exception:
            return []