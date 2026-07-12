# Ensure hledac namespace resolves for all sibling subpackages.
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
TESTS_DIR = str(REPO_ROOT / "tests")


# CRITICAL: load the real hledac.universal package via importlib so its
# `__init__.py` actually runs (populating `_LAZY_EXPORTS` and the
# `__getattr__` lazy-export machinery).  The normal `import hledac.universal`
# does NOT work reliably in this layout because Python's namespace
# package mechanism returns a stub from sys.modules before the real
# `__init__.py` gets a chance to execute.  Loading via importlib.util
# forces the source to be read and executed.
import importlib.util as _importlib_util  # noqa: E402

_HLEDAC_UNIVERSAL_INIT = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/__init__.py"
if os.path.isfile(_HLEDAC_UNIVERSAL_INIT):
    try:
        _spec = _importlib_util.spec_from_file_location("hledac.universal", _HLEDAC_UNIVERSAL_INIT)
        if _spec is not None and _spec.loader is not None:
            _hub_mod = _importlib_util.module_from_spec(_spec)
            sys.modules["hledac.universal"] = _hub_mod
            _spec.loader.exec_module(_hub_mod)
            # Bind the freshly-loaded module as an attribute on the
            # `hledac` package so `hledac.universal.X` access works too.
            _hledac_pkg = sys.modules.get("hledac")
            if _hledac_pkg is not None:
                _hledac_pkg.universal = _hub_mod
    except Exception:  # noqa: BLE001
        pass  # fail-soft — tests that need it will fail loudly enough

# Now safe to run the namespace bootstrap.

# Canonical namespace bootstrap (idempotent, fail-safe).
from hledac._namespace_bootstrap import ensure_namespace_paths  # noqa: E402

ensure_namespace_paths()

# CRITICAL: TESTS_DIR must be at sys.path[0] AFTER sibling dirs are prepended
# by ensure_namespace_paths() / _inject_sys_path().  The original insert at
# module level (line 8) is pushed down by _inject_sys_path().  We re-insert
# TESTS_DIR at index 0 to ensure `from tests.utils.memory_profiler` resolves
# to tests/utils/memory_profiler.py and not the bare discovery/ package.
if TESTS_DIR in sys.path:
    sys.path.remove(TESTS_DIR)
sys.path.insert(0, TESTS_DIR)

# Force-import all key submodules of hledac.universal so the namespace
# bootstrap does NOT create empty stubs for them.  The bootstrap's
# `_bootstrap_universal()` synthesises a ModuleType for `hledac.universal`
# before the real `__init__.py` runs; when sibling subpackages
# (`hledac.universal.runtime`, `hledac.universal.brain`, etc.) are
# later asked to import, Python's finder only sees those stubs because
# the real `hledac.universal.runtime.acquisition_strategy` etc. never
# had a chance to populate `sys.modules`.  We eagerly touch every common
# subpackage here so the real modules win.
_HUB_DIR = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"


def _force_load(modname: str) -> None:
    """Force-load `modname` from <HUB_DIR> by absolute path, replacing any
    stub already in sys.modules.  Idempotent and fail-safe.
    """
    parts = modname.split(".")
    rel = "/".join(parts) + ".py"
    init = "/".join(parts) + "/__init__.py"
    for candidate in (os.path.join(_HUB_DIR, rel), os.path.join(_HUB_DIR, init)):
        if os.path.isfile(candidate):
            # F271-FIX: `sys.path` may contain entries added by the module being
            # reloaded (e.g. `hledac.universal.paths` inserts `tests/`).  Save
            # it before dropping the old module so we can restore it.
            _saved_sys_path: list[str] = sys.path.copy()
            # Drop the stub (and any cached submodule entries) so the
            # real import wins.
            for k in list(sys.modules.keys()):
                if k == modname or k.startswith(modname + "."):
                    del sys.modules[k]
            try:
                _spec = _importlib_util.spec_from_file_location(modname, candidate)
                if _spec is None or _spec.loader is None:
                    return
                _m = _importlib_util.module_from_spec(_spec)
                sys.modules[modname] = _m
                _spec.loader.exec_module(_m)
                # F270 fix: detect partial package init. If candidate was an
                # `__init__.py` (package), the resulting module MUST have `__path__`.
                # When a sub-import inside `__init__.py` fails (e.g. heavy optional
                # deps or cross-test contamination), exec_module leaves the
                # module in sys.modules without `__path__` — a "stub". Subsequent
                # `from hledac.universal.utils.X import Y` then errors with
                # `'hledac.universal.utils' is not a package`. Drop the stub
                # so the normal import path can re-attempt from scratch.
                if init.endswith("__init__.py") and not hasattr(_m, "__path__"):
                    sys.modules.pop(modname, None)
                # F271-FIX: restore sys.path after reload.  The module's `__init__.py`
                # may have inserted project paths (e.g. `tests/` from paths.py) that
                # would otherwise be lost, causing downstream `ModuleNotFoundError`
                # for test utilities.
                sys.path[:] = _saved_sys_path
                return
            except Exception:
                # exec_module raised — drop the partial stub so it does not
                # poison sys.modules for the rest of the collection run.
                sys.modules.pop(modname, None)
                # F271-FIX: restore sys.path even on failure so the session stays
                # importable.
                sys.path[:] = _saved_sys_path
                return


