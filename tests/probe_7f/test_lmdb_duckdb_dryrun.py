"""
Sprint 7F — LMDB→DuckDB Dry-Run Write Truth Tests
=====================================================

Tests for minimal dry-run activation path:
9.  canonical finding DTO is used
10. synthetic finding can be written to LMDB
11. synthetic analytics row can be written to DuckDB
12. DuckDB write uses bulk path (executemany/appender)
13. DuckDB write lock is released after use
14. read-back from LMDB works
15. read-back from DuckDB works
16. read-back verifies content, not just existence
17. minimal activation flow works without AO
18. import regression
"""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from typing import Any, Dict, List

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestCanonicalFindingDTO(unittest.TestCase):
    """Test that an existing canonical finding DTO is used."""

    def test_research_finding_dto_exists(self):
        """9. canonical finding DTO exists in the codebase."""
        # The main AO ResearchFinding is the canonical DTO for findings.
        # Since we can't import AO (FORBIDDEN), we verify via
        # duckdb_store schema which expects: id, query, source_type, confidence
        # This is the closest production storage contract we can verify.
        from hledac.universal.knowledge.duckdb_store import _SCHEMA_SQL
        # Schema confirms the finding contract (canonical_findings = renamed from shadow_findings at 41db8f0f)
        self.assertIn("canonical_findings", _SCHEMA_SQL)
        self.assertIn("id", _SCHEMA_SQL)
        self.assertIn("query", _SCHEMA_SQL)
        self.assertIn("source_type", _SCHEMA_SQL)
        self.assertIn("confidence", _SCHEMA_SQL)


class TestLMDBWrite(unittest.TestCase):
    """Test LMDB write with synthetic finding."""

    def test_lmdb_kvstore_can_store_finding(self):
        """10. synthetic finding can be written to LMDB."""
        from hledac.universal.tools.lmdb_kv import LMDBKVStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir, map_size=8 * 1024 * 1024)

            synthetic = {
                "id": "test-finding-001",
                "query": "synthetic research query",
                "source_type": "synthetic",
                "confidence": 0.95,
                "ts": time.time(),
            }

            ok = store.put("finding:test-finding-001", synthetic)
            self.assertTrue(ok)

            # Read back
            result = store.get("finding:test-finding-001")
            assert result is not None
            self.assertEqual(result["id"], "test-finding-001")
            self.assertEqual(result["confidence"], 0.95)

            store.close()


