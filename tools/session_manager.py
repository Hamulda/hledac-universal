"""
Session Manager – ukládá cookies a credentials, automaticky je injectuje do fetch.
Sprint 46: Access to Unreachable Data (Sessions + Paywall + OSINT + Darknet)
Sprint 48: Async LMDB operations via executor, orjson serialization
"""

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import os
import secrets
import sys
import time

import lmdb

# S48-P8: Try orjson for faster serialization, fallback to json
try:
    import orjson
    USE_ORJSON = True
except ImportError:
    USE_ORJSON = False
    import json

# F206L: Fernet encryption for cookies (P25 - Cookies stored unencrypted)
try:
    from cryptography.fernet import Fernet
    FERNET_AVAILABLE = True
except ImportError:
    FERNET_AVAILABLE = False

logger = logging.getLogger(__name__)

# F206L: Encryption key storage key in LMDB
_ENCRYPTION_KEY_KEY = b"session:_encryption_key"


def _derive_encryption_key() -> bytes:
    """
    Derive a machine-specific Fernet key from unique machine identifiers.
    Uses multiple sources to ensure same machine produces same key across reinstalls.
    This allows decryption of existing sessions after app reinstall on same machine.

    Falls back to random key if machine ID cannot be determined.
    """
    import base64
    import subprocess

    # Collect machine-specific data
    key_material = []

    # Machine identifier from hostname
    try:
        key_material.append(os.environ.get('HOSTNAME', ''))
        key_material.append(os.environ.get('COMPUTERNAME', ''))
    except Exception:  # noqa: BLE001
        pass

    # User-specific data
    key_material.append(os.environ.get('USER', ''))
    key_material.append(os.environ.get('USERNAME', ''))

    # Platform-specific data
    key_material.append(sys.platform)

    # Try to get a unique machine ID (common on Linux/Mac)
    machine_id = ''
    try:
        if sys.platform == 'darwin':
            # Issue #13 fix: run in thread to avoid blocking event loop
            result = subprocess.run(
                ['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.split('\n'):
                if 'IOPlatformUUID' in line:
                    machine_id = line.split('"')[-2] if '"' in line else ''
                    break
        elif sys.platform == 'linux':
            for mpath in ['/etc/machine-id', '/var/lib/dbus/machine-id']:
                if os.path.exists(mpath):
                    with open(mpath) as f:
                        machine_id = f.read().strip()
                    break
    except Exception:  # noqa: BLE001
        pass

    if machine_id:
        key_material.append(machine_id)
    else:
        # Fallback: random key - sessions will be lost on reinstall
        return Fernet.generate_key() if FERNET_AVAILABLE else secrets.token_bytes(32)

    # Derive 32-byte key and encode as Fernet-compatible (URL-safe base64)
    combined = ''.join(key_material)
    derived = hashlib.sha256(combined.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return fernet_key


async def _derive_encryption_key_async() -> bytes:
    """
    Async version: run the sync _derive_encryption_key in a thread pool.
    Issue #13 fix: asyncio.to_thread avoids blocking the event loop.
    """
    return await asyncio.to_thread(_derive_encryption_key)


class SessionManager:
    """
    Manages HTTP sessions with cookies/credentials persistence in LMDB.

    AUTHORITY NOTE (Sprint 8UX):
        This module is the PERSISTED SESSION authority.
        It stores cookies and headers in LMDB, keyed by domain.
        It is SEPARATE from session_runtime.py (shared async HTTP surface).

        Split is intentional:
          - session_runtime.py: raw HTTP session pool, no credentials
          - SessionManager: credentialed session state, domain-scoped persistence

        FetchCoordinator._fetch_url() calls SessionManager.get_session()
        to inject cookies/headers into transport-layer fetch operations.

    OWNERSHIP BOUNDARY (F300K):
        - LMDB env is INJECTED via __init__ — SessionManager does NOT own it
        - ThreadPoolExecutor is OWNED locally, closed via close()
        - post-close: all methods guard against use-after-close

    CLOSE SEMANTICS (F300K):
        - close() is idempotent — safe to call multiple times
        - executor.shutdown(wait=False) — non-blocking, no event-loop stall
        - _cache is NOT cleared (by design — remains accessible for reads)
        - _closed flag guards all mutating operations post-close
    """

    def __init__(self, lmdb_env: lmdb.Environment):
        self._env = lmdb_env
        self._cache: dict[str, dict] = {}  # domain -> {cookies, headers, last_used}
        # S49-B: Thread pool executor for async LMDB operations
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="session_lmdb"
        )
        # F300K: explicit closed state — guards post-close truthfulness
        self._closed: bool = False
        # F206L: Fernet cipher for cookie encryption (P25)
        # Issue #13 fix: lazy init — _derive_encryption_key uses subprocess.run
        # which would block event loop if called in __init__ from async context.
        # Initialize lazily on first async operation.
        self._fernet: Fernet | None = None
        self._encryption_key: bytes | None = None

    def _get_key(self, domain: str) -> bytes:
        return f"session:{domain}".encode()

    async def _ensure_fernet(self) -> None:
        """
        F206L: Lazy initialization of Fernet cipher (P25).

        Issue #13 fix: _derive_encryption_key_async uses asyncio.to_thread
        to avoid blocking the event loop with subprocess.run/ioreg.
        Must be called from async context (get_session, save_session).
        """
        if self._fernet is not None:
            return
        if not FERNET_AVAILABLE:
            return
        # Check LMDB first
        with self._env.begin() as txn:
            key_data = txn.get(_ENCRYPTION_KEY_KEY)
            if key_data:
                self._encryption_key = key_data
                self._fernet = Fernet(self._encryption_key)
                return
        # First time: derive async and store
        self._encryption_key = await _derive_encryption_key_async()
        with self._env.begin(write=True) as txn:
            txn.put(_ENCRYPTION_KEY_KEY, self._encryption_key)
        self._fernet = Fernet(self._encryption_key)

    def _encrypt(self, data: bytes) -> bytes:
        """F206L: Encrypt data using Fernet (P25)."""
        if self._fernet is None:
            return data  # Fallback: no encryption
        return self._fernet.encrypt(data)

    def _decrypt(self, data: bytes) -> bytes:
        """
        F206L: Decrypt data using Fernet (P25).

        Backward compatibility: if decryption fails (old unencrypted data),
        return data as-is for plain deserialization.
        """
        if self._fernet is None:
            return data  # Fallback: no decryption
        try:
            return self._fernet.decrypt(data)
        except Exception:
            # Backward compatibility: data stored before encryption was added
            return data

    # S48-P8: Fast serialization with F206L encryption (P25)
    def _serialize(self, data: dict) -> bytes:
        serialized = orjson.dumps(data) if USE_ORJSON else json.dumps(data).encode()
        return self._encrypt(serialized)

    def _deserialize(self, data: bytes) -> dict:
        decrypted = self._decrypt(data)
        return orjson.loads(decrypted) if USE_ORJSON else json.loads(decrypted.decode())

    # S49-B: Sync LMDB operations for executor
    def _sync_get(self, key: bytes) -> dict | None:
        with self._env.begin() as txn:
            data = txn.get(key)
            return self._deserialize(data) if data else None

    def _sync_put(self, key: bytes, data: bytes) -> None:
        with self._env.begin(write=True) as txn:
            txn.put(key, data)

    def _sync_delete(self, key: bytes) -> None:
        with self._env.begin(write=True) as txn:
            txn.delete(key)

    # F300K: Helper to check and guard closed state for read-only methods.
    # Returns cached data if available after close, else None.
    def _get_from_cache_after_close(self, domain: str) -> dict | None:
        if self._closed and domain in self._cache:
            return self._cache[domain]
        return None

    # S49-B: Async LMDB operations via executor
    async def get_session(self, domain: str) -> dict | None:
        """Vrátí uložené session pro domain."""
        # Issue #13 fix: lazy Fernet init — must be async for subprocess.to_thread
        await self._ensure_fernet()
        # F300K: After close, return stale cached data (read-only, no LMDB write)
        if self._closed:
            cached = self._cache.get(domain)
            if cached:
                cached['last_used'] = time.time()
            return cached

        # Check RAM cache first
        if domain in self._cache:
            self._cache[domain]['last_used'] = time.time()
            return self._cache[domain]

        # S49-B: Async LMDB read via executor - non-blocking
        try:
            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(self._executor, self._sync_get, self._get_key(domain))
            if data:
                self._cache[domain] = data
                return data
        except Exception:  # noqa: BLE001
            pass
        return None

    async def save_session(self, domain: str, cookies: dict, headers: dict | None = None):
        """Uloží session pro domain. F300K: no-op after close."""
        # Issue #13 fix: lazy Fernet init
        await self._ensure_fernet()
        # F300K: Guard — mutate operations blocked after close
        if self._closed:
            logger.debug(f"[SESSION] save_session({domain}) — blocked, manager closed")
            return

        session = {
            'cookies': cookies,
            'headers': headers or {},
            'created': time.time(),
            'last_used': time.time()
        }
        self._cache[domain] = session

        # S49-B: Async LMDB write via executor
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self._executor,
                self._sync_put,
                self._get_key(domain),
                self._serialize(session)
            )
        except Exception as e:
            logger.warning(f"[SESSION] Failed to save {domain}: {e}")

    async def rotate_credentials(self, domain: str):
        """Zahodí staré session, přiští fetch zkusí znovu přihlásit. F300K: no-op after close."""
        # F300K: Guard — mutate operations blocked after close
        if self._closed:
            logger.debug(f"[SESSION] rotate_credentials({domain}) — blocked, manager closed")
            return

        if domain in self._cache:
            del self._cache[domain]

        # S49-B: Async LMDB delete via executor
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._sync_delete, self._get_key(domain))
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        """
        F300K: Cleanup executor on shutdown.

        Idempotent — safe to call multiple times.
        Uses wait=False to avoid blocking the event loop.
        Mutating operations (save_session, rotate_credentials) are
        blocked after close. Read operations (get_session) continue
        to return stale cached data.
        """
        if self._closed:
            return
        self._closed = True
        # F300K: wait=False — non-blocking, no event-loop stall on M1 8GB UMA
        self._executor.shutdown(wait=False)
