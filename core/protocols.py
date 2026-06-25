"""
core/protocols.py — Protocol-based Dependency Injection contracts.

PEP 544 + runtime_checkable umožňuje structural subtyping bez importu
závislých tříd. Moduly tak definují pouze "co potřebuji", ne "od koho".

Výhody pro M1 8GB / Python 3.14+:
- TYPE_CHECKING=True: type checkers vidí plné typy, runtime žádné extra importy
- runtime_checkable: isinstance() kontrola pro testování a adapter pattern
- Lazy evaluation: žádný modul neimportuje DuckDBShadowStore / Hermes3Engine na úrovni
  modulu — pouze při skutečném volání

Použití:
    from core.protocols import DuckDBStoreProtocol

    def __init__(self, db: DuckDBStoreProtocol | None = None):
        self._db = db or _create_default_store()

Invariant: Všechny Protocol metody jsou async — synchroní metody vrací Awaitable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from typing import Any

    # Forward-declare bez importu — pouze pro type hints
    from hledac.universal.knowledge.duckdb_store import (
        ActivationResult,
        CanonicalFinding,
        FindingQualityDecision,
    )


# ---------------------------------------------------------------------------
# DuckDB Store Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class DuckDBStoreProtocol(Protocol):
    """
    Minimální kontrakt pro DuckDB canonical write path.

    Implementuje: DuckDBShadowStore

    Klíčová invarianta:
        async_ingest_findings_batch returns list with len(results) == len(findings)
        Each entry is FindingQualityDecision (rejected/duplicate) or ActivationResult (accepted).
    """

    async def async_ingest_findings_batch(
        self,
        findings: list[CanonicalFinding],
    ) -> list[FindingQualityDecision | ActivationResult]: ...


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


# ---------------------------------------------------------------------------
# Graph Service Protocol
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Fetch Coordinator Protocol
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Hermes3 / MLX Inference Engine Protocol
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Lifecycle Adapter Protocol
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Quality Assessment Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class DedupManagerProtocol(Protocol):
    """Kontrakt pro dedup manager — používá se v kvalitativní filtraci."""

    async def is_duplicate(self, fingerprint: str) -> bool: ...

    def add(self, fingerprint: str) -> None: ...
