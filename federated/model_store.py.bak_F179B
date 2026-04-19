"""
LMDB-backed model store pro federated learning.
"""

import asyncio
import orjson
from pathlib import Path
from typing import Dict, Optional, Any
import numpy as np
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Max payload size: 256KB
MAX_PAYLOAD_BYTES = 256 * 1024


def _try_encrypt(plaintext: bytes, bucket_key: bytes, associated_data: bytes) -> bytes:
    """Try to encrypt with AES-GCM. Returns plaintext if encryption unavailable."""
    try:
        from hledac.universal.security import encrypt_aes_gcm
        return encrypt_aes_gcm(bucket_key, plaintext, associated_data=associated_data)
    except ImportError:
        logger.debug("Encryption unavailable, storing plaintext")
        return plaintext


def _try_decrypt(data: bytes, bucket_key: bytes, associated_data: bytes) -> bytes:
    """Try to decrypt with AES-GCM. Returns data as-is if decryption unavailable."""
    try:
        from hledac.universal.security import decrypt_aes_gcm
        return decrypt_aes_gcm(bucket_key, data, associated_data=associated_data)
    except ImportError:
        logger.debug("Decryption unavailable, reading plaintext")
        return data


class ModelStore:
    """LMDB-backed store pro modely a klíče."""

    def __init__(self, path: Optional[str] = None):
        from hledac.universal.paths import DB_ROOT
        if path is None:
            self.path = DB_ROOT / "federated_models"
        else:
            self.path = Path(path).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)
        # Sprint 2B: use env-driven map_size via paths helper
        from hledac.universal.paths import open_lmdb
        self.env = open_lmdb(self.path, map_size=1024 * 1024 * 100)  # 100MB
        self._executor = ThreadPoolExecutor(max_workers=2)

    def put_model(self, round_num: int, weights: Dict[str, np.ndarray]):
        """Uloží modelové váhy."""
        key = f"model:{round_num}".encode()
        # Serializace vah jako hex stringy (orjson neumí bytes)
        data = {k: v.tobytes().hex() for k, v in weights.items()}
        shapes = {k: list(v.shape) for k, v in weights.items()}
        payload = {
            'weights': data,
            'shapes': shapes
        }
        # Bounded payload check
        serialized = orjson.dumps(payload)
        if len(serialized) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"Payload too large: {len(serialized)} > {MAX_PAYLOAD_BYTES}")
        with self.env.begin(write=True) as txn:
            txn.put(key, serialized)

    def get_model(self, round_num: int) -> Optional[Dict[str, np.ndarray]]:
        """Načte modelové váhy."""
        key = f"model:{round_num}".encode()
        with self.env.begin() as txn:
            data = txn.get(key)
        if not data:
            return None

        payload = orjson.loads(data)
        weights = {}
        for k, v in payload['weights'].items():
            shape = tuple(payload['shapes'][k])
            weights[k] = np.frombuffer(bytes.fromhex(v), dtype=np.float32).reshape(shape)
        return weights

    def put_trusted_key(self, node_id: str, public_key: bytes):
        """Uloží důvěryhodný veřejný klíč."""
        key = f"trusted_key:{node_id}".encode()
        with self.env.begin(write=True) as txn:
            txn.put(key, public_key)

    def get_trusted_key(self, node_id: str) -> Optional[bytes]:
        """Načte důvěryhodný veřejný klíč."""
        key = f"trusted_key:{node_id}".encode()
        with self.env.begin() as txn:
            return txn.get(key)

    # Async wrappers - additive API from model_store_v2.bak
    async def async_save_model(self, key: str, weights: Dict[str, np.ndarray],
                               encrypted: bool = False, bucket_key: Optional[bytes] = None):
        """
        Async wrapper for save_model.

        Args:
            key: Storage key
            weights: Model weights dict
            encrypted: If True, encrypt with AES-GCM (auto-fallback to plaintext if unavailable)
            bucket_key: Encryption key (required if encrypted=True)
        """
        # Serialize weights
        serializable = {}
        for k, v in weights.items():
            arr = np.array(v, dtype=np.float16)
            serializable[k] = {
                'shape': list(arr.shape),
                'data': arr.tobytes().hex()
            }
        plaintext = orjson.dumps(serializable)

        # Encrypt if requested (auto-fallback to plaintext if encryption unavailable)
        if encrypted and bucket_key:
            plaintext = _try_encrypt(plaintext, bucket_key, key.encode())

        # Store via executor
        def _put():
            with self.env.begin(write=True) as txn:
                txn.put(key.encode(), plaintext)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, _put)

    async def async_load_model(self, key: str, encrypted: bool = False,
                               bucket_key: Optional[bytes] = None) -> Optional[Dict[str, np.ndarray]]:
        """
        Async wrapper for load_model.

        Args:
            key: Storage key
            encrypted: If True, decrypt with AES-GCM (auto-fallback to plaintext if unavailable)
            bucket_key: Decryption key (required if encrypted=True)

        Returns:
            Model weights dict or None if not found
        """
        def _get():
            with self.env.begin() as txn:
                return txn.get(key.encode())

        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(self._executor, _get)

        if data is None:
            return None

        # Decrypt if requested (auto-fallback to plaintext if decryption unavailable)
        if encrypted and bucket_key:
            data = _try_decrypt(data, bucket_key, key.encode())

        loaded = orjson.loads(data)
        result = {}
        for k, v in loaded.items():
            shape = tuple(v['shape'])
            arr = np.frombuffer(bytes.fromhex(v['data']), dtype=np.float16).reshape(shape)
            result[k] = arr.astype(np.float32)
        return result

    def close(self):
        """Zavře databázi."""
        self._executor.shutdown(wait=False)
        self.env.close()
