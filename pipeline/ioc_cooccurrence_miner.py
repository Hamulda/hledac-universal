"""Issue #18: IOC Co-occurrence Mining — Rust-only Engine.

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
            └─► SpeculativeEdge[] (returned to caller for downstream prefetch)

M1 8GB constraints:
- In-memory co-occurrence matrix bounded: _MAX_PAIRS=10_000
- asyncio.to_thread() for non-blocking Rust call (no ProcessPoolExecutor)
- Rust engine: ~10× faster than pure Python (HashMap vs dict + ahash vs FNV)
"""
from __future__ import annotations
import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from typing import Final

import msgspec

from hledac.universal.compat.msgspec_gc_compat import Struct
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
from hledac.universal.utils.asyncx import safe_create_task, safe_wait_for, parallel
logger = logging.getLogger(__name__)
_MAX_PAIRS: Final[int] = 10000
_MAX_FINDINGS_PER_CALL: Final[int] = 10000

# D4: SIMD Aho-Corasick pre-filter (lazy import to avoid circular deps)
_SIMD_PREFILTER_AVAILABLE: bool = False
_ioc_prefilter_batch: Any = None


def _try_import_simd_prefilter() -> bool:
    """D4: Lazy import of SIMD Aho-Corasick pre-filter."""
    global _SIMD_PREFILTER_AVAILABLE, _ioc_prefilter_batch
    if _SIMD_PREFILTER_AVAILABLE:
        return True
    try:
        from rust_extensions.wiring.aho_corasick_simd_wiring import (
            ioc_prefilter_batch as _func,
            simd_aho_available,
        )
        _ioc_prefilter_batch = _func
        _SIMD_PREFILTER_AVAILABLE = simd_aho_available
        if _SIMD_PREFILTER_AVAILABLE:
            logger.debug("[IOC] SIMD Aho-Corasick pre-filter loaded")
        return _SIMD_PREFILTER_AVAILABLE
    except ImportError:
        _SIMD_PREFILTER_AVAILABLE = False
        return False


class IOCooccurrenceEngineUnavailable(RuntimeError):
    """Raised when Rust co-occurrence engine is unavailable.

    Issue #18: Python O(n²) fallback eliminated. Sprint must have
    rust_extensions built and installed — no silent fallback.
    """
_rust_engine_available: bool = False
_compute_cooccurrence_edges_py: Any = None
_batch_cooccurrence_edges_py: Any = None

def _try_import_rust_engine() -> bool:
    """Lazy import of Rust co-occurrence engine. Raises if unavailable."""
    global _rust_engine_available, _compute_cooccurrence_edges_py, _batch_cooccurrence_edges_py
    if _rust_engine_available:
        return True
    try:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust
        compute_cooccurrence_edges_py = rust.raw.compute_cooccurrence_edges_py
        batch_cooccurrence_edges_py = rust.raw.batch_cooccurrence_edges_py
        _compute_cooccurrence_edges_py = compute_cooccurrence_edges_py
        _batch_cooccurrence_edges_py = batch_cooccurrence_edges_py
        _rust_engine_available = True
        logger.debug("[IOC] Rust co-occurrence engine loaded")
        return True
    except ImportError as exc:
        raise IOCooccurrenceEngineUnavailable(f"Rust co-occurrence engine unavailable. Build rust_extensions: cd rust_extensions && cargo build --release. Original error: {exc}") from exc

class CoOccurrencePair(Struct):
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

class SpeculativeEdge(Struct):
    """A speculative IOC connection for prefetch."""

    source_ioc: str
    source_type: str
    target_ioc: str
    target_type: str
    confidence: float
    reason: str
    prefetch_priority: int
    speculative: bool = True

class IOCounterStats(Struct):
    """IOC co-occurrence mining statistics."""

    findings_analyzed: int = 0
    pairs_mined: int = 0
    speculative_edges: int = 0
    prefetch_tasks_dispatched: int = 0
    compute_time_ms: float = 0.0
    rust_used: bool = True

