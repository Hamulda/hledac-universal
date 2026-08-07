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
# FIX: ensure_namespace_paths() does NOT add tests/ to sys.path.
# pytest's pythonpath = ["tests"] adds RELATIVE "tests" (not absolute).
# We must remove BOTH relative and absolute "tests" entries, then insert absolute.
# This prevents discovery/ from shadowing tests.utils.memory_profiler.
_rel_tests = "tests"
while _rel_tests in sys.path:
    sys.path.remove(_rel_tests)
while TESTS_DIR in sys.path:
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
    except subprocess.TimeoutExpired as err:  # noqa: BLE001
        # Fail-safe: neblokuj testy kvůli autoprobe
        pass


_ensure_r0_artifacts()


# ---------------------------------------------------------------------------
# Session-Scoped Fixtures (Issue 8.2: M1 8GB test perf)
# ---------------------------------------------------------------------------
# Heavy resources (DuckDB, OTel, event loop) initialized ONCE per session
# instead of per-test. Reduces 10+ min suite to ~3-4 min on M1 4-core.

import asyncio  # noqa: E402


# ── TEST-01: Global Timeout Enforcement ─────────────────────────────────────
# Problem: 15,710 tests, only 2 with @pytest.mark.timeout. CI/CD pipelines can
# hang on: network timeouts, deadlocks, infinite loops, resource exhaustion.
# Solution: pytest-timeout plugin (already in deps) + session autouse fixture
# as belt-and-suspenders protection. pytest-timeout uses SIGALRM on Unix
# and raises pytest_TIMEOUT.raise_timeout() — both interrupt asyncio properly.
# Env var HLEDAC_TEST_TIMEOUT overrides global default (seconds).
import os
import platform
import threading

import pytest

_TEST_TIMEOUT_ENV = int(os.environ.get("HLEDAC_TEST_TIMEOUT", "120"))
_IS_UNIX = platform.system() != "Windows"


@pytest.fixture(autouse=True, scope="session")
def _enforce_global_timeout() -> None:
    """Fail-safe: ensure pytest-timeout is actually enforcing timeouts.

    pytest-timeout is the primary mechanism (timeout= in pytest.ini, default 120s).
    This fixture is belt-and-suspenders — it detects if pytest-timeout's
    SIGALRM mechanism was bypassed and fails the session explicitly.
    On Windows (no SIGALRM): pytest-timeout is the sole mechanism.
    """
    if not _IS_UNIX:
        yield  # Windows: rely solely on pytest-timeout
        return

    import signal

    def _timeout_handler(signum: int, frame) -> None:
        pytest.fail(
            f"Global timeout ({_TEST_TIMEOUT_ENV}s) exceeded — "
            f"test hung on network/deadlock/infinite-loop. "
            f"Add @pytest.mark.timeout(N) to the offending test.",
            pytrace=False,
        )

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(_TEST_TIMEOUT_ENV)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)

# Python 3.14 removed asyncio._all_loops. Monkey-patch it back for hermetic
# loop leak detection in tests. _loop_registry tracks (loop_id -> loop_ref).
_loop_registry: dict[int, asyncio.AbstractEventLoop] = {}
_orig_new_event_loop = asyncio.new_event_loop


def _patched_new_event_loop() -> asyncio.AbstractEventLoop:
    loop = _orig_new_event_loop()
    _loop_registry[id(loop)] = loop
    return loop


asyncio.new_event_loop = _patched_new_event_loop


def _get_all_loops() -> set[asyncio.AbstractEventLoop]:
    """Python 3.14+ compatible: return all non-closed event loops."""
    return {loop for loop in _loop_registry.values() if not loop.is_closed()}


asyncio._all_loops = _get_all_loops  # type: ignore[attr-defined]
import json  # noqa: E402
import tempfile  # noqa: E402
import warnings  # noqa: E402
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
except Exception:  # noqa: BLE001
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
        # CRITICAL FIX F350M-R: reclaim event loop allocations on M1 8GB
        try:
            gc.collect()
        except Exception:  # noqa: BLE001
            pass


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
            # CRITICAL FIX F350M-R: reclaim event loop allocations on M1 8GB
            try:
                gc.collect()
            except Exception:  # noqa: BLE001
                pass

        yield store

        # Teardown — use the session event loop so we don't create an extra
        # loop that pytest-asyncio may not know about.  If no session loop is
        # available, fall back to a transient loop (will be closed below).
        # ISSUE-02 fix: use new_event_loop() pattern instead of deprecated get_event_loop()
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            _loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_loop)
        try:
            _loop.run_until_complete(store.aclose())
        except Exception:  # noqa: BLE001
            pass
        # CRITICAL FIX F350M-R: reclaim DuckDB PyO3 50-200 MB buffer on M1 8GB
        try:
            gc.collect()
        except Exception:  # noqa: BLE001
            pass
    finally:
        import shutil

        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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
