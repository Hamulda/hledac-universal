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

logger = logging.getLogger(__name__)

# =============================================================================
# Force flags (mirrors original HLEDAC_FORCE_PYTHON / HLEDAC_FORCE_RUST)
# =============================================================================

_FORCE_PYTHON: bool = os.environ.get("HLEDAC_FORCE_PYTHON", "0") == "1"
_FORCE_RUST: bool = os.environ.get("HLEDAC_FORCE_RUST", "0") == "1"

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


class _RustCompatShim:
    """
    Backward-compatibility shim that delegates to AccelBackend.

    Provides the same module-level interface as the old RustBackend class:
        rust.bloom, rust.quality, rust.ioc, etc.
        rust.is_available
        RustBackend() → same instance
        RustBackend is RustBackend  (singleton identity)
    """

    __slots__ = ("_accel",)

    def __new__(cls) -> "_RustCompatShim":
        global _rust_compat_instance
        if _rust_compat_instance is None:
            instance = super().__new__(cls)
            instance._accel = AccelBackend()
            _rust_compat_instance = instance
        return _rust_compat_instance

    def __init__(self) -> None:
        # Skip if already initialized (singleton — __new__ handles idempotence)
        if hasattr(self, "_accel"):
            return
        self._accel = AccelBackend()

    @property
    def is_available(self) -> bool:
        return self._accel.is_available

    @property
    def info(self) -> AccelInfo:
        return self._accel.info

    @property
    def raw(self) -> Any:
        """Direct access to hledac_rust_extensions module (for legacy callers)."""
        probe = self._accel._ensure_probe()
        return probe.ext

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

    __slots__ = ()

    def __new__(cls) -> "RustBackend":  # type: ignore[override]
        return _get_or_create_singleton()

    def __init__(self) -> None:
        # Idempotent — singleton already initialized
        pass


def _get_or_create_singleton() -> "RustBackend":
    global _rust_compat_instance
    if _rust_compat_instance is None:
        instance: RustBackend = object.__new__(RustBackend)  # type: ignore[arg-type]
        object.__init__(instance)
        instance._accel = AccelBackend()  # type: ignore[attr-defined]
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