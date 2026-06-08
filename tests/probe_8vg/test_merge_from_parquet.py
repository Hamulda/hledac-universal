import pytest

# Skip the entire module when the optional `pyarrow` dependency is not
# installed.  `pyarrow` is intentionally a non-default dependency
# (it adds ~40 MB to the install footprint) and is only needed for
# the parquet ingestion probe.
pytest.importorskip("pyarrow", reason="pyarrow optional dep not installed")

import pyarrow as pa
import pyarrow.parquet as pq
import pathlib
import tempfile
from graph.quantum_pathfinder import DuckPGQGraph

def test_merge_from_parquet():
    with tempfile.TemporaryDirectory() as tmp:
        pq_path = pathlib.Path(tmp) / "batch_test.parquet"
        t = pa.table({
            "ioc":        ["1.2.3.4", "evil.com", None],
            "ioc_type":   ["ipv4", "domain", "ip"],
            "confidence": pa.array([0.9, 0.8, 0.5], pa.float32()),
            "source":     ["abuse_ch", "crtsh", "test"],
        })
        pq.write_table(t, pq_path)
        db = str(pathlib.Path(tmp) / "ioc.duckdb")
        g  = DuckPGQGraph(db_path=db)
        count = g.merge_from_parquet(str(pathlib.Path(tmp) / "*.parquet"))
        assert count >= 2  # None row preskocene