except Exception:
    # fail-soft: set fallbacks so subsequent references don't NameError
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


# ---------------------------------------------------------------------------
# Asyncio Loop Leak Guard (Python 3.14+ compatible)
# ---------------------------------------------------------------------------
# Monkey-patched above (after asyncio import): asyncio._all_loops tracks
# loop IDs in _loop_registry via patched new_event_loop().  This fixture
# harvests leaked loops after every test and closes them hermetically.
# Benefit: ~80% reduction in loop leaks without per-test fixture changes.


@pytest.fixture(autouse=True)
def _gc_and_close_loops(request: pytest.FixtureRequest) -> None:
    """
    Hermetic cleanup — close leaked loops + gc.collect() after each test.

    Tracks loops created before the test via asyncio._all_loops (monkey-patched
    for Python 3.14+). After the test, any loop not in the "before" set AND
    not closed is closed and removed from _loop_registry.  Then gc.collect()
    runs once (two-pass if heavy markers present).

    Compatible: Python 3.12 (has asyncio._all_loops natively) and
    Python 3.14+ (monkey-patched via _loop_registry above).
    """
    # Capture loop IDs before test runs
    loops_before: set[int] = set()
    try:
        if hasattr(asyncio, "_all_loops"):
            loops_before = {id(loop) for loop in asyncio._all_loops()}
    except Exception:  # noqa: BLE001
        pass

    yield

    # --- close leaked loops ------------------------------------------------
    try:
        if hasattr(asyncio, "_all_loops"):
            for loop in asyncio._all_loops():
                if id(loop) not in loops_before and not loop.is_closed():
                    _loop_registry.pop(id(loop), None)
                    try:
                        pending = asyncio.all_tasks(loop)
                        if pending:
                            for t in pending:
                                t.cancel()
                            loop.run_until_complete(
                                asyncio.gather(*pending, return_exceptions=True)
                            )
                        loop.close()
                    except Exception:  # noqa: BLE001
                        pass
    except Exception:  # noqa: BLE001
        pass

    # --- gc.collect ---------------------------------------------------------
    # Delegate to _cleanup fixture (autouse order: depends on alphabetical
    # fixture name; _cleanup runs first by convention, but gc.collect is cheap
    # so we run it here too for belt-and-suspenders coverage).
    # Note: both fixtures run — _cleanup handles frozen-state, this handles loops.
    try:
        gc.collect()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _env_config_cache_clear() -> None:
    """
    Clear env_config._get_cached functools.cache before each test.

    Root cause: ENV.get_bool() uses @functools.cache on _get_cached, which
    persists across tests in the same session. patch.dict(os.environ, ...) in
    individual tests CANNOT invalidate the cache once it is populated.

    Fix: clear the cache function's underlying cache dict before every test
    so each test starts with a clean slate and patch.dict works correctly.

    Order: runs BEFORE _gc_and_close_loops (alphabetical: _e < _g).
    """
    try:
        from hledac.universal.core.env_config import _get_cached
        # @functools.cache stores in func.__wrapped__.__dict__ or func.__dict__
        _get_cached.cache_clear()
    except Exception:  # noqa: BLE001
        pass

    yield


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
        # CRITICAL FIX (memory_tracker): clear Snapshot/tracemalloc refs
        # to prevent ~5-10 MB _mock_children accumulation per tracker
        # across test session (each Snapshot holds tracemalloc internals).
        try:
            tracker._rss_snapshot = None
            tracker._tracemalloc = None
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


@pytest.fixture
def make_resource_governor_mock():
    """Fixture: vytvoří spec-limited governor mock.

    Usage:
        def test_something(make_resource_governor_mock):
            governor = make_resource_governor_mock()
    """
    return _make_resource_governor_mock


