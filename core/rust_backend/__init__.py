# __init__.py — AccelBackend public API
"""
AccelBackend: lazy-resolving facade for the Rust acceleration layer.






Architecture:
  core/rust_backend/

  ├── __init__.py    ← AccelBackend facade + get_accel() singleton
  ├── _prober.py     ← one-time Rust extension probe (cached, never re-probes)
  ├── bloom.py       ← BloomFilter, UrlSet
  ├── url.py         ← URL normalization, fingerprint, classification
  ├── hash.py        ← ContentHasher, xxHash64, blake3
  ├── quality.py     ← entropy, dedup fingerprint, URL fingerprint
  ├── ioc.py         ← IOC extraction (URLs, IPs, emails, hashes)
  ├── ip.py          ← IP parsing, private/public classification, CIDR
  ├── misc.py        ← graph, hot_edges, aho, evidence, madvise, memory,
  │                     json, spsc, query, text, int_counter, simd,
  │                     sprint_policies + all pure-Python fallbacks

R6: SINGLE ENTRY POINT — all Rust extension access MUST go through this module.
=================================================================================

Canonical import pattern:
    from hledac.universal.core.rust_backend import rust

Domain access (typed, with Python fallback):
    fingerprints = rust.quality.batch_dedup_fingerprints(texts)
    urls = rust.ioc.extract_iocs_flat(html)
    normalized = rust.url.normalize(url)

Raw symbol access (for symbols not yet wrapped in a domain):
    PyAIMDController = rust.raw.PyAIMDController  # None if N/A
    MPSCPool = rust.raw.MPSCPool                  # None if N/A
    result = rust.raw.batch_xxh3_64_bytes(data)    # None if N/A

Feature-gated submodule access:
    dns_mod = rust.dns              # None if 'dns' feature not built
    stix_mod = rust.stix            # None if 'stix' feature not built
    fulltext_mod = rust.fulltext    # None if 'fulltext' feature not built
    native_db_mod = rust.native_db  # None if 'native_db' feature not built

ANTI-PATTERN (do not use):
    # ❌ Direct import bypasses ABI checking + capability scoring + force override
    from hledac_rust_extensions import Something
    try:
        from hledac_rust_extensions import Something
    except ImportError:
        Something = None

    # ✅ Correct:
    from hledac.universal.core.rust_backend import rust
    Something = rust.raw.Something  # None if N/A

Migration helper — replace any `from hledac_rust_extensions import X` with:
    from hledac.universal.core.rust_backend import rust
    X = rust.raw.X  # None if N/A

For submodule imports like `from hledac_rust_extensions import dns`:
    dns = rust.dns  # None if feature not built

For imports with `as` aliases:
    # OLD: from hledac_rust_extensions import batch_fn as _rust_batch_fn
    # NEW: _rust_batch_fn = rust.raw.batch_fn  # None if N/A

Feature flags (Cargo.toml):
    default = ["core", "data"]  # Standard build (~15s compile)
    --features "dns"            # hickory-dns DoH/DoT resolution (~5MB extra)
    --features "native_db"      # MongoDB/Redis/ES wire-protocol extraction
    --features "full"           # Everything (~3× compile time vs default)

Usage:
    from hledac.universal.core.rust_backend import get_accel

    accel = get_accel()
    if accel.is_available:
        fingerprints = accel.quality.batch_dedup_fingerprints(texts)
    else:
        # same API — Python fallback transparently
        fingerprints = accel.quality.batch_dedup_fingerprints(texts)

F350M-R (A3): Container-based force override:
    from hledac.universal.core.rust_backend import get_accel, set_container, RustForce
    from hledac.universal.core.container import get_global_container

    # Sprint-scoped: force Python fallback for this sprint
    container = get_global_container()
    container.register('rust.force', factory=lambda: RustForce(python=True), scope='factory')
    set_container(container)

    accel = get_accel()  # will use Python fallback

For testing:
    from hledac.universal.core.rust_backend import reset_accel
    reset_accel()  # clear singleton and probe cache

Backward compatibility:
    from hledac.universal.core.rust_backend import rust
    rust.bloom.BloomFilter()        # still works
    rust.quality.batch_entropy(...) # still works
"""

import logging
import os
import weakref
import msgspec
from typing import TYPE_CHECKING, Any

__all__ = [
    "AccelBackend",
    "AccelInfo",
    "RustForce",
    "RustRawAccessor",
    "get_accel",
    "set_container",
    "reset_accel",
    "ProbeResult",
    # Backward compat
    "rust",
    "RustBackend",
    "check_metal_availability",
]

# Eager imports — used at module level, must be available immediately
from ._prober import force_python as _force_python
from ._prober import force_rust as _force_rust
from ._prober import probe as _probe
from ._prober import reset as _reset_probe
from ._prober import ProbeResult