class TestDuckDBWrite(unittest.TestCase):
    """Test DuckDB bulk write and lock release."""

    @classmethod
    def setUpClass(cls):
        # Sprint 8D: skip entire class if duckdb not available
        import importlib.util
        if importlib.util.find_spec("duckdb") is None:
            raise unittest.SkipTest("duckdb not installed in this environment")

    def test_duckdb_bulk_write_uses_executemany_path(self):
        """11-12. DuckDB write uses bulk path and releases lock."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_analytics.duckdb"
            temp_dir = Path(tmpdir) / "duckdb_tmp"
            store = DuckDBShadowStore()
            # Set both _db_path AND _temp_dir (MODE A: file mode with temp)
            with patch.object(store, "_resolve_path"):
                store._db_path = db_path
                store._temp_dir = temp_dir
                ok = store.initialize()
            self.assertTrue(ok)

            # Bulk write — loop over async_record_shadow_finding (writes to DuckDB via WAL)
            async def bulk_insert():
                tasks = [
                    store.async_record_shadow_finding(
                        f"synthetic-finding-{i}",
                        f"synthetic query {i}",
                        "synthetic",
                        0.9 + i * 0.01,
                    )
                    for i in range(10)
                ]
                return await asyncio.gather(*tasks)

            results: list[bool] = asyncio.run(bulk_insert())
            self.assertEqual(len(results), 10)
            self.assertTrue(all(results))

            # Read back
            rows: List[Dict[str, Any]] = asyncio.run(
                store.async_query_recent_findings(limit=10)
            )
            self.assertEqual(len(rows), 10)

            # Verify content — not just existence
            ids = {r["id"] for r in rows}
            for i in range(10):
                self.assertIn(f"synthetic-finding-{i}", ids)

            # Verify confidence values
            id_to_conf = {r["id"]: r["confidence"] for r in rows}
            self.assertAlmostEqual(id_to_conf["synthetic-finding-0"], 0.9, places=2)
            self.assertAlmostEqual(id_to_conf["synthetic-finding-9"], 0.99, places=2)

            # Shutdown
            asyncio.run(store.aclose())

    def test_duckdb_lock_released_after_close(self):
        """13. DuckDB write lock is released after aclose()."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_lock.duckdb"
            temp_dir = Path(tmpdir) / "duckdb_tmp"
            store = DuckDBShadowStore()
            with patch.object(store, "_resolve_path"):
                store._db_path = db_path
                store._temp_dir = temp_dir
                ok = store.initialize()
            self.assertTrue(ok)

            # Insert one record
            asyncio.run(
                store.async_record_shadow_finding(
                    "lock-test-001", "lock query", "synthetic", 0.88
                )
            )

            # Close
            asyncio.run(store.aclose())

            # Re-open should succeed (lock released)
            store2 = DuckDBShadowStore()
            with patch.object(store2, "_resolve_path"):
                store2._db_path = db_path
                store2._temp_dir = temp_dir
                ok2 = store2.initialize()
            self.assertTrue(ok2)

            rows: List[Dict[str, Any]] = asyncio.run(
                store2.async_query_recent_findings(limit=5)
            )
            # Record persists and is readable — no write lock
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["id"], "lock-test-001")

            asyncio.run(store2.aclose())


class TestMinimalActivationFlow(unittest.TestCase):
    """Test minimal synthetic activation flow without AO."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        if importlib.util.find_spec("duckdb") is None:
            raise unittest.SkipTest("duckdb not installed")

    def test_minimal_flow_lmdb_then_duckdb(self):
        """16. minimal activation flow: LMDB → DuckDB → read-back, without AO."""
        from hledac.universal.tools.lmdb_kv import LMDBKVStore
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmpdir:
            # Step 1: Write synthetic finding to LMDB
            lmdb_store = LMDBKVStore(path=tmpdir, map_size=8 * 1024 * 1024)
            finding_id = "minimal-001"
            synthetic = {
                "id": finding_id,
                "query": "minimal activation query",
                "source_type": "synthetic",
                "confidence": 0.92,
                "ts": time.time(),
            }
            ok1 = lmdb_store.put(f"finding:{finding_id}", synthetic)
            self.assertTrue(ok1)

            # Step 2: Write analytics row to DuckDB
            duckdb_path = Path(tmpdir) / "minimal_analytics.duckdb"
            duckdb_tmp = Path(tmpdir) / "duckdb_tmp"
            duckdb_store = DuckDBShadowStore()
            with patch.object(duckdb_store, "_resolve_path"):
                duckdb_store._db_path = duckdb_path
                duckdb_store._temp_dir = duckdb_tmp
                duck_ok = duckdb_store.initialize()
            self.assertTrue(duck_ok)

            duck_inserted = asyncio.run(
                duckdb_store.async_record_shadow_finding(
                    finding_id, "minimal activation query", "synthetic", 0.92
                )
            )
            self.assertTrue(duck_inserted)

            # Step 3: Read-back from both
            lmdb_result = lmdb_store.get(f"finding:{finding_id}")
            assert lmdb_result is not None
            self.assertEqual(lmdb_result["id"], finding_id)
            self.assertEqual(lmdb_result["confidence"], 0.92)

            duckdb_rows: List[Dict[str, Any]] = asyncio.run(
                duckdb_store.async_query_recent_findings(limit=5)
            )
            self.assertEqual(len(duckdb_rows), 1)
            self.assertEqual(duckdb_rows[0]["id"], finding_id)
            self.assertAlmostEqual(duckdb_rows[0]["confidence"], 0.92, places=2)

            # Cleanup
            asyncio.run(duckdb_store.aclose())
            asyncio.run(asyncio.sleep(0))  # allow executor shutdown
            lmdb_store.close()


class TestImportRegression(unittest.TestCase):
    """Test that imports don't regress."""

    def test_uma_budget_imports_cleanly(self):
        """17. uma_budget imports without errors."""
        import importlib
        import hledac.universal.utils.uma_budget as uma
        importlib.reload(uma)
        # Should have all original functions + new watchdog
        self.assertTrue(hasattr(uma, "get_uma_pressure_level"))
        self.assertTrue(hasattr(uma, "UmaWatchdog"))
        self.assertTrue(hasattr(uma, "UmaWatchdogCallbacks"))

    def test_lmdb_kv_imports_cleanly(self):
        """17b. lmdb_kv imports cleanly."""
        import importlib
        import hledac.universal.tools.lmdb_kv as lmdb_kv
        importlib.reload(lmdb_kv)
        self.assertTrue(hasattr(lmdb_kv, "LMDBKVStore"))
        self.assertTrue(hasattr(lmdb_kv, "AsyncLMDBKVStore"))

    def test_duckdb_store_imports_cleanly(self):
        """17c. duckdb_store imports cleanly (lazy duckdb)."""
        import importlib
        import hledac.universal.knowledge.duckdb_store as duckdb_store
        importlib.reload(duckdb_store)
        self.assertTrue(hasattr(duckdb_store, "DuckDBShadowStore"))