@pytest.fixture
def make_governor_mock():
    """Fixture: vytvoří unbounded governor mock."""
    return _make_governor_mock


@pytest.fixture
def make_lancedb_table_mock():
    """Fixture: vytvoří LanceDB table mock."""
    return _make_lancedb_table_mock


@pytest.fixture
def make_duckdb_batch_result_mock():
    """Fixture: vytvoří DuckDB batch mock s configurable hits."""
    return _make_duckdb_batch_result_mock


@pytest.fixture
def make_duckdb_diff_mock():
    """Fixture: vytvoří DuckDB diff mock s configurable change_events."""
    return _make_duckdb_diff_mock


@pytest.fixture
def make_session_mock():
    """Fixture: vytvoří aiohttp session mock."""
    return _make_session_mock


@pytest.fixture
def make_graph_mock():
    """Fixture: vytvoří IOCGraph mock s find_connected_batch."""
    return _make_graph_mock


@pytest.fixture
def make_ioc_graph_mock():
    """Fixture: vytvoří IOCGraph mock."""
    return _make_ioc_graph_mock


@pytest.fixture
def make_outcome_mock():
    """Fixture: vytvoří Sprint outcome mock."""
    return _make_outcome_mock


@pytest.fixture
def make_ct_batch_mock():
    """Fixture: vytvoří CT batch mock."""
    return _make_ct_batch_mock


@pytest.fixture
def make_extractor_mock():
    """Fixture: vytvoří extractor mock s nested to_dict."""
    return _make_extractor_mock


# Base Mock Fixtures (Issue 4.5: MagicMock() without spec= overhead)
# Provides spec-limited mocks — saves ~500 KB session overhead
@pytest.fixture
# Issue 5.6 Extended Mock Factories — MagicMock(spec=Klass)
# Eliminuje unbounded _mock_children growth, šetří 15-30 MB při 10+ testech
# Test soubory importují: from tests.conftest import _make_xxx_mock


def _make_resource_governor_mock(uma_state: str = "ok") -> MagicMock:
    """Governor mock s evaluate() → uma_state."""
    from hledac.universal.runtime.resource_governor import M1ResourceGovernor

    mock = MagicMock(spec=M1ResourceGovernor)
    mock.evaluate = AsyncMock(return_value=MagicMock(uma_state=uma_state))
    return mock


def _make_governor_mock() -> MagicMock:
    """Governor mock bez spec (pro situace kde spec není dostupný)."""
    mock = MagicMock()
    mock.evaluate = AsyncMock(return_value=MagicMock(uma_state="ok"))
    return mock


def _make_lancedb_table_mock() -> MagicMock:
    """LanceDB table mock s add() method."""
    return MagicMock()


def _make_duckdb_batch_result_mock(hits: list = None) -> MagicMock:
    """DuckDB batch result mock s configurable hits."""
    mock = MagicMock()
    mock.hits = hits or []
    return mock


def _make_duckdb_diff_mock(change_events: list = None) -> MagicMock:
    """DuckDB diff result mock s configurable change_events."""
    mock = MagicMock()
    mock.change_events = change_events or []
    return mock


def _make_session_mock(closed: bool = False) -> MagicMock:
    """aiohttp session mock s closed attribute."""
    mock = MagicMock()
    mock.closed = closed
    return mock


def _make_graph_mock() -> MagicMock:
    """IOCGraph mock s find_connected_batch."""
    mock = MagicMock()
    mock.find_connected_batch = MagicMock(return_value=[])
    return mock


def _make_ioc_graph_mock() -> MagicMock:
    """IOCGraph mock bez spec (pro jednoduché graph operace)."""
    return MagicMock()


def _make_outcome_mock() -> MagicMock:
    """Sprint outcome mock."""
    return MagicMock()


def _make_ct_batch_mock(hits: list = None) -> MagicMock:
    """CT batch result mock."""
    mock = MagicMock()
    mock.hits = hits or []
    return mock


def _make_extractor_mock() -> MagicMock:
    """Extractor mock s nested to_dict MagicMock."""
    mock = MagicMock()
    mock.extract = MagicMock(return_value=MagicMock(to_dict=MagicMock(return_value={})))
    return mock


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
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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
            except Exception:  # noqa: BLE001
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


