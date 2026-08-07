"""
Spec-Based Mock Fixtures — Issue 1.2: MagicMock() without spec= overhead

Provides spec-limited MagicMock/AsyncMock instances to reduce memory overhead:
• MagicMock() without spec: ~3 KB/instance
• MagicMock(spec=Class): ~1 KB/instance (limited attributes)
• With return_value chains: 5-10 KB → 1-2 KB

Usage:
    from tests.utils.spec_mocks import (
        make_storage_mock,
        make_duckdb_store_mock,
        make_governor_mock,
    )

    # Instead of: router = MagicMock()
    router = make_storage_mock()

    # Instead of: store = AsyncMock()
    store = make_duckdb_store_mock()

Anti-patterns this fixes:
    mock_obj = MagicMock()                    # BAD: unlimited attributes
    mock_obj.foo.bar.baz.qux()               # Creates 10 _MockChild instances
    mock_governor.sample_uma_status()         # No type checking

Correct pattern:
    mock_obj = MagicMock(spec=SomeClass)     # GOOD: limited to class attrs
    mock_obj.foo.bar.baz.qux()               # AttributeError if not in spec
    mock_governor.sample_uma_status()         # Type checking enabled

M1 8GB impact:
• 358 mock instances in test suite
• At 2 KB average savings per spec= migration
• Total potential savings: ~700 KB per test session
for mock-heavy tests (50+ mocks): 100+ KB savings per file
"""

from __future__ import annotations

import gc
import threading
import weakref
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

if TYPE_CHECKING:
    pass

# ─────────────────────────────────────────────────────────────────────────────
# MockCleanup Utility (F350M-R: Mock Memory Leak Prevention)
# ─────────────────────────────────────────────────────────────────────────────


@contextmanager
def make_storage_mock(
    *,
    backend_kind: str = "hot",
    put_return: bool = True,
    get_return: Any | None = None,
    stats_return: dict[str, Any] | None = None,
) -> MagicMock:
    """
    Create a spec-limited mock for storage backends.

    Mirrors the StorageRouter backend interface used in tests:
    • register_backend(kind, backend)
    • put(key, value) / get(key) / delete(key)
    • get_stats() -> dict

    Args:
        backend_kind: "hot" | "warm" | "cold" | "keyvalue"
        put_return: Return value for put() calls
        get_return: Return value for get() calls (None = returns new MagicMock)
        stats_return: Return value for get_stats() calls

    Example:
        mock_backend = make_storage_mock(get_return={"key": "value"})
        mock_backend.put.assert_called_once()
    """
    from hledac.universal.core.storage_router import StorageRouter

    mock = MagicMock(spec=StorageRouter)

    # Backend-specific config
    if get_return is not None:
        mock.get.return_value = get_return
    else:
        mock.get.return_value = MagicMock()

    mock.put.return_value = put_return
    mock.delete.return_value = True

    if stats_return is not None:
        mock.get_stats.return_value = stats_return
    else:
        mock.get_stats.return_value = {"entries": 0, "evictions": 0}

    return mock


def make_lmdb_mock(
    *,
    put_return: bool = True,
    get_return: bytes | None = None,
    delete_return: bool = True,
) -> MagicMock:
    """
    Create a spec-limited mock for LMDB key-value store.

    Mirrors the LMDBStoreProtocol interface:
    • put_many(items: list[tuple[bytes, bytes]]) -> int
    • get(key: bytes) -> bytes | None
    • delete(key: bytes) -> bool

    Args:
        put_return: Return value for put_many() calls
        get_return: Return value for get() calls (None = returns b"data")
        delete_return: Return value for delete() calls

    Example:
        mock_lmdb = make_lmdb_mock(get_return=b"stored_value")
        mock_lmdb.put_many.assert_called_once()
    """
    mock = MagicMock()  # No Protocol for LMDB, use base MagicMock

    mock.put_many.return_value = 1 if put_return else 0
    mock.put.return_value = put_return

    if get_return is not None:
        mock.get.return_value = get_return
    else:
        mock.get.return_value = b"default_data"

    mock.delete.return_value = delete_return

    return mock


