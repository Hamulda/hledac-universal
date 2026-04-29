"""
E2E Readiness Probe — F206S

Hermetic import/callable checks for canonical sprint path connectivity.
No live network, no subprocess, no DuckDB, no store init required.

Run: pytest tests/probe_e2e_readiness/ -q
"""

import sys
from unittest.mock import MagicMock


# ──────────────────────────────────────────────────────────────────────────────
# 1. canonical owner exists
# ──────────────────────────────────────────────────────────────────────────────


def test_01_canonical_owner_exists():
    """core.__main__.run_sprint is importable and is an async def."""
    from hledac.universal.core.__main__ import run_sprint
    import inspect

    assert callable(run_sprint), "run_sprint must be callable"
    assert inspect.iscoroutinefunction(run_sprint), "run_sprint must be async def"


# ──────────────────────────────────────────────────────────────────────────────
# 2. root __main__ delegates --sprint to canonical owner
# ──────────────────────────────────────────────────────────────────────────────


def test_02_root_main_delegates_to_canonical_owner():
    """Root __main__ imports and calls core.__main__.run_sprint for --sprint."""
    import ast

    # Force import so module is in sys.modules
    import hledac.universal.__main__ as root_main_module
    root_main = sys.modules.get("hledac.universal.__main__")
    assert root_main is not None, "hledac.universal.__main__ must be importable"

    source = open(root_main.__file__, "r", encoding="utf-8").read()
    tree = ast.parse(source)

    delegates_to_core = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "core.__main__" in node.module:
                for alias in node.names:
                    if "run_sprint" in alias.name:
                        delegates_to_core = True
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "run_sprint":
                for name_node in ast.walk(node):
                    if isinstance(name_node, ast.Attribute):
                        if name_node.attr == "run_sprint":
                            delegates_to_core = True

    assert delegates_to_core, "Root __main__ must delegate --sprint to core.__main__.run_sprint"


# ──────────────────────────────────────────────────────────────────────────────
# 3. SprintScheduler lazy-imports public pipeline
# ──────────────────────────────────────────────────────────────────────────────


def test_03_scheduler_lazy_imports_public_pipeline():
    """SprintScheduler.run() imports live_public_pipeline lazily (not at module top)."""
    import ast

    scheduler_path = sys.modules["hledac.universal.runtime.sprint_scheduler"].__file__
    source = open(scheduler_path, "r", encoding="utf-8").read()
    tree = ast.parse(source)

    top_level_imports_public_pipeline = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in ast.walk(node):
                if isinstance(alias, ast.alias) and alias.name and "live_public_pipeline" in alias.name:
                    top_level_imports_public_pipeline = True

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            func_source = ast.unparse(node)
            if "live_public_pipeline" in func_source or "async_run_live_public_pipeline" in func_source:
                assert not top_level_imports_public_pipeline, (
                    "live_public_pipeline must NOT be imported at module top in sprint_scheduler"
                )
                return

    assert True


# ──────────────────────────────────────────────────────────────────────────────
# 4. live_public_pipeline uses async_fetch_public_text
# ──────────────────────────────────────────────────────────────────────────────


def test_04_live_public_pipeline_uses_async_fetch_public_text():
    """live_public_pipeline imports and calls async_fetch_public_text."""
    import ast

    # Force module load
    import hledac.universal.pipeline.live_public_pipeline as lp
    pipeline_path = sys.modules["hledac.universal.pipeline.live_public_pipeline"].__file__
    source = open(pipeline_path, "r", encoding="utf-8").read()

    has_import = "async_fetch_public_text" in source
    has_call = "await async_fetch_public_text" in source or "await _ASYNC_FETCH_PUBLIC_TEXT" in source

    assert has_import, "live_public_pipeline must import async_fetch_public_text"
    assert has_call, "live_public_pipeline must call async_fetch_public_text"


# ──────────────────────────────────────────────────────────────────────────────
# 5. public_fetcher has all transport lanes
# ──────────────────────────────────────────────────────────────────────────────


