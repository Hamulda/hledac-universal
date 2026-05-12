"""
LMDB-backed model store pro federated learning.

PROMOTION GATE — DONOR ONLY / DORMANT
======================================
Tento modul je určen POUZE jako datová vrstva (donor) pro federated learning,
ALE samotný federated learning workflow v tomto projektu NENÍ IMPLEMENTOVÁN.

STATUS: DORMANT — není aktivní federated learning koordinace
M1 8GB MEMORY CEILING: MAX_PAYLOAD_BYTES = 256KB per model write
  - 100MB LMDB map_size
  - 2x ThreadPoolExecutor workers (LAZY INIT — F184F fix)
  - Model váhy se serializují přes numpy hex → float16 → float32 roundtrip
  - LMDB env OTEVŘEN LAZY — F184F fix (původně v __init__)

CONTAINMENT HARDENING (F184F):
  - LMDB env: lazy open — otevřen při prvním put/get, ne při __init__
  - ThreadPoolExecutor: lazy init — vytvořen při prvním async operaci
  - close() je IDEMPOTENT — _closed flag zamezuje dvojímu zavření

ALLOWED PURPOSE: LMDB persistence layer pro model checkpointing (donor only)
PROMOTION ELIGIBILITY: NO — žádný FL coordinator neexistuje, rl/marl_coordinator.py je DORMANT

CALL-SITE AUDIT: 0 skutečných volání async_save_model / async_load_model
  v projektu mimo testy. Model federation je paper-compliant storage bez FL algoritmu.

SECURITY: _try_encrypt / _try_decrypt s AES-GCM fallback na plaintext —
  toto je záměrné (security je out-of-scope pro donor-only store)

AUTHORITY: Tento soubor NEOPRAVŇUJE žádné federated learning operace.
Je to čistě LMDB wrapper kolem numpy serializace.
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
    """
    LMDB-backed store pro modely a klíče.

    F184F: LMDB env a ThreadPoolExecutor jsou LAZY INICIALIZOVÁNY.
    Žádné otevření DB při importu — pouze při prvním skutečném put/get.
    close() je IDEMPOTENT — lze volat vícekrát bez chyby.
    """

    def __init__(self, path: Optional[str] = None):
        from hledac.universal.paths import DB_ROOT
        if path is None:
            self.path = DB_ROOT / "federated_models"
        else:
            self.path = Path(path).expanduser()
        self.path.mkdir(parents=True, exist_ok=True)
        # F184F: lazy init flags — env a executor se otevřou při prvním použití
        self._env = None
        self._executor = None
        self._closed = False  # F184F: idempotent close guard

    def _ensure_env(self):
        """Lazy LMDB open — voláno při prvním skutečném přístupu k datům."""
        if self._closed:
            raise RuntimeError("ModelStore: already closed")
        if self._env is None:
            from hledac.universal.paths import open_lmdb
            self._env = open_lmdb(self.path, map_size=1024 * 1024 * 100)  # 100MB
        return self._env

    def _ensure_executor(self):
        """Lazy ThreadPoolExecutor init — voláno při prvním async operaci."""
        if self._closed:
            raise RuntimeError("ModelStore: already closed")
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=2)
        return self._executor

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
        # F184F: lazy env open
        with self._ensure_env().begin(write=True) as txn:
            txn.put(key, serialized)

    def get_model(self, round_num: int) -> Optional[Dict[str, np.ndarray]]:
        """Načte modelové váhy."""
        key = f"model:{round_num}".encode()
        # F184F: lazy env open
        with self._ensure_env().begin() as txn:
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
        # F184F: lazy env open
        with self._ensure_env().begin(write=True) as txn:
            txn.put(key, public_key)

    def get_trusted_key(self, node_id: str) -> Optional[bytes]:
        """Načte důvěryhodný veřejný klíč."""
        key = f"trusted_key:{node_id}".encode()
        # F184F: lazy env open
        with self._ensure_env().begin() as txn:
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

        # Store via executor — F184F: lazy executor init
        def _put():
            with self._ensure_env().begin(write=True) as txn:
                txn.put(key.encode(), plaintext)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._ensure_executor(), _put)

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
            with self._ensure_env().begin() as txn:
                return txn.get(key.encode())

        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(self._ensure_executor(), _get)

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
        """
        Zavře databázi.

        F184F: IDEMPOTENT — lze volat vícekrát bez chyby.
        _closed flag zamezuje dvojímu zavření env i executor.
        """
        if self._closed:
            return
        self._closed = True
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
        if self._env is not None:
            try:
                self._env.close()
            except Exception:
                pass
            self._env = None
