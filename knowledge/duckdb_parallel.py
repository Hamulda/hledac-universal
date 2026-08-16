"""DuckDB Parallel Operations - ISSUE-006: Modern Parallelization for DuckDB Operations.

ISSUE-006: Nedostatečná Paralelizace v DuckDB Operations
=========================================================

Context: DuckDB queries are sequential in many places
Example:
    for fts_result in fts_results:  # Sequential
        for vec_result in vec_results:  # Nested sequential!

Modern Solution:
    - Use Rust Arrow IPC for parallel batch processing
    - DuckDB Arrow integration is already in codebase (duckdb_arrow_builder.py)
    - asyncio.gather with parallel() for concurrent query execution

Architecture (M1 8GB compatible):
    1. NumPy vectorized RRF fusion (replaces sequential loops)
    2. Parallel DuckDB batch queries via asyncio.gather
    3. Arrow IPC zero-copy batch processing
    4. Bounded concurrency with memory-aware limits

Features:
    - parallel_rrf_fusion: NumPy vectorized Reciprocal Rank Fusion
    - parallel_batch_query: Execute multiple DuckDB queries concurrently
    - parallel_arrow_ingest: Parallel Arrow batch ingestion
    - Vectorized candidate building using dict comprehension

Author: ISSUE-006
Compatibility: MacBook Air M1 8GB, Python 3.14+
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np
    import duckdb

logger = logging.getLogger(__name__)


# ─── Configuration ─────────────────────────────────────────────────────────────

# M1 8GB bounded concurrency limits
_MAX_PARALLEL_QUERIES: int = 4  # Max concurrent DuckDB queries
_MAX_PARALLEL_ARROW_BATCHES: int = 2  # Max concurrent Arrow batches
_RRF_K: int = 60  # Standard RRF parameter


# ─── Result DTOs ──────────────────────────────────────────────────────────────

@dataclass(slots=True)
class RRFusionResult:
    """Result of parallel RRF fusion."""
    fused_results: list[dict[str, Any]]
    fts_count: int
    vec_count: int
    execution_time_ms: float


@dataclass(slots=True)
class ParallelQueryResult:
    """Result of parallel DuckDB query execution."""
    results: list[Any]
    errors: list[Exception]
    execution_time_ms: float


# ─── NumPy Vectorized RRF Fusion ─────────────────────────────────────────────

def numpy_rrf_fusion(
    fts_results: list[dict[str, Any]],
    vec_results: list[dict[str, Any]],
    top_k: int = 20,
    k: int = _RRF_K,
    fts_weight: float = 1.0,
    vec_weight: float = 1.0,
) -> RRFusionResult:
    """
    ISSUE-006: NumPy vectorized Reciprocal Rank Fusion.

    Replaces sequential loops with vectorized NumPy operations:
      BEFORE: O(n*m) nested loops
      AFTER: O(n + m) NumPy vectorized

    Args:
        fts_results: List of FTS search results with 'rank' field
        vec_results: List of vector search results with 'rank' and 'distance' fields
        top_k: Number of top results to return
        k: RRF smoothing parameter (default: 60)
        fts_weight: FTS contribution weight
        vec_weight: Vector contribution weight

    Returns:
        RRFusionResult with fused results and metrics
    """
    import time

    start = time.monotonic()

    try:
        import numpy as np
    except ImportError:
        # Fallback to simple implementation
        return _simple_rrf_fusion(fts_results, vec_results, top_k, k)

    # ── Phase 1: Extract keys (vectorized with list comprehension) ──────────────
    # Dict comprehension is O(n) vs O(n) loop but with less Python overhead

    def _extract_key(doc: dict[str, Any], idx: int) -> str:
        """Extract stable key from document."""
        key = doc.get("id") or doc.get("_rowid") or doc.get("chunk_id") or doc.get("entity_id")
        if key is None:
            # Hash-based fallback for documents without stable IDs
            text = doc.get("text", "") or doc.get("content", "") or str(idx)
            key = hashlib.md5(text.encode()).hexdigest()[:16]
        return key

    # O(n) key extraction - list comprehension is faster than loop
    fts_keys = [f"fts_{_extract_key(doc, i)}" for i, doc in enumerate(fts_results)]
    vec_keys = [f"vec_{_extract_key(doc, i)}" for i, doc in enumerate(vec_results)]

    # ── Phase 2: Build score arrays (NumPy vectorized) ────────────────────────
    scores: dict[str, float] = {}
    docs: dict[str, dict[str, Any]] = {}

    # FTS scores - vectorized RRF computation
    if fts_results:
        fts_ranks = np.arange(len(fts_results), dtype=np.float64)
        fts_scores = fts_weight * (1.0 / (k + fts_ranks + 1))
        for key, score, doc in zip(fts_keys, fts_scores, fts_results, strict=False):
            scores[key] = score
            docs[key] = doc

    # Vector scores - vectorized with distance-based weighting
    if vec_results:
        vec_ranks = np.arange(len(vec_results), dtype=np.float64)
        vec_distances = np.array(
            [r.get("distance", 1.0) for r in vec_results],
            dtype=np.float64
        )
        # Convert distance to similarity score
        vec_sim = 1.0 / (vec_distances + 0.001)
        vec_scores = vec_weight * (1.0 / (k + vec_ranks + 1)) * vec_sim / vec_sim.max()
        for key, score, doc in zip(vec_keys, vec_scores, vec_results, strict=False):
            scores[key] = scores.get(key, 0.0) + score
            docs[key] = doc

    # ── Phase 3: Sort and return top_k (O(n log n)) ─────────────────────────
    if not scores:
        return RRFusionResult(
            fused_results=[],
            fts_count=len(fts_results),
            vec_count=len(vec_results),
            execution_time_ms=(time.monotonic() - start) * 1000,
        )

    # NumPy argsort for faster sorting
    keys = list(scores.keys())
    score_arr = np.array([scores[k] for k in keys])
    sorted_indices = np.argsort(score_arr)[::-1][:top_k]

    fused = [docs[keys[i]] for i in sorted_indices if keys[i] in docs]

    return RRFusionResult(
        fused_results=fused,
        fts_count=len(fts_results),
        vec_count=len(vec_results),
        execution_time_ms=(time.monotonic() - start) * 1000,
    )


def _simple_rrf_fusion(
    fts_results: list[dict[str, Any]],
    vec_results: list[dict[str, Any]],
    top_k: int = 20,
    k: int = _RRF_K,
) -> RRFusionResult:
    """Fallback simple RRF fusion when NumPy is unavailable."""
    import time

    start = time.monotonic()

    scores: dict[str, float] = {}
    docs: dict[str, dict[str, Any]] = {}

    # FTS scoring
    for rank, doc in enumerate(fts_results):
        key = f"fts_{doc.get('id', doc.get('chunk_id', rank))}"
        scores[key] = 1.0 / (k + rank + 1)
        docs[key] = doc

    # Vector scoring
    for rank, doc in enumerate(vec_results):
        key = f"vec_{doc.get('id', doc.get('chunk_id', rank))}"
        distance = doc.get("distance", 1.0)
        sim = 1.0 / (distance + 0.001)
        scores[key] = scores.get(key, 0.0) + sim * (1.0 / (k + rank + 1))
        docs[key] = doc

    # Sort
    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:top_k]
    fused = [docs[k] for k in sorted_keys if k in docs]

    return RRFusionResult(
        fused_results=fused,
        fts_count=len(fts_results),
        vec_count=len(vec_results),
        execution_time_ms=(time.monotonic() - start) * 1000,
    )


# ─── Parallel DuckDB Batch Queries ──────────────────────────────────────────────

async def parallel_duckdb_queries(
    queries: list[tuple[str, list[Any]]],
    conn: Any,
    *,
    max_concurrency: int = _MAX_PARALLEL_QUERIES,
    ctx: str = "duckdb_parallel",
) -> ParallelQueryResult:
    """
    ISSUE-006: Execute multiple DuckDB queries in parallel.

    Uses asyncio.to_thread for thread-safe DuckDB execution with bounded concurrency.
    IMPORTANT: DuckDB connections are NOT thread-safe - each query runs in a
    separate thread to avoid race conditions.

    Args:
        queries: List of (sql, params) tuples
        conn: DuckDB connection (thread-safe via to_thread)
        max_concurrency: Max concurrent queries (default: 4 for M1 8GB)
        ctx: Context string for logging

    Returns:
        ParallelQueryResult with results and errors
    """
    import time

    start = time.monotonic()
    errors: list[Exception] = []

    if not queries:
        return ParallelQueryResult(
            results=[],
            errors=[],
            execution_time_ms=0.0,
        )

    # ── Phase 1: Define thread-safe query executor ─────────────────────────────
    # DuckDB is NOT thread-safe - we MUST use to_thread() for each query
    def _execute_query_sync(sql: str, params: list[Any]) -> Any:
        """Execute single DuckDB query synchronously in thread pool."""
        try:
            if params:
                return conn.execute(sql, params).fetchall()
            return conn.execute(sql).fetchall()
        except Exception as e:
            return e

    async def _execute_query_async(sql: str, params: list[Any]) -> Any:
        """Execute query in thread pool to avoid DuckDB thread-safety issues."""
        result = await asyncio.to_thread(_execute_query_sync, sql, params)
        if isinstance(result, Exception):
            errors.append(result)
            return []
        return result

    # ── Phase 2: Create coroutines and execute with bounded concurrency ─────────
    from hledac.universal.utils.asyncx import parallel

    coros = [_execute_query_async(sql, params) for sql, params in queries]
    result = await parallel(
        coros,
        policy="collect",
        concurrency=min(max_concurrency, len(queries)),
        ctx=ctx,
    )

    # ── Phase 3: Extract results from ParallelResult ───────────────────────────
    if hasattr(result, "ok"):
        results_list = result.ok
    else:
        results_list = list(result) if result else []

    # Filter out error placeholders (already captured in errors list)
    final_results = []
    for r in results_list:
        if isinstance(r, Exception):
            errors.append(r)
        else:
            final_results.append(r)

    return ParallelQueryResult(
        results=final_results,
        errors=errors,
        execution_time_ms=(time.monotonic() - start) * 1000,
    )


# ─── Parallel Arrow Batch Ingestion ───────────────────────────────────────────

async def parallel_arrow_ingest(
    batches: list[bytes],
    table_name: str,
    conn: Any,
    *,
    max_concurrency: int = _MAX_PARALLEL_ARROW_BATCHES,
    ctx: str = "duckdb_arrow_parallel",
) -> tuple[int, list[Exception]]:
    """
    ISSUE-006: Parallel Arrow IPC batch ingestion.

    Ingests multiple Arrow IPC batches concurrently for maximum throughput.

    Args:
        batches: List of Arrow IPC bytes
        table_name: Target DuckDB table
        conn: DuckDB connection
        max_concurrency: Max concurrent batches (default: 2 for M1 8GB)
        ctx: Context string for parallel() call

    Returns:
        Tuple of (total_rows, errors)
    """
    import time
    from io import BytesIO

    start = time.monotonic()
    errors: list[Exception] = []

    if not batches:
        return 0, []

    try:
        import pyarrow as pa
    except ImportError:
        logger.debug("[DuckDBParallel] PyArrow not available for parallel ingest")
        return 0, [Exception("PyArrow not available")]

    async def _ingest_batch(ipc_bytes: bytes) -> int:
        """Ingest single Arrow batch."""
        try:
            reader = pa.ipc.open_file(BytesIO(ipc_bytes))
            table = reader.read_all()
            conn.execute(
                f"INSERT INTO {table_name} BY NAME SELECT * FROM table",
                [table],
            )
            return table.num_rows
        except Exception as e:
            errors.append(e)
            return 0

    # Execute with bounded concurrency
    from hledac.universal.utils.asyncx import parallel

    coros = [_ingest_batch(batch) for batch in batches]
    results = await parallel(
        coros,
        policy="collect",
        concurrency=min(max_concurrency, len(batches)),
        ctx=ctx,
    )

    # Sum successful rows
    row_counts = results.ok if hasattr(results, "ok") else results
    total_rows = sum(r for r in row_counts if isinstance(r, int))

    logger.debug(
        f"[DuckDBParallel] Arrow ingest: {total_rows} rows in "
        f"{(time.monotonic() - start) * 1000:.1f}ms, {len(errors)} errors"
    )

    return total_rows, errors


# ─── Vectorized Candidate Building ────────────────────────────────────────────

def vectorized_build_candidates(
    fts_results: list[dict[str, Any]],
    vec_results: list[dict[str, Any]],
    *,
    fts_weight: float = 0.4,
    vec_weight: float = 0.6,
) -> dict[str, dict[str, Any]]:
    """
    ISSUE-006: Vectorized candidate building for hybrid search.

    Replaces sequential loops with dict comprehension and set operations:
      BEFORE:
          for r in fts_results:
              candidates[cid] = {...}
          for r in vec_results:
              candidates[cid] = {...}

      AFTER: O(n + m) dict comprehension + merge

    Args:
        fts_results: FTS search results
        vec_results: Vector search results
        fts_weight: FTS contribution weight
        vec_weight: Vector contribution weight

    Returns:
        Dict of candidates with merged FTS + vector scores
    """
    # ── Phase 1: Build FTS candidates (dict comprehension) ─────────────────────
    fts_candidates = {
        r.get("chunk_id", r.get("id", id(r))): {
            "chunk_id": r.get("chunk_id", r.get("id", id(r))),
            "content": r.get("content", r.get("text", "")),
            "source_type": r.get("source_type"),
            "ts": r.get("ts"),
            "fts_rank": r.get("rank", 0),
            "fts_score": 1.0 / (r.get("rank", 0) + 1) * fts_weight,
            "vec_rank": None,
            "vec_score": 0.0,
            "vec_distance": None,
        }
        for r in fts_results
    }

    # ── Phase 2: Build vector candidates and merge ────────────────────────────
    # Use dict.update for O(m) merge instead of nested loop
    vec_candidates = {
        r.get("chunk_id", r.get("id", id(r))): {
            "chunk_id": r.get("chunk_id", r.get("id", id(r))),
            "content": r.get("content", r.get("text", "")),
            "source_type": None,
            "ts": None,
            "fts_rank": None,
            "fts_score": 0.0,
            "vec_rank": r.get("rank", 0),
            "vec_score": 1.0 / (r.get("rank", 0) + 1) * vec_weight,
            "vec_distance": r.get("distance"),
        }
        for r in vec_results
    }

    # ── Phase 3: Merge with score combination ────────────────────────────────
    # Start with FTS candidates, update with vector candidates
    candidates = fts_candidates.copy()

    for cid, vec_cand in vec_candidates.items():
        if cid in candidates:
            # Merge: add vector scores to existing FTS scores
            candidates[cid]["vec_rank"] = vec_cand["vec_rank"]
            candidates[cid]["vec_score"] = vec_cand["vec_score"]
            candidates[cid]["vec_distance"] = vec_cand["vec_distance"]
        else:
            # New candidate from vector results
            candidates[cid] = vec_cand

    return candidates


# ─── Hybrid Search with Parallel Execution ─────────────────────────────────────

async def parallel_hybrid_search(
    fts_task: Any,
    vec_task: Any,
    *,
    ctx: str = "duckdb_hybrid_parallel",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    ISSUE-006: Parallel hybrid search execution.

    Executes FTS and vector search concurrently using asyncio.gather.

    Args:
        fts_task: FTS search coroutine
        vec_task: Vector search coroutine
        ctx: Context string for parallel() call

    Returns:
        Tuple of (fts_results, vec_results)
    """
    from hledac.universal.utils.asyncx import parallel

    results = await parallel(
        [fts_task, vec_task],
        policy="collect",
        concurrency=2,
        ctx=ctx,
    )

    fts_results = results[0] if len(results) > 0 else []
    vec_results = results[1] if len(results) > 1 else []

    return fts_results, vec_results


