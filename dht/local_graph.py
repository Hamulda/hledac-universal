import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import orjson
from hledac.universal.security import decrypt_aes_gcm, encrypt_aes_gcm
from hledac.universal.security.key_manager import KeyManager

if TYPE_CHECKING:
    import mlx.core as mx

MAX_NODES_FOR_SCAN = 10_000


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
        plaintext = orjson.dumps(node_data)

        bucket_key, _ = await self.key_manager.get_bucket_key(self.bucket_id)
        encrypted = encrypt_aes_gcm(bucket_key, plaintext, associated_data=node_id.encode())

        def _put():
            with self.env.begin(write=True) as txn:
                txn.put(node_id.encode(), encrypted)
                txn.put(f"neighbors:{node_id}".encode(), orjson.dumps(neighbors[:1000]))

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _put)

        if self.graph is not None:
            # Best-effort: store float32 features
            import mlx.core as mx

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
                            return orjson.loads(data) if data else []

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
                neighbors = orjson.loads(neigh) if neigh else []
                return blob, neighbors

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _get)
        if result is None:
            return None
        blob, neighbors = result

        plaintext = decrypt_aes_gcm(bucket_key, blob, associated_data=node_id.encode())
        node_data = orjson.loads(plaintext)
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
        node_data = orjson.dumps({"host": host, "port": port, "node_id": node_id})
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
                    return orjson.loads(plaintext)

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

    async def close(self) -> None:
        self.env.close()
