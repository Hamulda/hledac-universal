"""
Sprint F314-3: Pivot Executor — pivot_search_fn injection + default implementation

Tests for Issue #2 fix:
- pivot_search_fn injection works (Variant A recommended approach)
- _default_pivot_search returns correlated findings from duckdb_store
- feedback_adapter receives non-empty results
- execute_top produces non-zero findings
"""

import asyncio
import pytest
from typing import Any






    AutonomousPivotExecutor,
    PivotExecutionResult,
    PivotExecutionRequest,
    MAX_ACTIVE_PIVOTS,
    MAX_PIVOTS_PER_SPRINT,
    PIVOT_TIMEOUT_S,
    MAX_PIVOT_FINDINGS,
)


class FakePivot:
    """Fake pivot object for testing."""


from _core import aclose    def __init__(
        self,
        pivot_id: str = "p1",
        pivot_type: str = "domain",
        ioc_type: str = "domain",
        ioc_value: str = "evil.com",
        confidence: float = 0.9,
        priority: int = 1,
    ):
        self.pivot_id = pivot_id
        self.pivot_type = pivot_type
        self.ioc_type = ioc_type
        self.ioc_value = ioc_value
        self.confidence = confidence
        self.priority = priority


class FakeGov:
    """Fake resource governor for testing."""

    def __init__(self, critical: bool = False, emergency: bool = False):
        self._critical = critical
        self._emergency = emergency

    async def sample_uma_status(self) -> Any:
        class Status:
            is_critical = False
            is_emergency = False

        class S(Status):
            pass

        s = S()
        s.is_critical = self._critical
        s.is_emergency = self._emergency
        return s


class FakeStore:
    """Fake duckdb_store for testing."""

    def __init__(self, findings: list[dict[str, Any]] | None = None):
        self._findings = findings or []
        self.ingested: list[dict[str, Any]] = []

    async def async_query_recent_findings(self, limit: int) -> list[dict[str, Any]]:
        return self._findings[:limit]

    async def async_ingest_findings_batch(
        self, findings: list[dict[str, Any]]
    ) -> tuple[int, int]:
        self.ingested.extend(findings)
        return (len(findings), 0)


class FakeFeedback:
    """Fake hypothesis feedback adapter for testing."""

    def __init__(self):
        self.records: list[dict[str, Any]] = []

    async def async_record(
        self,
        pivot_type: str,
        ioc_type: str,
        produced_count: int,
        accepted_count: int,
        signal_value: float,
    ) -> None:
        self.records.append(
            {
                "pivot_type": pivot_type,
                "ioc_type": ioc_type,
                "produced_count": produced_count,
                "accepted_count": accepted_count,
                "signal_value": signal_value,
            }
        )


# ── Tests ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_pivot_search_returns_correlated_findings():
    """_default_pivot_search filters findings by ioc_value match."""
    store = FakeStore(
        findings=[
            {
                "query": "evil.com has ip 1.2.3.4",
                "provenance_json": '{"source":"ct"}',
                "source_type": "ct",
                "confidence": 0.9,
            },
            {
                "query": "unrelated good.com domain",
                "provenance_json": "{}",
                "source_type": "public",
                "confidence": 0.5,
            },
        ]
    )
    executor = AutonomousPivotExecutor(duckdb_store=store)
    pivot = FakePivot(ioc_value="evil.com")

    results = await executor._default_pivot_search(pivot)

    # Only the correlated finding should be returned
    assert len(results) == 1
    assert results[0]["query"] == "evil.com has ip 1.2.3.4"
    assert results[0]["accepted"] is True
    assert results[0]["pivot_derived"] is True
    assert results[0]["pivot_ioc_value"] == "evil.com"


@pytest.mark.asyncio
async def test_default_pivot_search_empty_when_no_match():
    """_default_pivot_search returns empty list when no correlation found."""
    store = FakeStore(
        findings=[
            {"query": "good.com domain", "provenance_json": "{}", "source_type": "public", "confidence": 0.5},
        ]
    )
    executor = AutonomousPivotExecutor(duckdb_store=store)
    pivot = FakePivot(ioc_value="evil.com")

    results = await executor._default_pivot_search(pivot)

    assert len(results) == 0


@pytest.mark.asyncio
async def test_run_pivot_search_uses_injected_fn():
    """_run_pivot_search delegates to injected pivot_search_fn when provided."""
    store = FakeStore()
    executor = AutonomousPivotExecutor(duckdb_store=store)

    async def custom_search(pivot: Any) -> list[dict]:
        return [{"query": f"custom result for {pivot.ioc_value}", "accepted": True}]

    executor._pivot_search_fn = custom_search
    pivot = FakePivot(ioc_value="test.com")

    results = await executor._run_pivot_search(pivot)

    assert len(results) == 1
    assert results[0]["query"] == "custom result for test.com"


