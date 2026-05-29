from unittest.mock import AsyncMock, MagicMock

import pytest
from hledac.universal.core.resource_governor import ResourceGovernor
from hledac.universal.dht.kademlia_node import KademliaNode
from hledac.universal.dht.local_graph import LocalGraphStore
from hledac.universal.dht.sketch_exchange import SketchExchange


class DummyTransport:
    def __init__(self):
        self.handlers = {}

    def register_handler(self, msg_type, handler):
        self.handlers[msg_type] = handler

    async def send_message(self, target, msg_type, payload, signature, msg_id=None):
        return "OK"


@pytest.fixture
def mock_governor():
    g = MagicMock(spec=ResourceGovernor)
    cm = AsyncMock()
    cm.__aenter__.return_value = None
    cm.__aexit__.return_value = None
    g.reserve.return_value = cm
    return g


@pytest.mark.asyncio
async def test_sketch_similarity_positive_same_graph(tmp_path, mock_governor):
    # LocalGraphStore uses real LMDB; keep it in tmp
    from hledac.universal.security.key_manager import KeyManager
    km = KeyManager(db_path=str(tmp_path / "keys.lmdb"))
    await km.get_master_key()

    lg = LocalGraphStore(km, db_path=str(tmp_path / "graph.lmdb"))
    # put a couple nodes
    import mlx.core as mx
    await lg.put_node("A", mx.random.normal(shape=(8,)), ["B"])
    await lg.put_node("B", mx.random.normal(shape=(8,)), ["A"])

    dht = KademliaNode("node1", mock_governor, bootstrap_nodes=[])
    dht.set_transport(DummyTransport())
    # publish local sketch manually (simulate)
    se = SketchExchange(mock_governor, "node1", dht, lg)
    await se._refresh_digests()
    await dht.store("sketch:node1", {"digests": se._digests, "ts": 0, "v": 1})

    # query should find itself with similarity 1.0 (>= >0)
    res = await se.query_entity("A", min_jaccard=0.01)
    assert res, "Expected at least one match"
    assert res[0]["similarity"] > 0.0