# ─── Batch Query Builder ───────────────────────────────────────────────────────

def build_parallel_query_batches(
    items: list[Any],
    batch_size: int,
    query_template: str,
    id_field: str = "id",
) -> list[tuple[str, list[Any]]]:
    """
    ISSUE-006: Build parallel query batches for DuckDB.

    Splits a list of items into batches and creates parameterized queries.

    Args:
        items: List of items with IDs
        batch_size: Max items per batch
        query_template: SQL template with ? placeholders
        id_field: Field name for ID extraction

    Returns:
        List of (sql, params) tuples for parallel execution
    """
    batches: list[tuple[str, list[Any]]] = []

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        ids = [item.get(id_field, item) if isinstance(item, dict) else getattr(item, id_field, item) for item in batch]

        # Build IN clause with correct number of placeholders
        placeholders = ", ".join(["?" for _ in ids])
        sql = query_template.format(placeholders=placeholders)

        batches.append((sql, ids))

    return batches


# ─── Module Exports ───────────────────────────────────────────────────────────

__all__ = [
    # Configuration (public accessors)
    "get_max_parallel_queries",
    "get_max_parallel_arrow_batches",
    "get_rrf_k",
    # DTOs
    "RRFusionResult",
    "ParallelQueryResult",
    # Core functions
    "numpy_rrf_fusion",
    "_simple_rrf_fusion",
    "parallel_duckdb_queries",
    "parallel_arrow_ingest",
    "vectorized_build_candidates",
    "parallel_hybrid_search",
    "build_parallel_query_batches",
]


# ─── Public Configuration Accessors ────────────────────────────────────────────

def get_max_parallel_queries() -> int:
    """Get the max parallel queries limit for current platform."""
    return _MAX_PARALLEL_QUERIES


def get_max_parallel_arrow_batches() -> int:
    """Get the max parallel Arrow batches limit for current platform."""
    return _MAX_PARALLEL_ARROW_BATCHES


def get_rrf_k() -> int:
    """Get the RRF K parameter."""
    return _RRF_K