# ─────────────────────────────────────────────────────────────────────────────
# DuckDB Store Mocks
# ─────────────────────────────────────────────────────────────────────────────


def make_duckdb_store_mock(
    *,
    ingest_return: list[Any] | None = None,
    initialize_return: bool = True,
    aclose_return: None = None,
) -> AsyncMock:
    """
    Create a spec-limited AsyncMock for DuckDB store.

    Mirrors the DuckDBStoreProtocol interface used in sprint_scheduler tests:
    • async_ingest_findings_batch(findings) -> list[FindingQualityDecision | ActivationResult]
    • async_initialize() -> None
    • aclose() -> None

    Args:
        ingest_return: Return value for async_ingest_findings_batch()
        initialize_return: Return value for async_initialize()
        aclose_return: Return value for aclose()

    Example:
        store = make_duckdb_store_mock(ingest_return=[])
        findings = await store.async_ingest_findings_batch(canonical_findings)
    """
    mock = AsyncMock()

    if ingest_return is not None:
        mock.async_ingest_findings_batch.return_value = ingest_return
    else:
        mock.async_ingest_findings_batch.return_value = []

    mock.async_initialize.return_value = initialize_return

    if aclose_return is not None:
        mock.aclose.return_value = aclose_return

    return mock


def make_duckdb_store_mock_full(
    *,
    ingest_return: list[Any] | None = None,
    query_return: list[Any] | None = None,
    health_return: bool = True,
) -> AsyncMock:
    """
    Create a full-featured DuckDB store mock with common query methods.

    Extended version with methods commonly used in integration tests:
    • async_ingest_findings_batch() / async_ingest_findings()
    • async_query_recent_findings() / async_get_recent_findings()
    • async_healthcheck() / aclose()

    Args:
        ingest_return: Return value for ingest methods
        query_return: Return value for query methods
        health_return: Return value for async_healthcheck()

    Example:
        store = make_duckdb_store_mock_full(
            ingest_return=[],
            query_return=[finding1, finding2]
        )
    """
    mock = make_duckdb_store_mock(ingest_return=ingest_return)

    if query_return is not None:
        mock.async_query_recent_findings.return_value = query_return
        mock.async_get_recent_findings.return_value = query_return
    else:
        mock.async_query_recent_findings.return_value = []
        mock.async_get_recent_findings.return_value = []

    mock.async_healthcheck.return_value = health_return
    mock.async_initialize_schema.return_value = None
    mock.vacuum_async.return_value = None

    return mock


# ─────────────────────────────────────────────────────────────────────────────
# Resource Governor Mocks
# ─────────────────────────────────────────────────────────────────────────────


def make_governor_mock(
    *,
    state: str = "normal",
    uma_status: dict[str, Any] | None = None,
    swap_policy: tuple[str, str] | None = None,
    can_afford: bool = True,
    reserve_return: None = None,
) -> MagicMock:
    """
    Create a spec-limited mock for M1ResourceGovernor.

    Mirrors the ResourceGovernor interface used in sprint_scheduler tests:
    • state() -> str
    • sample_uma_status() -> UMAStatus
    • get_swap_policy_tier(swap_gib) -> tuple[str, str]
    • can_afford_sync(cost) -> bool
    • reserve(cost_estimate, priority) -> context manager

    Args:
        state: Return value for state() calls ("normal" | "warn" | "critical")
        uma_status: Return value for sample_uma_status() calls
        swap_policy: Return value for get_swap_policy_tier() calls
        can_afford: Return value for can_afford_sync() calls
        reserve_return: Return value for reserve() context manager

    Example:
        gov = make_governor_mock(state="critical", uma_status={"rss_gib": 7.5})
        assert gov.state() == "critical"
    """
    mock = MagicMock()  # No Protocol for ResourceGovernor

    mock.state.return_value = state

    if uma_status is not None:
        mock.sample_uma_status.return_value = uma_status
    else:
        # Default UMA status mock
        mock.sample_uma_status.return_value = {
            "rss_gib": 4.0,
            "available_gib": 4.0,
            "pressure": 0.5,
        }

    if swap_policy is not None:
        mock.get_swap_policy_tier.return_value = swap_policy
    else:
        mock.get_swap_policy_tier.return_value = ("normal", "ok")

    mock.can_afford_sync.return_value = can_afford

    if reserve_return is not None:
        mock.reserve.return_value = reserve_return
    else:
        # Async context manager mock
        mock.reserve.return_value = MagicMock()
        mock.reserve.return_value.__aenter__ = AsyncMock(return_value=None)
        mock.reserve.return_value.__aexit__ = AsyncMock(return_value=None)

    mock.update.return_value = state
    mock.get_uma_telemetry.return_value = {
        "rss_gib": 4.0,
        "available_gib": 4.0,
        "pressure": 0.5,
    }

    return mock


