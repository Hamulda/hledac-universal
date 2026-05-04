"""
Sprint 8AR: pytest configuration for early cache root enforcement.
Sets HF_* and model cache env vars BEFORE any project imports.

This is the ONLY safe bootstrap point in the test harness because pytest
calls pytest_configure() before importing any test modules.
"""

import asyncio
import os
import sys
import types
import pytest


def _mock_hledac_namespace() -> None:
    """
    Mock the hledac.universal.* namespace so probe tests can import
    runtime.acquisition_strategy and other hledac.universal packages
    without requiring hledac-universal to be pip-installed.

    Strategy: pre-register every possible hledac.universal.* package as a
    fake module with __path__=[] so Python's package-import machinery finds
    them directly in sys.modules without needing __getattr__ fallthrough.
    """
    if "hledac" in sys.modules:
        return

    def _make_fake_pkg(name: str) -> types.ModuleType:
        """Create a fake package-like module (can be traversed as a namespace)."""
        pkg = types.ModuleType(name)
        pkg.__file__ = f"<mock {name}>"
        pkg.__path__ = []  # <-- makes Python treat it as a package for submodule lookup
        return pkg

    class _FakeBridge:
        ct_results_to_findings = None
        wayback_results_to_findings = None
        passive_dns_results_to_findings = None
        MAX_SAMPLE_REJECTIONS = 100

    _fake_session_runtime = _make_fake_pkg("hledac.universal.network.session_runtime")
    _fake_session_runtime.async_get_aiohttp_session = lambda: None

    # hledac.universal subpackages referenced by __init__.py imports
    for _sub in ("patterns", "fetching", "knowledge", "config",
                 "resource_allocator", "utils", "network", "export", "coordinators",
                 "graph", "security", "discovery", "intelligence", "pipeline",
                 "rendering", "discovery"):
        _pkg = _make_fake_pkg(f"hledac.universal.{_sub}")
        sys.modules[f"hledac.universal.{_sub}"] = _pkg

    # Also create second-level subpackages commonly imported, with stub attributes
    _stubs = {
        "hledac.universal.patterns.pattern_matcher": {
            "match_text": None,
            "get_pattern_pack_metadata": None,
            "extract_high_precision_entities": None,
            "get_pattern_matcher": None,
            "configure_patterns": None,
            "reset_pattern_matcher": None,
            "get_default_bootstrap_patterns": None,
            "configure_default_bootstrap_patterns_if_empty": None,
            "benchmark_build": None,
            "benchmark_match": None,
        },
        "hledac.universal.fetching.public_fetcher": {
            "async_fetch_public_text": None,
            "process_html_payload": None,
            "DEFAULT_UA": "",
            "MAX_BYTES_DEFAULT": 0,
            "MAX_BYTES_HARD": 0,
            "MAX_RETRIES": 0,
            "FetchResult": None,
        },
        "hledac.universal.knowledge.duckdb_store": {
            "DuckDBShadowStore": None,
            "ActivationResult": None,
            "ReplayResult": None,
            "CanonicalFinding": None,
            "create_owned_store": None,
        },
        "hledac.universal.config": {
            "UniversalConfig": None,
            "create_config": None,
            "load_config_from_file": None,
        },
        "hledac.universal.resource_allocator": {
            "AdaptiveSemaphore": None,
        },
        "hledac.universal.utils.concurrency": {
            "FETCH_SEMAPHORE": None,
            "adjust_fetch_workers": None,
        },
    }
    for _sub2 in _stubs:
        _pkg2 = _make_fake_pkg(_sub2)
        for _attr, _val in _stubs[_sub2].items():
            setattr(_pkg2, _attr, _val)
        sys.modules[_sub2] = _pkg2

    # Wire up parent chain
    _fake_hledac = _make_fake_pkg("hledac")
    _fake_universal = _make_fake_pkg("hledac.universal")
    _fake_hledac.universal = _fake_universal

    _fake_runtime = _make_fake_pkg("hledac.universal.runtime")
    _fake_runtime.source_finding_bridge = _FakeBridge()

    sys.modules["hledac"] = _fake_hledac
    sys.modules["hledac.universal"] = _fake_universal
    sys.modules["hledac.universal.runtime"] = _fake_runtime
    sys.modules["hledac.universal.runtime.source_finding_bridge"] = _FakeBridge()
    sys.modules["hledac.universal.network.session_runtime"] = _fake_session_runtime


