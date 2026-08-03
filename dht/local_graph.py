"""
Local DHT graph store — ISS UE-004: asyncio.to_thread elimination via Rust LMDB backend.

Replace 10+ asyncio.to_thread() hops with single Rust→Python lmdb calls.
BFS traversal: 5-10 Python hops → 1 Rust call.

Rust backend: rust_extensions/src/lmdb_dht.rs
Python fallback: original asyncio.to_thread() path (always-on, fail-safe).
"""
from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from hledac.universal.security import decrypt_aes_gcm, encrypt_aes_gcm
from hledac.universal.security.key_manager import KeyManager
from hledac.universal.utils.lmdb_bulk import putmulti_bounded
from hledac.universal.utils.msgspec_json import decode, encode

if TYPE_CHECKING:
    import mlx.core as mx

# Lazy import — Rust module loaded on first use, not at module load time.
# This keeps the M1 RAM budget intact during startup.
_lmdb_dht: Any | None = None
# P2-1: lazy lmdb_pool import to avoid circular dependency at module load.
_lmdb_pool: Any | None = None


def _get_lmdb_dht() -> Any:
    """Lazy-load the Rust LMDB DHT backend."""
    global _lmdb_dht
    if _lmdb_dht is None:
        try:
            # R6: Centralized Rust access via core.rust_backend
            from hledac.universal.core.rust_backend import rust
            ext = rust.raw.module

            _lmdb_dht = ext
        except ImportError:
            _lmdb_dht = None
    return _lmdb_dht


def _get_lmdb_pool() -> Any:
    """Lazy-load LmdbPool to avoid circular import at module load."""
    global _lmdb_pool
    if _lmdb_pool is None:
        from hledac.universal.runtime.lmdb_pool import get_lmdb_pool
        _lmdb_pool = get_lmdb_pool()
    return _lmdb_pool


MAX_NODES_FOR_SCAN = 10000
MAX_DHT_GRAPH_NODES: int = 1024


def _scan_lmdb_by_prefix(
    env,
    prefix: bytes,
    limit: int,
    *,
    include_prefix: bool = True,
) -> list[bytes]:
    """
    Parameterized LMDB prefix scan — replaces 4 Type-2 renamed clones.

    Common pattern:
        with env.begin() as txn:
            cur = txn.cursor()
            for k, _v in cur:
                if k.startswith(prefix):
                    out.append(decode_prefix(k))
                    if len(out) >= limit:
                        break

    Args:
        env: LMDB environment
        prefix: Byte prefix to filter keys (e.g. b"dht_node:")
        limit: Maximum number of results to return
        include_prefix: If True, strips prefix from decoded keys

    Returns:
        List of decoded key bytes (prefix stripped)
    """
    out: list[bytes] = []

    def _scan():
        with env.begin() as txn:
            cur = txn.cursor()
            for k, _v in cur:
                if k.startswith(prefix):
                    key = k[len(prefix):] if not include_prefix else k
                    out.append(key)
                    if len(out) >= limit:
                        break

    _scan()
    return out


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
        for method_name in ("remove_node", "delete_node", "pop_node"):
            method = getattr(graph, method_name, None)
            if callable(method):
                method(oldest)
                return
    except Exception:
        pass


def _use_rust_lmdb() -> bool:
    """Check if Rust LMDB backend is available and healthy."""
    mod = _get_lmdb_dht()
    return mod is not None and hasattr(mod, "lmdb_dht_put_node")


