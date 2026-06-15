"""Sprint 8TC B.1: RRF three signals SQL query structure"""
import pytest
import re


def test_rrf_three_signals_union():
    """SQL query v rrf_rank_findings obsahuje 3× ROW_NUMBER (3 signály: confidence, ts, source_type)"""
    # Načteme actual source code rrf_rank_findings
    import hledac.universal.knowledge.duckdb_store as store_module
    import inspect
    source = inspect.getsource(store_module.DuckDBShadowStore.rrf_rank_findings)

    # Počet ROW_NUMBER volání — 3 signály = 3 ROW_NUMBER
    row_number_count = source.count("ROW_NUMBER()")
    assert row_number_count >= 3, f"Expected at least 3 ROW_NUMBER calls, got {row_number_count}"

    # 3 signály = 3 ROW_NUMBER() v jednom ranked CTE (s1,s2,s3 variant nebo ranked)
    row_number_count = source.count("ROW_NUMBER()")
    assert row_number_count >= 3, f"Expected at least 3 ROW_NUMBER calls, got {row_number_count}"

    # UNION ALL mezi signály
    assert "UNION ALL" in source, "Missing UNION ALL between signal CTEs"
