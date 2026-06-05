"""Sprint 8RB — STIX export: CVE IOC → Vulnerability with external_id."""
import pytest
pytest.importorskip("kuzu")

import asyncio
import tempfile
import time
from pathlib import Path
import kuzu


def test_stix_export_cve():
    """IOCGraph with CVE node → export_stix_bundle() → dict type=vulnerability, external_id."""
    from hledac.universal.knowledge.ioc_graph import IOCGraph

    with tempfile.TemporaryDirectory() as tmpdir:
        g = IOCGraph(db_path=Path(tmpdir) / "test_cve")
        g._db = kuzu.Database(str(Path(tmpdir) / "test_cve"))
        g._conn = kuzu.Connection(g._db)
        g._init_schema_sync()

        now = time.time()
        g._conn.execute(
            "CREATE (:IOC {id: $id, ioc_type: $t, value: $v, "
            "first_seen: $ts, last_seen: $ts, confidence: $c})",
            {"id": "cve:xyz789", "t": "cve", "v": "CVE-2026-1234", "ts": now, "c": 0.9},
        )

        result = asyncio.run(g.export_stix_bundle())

        assert len(result) == 1, f"Expected 1 object, got {len(result)}"
        obj = result[0]
        assert obj["type"] == "vulnerability", f"Expected type=vulnerability, got {obj['type']}"
        refs = obj.get("external_references", [])
        assert any(ref.get("external_id") == "CVE-2026-1234" for ref in refs), \
            f"Expected CVE-2026-1234 in external_references, got {refs}"
        g._close_sync()


if __name__ == "__main__":
    test_stix_export_cve()
    print("test_stix_export_cve: PASS")
