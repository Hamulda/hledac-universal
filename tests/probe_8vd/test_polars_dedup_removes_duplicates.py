"""Test Polars dedup removes duplicate URLs."""
import pathlib
import tempfile
import sys

import pytest

_universal = pathlib.Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_universal))

pl = pytest.importorskip("polars")
pytest.importorskip("pyarrow")
import pyarrow as pa
import pyarrow.parquet as pq


def test_polars_dedup():
    """Duplicate URLs are deduplicated by Polars group_by."""
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "dedup.parquet"
        rows = [
            {"url": "http://x.com", "ioc": None, "title": "T",
             "source": "s", "confidence": 0.8, "hit_count": 1},
            {"url": "http://x.com", "ioc": None, "title": "T",
             "source": "s", "confidence": 0.8, "hit_count": 1},
            {"url": "http://y.com", "ioc": None, "title": "U",
             "source": "s", "confidence": 0.6, "hit_count": 1},
        ]
        pq.write_table(pa.Table.from_pylist(rows), path)

        df = (
            pl.scan_parquet(str(path))
            .group_by(["url", "ioc"])
            .agg(pl.col("title").first(), pl.len().alias("hit_count"))
            .collect(engine="streaming")
        )

        assert len(df) == 2, f"Expected 2 rows after dedup, got {len(df)}"


if __name__ == "__main__":
    test_polars_dedup()
    print("test_polars_dedup_removes_duplicates: PASSED")
