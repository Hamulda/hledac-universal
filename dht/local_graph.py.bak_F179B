import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Any

import lmdb
import orjson
import numpy as np
import mlx.core as mx

from hledac.universal.security import encrypt_aes_gcm, decrypt_aes_gcm
from hledac.universal.security.key_manager import KeyManager

MAX_NODES_FOR_SCAN = 10_000


class LocalGraphStore:
    def __init__(self, key_manager: KeyManager, db_path: Optional[str] = None):
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

    async def put_node(self, node_id: str, features: mx.array, neighbors: List[str]) -> None:
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
            self.graph.add_node(node_id, x=mx.array(features, dtype=mx.float32))

    async def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
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
        return {"node_id": node_id, "features": mx.array(arr.astype(np.float32)), "neighbors": neighbors}

    async def get_all_nodes(self, limit: int = MAX_NODES_FOR_SCAN) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []

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

    async def close(self) -> None:
        self.env.close()