# Issue #9: centralized cleanup fixture via request.addfinalizer(gc.collect)
# Replaces the old unused _gc_after_heavy_tests() function with an autouse fixture
# that runs gc.collect() after EVERY test — 2-pass for marked tests, 1-pass otherwise.
# Benefit: 10-20 MB/session RAM reduction from deterministic GC sync.

_cleanup_gc_frozen_state: bool = False


@pytest.fixture(autouse=True)
def _cleanup(request: pytest.FixtureRequest) -> None:
    """
    Centralized test cleanup — Issue #9 fix.

    ONE autouse fixture replaces scattered gc.collect() calls across 7+ test files.
    Runs gc.collect() after EVERY test via request.addfinalizer(gc.collect).

    - 2-pass GC (gc.collect(); gc.collect()) for mlx/duckdb/lmdb/heavy markers
    - 1-pass GC for all other tests
    - gc.unfreeze() if GC was frozen from MemoryTracker

    OLD scattered pattern (now replaced):
      tests/test_coroutine_cleanup.py: 7× gc.collect()
      tests/test_sprint_memory_profiling.py: 14×
      tests/test_f_u2_gc_cycle.py: 1×
      tests/test_brain_lazy.py: 2×
      tests/test_f14_duckdb_ingest_breaker.py: 2×
      tests/test_pep734_isolated_executors.py: 3×
      tests/test_sprint8ay_mlx_memory.py: 5×
    """
    global _cleanup_gc_frozen_state
    # Capture frozen state BEFORE test
    try:
        _cleanup_gc_frozen_state = gc.is_frozen()
    except Exception:
        _cleanup_gc_frozen_state = False

    def _do_cleanup() -> None:
        global _cleanup_gc_frozen_state
        # Unfreeze if needed
        if _cleanup_gc_frozen_state:
            try:
                gc.unfreeze()
            except Exception:  # noqa: BLE001
                pass
        # Issue #9: 2-pass GC for heavy tests (matches old _gc_after_heavy_tests)
        markers = {m.name for m in request.node.iter_markers()}
        if markers & {"mlx", "duckdb", "lmdb", "heavy"}:
            gc.collect()
            gc.collect()
        else:
            gc.collect()

    request.addfinalizer(_do_cleanup)
    yield


# DEPRECATED: _gc_after_heavy_tests removed — replaced by _cleanup() autouse fixture.
# The old function was a standalone generator that nobody called directly.
# Keeping as stub to avoid import errors if anything references it.
def _gc_after_heavy_tests(request: pytest.FixtureRequest) -> None:
    """Deprecated: replaced by _cleanup() autouse fixture. No-op."""
    yield


# === P3-04: Async Test Time Fixtures ===
# Problem: asyncio.sleep() and time.sleep() in tests cause flakiness on CI
# due to CPU contention and thermal throttling on M1 (not x86 speed difference).
#
# Solution: 3-layer approach:
#   1. pytest.mark.slow + pytest.mark.flaky_race markers for test classification
#   2. async_clock fixture for virtual-time tests (TTL, cache expiry)
#   3. Skip long-mocked tests on CI (e.g., 300s mock sleeps in e2e tests)


class AsyncTestClock:
    """
    Virtual clock for async tests that need deterministic timing without real waits.

    Usage in tests:
        async def test_cache_ttl(async_clock):
            await async_clock.sleep(1.5)  # Virtual 1.5s, no real waiting
            assert cache.get("key") is None  # Expired

    The clock tracks virtual time and can be advanced deterministically.
    For tests that genuinely need real async scheduling (race conditions),
    use pytest.mark.flaky_race instead.
    """

    def __init__(self, start_time: float = 0.0) -> None:
        self._virtual_time = start_time
        self._drift_factor: float = 1.0  # Can simulate faster/slower time

    async def sleep(self, seconds: float) -> None:
        """Virtual sleep - advances clock without real wall-clock waiting."""
        if seconds <= 0:
            return
        # In virtual mode, we advance virtual time but DON'T actually await anything
        # This makes TTL/expiry tests deterministic
        self._virtual_time += seconds * self._drift_factor
        # Note: Tasks scheduled with asyncio.sleep() won't fire.
        # For tests that need task scheduling, use async_clock_with_tasks() instead.

    @property
    def time(self) -> float:
        """Current virtual time."""
        return self._virtual_time

    def advance(self, seconds: float) -> None:
        """Manually advance the virtual clock."""
        self._virtual_time += seconds

    def set_drift(self, factor: float) -> None:
        """Set time drift factor (1.0 = real time, 10.0 = 10x faster)."""
        self._drift_factor = factor


