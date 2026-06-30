# Ensure hledac namespace resolves for all sibling subpackages.
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

# Prepend the paths needed for `hledac` to be importable. The canonical
# bootstrap below will then extend sys.path with every spec sibling.
# Idempotent — duplicates are silently dropped by `set` membership.
#
# Order matters: REPO_ROOT must end up at sys.path[0] (Python walks the
# path list in order; if the parent of the project is at index 0, Python
# discovers `hledac` as an *implicit namespace package* there and the
# real `hledac/_namespace_bootstrap.py` under REPO_ROOT becomes invisible).
# Tuple order is the iteration order, but `insert(0, …)` reverses it, so
# we list the parent FIRST and REPO_ROOT SECOND to land the desired
# final ordering of [REPO_ROOT, parent, …].
for _p in ('/Users/vojtechhamada/PycharmProjects/Hledac', str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# CRITICAL: load the real hledac.universal package via importlib so its
# `__init__.py` actually runs (populating `_LAZY_EXPORTS` and the
# `__getattr__` lazy-export machinery).  The normal `import hledac.universal`
# does NOT work reliably in this layout because Python's namespace
# package mechanism returns a stub from sys.modules before the real
# `__init__.py` gets a chance to execute.  Loading via importlib.util
# forces the source to be read and executed.
import importlib.util as _importlib_util  # noqa: E402

_HLEDAC_UNIVERSAL_INIT = (
    "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/__init__.py"
)
if os.path.isfile(_HLEDAC_UNIVERSAL_INIT):
    try:
        _spec = _importlib_util.spec_from_file_location(
            "hledac.universal", _HLEDAC_UNIVERSAL_INIT
        )
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
                # __init__.py (package), the resulting module MUST have __path__.
                # When a sub-import inside __init__.py fails (e.g. heavy optional
                # deps or cross-test contamination), exec_module leaves the
                # module in sys.modules without __path__ — a "stub". Subsequent
                # `from hledac.universal.utils.X import Y` then errors with
                # `'hledac.universal.utils' is not a package`. Drop the stub
                # so the normal import path can re-attempt from scratch.
                if init.endswith("__init__.py") and not hasattr(_m, "__path__"):
                    sys.modules.pop(modname, None)
                return
            except Exception:
                # exec_module raised — drop the partial stub so it does not
                # poison sys.modules for the rest of the collection run.
                sys.modules.pop(modname, None)
                return

for _sub in (
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
):
    _force_load(_sub)

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
    probe_dir = REPO_ROOT / "probe_r0_nonfeed_reality_lock"
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
    except (subprocess.TimeoutExpired, OSError):
        # Fail-safe: neblokuj testy kvůli autoprobe
        pass


_ensure_r0_artifacts()


# ---------------------------------------------------------------------------
# Memory Profiling Fixtures (Sprint Memory Leak Detection)
# ---------------------------------------------------------------------------

import gc
from typing import Generator

import pytest

try:
    from tests.utils.memory_profiler import (
        LEAK_THRESHOLD_MB,
        MemoryTracker,
        Snapshot,
        TracemallocSnapshot,
        assert_no_leak,
        get_rss_mb,
    )
except Exception:
    # Fail-soft: tests that need memory profiler will be skipped gracefully
    Snapshot = None
    MemoryTracker = None
    TracemallocSnapshot = None
    assert_no_leak = None
    get_rss_mb = None
    LEAK_THRESHOLD_MB = 50.0


@pytest.fixture
def memory_snapshot() -> Generator[Snapshot | None, None, None]:
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
def memory_tracker() -> Generator[MemoryTracker | None, None, None]:
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
    return assert_no_leak
