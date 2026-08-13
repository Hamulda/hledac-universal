"""
core/protocols.py — Protocol-based structural typing for Hledac Universal.

Sprint F290: Replaces ~45 getattr()/hasattr() calls with explicit structural
protocols. Any class implementing the required methods satisfies the Protocol
— no inheritance required (duck typing with type-safety).

PEP 544 + runtime_checkable enables structural subtyping without importing
dependent classes. Modules define only "what I need", not "from whom".

Invariant table (test name → validated property):
  test_protocol_duckdb_store       → DuckDBStoreProtocol.async_ingest_findings_batch
  test_protocol_graph_service       → GraphServiceProtocol.upsert_ioc
  test_protocol_fetch_coordinator   → FetchCoordinatorProtocol.fetch
  test_protocol_finding_runtime     → FindingProto, FindingWithPayloadProto
  test_safe_get_finding_field      → safe_get_finding_field()
  test_safe_get_payload_text       → safe_get_payload_text()

References:
  - runtime/sidecar_bus.py (getattr/getattr calls — 13 usages)
  - brain/research_hypothesis_engine.py (hasattr checks — 7 usages)
  - knowledge/duckdb_store.py (hasattr checks — 4 usages)
  - runtime/sprint_scheduler.py (getattr calls — 6 usages)
  - brain/ (getattr calls — 19 usages)

PRUNED (F290CLEAN): Removed 10 unused protocols with zero isinstance() checks:
  - IOCExtractorProto (no runtime checks)
  - TelemetryWritePort (no runtime checks)
  - DuckDBReadProtocol (no runtime checks)
  - LMDBStoreProtocol (no runtime checks)
  - IOCGraphProto (no runtime checks)
  - CircuitBreakerProto (no runtime checks)
  - InferenceEngineProtocol (no runtime checks)
  - UMAManagerProto (no runtime checks)
  - LifecycleAdapterProtocol (no runtime checks)
  - DedupManagerProtocol (no runtime checks)

KEPT: FindingProto, FindingWithPayloadProto, DuckDBStoreProtocol,
  GraphServiceProtocol, FetchCoordinatorProtocol (all have isinstance() checks).
"""


from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import Any

    from hledac.universal.knowledge.duckdb_store import (
        ActivationResult,
        CanonicalFinding,
        FindingQualityDecision,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Finding / IOC structural protocols (replaces ~45 getattr() calls)
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class FindingProto(Protocol):
    """
    Structural protocol for canonical finding objects.

    Implemented by: CanonicalFinding, Finding, dict (duck-typed).
    Use isinstance(x, FindingProto) for type-safe duck typing.
    """

    source_type: str
    url: str
    ioc_value: str
    ioc_type: str
    raw_ioc: bool

    def to_dict(self) -> dict[str, Any]: ...


@runtime_checkable
class FindingWithPayloadProto(Protocol):
    """
    Finding objects that carry extracted payload text.

    Extends FindingProto with payload field access used in sidecar bus.
    Replaces: getattr(f, "payload_text", None)
    """

    source_type: str
    url: str
    ioc_value: str
    ioc_type: str
    raw_ioc: bool
    payload_text: str

    def to_dict(self) -> dict[str, Any]: ...


# ─────────────────────────────────────────────────────────────────────────────
# DuckDB Store Protocol
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class FindingsWritePort(Protocol):
    """
    Findings-only canonical write path.

    ISSUE-K3: All findings MUST go through this port.

    Implementuje: DuckDBShadowStore

    Klíčová invarianta:
        async_ingest_findings_batch returns list with len(results) == len(findings)
        Each entry is FindingQualityDecision (rejected/duplicate) or ActivationResult (accepted).
    """

    async def async_ingest_findings_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[FindingQualityDecision | ActivationResult]: ...


# Alias pro zpětnou kompatibilitu — mnoho souborů reference DuckDBStoreProtocol
DuckDBStoreProtocol = FindingsWritePort


# ─────────────────────────────────────────────────────────────────────────────
# Graph / IOC graph protocols
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class GraphServiceProtocol(Protocol):
    """
    kontrakt pro DuckPGQGraph / knowledge graph persistence.

    Implementuje: DuckPGQGraph
    """

    async def upsert_ioc(
        self,
        ioc: Any,
        *,
        sprint_id: str | None = None,
    ) -> bool: ...

    async def find_connected(
        self,
        node_id: str,
        *,
        max_depth: int = 2,
    ) -> list[dict[str, Any]]: ...


# ─────────────────────────────────────────────────────────────────────────────
# Fetch / network protocols
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class FetchCoordinatorProtocol(Protocol):
    """
    kontrakt pro HTTP fetching.

    Implementuje: FetchCoordinator
    """

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, bytes, dict[str, str]] | None: ...


def get_governor() -> Any:
    """
    Get the singleton ResourceGovernor via the SSOT module.

    Replaces: resource_governor_provider callable pattern in coordinators.
    """
    from hledac.universal.core.resource_governor import get_governor as _gg

    return _gg()


# ─────────────────────────────────────────────────────────────────────────────
# Safe attribute access helpers (replaces getattr() abuse)
# ─────────────────────────────────────────────────────────────────────────────

def safe_get_finding_field(obj: Any, field: str, default: Any = None) -> Any:
    """
    Safely extract field from finding-like object with Protocol fallback.

    Replaces: getattr(finding, "source_type", "")
    Supports: CanonicalFinding, dict, dataclass, any object with attribute.
    """
    if isinstance(obj, FindingProto):
        return getattr(obj, field, default)
    if isinstance(obj, dict):
        return obj.get(field, default)
    if hasattr(obj, "__dataclass_fields__"):
        return getattr(obj, field, default)
    return default


def safe_get_payload_text(obj: Any) -> str:
    """
    Safely extract payload_text from finding object.

    Replaces: getattr(f, "payload_text", None)
    """
    if isinstance(obj, FindingWithPayloadProto):
        return obj.payload_text
    if isinstance(obj, dict):
        return obj.get("payload_text", "") or ""
    return getattr(obj, "payload_text", "") or ""


def safe_get_uma_state(obj: Any, default: str = "ok") -> str:
    """
    Safely extract UMA state from memory snapshot.

    Replaces: getattr(uma_snapshot, "state", "ok")
    """
    state = getattr(obj, "state", default)
    if state not in {"ok", "soft_warn", "warn", "critical", "emergency"}:
        return default
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Protocol verification helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_finding(obj: Any) -> bool:
    """Check if object satisfies FindingProto."""
    return isinstance(obj, FindingProto)


def is_finding_with_payload(obj: Any) -> bool:
    """Check if object satisfies FindingWithPayloadProto."""
    return isinstance(obj, FindingWithPayloadProto)


def is_store(obj: Any) -> bool:
    """Check if object satisfies DuckDBStoreProtocol."""
    return isinstance(obj, DuckDBStoreProtocol)


def is_graph_service(obj: Any) -> bool:
    """Check if object satisfies GraphServiceProtocol."""
    return isinstance(obj, GraphServiceProtocol)


def is_fetch_coordinator(obj: Any) -> bool:
    """Check if object satisfies FetchCoordinatorProtocol."""
    return isinstance(obj, FetchCoordinatorProtocol)