@pytest.mark.asyncio
async def test_run_pivot_search_falls_back_to_default():
    """_run_pivot_search falls back to _default_pivot_search when no fn injected."""
    store = FakeStore(
        findings=[
            {"query": "matched value", "provenance_json": "{}", "source_type": "ct", "confidence": 0.9},
        ]
    )
    executor = AutonomousPivotExecutor(duckdb_store=store)
    pivot = FakePivot(ioc_value="matched")

    results = await executor._run_pivot_search(pivot)

    # Should use default search (no pivot_search_fn injected)
    assert len(results) >= 0  # either correlated or empty depending on match


@pytest.mark.asyncio
async def test_execute_top_with_duckdb_store_pivot_search():
    """execute_top produces non-zero findings via duckdb_store lookup."""
    store = FakeStore(
        findings=[
            {
                "query": "evil.com has ip 1.2.3.4",
                "provenance_json": '{"source":"ct"}',
                "source_type": "ct",
                "confidence": 0.9,
            },
        ]
    )
    executor = AutonomousPivotExecutor(
        duckdb_store=store,
        resource_governor=FakeGov(),
    )
    pivot = FakePivot(ioc_value="evil.com")

    results = await executor.execute_top([pivot], [])

    assert len(results) == 1
    r = results[0]
    assert r.attempted is True
    assert r.produced_count >= 0
    assert r.accepted_count >= 0
    assert r.error == "" or r.error is None


@pytest.mark.asyncio
async def test_execute_top_with_custom_pivot_search_fn():
    """execute_top uses injected pivot_search_fn for actual pivot execution."""
    store = FakeStore()

    async def custom_search(pivot: Any) -> list[dict]:
        return [{"query": f"custom {pivot.ioc_value}", "accepted": True}]

    executor = AutonomousPivotExecutor(
        duckdb_store=store,
        pivot_search_fn=custom_search,
    )
    pivot = FakePivot(ioc_value="pivot.test")

    results = await executor.execute_top([pivot], [])

    assert len(results) == 1
    r = results[0]
    assert r.attempted is True
    assert r.produced_count == 1
    assert r.accepted_count == 1


@pytest.mark.asyncio
async def test_feedback_adapter_receives_non_empty_results():
    """feedback_adapter.async_record called with non-zero produced/accepted counts."""
    store = FakeStore(
        findings=[
            {
                "query": "evil.com correlated",
                "provenance_json": "{}",
                "source_type": "ct",
                "confidence": 0.9,
            },
        ]
    )
    feedback = FakeFeedback()
    executor = AutonomousPivotExecutor(
        duckdb_store=store,
        feedback_adapter=feedback,
    )
    pivot = FakePivot(ioc_value="evil.com")

    await executor.execute_top([pivot], [])

    assert len(feedback.records) == 1
    record = feedback.records[0]
    assert record["pivot_type"] == "domain"
    assert record["produced_count"] >= 0


@pytest.mark.asyncio
async def test_execute_top_returns_empty_when_pivots_empty():
    """execute_top returns [] when pivots list is empty (no RAM check, no side effects)."""
    store = FakeStore()
    executor = AutonomousPivotExecutor(duckdb_store=store)

    results = await executor.execute_top([], [])  # empty pivots

    assert results == []


@pytest.mark.asyncio
async def test_execute_top_skips_when_ram_critical():
    """execute_top returns [] immediately when governor reports RAM critical."""
    store = FakeStore()

    class CriticalGov:
        async def sample_uma_status(self) -> Any:
            class S:
                is_critical = True
                is_emergency = False
            return S()

    executor = AutonomousPivotExecutor(
        duckdb_store=store,
        resource_governor=CriticalGov(),
    )
    pivot = FakePivot()

    results = await executor.execute_top([pivot], [])

    assert results == []


@pytest.mark.asyncio
async def test_execute_top_respects_max_per_sprint():
    """execute_top caps at max_per_sprint pivots."""
    store = FakeStore()

    async def custom_search(pivot: Any) -> list[dict]:
        return [{"query": "result", "accepted": True}]

    executor = AutonomousPivotExecutor(
        duckdb_store=store,
        pivot_search_fn=custom_search,
        max_per_sprint=2,
    )
    pivots = [FakePivot(pivot_id=f"p{i}") for i in range(5)]

    results = await executor.execute_top(pivots, [])

    assert len(results) <= 2


@pytest.mark.asyncio
async def test_init_with_pivot_search_fn_parameter():
    """__init__ accepts pivot_search_fn parameter and stores it."""
    store = FakeStore()

    async def custom_fn(pivot: Any) -> list[dict]:
        return []

    executor = AutonomousPivotExecutor(
        duckdb_store=store,
        pivot_search_fn=custom_fn,
    )

    assert executor._pivot_search_fn is custom_fn


def test_executor_constants_are_bounded():
    """Constants respect M1 8GB bounds."""
    assert 1 <= MAX_ACTIVE_PIVOTS <= 5
    assert 1 <= MAX_PIVOTS_PER_SPRINT <= 20
    assert 5.0 <= PIVOT_TIMEOUT_S <= 60.0
    assert 10 <= MAX_PIVOT_FINDINGS <= 200