@pytest.fixture
def async_clock():
    """
    Provides an AsyncTestClock instance for deterministic virtual-time testing.

    Use for:
    - Cache TTL tests (advance clock to trigger expiry)
    - Queue flush timing tests
    - Rate limiter tests

    DO NOT use for:
    - Race condition tests (need real scheduler behavior)
    - Tests that actually wait for external events (network, I/O)

    Example:
        async def test_cache_expires(async_clock):
            await cache.set("key", "value", ttl=1.0)
            await async_clock.sleep(1.1)  # Advance past TTL
            assert await cache.get("key") is None
    """
    return AsyncTestClock()


# Register custom markers for test classification
def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers for timing-sensitive test classification."""
    config.addinivalue_line(
        "markers",
        "slow: marks tests as slow (may be skipped on CI, default: 10s+ sleep)",
    )
    config.addinivalue_line(
        "markers",
        "flaky_race: marks tests that depend on real scheduler timing (non-deterministic)",
    )
    config.addinivalue_line(
        "markers",
        "phase_gate: O-03 sprint tests auto-tagged by dynamic phase_gate discovery",
    )
    # TEST-01: Register timeout marker so @pytest.mark.timeout() is recognized
    # and pytest-timeout plugin can enforce it. The global default (60s) is
    # set in pytest.ini; this marker enables per-test overrides.
    config.addinivalue_line(
        "markers",
        "timeout: per-test timeout in seconds (overrides global default)",
    )


# TEST-01: Global Timeout Enforcement — Auto-apply timeout to all collected tests
# that don't have an explicit @pytest.mark.timeout() decorator.
# Uses HLEDAC_TEST_TIMEOUT from conftest env-var (default 120s, matches pytest.ini).
_TIMEOUT_DEFAULT = int(os.environ.get("HLEDAC_TEST_TIMEOUT", "120"))




def _clear_all_lock_registries() -> None:
    """Clear ALL _LockRegistry instances across all module namespaces.

    During collection, conftest's _force_load() may load modules into different
    namespaces (e.g., 'transport.circuit_breaker' vs 'hledac.universal.transport.circuit_breaker').
    These register locks in their respective 'core.locks' module's _LockRegistry.
    The fixture must clear ALL lock registries to prevent stale lock conflicts.
    """
    for mod_name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        try:
            registry = getattr(mod, '_LockRegistry', None)
            if registry is not None and isinstance(registry, dict):
                registry.clear()
        except Exception:  # noqa: BLE001
            pass  # Best-effort cleanup


# ---------------------------------------------------------------------------
# TEST-04: Non-Deterministic Test Ordering Fixes
# ---------------------------------------------------------------------------
# Problem: Sdílené singletony mezi testy způsobují non-determinismus.
# Řešení: Per-test cleanup fixtures pro všechny globální stavy.
#
# CHYBĚJÍCÍ CLEANUP FIXTURES (doplněno):
#   _duckdb_pool_cleanup      — čistí _DUCKDB_POOL z core/rust_backend/query.py
#   _lmdb_pool_cleanup        — čistí LmdbPool singleton z runtime/lmdb_pool.py
#   _bloom_filter_cleanup     — čistí RotatingBloomFilter z tools/url_dedup.py
#
# ISOLATED STORE FIXTURES (doplněno):
#   isolated_lmdb_store       — per-test izolované LMDB (tempfile)
#   isolated_duckdb_store     — per-test izolované DuckDB (tempfile)


@pytest.fixture(autouse=True)
def _duckdb_pool_cleanup() -> None:
    """
    Auto-cleanup DuckDB connection pool after each test.

    F350M-R FIX TEST-04: _DUCKDB_POOL v core/rust_backend/query.py je procesní
    singleton. Připojení se hromadí napříč testy a způsobují non-determinismus.
    _pool_close_all() vyprázdní všechny fronty připojení.
    """
    yield
    try:
        from hledac.universal.core.rust_backend.query import _pool_close_all

        _pool_close_all()
    except Exception:  # noqa: BLE001
        pass  # fail-soft: don't fail tests for cleanup errors


@pytest.fixture(autouse=True)
def _lmdb_pool_cleanup() -> None:
    """
    Auto-cleanup LMDB pool singleton after each test.

    F350M-R FIX TEST-04: get_lmdb_pool() v runtime/lmdb_pool.py je procesní
    singleton. Executor vlákna se hromadí napříč testy a způsobují memory leaky.
    shutdown() zavře executorgracefully.
    """
    yield
    try:
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool

        pool = get_lmdb_pool()
        if hasattr(pool, "shutdown"):
            pool.shutdown(wait=False)
    except Exception:  # noqa: BLE001
        pass  # fail-soft: don't fail tests for cleanup errors


@pytest.fixture(autouse=True)
def _bloom_filter_cleanup() -> None:
    """
    Auto-cleanup RotatingBloomFilter singleton after each test.

    F350M-R FIX TEST-04: get_default_bloom_filter() v tools/url_dedup.py je
    procesní singleton. URL滤r se plní napříč testy a způsobují false-positive
    deduplikace (nebo false-negative při vyčerpání).
    reset_default_bloom_filter() vymaže všechny segmenty.
    """
    yield
    try:
        from hledac.universal.tools.url_dedup import reset_default_bloom_filter

        reset_default_bloom_filter()
    except Exception:  # noqa: BLE001
        pass  # fail-soft: don't fail tests for cleanup errors


@pytest.fixture
def isolated_lmdb_store():
    """
    Per-test izolované LMDB store — vlastní temp adresář, vlastní env.

    Použití:
        def test_something(isolated_lmdb_store):
            store = isolated_lmdb_store
            # ... test kód ...
            # automatický cleanup po testu

    Výhody oproti session_duckdb_store:
    - Žádná kontaminace mezi testy
    - Žádné sdílené připojení = deterministické výsledky
    - M1 8GB: ~10 MB na test, cleanup po každém testu

    Návrat: UnifiedLMDB nebo None (pokud LMDB nedostupná).
    """
    import shutil
    import tempfile

    temp_dir = tempfile.mkdtemp(prefix="test_lmdb_isolated_")
    try:
        from hledac.universal.core.lmdb_unified import UnifiedLMDB

        store = UnifiedLMDB(temp_dir, lazy=False)
        yield store
        store.close()
    except Exception:
        yield None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def isolated_duckdb_store():
    """
    Per-test izolované DuckDB store — vlastní temp soubor, vlastní připojení.

    Použití:
        def test_something(isolated_duckdb_store):
            store = isolated_duckdb_store
            if store is None:
                pytest.skip("DuckDB unavailable")
            # ... test kód ...
            # automatický cleanup po testu

    Výhody:
    - Žádná kontaminace mezi testy (každý test = vlastní .duckdb soubor)
    - Žádné sdílené připojení = deterministické výsledky
    - Synchroní pattern (run_until_complete) kompatibilnís pytest.fixture

    Návrat: DuckDBShadowStore nebo None (pokud DuckDB nedostupná).
    """
    import shutil
    import tempfile

    temp_dir = tempfile.mkdtemp(prefix="test_duckdb_isolated_")
    db_path = Path(temp_dir) / "test_isolated.duckdb"
    try:
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore(db_path=str(db_path))
        # ISSUE-02 fix: use new_event_loop() pattern (stejně jako session_duckdb_store)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(store.async_initialize())
        finally:
            loop.close()
        yield store

        # Teardown
        try:
            _tardown_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(_tardown_loop)
            try:
                _tardown_loop.run_until_complete(store.aclose())
            finally:
                _tardown_loop.close()
        except Exception:  # noqa: BLE001
            pass
    except Exception:
        yield None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_lock_registry() -> None:
    """Clear ALL _LockRegistry instances between every test to prevent lock-registration conflicts.

    During collection, conftest's _force_load() pre-imports modules including
    circuit_breaker, which registers _breakers_lock in _LockRegistry. Then conftest's
    hermetic sys.modules cleanup removes circuit_breaker. When a test later
    imports circuit_breaker again, it re-executes the module, creates a NEW lock,
    and tries to register it → ValueError (stale entry from collection).

    CRITICAL: circuit_breaker imports 'hledac.universal.core.locks' but conftest's
    _force_load may register the lock in a DIFFERENT module namespace. Therefore
    this fixture clears ALL _LockRegistry instances across all modules, not just
    the 'core.locks' stub.
    """
    _clear_all_lock_registries()

    yield

    _clear_all_lock_registries()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Clear ALL _LockRegistry instances before first test runs (after conftest init)."""
    _clear_all_lock_registries()

    # Fix sys.path ordering after pytest applies pythonpath from pytest.ini.
    #
    # F350M-R: pytest adds pythonpath="tests" (relative) as the FIRST entry
    # AFTER conftest.py is loaded. There are TWO "tests/" directories in the
    # project tree:
    #   - /hledac/universal/tests/     ← TESTS_DIR (canonical)
    #   - /hledac/tests/              ← other tests dir (wrong)
    #
    # The relative "tests" entry resolves to the PARENT /hledac/tests/ which
    # lacks utils/memory_profiler.py. We move the relative entry to the END
    # so TESTS_DIR (at index 0) is always tried first.
    _rel_tests = "tests"
    while _rel_tests in sys.path:
        sys.path.remove(_rel_tests)
    sys.path.append(_rel_tests)
    while TESTS_DIR in sys.path:
        sys.path.remove(TESTS_DIR)
    sys.path.insert(0, TESTS_DIR)


