"""
Lightweight source registry for structured TI adapters.

Provides a simple registry pattern for source adapters without
introducing heavy plugin infrastructure.

Sprint 8BN — Structured TI Ingest V1
Sprint F202G — Pivot type mapping added
Sprint F229 — SourceEntry dataclass with tier + acquisition_lane
"""
from __future__ import annotations



from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hledac.universal.utils.cache import PyCacheDict

# ---------------------------------------------------------------------------
# SourceEntry — F229: tier + acquisition_lane for source classification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceEntry:
    """F229: Named source with tier and acquisition lane."""
    adapter: Callable[..., Any]
    tier: int = 1  # 1=structured/deterministic, 2=overlay, 3=experimental
    acquisition_lane: str = "passive_dns"  # which lane uses this source


# ---------------------------------------------------------------------------
# Registry — stores SourceEntry by source_type string
# ---------------------------------------------------------------------------

_SOURCE_REGISTRY: dict[str, SourceEntry] = {}


def register_source_adapter(source_type: str, entry: SourceEntry) -> None:
    """
    Register a SourceEntry for the given source_type.

    Parameters
    ----------
    source_type:
        Unique identifier for the source type (e.g. "nvd", "cisa_kev", "rss").
    entry:
        SourceEntry with adapter callable, tier, and acquisition_lane.

    Raises
    ------
    ValueError
        If source_type is already registered.
    """
    if source_type in _SOURCE_REGISTRY:
        raise ValueError(f"source_type already registered: {source_type}")
    _SOURCE_REGISTRY[source_type] = entry


def get_source_adapter(source_type: str) -> SourceEntry | None:
    """
    Return the SourceEntry for source_type.

    Returns None if source_type is not registered.
    """
    return _SOURCE_REGISTRY.get(source_type)


def list_registered_source_types() -> list[str]:
    """Return sorted list of all registered source types."""
    return sorted(_SOURCE_REGISTRY.keys())


# F3.2: PyCacheDict replaces lru_cache — bounded + TTL + thread-safe
# 360-key space: 3×bool × ~9 tier values; 64 was thrashing; maxsize=512 matches original
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


# ---------------------------------------------------------------------------
# Sprint F229: Source registration
# ---------------------------------------------------------------------------

from .circl_pdns_adapter import async_search_circl_pdns as _circl_adapter  # noqa: E402
from .dht_adapter import async_search_dht as _dht_adapter  # noqa: E402

register_source_adapter(
    "circl_pdns",
    SourceEntry(
        adapter=_circl_adapter,
        tier=1,
        acquisition_lane="passive_dns",
    ),
)

# ---------------------------------------------------------------------------
# Sprint F229 / F214Q: DHT Discovery — tier-3 experimental
# ---------------------------------------------------------------------------
register_source_adapter(
    "dht_discovery",
    SourceEntry(
        adapter=_dht_adapter,
        tier=3,
        acquisition_lane="experimental",
    ),
)

# ---------------------------------------------------------------------------
# Sprint F250F: IPFS Discovery — tier-3 experimental (unindexed archival data)
# ---------------------------------------------------------------------------
try:
    from ..network.ipfs_client import (  # noqa: F401  # ..network.ipfs_client.ipfs_search_as_findings
        ipfs_fetch_as_findings,
        ipfs_search_as_findings,
    )

    register_source_adapter(
        "ipfs_discovery",
        SourceEntry(
            adapter=ipfs_fetch_as_findings,
            tier=3,
            acquisition_lane="experimental",
        ),
    )
except ImportError:
    pass  # IPFS client not available