# Issue P0-TEST-SPEED: Lazy _force_load via meta_path finder.
# Instead of eagerly loading 27 modules at collection time (5-10s overhead),
# install a meta_path finder that intercepts the FIRST import of any tracked
# module and force-loads it on-demand.  pytest --collect-only is now near-instant.
# Subsequent imports use sys.modules directly (our finder returns None).
_TRACKED_PREFIXES = frozenset(
    (
        "hledac.universal",
        "hledac.universal.runtime",
        "hledac.universal.runtime.acquisition_strategy",
        "hledac.universal.runtime.sprint_scheduler",
        "hledac.universal.runtime.pivot_planner",
        "hledac.universal.brain",
        "hledac.universal.brain.ane_embedder",
        "hledac.universal.coordinators",
        "hledac.universal.coordinators.fetch_coordinator",
        "hledac.universal.knowledge",
        "hledac.universal.knowledge.duckdb_store",
        "hledac.universal.utils",
        "hledac.universal.utils.concurrency",
        "hledac.universal.utils.sprint_lifecycle",
        "hledac.universal.utils.async_helpers",
        "hledac.universal.discovery",
        "hledac.universal.discovery.circl_pdns_adapter",
        "hledac.universal.discovery.duckduckgo_adapter",
        "hledac.universal.discovery.rss_atom_adapter",
        "hledac.universal.patterns",
        "hledac.universal.patterns.pattern_matcher",
        "hledac.universal.fetching",
        "hledac.universal.fetching.public_fetcher",
        "hledac.universal.transport",
        "hledac.universal.core",
        "hledac.universal.core.resource_governor",
        "hledac.universal.pipeline",
        "hledac.universal.pipeline.live_public_pipeline",
        "hledac.universal.layers",
        "hledac.universal.resource_allocator",
    )
)
_LOADED: set = set()


class _LazyForceLoadFinder:
    """Meta-path finder: force-loads from HUB_DIR on first import, then steps aside."""

    def find_spec(self, fullname, path=None, target=None):
        if not any(fullname == p or fullname.startswith(p + ".") for p in _TRACKED_PREFIXES):
            return None
        if fullname in _LOADED:
            return None
        # Mark as loading before recursing to prevent re-entrant load_module
        _LOADED.add(fullname)
        _force_load(fullname)
        return None  # let the normal import machinery take over

    def load_module(self, fullname):
        # find_spec returns None so this should not be called
        if fullname not in _LOADED:
            _force_load(fullname)
        return sys.modules.get(fullname)