class LocalGraphStore:
    """
    DHT LocalGraphStore s dual backend:
    - Rust LMDB (preferovaný): elimnuje asyncio.to_thread overhead
    - Python asyncio.to_thread (fallback): vždy funkční
    """

    __slots__ = tuple(
        ("_mxg", "bucket_id", "db_path", "env", "graph", "key_manager")
    )

    def __init__(self, key_manager: KeyManager, db_path: str | None = None):
        from hledac.universal.paths import LMDB_ROOT

        self.key_manager = key_manager
        self.bucket_id = "local_graph"
        if db_path is None:
            self.db_path = LMDB_ROOT / "local_graph.lmdb"
        else:
            self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        from hledac.universal.paths import open_lmdb

        self.env = open_lmdb(self.db_path.parent, map_size=None)
        try:
            import mlx_graphs as mxg

            self._mxg = mxg
            self.graph = mxg.Graph()
        except ImportError:
            self._mxg = None
            self.graph = None

    # ─────────────────────────────────────────────────────────────────────
    # ISSUE-004: Rust LMDB backend (primary path)
    # ─────────────────────────────────────────────────────────────────────

    async def put_node(
        self, node_id: str, features: mx.array, neighbors: list[str]
    ) -> None:
        arr = np.array(features, dtype=np.float16)
        node_data = {"features": arr.tobytes().hex(), "shape": list(arr.shape)}
        plaintext = encode(node_data)
        bucket_key, _ = await self.key_manager.get_bucket_key(self.bucket_id)
        encrypted = encrypt_aes_gcm(
            bucket_key, plaintext, associated_data=node_id.encode()
        )
        neighbors_json = encode(neighbors[:1000])

        # Rust LMDB path: single Python→Rust call, no asyncio.to_thread
        if _use_rust_lmdb():
            path_str = str(self.db_path.parent)
            _get_lmdb_dht().lmdb_dht_put_node(
                path_str,
                node_id.encode(),
                encrypted,
                neighbors_json,
            )
        else:
            # P2-1 Fallback: dedicated LMDB pool (not default asyncio executor)
            def _put():
                putmulti_bounded(
                    self.env,
                    [
                        (node_id.encode(), encrypted),
                        (
                            f"neighbors:{node_id}".encode(),
                            neighbors_json,
                        ),
                    ],
                    overwrite=True,
                )

            await _get_lmdb_pool().run_lmdb(_put)

        if self.graph is not None:
            import mlx.core as mx

            try:
                current_nodes = getattr(self.graph, "node_ids", None)
                if (
                    current_nodes is not None
                    and len(current_nodes) >= MAX_DHT_GRAPH_NODES
                ):
                    _evict_oldest_graph_node(self.graph)
            except Exception:
                pass
            self.graph.add_node(node_id, x=mx.array(features, dtype=mx.float32))

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        # Rust LMDB path: single call
        if _use_rust_lmdb():
            path_str = str(self.db_path.parent)
            result = _get_lmdb_dht().lmdb_dht_get_node(
                path_str, node_id.encode()
            )
            if result is None:
                return None
            blob, neigh_data = result
            neighbors = decode(neigh_data) if neigh_data else []
            bucket_key, _ = await self.key_manager.get_bucket_key(
                self.bucket_id
            )
            plaintext = decrypt_aes_gcm(
                bucket_key, blob, associated_data=node_id.encode()
            )
            node_data = decode(plaintext)
            arr = np.frombuffer(
                bytes.fromhex(node_data["features"]), dtype=np.float16
            ).reshape(node_data["shape"])
            import mlx.core as mx

            return {
                "node_id": node_id,
                "features": mx.array(arr.astype(np.float32)),
                "neighbors": neighbors,
            }

        # Fallback: Python asyncio.to_thread
        if self.graph is not None:
            try:
                if node_id in self.graph.node_ids:
                    feat = self.graph.get_node_features(node_id)

                    def _get_neighbors():
                        with self.env.begin() as txn:
                            data = txn.get(f"neighbors:{node_id}".encode())
                            return decode(data) if data else []

                    neighbors = await _get_lmdb_pool().run_lmdb(_get_neighbors)
                    return {
                        "node_id": node_id,
                        "features": feat,
                        "neighbors": neighbors,
                    }
            except Exception:
                pass
        bucket_key, _ = await self.key_manager.get_bucket_key(self.bucket_id)

        def _get():
            with self.env.begin() as txn:
                blob = txn.get(node_id.encode())
                if blob is None:
                    return None
                neigh = txn.get(f"neighbors:{node_id}".encode())
                neighbors = decode(neigh) if neigh else []
                return (blob, neighbors)

        result = await _get_lmdb_pool().run_lmdb(_get)
        if result is None:
            return None
        blob, neighbors = result
        plaintext = decrypt_aes_gcm(
            bucket_key, blob, associated_data=node_id.encode()
        )
        node_data = decode(plaintext)
        arr = np.frombuffer(
            bytes.fromhex(node_data["features"]), dtype=np.float16
        ).reshape(node_data["shape"])
        import mlx.core as mx

        return {
            "node_id": node_id,
            "features": mx.array(arr.astype(np.float32)),
            "neighbors": neighbors,
        }

    async def get_all_nodes(
        self, limit: int = MAX_NODES_FOR_SCAN
    ) -> list[dict[str, str]]:
        """
        ISSUE-004 optimization: Rust BFS traversal for node scan.
        Replaces asyncio.to_thread() scan with single Rust call.
        """
        if _use_rust_lmdb():
            path_str = str(self.db_path.parent)
            # Use Rust full scan (excludes "neighbors:" prefix)
            all_keys = _get_lmdb_dht().lmdb_dht_scan_all_nodes(path_str, limit)
            return [{"id": k.decode(errors="replace")} for k in all_keys[:limit]]

        # Fallback: asyncio.to_thread scan
        keys = await _get_lmdb_pool().run_lmdb(
            lambda: _scan_lmdb_by_prefix(self.env, b"neighbors:", limit, include_prefix=False)
        )
        return [{"id": k.decode(errors="replace")} for k in keys]

    async def put_dht_node(
        self, node_id: str, host: str, port: int
    ) -> None:
        """
        Persist a discovered DHT node to LMDB.

        Args:
            node_id: 40-char hex node ID
            host: IP address string
            port: UDP port number
        """
        node_data = encode(
            {"host": host, "port": port, "node_id": node_id}
        )
        try:
            bucket_key = self.key_manager.get_key_for_bucket(self.bucket_id)
            encrypted = encrypt_aes_gcm(
                bucket_key, node_data, associated_data=node_id.encode()
            )

            if _use_rust_lmdb():
                path_str = str(self.db_path.parent)
                _get_lmdb_dht().lmdb_dht_put_dht_node(
                    path_str, node_id.encode(), encrypted
                )
            else:
                def _put():
                    with self.env.begin(write=True) as txn:
                        txn.put(f"dht_node:{node_id}".encode(), encrypted)

                await _get_lmdb_pool().run_lmdb(_put)
        except Exception:
            pass

    async def get_dht_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a DHT node from LMDB by node_id."""
        try:
            bucket_key = self.key_manager.get_key_for_bucket(self.bucket_id)

            if _use_rust_lmdb():
                path_str = str(self.db_path.parent)
                blob = _get_lmdb_dht().lmdb_dht_get_dht_node(
                    path_str, node_id.encode()
                )
                if blob is None:
                    return None
                plaintext = decrypt_aes_gcm(
                    bucket_key, blob, associated_data=node_id.encode()
                )
                return decode(plaintext)

            def _get():
                with self.env.begin() as txn:
                    blob = txn.get(f"dht_node:{node_id}".encode())
                    if blob is None:
                        return None
                    plaintext = decrypt_aes_gcm(
                        bucket_key, blob, associated_data=node_id.encode()
                    )
                    return decode(plaintext)

            return await _get_lmdb_pool().run_lmdb(_get)
        except Exception:
            return None

    async def get_all_dht_nodes(
        self, limit: int = 1000
    ) -> list[dict[str, Any]]:
        """Retrieve all persisted DHT nodes (up to limit)."""
        if _use_rust_lmdb():
            path_str = str(self.db_path.parent)
            results = _get_lmdb_dht().lmdb_dht_get_all_dht_nodes(
                path_str, limit
            )
            return [{"id": k.decode()} for k, _ in results]

        # Fallback: asyncio.to_thread scan
        keys = await _get_lmdb_pool().run_lmdb(
            lambda: _scan_lmdb_by_prefix(self.env, b"dht_node:", limit, include_prefix=True)
        )
        return [{"id": k.decode().replace("dht_node:", "")} for k in keys]

    async def count_dht_nodes(self) -> int:
        """
        F214Q: Count total persisted DHT nodes in LMDB.

        Returns:
            Total count of DHT nodes stored.
        """
        if _use_rust_lmdb():
            path_str = str(self.db_path.parent)
            return _get_lmdb_dht().lmdb_dht_count_dht_nodes(path_str)

        keys = await _get_lmdb_pool().run_lmdb(
            lambda: _scan_lmdb_by_prefix(self.env, b"dht_node:", 1_000_000, include_prefix=True)
        )
        return len(keys)

    async def clear_dht_nodes(self) -> None:
        """Clear all persisted DHT nodes (e.g., on startup)."""
        if _use_rust_lmdb():
            path_str = str(self.db_path.parent)
            _get_lmdb_dht().lmdb_dht_clear_dht_nodes(path_str)
            return

        def _clear():
            with self.env.begin(write=True) as txn:
                cur = txn.cursor()
                for k, _v in cur:
                    if k.startswith(b"dht_node:"):
                        txn.delete(k)

        await _get_lmdb_pool().run_lmdb(_clear)

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

            if _use_rust_lmdb():
                path_str = str(self.db_path.parent)
                _get_lmdb_dht().lmdb_dht_save_routing_snapshot(
                    path_str, encrypted
                )
            else:
                def _put():
                    with self.env.begin(write=True) as txn:
                        txn.put(b"routing_table_v1", encrypted)

                await _get_lmdb_pool().run_lmdb(_put)
        except Exception:
            pass

    async def load_routing_snapshot(self) -> list[dict]:
        """
        Load persisted routing table snapshot. Returns empty list if missing
        or on any decryption/deserialization error.
        """
        try:
            bucket_key = self.key_manager.get_key_for_bucket(self.bucket_id)

            if _use_rust_lmdb():
                path_str = str(self.db_path.parent)
                blob = _get_lmdb_dht().lmdb_dht_load_routing_snapshot(
                    path_str
                )
                if not blob:
                    return []
                plaintext = decrypt_aes_gcm(
                    bucket_key, blob, associated_data=b"routing_table_v1"
                )
                data = decode(plaintext)
                nodes = data.get("nodes", []) if isinstance(data, dict) else []
                return nodes if isinstance(nodes, list) else []
            else:
                def _get():
                    with self.env.begin() as txn:
                        blob = txn.get(b"routing_table_v1")
                        if blob is None:
                            return None
                        return decrypt_aes_gcm(
                            bucket_key, blob, associated_data=b"routing_table_v1"
                        )

                plaintext = await _get_lmdb_pool().run_lmdb(_get)
                if not plaintext:
                    return []
                data = decode(plaintext)
                nodes = data.get("nodes", []) if isinstance(data, dict) else []
                return nodes if isinstance(nodes, list) else []
        except Exception:
            return []

    async def close(self) -> None:
        self.env.close()
