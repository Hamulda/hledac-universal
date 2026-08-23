"""
Lightweight source registry for structured TI adapters.

Provides a simple registry pattern for source adapters without

introducing heavy plugin infrastructure.

Sprint 8BN — Structured TI Ingest V1
Sprint F202G — Pivot type mapping added
Sprint F229 — SourceEntry dataclass with tier + acquisition_lane
Sprint P1-12 — PEP 810 lazy loading: adapters loaded on first use, not at import.
"""

import importlib
from collections.abc import Callable
from typing import Any

import msgspec

from compat.msgspec_gc_compat import Struct
from hledac.universal.utils.cache import PyCacheDict


class SourceEntry(Struct):
    """F229: Named source with tier and acquisition lane."""

    adapter: Callable[..., Any] | None = None
    tier: int = 1
    acquisition_lane: str = "passive_dns"
    _lazy_module: str | None = None
    _lazy_attr: str | None = None
    _loaded: bool = msgspec.field(default=False, omit=True)


_SOURCE_REGISTRY: dict[str, SourceEntry] = {}


def register_source_adapter(source_type: str, entry: SourceEntry, *, allow_override: bool = False) -> None:
    """
    Register a SourceEntry for the given source_type.

    Parameters
    ----------
    source_type:
        Unique identifier for the source type (e.g. "nvd", "cisa_kev", "rss").
    entry:
        SourceEntry with adapter callable, tier, and acquisition_lane.
    allow_override:
        P1-12: If True, replace an existing entry (useful for conditional
        registration after initial lazy registration).

    Raises
    ------
    ValueError
        If source_type is already registered and allow_override is False.
    """
    if source_type in _SOURCE_REGISTRY and (not allow_override):
        raise ValueError(f"source_type already registered: {source_type}")
    _SOURCE_REGISTRY[source_type] = entry


def get_source_adapter(source_type: str) -> SourceEntry | None:
    """
    Return the SourceEntry for source_type, resolving lazy entries on first access.

    P1-12: If the entry has _lazy_module + _lazy_attr set, the module is
    imported once and adapter is populated in-place. Subsequent calls return
    the resolved entry without re-importing.

    Returns None if source_type is not registered.
    """
    _ensure_adapters_registered()
    entry = _SOURCE_REGISTRY.get(source_type)
    if entry is None:
        return None
    if entry._loaded and entry.adapter is not None:
        return entry
    if entry._lazy_module and entry._lazy_attr:
        try:
            mod = importlib.import_module(entry._lazy_module)
            resolved_adapter: Callable[..., Any] = getattr(mod, entry._lazy_attr)
            entry.adapter = resolved_adapter
            entry._loaded = True
        except (ImportError, AttributeError):
            entry._loaded = True
            entry.adapter = None
        return entry
    return entry


def list_registered_source_types() -> list[str]:
    """Return sorted list of all registered source types."""
    _ensure_adapters_registered()
    return sorted(_SOURCE_REGISTRY.keys())


_LAZY_SUBMODULES: dict[str, tuple[str, str]] = {
    "circl_pdns_adapter": ("hledac.universal.discovery.circl_pdns_adapter", "async_search_circl_pdns"),
    "dht_adapter": ("hledac.universal.discovery.dht_adapter", "async_search_dht"),
    "ipfs_discovery": ("hledac.universal.network.ipfs_client", "ipfs_fetch_as_findings"),
}

# PEP 810 lazy registration — register source adapters on first access, not at import.
# This avoids eager module-level side-effects that trigger I/O in __init__ or
# transitively-loaded submodules during `from discovery import *`.
_LAZY_ADAPTERS: tuple[tuple[str, str, str, int, str, str], ...] = (
    (
        "circl_pdns",
        "hledac.universal.discovery.circl_pdns_adapter",
        "async_search_circl_pdns",
        1,
        "passive_dns",
        "hledac.universal.discovery.circl_pdns_adapter",
    ),
    (
        "dht_discovery",
        "hledac.universal.discovery.dht_adapter",
        "async_search_dht",
        3,
        "experimental",
        "hledac.universal.discovery.dht_adapter",
    ),
    (
        "ipfs_discovery",
        "hledac.universal.network.ipfs_client",
        "ipfs_fetch_as_findings",
        3,
        "experimental",
        "hledac.universal.network.ipfs_client",
    ),
)