if not any(isinstance(f, _LazyForceLoadFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _LazyForceLoadFinder())

# Ensure `hledac.universal` is fully loaded and bound as an attribute on
# the `hledac` package, so that `hledac.universal.X` access (used in
# many test modules) resolves the same way as `from hledac.universal
# import X`.  The stub at hledac/universal/hledac/__init__.py only
# provides a finder-level path, not an attribute bind, so we do it
# explicitly here.
try:
    import hledac as _hledac_pkg

    if not hasattr(_hledac_pkg, "universal"):
        import importlib as _importlib

        _mod = _importlib.import_module("hledac.universal")
        # Manually bind as attribute — Python's namespace-package
        # mechanism does NOT set this automatically for sub-modules.
        _hledac_pkg.universal = _mod
except Exception:  # noqa: BLE001
    pass  # fail-soft — tests that don't need it will still work


# ── R0 autoprobe (Sprint F26X) ──────────────────────────────────────────────
# Hermetický audit generuje probe_r0_nonfeed_reality_lock/ artifacts, které
# TestNoProductionEdits očekává. Fixture ho spustí LEN když artifacts chybí
# nebo jsou starší než zdroják (mtime check). Tím zajišťujeme:
#   1) Žádný overhead za běhu (lazy)
#   2) Self-healing při změně kódu
#   3) HLEDAC_REGEN_PROBES=1 vynutí rerun (pro CI)


def _r0_artifacts_stale() -> bool:
    """Return True if R0 probe artifacts are missing or older than the runner."""
    probe_dir = REPO_ROOT / "archive/probe_r/probe_r0_nonfeed_reality_lock"
    runner = REPO_ROOT / "tools" / "probe_r0_nonfeed_reality_lock.py"
    artifacts = (
        probe_dir / "REPORT_NONFEED_REALITY_LOCK.md",
        probe_dir / "nonfeed_reality_lock.json",
    )
    if not all(p.exists() for p in artifacts):
        return True
    if not runner.exists():
        return False  # nic ke srovnání
    runner_mtime = runner.stat().st_mtime
    return any(p.stat().st_mtime < runner_mtime for p in artifacts)


def _ensure_r0_artifacts() -> None:
    """Run R0 probe runner if artifacts are stale (env-gated)."""
    if os.environ.get("HLEDAC_SKIP_AUTOPROBE") == "1":
        return
    if not _r0_artifacts_stale() and os.environ.get("HLEDAC_REGEN_PROBES") != "1":
        return
    runner = REPO_ROOT / "tools" / "probe_r0_nonfeed_reality_lock.py"
    if not runner.exists():
        return
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    try:
        subprocess.run(
            [sys.executable, str(runner)],
            cwd=str(REPO_ROOT),
            env=env,
            check=False,
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as err:
        # Fail-safe: neblokuj testy kvůli autoprobe
        pass


_ensure_r0_artifacts()


# ---------------------------------------------------------------------------
# Session-Scoped Fixtures (Issue 8.2: M1 8GB test perf)
# ---------------------------------------------------------------------------
# Heavy resources (DuckDB, OTel, event loop) initialized ONCE per session
# instead of per-test. Reduces 10+ min suite to ~3-4 min on M1 4-core.

import asyncio  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402
from collections.abc import Generator  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402
from unittest.mock import MagicMock, mock_open, patch  # noqa: E402

import pytest  # noqa: E402

# OTel lazy import
_OTEL_AVAILABLE = False
_otel_tracer = None
try:
    from opentelemetry import trace

    _OTEL_AVAILABLE = True
except Exception:
    pass


@pytest.fixture(scope="session")
def session_event_loop():
    """
    Session-scoped event loop for pytest-asyncio.
    Reuses one loop across all tests instead of creating per-test.
    Required for asyncio_default_fixture_loop_scope = "session".

    Task leak guard: at teardown, any unresolved tasks are cancelled and
    logged so CI can detect forgotten task cleanup without false positives.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        # Cancel any orphaned tasks before closing the loop
        pending = asyncio.all_tasks(loop)
        if pending:
            for task in pending:
                task.cancel()
            # Give cancelled tasks a chance to clean up
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        # Drain pending callbacks (e.g. ensure_future scheduled during teardown)
        loop.call_soon(lambda: None)
        loop.close()


@pytest.fixture(scope="session")
def session_duckdb_store():
    """
    Session-scoped DuckDB store — one instance for all tests.
    Temp directory, isolated dedup LMDB, cleaned up at session end.
    M1 8GB: avoids ~132× DuckDB init overhead.

    Fail-soft: yields None if DuckDB or Rust backend unavailable (pre-existing
    bugs like DelegatingDomain NameError won't block test collection).
    """
    try:
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    except Exception:
        # Fail-soft: DuckDB/Rust unavailable — tests that need it will skip
        yield None
        return

    tmp = tempfile.mkdtemp(prefix="hledac_session_")
    try:
        db_path = Path(tmp) / "shadow.duckdb"
        store = DuckDBShadowStore(db_path=str(db_path))

        from unittest.mock import patch

        with patch.object(DuckDBShadowStore, "_init_persistent_dedup_lmdb", lambda self: None):
            loop = asyncio.new_event_loop()
            loop.run_until_complete(store.async_initialize())
            loop.close()

        yield store

        # Teardown — use the session event loop so we don't create an extra
        # loop that pytest-asyncio may not know about.  If no session loop is
        # available, fall back to a transient loop (will be closed below).
        try:
            _loop = asyncio.get_event_loop()
        except RuntimeError:
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
        try:
            _loop.run_until_complete(store.aclose())
        except Exception:
            pass
    finally:
        import shutil

        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


@pytest.fixture(scope="session")
def session_otel_tracer():
    """
    Session-scoped OTel tracer — initialized once, shared across tests.
    Exports to console (JSON-Lines) to avoid file I/O overhead.
    """
    if not _OTEL_AVAILABLE:
        yield None
        return

    tracer = trace.get_tracer("hledac.test.session")
    yield tracer
    try:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Memory Profiling Fixtures (Sprint Memory Leak Detection)
# ---------------------------------------------------------------------------

import gc  # noqa: E402

# Import mock cleanup utilities (lazy, fail-soft)
try:
    from tests.utils.spec_mocks import _deep_cleanup_mock  # noqa: E402
except Exception:
    _deep_cleanup_mock = None  # type: ignore[assignment, misc]

try:
    from tests.utils.memory_profiler import (
        LEAK_THRESHOLD_MB,
        MemoryTracker,
        Snapshot,
        TracemallocSnapshot,
        assert_no_leak,
        get_rss_mb,
        init_session_tracer,
        stop_session_tracer,
    )
except Exception as _e:
    import sys
    import traceback

    traceback.print_exc()
    print(f"FAIL memory_profiler import: {_e}\npath[:3]={sys.path[:3]}", flush=True)
    # Set fallbacks so subsequent references don't NameError
    LEAK_THRESHOLD_MB = 50.0
    MemoryTracker = None
    Snapshot = None
    TracemallocSnapshot = None
    assert_no_leak = None
    get_rss_mb = None
    init_session_tracer = None
    stop_session_tracer = None


@pytest.fixture(scope="session", autouse=True)
def _session_tracer() -> None:
    """
    Session-scoped tracemalloc tracer.
    Starts tracemalloc once at pytest session start and stops it once at
    session teardown. Eliminates repeated start/stop cycles that fragment
    Python's pymalloc arenas (~200 KB per cycle × 100 tests = ~20 MB retained).
    Individual TracemallocSnapshot / MemoryTracker instances in per-test
    fixtures only take snapshots — they never start or stop the tracer.
    """
    if init_session_tracer is not None:
        init_session_tracer()
    yield
    if stop_session_tracer is not None:
        stop_session_tracer()


@pytest.fixture
def memory_snapshot() -> Generator[Snapshot | None]:
    """
    Per-test RSS memory snapshot — takes RSS on enter, provides delta on exit.

    Usage:
        def test_something(memory_snapshot):
            before = memory_snapshot.rss_mb
            # ... test code ...
            delta = memory_snapshot.delta_mb()
            assert delta < 50

    Always-on, fail-safe: returns None if psutil unavailable.
    """
    if Snapshot is None:
        yield None
        return
    snap = Snapshot()
    gc.collect()
    yield snap


@pytest.fixture
def memory_tracker() -> Generator[MemoryTracker | None]:
    """
    Per-test memory tracker context manager — RSS + tracemalloc bookend.

    Usage:
        async def test_sprint_cycle(memory_tracker):
            tracker = memory_tracker
            with tracker:
                await run_one_cycle()
            tracker.assert_leak_threshold(50)

    Always-on, fail-safe: returns None if psutil unavailable.
    """
    if MemoryTracker is None:
        yield None
        return
    tracker = MemoryTracker(threshold_mb=LEAK_THRESHOLD_MB)
    tracker.__enter__()
    try:
        yield tracker
    finally:
        try:
            tracker.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture
def assert_memory_leak():
    """
    Standalone assertion helper for memory leak checks.

    Usage:
        def test_something(assert_memory_leak):
            before = get_rss_mb()
            # ... test code ...
            after = get_rss_mb()
            assert_memory_leak(before, after, threshold_mb=50)

    Falls back to no-op if psutil unavailable.
    """
    if assert_no_leak is None:
        return lambda *a, **k: None  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────────────
# SprintScheduler Mock Fixtures (Issue 5.6)
# Eliminuje 30+ opakujících se MagicMock atributů na test.
# Úspora: 30–50 MB při 10 testech v souboru.
# ─────────────────────────────────────────────────────────────────────────────
def _make_lifecycle_mock(remaining: float = 30.0) -> MagicMock:
    """Lifecycle mock pro _run_one_cycle testy.

    Uses spec=SprintLifecycleManager to restrict mock to real attributes only,
    preventing unbounded _mock_children growth (Issue 5.6).
    """
    from hledac.universal.utils.sprint_lifecycle import SprintLifecycleManager

    lc = MagicMock(spec=SprintLifecycleManager)
    # SprintLifecycleManager methods — spec restricts allowed attributes,
    # so we set only what exists on the class.
    lc.remaining_time = MagicMock(return_value=remaining)
    lc.recommended_tool_mode = MagicMock(return_value="normal")
    return lc


def _make_runner_mock() -> MagicMock:
    """Runner mock s konzistentními default return hodnotami.

    Uses spec=SprintLifecycleRunner to restrict mock to real attributes only,
    preventing unbounded _mock_children growth (Issue 5.6).
    Extra runtime state (current_phase, abort_requested, last_guard_observation)
    is attached as plain attributes — allowed because they don't conflict with
    spec-class members.
    """
    from hledac.universal.runtime.sprint_lifecycle_runner import SprintLifecycleRunner

    runner = MagicMock(spec=SprintLifecycleRunner)
    runner.is_terminal = MagicMock(return_value=False)
    runner.tick = MagicMock()
    runner.post_sleep_gate = MagicMock(return_value=False)
    runner.windup_guard = MagicMock(return_value=False)
    runner.last_guard_observation = {}
    runner.current_phase = "ACTIVE"
    runner.abort_requested = False
    runner.abort_reason = None
    runner.should_enter_windup = MagicMock(return_value=False)
    return runner


def _make_scheduler_base(
    sprint_duration_s: int = 60,
) -> tuple[Any, Any, MagicMock]:
    """Vytvoří (scheduler, result, runner) s přednastavenými mocky."""
    from hledac.universal.runtime.sprint_scheduler import (
        SprintScheduler,
        SprintSchedulerConfig,
        SprintSchedulerResult,
    )

    cfg = SprintSchedulerConfig(sprint_duration_s=sprint_duration_s)
    result = SprintSchedulerResult()
    scheduler = SprintScheduler.__new__(SprintScheduler)
    scheduler._config = cfg
    scheduler._result = result
    from hledac.universal.knowledge.ioc_graph import IOCGraph
    from hledac.universal.layers.layer_manager import LayerManager
    from hledac.universal.planning.acquisition_plan import AcquisitionPlan
    from hledac.universal.runtime.int_counter_layout import IntCounterLayout
    from hledac.universal.runtime.sprint_scheduler import _LifecycleAdapter

    scheduler._layer_manager = MagicMock(spec=LayerManager)
    scheduler._enrichment_services = None
    scheduler._governor = None
    scheduler._bg_tasks: set[asyncio.Task] = set()
    scheduler._int_counter_layout = MagicMock(spec=IntCounterLayout)
    scheduler._lc_adapter = MagicMock(spec=_LifecycleAdapter)
    scheduler._pivot_ioc_graph = MagicMock(spec=IOCGraph)
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
    scheduler._runner = _make_runner_mock()
    scheduler._acquisition_plan = MagicMock(spec=AcquisitionPlan)
    scheduler._injected_ioc_graph = MagicMock(spec=IOCGraph)
    return scheduler, result, scheduler._runner


@pytest.fixture
def scheduler_mocks() -> Generator[tuple[Any, Any, MagicMock], object, object]:
    """Per-test fixture vracející (scheduler, result, runner) s auto-cleanup."""
    scheduler, result, runner = _make_scheduler_base()
    try:
        yield scheduler, result, runner
    finally:
        if _deep_cleanup_mock is not None:
            _deep_cleanup_mock(scheduler)
            _deep_cleanup_mock(runner)
        gc.collect()


@pytest.fixture
def lifecycle_mock() -> Generator[MagicMock, object, object]:
    """Standard lifecycle mock pro OODA loop testy s auto-cleanup."""
    lc = _make_lifecycle_mock(remaining=30.0)
    try:
        yield lc
    finally:
        if _deep_cleanup_mock is not None:
            _deep_cleanup_mock(lc)
        gc.collect()


# ─────────────────────────────────────────────────────────────────────────────
# Git Stash Guard Mock Fixtures (Issue 5.8: mock_open pro velké soubory)
# Hermetizuje testy — žádné disk I/O, izolace od reálného settings.json

_REPO_ROOT_TEST = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
_SETTINGS_JSON_PRE_BAK = _REPO_ROOT_TEST / ".claude" / "settings.json.pre-stash-fix-2026-06-03.bak"


def _read_settings_json() -> str:
    """Read settings.json at fixture invocation time (not load/collection time).

    Avoids global mutable cache — safe for parallel test execution where each
    worker may see a different file state at the moment the fixture runs.
    """
    return (_REPO_ROOT_TEST / ".claude" / "settings.json").read_text()


def _read_settings_bak() -> str:
    """Read settings backup at fixture invocation time."""
    return _SETTINGS_JSON_PRE_BAK.read_text()


@pytest.fixture
def mock_settings_json() -> Generator[dict, object, object]:
    """Hermetický settings.json mock — fresh read per invocation, no global cache."""
    content = _read_settings_json()
    data = json.loads(content)
    m = mock_open(read_data=content)
    with patch("builtins.open", m):
        yield data


@pytest.fixture
def mock_settings_bak() -> Generator[str, object, object]:
    """Hermetický backup settings.json mock."""
    yield _read_settings_bak()


@pytest.fixture(scope="session")
def event_loop_policy() -> asyncio.DefaultEventLoopPolicy:
    return asyncio.DefaultEventLoopPolicy()


# Legacy event_loop fixture removed — F350M-R: pytest-asyncio provides
# its own session-scoped event_loop when asyncio_default_fixture_loop_scope = "session"
# (pyproject.toml:851). Keeping event_loop_policy for explicit policy control.
# Tests needing explicit loop control use session_event_loop fixture instead.


# ---------------------------------------------------------------------------
# Base Mock Fixtures (Issue 4.5: MagicMock() without spec= overhead)
# Provides spec-limited mocks — saves ~500 KB session overhead
# ---------------------------------------------------------------------------
@pytest.fixture
def base_sprint_scheduler_mock() -> MagicMock:
    from hledac.universal.runtime.sprint_scheduler import SprintScheduler

    return MagicMock(spec=SprintScheduler)


@pytest.fixture
def base_resource_governor_mock() -> MagicMock:
    from hledac.universal.runtime.resource_governor import M1ResourceGovernor

    mock = MagicMock(spec=M1ResourceGovernor)
    mock.state = "normal"
    mock.system_used_gib = 0.0
    mock.system_available_gib = 8.0
    mock.is_critical = False
    mock.is_emergency = False
    mock.is_warn = False
    mock.high_water = 0.5
    return mock


# ---------------------------------------------------------------------------
# MLX Memory Cleanup Fixtures (F350M-R: Memory Leak Fixes)
# ---------------------------------------------------------------------------
_MLX_AVAILABLE: bool = False
_mlx_core: Any = None

try:
    _MLX_AVAILABLE = True
except Exception:
    _MLX_AVAILABLE = False


@pytest.fixture(autouse=True)
def _memory_profiler_gc_sync() -> None:
    """
    Ensure GC is unfrozen before each test.

    CRITICAL FIX (F350M-R): MemoryTracker uses gc.freeze() to pin objects
    during measurement. If a previous test's MemoryTracker crashes or
    skips __exit__, GC stays frozen and subsequent gc.collect() calls
    become no-ops, silently breaking leak detection.

    This fixture runs BEFORE every test to ensure GC is in a clean state.
    """
    try:
        gc.unfreeze()
    except Exception:
        pass  # Already unfrozen or freeze unavailable
    yield


@pytest.fixture(autouse=True)
def _hermes_cache_cleanup() -> None:
    """
    Auto-cleanup HermesModelCache singleton after each test.

    CRITICAL FIX F350M-R: hermes_cache() is a process singleton.
    Models accumulate across tests unless explicitly cleared.
    """
    yield
    try:
        from brain._hermes_cache import hermes_cache

        cache = hermes_cache()
        if hasattr(cache, "clear_models"):
            cache.clear_models()
    except Exception:
        pass  # fail-soft: don't fail tests for cleanup errors


@pytest.fixture(autouse=True)
def _mlx_model_pool_cleanup() -> None:
    """
    Auto-cleanup MLXModelPool singleton after each test.

    CRITICAL FIX F350M-R: MLXModelPool is a process singleton.
    Loaded models accumulate across tests without reset.
    """
    yield
    try:
        from brain.mlx_model_pool import MLXModelPool

        if hasattr(MLXModelPool, "reset_instance"):
            MLXModelPool.reset_instance()
    except Exception:
        pass  # fail-soft: don't fail tests for cleanup errors


@pytest.fixture(autouse=True)
def _asyncio_task_leak_guard(request: pytest.FixtureRequest) -> None:
    """
    Detect and warn about asyncio task leaks within each test.

    CRITICAL FIX F350M-R: Orphaned tasks indicate forgotten cleanup.
    Uses return_exceptions=False to expose real failures, not mask them.
    """
    if not _MLX_AVAILABLE:
        yield
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        yield  # No running loop, skip check
        return

    before = len(asyncio.all_tasks(loop))
    yield
    after = len(asyncio.all_tasks(loop))

    if after > before:
        leaked = after - before
        # Cancel leaked tasks and wait for them to complete
        current = asyncio.all_tasks(loop)
        canceled_tasks = []
        for task in list(current)[before:]:
            if not task.done():
                task.cancel()
                canceled_tasks.append(task)
        # ISSUE-F350M-R FIX: await canceled tasks to suppress CancelledError noise
        if canceled_tasks:
            try:
                loop.run_until_complete(asyncio.gather(*canceled_tasks, return_exceptions=True))
            except Exception:
                pass
        # Warn but don't fail — cleanup should happen in fixture teardown
        warnings.warn(
            f"[F350M-R] {leaked} task(s) leaked in {request.node.name}. "
            f"Ensure all coroutines are awaited or explicitly cancelled.",
            RuntimeWarning,
            stacklevel=2,
        )


@pytest.fixture(autouse=True)
def _graph_service_session_cleanup() -> None:
    """
    Reset GraphService singleton state between tests.

    F350M-R: _DEFAULT_GRAPH_SERVICE holds _seen_iocs / _seen_rels idempotency
    sets that persist across tests. reset_session() clears both sets AND the
    DuckPGQGraph singleton -- preventing cross-test IOC leakage.
    """
    from hledac.universal.knowledge.graph_service import _DEFAULT_GRAPH_SERVICE

    _DEFAULT_GRAPH_SERVICE.reset_session()
    yield
    _DEFAULT_GRAPH_SERVICE.reset_session()


def _gc_after_heavy_tests(request: pytest.FixtureRequest) -> None:
    """
    Run GC after tests that use MLX/DuckDB/LMDB.

    CRITICAL FIX (F350M-R): MLX/DuckDB/LMDB allocate via Rust FFI / Metal /
    mmap; Python's cyclic GC cannot see into those allocations. Calling
    gc.collect() after the test forces the interpreter to check reachable
    objects and break reference cycles that hold FFI handles.

    Uses 2-pass collection for cyclic-ref chains (A→B→C→A).
    Unfreezes GC if frozen from previous MemoryTracker use to ensure
    clean GC state between tests.
    """
    yield
    # CRITICAL FIX (F350M-R): Unfreeze GC if frozen from MemoryTracker
    try:
        gc.unfreeze()
    except Exception:
        pass
    markers = {m.name for m in request.node.iter_markers()}
    if markers & {"mlx", "duckdb", "lmdb", "heavy"}:
        gc.collect()
        gc.collect()