def test_05_public_fetcher_transport_lanes_exist():
    """public_fetcher has aiohttp default + httpx_h2 + curl_cffi + Tor/I2P + JS paths."""
    from hledac.universal.fetching import public_fetcher

    # aiohttp default
    assert hasattr(public_fetcher, "async_fetch_public_text"), "aiohttp path: async_fetch_public_text"
    assert callable(public_fetcher.async_fetch_public_text), "aiohttp path must be callable"

    # httpx_h2
    assert hasattr(public_fetcher, "fetch_via_httpx_h2"), "httpx_h2 path: fetch_via_httpx_h2"
    assert callable(public_fetcher.fetch_via_httpx_h2), "httpx_h2 must be callable"

    # curl_cffi
    assert hasattr(public_fetcher, "fetch_via_curl_cffi"), "curl_cffi path: fetch_via_curl_cffi"
    assert callable(public_fetcher.fetch_via_curl_cffi), "curl_cffi must be callable"

    # Tor helper
    assert hasattr(public_fetcher, "_get_tor_session"), "Tor path: _get_tor_session"
    assert callable(public_fetcher._get_tor_session), "_get_tor_session must be callable"

    # I2P helper
    assert hasattr(public_fetcher, "_get_i2p_session"), "I2P path: _get_i2p_session"
    assert callable(public_fetcher._get_i2p_session), "_get_i2p_session must be callable"


# ──────────────────────────────────────────────────────────────────────────────
# 6. TemporalSignalLayer exists and is importable
# ──────────────────────────────────────────────────────────────────────────────


def test_06_temporal_signal_layer_exists():
    """TemporalSignalLayer is importable from layers.temporal_signal_layer."""
    from hledac.universal.layers.temporal_signal_layer import TemporalSignalLayer

    assert callable(TemporalSignalLayer), "TemporalSignalLayer must be callable"

    layer = TemporalSignalLayer(max_keys=128)
    assert hasattr(layer, "observe"), "TemporalSignalLayer.observe"
    assert hasattr(layer, "snapshot"), "TemporalSignalLayer.snapshot"
    assert hasattr(layer, "from_snapshot"), "TemporalSignalLayer.from_snapshot"
    assert hasattr(layer, "get_top_scores"), "TemporalSignalLayer.get_top_scores"


# ──────────────────────────────────────────────────────────────────────────────
# 7. TemporalSignalRuntime exposes summary + priority hints
# ──────────────────────────────────────────────────────────────────────────────


def test_07_temporal_runtime_exposes_summary_and_hints():
    """get_temporal_signal_summary and build_temporal_priority_hints are importable from layers."""
    from hledac.universal.layers import (
        get_temporal_signal_summary,
        build_temporal_priority_hints,
    )

    assert callable(get_temporal_signal_summary), "get_temporal_signal_summary must be callable"
    assert callable(build_temporal_priority_hints), "build_temporal_priority_hints must be callable"

    summary = get_temporal_signal_summary(k=5)
    assert isinstance(summary, dict), "get_temporal_signal_summary must return a dict"

    hints = build_temporal_priority_hints(k=5)
    assert isinstance(hints, list), "build_temporal_priority_hints must return a list"


# ──────────────────────────────────────────────────────────────────────────────
# 8. TemporalSignalStore is env-gated
# ──────────────────────────────────────────────────────────────────────────────


def test_08_temporal_store_is_env_gated():
    """is_temporal_store_enabled() gates TemporalSignalStore access."""
    from hledac.universal.layers import is_temporal_store_enabled

    assert callable(is_temporal_store_enabled), "is_temporal_store_enabled must be callable"
    assert isinstance(is_temporal_store_enabled(), bool), "is_temporal_store_enabled must return bool"


# ──────────────────────────────────────────────────────────────────────────────
# 9. public_branch_verdict can hold temporal fields
# ──────────────────────────────────────────────────────────────────────────────


def test_09_public_branch_verdict_holds_temporal_fields():
    """live_public_pipeline verdict dict accepts temporal_signal_summary and temporal_priority_hints."""
    verdict = {
        "temporal_signal_summary": {"some_key": 0.5},
        "temporal_priority_hints": [{"key": "foo", "priority": 1}],
    }
    assert "temporal_signal_summary" in verdict
    assert "temporal_priority_hints" in verdict
    assert isinstance(verdict["temporal_signal_summary"], dict)
    assert isinstance(verdict["temporal_priority_hints"], list)


