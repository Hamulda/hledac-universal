import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from hledac.universal.security import decrypt_aes_gcm, encrypt_aes_gcm
from hledac.universal.security.key_manager import KeyManager
from hledac.universal.utils.msgspec_json import decode, encode

if TYPE_CHECKING:
    import mlx.core as mx

MAX_NODES_FOR_SCAN = 10_000

# B.6: Hard ceiling on the MLX in-memory graph (mlx_graphs backend).
# LMDB persistence is bounded by mmap_size — separate concern. This cap
# protects the hot in-process `self.graph` from unbounded growth in long
# crawls. 1024 nodes × 16-dim float32 = 64 KB features; ~1 MB with
# auxiliary state — fits M1 8GB UMA with headroom for MLX activations.
# FIFO eviction: oldest inserted node is dropped on overflow.
MAX_DHT_GRAPH_NODES: int = 1024


def _evict_oldest_graph_node(graph: Any) -> None:
    """
    Drop the oldest node from the mlx_graphs graph. Best-effort:
    - Uses node_ids[0] if available (FIFO list contract).
    - Silently no-ops on any API mismatch — fail-safe.
    """
    try:
        node_ids = getattr(graph, "node_ids", None)
        if not node_ids:
            return
        oldest = node_ids[0]
        # mlx_graphs API: remove_node or delete_node — try common names
        for method_name in ("remove_node", "delete_node", "pop_node"):
            method = getattr(graph, method_name, None)
            if callable(method):
                method(oldest)
                return
    except Exception:
        pass  # Fail-soft: graph remains at cap; caller retries next insert


