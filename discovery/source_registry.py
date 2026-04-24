"""
Lightweight source registry for structured TI adapters.

Provides a simple registry pattern for source adapters without
introducing heavy plugin infrastructure.

Sprint 8BN — Structured TI Ingest V1
Sprint F202G — Pivot type mapping added
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Registry — stores adapter classes by source_type string
# ---------------------------------------------------------------------------

_SOURCE_REGISTRY: dict[str, object] = {}


def register_source_adapter(source_type: str, adapter_class: object) -> None:
    """
    Register a source adapter class for the given source_type.

    Parameters
    ----------
    source_type:
        Unique identifier for the source type (e.g. "nvd", "cisa_kev", "rss").
    adapter_class:
        The adapter class implementing SourceAdapter protocol.

    Raises
    ------
    ValueError
        If source_type is already registered.
    """
    if source_type in _SOURCE_REGISTRY:
        raise ValueError(f"source_type already registered: {source_type}")
    _SOURCE_REGISTRY[source_type] = adapter_class


def get_source_adapter(source_type: str) -> object | None:
    """
    Return a new instance of the registered adapter for source_type.

    Returns None if source_type is not registered.
    """
    cls = _SOURCE_REGISTRY.get(source_type)
    if cls is None:
        return None
    return cls()


def list_registered_source_types() -> list[str]:
    """Return sorted list of all registered source types."""
    return sorted(_SOURCE_REGISTRY.keys())


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


def get_pivot_type(ioc_type: str) -> str:
    """
    Get the appropriate pivot type for an IOC type.

    Args:
        ioc_type: The IOC type string

    Returns:
        The pivot type: domain, identity, leak, archive, or graph
    """
    return PIVOT_TYPE_MAP.get(ioc_type.lower(), "graph")


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