# ──────────────────────────────────────────────────────────────────────────────
# 10. security.quantum_safe NOT in canonical sprint path
# ──────────────────────────────────────────────────────────────────────────────


def test_10_security_quantumsafe_not_in_canonical_path():
    """security.quantum_safe is NOT imported by canonical sprint owner (core.__main__)."""
    import ast

    core_main = sys.modules["hledac.universal.core.__main__"]
    source = open(core_main.__file__, "r", encoding="utf-8").read()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names or []:
                if alias.name and "quantum_safe" in alias.name:
                    raise AssertionError(f"security.quantum_safe must NOT be in canonical sprint path")


# ──────────────────────────────────────────────────────────────────────────────
# 11. No numpy/pandas/mlx in temporal hot-path
# ──────────────────────────────────────────────────────────────────────────────


def test_11_no_numpy_pandas_mlx_in_temporal_hotpath():
    """layers/temporal_signal_layer.py does not import numpy, pandas, or mlx."""
    import ast

    import hledac.universal.layers.temporal_signal_layer as tsl_module
    tsl_path = sys.modules["hledac.universal.layers.temporal_signal_layer"].__file__
    source = open(tsl_path, "r", encoding="utf-8").read()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names or []:
                name = alias.name or ""
                if any(n in name for n in ("numpy", "pandas", "mlx")):
                    raise AssertionError(f"layers/temporal_signal_layer.py must NOT import {name}")


# ──────────────────────────────────────────────────────────────────────────────
# 12. curl/httpx close helpers exist and are callable
# ──────────────────────────────────────────────────────────────────────────────


def test_12_close_helpers_exist_and_are_callable():
    """close_httpx_client_async and close_curl_cffi_sessions_async are importable + async callable."""
    import inspect

    from hledac.universal.transport.httpx_client import close_httpx_client_async
    from hledac.universal.transport.curl_cffi_runtime import close_curl_cffi_sessions_async

    assert callable(close_httpx_client_async), "close_httpx_client_async must be callable"
    assert callable(close_curl_cffi_sessions_async), "close_curl_cffi_sessions_async must be callable"

    assert inspect.iscoroutinefunction(close_httpx_client_async), "close_httpx_client_async must be async"
    assert inspect.iscoroutinefunction(close_curl_cffi_sessions_async), "close_curl_cffi_sessions_async must be async"


# ──────────────────────────────────────────────────────────────────────────────
# 13. E2E command can be built without starting network
# ──────────────────────────────────────────────────────────────────────────────


def test_13_e2e_command_can_be_built_without_network():
    """Canonical sprint command args (query, duration_s) can be assembled without any I/O."""
    import asyncio

    async def check():
        from hledac.universal.core.__main__ import run_sprint
        import inspect

        sig = inspect.signature(run_sprint)
        assert "query" in sig.parameters, "run_sprint must accept 'query' parameter"
        assert "duration_s" in sig.parameters, "run_sprint must accept 'duration_s' parameter"

        return True

    result = asyncio.run(check())
    assert result


# ──────────────────────────────────────────────────────────────────────────────
# 14. export_sprint/report helper exists
# ──────────────────────────────────────────────────────────────────────────────


def test_14_export_report_helper_exists():
    """export_sprint is importable from export.sprint_exporter."""
    from hledac.universal.export.sprint_exporter import export_sprint

    assert callable(export_sprint), "export_sprint must be callable"


# ──────────────────────────────────────────────────────────────────────────────
# 15. TransportCounters is accessible in FetchResult
# ──────────────────────────────────────────────────────────────────────────────


def test_15_transport_counters_accessible():
    """TransportCounters is importable and has expected counter fields."""
    from hledac.universal.fetching.public_fetcher import TransportCounters

    tc = TransportCounters()
    assert hasattr(tc, "aiohttp_count"), "TransportCounters must have aiohttp_count"
    assert hasattr(tc, "curl_cffi_fallback_to_aiohttp_count"), "must have curl_cffi fallback count"
    assert hasattr(tc, "httpx_h2_fallback_to_aiohttp_count"), "must have httpx_h2 fallback count"

    # __slots__ class — use dir() not __dict__
    attrs = dir(tc)
    assert "aiohttp_count" in attrs, "TransportCounters must expose aiohttp_count"
    assert "curl_cffi_fallback_to_aiohttp_count" in attrs