def pytest_configure(config=None) -> None:
    """
    Called before any test module is imported.
    Sets cache root env vars so that HuggingFace/transformers/sentence-transformers
    use the declared runtime root instead of ~/.cache/.

    Also mocks the hledac.universal namespace for probe tests that import
    from runtime/ and other hledac.universal packages not installed as pip eggs.
    """
    # Install hledac mock so probe tests can import hledac.universal modules
    # without requiring the package to be pip-installed.
    _mock_hledac_namespace()
    # Determine runtime root - must match paths.py FALLBACK_ROOT logic
    # but without triggering the OPSEC warning (we're in test context)
    _ramdisk_env = os.environ.get("GHOST_RAMDISK", "")
    if _ramdisk_env:
        _selected = _ramdisk_env
    else:
        _selected = os.environ.get("HLEDAC_RUNTIME_ROOT", "")

    # Only override if not already set by user
    _cache_root = os.environ.get("HLEDAC_CACHE_ROOT", "")
    if not _cache_root:
        if _selected:
            os.environ["HLEDAC_CACHE_ROOT"] = _selected
        else:
            # Use fallback root path (same as paths.py)
            from pathlib import Path
            os.environ["HLEDAC_CACHE_ROOT"] = str(Path.home() / ".hledac_fallback_ramdisk")

    # HuggingFace cache directories
    _fallback_cache = os.environ["HLEDAC_CACHE_ROOT"]
    for _env_var in [
        "HF_HOME",
        "HF_HUB_CACHE",
        "HF_DATASETS_CACHE",
        "TRANSFORMERS_CACHE",
        "PYTORCH_TRANSFORMERS_CACHE",
        "PYTORCH_PRETRAINED_BERT_CACHE",
        "TORCH_HOME",
        "XDG_CACHE_HOME",
        "SENTENCE_TRANSFORMERS_HOME",
    ]:
        if not os.environ.get(_env_var):
            os.environ[_env_var] = os.path.join(_fallback_cache, "hf_cache")

    # Ensure cache directory exists
    os.makedirs(os.environ["HLEDAC_CACHE_ROOT"], exist_ok=True)
    os.makedirs(os.path.join(_fallback_cache, "hf_cache"), exist_ok=True)


# ----------------------------------------------------------------------
# Sprint 8J: Event loop repair after asyncio.run() damage
# ----------------------------------------------------------------------
# test_uma_watchdog.py uses asyncio.run() which permanently closes the
# main-thread event loop. This autouse fixture restores a fresh loop
# after every test so subsequent tests never see "no current event loop".
# https://docs.python.org/3.11/library/asyncio-runner.html#asyncio.run


@pytest.fixture(autouse=True)
def _restore_event_loop():
    """
    Restore a fresh event loop after every test.

    Problem: asyncio.run() calls loop.close() and does NOT restore the
    previous event loop. This leaves MainThread with no registered loop,
    causing subsequent tests that call asyncio.get_event_loop() to raise:
        RuntimeError: There is no current event loop in thread 'MainThread'.

    Solution: snapshot the loop before the test, restore it after.
    If the loop was destroyed (asyncio.run case), create a new one.
    """
    # Snapshot loop before test
    old_loop = None
    try:
        old_loop = asyncio.get_event_loop()
    except RuntimeError:
        pass  # no loop registered yet — normal for first test

    yield

    # Restore or recreate loop after test
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        # Loop was destroyed (asyncio.run() damage) — restore it
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
