"""
Probe F192E.1 conftest — shared fixtures for E2E benchmark suite.

Edit ONLY these files:
- hledac/universal/tests/probe_sprint_benchmark/conftest.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any
import shutil

import pytest

from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore


# ---------------------------------------------------------------------------
# DuckDB store fixture — isolated for hermetic testing
# ---------------------------------------------------------------------------

@pytest.fixture
async def temp_duckdb_store():
    """
    Create a DuckDB store backed by a temp directory.
    Isolated: persistent dedup LMDB is bypassed so test findings aren't
    rejected as duplicates from previous runs.
    Cleaned up after test. Hermetic: no shared dedup state.
    """
    tmp = tempfile.mkdtemp(prefix="hledac_bench_")
    db_path = Path(tmp) / "shadow.duckdb"
    store = DuckDBShadowStore(db_path=str(db_path))
    store._init_persistent_dedup_lmdb = lambda: None
    await store.async_initialize()
    yield store
    try:
        await store.aclose()
    except Exception:
        pass
    try:
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# pytest configuration
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "benchmark_e2e: E2E benchmark tests for canonical sprint path",
    )
    config.addinivalue_line(
        "markers",
        "memory_budget: memory ceiling tests for M1 8GB bounded runs",
    )