# PEP 562: lazy submodule loading via __getattr__
# Each submodule is imported on first attribute access, not at module load.
# This avoids triggering #[pymodule] init in hledac_rust_extensions until needed.
# ISSUE-4.1 fix: 150-300ms import overhead eliminated for submodules not used in a session.
import importlib
import sys
from dataclasses import dataclass as _dataclass

_SUBMODULE_NAMES: tuple[str, ...] = (
    "bloom", "hash", "ip", "ioc", "ioc_dedup", "quality",
    "rolling_hash", "simhash", "url", "lsh",
    "graph", "hot_edges", "aho", "evidence", "madvise",
    "memory", "json", "spsc", "query", "text", "xml",
    "int_counter", "simd", "sprint_policies", "html", "metal",
    # TLS 1.3 JA4 fingerprinting (rustls-based)
    "tls",
    # ISSUE [ULTIMATE]-005: Unicode attribution fingerprint
    "unicode_fingerprint",
    # SWARM-003: Link prediction (Adamic-Adar, Jaccard, Preferential Attachment)
    "link_predictor",
    # misc is used for _TlsDomain backward-compat and html property routing
    "misc",
)

_lazy_mod_cache: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()


def _get_submodule(name: str) -> Any:
    """Lazily import and cache a submodule."""
    if name not in _lazy_mod_cache:
        _lazy_mod_cache[name] = importlib.import_module(f"{__name__}.{name}")
    return _lazy_mod_cache[name]


