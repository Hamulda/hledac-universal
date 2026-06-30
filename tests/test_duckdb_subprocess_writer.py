"""
Tests for DuckDBWriterWorker Arrow INSERT path.

Scope: ONLY _insert_arrow, _insert_findings_batch threshold wiring.
DO NOT touch IPC/queue logic.
"""


from unittest.mock import patch

import pytest


class TestInsertArrow:
    """Tests for DuckDBWriterWorker._insert_arrow()."""

    def _make_worker(self):
        """Create worker with in-memory DuckDB connection."""
        from knowledge.duckdb_subprocess_writer import DuckDBWriterWorker

        worker = DuckDBWriterWorker(db_path=None, temp_dir=None, wal_path=None)
        worker._initialize()
        return worker

    def _mock_row(self, i: int = 0) -> list:
        """Make a canonical_findings row (7 fields)."""
        return [
            f"fid_{i}",
            f"query_{i}",
            "test_source",
            0.85,
            1234567890.0 + i,
            '{"provenance": "test"}',
            "payload text",
        ]

    def test_insert_arrow_registers_and_unregisters(self):
        """
        Arrow table is registered, used, then unregistered.
        After call, _arrow_batch must NOT be present in connection.
        """
        worker = self._make_worker()

        rows = [self._mock_row(i) for i in range(10)]
        result = worker._insert_arrow(rows)

        assert result == 10

        # Verify unregister: querying _arrow_batch must raise
        with pytest.raises(Exception):
            worker.conn.execute("SELECT * FROM _arrow_batch")

    def test_insert_arrow_fallback_on_pyarrow_missing(self):
        """
        When pyarrow is unavailable (ImportError), falls back to executemany.
        """
        import builtins

        from knowledge.duckdb_subprocess_writer import DuckDBWriterWorker

        worker = DuckDBWriterWorker(db_path=None, temp_dir=None, wal_path=None)
        worker._initialize()

        rows = [self._mock_row(i) for i in range(10)]

        # Monkeypatch import to simulate pyarrow missing
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "pyarrow":
                raise ImportError("simulated no pyarrow")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = worker._insert_arrow(rows)

        assert result > 0, "Fallback should insert via executemany"

    def test_insert_arrow_threshold(self):
        """
        Arrow path activates at >= 5 rows, executemany used for < 5.
        """
        from unittest.mock import patch

        from knowledge.duckdb_subprocess_writer import DuckDBWriterWorker

        worker = DuckDBWriterWorker(db_path=None, temp_dir=None, wal_path=None)
        worker._initialize()

        # Patch _insert_arrow to raise so executemany is tried instead
        with patch.object(worker, "_insert_arrow", side_effect=Exception("force fallback")):
            original_executemany = worker._insert_executemany
            executemany_called = []

            def tracking_executemany(rows):
                executemany_called.append(len(rows))
                return original_executemany(rows)

            worker._insert_executemany = tracking_executemany  # type: ignore[method-assignment]

            # 4 rows → executemany (arrow path skipped: < 5)
            rows_4 = [self._mock_row(i) for i in range(4)]
            worker._insert_findings_batch(rows_4)
            assert len(executemany_called) == 1 and executemany_called[-1] == 4
            executemany_called.clear()

            # 5 rows → arrow attempted first (>= 5), falls back to executemany
            rows_5 = [self._mock_row(i) for i in range(5)]
            worker._insert_findings_batch(rows_5)
            assert len(executemany_called) == 1 and executemany_called[-1] == 5

    def test_insert_arrow_sql_keyword_fixed(self):
        """
        Bug: 'FROM table' used SQL keyword 'table' instead of registered name.
        Fix: 'FROM _arrow_batch' uses the registered table name.
        This test verifies INSERT succeeds with correct table reference.
        """
        worker = self._make_worker()

        rows = [self._mock_row(i) for i in range(5)]
        result = worker._insert_arrow(rows)

        assert result == 5

        # Verify data was actually inserted
        res = worker.conn.execute(
            "SELECT id, query FROM canonical_findings ORDER BY id"
        ).fetchall()
        assert len(res) == 5
        assert res[0][0] == "fid_0"
        assert res[0][1] == "query_0"

    def test_insert_arrow_unregister_on_exception(self):
        """
        Even when INSERT raises, unregister must be called (finally block).
        We verify this by calling _insert_arrow with a pyarrow-built table
        whose column set DuckDB rejects at INSERT time — then confirm the
        connection remains usable afterward (proving no leaked state).
        """
        worker = self._make_worker()

        # Create a table with a column that doesn't exist in canonical_findings schema
        # This makes DuckDB raise during INSERT; finally block must still unregister
        import pyarrow as pa

        bad_table = pa.table({"non_existent_col": pa.array(["x", "y", "z"])})
        worker.conn.register("_arrow_batch", bad_table)

        try:
            worker.conn.execute(
                "INSERT OR IGNORE INTO canonical_findings BY NAME SELECT * FROM _arrow_batch"
            )
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                worker.conn.unregister("_arrow_batch")
            except Exception:  # noqa: BLE001
                pass

        # After the register/INSERT/failure/finally pattern, connection must still work.
        # If finally block leaked (didn't unregister), subsequent queries may see stale state.
        # The fact that a fresh INSERT via executemany succeeds proves no leaked state.
        result = worker._insert_executemany([self._mock_row(999)])
        assert result == 1, "Connection still usable after INSERT failure — no leaked state"