class IOCooccurrenceMiner:
    """Mines co-occurrence patterns from CanonicalFinding objects.

    Generates SpeculativeEdge recommendations for prefetch.

    Issue #18: Rust engine is a HARD REQUIREMENT. No Python fallback.
    asyncio.to_thread() runs the Rust compute_cooccurrence_edges_py() in a
    thread pool without blocking the event loop.

    D4: SIMD Aho-Corasick pre-filter is used in prefilter_findings() method
    via module-level lazy import, not as an instance attribute.
    """

    __slots__ = tuple(("_duckdb_store", "_ioc_counts", "_lock", "_pairs", "_stats", "_type_counts"))

    def __init__(self, duckdb_store: Any | None=None) -> None:
        """Initialize IOCooccurrenceMiner with DuckDB store."""
        self._pairs: dict[tuple[str, str], CoOccurrencePair] = {}
        self._ioc_counts: dict[str, int] = defaultdict(int)
        self._type_counts: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()
        self._stats = IOCounterStats()
        self._duckdb_store = duckdb_store
        _try_import_rust_engine()
        _try_import_simd_prefilter()  # D4: Initialize SIMD pre-filter (module-level lazy import)

    @property
    def simd_prefilter_available(self) -> bool:
        """D4: True if SIMD Aho-Corasick pre-filter is available."""
        return _SIMD_PREFILTER_AVAILABLE

    @staticmethod
    def extract_iocs_from_finding(finding: CanonicalFinding) -> list[tuple[str, str]]:
        """Extract (ioc_value, ioc_type) pairs from a CanonicalFinding.

        Note: This is only for external callers that need raw IOC extraction.
        The analyze() path uses Rust engine internally.
        """
        try:
            # R6: Centralized Rust access via core.rust_backend
            from hledac.universal._core.rust_backend import rust
            _extract_iocs_rust = rust.raw.extract_iocs
            return _extract_iocs_rust(finding.payload_text or "")
        except ImportError:  # noqa: BLE001
            pass
        return _extract_iocs_python(finding.payload_text or "")

    @staticmethod
    def extract_iocs_from_findings_batch(
        findings: list[CanonicalFinding],
    ) -> list[list[tuple[str, str]]]:
        """Batch IOC extraction for multiple CanonicalFindings via Rust rayon pool.

        Issue E1: Replaces sequential loop of extract_iocs_from_finding() calls
        with a single rayon-parallel batch call — ~4× speedup on 4P+4E M1 cores.

        Delegates to public_patterns.extract_iocs_from_texts() which handles:
          - batch >= 4 texts OR total >= 16KB → Rust batch_extract_iocs_simd_indexed
          - small batches → per-text extract_iocs_from_text (SIMD for >1KB)

        Args:
            findings: List of CanonicalFinding objects to extract IOCs from.

        Returns:
            List of IOC lists, one per input finding in same order.
            Returns [[] * len(findings)] on any error (fail-safe).

        """
        texts: list[str] = [getattr(f, "payload_text", "") or "" for f in findings]
        # Import here to avoid circular dependency — public_patterns is a sibling pipeline module
        try:
            from hledac.universal.pipeline.public_patterns import extract_iocs_from_texts
            return extract_iocs_from_texts(texts)
        except Exception:  # noqa: BLE001
            # Fail-safe: return empty lists matching input length
            return [[] for _ in findings]

    # D4: SIMD pre-filter methods
    async def prefilter_findings(self, findings: list[CanonicalFinding]) -> list[list[tuple[str, str]]]:
        """D4: Fast IOC prefilter using SIMD Aho-Corasick.

        This is a FAST pre-filter that identifies potential IOC regions
        in findings before full co-occurrence analysis. Reduces Rust engine
        workload by ~50% by filtering out findings with no IOC content.

        Args:
            findings: List of CanonicalFinding objects

        Returns:
            List of IOC lists, one per input finding
        """
        if not findings:
            return []

        texts: list[str] = [getattr(f, "payload_text", "") or "" for f in findings]

        # Try SIMD pre-filter first
        if _SIMD_PREFILTER_AVAILABLE and _ioc_prefilter_batch is not None:
            try:
                return await asyncio.to_thread(_ioc_prefilter_batch, texts)
            except Exception:  # noqa: BLE001
                pass

        # Fallback: use extract_iocs_from_findings_batch
        return self.extract_iocs_from_findings_batch(findings)

    async def prefilter_and_analyze(
        self, findings: list[CanonicalFinding]
    ) -> tuple[list[SpeculativeEdge], list[list[tuple[str, str]]]]:
        """D4: Combined prefilter + analyze for efficiency.

        Runs SIMD pre-filter and co-occurrence analysis together.
        Returns both edges and pre-filter results.

        Args:
            findings: List of CanonicalFinding objects

        Returns:
            Tuple of (speculative_edges, prefilter_iocs)
        """
        # Run prefilter first (fast)
        prefilter_iocs = await self.prefilter_findings(findings)

        # Count findings with IOCs
        ioc_count = sum(1 for iocs in prefilter_iocs if iocs)

        # Run full analysis only if we have findings with IOCs
        if ioc_count > 0:
            edges = await self.analyze(findings)
        else:
            edges = []

        return edges, prefilter_iocs

    async def analyze(self, findings: list[CanonicalFinding]) -> list[SpeculativeEdge]:
        """Analyze findings and return speculative IOC edges.

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
        _try_import_rust_engine()
        finding_dicts: list[dict] = []
        for f in findings:
            try:
                finding_dicts.append(msgspec.to_builtins(f))
            except TypeError:
                finding_dicts.append({"finding_id": getattr(f, "finding_id", ""), "payload_text": getattr(f, "payload_text", None)})
        try:
            raw_edges: list[tuple] = await asyncio.to_thread(_compute_cooccurrence_edges_py, finding_dicts)
        except Exception as exc:
            raise IOCooccurrenceEngineUnavailable(f"Rust co-occurrence engine failed at analyze() time: {exc}") from exc
        self._stats.rust_used = True
        edges = [SpeculativeEdge(source_ioc=r[0], source_type=r[1], target_ioc=r[2], target_type=r[3], confidence=r[4], reason=r[5], prefetch_priority=r[6], speculative=True) for r in raw_edges]
        await self._update_pairs_from_edges(raw_edges)
        self._stats.findings_analyzed += len(findings)
        self._stats.speculative_edges = len(edges)
        self._stats.compute_time_ms = (time.monotonic() - t0) * 1000
        logger.debug("[IOC] analyzed %d findings → %d edges (%.1fms, rust=True)", len(findings), len(edges), self._stats.compute_time_ms)
        return edges

    async def _update_pairs_from_edges(self, raw_edges: list[tuple]) -> None:
        """Update in-process _pairs state from raw edge results."""
        async with self._lock:
            for edge in raw_edges:
                val_a, type_a, val_b, type_b = (edge[0], edge[1], edge[2], edge[3])
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
                    self._pairs[key] = CoOccurrencePair(ioc_a=norm_a, ioc_b=norm_b, ioc_type_a=norm_type_a, ioc_type_b=norm_type_b, support=support, last_seen=time.time())

    def _evict_lru(self) -> None:
        """Evict lowest-support pairs to make room for new ones."""
        if not self._pairs:
            return
        sorted_pairs = sorted(self._pairs.items(), key=lambda x: x[1].support)
        evict_count = max(1, len(self._pairs) // 10)
        for key, _ in sorted_pairs[:evict_count]:
            del self._pairs[key]

    async def persist(self) -> None:
        """Persist co-occurrence matrix to DuckDB for cross-sprint recall.

        Uses DuckDBShadowStore.async_ingest_cooccurrence_batch().
        No-op if duckdb_store is not configured.
        """
        if self._duckdb_store is None:
            return
        pairs = [{"ioc_a": p.ioc_a, "ioc_b": p.ioc_b, "ioc_type_a": p.ioc_type_a, "ioc_type_b": p.ioc_type_b, "support": p.support, "confidence": max(p.confidence_a_to_b, p.confidence_b_to_a), "score": p.score, "last_seen": p.last_seen} for p in self._pairs.values() if p.support >= 2]
        await self._duckdb_store.async_ingest_cooccurrence_batch(pairs)

    async def load(self) -> None:
        """Load co-occurrence matrix from DuckDB at sprint start.

        Uses DuckDBShadowStore.async_load_cooccurrence().
        No-op if duckdb_store is not configured.
        """
        if self._duckdb_store is None:
            return
        rows = await self._duckdb_store.async_load_cooccurrence(limit=_MAX_PAIRS)
        self._pairs.clear()
        for row in rows:
            pair = CoOccurrencePair(ioc_a=row["ioc_a"], ioc_b=row["ioc_b"], ioc_type_a=row["ioc_type_a"], ioc_type_b=row["ioc_type_b"], support=row["support"], confidence_a_to_b=row["confidence"], confidence_b_to_a=row["confidence"], score=row["score"], last_seen=row["last_seen"])
            self._pairs[row["ioc_a"], row["ioc_b"]] = pair

    async def get_speculative_edges_for_ioc(self, ioc_value: str, limit: int=5) -> list[SpeculativeEdge]:
        """Get top speculative edges originating from a specific IOC."""
        edges: list[SpeculativeEdge] = []
        async with self._lock:
            for pair in self._pairs.values():
                if pair.ioc_a == ioc_value and pair.support >= 2:
                    edges.append(SpeculativeEdge(source_ioc=pair.ioc_a, source_type=pair.ioc_type_a, target_ioc=pair.ioc_b, target_type=pair.ioc_type_b, confidence=pair.confidence_a_to_b, reason=f"co-occurred in {pair.support} findings", prefetch_priority=max(0, 100 - int(pair.score)), speculative=True))
                elif pair.ioc_b == ioc_value and pair.support >= 2:
                    edges.append(SpeculativeEdge(source_ioc=pair.ioc_b, source_type=pair.ioc_type_b, target_ioc=pair.ioc_a, target_type=pair.ioc_type_a, confidence=pair.confidence_b_to_a, reason=f"co-occurred in {pair.support} findings", prefetch_priority=max(0, 100 - int(pair.score)), speculative=True))
        edges.sort(key=lambda e: (e.prefetch_priority, -e.confidence))
        return edges[:limit]

    def get_stats(self) -> IOCounterStats:
        """Return mining statistics."""
        return self._stats

    async def aclose(self) -> None:
        """No-op: no ProcessPoolExecutor to shutdown."""
        pass
import re
from _core import aclose
_DOMAIN_PATTERN = re.compile("\\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\\.)+[a-zA-Z]{2,}\\b")
_IPV4_PATTERN = re.compile("\\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\\b")
_IPV6_PATTERN = re.compile("\\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\\b")
_URL_PATTERN = re.compile('https?://[^\\s<>"{}|\\\\^`\\[\\]]+')
_MD5_PATTERN = re.compile("\\b[a-fA-F0-9]{32}\\b")
_SHA1_PATTERN = re.compile("\\b[a-fA-F0-9]{40}\\b")
_SHA256_PATTERN = re.compile("\\b[a-fA-F0-9]{64}\\b")
_EMAIL_PATTERN = re.compile("\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b")
_CVE_PATTERN = re.compile("CVE-\\d{4}-\\d{4,}")

def _is_valid_hex_hash(value: str, expected_len: int) -> bool:
    r"""Validate hex hash to prevent false positives without \b boundaries."""
    return len(value) == expected_len and all((c in "0123456789abcdefABCDEF" for c in value))

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

