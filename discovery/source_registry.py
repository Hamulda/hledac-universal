"""
Lightweight source registry for structured TI adapters.

Provides a simple registry pattern for source adapters without
introducing heavy plugin infrastructure.

Sprint 8BN — Structured TI Ingest V1
Sprint F202G — Pivot type mapping added
Sprint F229 — SourceEntry dataclass with tier + acquisition_lane
Sprint P1-12 — PEP 810 lazy loading: adapters loaded on first use, not at import.
"""
from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from hledac.universal.utils.cache import PyCacheDict

# ---------------------------------------------------------------------------
# SourceEntry — F229: tier + acquisition_lane for source classification
# P1-12: removed frozen=True to allow adapter replacement after lazy load.
#         adapter=None + _lazy_module/_lazy_attr marks a lazy entry.
# ---------------------------------------------------------------------------

@dataclass
class SourceEntry:
    """F229: Named source with tier and acquisition lane."""
    adapter: Callable[..., Any] | None = None
    tier: int = 1  # 1=structured/deterministic, 2=overlay, 3=experimental
    acquisition_lane: str = "passive_dns"  # which lane uses this source
    # P1-12: lazy loading fields — set when adapter is a deferred import
    _lazy_module: str | None = None
    _lazy_attr: str | None = None
    _loaded: bool = field(default=False, repr=False)


# ---------------------------------------------------------------------------
# Registry — stores SourceEntry by source_type string
# P1-12: adapters registered WITHOUT importing their modules at registry load time.
#   Lazy entries have adapter=None, _lazy_module + _lazy_attr set.
#   get_source_adapter() resolves them on first access and caches in-place.
# ---------------------------------------------------------------------------

_SOURCE_REGISTRY: dict[str, SourceEntry] = {}


def register_source_adapter(
    source_type: str,
    entry: SourceEntry,
    *,
    allow_override: bool = False,
) -> None:
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
    if source_type in _SOURCE_REGISTRY and not allow_override:
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
    entry = _SOURCE_REGISTRY.get(source_type)
    if entry is None:
        return None

    # Already resolved (eager or previously lazy-loaded)
    if entry._loaded and entry.adapter is not None:
        return entry

    # P1-12: lazy resolve
    if entry._lazy_module and entry._lazy_attr:
        try:
            mod = importlib.import_module(entry._lazy_module)
            resolved_adapter: Callable[..., Any] = getattr(mod, entry._lazy_attr)
            entry.adapter = resolved_adapter
            entry._loaded = True
        except (ImportError, AttributeError):
            # Module or attribute not available — mark loaded so we don't retry
            entry._loaded = True
            entry.adapter = None
        return entry

    return entry


def list_registered_source_types() -> list[str]:
    """Return sorted list of all registered source types."""
    return sorted(_SOURCE_REGISTRY.keys())


# ---------------------------------------------------------------------------
# PEP 810 lazy module-level __getattr__ for top-level package access
# P1-12: from discovery import circl_pdns_adapter triggers lazy module load
# ---------------------------------------------------------------------------

_LAZY_SUBMODULES: dict[str, tuple[str, str]] = {
    # submodule: (package, attr) — resolved on first attribute access
    "circl_pdns_adapter": ("hledac.universal.discovery.circl_pdns_adapter", "async_search_circl_pdns"),
    "dht_adapter": ("hledac.universal.discovery.dht_adapter", "async_search_dht"),
}


def __getattr__(name: str) -> Any:
    """PEP 810: lazily import submodules on demand from the discovery package."""
    if name in _LAZY_SUBMODULES:
        mod_path, attr_name = _LAZY_SUBMODULES[name]
        mod = importlib.import_module(mod_path)
        return getattr(mod, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# P1-12: Pre-register all sources with lazy entries — ZERO imports at load time.
# Tier-3 experimental sources (dht, ipfs) can be overridden conditionally
# via register_source_adapter(..., allow_override=True) if needed.
# ---------------------------------------------------------------------------

register_source_adapter(
    "circl_pdns",
    SourceEntry(
        adapter=None,
        tier=1,
        acquisition_lane="passive_dns",
        _lazy_module="hledac.universal.discovery.circl_pdns_adapter",
        _lazy_attr="async_search_circl_pdns",
        _loaded=False,
    ),
)

# DHT — tier-3 experimental
register_source_adapter(
    "dht_discovery",
    SourceEntry(
        adapter=None,
        tier=3,
        acquisition_lane="experimental",
        _lazy_module="hledac.universal.discovery.dht_adapter",
        _lazy_attr="async_search_dht",
        _loaded=False,
    ),
)

# IPFS — tier-3 experimental (unindexed archival data)
# P1-12: Availability is determined at first get_source_adapter() call,
# NOT at module load time. The lazy entry is always present; if the
# import fails, adapter=None is returned and callers degrade gracefully.

# Pre-register IPFS as lazy — resolved on first access
register_source_adapter(
    "ipfs_discovery",
    SourceEntry(
        adapter=None,
        tier=3,
        acquisition_lane="experimental",
        _lazy_module="hledac.universal.network.ipfs_client",
        _lazy_attr="ipfs_fetch_as_findings",
        _loaded=False,
    ),
)


# ---------------------------------------------------------------------------
# F3.2: PyCacheDict replaces lru_cache — bounded + TTL + thread-safe
# 360-key space: 3×bool × ~9 tier values; 64 was thrashing; maxsize=512 matches original
# ---------------------------------------------------------------------------

_quality_cache: PyCacheDict[tuple[bool, bool, bool, str], int] = PyCacheDict(512, 300.0)


def source_quality_score(
    parseable: bool,
    stable_schema: bool,
    identifier_rich: bool,
    source_tier: str,
) -> int:
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


# ---------------------------------------------------------------------------
# Sprint F202G: Pivot type mapping
# Maps IOC types to appropriate pivot types for investigation
# ---------------------------------------------------------------------------

# IOC type to pivot type mapping
PIVOT_TYPE_MAP: dict[str, str] = {
    # Domain pivots
    "domain": "domain",
    "fqdn": "domain",
    "hostname": "domain",
    # IP pivots
    "ip": "domain",  # Reverse DNS lookup
    "ipv4": "domain",
    "ipv6": "domain",
    # Hash pivots
    "md5": "graph",
    "sha1": "graph",
    "sha256": "graph",
    "sha512": "graph",
    "hash": "graph",
    # Email pivots
    "email": "leak",
    "email_addr": "leak",
    # URL pivots
    "url": "archive",
    "uri": "archive",
    # Identity pivots
    "username": "identity",
    "handle": "identity",
    "name": "identity",
    "profile": "identity",
    # Generic / unknown
    "unknown": "graph",
}


# F3.2: PyCacheDict replaces lru_cache — bounded + TTL + thread-safe
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
        "domain": ["domain_to_dns", "domain_to_wayback", "domain_to_pdns",
                   "domain_to_ct", "rdap_lookup"],
        "identity": ["identity_to_profile", "identity_to_email", "identity_to_social"],
        "leak": ["paste_keyword_search", "github_secret_scan", "breach_check"],
        "archive": ["wayback_search", "commoncrawl_search"],
        "graph": ["ioc_graph_traverse", "threat_intel_lookup"],
    }
    return task_map.get(pivot_type, ["multi_engine_search"])