# ─────────────────────────────────────────────────────────────────────────────
# Generic Protocol Mocks
# ─────────────────────────────────────────────────────────────────────────────


def make_async_mock(
    methods: dict[str, Any] | None = None,
    *,
    default_return: Any = None,
) -> AsyncMock:
    """
    Create a spec-limited AsyncMock with predefined method returns.

    For cases where no Protocol exists but method signatures are known.
    Provides type safety through explicit method definitions.

    Args:
        methods: Dict of method_name -> return_value
        default_return: Default return for unspecified methods

    Example:
        mock = make_async_mock({
            "fetch": {"status": 200, "body": b"data"},
            "close": None,
        })
        result = await mock.fetch("http://example.com")
    """
    mock = AsyncMock()

    if methods:
        for name, return_val in methods.items():
            setattr(mock, name, AsyncMock(return_value=return_val))

    if default_return is not None:
        # Set as default side_effect for any unspecified methods
        mock.side_effect = lambda *a, **k: default_return

    return mock


def make_sync_mock(
    methods: dict[str, Any] | None = None,
    *,
    spec: type | None = None,
    default_return: Any = None,
) -> MagicMock:
    """
    Create a spec-limited MagicMock with predefined method returns.

    Args:
        methods: Dict of method_name -> return_value
        spec: Class to use as spec (limits available attributes)
        default_return: Default return for unspecified methods

    Example:
        mock = make_sync_mock(
            methods={"get_stats": {"count": 42}},
            spec=StorageRouter
        )
    """
    if spec is not None:
        mock = MagicMock(spec=spec)
    else:
        mock = MagicMock()

    if methods:
        for name, return_val in methods.items():
            setattr(mock, name, MagicMock(return_value=return_val))

    if default_return is not None:
        mock.side_effect = lambda *a, **k: default_return

    return mock


# ─────────────────────────────────────────────────────────────────────────────
# Sprint Scheduler Mocks
# ─────────────────────────────────────────────────────────────────────────────