def __getattr__(name: str) -> Any:
    """PEP 810: lazily import submodules on demand + lazy source-adapter registration."""
    if name in _LAZY_SUBMODULES:
        mod_path, attr_name = _LAZY_SUBMODULES[name]
        mod = importlib.import_module(mod_path)
        return getattr(mod, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _ensure_adapters_registered() -> None:
    """Register all lazy adapters once (idempotent)."""
    for source_type, mod_path, attr_name, tier, lane, _lazy_mod in _LAZY_ADAPTERS:
        if source_type not in _SOURCE_REGISTRY:
            register_source_adapter(
                source_type,
                SourceEntry(
                    adapter=None,
                    tier=tier,
                    acquisition_lane=lane,
                    _lazy_module=mod_path,
                    _lazy_attr=attr_name,
                    _loaded=False,
                ),
            )


_quality_cache: PyCacheDict[tuple[bool, bool, bool, str], int] = PyCacheDict(512, 300.0)


def source_quality_score(parseable: bool, stable_schema: bool, identifier_rich: bool, source_tier: str) -> int:
    """
    Compute deterministic quality score for a source.

    Scoring (V1):
    - parseable: +30 points
    - stable_schema: +25 points
    - identifier_rich: +20 points
    - tier structured_ti: +15 points
    - tier surface: +5 points
    - tier overlay_ready: +0 points
    """
    key = (parseable, stable_schema, identifier_rich, source_tier)
    cached = _quality_cache.get(key)
    if cached is not None:
        return cached
    score = 0
    if parseable:
        score += 30
    if stable_schema:
        score += 25
    if identifier_rich:
        score += 20
    if source_tier == "structured_ti":
        score += 15
    elif source_tier == "surface":
        score += 5
    _quality_cache.set(key, score)
    return score


PIVOT_TYPE_MAP: dict[str, str] = {
    "domain": "domain",
    "fqdn": "domain",
    "hostname": "domain",
    "ip": "domain",
    "ipv4": "domain",
    "ipv6": "domain",
    "md5": "graph",
    "sha1": "graph",
    "sha256": "graph",
    "sha512": "graph",
    "hash": "graph",
    "email": "leak",
    "email_addr": "leak",
    "url": "archive",
    "uri": "archive",
    "username": "identity",
    "handle": "identity",
    "name": "identity",
    "profile": "identity",
    "unknown": "graph",
}
_pivot_type_cache: PyCacheDict[str, str] = PyCacheDict(128, 300.0)


def get_pivot_type(ioc_type: str) -> str:
    """
    Get the appropriate pivot type for an IOC type.

    Args:
        ioc_type: The IOC type string

    Returns:
        The pivot type: domain, identity, leak, archive, or graph
    """
    cached = _pivot_type_cache.get(ioc_type)
    if cached is not None:
        return cached
    result = PIVOT_TYPE_MAP.get(ioc_type.lower(), "graph")
    _pivot_type_cache.set(ioc_type, result)
    return result


def get_pivot_task_types(pivot_type: str) -> list[str]:
    """
    Get the task types to enqueue for a given pivot type.

    Args:
        pivot_type: The pivot type (domain, identity, leak, archive, graph)

    Returns:
        List of task type strings for the pivot queue
    """
    task_map: dict[str, list[str]] = {
        "domain": ["domain_to_dns", "domain_to_wayback", "domain_to_pdns", "domain_to_ct", "rdap_lookup"],
        "identity": ["identity_to_profile", "identity_to_email", "identity_to_social"],
        "leak": ["paste_keyword_search", "github_secret_scan", "breach_check"],
        "archive": ["wayback_search", "commoncrawl_search"],
        "graph": ["ioc_graph_traverse", "threat_intel_lookup"],
    }
    return task_map.get(pivot_type, ["multi_engine_search"])
