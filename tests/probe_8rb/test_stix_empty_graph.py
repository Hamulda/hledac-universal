"""Sprint 8RB — STIX empty graph: no IOCs → export_stix_bundle() → []."""
import pytest
pytest.importorskip("kuzu")

import asyncio
import tempfile
from pathlib import Path
import kuzu


def test_stix_empty_graph():
    """Empty IOCGraph → export_stix_bundle() → [] (no exception)."""
    from hledac.universal.knowledge.ioc_graph import IOCGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        g = IOCGraph(db_path=Path(tmpdir) / "test_empty")
        g._db = kuzu.Database(str(Path(tmpdir) / "test_empty"))
        g._conn = kuzu.Connection(g._db)
        g._init_schema_sync()

        result = asyncio.run(g.export_stix_bundle())

        assert result == [], f"Expected [], got {result}"
        g._close_sync()


if __name__ == "__main__":
    test_stix_empty_graph()
    print("test_stix_empty_graph: PASS")