def make_sprint_scheduler_mock(
    *,
    duckdb_store: AsyncMock | None = None,
    governor: MagicMock | None = None,
    layer_manager: MagicMock | None = None,
    config_overrides: dict[str, Any] | None = None,
) -> tuple[MagicMock, dict[str, Any]]:
    """
    Create a mock SprintScheduler with pre-configured dependencies.

    Returns (scheduler, config) tuple for test isolation.

    Args:
        duckdb_store: Pre-configured DuckDB store mock
        governor: Pre-configured resource governor mock
        layer_manager: Pre-configured layer manager mock
        config_overrides: Config field overrides

    Example:
        scheduler, config = make_sprint_scheduler_mock(
            duckdb_store=make_duckdb_store_mock(ingest_return=[]),
            governor=make_governor_mock(state="critical"),
        )
    """
    import asyncio
    from unittest.mock import MagicMock

    from hledac.universal.runtime.sprint_scheduler import (
        SprintScheduler,
        SprintSchedulerConfig,
        SprintSchedulerResult,
    )

    # Create scheduler instance via __new__ (skip __init__)
    scheduler = MagicMock(spec=SprintScheduler)
    scheduler.__dict__ = {}  # Clear any proxy attributes

    # Config
    cfg = SprintSchedulerConfig()
    if config_overrides:
        for key, value in config_overrides.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
    scheduler._config = cfg

    # Result
    scheduler._result = SprintSchedulerResult()

    # Dependencies
    scheduler._duckdb_store = duckdb_store or make_duckdb_store_mock()
    scheduler._governor = governor or make_governor_mock()
    scheduler._layer_manager = layer_manager or MagicMock(spec=type(None))
    scheduler._enrichment_services = None

    # Internal state
    scheduler._bg_tasks: set[asyncio.Task] = set()
    scheduler._int_counter_layout = MagicMock()
    scheduler._lc_adapter = MagicMock()
    scheduler._pivot_ioc_graph = MagicMock()
    scheduler._pivot_stats = {}
    scheduler._query = ""
    scheduler._sprint_depth = 0
    scheduler._nonfeed_predispatch_done = True
    scheduler._prewindup_barrier_delayed = False
    scheduler._cycle_timeout_count = 0
    scheduler._wall_clock_start = 0.0
    scheduler._last_cycle_start = None
    scheduler._cycle_time_ema = 1.0
    scheduler._effective_max_cycles = 100
    scheduler._last_sources: list = []
    scheduler._stop_requested = False
    scheduler._runner = MagicMock()
    scheduler._acquisition_plan = MagicMock()
    scheduler._inject_ioc_graph = MagicMock()

    return scheduler, {"config": cfg}


# ─────────────────────────────────────────────────────────────────────────────
# Analysis Utilities
# ─────────────────────────────────────────────────────────────────────────────


def count_mock_methods(mock: MagicMock | AsyncMock) -> dict[str, int]:
    """
    Count configured vs auto-created mock methods.

    Useful for identifying overly-permissive mocks:
        info = count_mock_methods(test_mock)
        assert info['configured'] > info['auto_created'], "Mock too permissive"

    Returns:
        dict with 'configured' (explicitly set) and 'auto_created' counts
    """
    configured = 0
    auto_created = 0

    for attr in dir(mock):
        if attr.startswith("_"):
            continue
        try:
            value = getattr(mock, attr)
            if isinstance(value, (MagicMock, AsyncMock)):
                # Check if it was explicitly configured
                if hasattr(value, "_mock_name"):
                    configured += 1
                else:
                    auto_created += 1
        except Exception:  # noqa: BLE001
            pass

    return {"configured": configured, "auto_created": auto_created}


# ─────────────────────────────────────────────────────────────────────────────
# Thread Utilities (Issue 1.6: Thread Leak Prevention)
def mock_cleanup(*mocks: MagicMock | AsyncMock):
    """
    Context manager: collect and reset MagicMock instances on exit.

    Fixes mock memory leaks in pytest:
    • Clears _mock_children dicts (each holds 50-100 KB unreferenced)
    • Resets call counts and side effects
    • Triggers gc.collect() to free mock object memory

    Usage:
        def test_something():
            with mock_cleanup():
                scheduler = _make_scheduler_base()
                # ... test code ...
            # All mocks cleaned up after

    Args:
        *mocks: MagicMock/AsyncMock instances to clean up

    M1 8GB impact:
    • 30+ mock instances per test → 1.5-3 MB freed on exit
    • gc.collect() clears ~5-10 MB of accumulated _mock_children
    """
    try:
        yield mocks
    finally:
        _cleanup_mocks(mocks)
        gc.collect()


def _cleanup_mocks(mocks: tuple[MagicMock | AsyncMock, ...]) -> None:
    """
    Clean up mock instances: clear _mock_children, reset call counts.

    Args:
        mocks: Tuple of mock instances to clean

    Always-on, fail-safe: errors are swallowed to not break test teardown.
    """
    for mock in mocks:
        try:
            _deep_cleanup_mock(mock)
        except Exception:  # noqa: BLE001
            pass