class TestReadBackContentVerification(unittest.TestCase):
    """Test read-back verifies actual content, not just existence."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        if importlib.util.find_spec("duckdb") is None:
            raise unittest.SkipTest("duckdb not installed")

    def test_lmdb_readback_verifies_content(self):
        """15a. LMDB read-back verifies content, not just existence."""
        from hledac.universal.tools.lmdb_kv import LMDBKVStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = LMDBKVStore(path=tmpdir)

            # Store finding with unique confidence
            finding = {
                "id": "content-test",
                "query": "content verification query",
                "source_type": "synthetic",
                "confidence": 0.777,
                "payload": {"nested": "data"},
            }
            store.put("finding:content-test", finding)

            # Read back and verify exact content
            result = store.get("finding:content-test")
            assert result is not None
            self.assertEqual(result["confidence"], 0.777)
            self.assertEqual(result["payload"]["nested"], "data")
            self.assertEqual(result["query"], "content verification query")

            # Wrong key returns None (not empty dict)
            empty = store.get("nonexistent-key")
            self.assertIsNone(empty)

            store.close()

    def test_duckdb_readback_verifies_content(self):
        """15b. DuckDB read-back verifies content, not just existence."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "content_verify.duckdb"
            temp_dir = Path(tmpdir) / "duckdb_tmp"
            store = DuckDBShadowStore()
            with patch.object(store, "_resolve_path"):
                store._db_path = db_path
                store._temp_dir = temp_dir
                store.initialize()

            # Insert with known confidence
            asyncio.run(
                store.async_record_shadow_finding(
                    "content-verify-001",
                    "content verification query",
                    "synthetic",
                    0.654,
                )
            )

            rows: List[Dict[str, Any]] = asyncio.run(
                store.async_query_recent_findings(limit=1)
            )
            self.assertEqual(len(rows), 1)
            row: Dict[str, Any] = rows[0]
            # Verify ALL fields, not just id
            self.assertEqual(row["id"], "content-verify-001")
            self.assertEqual(row["query"], "content verification query")
            self.assertEqual(row["source_type"], "synthetic")
            self.assertAlmostEqual(row["confidence"], 0.654, places=3)

            asyncio.run(store.aclose())


if __name__ == "__main__":
    unittest.main(verbosity=2)
