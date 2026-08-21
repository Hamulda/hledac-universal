import sys

sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac")

from unittest.mock import MagicMock, patch

from hledac.universal.knowledge import graph_service
from hledac.universal.knowledge.graph_service import GraphService


def _make_fake_graph():
    fake = MagicMock(spec=object)
    fake.upsert_ioc.return_value = True
    fake.upsert_relation.return_value = True
    fake.upsert_identity_edge.return_value = True
    fake.query.return_value = []
    fake.upsert_ioc_batch.return_value = 0
    return fake


gs = GraphService()
gs._seen_iocs.clear()

with patch.object(graph_service, "_get_graph", return_value=_make_fake_graph()):
    r1 = gs.upsert_ioc("1.2.3.4", "ip", 0.9, "test")
    r2 = gs.upsert_ioc("1.2.3.4", "ip", 0.9, "test")
    print(f"r1={r1}, r2={r2}")
    print(f"_RUST_AVAILABLE={graph_service._RUST_IOC_DEDUP_AVAILABLE}")
    print(f"_seen_iocs type={type(gs._seen_iocs)}")
    if hasattr(gs._seen_iocs, "contains"):
        print(f"contains after r1: {gs._seen_iocs.contains('1.2.3.4', 'ip')}")