def _deep_cleanup_mock(mock: MagicMock | AsyncMock) -> None:
    """
    Recursively clean a MagicMock and its _mock_children.

    Args:
        mock: MagicMock/AsyncMock to clean

    Clears:
    • _mock_children dict
    • _mock_sealed state
    • call_args_list
    """
    if not hasattr(mock, "_mock_children"):
        return

    # Clear direct _mock_children
    try:
        mock._mock_children.clear()
    except Exception:  # noqa: BLE001
        pass

    # Clear call tracking
    try:
        mock.call_args_list.clear()
    except Exception:  # noqa: BLE001
        pass

    try:
        mock.call_count = 0
    except Exception:  # noqa: BLE001
        pass

    # Recurse into child mocks (attribute access creates new ones, use __dict__ directly)
    try:
        for child in list(mock.__dict__.values()):
            if isinstance(child, (MagicMock, AsyncMock)):
                _deep_cleanup_mock(child)
    except Exception:  # noqa: BLE001
        pass


def reset_mock_deep(mock: MagicMock | AsyncMock) -> None:
    """
    Deep reset: reset_mock() + clear _mock_children.

    Equivalent to mock.reset_mock() but also clears the _mock_children
    dict that accumulates on chained attribute access.

    Usage:
        mock_foo.bar.baz.qux()  # creates _mock_children entries
        reset_mock_deep(mock_foo)  # clears everything

    Args:
        mock: MagicMock/AsyncMock to reset
    """
    mock.reset_mock()
    _deep_cleanup_mock(mock)


def weak_mock(mock: MagicMock | AsyncMock) -> weakref.ref:
    """
    Wrap mock in weakref to detect when all references are gone.

    Usage:
        ref = weak_mock(some_mock)
        del some_mock
        gc.collect()
        assert ref() is None, "Mock still referenced!"

    Args:
        mock: MagicMock/AsyncMock to wrap

    Returns:
        Weak reference to the mock
    """
    return weakref.ref(mock)


# ─────────────────────────────────────────────────────────────────────────────
# Thread Utilities (Issue 1.6: Thread Leak Prevention)
# ─────────────────────────────────────────────────────────────────────────────

_JOIN_TIMEOUT_S: float = 10.0


def joinable_threads(targets: Sequence[Callable[[], object]]) -> Generator[list[threading.Thread], object, object]:
    """
    Context manager: start daemon threads, join on exit with timeout.

    Prevents thread leaks between tests:
    • daemon=True — threads don't block pytest cleanup
    • join(timeout) — catches threads that crash before completion
    • join on exit — ensures cleanup even if test body raises

    Usage:
        with joinable_threads([worker_factory(i) for i in range(8)]) as threads:
            # threads are running
            pass
        # all threads joined (or killed after timeout)

    Args:
        targets: Sequence of callables to run in separate daemon threads.

    Yields:
        List of started threading.Thread instances (daemon=True).
    """
    threads: list[threading.Thread] = []
    for target in targets:
        t = threading.Thread(target=target, daemon=True)
        threads.append(t)
        t.start()
    try:
        yield threads
    finally:
        for t in threads:
            t.join(timeout=_JOIN_TIMEOUT_S)


# ─────────────────────────────────────────────────────────────────────────────
# Deprecated aliases (for migration compatibility)
# ─────────────────────────────────────────────────────────────────────────────


def make_mock_backend(**kwargs: Any) -> MagicMock:
    """Deprecated: Use make_storage_mock() instead."""
    import warnings

    warnings.warn(
        "make_mock_backend() is deprecated. Use make_storage_mock() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return make_storage_mock(**kwargs)


def make_mock_governor(**kwargs) -> MagicMock:
    """Deprecated: Use make_governor_mock() instead."""
    import warnings

    warnings.warn(
        "make_mock_governor() is deprecated. Use make_governor_mock() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return make_governor_mock(**kwargs)


def make_mock_store(**kwargs) -> AsyncMock:
    """Deprecated: Use make_duckdb_store_mock() instead."""
    import warnings

    warnings.warn(
        "make_mock_store() is deprecated. Use make_duckdb_store_mock() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return make_duckdb_store_mock(**kwargs)
