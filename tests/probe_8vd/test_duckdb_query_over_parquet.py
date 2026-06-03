"""Test DuckDB query over Parquet files."""
import pathlib
import tempfile
import sys

import pytest

_universal = pathlib.Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_universal))

pytest.importorskip("pyarrow")
pytest.importorskip("duckdb")

import pyarrow as pa
import pyarrow.parquet as pq
import duckdb


def test_duckdb_parquet_query():
    """DuckDB can query parquet with correct ordering."""
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "probe.parquet"
        t = pa.table({
            "ioc": ["1.2.3.4", "evil.com", "3.3.3.3"],
            "confidence": pa.array([0.9, 0.7, 0.85], type=pa.float32()),
        })
        pq.write_table(t, path, compression="snappy")

        con = duckdb.connect()
        rows = con.execute(
            f"SELECT ioc, confidence FROM read_parquet('{path}') ORDER BY confidence DESC"
        ).fetchall()
        con.close()

        assert rows[0][0] == "1.2.3.4", f"Expected 1.2.3.4 first, got {rows[0][0]}"
        assert rows[1][0] == "3.3.3.3", f"Expected 3.3.3.3 second, got {rows[1][0]}"
        assert rows[2][0] == "evil.com", f"Expected evil.com third, got {rows[2][0]}"


if __name__ == "__main__":
    test_duckdb_parquet_query()
    print("test_duckdb_query_over_parquet: PASSED")
