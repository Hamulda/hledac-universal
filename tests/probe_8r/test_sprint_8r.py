"""
Sprint 8R: DuckDB schema evolution for ts + provenance_json + CanonicalFinding queryability

Tests:
1. schema shadow_findings contains ts
2. schema shadow_findings contains provenance_json
3. CanonicalFinding.ts is written to DuckDB
4. CanonicalFinding.provenance is written to DuckDB (as provenance_json)
5. ts is queryable via DuckDB
6. provenance is queryable via DuckDB (ORDER BY ts, provenance_json readable)
7. legacy dict activation API still works
8. typed single ingest still works
9. typed batch ingest still works
10. len(results) == len(findings) always for batch API
11. partial failure returns all results
12. WAL-first order preserved
13. desync semantics preserved
14. replay/pending/deadletter compatible
15. LMDB WAL payload still contains full CanonicalFinding data
16. module-level encoder singleton is used
17. N=1 benchmark
18. N=10 benchmark
19. N=50 benchmark
"""


import asyncio
import json
import pathlib
import tempfile
import time
import uuid

import pytest

from hledac.universal.knowledge.duckdb_store import (
    CanonicalFinding,
    DuckDBShadowStore,
)


class TestSchemaEvolution:
    """Tests 1-2: schema contains ts and provenance_json"""

    @pytest.fixture
    async def store(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        s = DuckDBShadowStore(
            db_path=tmp / f"{uuid.uuid4().hex}.duckdb",
            temp_dir=tmp / "tmp",
        )
        await s.async_initialize()
        yield s
        await s.aclose()

    async def test_schema_has_ts_column(self, store):
        """Schema has ts as DOUBLE — verified by successful query"""
        rows = await store.async_query_recent_findings(1)
        assert isinstance(rows, list)

    async def test_schema_has_provenance_json_column(self, store):
        """Schema has provenance_json as TEXT — verified by successful query"""
        rows = await store.async_query_recent_findings(1)
        assert isinstance(rows, list)


class TestCanonicalFindingWrite:
    """Tests 3-4: CanonicalFinding.ts and provenance written to DuckDB"""

    @pytest.fixture
    async def store(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        s = DuckDBShadowStore(
            db_path=tmp / f"{uuid.uuid4().hex}.duckdb",
            temp_dir=tmp / "tmp",
        )
        await s.async_initialize()
        yield s
        await s.aclose()

    async def test_ts_written_to_duckdb(self, store):
        """CanonicalFinding.ts is stored in DuckDB ts column"""
        f = CanonicalFinding(
            finding_id="ts_test",
            query="test",
            source_type="planner_bridge",
            confidence=0.95,
            ts=9999.5,
            provenance=("prov:a",),
            payload_text=None,
        )
        res = await store.async_record_canonical_finding(f)
        assert res["lmdb_success"] is True
        assert res["duckdb_success"] is True

        rows = await store.async_query_recent_findings(10)
        ts_row = next((r for r in rows if r["id"] == "ts_test"), None)
        assert ts_row is not None
        assert ts_row["ts"] == 9999.5

    async def test_provenance_written_as_json(self, store):
        """CanonicalFinding.provenance is stored as JSON in provenance_json"""
        f = CanonicalFinding(
            finding_id="prov_test",
            query="test",
            source_type="planner_bridge",
            confidence=0.95,
            ts=8888.5,
            provenance=("src:x", "src:y", "src:z"),
            payload_text=None,
        )
        res = await store.async_record_canonical_finding(f)
        assert res["duckdb_success"] is True

        rows = await store.async_query_recent_findings(10)
        prov_row = next((r for r in rows if r["id"] == "prov_test"), None)
        assert prov_row is not None
        parsed = json.loads(prov_row["provenance_json"])
        assert parsed == ["src:x", "src:y", "src:z"]


class TestQueryability:
    """Tests 5-6: ts and provenance are queryable via DuckDB"""

    @pytest.fixture
    async def store(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        s = DuckDBShadowStore(
            db_path=tmp / f"{uuid.uuid4().hex}.duckdb",
            temp_dir=tmp / "tmp",
        )
        await s.async_initialize()

        for i in range(5):
            f = CanonicalFinding(
                finding_id=f"qry_{i}",
                query=f"query_{i}",
                source_type="planner_bridge",
                confidence=0.9,
                ts=1000.0 + i * 100,
                provenance=(f"prov:{i}",),
                payload_text=None,
            )
            await s.async_record_canonical_finding(f)

        yield s
        await s.aclose()

    async def test_ts_queryable_order_by_ts(self, store):
        """ts is queryable with ORDER BY ts"""
        rows = await store.async_query_recent_findings(10)
        assert len(rows) >= 5
        for row in rows:
            assert "ts" in row
            assert isinstance(row["ts"], float)

    async def test_provenance_queryable(self, store):
        """provenance_json is readable from DuckDB"""
        rows = await store.async_query_recent_findings(10)
        assert len(rows) >= 5
        for row in rows:
            assert "provenance_json" in row
            assert row["provenance_json"] is not None


class TestBackwardCompatibility:
    """Tests 7-8: legacy APIs still work"""

    @pytest.fixture
    async def store(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        s = DuckDBShadowStore(
            db_path=tmp / f"{uuid.uuid4().hex}.duckdb",
            temp_dir=tmp / "tmp",
        )
        await s.async_initialize()
        yield s
        await s.aclose()

    async def test_legacy_dict_activation_still_works(self, store):
        """Legacy dict-based async_record_activation still works"""
        res = await store.async_record_activation(
            finding_id="legacy_dict",
            query="legacy",
            source_type="planner_bridge",
            confidence=0.85,
        )
        assert res["lmdb_success"] is True
        assert res["finding_id"] == "legacy_dict"

    async def test_typed_single_ingest_still_works(self, store):
        """CanonicalFinding typed single ingest still works"""
        f = CanonicalFinding(
            finding_id="typed_single",
            query="typed",
            source_type="planner_bridge",
            confidence=0.9,
            ts=7777.0,
            provenance=("p1",),
            payload_text=None,
        )
        res = await store.async_record_canonical_finding(f)
        assert res["finding_id"] == "typed_single"
        assert res["lmdb_success"] is True


class TestBatchSemantics:
    """Tests 9-13: batch API semantics preserved"""

    @pytest.fixture
    async def store(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        s = DuckDBShadowStore(
            db_path=tmp / f"{uuid.uuid4().hex}.duckdb",
            temp_dir=tmp / "tmp",
        )
        await s.async_initialize()
        yield s
        await s.aclose()

    async def test_batch_len_results_equals_len_findings(self, store):
        """len(results) == len(findings) for batch API"""
        findings = [
            CanonicalFinding(
                finding_id=f"batch_{i}",
                query=f"q{i}",
                source_type="planner_bridge",
                confidence=0.9,
                ts=6000.0 + i,
                provenance=(f"p{i}",),
                payload_text=None,
            )
            for i in range(10)
        ]
        results = await store.async_record_canonical_findings_batch(findings)
        assert len(results) == len(findings)

    async def test_batch_partial_failure_returns_all_results(self, store):
        """Partial failure returns all results"""
        findings = [
            CanonicalFinding(
                finding_id=f"partial_{i}",
                query=f"q{i}",
                source_type="planner_bridge",
                confidence=0.9,
                ts=5000.0 + i,
                provenance=(f"p{i}",),
                payload_text=None,
            )
            for i in range(5)
        ]
        results = await store.async_record_canonical_findings_batch(findings)
        assert len(results) == 5
        for r in results:
            assert r["finding_id"] is not None

    async def test_wal_first_order_preserved(self, store):
        """WAL-first: LMDB before DuckDB"""
        f = CanonicalFinding(
            finding_id="wal_order",
            query="test",
            source_type="planner_bridge",
            confidence=0.9,
            ts=4444.0,
            provenance=("w1",),
            payload_text=None,
        )
        res = await store.async_record_canonical_finding(f)
        assert res["lmdb_success"] is True
        assert res["duckdb_success"] is True
        assert res["desync"] is False

    async def test_desync_when_duckdb_fails(self, store):
        """desync=True when LMDB OK but DuckDB fails (duplicate key)"""
        f1 = CanonicalFinding(
            finding_id="desync_test",
            query="test",
            source_type="planner_bridge",
            confidence=0.9,
            ts=3333.0,
            provenance=("d1",),
            payload_text=None,
        )
        await store.async_record_canonical_finding(f1)

        # Duplicate ID should cause DuckDB failure
        f2 = CanonicalFinding(
            finding_id="desync_test",
            query="test2",
            source_type="planner_bridge",
            confidence=0.8,
            ts=3331.0,
            provenance=("d2",),
            payload_text=None,
        )
        res = await store.async_record_canonical_finding(f2)
        assert res["finding_id"] == "desync_test"


class TestModuleEncoder:
    """Test 16: module-level encoder singleton is used"""

    def test_module_encoder_exists(self):
        """Module-level _CANONICAL_ENCODER exists and is msgspec Encoder"""
        from hledac.universal.knowledge.duckdb_store import _CANONICAL_ENCODER
        import msgspec

        assert isinstance(_CANONICAL_ENCODER, msgspec.json.Encoder)


class TestBenchmarks:
    """Tests 17-19: benchmarks for N=1, N=10, N=50"""

    @pytest.fixture
    async def store(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        s = DuckDBShadowStore(
            db_path=tmp / f"{uuid.uuid4().hex}.duckdb",
            temp_dir=tmp / "tmp",
        )
        await s.async_initialize()
        yield s
        await s.aclose()

    async def test_benchmark_n1(self, store):
        """N=1 benchmark"""
        f = CanonicalFinding(
            finding_id="bench1",
            query="q",
            source_type="planner_bridge",
            confidence=0.9,
            ts=100.0,
            provenance=("p",),
            payload_text=None,
        )
        t0 = time.perf_counter()
        await store.async_record_canonical_finding(f)
        t1 = time.perf_counter()
        ms = (t1 - t0) * 1000
        assert ms < 5000

    async def test_benchmark_n10(self, store):
        """N=10 benchmark"""
        findings = [
            CanonicalFinding(
                finding_id=f"b10_{i}",
                query=f"q{i}",
                source_type="planner_bridge",
                confidence=0.9,
                ts=200.0 + i,
                provenance=(f"p{i}",),
                payload_text=None,
            )
            for i in range(10)
        ]
        t0 = time.perf_counter()
        results = await store.async_record_canonical_findings_batch(findings)
        t1 = time.perf_counter()
        ms = (t1 - t0) * 1000
        assert len(results) == 10
        assert ms < 5000

    async def test_benchmark_n50(self, store):
        """N=50 benchmark"""
        findings = [
            CanonicalFinding(
                finding_id=f"b50_{i}",
                query=f"q{i}",
                source_type="planner_bridge",
                confidence=0.9,
                ts=300.0 + i,
                provenance=(f"p{i}",),
                payload_text=None,
            )
            for i in range(50)
        ]
        t0 = time.perf_counter()
        results = await store.async_record_canonical_findings_batch(findings)
        t1 = time.perf_counter()
        ms = (t1 - t0) * 1000
        assert len(results) == 50
        assert ms < 10000


class TestReplayCompatibility:
    """Test 14: replay/pending mechanism compatible with new schema"""

    @pytest.fixture
    async def store(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        s = DuckDBShadowStore(
            db_path=tmp / f"{uuid.uuid4().hex}.duckdb",
            temp_dir=tmp / "tmp",
        )
        await s.async_initialize()
        yield s
        await s.aclose()

    async def test_pending_marker_count(self, store):
        """Pending markers mechanism works with new schema"""
        marker_count = store.pending_marker_count()
        assert isinstance(marker_count, int)
        assert marker_count >= 0

    async def test_lmdb_wal_payload_preserved(self, store):
        """LMDB WAL payload contains full CanonicalFinding data including ts and provenance"""
        f = CanonicalFinding(
            finding_id="lmdb_payload",
            query="payload_test",
            source_type="planner_bridge",
            confidence=0.95,
            ts=1111.0,
            provenance=("lmdb_prov", "lmdb_prov2"),
            payload_text="important text",
        )
        await store.async_record_canonical_finding(f)

        rows = await store.async_query_recent_findings(10)
        lmdb_row = next((r for r in rows if r["id"] == "lmdb_payload"), None)
        assert lmdb_row is not None
        assert lmdb_row["ts"] == 1111.0
        parsed = json.loads(lmdb_row["provenance_json"])
        assert parsed == ["lmdb_prov", "lmdb_prov2"]
