"""
Issue #18: IOC Co-occurrence Mining — Rust-only Engine

Problem: Python O(n²) fallback in _rust_cooccurrence_worker silently triggered
when Rust engine unavailable. With 50 IOC/finding × 10_000 findings =
12M pairs worst-case — sprint bottleneck.

Solution: Python fallback ELIMINATED. Rust engine is a hard requirement.
If rust_extensions are unavailable, IOCooccurrenceEngineUnavailable is raised
at __init__ time (not at analyze() time), failing fast.

Architecture:
    Accumulated findings in DuckDB (per sprint)
            │
            ▼
    IOCooccurrenceMiner.analyze(findings)
            │
            ├─► msgspec.to_builtins() → dicts (cheap IPC serialization)
            ├─► asyncio.to_thread() — Rust compute_cooccurrence_edges_py()
            │       (cpu_pool: 4 P-cores, rayon parallel across batch)
            │
            ├─► Top pairs by confidence (support × confidence)
            │
            └─► SpeculativeEdge[] → SpeculativePrefetcher

M1 8GB constraints:
- In-memory co-occurrence matrix bounded: _MAX_PAIRS=10_000
- asyncio.to_thread() for non-blocking Rust call (no ProcessPoolExecutor)
- Rust engine: ~10× faster than pure Python (HashMap vs dict + ahash vs FNV)
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec
from typing import Final

if TYPE_CHECKING:
    from knowledge.duckdb_store import CanonicalFinding

from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_ok

logger = logging.getLogger(__name__)

# Bounded co-occurrence matrix (in-process state)
_MAX_PAIRS: Final[int] = 10_000
# Findings batch cap
_MAX_FINDINGS_PER_CALL: Final[int] = 10_000


class IOCooccurrenceEngineUnavailable(RuntimeError):
    """Raised when Rust co-occurrence engine is unavailable.

    Issue #18: Python O(n²) fallback eliminated. Sprint must have
    rust_extensions built and installed — no silent fallback.
    """


# Rust engine (lazy import — hard requirement, raises on failure)

_rust_engine_available: bool = False
_compute_cooccurrence_edges_py: Any = None
_batch_cooccurrence_edges_py: Any = None


def _try_import_rust_engine() -> bool:
    """Lazy import of Rust co-occurrence engine. Raises if unavailable."""
    global _rust_engine_available, _compute_cooccurrence_edges_py, _batch_cooccurrence_edges_py
    if _rust_engine_available:
        return True
    try:
        from hledac_rust_extensions import (
            compute_cooccurrence_edges_py,
            batch_cooccurrence_edges_py,
        )
        _compute_cooccurrence_edges_py = compute_cooccurrence_edges_py
        _batch_cooccurrence_edges_py = batch_cooccurrence_edges_py
        _rust_engine_available = True
        logger.debug("[IOC] Rust co-occurrence engine loaded")
        return True
    except ImportError as exc:
        raise IOCooccurrenceEngineUnavailable(
            "Rust co-occurrence engine unavailable. "
            "Build rust_extensions: cd rust_extensions && cargo build --release. "
            f"Original error: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CoOccurrencePair:
    """A co-occurrence relationship between two IOCs."""
    ioc_a: str
    ioc_b: str
    ioc_type_a: str
    ioc_type_b: str
    support: int = 1
    confidence_a_to_b: float = 0.0
    confidence_b_to_a: float = 0.0
    last_seen: float = 0.0
    score: float = 0.0


@dataclass(slots=True)
class SpeculativeEdge:
    """A speculative IOC connection for prefetch."""
    source_ioc: str
    source_type: str
    target_ioc: str
    target_type: str
    confidence: float
    reason: str
    prefetch_priority: int
    speculative: bool = True


class IOCounterStats(msgspec.Struct, gc=False):
    """IOC co-occurrence mining statistics."""
    findings_analyzed: int = 0
    pairs_mined: int = 0
    speculative_edges: int = 0
    prefetch_tasks_dispatched: int = 0
    compute_time_ms: float = 0.0
    rust_used: bool = True  # Always True — no Python fallback


# ---------------------------------------------------------------------------
# IOCooccurrenceMiner — main class
# ---------------------------------------------------------------------------

class IOCooccurrenceMiner:
    """
    Mines co-occurrence patterns from CanonicalFinding objects.

    Generates SpeculativeEdge recommendations for prefetch.

    Issue #18: Rust engine is a HARD REQUIREMENT. No Python fallback.
    asyncio.to_thread() runs the Rust compute_cooccurrence_edges_py() in a
    thread pool without blocking the event loop.
    """

    def __init__(self, lmdb_path: Path | None = None) -> None:
        self._pairs: dict[tuple[str, str], CoOccurrencePair] = {}
        self._ioc_counts: dict[str, int] = defaultdict(int)
        self._type_counts: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()
        self._stats = IOCounterStats()
        self._lmdb_path = lmdb_path
        # Issue #18: Fail fast at __init__ if Rust engine unavailable
        _try_import_rust_engine()

    @staticmethod
    def extract_iocs_from_finding(finding: CanonicalFinding) -> list[tuple[str, str]]:
        """Extract (ioc_value, ioc_type) pairs from a CanonicalFinding.

        Note: This is only for external callers that need raw IOC extraction.
        The analyze() path uses Rust engine internally.
        """
        # Lazy import to avoid early dependency
        try:
            from hledac_rust_extensions import extract_iocs as _extract_iocs_rust
            return _extract_iocs_rust(finding.payload_text or "")
        except ImportError:
            # Fallback to Python regex extraction (only for extract_iocs_from_finding)
            pass
        return _extract_iocs_python(finding.payload_text or "")

    async def analyze(self, findings: list[CanonicalFinding]) -> list[SpeculativeEdge]:
        """
        Analyze findings and return speculative IOC edges.

        Issue #18: Rust engine ONLY. asyncio.to_thread() runs the CPU-bound
        Rust computation in a thread pool without blocking the event loop.

        Raises:
            IOCooccurrenceEngineUnavailable: if Rust engine fails at analyze() time
                (e.g., module unloaded after __init__). At __init__ time this is
                already checked, so this is a safety net for edge cases.
        """
        t0 = time.monotonic()

        if len(findings) > _MAX_FINDINGS_PER_CALL:
            findings = findings[:_MAX_FINDINGS_PER_CALL]

        # Ensure Rust engine is available (safety net — should be caught at __init__)
        _try_import_rust_engine()

        # msgspec.to_builtins: 5-10× faster than pickle.dumps, zero-copy for msgspec.Struct
        finding_dicts: list[dict] = []
        for f in findings:
            try:
                finding_dicts.append(msgspec.to_builtins(f))
            except TypeError:
                # Non-msgspec type (e.g. MockCanonicalFinding in tests)
                finding_dicts.append({
                    "finding_id": getattr(f, "finding_id", ""),
                    "payload_text": getattr(f, "payload_text", None),
                })

        # Issue #18: Rust engine only — asyncio.to_thread for non-blocking call
        try:
            raw_edges: list[tuple] = await asyncio.to_thread(
                _compute_cooccurrence_edges_py, finding_dicts
            )
        except Exception as exc:
            raise IOCooccurrenceEngineUnavailable(
                f"Rust co-occurrence engine failed at analyze() time: {exc}"
            ) from exc

        self._stats.rust_used = True

        # Convert raw edge tuples → SpeculativeEdge dataclass objects
        edges = [
            SpeculativeEdge(
                source_ioc=r[0],
                source_type=r[1],
                target_ioc=r[2],
                target_type=r[3],
                confidence=r[4],
                reason=r[5],
                prefetch_priority=r[6],
                speculative=True,
            )
            for r in raw_edges
        ]

        # Update in-process state with newly computed pairs (for cross-batch continuity)
        await self._update_pairs_from_edges(raw_edges)

        self._stats.findings_analyzed += len(findings)
        self._stats.speculative_edges = len(edges)
        self._stats.compute_time_ms = (time.monotonic() - t0) * 1000

        logger.debug(
            "[IOC] analyzed %d findings → %d edges (%.1fms, rust=True)",
            len(findings), len(edges), self._stats.compute_time_ms,
        )
        return edges

    async def _update_pairs_from_edges(self, raw_edges: list[tuple]) -> None:
        """Update in-process _pairs state from raw edge results."""
        async with self._lock:
            for edge in raw_edges:
                val_a, type_a, val_b, type_b = edge[0], edge[1], edge[2], edge[3]
                support = int(edge[5].split()[-2]) if edge[5] else 1

                key = (val_a, val_b) if val_a <= val_b else (val_b, val_a)
                existing = self._pairs.get(key)
                if existing:
                    existing.support = max(existing.support, support)
                    existing.last_seen = time.time()
                else:
                    if len(self._pairs) >= _MAX_PAIRS:
                        self._evict_lru()
                    norm_a, norm_b = key
                    norm_type_a = type_a if val_a <= val_b else type_b
                    norm_type_b = type_b if val_a <= val_b else type_a
                    self._pairs[key] = CoOccurrencePair(
                        ioc_a=norm_a,
                        ioc_b=norm_b,
                        ioc_type_a=norm_type_a,
                        ioc_type_b=norm_type_b,
                        support=support,
                        last_seen=time.time(),
                    )

    def _evict_lru(self) -> None:
        """Evict lowest-support pairs to make room for new ones."""
        if not self._pairs:
            return
        sorted_pairs = sorted(self._pairs.items(), key=lambda x: x[1].support)
        evict_count = max(1, len(self._pairs) // 10)
        for key, _ in sorted_pairs[:evict_count]:
            del self._pairs[key]

    async def persist(self, db_path: Path) -> None:
        """Persist co-occurrence matrix to SQLite for cross-sprint recall."""
        await asyncio.to_thread(self._persist_sync, db_path)

    def _persist_sync(self, db_path: Path) -> None:
        """Synchronous persistence to SQLite."""
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ioc_cooccurrence "
            "(ioc_a TEXT, ioc_b TEXT, ioc_type_a TEXT, ioc_type_b TEXT, "
            "support INTEGER, confidence REAL, score REAL, last_seen REAL)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_score ON ioc_cooccurrence(score DESC)")
        conn.execute("DELETE FROM ioc_cooccurrence")
        for pair in self._pairs.values():
            if pair.support >= 2:
                conn.execute(
                    "INSERT INTO ioc_cooccurrence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        pair.ioc_a, pair.ioc_b, pair.ioc_type_a, pair.ioc_type_b,
                        pair.support,
                        max(pair.confidence_a_to_b, pair.confidence_b_to_a),
                        pair.score, pair.last_seen,
                    ),
                )
        conn.commit()
        conn.close()

    async def load(self, db_path: Path) -> None:
        """Load co-occurrence matrix from SQLite at sprint start."""
        await asyncio.to_thread(self._load_sync, db_path)

    def _load_sync(self, db_path: Path) -> None:
        """Synchronous load from SQLite."""
        if not db_path.exists():
            return
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            "SELECT ioc_a, ioc_b, ioc_type_a, ioc_type_b, support, "
            "confidence, score, last_seen FROM ioc_cooccurrence "
            "ORDER BY score DESC LIMIT ?", (_MAX_PAIRS,),
        ).fetchall()
        conn.close()
        self._pairs.clear()
        for row in rows:
            pair = CoOccurrencePair(
                ioc_a=row[0], ioc_b=row[1],
                ioc_type_a=row[2], ioc_type_b=row[3],
                support=row[4],
                confidence_a_to_b=row[5], confidence_b_to_a=row[5],
                score=row[6], last_seen=row[7],
            )
            self._pairs[(row[0], row[1])] = pair

    async def get_speculative_edges_for_ioc(
        self, ioc_value: str, limit: int = 5
    ) -> list[SpeculativeEdge]:
        """Get top speculative edges originating from a specific IOC."""
        edges: list[SpeculativeEdge] = []
        async with self._lock:
            for pair in self._pairs.values():
                if pair.ioc_a == ioc_value and pair.support >= 2:
                    edges.append(SpeculativeEdge(
                        source_ioc=pair.ioc_a,
                        source_type=pair.ioc_type_a,
                        target_ioc=pair.ioc_b,
                        target_type=pair.ioc_type_b,
                        confidence=pair.confidence_a_to_b,
                        reason=f"co-occurred in {pair.support} findings",
                        prefetch_priority=max(0, 100 - int(pair.score)),
                        speculative=True,
                    ))
                elif pair.ioc_b == ioc_value and pair.support >= 2:
                    edges.append(SpeculativeEdge(
                        source_ioc=pair.ioc_b,
                        source_type=pair.ioc_type_b,
                        target_ioc=pair.ioc_a,
                        target_type=pair.ioc_type_a,
                        confidence=pair.confidence_b_to_a,
                        reason=f"co-occurred in {pair.support} findings",
                        prefetch_priority=max(0, 100 - int(pair.score)),
                        speculative=True,
                    ))
        edges.sort(key=lambda e: (e.prefetch_priority, -e.confidence))
        return edges[:limit]

    def get_stats(self) -> IOCounterStats:
        """Return mining statistics."""
        return self._stats

    async def aclose(self) -> None:
        """No-op: no ProcessPoolExecutor to shutdown."""
        pass


# ---------------------------------------------------------------------------
# Pure-Python IOC extraction (only for extract_iocs_from_finding external API)
# Issue #8: Patterns consolidated from ioc_patterns.rs (single source of truth)
# ---------------------------------------------------------------------------

import re

# Domain: \b boundary, case-insensitive (lowercased on extraction)
_DOMAIN_PATTERN = re.compile(
    r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b'
)
_IPV4_PATTERN = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}'
    r'(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b'
)
_IPV6_PATTERN = re.compile(
    r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b'
)
_URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')
_MD5_PATTERN = re.compile(r'\b[a-fA-F0-9]{32}\b')
_SHA1_PATTERN = re.compile(r'\b[a-fA-F0-9]{40}\b')
_SHA256_PATTERN = re.compile(r'\b[a-fA-F0-9]{64}\b')
_EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
_CVE_PATTERN = re.compile(r'CVE-\d{4}-\d{4,}')


def _is_valid_hex_hash(value: str, expected_len: int) -> bool:
    """Validate hex hash to prevent false positives without \\b boundaries."""
    return len(value) == expected_len and all(c in '0123456789abcdefABCDEF' for c in value)


def _extract_iocs_python(payload: str) -> list[tuple[str, str]]:
    """Extract (ioc_value, ioc_type) pairs from text. Only for external API."""
    iocs: list[tuple[str, str]] = []

    for m in _DOMAIN_PATTERN.finditer(payload):
        val = m.group().lower()
        if len(val) > 3:
            iocs.append((val, "domain"))

    for m in _IPV4_PATTERN.finditer(payload):
        iocs.append((m.group(), "ipv4"))

    for m in _IPV6_PATTERN.finditer(payload):
        iocs.append((m.group(), "ipv6"))

    for m in _URL_PATTERN.finditer(payload):
        val = m.group().lower()
        if len(val) > 8:
            iocs.append((val, "url"))

    for m in _MD5_PATTERN.finditer(payload):
        val = m.group().lower()
        if _is_valid_hex_hash(val, 32):
            iocs.append((val, "md5"))

    for m in _SHA1_PATTERN.finditer(payload):
        val = m.group().lower()
        if _is_valid_hex_hash(val, 40):
            iocs.append((val, "sha1"))

    for m in _SHA256_PATTERN.finditer(payload):
        val = m.group().lower()
        if _is_valid_hex_hash(val, 64):
            iocs.append((val, "sha256"))

    for m in _EMAIL_PATTERN.finditer(payload):
        iocs.append((m.group().lower(), "email"))

    for m in _CVE_PATTERN.finditer(payload):
        iocs.append((m.group(), "cve"))

    return iocs


# ---------------------------------------------------------------------------
# SpeculativePrefetcher (unchanged from P4-2)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PrefetcherStats:
    """Statistics for the speculative prefetcher."""
    edges_received: int = 0
    prefetch_dispatched: int = 0
    prefetch_completed: int = 0
    prefetch_failed: int = 0


class SpeculativePrefetcher:
    """
    Takes SpeculativeEdges from IOCooccurrenceMiner and fires prefetch tasks.

    Architecture:
        IOCooccurrenceMiner.analyze() → SpeculativeEdge[]
                │
                ▼
        SpeculativePrefetcher.dispatch_batch(edges)
                │
                ├─► validate edge (not stale, not already fetched)
                ├─► build fetch task (URL/IP/DNS based on IOC type)
                └─► submit to FetchCoordinator or NonfeedCandidateLedger

    M1 8GB: Batched dispatch (max 10 concurrent prefetches),
            bounded queue for pending prefetches.
    """

    def __init__(
        self,
        fetch_coordinator: Any = None,
        candidate_ledger: Any = None,
    ) -> None:
        self._fetch_coordinator = fetch_coordinator
        self._candidate_ledger = candidate_ledger
        self._seen_edges: set[tuple[str, str]] = set()
        self._seen_lock = asyncio.Lock()
        self._stats = PrefetcherStats()
        self._prefetch_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=100)
        self._workers: list[asyncio.Task[None]] = []
        self._running = False

    async def start(self, num_workers: int = 3) -> None:
        """Start prefetcher workers."""
        if self._running:
            return
        self._running = True
        # F320: asyncio.create_task -> safe_create_task (eager_start, loop probe)
        for i in range(num_workers):
            task = safe_create_task(self._prefetch_worker(worker_id=i), name=f"ioc_miner:prefetch_{i}")
            self._workers.append(task)
        logger.info(f"SpeculativePrefetcher: started {num_workers} workers")

    async def stop(self, timeout: float = 10.0) -> None:
        """Stop prefetcher workers gracefully."""
        if not self._running:
            return
        self._running = False
        for _ in self._workers:
            try:
                self._prefetch_queue.put_nowait((None, None))
            except asyncio.QueueFull:
                pass
        try:
            await asyncio.wait_for(
                safe_gather_ok(*self._workers, label="ioc_cooccurrence_miner:prefetcher"),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("SpeculativePrefetcher: shutdown timeout")
        self._workers.clear()

    async def dispatch_batch(self, edges: list[SpeculativeEdge]) -> int:
        """Dispatch a batch of speculative edges for prefetching."""
        dispatched = 0
        async with self._seen_lock:
            for edge in edges:
                key = (edge.source_ioc, edge.target_ioc)
                if key in self._seen_edges:
                    continue
                self._seen_edges.add(key)
                try:
                    self._prefetch_queue.put_nowait((edge, None))
                    dispatched += 1
                    self._stats.edges_received += 1
                except asyncio.QueueFull:
                    break
        self._stats.prefetch_dispatched += dispatched
        return dispatched

    async def _prefetch_worker(self, worker_id: int) -> None:
        """Worker that processes prefetch tasks from queue."""
        logger.debug(f"SpeculativePrefetcher: worker-{worker_id} started")
        while True:
            try:
                edge, _ = await self._prefetch_queue.get()
                if edge is None:
                    self._prefetch_queue.task_done()
                    logger.debug(f"SpeculativePrefetcher: worker-{worker_id} received poison")
                    return
                await self._execute_prefetch(edge)
                self._prefetch_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"SpeculativePrefetcher: worker-{worker_id} error: {e}")
        logger.debug(f"SpeculativePrefetcher: worker-{worker_id} stopped")

    async def _execute_prefetch(self, edge: SpeculativeEdge) -> None:
        """Execute a single speculative prefetch."""
        try:
            if edge.target_type == "domain":
                if self._candidate_ledger is not None:
                    self._candidate_ledger.add_candidate(
                        candidate=edge.target_ioc,
                        source="speculative_cooccurrence",
                        family="PIVOT",
                        reason=f"co-occurred with {edge.source_ioc} "
                               f"({edge.confidence:.0%} confidence)",
                    )
            elif edge.target_type == "url":
                if self._fetch_coordinator is not None:
                    # F320: asyncio.create_task -> safe_create_task (eager_start, loop probe)
                    safe_create_task(
                        self._fetch_coordinator.prefetch_url(edge.target_ioc),
                        name="ioc_miner:prefetch_url",
                    )
            elif edge.target_type in ("ip", "ipv4"):
                if self._candidate_ledger is not None:
                    self._candidate_ledger.add_candidate(
                        candidate=edge.target_ioc,
                        source="speculative_cooccurrence",
                        family="PIVOT",
                        reason=f"IP co-occurred with {edge.source_ioc}",
                    )
            self._stats.prefetch_completed += 1
        except Exception as e:
            self._stats.prefetch_failed += 1
            logger.warning(
                f"SpeculativePrefetcher: prefetch failed for {edge.target_ioc}: {e}"
            )

    def get_stats(self) -> PrefetcherStats:
        """Return prefetcher statistics."""
        return self._stats