def __getattr__(name: str) -> Any:
    if name in _SUBMODULE_NAMES:
        return _get_submodule(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

if TYPE_CHECKING:
    from .bloom import _PythonBloomDomain, _RustBloomDomain
    from .hash import _PythonHashDomain, _RustHashDomain
    from .ip import _PythonIpDomain, _RustIpDomain
    from .ioc import _PythonIocDomain, _RustIocDomain
    from .ioc_dedup import _PythonIocDedupDomain, _RustIocDedupDomain
    from .quality import _PythonQualityDomain, _RustQualityDomain
    from .rolling_hash import _PythonRollingHashDomain, _RustRollingHashDomain
    from .simhash import _PythonSimhashDomain, _RustSimhashDomain
    from .url import _PythonUrlDomain, _RustUrlDomain
    from .lsh import _PythonLshDomain, _RustLshDomain
    # Modular misc domains
    from .graph import _PythonGraphDomain, _RustGraphDomain
    from .hot_edges import _PythonHotEdgesDomain, _RustHotEdgesDomain
    from .aho import _PythonAhoDomain, _RustAhoDomain
    from .evidence import _PythonEvidenceDomain, _RustEvidenceDomain
    from .madvise import _PythonMadvisDomain, _RustMadvisDomain
    from .memory import _PythonMemoryDomain, _RustMemoryDomain
    from .json import _PythonJsonDomain, _RustJsonDomain
    from .spsc import _PythonSPSCDomain, _RustSPSCDomain
    from .query import _PythonQueryDomain, _RustQueryDomain
    from .text import _PythonTextDomain, _RustTextDomain
    from .xml import _PythonXmlDomain, _RustXmlDomain
    from .int_counter import _PythonIntCounterDomain, _RustIntCounterDomain
    from .simd import _PythonSimdDomain, _RustSimdDomain
    from .sprint_policies import _PythonSprintPoliciesDomain, _RustSprintPoliciesDomain
    from .pipeline_compose import PipelineComposeDomain, get_domain as _pipeline_compose_get_domain
    from .signal_batch import SignalBatchDomain, get_domain as _signal_batch_get_domain
    from .federated_qtable import FederatedQTableDomain, get_domain as _federated_qtable_get_domain
    from .async_query import AsyncQueryDomain, PythonFallbackAsyncQueryDomain, get_domain as _async_query_get_domain
    from .feed_decision import FeedDecisionDomain, get_domain as _feed_decision_get_domain
    from .feed_pipeline import FeedPipelineDomain, get_domain as _feed_pipeline_get_domain
    from .swarm_dag import SwarmDAG, PythonFallbackSwarmDAG, get_domain as _swarm_dag_get_domain
    from .link_predictor import _LinkPredictorDomain

from hledac.universal.core.feature_flags import FeatureFlag, FeatureFlags

logger = logging.getLogger(__name__)

# =============================================================================
# Force flags (mirrors original HLEDAC_FORCE_PYTHON / HLEDAC_FORCE_RUST)
# =============================================================================

_FORCE_PYTHON: bool = FeatureFlags.get(FeatureFlag.FORCE_PYTHON, default=False)
_FORCE_RUST: bool = FeatureFlags.get(FeatureFlag.FORCE_RUST, default=False)

# =============================================================================
# AccelBackend — public facade
# =============================================================================


class AccelInfo(msgspec.Struct, frozen=True, gc=False):
    """
    Frozen accelerator backend info.
    Describes which backend is active and its capabilities.
    F350M-R: gc=False for M1 8GB — hot-path IPC between Python and Rust.
    """

    available: bool
    version_str: str
    version_tuple: tuple[int, int, int]
    abi_version: tuple[int, int, int]  # ISSUE-040: ABI version from Rust extension ((0,0,0) = unknown/unavailable)
    backend: str  # "rust" | "python"
    capability_score: float = 0.0  # ISSUE-2: fraction of reference symbols present
    so_mtime: float | None = None  # ISSUE-2: mtime of loaded .so at probe time

    @property
    def is_compatible(self) -> bool:
        # NOTE: version_tuple checked by ProbeResult at probe time;
        # AccelInfo.backend=="rust" means Rust was selected AND passed compatibility.
        return self.backend == "rust"


@_dataclass(frozen=True, slots=True)
class RustForce:
    """
    F350M-R: Canonical Rust-backend force override resolved at probe time.

    Resolved priority (highest → lowest):
      1. env HLEDAC_FORCE_RUST=1       → force_rust()
      2. env HLEDAC_FORCE_PYTHON=1     → force_python()
      3. container.get('rust.force')    → RustForce object
      4. default                        → auto-probe

    Container usage (A3 integration):
        container.register('rust.force', factory=lambda: RustForce(python=False), scope='singleton')

    For sprint-scoped override (bootstrap.py):
        ctx.container.register('rust.force',
            factory=lambda: RustForce(python=False),
            scope='factory')
    """

    python: bool = False  # True = force Python fallback
    rust: bool = False   # True = force Rust (warn if unavailable)


class AccelBackend:
    """
    Lazy-resolving facade for Rust acceleration.

    Probes the Rust extension exactly once on first access to any domain.
    All domains are accessible via properties — each triggers the probe
    on first call and caches the result behind a property.

    F350M-R: Supports container-based force override via ServiceContainer.
    Set container.get('rust.force') = RustForce(python=True/False) before first domain access.
    """

    __slots__ = ("_probe_result", "_domains", "_container")

    def __new__(cls) -> "AccelBackend":
        # Singleton — one instance per process
        global _accel_instance
        if _accel_instance is None:
            instance = super().__new__(cls)
            _accel_instance = instance
        return _accel_instance

    def __init__(self) -> None:
        # Probe result — set once on first _ensure_probe() call
        self._probe_result: ProbeResult | None = None
        # Lazy domain cache — populated on first access to each property
        self._domains: dict[str, Any] = {}
        # Container reference for rust.force resolution (set by set_container)
        self._container: Any = None

    # -------------------------------------------------------------------------
    # Probe — one-time, cached
    # -------------------------------------------------------------------------

    def _ensure_probe(self) -> ProbeResult:
        """Run the probe exactly once (on first domain access)."""
        if self._probe_result is not None:
            return self._probe_result

        # F350M-R: Container-based force resolution (A3 integration).
        #
        # Resolution order (first match wins):
        #   1. env HLEDAC_FORCE_PYTHON=1  → force_python()
        #   2. env HLEDAC_FORCE_RUST=1    → force_rust()
        #   3. container.get('rust.force') → RustForce(python=True/False) — only if registered
        #   4. default                     → auto-probe()
        #
        # Container is checked FIRST to populate the `force` variable, but env vars
        # short-circuit before `force` is used. Bootstrap registers container ONLY
        # when no env var is set, so in practice: env vars > container > auto-probe.
        force: RustForce | None = None

        # Peek container (lowest priority — env vars above win if set)
        if self._container is not None:
            try:
                force = self._container.try_get("rust.force")
            except Exception:
                pass  # container not available or rust.force not registered

        # 1-2. Env var override (backward compat — always-on, no toggles)
        if _FORCE_PYTHON:
            self._probe_result = _force_python()
        elif _FORCE_RUST:
            self._probe_result = _force_rust()
        # 3. Container override (sprint-scoped, only if no env var matched)
        elif force is not None:
            if force.python:
                logger.debug("[AccelBackend] Python fallback FORCED via container rust.force")
                self._probe_result = _force_python()
            elif force.rust:
                logger.debug("[AccelBackend] Rust path FORCED via container rust.force")
                self._probe_result = _force_rust()
            else:
                # Explicit RustForce() with both False → auto-probe
                self._probe_result = _probe()
        # 4. Default: auto-probe
        else:
            self._probe_result = _probe()

        logger.debug(
            f"[AccelBackend] backend={self._probe_result.backend}, "
            f"available={self._probe_result.available}, "
            f"version={self._probe_result.version_str}"
        )
        return self._probe_result

    @property
    def is_available(self) -> bool:
        """True if the Rust extension is available and compatible."""
        return self._ensure_probe().available

    @property
    def capability_score(self) -> float:
        """ISSUE-2: Fraction of reference symbols present in the Rust binary (0.0-1.0)."""
        return self._ensure_probe().capability_score

    @property
    def so_mtime(self) -> float | None:
        """ISSUE-2: mtime of the loaded .so at probe time."""
        return self._ensure_probe().so_mtime

    @property
    def info(self) -> AccelInfo:
        """Frozen accelerator info snapshot."""
        p = self._ensure_probe()
        return AccelInfo(
            available=p.available,
            version_str=p.version_str,
            version_tuple=p.version_tuple,
            abi_version=p.abi_version,
            backend="rust" if p.available else "python",
            capability_score=p.capability_score,
            so_mtime=p.so_mtime,
        )

    # -------------------------------------------------------------------------
    # Domain properties — lazy, cached after first access
    # -------------------------------------------------------------------------

    @property
    def bloom(self) -> "_RustBloomDomain | _PythonBloomDomain":
        return self._get_domain("bloom", _get_submodule("bloom").get_domain)

    @property
    def url(self) -> "_RustUrlDomain | _PythonUrlDomain":
        return self._get_domain("url", _get_submodule("url").get_domain)

    @property
    def hash(self) -> "_RustHashDomain | _PythonHashDomain":
        return self._get_domain("hash", _get_submodule("hash").get_domain)

    @property
    def quality(self) -> "_RustQualityDomain | _PythonQualityDomain":
        return self._get_domain("quality", _get_submodule("quality").get_domain)

    @property
    def consistency(self) -> Any:
        """META-007: Propositional consistency verifier domain."""
        return _get_submodule("consistency").get_consistency_domain()

    @property
    def ioc(self) -> "_RustIocDomain | _PythonIocDomain":
        return self._get_domain("ioc", _get_submodule("ioc").get_domain)

    @property
    def ioc_dedup(self) -> "_RustIocDedupDomain | _PythonIocDedupDomain":
        return self._get_domain("ioc_dedup", _get_submodule("ioc_dedup").get_domain)

    @property
    def ip(self) -> "_RustIpDomain | _PythonIpDomain":
        return self._get_domain("ip", _get_submodule("ip").get_domain)

    # --- misc domains (now split into modular files) ---

    @property
    def graph(self) -> "_RustGraphDomain | _PythonGraphDomain":
        return self._get_domain("graph", _get_submodule("graph").get_graph_domain)

    @property
    def hot_edges(self) -> "_RustHotEdgesDomain | _PythonHotEdgesDomain":
        return self._get_domain("hot_edges", _get_submodule("hot_edges").get_hot_edges_domain)

    @property
    def aho(self) -> "_RustAhoDomain | _PythonAhoDomain":
        return self._get_domain("aho", _get_submodule("aho").get_aho_domain)

    @property
    def evidence(self) -> "_RustEvidenceDomain | _PythonEvidenceDomain":
        return self._get_domain("evidence", _get_submodule("evidence").get_evidence_domain)

    @property
    def madvise(self) -> "_RustMadvisDomain | _PythonMadvisDomain":
        return self._get_domain("madvise", _get_submodule("madvise").get_madvise_domain)

    @property
    def memory(self) -> "_RustMemoryDomain | _PythonMemoryDomain":
        return self._get_domain("memory", _get_submodule("memory").get_memory_domain)

    @property
    def json(self) -> "_RustJsonDomain | _PythonJsonDomain":
        return self._get_domain("json", _get_submodule("json").get_json_domain)

    @property
    def spsc(self) -> "_RustSPSCDomain | _PythonSPSCDomain":
        return self._get_domain("spsc", _get_submodule("spsc").get_spsc_domain)

    @property
    def swarm_dag(self) -> Any:
        """SILICON-07: Work-stealing task DAG with ROI-based adaptive pool sizing.

        Provides WorkStealingDAG for dynamic lane rebalancing.
        Falls back to PythonFallbackSwarmDAG when Rust is unavailable.
        """
        return self._get_domain("swarm_dag", _swarm_dag_get_domain)

    @property
    def query(self) -> "_RustQueryDomain | _PythonQueryDomain":
        return self._get_domain("query", _get_submodule("query").get_query_domain)

    @property
    def text(self) -> "_RustTextDomain | _PythonTextDomain":
        return self._get_domain("text", _get_submodule("text").get_text_domain)

    @property
    def xml(self) -> "_RustXmlDomain | _PythonXmlDomain":
        return self._get_domain("xml", _get_submodule("xml").get_xml_domain)

    @property
    def int_counter(self) -> "_RustIntCounterDomain | _PythonIntCounterDomain":
        return self._get_domain("int_counter", _get_submodule("int_counter").get_int_counter_domain)

    @property
    def simd(self) -> "_RustSimdDomain | _PythonSimdDomain":
        return self._get_domain("simd", _get_submodule("simd").get_simd_domain)

    @property
    def rolling_hash(self) -> "_RustRollingHashDomain | _PythonRollingHashDomain":
        return self._get_domain("rolling_hash", _get_submodule("rolling_hash").get_domain)

    @property
    def simhash(self) -> "_RustSimhashDomain | _PythonSimhashDomain":
        return self._get_domain("simhash", _get_submodule("simhash").get_domain)

    @property
    def lsh(self) -> "_RustLshDomain | _PythonLshDomain":
        return self._get_domain("lsh", _get_submodule("lsh").get_lsh_domain)

    @property
    def sprint_policies(self) -> "_RustSprintPoliciesDomain | _PythonSprintPoliciesDomain":
        return self._get_domain("sprint_policies", _get_submodule("sprint_policies").get_sprint_policies_domain)

    @property
    def pipeline_compose(self) -> PipelineComposeDomain | None:
        """Rust-backed pipeline operators (MAP/FILTER/FOLD/COUNT).

        Zero-copy Arc staging, rayon parallelism, M1 8GB safe.
        Returns None if hledac_rust_extensions is unavailable.
        """
        try:
            return _pipeline_compose_get_domain()
        except Exception:
            return None

    @property
    def signal(self) -> SignalBatchDomain | None:
        """NEON-accelerated signal aggregation (M1) or scalar fallback.

        batch_compute_scores: F199A source quality scoring.
        batch_aggregate_signals: weighted signal vector aggregation.
        Returns None if hledac_rust_extensions is unavailable.
        """
        try:
            return _signal_batch_get_domain()
        except Exception:
            return None

    @property
    def federated_qtable(self) -> FederatedQTableDomain | None:
        """Rust Federated Q-Learning table for acquisition source prioritization.

        Q(s,a) += alpha * (reward + gamma * max(Q(s',a'))) update.
        rayon parallel batch, auto-eviction, bincode persistence.
        Returns None if hledac_rust_extensions is unavailable.
        """
        try:
            return _federated_qtable_get_domain()
        except Exception:
            return None

    @property
    def async_query(self) -> AsyncQueryDomain | PythonFallbackAsyncQueryDomain:
        """Rust DuckDB async query functions (falls back to Python if unavailable).

        rust_async_query: O(1) connection pool, lock-held-throughout.
        rust_async_query_batch: rayon parallel N queries.
        Always returns a domain (Rust or Python fallback) — never None.
        """
        return _async_query_get_domain(self._probe_result.ext if self._probe_result else None)

    @property
    def feed_decision(self) -> FeedDecisionDomain | None:
        """Rust-backed feed signal classification — pure FSM functions.

        feed_decision_classify: classify fallback decision outcome.
        feed_stage_diagnose: diagnose which pipeline stage lost the signal.
        feed_branch_hint: sprint hint about feed branch quality.
        feed_economics_verdict: condensed economics verdict.
        feed_branch_verdict: rich dict verdict for feed economics.
        Returns None if hledac_rust_extensions is unavailable.
        """
        try:
            return _feed_decision_get_domain()
        except Exception:
            return None

    @property
    def feed_pipeline(self) -> FeedPipelineDomain | None:
        """Rust-backed RSS/Atom parse + scan + dedup pipeline.

        feed_entry_pipeline: single feed parse+scan+dedup via Aho-Corasick.
        feed_batch_pipeline: rayon-parallel multi-feed processing.
        Returns None if hledac_rust_extensions is unavailable.
        """
        try:
            return _feed_pipeline_get_domain()
        except Exception:
            return None

    @property
    def html(self) -> Any:
        return self._get_domain("html", _get_submodule("html").get_html_domain)

    # SWARM-003: Link prediction domain
    @property
    def link_predictor(self) -> Any:
        """Link prediction domain (Adamic-Adar, Jaccard, Preferential Attachment).

        Uses hledac_rust_extensions.link_predictor module for edge prediction.
        Falls back to Python implementation if Rust is unavailable.
        """
        return _get_submodule("link_predictor").get_link_predictor_domain(
            self._ensure_probe().ext
        )

    # -------------------------------------------------------------------------
    # Internal
    # -------------------------------------------------------------------------

    def _get_domain(self, name: str, factory: Any) -> Any:
        """Lazily create and cache a domain using the given factory."""
        if name not in self._domains:
            probe_result = self._ensure_probe()
            self._domains[name] = factory(probe_result.ext)
        return self._domains[name]

    def __repr__(self) -> str:
        p = self._ensure_probe()
        return f"AccelBackend(backend={p.backend}, available={p.available})"

    def set_container(self, container: Any) -> None:
        """
        F350M-R (A3): Attach ServiceContainer for rust.force resolution.

        Call this BEFORE first domain access (bloom, hash, ioc, etc.)
        to enable sprint-scoped force override via container.get('rust.force').

        Idempotent: can be called multiple times; last container wins.
        """
        self._container = container


# =============================================================================
# Singleton instance
# =============================================================================

_accel_instance: AccelBackend | None = None


def get_accel() -> AccelBackend:
    """Return the AccelBackend singleton (creating it on first call)."""
    return AccelBackend()


def set_container(container: Any) -> None:
    """
    F350M-R (A3): Attach ServiceContainer for rust.force resolution.

    Convenience wrapper around AccelBackend.set_container().

    Usage (bootstrap.py):
        from hledac.universal.core.rust_backend import get_accel, set_container
        from hledac.universal.core.container import get_global_container

        set_container(get_global_container())

    Call this BEFORE first domain access (bloom, hash, ioc, etc.)
    to enable sprint-scoped force override via container.get('rust.force').
    """
    get_accel().set_container(container)


def reset_accel() -> None:
    """Reset the singleton and probe cache — for testing only."""
    global _accel_instance
    _accel_instance = None
    _reset_probe()


# =============================================================================
# Backward-compatibility shim: `rust` module-level object
# Exposes the same API as the old RustBackend singleton so existing
# call sites (rust.bloom.BloomFilter(), rust.quality.batch_entropy(...), etc.)
# continue to work without modification.
# =============================================================================


# =============================================================================
# RustRawAccessor — safe wrapper for probe.ext
# =============================================================================


class RustRawAccessor:
    """
    Safe wrapper around the raw hledac_rust_extensions module.

    Provides __getattr__ that returns None for missing attributes instead
    of raising AttributeError. This is the canonical way to access ANY
    raw Rust symbol through the centralized backend:

        from hledac.universal.core.rust_backend import rust

        PyAIMDController = rust.raw.PyAIMDController  # None if unavailable
        dns_module = rust.raw.dns                      # None if unavailable
        result = rust.raw.batch_xxh3_64_bytes(data)    # None if unavailable

    R6: Single entry point — ALL access to hledac_rust_extensions
    MUST go through rust.raw, never through direct import.
    """

    __slots__ = ("_ext",)

    def __init__(self, ext: object | None) -> None:
        self._ext = ext

    def __getattr__(self, name: str) -> Any:
        if self._ext is None:
            return None
        return getattr(self._ext, name, None)

    def __repr__(self) -> str:
        return f"RustRawAccessor(ext={'available' if self._ext is not None else 'None'})"

    def __bool__(self) -> bool:
        return self._ext is not None

    @property
    def module(self) -> object | None:
        """Direct access to the raw extension module (advanced use only)."""
        return self._ext

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Safely call a function from the Rust extension, returning None on any error."""
        fn = getattr(self._ext, name, None) if self._ext is not None else None
        if fn is None:
            return None
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None


class _RustCompatShim:
    """
    Backward-compatibility shim that delegates to AccelBackend.

    Provides the same module-level interface as the old RustBackend class:
        rust.bloom, rust.quality, rust.ioc, etc.
        rust.is_available
        RustBackend() → same instance
        RustBackend is RustBackend  (singleton identity)

    R6: This is the SINGLE entry point for all Rust access. Submodule
    properties (dns, rate_limit, aimd, fulltext, native_db, stix, etc.)
    route through _ensure_probe() → probe.ext, ensuring:
      - Lazy wheel loading (no import until first use)
      - ABI version checking
      - Capability scoring
      - Container-based force override
      - Graceful fallback to None when unavailable
    """

    __slots__ = ("_accel", "_raw_accessor")

    def __new__(cls) -> "_RustCompatShim":
        global _rust_compat_instance
        if _rust_compat_instance is None:
            instance = super().__new__(cls)
            instance._accel = AccelBackend()
            instance._raw_accessor = None
            _rust_compat_instance = instance
        return _rust_compat_instance

    def __init__(self) -> None:
        # Skip if already initialized (singleton — __new__ handles idempotence)
        if hasattr(self, "_accel"):
            return
        self._accel = AccelBackend()
        self._raw_accessor = None

    def _get_raw(self) -> RustRawAccessor:
        """Get or create the RustRawAccessor for this shim."""
        if self._raw_accessor is None:
            probe = self._accel._ensure_probe()
            self._raw_accessor = RustRawAccessor(probe.ext)
        return self._raw_accessor

    # ── Sentinel for _get_submodule_attr ──────────────────────────────
    _MISSING = object()

    def _get_submodule_attr(self, submodule_name: str, attr_name: str) -> Any:
        """Safely get an attribute from a Rust submodule via probe.ext.

        Returns None if the extension or submodule or attribute is missing.
        Used for feature-gated submodules (dns, rate_limit, fulltext, etc.).
        """
        probe = self._accel._ensure_probe()
        ext = probe.ext
        if ext is None:
            return None
        submod = getattr(ext, submodule_name, self._MISSING)
        if submod is self._MISSING:
            return None
        return getattr(submod, attr_name, None)

    @property
    def is_available(self) -> bool:
        return self._accel.is_available

    @property
    def info(self) -> AccelInfo:
        return self._accel.info

    @property
    def raw(self) -> RustRawAccessor:
        """R6: Canonical access to hledac_rust_extensions symbols.

        Returns a RustRawAccessor that wraps probe.ext. All attribute
        accesses return None when the extension is unavailable —
        no ImportError or AttributeError to catch.

        Usage:
            from hledac.universal.core.rust_backend import rust

            # Type/class access
            PyAIMDController = rust.raw.PyAIMDController         # None if N/A
            MPSCPool = rust.raw.MPSCPool                         # None if N/A
            AhoCorasickMatcher = rust.raw.AhoCorasickMatcher     # None if N/A

            # Function access
            result = rust.raw.batch_xxh3_64_bytes(data)          # None if N/A
            result = rust.raw.batch_ioc_extract_unified(texts)   # None if N/A

            # Submodule access
            dns_mod = rust.raw.dns                               # None if N/A
            rate_mod = rust.raw.rate_limit                       # None if N/A

        Prefer typed domain properties (rust.bloom, rust.url, etc.)
        for wrapped domains. Use rust.raw for symbols not yet wrapped.
        """
        return self._get_raw()

    # ── R6: Submodule accessors for commonly-bypassed feature-gated modules ──
    # These route through probe.ext so they benefit from ABI checking,
    # capability scoring, and container-based force override.

    @property
    def dns(self) -> Any:
        """Rust DNS submodule (hickory-dns based, feature-gated: 'dns').

        Returns the raw dns submodule from hledac_rust_extensions,
        or None if the extension or dns feature is unavailable.

        Usage:
            from hledac.universal.core.rust_backend import rust
            dns_mod = rust.dns
            if dns_mod is not None:
                results = dns_mod.resolve_async(hosts)
        """
        probe = self._accel._ensure_probe()
        if probe.ext is None:
            return None
        return getattr(probe.ext, "dns", None)

    @property
    def rate_limit(self) -> Any:
        """Rust rate_limit submodule (token-bucket, feature: always compiled).

        Returns the raw rate_limit submodule, or None if unavailable.
        """
        probe = self._accel._ensure_probe()
        if probe.ext is None:
            return None
        return getattr(probe.ext, "rate_limit", None)

    @property
    def fulltext(self) -> Any:
        """Rust fulltext_index submodule (Tantivy BM25, feature-gated: 'fulltext').

        Returns the raw fulltext submodule, or None if unavailable.
        """
        probe = self._accel._ensure_probe()
        if probe.ext is None:
            return None
        return getattr(probe.ext, "fulltext", None)

    @property
    def native_db(self) -> Any:
        """Rust native_db submodule (MongoDB/Redis/ES wire-protocol, feature-gated: 'native_db').

        Returns the raw native_db submodule, or None if unavailable.
        """
        probe = self._accel._ensure_probe()
        if probe.ext is None:
            return None
        return getattr(probe.ext, "native_db", None)

    @property
    def stix(self) -> Any:
        """Rust stix_2_1 submodule (STIX 2.1 encode/decode/validate, feature-gated: 'stix').

        Returns the raw stix_2_1 submodule, or None if unavailable.
        """
        probe = self._accel._ensure_probe()
        if probe.ext is None:
            return None
        return getattr(probe.ext, "stix_2_1", None)

    @property
    def simdjson(self) -> Any:
        """Rust simdjson_extract submodule (zero-alloc JSON Pointer, feature-gated: 'simdjson').

        Returns the raw simdjson_extract submodule, or None if unavailable.
        """
        probe = self._accel._ensure_probe()
        if probe.ext is None:
            return None
        return getattr(probe.ext, "simdjson_extract", None)

    @property
    def bloom(self) -> Any:
        return self._accel.bloom

    @property
    def url(self) -> Any:
        return self._accel.url

    @property
    def hash(self) -> Any:
        return self._accel.hash

    @property
    def quality(self) -> Any:
        return self._accel.quality

    @property
    def ioc(self) -> Any:
        return self._accel.ioc

    @property
    def graph(self) -> Any:
        return self._accel.graph

    @property
    def hot_edges(self) -> Any:
        return self._accel.hot_edges

    @property
    def ip(self) -> Any:
        return self._accel.ip

    @property
    def html(self) -> Any:
        return self._accel.html

    @property
    def batch_extract_emails(self) -> Any:
        """Batch email extraction — Rust rayon-parallel, via rust.raw."""
        return getattr(self.raw, "batch_extract_emails", None)

    @property
    def batch_extract_titles(self) -> Any:
        """Batch title extraction — Rust rayon-parallel, via rust.raw."""
        return getattr(self.raw, "batch_extract_titles", None)

    @property
    def int_counter(self) -> Any:
        return self._accel.int_counter

    @property
    def simd(self) -> Any:
        return self._accel.simd

    @property
    def aho(self) -> Any:
        return self._accel.aho

    @property
    def evidence(self) -> Any:
        return self._accel.evidence

    @property
    def madvise(self) -> Any:
        return self._accel.madvise

    @property
    def memory(self) -> Any:
        return self._accel.memory

    @property
    def spsc(self) -> Any:
        return self._accel.spsc

    @property
    def swarm_dag(self) -> Any:
        """SILICON-07: Work-stealing task DAG. Falls back to PythonFallbackSwarmDAG."""
        return self._accel.swarm_dag

    @property
    def query(self) -> Any:
        return self._accel.query

    @property
    def json(self) -> Any:
        return self._accel.json

    @property
    def ioc_dedup(self) -> Any:
        return self._accel.ioc_dedup

    @property
    def sprint_policies(self) -> Any:
        return self._accel.sprint_policies

    @property
    def text(self) -> Any:
        return self._accel.text

    @property
    def xml(self) -> Any:
        return self._accel.xml

    @property
    def rolling_hash(self) -> Any:
        return self._accel.rolling_hash

    @property
    def simhash(self) -> Any:
        return self._accel.simhash

    @property
    def lsh(self) -> Any:
        return self._accel.lsh

    def __repr__(self) -> str:
        return repr(self._accel)

    @property
    def tls(self) -> Any:
        """Issue B5: TLS cert metadata — wraps extract_tls_metadata as rust.tls.extract_tls_metadata(...)."""
        probe = self._accel._ensure_probe()
        raw_fn = getattr(probe.ext, "extract_tls_metadata", None)
        if raw_fn is not None:
            # Inline minimal wrapper — same pattern as _TlsDomain removed from misc.py
            class _TlsMetadataWrapper:
                __slots__ = ("_fn",)
                def __init__(self, fn: object) -> None:
                    self._fn = fn
                def extract_tls_metadata(
                    self,
                    san_entries: list[tuple[int, str]],
                    issuer_org: str | None,
                    der_bytes: bytes | None,
                ) -> tuple[list[str], str | None, str | None]:
                    try:
                        return self._fn(san_entries, issuer_org, der_bytes)  # type: ignore
                    except Exception:  # noqa: BLE001
                        return ([], None, None)
            return _TlsMetadataWrapper(raw_fn)
        return None

    def set_container(self, container: Any) -> None:
        """F350M-R (A3): Attach ServiceContainer for rust.force resolution."""
        self._accel.set_container(container)


_rust_compat_instance: "RustBackend | None" = None


# -------------------------------------------------------------------------
# Public names for backward compatibility
# -------------------------------------------------------------------------


class RustBackend(_RustCompatShim):
    """
    Backward-compatible entry point.

    RustBackend() returns the singleton shim instance.
    isinstance(rust, RustBackend) is True because rust is a RustBackend instance.
    """

    # Inherit parent's __slots__ by not declaring any here.
    # This is intentional — RustBackend is just a type alias for the singleton.
    __slots__ = ()

    def __new__(cls) -> "RustBackend":  # type: ignore[override]
        return _get_or_create_singleton()

    def __init__(self) -> None:
        # Idempotent — singleton already initialized.
        # Note: _accel and _raw_accessor are set by _get_or_create_singleton()
        # via direct attribute assignment, bypassing slots.
        pass


def _get_or_create_singleton() -> "RustBackend":
    global _rust_compat_instance
    if _rust_compat_instance is None:
        instance: RustBackend = object.__new__(RustBackend)  # type: ignore[arg-type]
        # Initialize all slot attributes (RustBackend inherits __slots__ from _RustCompatShim)
        instance._accel = AccelBackend()  # type: ignore[attr-defined]
        instance._raw_accessor = None  # type: ignore[attr-defined]
        _rust_compat_instance = instance
    assert _rust_compat_instance is not None
    return _rust_compat_instance


#: Singleton rust backend instance — mirrors `from core.rust_backend import rust`
rust: RustBackend = _get_or_create_singleton()


def check_metal_availability() -> dict[str, Any]:
    """Check Metal/GPU availability — telemetry only, always returns Python fallback."""
    return _get_submodule("metal").check_metal_availability()


# =============================================================================
# Module-level reset (for test isolation)
# =============================================================================


def _reset_rust_backend_for_tests() -> None:
    """Reset all singletons — for test isolation."""
    global _accel_instance, _rust_compat_instance
    _accel_instance = None
    _rust_compat_instance = None
    _reset_probe()