"""Sprint 8RB — STIX export: IP IOC → indicator with ipv4-addr pattern."""
import pytest
pytest.importorskip("kuzu")

import asyncio
import tempfile
import time
from pathlib import Path
import kuzu


def test_stix_export_ip():
    """IOCGraph with IP node → export_stix_bundle() → dict type=indicator, pattern contains ipv4-addr."""
    from hledac.universal.knowledge.ioc_graph import IOCGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        g = IOCGraph(db_path=Path(tmpdir) / "test_ip")
        g._db = kuzu.Database(str(Path(tmpdir) / "test_ip"))
        g._conn = kuzu.Connection(g._db)
        g._init_schema_sync()

        now = time.time()
        g._conn.execute(
            "CREATE (:IOC {id: $id, ioc_type: $t, value: $v, "
            "first_seen: $ts, last_seen: $ts, confidence: $c})",
            {"id": "ip:abc123", "t": "ip", "v": "1.2.3.4", "ts": now, "c": 0.95},
        )

        result = asyncio.run(g.export_stix_bundle())

        assert len(result) == 1, f"Expected 1 object, got {len(result)}"
        obj = result[0]
        assert obj["type"] == "indicator", f"Expected type=indicator, got {obj['type']}"
        assert "ipv4-addr" in obj["pattern"], f"Expected ipv4-addr in pattern, got {obj['pattern']}"
        assert "1.2.3.4" in obj["pattern"], f"Expected 1.2.3.4 in pattern, got {obj['pattern']}"
        g._close_sync()


if __name__ == "__main__":
    test_stix_export_ip()
    print("test_stix_export_ip: PASS")