class LocalGraphStore:
    def __init__(self, key_manager: KeyManager, db_path: str | None = None):
        from hledac.universal.paths import LMDB_ROOT
        self.key_manager = key_manager
        self.bucket_id = "local_graph"
        if db_path is None:
            self.db_path = LMDB_ROOT / "local_graph.lmdb"
        else:
            self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Sprint 2B: use env-driven map_size via paths helper
        from hledac.universal.paths import open_lmdb
        self.env = open_lmdb(self.db_path.parent, map_size=None)  # env-driven default

        # Optional accel (must not crash if missing)
        try:
            import mlx_graphs as mxg  # noqa
            self._mxg = mxg
            self.graph = mxg.Graph()
        except ImportError:
            self._mxg = None
            self.graph = None

    async def put_node(self, node_id: str, features: mx.array, neighbors: list[str]) -> None:
        arr = np.array(features, dtype=np.float16)
        node_data = {"features": arr.tobytes().hex(), "shape": list(arr.shape)}
        plaintext = encode(node_data)

        bucket_key, _ = await self.key_manager.get_bucket_key(self.bucket_id)
        encrypted = encrypt_aes_gcm(bucket_key, plaintext, associated_data=node_id.encode())

        def _put():
            with self.env.begin(write=True) as txn:
                txn.put(node_id.encode(), encrypted)
                txn.put(f"neighbors:{node_id}".encode(), encode(neighbors[:1000]))

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _put)

        if self.graph is not None:
            # Best-effort: store float32 features
            import mlx.core as mx

            # B.6: cap in-memory graph at MAX_DHT_GRAPH_NODES. Evict FIFO
            # before insert if at cap. LMDB persistence (above) is
            # unbounded by mmap_size and is unaffected by this guard.
            try:
                current_nodes = getattr(self.graph, "node_ids", None)
                if current_nodes is not None and len(current_nodes) >= MAX_DHT_GRAPH_NODES:
                    _evict_oldest_graph_node(self.graph)
            except Exception:
                pass  # Fail-soft: try add_node anyway

            self.graph.add_node(node_id, x=mx.array(features, dtype=mx.float32))

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        # Best-effort accel for features (neighbors still in LMDB)
        if self.graph is not None:
            try:
                if node_id in self.graph.node_ids:
                    feat = self.graph.get_node_features(node_id)

                    def _get_neighbors():
                        with self.env.begin() as txn:
                            data = txn.get(f"neighbors:{node_id}".encode())
                            return decode(data) if data else []

                    loop = asyncio.get_running_loop()
                    neighbors = await loop.run_in_executor(None, _get_neighbors)
                    return {"node_id": node_id, "features": feat, "neighbors": neighbors}
            except Exception:
                pass

        # CRITICAL: bucket_key outside executor
        bucket_key, _ = await self.key_manager.get_bucket_key(self.bucket_id)

        def _get():
            with self.env.begin() as txn:
                blob = txn.get(node_id.encode())
                if blob is None:
                    return None
                neigh = txn.get(f"neighbors:{node_id}".encode())
                neighbors = decode(neigh) if neigh else []
                return blob, neighbors

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _get)
        if result is None:
            return None
        blob, neighbors = result

        plaintext = decrypt_aes_gcm(bucket_key, blob, associated_data=node_id.encode())
        node_data = decode(plaintext)
        arr = np.frombuffer(bytes.fromhex(node_data["features"]), dtype=np.float16).reshape(node_data["shape"])
        import mlx.core as mx

        return {"node_id": node_id, "features": mx.array(arr.astype(np.float32)), "neighbors": neighbors}

    async def get_all_nodes(self, limit: int = MAX_NODES_FOR_SCAN) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []

        def _scan():
            with self.env.begin() as txn:
                cur = txn.cursor()
                for k, _v in cur:
                    if k.startswith(b"neighbors:"):
                        continue
                    out.append({"id": k.decode()})
                    if len(out) >= limit:
                        break

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _scan)
        return out

    # =============================================================================
    # DHT Routing Table Persistence — Sprint F214
    # =============================================================================
    # Stores discovered DHT nodes (peer_id, host, port) in LMDB for cross-run
    # persistence. Nodes are stored encrypted under `dht_node:<node_id>` key.

    async def put_dht_node(self, node_id: str, host: str, port: int) -> None:
        """
        Persist a discovered DHT node to LMDB.

        Args:
            node_id: 40-char hex node ID
            host: IP address string
            port: UDP port number
        """
        node_data = encode({"host": host, "port": port, "node_id": node_id})
        try:
            bucket_key = self.key_manager.get_key_for_bucket(self.bucket_id)
            encrypted = encrypt_aes_gcm(bucket_key, node_data, associated_data=node_id.encode())
            loop = asyncio.get_running_loop()

            def _put():
                with self.env.begin(write=True) as txn:
                    txn.put(f"dht_node:{node_id}".encode(), encrypted)

            await loop.run_in_executor(None, _put)
        except Exception:
            pass  # Fail-soft: DHT persistence never blocks crawl

    async def get_dht_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a DHT node from LMDB by node_id."""
        try:
            bucket_key = self.key_manager.get_key_for_bucket(self.bucket_id)

            def _get():
                with self.env.begin() as txn:
                    blob = txn.get(f"dht_node:{node_id}".encode())
                    if blob is None:
                        return None
                    plaintext = decrypt_aes_gcm(bucket_key, blob, associated_data=node_id.encode())
                    return decode(plaintext)

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _get)
        except Exception:
            return None

    async def get_all_dht_nodes(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Retrieve all persisted DHT nodes (up to limit)."""
        out: list[dict[str, Any]] = []

        def _scan():
            with self.env.begin() as txn:
                cur = txn.cursor()
                for k, _v in cur:
                    if not k.startswith(b"dht_node:"):
                        continue
                    out.append({"id": k.decode().replace("dht_node:", "")})
                    if len(out) >= limit:
                        break

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _scan)
        return out

    async def count_dht_nodes(self) -> int:
        """
        F214Q: Count total persisted DHT nodes in LMDB.

        Returns:
            Total count of DHT nodes stored.
        """
        def _count() -> int:
            count = 0
            with self.env.begin() as txn:
                cur = txn.cursor()
                for k, _v in cur:
                    if k.startswith(b"dht_node:"):
                        count += 1
            return count

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _count)

    async def clear_dht_nodes(self) -> None:
        """Clear all persisted DHT nodes (e.g., on startup)."""
        def _clear():
            with self.env.begin(write=True) as txn:
                cur = txn.cursor()
                for k, _v in cur:
                    if k.startswith(b"dht_node:"):
                        txn.delete(k)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _clear)

    # =============================================================================
    # DHT Routing Table Snapshot — Sprint F214
    # =============================================================================
    # Periodic snapshot of the full routing table (msgpack-encoded list of
    # {node_id, host, port, last_seen} dicts). Stored under a single key
    # ("routing_table_v1") to enable fast whole-routing-table restore on startup
    # without per-node cursor scan.

    async def save_routing_snapshot(self, nodes: list[dict]) -> None:
        """
        Persist full routing table snapshot to LMDB.

        Args:
            nodes: list of {node_id, host, port, last_seen} dicts (capped by
                caller; typically <= 160 * 20 = 3200 entries).
        """
        try:
            payload = encode({"version": 1, "nodes": nodes})
            bucket_key = self.key_manager.get_key_for_bucket(self.bucket_id)
            encrypted = encrypt_aes_gcm(
                bucket_key, payload, associated_data=b"routing_table_v1"
            )
            loop = asyncio.get_running_loop()

            def _put():
                with self.env.begin(write=True) as txn:
                    txn.put(b"routing_table_v1", encrypted)

            await loop.run_in_executor(None, _put)
        except Exception:
            pass  # Fail-soft: snapshot never blocks DHT

    async def load_routing_snapshot(self) -> list[dict]:
        """
        Load persisted routing table snapshot. Returns empty list if missing
        or on any decryption/deserialization error.
        """
        try:
            bucket_key = self.key_manager.get_key_for_bucket(self.bucket_id)

            def _get():
                with self.env.begin() as txn:
                    blob = txn.get(b"routing_table_v1")
                    if blob is None:
                        return None
                    return decrypt_aes_gcm(
                        bucket_key, blob, associated_data=b"routing_table_v1"
                    )

            loop = asyncio.get_running_loop()
            plaintext = await loop.run_in_executor(None, _get)
            if not plaintext:
                return []
            data = decode(plaintext)
            nodes = data.get("nodes", []) if isinstance(data, dict) else []
            return nodes if isinstance(nodes, list) else []
        except Exception:
            return []

    async def close(self) -> None:
        self.env.close()
