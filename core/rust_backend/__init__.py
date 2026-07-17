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
    from core.rust_backend import get_accel

    accel = get_accel()
    if accel.is_available:
        fingerprints = accel.quality.batch_dedup_fingerprints(texts)
    else:
        # same API — Python fallback transparently
        fingerprints = accel.quality.batch_dedup_fingerprints(texts)

For testing:
    from core.rust_backend import reset_accel
    reset_accel()  # clear singleton and probe cache

Backward compatibility:
    from core.rust_backend import rust
    rust.bloom.BloomFilter()        # still works
    rust.quality.batch_entropy(...) # still works
"""


import logging
import os
from dataclasses import dataclass, field
import msgspec
from functools import cached_property
from typing import TYPE_CHECKING, Any

# Lazy-loaded submodules — imported on first domain access, not at module load
from . import bloom as _bloom_mod
from . import hash as _hash_mod
from . import ip as _ip_mod
from . import ioc as _ioc_mod
from . import ioc_dedup as _ioc_dedup_mod
from . import misc as _misc_mod
from . import quality as _quality_mod
from . import rolling_hash as _rolling_hash_mod
from . import simhash as _simhash_mod
from . import url as _url_mod
from . import lsh as _lsh_mod
from ._prober import force_python as _force_python
from ._prober import force_rust as _force_rust
from ._prober import probe as _probe
from ._prober import reset as _reset_probe
from ._prober import ProbeResult

if TYPE_CHECKING:
    from .bloom import _PythonBloomDomain, _RustBloomDomain
    from .hash import _PythonHashDomain, _RustHashDomain
    from .ip import _PythonIpDomain, _RustIpDomain
    from .ioc import _PythonIocDomain, _RustIocDomain
    from .ioc_dedup import _PythonIocDedupDomain, _RustIocDedupDomain
    from .misc import (
        _PythonAhoDomain, _PythonEvidenceDomain, _PythonHotEdgesDomain,
        _PythonIntCounterDomain, _PythonJsonDomain, _PythonMadvisDomain,
        _PythonMemoryDomain, _PythonGraphDomain,
        _PythonQueryDomain, _PythonSimdDomain, _PythonSPSCDomain,
        _PythonSprintPoliciesDomain, _PythonTextDomain, _PythonXmlDomain,
        _RustAhoDomain, _RustEvidenceDomain, _RustHotEdgesDomain,
        _RustIntCounterDomain, _RustJsonDomain, _RustMadvisDomain,
        _RustMemoryDomain, _RustGraphDomain,
        _RustQueryDomain, _RustSimdDomain, _RustSPSCDomain,
        _RustSprintPoliciesDomain, _RustTextDomain, _RustXmlDomain,
    )
    from .quality import _PythonQualityDomain, _RustQualityDomain
    from .rolling_hash import _PythonRollingHashDomain, _RustRollingHashDomain
    from .simhash import _PythonSimhashDomain, _RustSimhashDomain
    from .url import _PythonUrlDomain, _RustUrlDomain
    from .lsh import _PythonLshDomain, _RustLshDomain

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
    abi_version: int  # ISSUE-040: ABI version from Rust extension (0 = unknown/unavailable)
    backend: str  # "rust" | "python"

    @property
    def is_compatible(self) -> bool:
        # NOTE: version_tuple checked by ProbeResult at probe time;
        # AccelInfo.backend=="rust" means Rust was selected AND passed compatibility.
        return self.backend == "rust"


class AccelBackend:
    """
    Lazy-resolving facade for Rust acceleration.

    Probes the Rust extension exactly once on first access to any domain.
    All domains are accessible via properties — each triggers the probe
    on first call and caches the result behind a property.
    """

    __slots__ = ("_probe_result", "_domains")

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

    # -------------------------------------------------------------------------
    # Probe — one-time, cached
    # -------------------------------------------------------------------------

    def _ensure_probe(self) -> ProbeResult:
        """Run the probe exactly once (on first domain access)."""
        if self._probe_result is not None:
            return self._probe_result

        if _FORCE_PYTHON:
            self._probe_result = _force_python()
        elif _FORCE_RUST:
            self._probe_result = _force_rust()
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
    def info(self) -> AccelInfo:
        """Frozen accelerator info snapshot."""
        p = self._ensure_probe()
        return AccelInfo(
            available=p.available,
            version_str=p.version_str,
            version_tuple=p.version_tuple,
            abi_version=p.abi_version,
            backend="rust" if p.available else "python",
        )

    # -------------------------------------------------------------------------
    # Domain properties — lazy, cached after first access
    # -------------------------------------------------------------------------

    @property
    def bloom(self) -> "_RustBloomDomain | _PythonBloomDomain":
        return self._get_domain("bloom", _bloom_mod.get_domain)

    @property
    def url(self) -> "_RustUrlDomain | _PythonUrlDomain":
        return self._get_domain("url", _url_mod.get_domain)

    @property
    def hash(self) -> "_RustHashDomain | _PythonHashDomain":
        return self._get_domain("hash", _hash_mod.get_domain)

    @property
    def quality(self) -> "_RustQualityDomain | _PythonQualityDomain":
        return self._get_domain("quality", _quality_mod.get_domain)

    @property
    def ioc(self) -> "_RustIocDomain | _PythonIocDomain":
        return self._get_domain("ioc", _ioc_mod.get_domain)

    @property
    def ioc_dedup(self) -> "_RustIocDedupDomain | _PythonIocDedupDomain":
        return self._get_domain("ioc_dedup", _ioc_dedup_mod.get_domain)

    @property
    def ip(self) -> "_RustIpDomain | _PythonIpDomain":
        return self._get_domain("ip", _ip_mod.get_domain)

    # --- misc domains (all from misc.py) ---

    @property
    def graph(self) -> "_RustGraphDomain | _PythonGraphDomain":
        return self._get_domain("graph", _misc_mod.get_graph_domain)

    @property
    def hot_edges(self) -> "_RustHotEdgesDomain | _PythonHotEdgesDomain":
        return self._get_domain("hot_edges", _misc_mod.get_hot_edges_domain)

    @property
    def aho(self) -> "_RustAhoDomain | _PythonAhoDomain":
        return self._get_domain("aho", _misc_mod.get_aho_domain)

    @property
    def evidence(self) -> "_RustEvidenceDomain | _PythonEvidenceDomain":
        return self._get_domain("evidence", _misc_mod.get_evidence_domain)

    @property
    def madvise(self) -> "_RustMadvisDomain | _PythonMadvisDomain":
        return self._get_domain("madvise", _misc_mod.get_madvise_domain)

    @property
    def memory(self) -> "_RustMemoryDomain | _PythonMemoryDomain":
        return self._get_domain("memory", _misc_mod.get_memory_domain)

    @property
    def json(self) -> "_RustJsonDomain | _PythonJsonDomain":
        return self._get_domain("json", _misc_mod.get_json_domain)

    @property
    def spsc(self) -> "_RustSPSCDomain | _PythonSPSCDomain":
        return self._get_domain("spsc", _misc_mod.get_spsc_domain)

    @property
    def query(self) -> "_RustQueryDomain | _PythonQueryDomain":
        return self._get_domain("query", _misc_mod.get_query_domain)

    @property
    def text(self) -> "_RustTextDomain | _PythonTextDomain":
        return self._get_domain("text", _misc_mod.get_text_domain)

    @property
    def xml(self) -> "_RustXmlDomain | _PythonXmlDomain":
        return self._get_domain("xml", _misc_mod.get_xml_domain)

    @property
    def int_counter(self) -> "_RustIntCounterDomain | _PythonIntCounterDomain":
        return self._get_domain("int_counter", _misc_mod.get_int_counter_domain)

    @property
    def simd(self) -> "_RustSimdDomain | _PythonSimdDomain":
        return self._get_domain("simd", _misc_mod.get_simd_domain)

    @property
    def rolling_hash(self) -> "_RustRollingHashDomain | _PythonRollingHashDomain":
        return self._get_domain("rolling_hash", _rolling_hash_mod.get_domain)

    @property
    def simhash(self) -> "_RustSimhashDomain | _PythonSimhashDomain":
        return self._get_domain("simhash", _simhash_mod.get_domain)

    @property
    def lsh(self) -> "_RustLshDomain | _PythonLshDomain":
        return self._get_domain("lsh", _lsh_mod.get_lsh_domain)

    @property
    def sprint_policies(self) -> "_RustSprintPoliciesDomain | _PythonSprintPoliciesDomain":
        return self._get_domain("sprint_policies", _misc_mod.get_sprint_policies_domain)

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


# =============================================================================
# Singleton instance
# =============================================================================

_accel_instance: AccelBackend | None = None


def get_accel() -> AccelBackend:
    """Return the AccelBackend singleton (creating it on first call)."""
    return AccelBackend()


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
        # Route to the actual HTML domain from misc.py
        from . import misc as _misc_mod
        probe = self._accel._ensure_probe()
        return _misc_mod.get_html_domain(probe.ext)

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
        from . import misc as _misc_mod
        probe = self._accel._ensure_probe()
        raw_fn = getattr(probe.ext, "extract_tls_metadata", None)
        if raw_fn is not None:
            return _misc_mod._TlsDomain(raw_fn)
        return None


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
    return _misc_mod._python_check_metal_availability()


# =============================================================================
# Module-level reset (for test isolation)
# =============================================================================


def _reset_rust_backend_for_tests() -> None:
    """Reset all singletons — for test isolation."""
    global _accel_instance, _rust_compat_instance
    _accel_instance = None
    _rust_compat_instance = None
    _reset_probe()