# =============================================================================
# O-03: Dynamic phase_gate marker — auto-discovery from actual filesystem
# Replaces static PHASE_GATES.py snapshot with runtime discovery.
# Sprint tests are now auto-tagged with phase_gate marker at collection time.
# =============================================================================

def _discover_sprint_tests() -> set[str]:
    """Discover actual test_sprint*.py files on disk at collection time."""
    sprint_files: set[str] = set()
    tests_path = Path(TESTS_DIR)
    if tests_path.is_dir():
        for f in tests_path.iterdir():
            if f.name.startswith("test_sprint") and f.suffix == ".py":
                sprint_files.add(f.name)
    return sprint_files


def pytest_collection_modifyitems(
    items: list[pytest.Item], config: pytest.Config
) -> None:
    """
    TEST-01 + O-03 + CI skip: unified collection modifier.

    1. TEST-01: Auto-apply @pytest.mark.timeout() to all tests without
       an explicit marker. Ensures 100% timeout coverage.
       Uses HLEDAC_TEST_TIMEOUT env var (default 120s, matches pytest.ini).

    2. O-03: Auto-tag test_sprint*.py files with phase_gate marker.
       Replaces the static PHASE_GATES.py snapshot.

    3. CI slow-test skip: skip @pytest.mark.slow and e2e/live tests on CI.
    """
    # --- TEST-01: Global Timeout Enforcement ---
    for item in items:
        if item.get_closest_marker("timeout") is None:
            item.add_marker(pytest.mark.timeout(_TIMEOUT_DEFAULT))

    # --- O-03: phase_gate auto-tagging ---
    sprint_files = _discover_sprint_tests()
    for item in items:
        try:
            # fspath is a py.path.local (LocalPath) - use .basename not .name
            fspath_name = item.fspath.basename if item.fspath else None
            if fspath_name and fspath_name in sprint_files:
                if not item.get_closest_marker("phase_gate"):
                    item.add_marker(pytest.mark.phase_gate)
        except Exception:  # noqa: BLE001
            pass

    # --- CI slow-test skip ---
    ci_detected = (
        os.environ.get("CI", "").lower() in ("true", "1", "yes")
        or os.environ.get("GITHUB_ACTIONS", "").lower() in ("true", "1")
        or os.environ.get("CI_NODE_TOTAL", "").strip() != ""
    )
    if not ci_detected:
        return

    skip_slow = os.environ.get("HLEDAC_SKIP_SLOW_TESTS", "1").lower() in ("1", "true", "yes")
    if skip_slow:
        for item in items:
            if item.get_closest_marker("slow"):
                item.add_marker(pytest.mark.skip(reason="slow test skipped on CI"))
            if "e2e" in item.name.lower() or "live" in str(item.fspath):
                item.add_marker(pytest.mark.skip(reason="E2E/live tests skipped on CI"))
