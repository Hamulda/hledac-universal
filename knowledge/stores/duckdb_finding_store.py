"""knowledge/stores/duckdb_finding_store.py — DuckDB FindingStore (F320 Phase 2)

PEP 544 FindingStore implementation wrapping DuckDBSubprocessAdapter.

 duckdb_subprocess_adapter.py (canonical):
   - ActivationResult / FindingQualityDecision msgspec structs
   - async_ingest_findings_batch(findings) -> list[FindingQualityDecision | ActivationResult]
   - subprocess mode: DuckDBProxy (posix_ipc Arrow IPC zero-copy)
   - in-process mode: DuckDBShadowStore direct

Design:
   - Lazy init: first append_batch triggers adapter creation
   - Arrow IPC zero-copy via subprocess DuckDBProxy when available
   - M1 8GB: subprocess isolation chrání MLX Metal před DuckDB memory pressure
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from typing_extensions import AsyncIterator

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    from knowledge.stores.protocols import FindingFilter

logger = logging.getLogger(__name__)

# Default chunk size for batch operations (M1 8GB safe)
_DEFAULT_CHUNK_SIZE = 1024


class DuckDBFindingStore:
    """
    PEP 544 FindingStore wrapping DuckDBSubprocessAdapter.

    Canonical implementation for DuckDB-backed finding storage.
    Supports both subprocess (Arrow IPC zero-copy) and in-process modes.

    M1 8GB invariants:
    - Subprocess mode: DuckDBProxy runs in isolated process (F289/F291)
    - In-process mode: DuckDBShadowStore with max 2 threads (F265-U5)
    - Chunk size 1024 for bounded memory during batch ingest
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        temp_dir: Path | str | None = None,
        chunk_size: int = _DEFAULT_CHUNK_SIZE,
    ):
        self._db_path = Path(db_path) if db_path else None
        self._temp_dir = Path(temp_dir) if temp_dir else None
        self._chunk_size = chunk_size
        self._adapter: Any = None  # DuckDBSubprocessAdapter, lazy init
        self._stats: dict[str, int] = {
            "append_batch_calls": 0,
            "findings_accepted": 0,
            "findings_rejected": 0,
            "findings_duplicate": 0,
            "errors": 0,
        }

    async def _ensure_adapter(self) -> Any:
        """Lazy initialization of DuckDBSubprocessAdapter."""
        if self._adapter is None:
            from hledac.universal.knowledge.duckdb_subprocess_adapter import (
                DuckDBSubprocessAdapter,
            )

            self._adapter = DuckDBSubprocessAdapter(
                db_path=self._db_path,
                temp_dir=self._temp_dir,
            )
            await self._adapter.async_initialize()
            await self._adapter.async_initialize_schema()
        return self._adapter

    async def append(self, finding: "CanonicalFinding") -> None:
        """
        Append single finding — delegates to append_batch.

        For single findings, quality gate overhead is minimal.
        """
        await self.append_batch([finding])

    async def append_batch(
        self, findings: list["CanonicalFinding"]
    ) -> list[Any]:
        """
        Batch append with quality gating.

        Returns list of FindingQualityDecision | ActivationResult (1:1 invariant).
        M1 8GB: chunks of 1024 rows (Arrow batch optimal size).
        """
        if not findings:
            return []

        try:
            adapter = await self._ensure_adapter()
            results = await adapter.async_ingest_findings_batch(findings)
            self._stats["append_batch_calls"] += 1

            # Aggregate stats from results
            for r in results:
                if hasattr(r, "accepted"):
                    if r.accepted:
                        self._stats["findings_accepted"] += 1
                    else:
                        self._stats["findings_rejected"] += 1
                elif hasattr(r, "reason"):
                    if r.reason == "duplicate":
                        self._stats["findings_duplicate"] += 1
                    else:
                        self._stats["findings_rejected"] += 1

            return results

        except Exception as e:
            logger.warning("[DuckDBFindingStore] append_batch failed: %s", e)
            self._stats["errors"] += 1
            # Return error decisions for all findings
            from hledac.universal.knowledge.duckdb_subprocess_adapter import (
                FindingQualityDecision,
            )

            return [
                FindingQualityDecision(
                    accepted=False,
                    reason="store_error",
                    entropy=0.0,
                    normalized_hash=None,
                    duplicate=False,
                )
                for _ in findings
            ]

    def query(self, filter: "FindingFilter") -> Iterator[dict[str, Any]]:
        """
        Synchronous query iterator.

        DuckDBSubprocessAdapter doesn't expose sync query.
        For Phase 2, returns empty iterator. Use query_async for actual results.
        """
        return iter([])

    async def query_async(
        self, filter: "FindingFilter"
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Async query iterator.

        Note: Subprocess adapter doesn't expose query API directly.
        For Phase 2, we return empty results. Phase 3 will wire this to the
        actual DuckDBShadowStore query path via the composite store.
        """
        # TODO: Wire to actual query path (DuckDBShadowStore.async_query_recent_findings)
        return
        if False:
            yield {}  # unreachable but makes type checker happy

    def get_stats(self) -> dict[str, Any]:
        """Return DuckDBFindingStore statistics."""
        return {
            **self._stats,
            "db_path": str(self._db_path) if self._db_path else ":memory:",
            "adapter_initialized": self._adapter is not None,
            "adapter_mode": (
                self._adapter.duckdb_mode() if self._adapter else "not_initialized"
            ),
        }

    async def close(self) -> None:
        """Close the adapter."""
        if self._adapter is not None:
            await self._adapter.aclose()
            self._adapter = None

    def __repr__(self) -> str:
        return f"DuckDBFindingStore(db_path={self._db_path!r})"
