"""
core/protocols.py — Protocol-based structural typing for Hledac Universal.

Sprint F290: Replaces ~45 getattr()/hasattr() calls with explicit structural
protocols. Any class implementing the required methods satisfies the Protocol
— no inheritance required (duck typing with type-safety).

PEP 544 + runtime_checkable enables structural subtyping without importing
dependent classes. Modules define only "what I need", not "from whom".

Invariant table (test name → validated property):
  test_protocol_duckdb_store       → DuckDBStoreProtocol.async_ingest_findings_batch
  test_protocol_duckdb_read        → DuckDBReadProtocol.async_query_findings
  test_protocol_graph_service       → GraphServiceProtocol.upsert_ioc
  test_protocol_fetch_coordinator   → FetchCoordinatorProtocol.fetch
  test_protocol_inference_engine    → InferenceEngineProtocol.generate
  test_protocol_lifecycle_adapter   → LifecycleAdapterProtocol.run_phase1_init
  test_protocol_dedup_manager      → DedupManagerProtocol.is_duplicate
  test_protocol_finding_runtime     → FindingProto, FindingWithPayloadProto
  test_safe_get_finding_field      → safe_get_finding_field()
  test_safe_get_payload_text       → safe_get_payload_text()

References:
  - runtime/sidecar_bus.py (getattr/getattr calls — 13 usages)
  - brain/research_hypothesis_engine.py (hasattr checks — 7 usages)
  - knowledge/duckdb_store.py (hasattr checks — 4 usages)
  - runtime/sprint_scheduler.py (getattr calls — 6 usages)
  - brain/ (getattr calls — 19 usages)
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


@runtime_checkable
class IOCExtractorProto(Protocol):
    """
    IOC extraction engine with structured extract method.

    Used by: rust.ioc.extract_iocs_flat, brain.ner_engine.extract_iocs_from_text
    """

    def extract(self, text: str) -> list[FindingProto]: ...


# ─────────────────────────────────────────────────────────────────────────────
# DuckDB Store Protocol
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class FindingsWritePort(Protocol):
    """
    Findings-only canonical write path.

    ISSUE-K3: All findings MUST go through this port. Telemetry (scorecard, episodes,
    target_memory, DHT metadata) uses TelemetryWritePort — different tables, same
    connection governor, no quality gate.

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


@runtime_checkable
class TelemetryWritePort(Protocol):
    """
    Telemetry/non-finding DuckDB write path.

    ISSUE-K3: Augments FindingsWritePort (DuckDBStoreProtocol) with telemetry
    tables that bypass the quality gate but share the same connection governor.

    Tables: sprint_scorecard, research_episodes, target_memory, dht_metadata.
    All writes are serialized through DuckDBShadowStore._executor (bounded thread pool).

    Invariant: Telemetry writes are NOT findings — they do NOT go through
    async_ingest_findings_batch() quality gate and do NOT produce
    FindingQualityDecision/ActivationResult values.
    """

    async def upsert_scorecard(self, data: dict) -> bool: ...

    async def upsert_episode(self, data: dict) -> None: ...

    async def upsert_target_memory(self, memory: Any) -> bool: ...

    async def async_ingest_dht_metadata(self, metadata: list[dict[str, Any]]) -> int: ...


@runtime_checkable
class DuckDBReadProtocol(Protocol):
    """Read-only kontrakt pro DuckDB query path."""

    async def async_query_findings(
        self,
        query: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[CanonicalFinding]: ...


@runtime_checkable
class LMDBStoreProtocol(Protocol):
    """
    LMDB key-value store for entity/claim metadata.

    Implemented by: paths.open_lmdb() context manager.
    Replaces: bytes() na LMDB buffer — ničí zero-copy.
    """

    def put_many(self, items: list[tuple[bytes, bytes]]) -> int: ...
    def get(self, key: bytes) -> bytes | None: ...


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


@runtime_checkable
class IOCGraphProto(Protocol):
    """
    IOC graph operations for identity stitching and entity resolution.

    Implemented by: ioc_graph.IOCGraph
    """

    def add_finding(self, finding: FindingProto) -> None: ...
    def get_entity(self, ioc_value: str) -> dict[str, Any] | None: ...
    def resolve_identity(self, ioc_value: str) -> list[str]: ...


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


@runtime_checkable
class CircuitBreakerProto(Protocol):
    """
    Circuit breaker for domain-level failure isolation.

    Implemented by: CircuitBreaker
    """

    def record_success(self, domain: str) -> None: ...
    def record_failure(self, domain: str) -> None: ...
    def is_open(self, domain: str) -> bool: ...


# ─────────────────────────────────────────────────────────────────────────────
# Hermes3 / MLX Inference Engine Protocol
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class InferenceEngineProtocol(Protocol):
    """
    kontrakt pro MLX LLM inference engine.

    Implementuje: Hermes3Engine
    """

    async def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str: ...

    async def generate_batch(
        self,
        prompts: list[str],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> list[str]: ...


@runtime_checkable
class UMAManagerProto(Protocol):
    """
    M1 UMA memory pressure manager.

    Implemented by: UMAWaterfall, MLXMemoryManager
    """

    async def aggressive_cleanup(self) -> None: ...
    def get_pressure_state(self) -> str: ...


def get_governor() -> Any:
    """
    Get the singleton ResourceGovernor via the SSOT module.

    Replaces: resource_governor_provider callable pattern in coordinators.
    """
    from hledac.universal.runtime.resource_governor import get_governor as _gg

    return _gg()


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle / scheduler protocols
# ─────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class LifecycleAdapterProtocol(Protocol):
    """
    kontrakt pro sprint lifecycle entry-point abstraction.

    Používá se místo přímé závislosti na konkrétní lifecycle implementaci.
    """

    async def run_phase1_init(
        self,
        adapter: Any,
        lifecycle: Any,
        ct_log_client: Any | None,
        policy_manager: Any,
        duckdb_store: DuckDBStoreProtocol | None,
        now_monotonic: float | None,
    ) -> tuple[float, bool, Any | None]: ...

    def is_terminal(self) -> bool: ...
    def should_enter_windup(self) -> bool: ...
    def remaining_time(self) -> float: ...
    def request_abort(self, reason: str) -> None: ...


@runtime_checkable
class DedupManagerProtocol(Protocol):
    """Kontrakt pro dedup manager — používá se v kvalitativní filtraci."""

    async def is_duplicate(self, fingerprint: str) -> bool: ...

    def add(self, fingerprint: str) -> None: ...


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
