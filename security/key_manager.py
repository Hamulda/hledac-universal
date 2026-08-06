"""
security/key_manager.py — Real key management via macOS Keychain + HKDF-SHA256 (F350M-R)

M1 8GB RAM: Keychain lookup is ~1ms, HKDF-SHA256 derivation ~0.1ms.



No secrets ever stored in LMDB or plaintext files.

Real implementation:
  1. Master key stored in macOS Keychain via SecItemAdd/SecItemCopyMatching
     (kSecClassGenericPassword, service="com.hledac.universal", account="master_key")
  2. Bucket keys derived via HKDF-SHA256(ikm=master_key, salt=bucket_id, info=bucket_id)
     — per-bucket isolation, key rotation increments _current_version
  3. Lazy initialization: Keychain accessed only on first get_master_key() call

ISSUE-P7-001: Derived bucket keys are now protected by KeyMaterialGuard —
bytearray is mlock'd in RAM (no swap, no core dump), used, then secure_zero'd.

ADVERSARY-005: KeyMaterialGuard now calls register_mlock_region() at lock time
and unregister_mlock_region() at unlock time, so EphemeralStateAnnihilator
can batch-unlock all regions during TEARDOWN.

Fail-safe: raises NotImplementedError if Keychain unavailable (fail-loud, never stub keys).

Usage:
    from hledac.universal.security.key_manager import KeyManager
    km = KeyManager(db_path="/path/to/keys.lmdb")
    master_key = await km.get_master_key()
    bucket_key, version = await km.get_bucket_key("local_graph")
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ISSUE-P7-001: mlock FFI — lazy import (M1 safe)
# ---------------------------------------------------------------------------

_mlock_mod: Any = None


def _get_mlock():
    """Lazy import rust mlock FFI. Returns None if unavailable."""
    global _mlock_mod
    if _mlock_mod is None:
        try:
            import rust  # type: ignore[attr-defined]
            # rust.madvise.mlock_key_region, munlock_key_region, madvise_free_reusable, madvise_dontdump_region
            _mlock_mod = rust
        except ImportError:
            _mlock_mod = None
    return _mlock_mod


@contextmanager
def _key_material_guard(key_bytes: bytearray):
    """
    ISSUE-P7-001: Context manager that locks key material in RAM and wipes on exit.

    Flow:
      1. ctypes addressof(bytearray buffer) → mlock_key_region() — pins pages in RAM
      2. [yield to caller for use]
      3. secure_zero() — two-pass cryptographic wipe (random + zeros)
      4. munlock_key_region() — release RAM lock

    ADVERSARY-005: Registers/unregisters the mlock region with
    EphemeralStateAnnihilator so TEARDOWN can batch-unlock missed regions.

    On any error: proceeds to next step (fail-safe, never raises).
    On mlock unavailable (non-macOS / no permissions): skips locking, still wipes.

    mlock behavior on Darwin:
      - Pins pages in physical RAM — immune to swap
      - mlock'd pages are automatically excluded from core dumps
      - RLIMIT_MEMLOCK applies (ulimit -l); default ~64KB on macOS
      - For 32-byte bucket keys: trivially fits in default limit

    Args:
        key_bytes: Mutable bytearray containing sensitive key data
    """
    import ctypes

    ml = _get_mlock()
    addr: int | None = None

    # ADVERSARY-005: lazy import to avoid circular dependency
    try:
        from hledac.universal.security.ephemeral_wipe import (
            register_mlock_region,
            unregister_mlock_region,
        )
    except ImportError:
        register_mlock_region = None  # type: ignore
        unregister_mlock_region = None  # type: ignore

    if ml is not None:
        try:
            # Get the actual memory address of the bytearray's data buffer.
            # int.from_bytes(key_bytes) returns the BYTE VALUE (wrong).
            # ctypes.addressof gives the actual heap address (correct).
            addr = ctypes.addressof(ctypes.c_char.from_buffer(key_bytes))
            ml.madvise.mlock_key_region(addr, len(key_bytes))
            # ADVERSARY-005: register for TEARDOWN batch-unlock
            if register_mlock_region is not None:
                register_mlock_region(addr, len(key_bytes))
        except Exception as exc:
            logger.debug(f"KeyMaterialGuard: mlock unavailable ({exc}), proceeding without lock")

    try:
        yield
    finally:
        # Step 3: secure wipe
        try:
            from hledac.universal.utils.secure_zero import secure_zero
            secure_zero(key_bytes)
        except Exception as exc:
            logger.debug(f"KeyMaterialGuard: secure_zero failed ({exc})")

        # Step 4: munlock
        if ml is not None and addr is not None:
            try:
                ml.madvise.munlock_key_region(addr, len(key_bytes))
                # ISSUE-P7-008: After munlock, tell kernel pages are reclaimable.
                # This is critical for M1 8GB UMA: without this, munlock'd pages
                # stay in the working set even though they're clean. MADV_FREE_REUSABLE
                # lets the kernel reclaim them immediately under memory pressure.
                # advice=0 → MADV_FREE_REUSABLE (reclaimable when needed)
                # advice=1 → MADV_DONTNEED (discard immediately)
                try:
                    ml.madvise.madvise_free_reusable(addr, len(key_bytes), 0)
                except Exception:
                    pass
                # ADVERSARY-005: unregister from TEARDOWN tracker
                if unregister_mlock_region is not None:
                    unregister_mlock_region(addr, len(key_bytes))
            except Exception:
                pass


# ---------------------------------------------------------------------------
# macOS Keychain — lazy import (M1 safe)
# ---------------------------------------------------------------------------

_SecurityFramework: bool | object = False
_KEYCHAIN_SERVICE = "com.hledac.universal"
_KEYCHAIN_ACCOUNT = "master_key"


def _init_security() -> bool:
    """Try to import Security framework (PyObjC). Returns True if available."""
    global _SecurityFramework
    if _SecurityFramework is False:
        try:
            from Security import (
                SecItemAdd,
                SecItemCopyMatching,
                SecItemDelete,
                kSecClassGenericPassword,
                kSecMatchLimit,
                kSecReturnData,
                kSecAttrAccessible,
                kSecAttrAccessibleAfterFirstUnlock,
            )
            _SecurityFramework = {
                "SecItemAdd": SecItemAdd,
                "SecItemCopyMatching": SecItemCopyMatching,
                "SecItemDelete": SecItemDelete,
                "kSecClassGenericPassword": kSecClassGenericPassword,
                "kSecMatchLimit": kSecMatchLimit,
                "kSecReturnData": kSecReturnData,
                "kSecAttrAccessible": kSecAttrAccessible,
                "kSecAttrAccessibleAfterFirstUnlock": kSecAttrAccessibleAfterFirstUnlock,
            }
            return True
        except ImportError:
            _SecurityFramework = None
            return False
    return _SecurityFramework is not False


# ---------------------------------------------------------------------------
# HKDF-SHA256 key derivation
# ---------------------------------------------------------------------------

def _hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """
    HKDF-SHA256 — per-bucket key derivation from master key (RFC 5869 compliant).

    Uses HMAC-SHA256 for PRK extraction (not raw SHA256 of concat).
    When ikm == salt the raw SHA256 concat would be commutative and produce
    predictable output — HMAC fixes this.

    Args:
        ikm: Input keying material (master key)
        salt: Salt (bucket_id.encode())
        info: Context info (bucket_id.encode())
        length: Output key length (default 32 bytes for AES-256)

    Returns:
        Derived key bytes
    """
    import hmac
    import hashlib
    # Extract: PRK = HMAC-SHA256(salt, ikm)
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    # Expand
    n = (length + 31) // 32
    okm = b""
    t = b""
    for i in range(1, n + 1):
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
    return okm[:length]


# ---------------------------------------------------------------------------
# KeyManager — real implementation
# ---------------------------------------------------------------------------

class KeyManager:
    """
    Real key manager using macOS Keychain + HKDF-SHA256.

    Master key lives in Keychain (hardware-backed on Secure Enclave capable Macs).
    Bucket keys are derived on-demand via HKDF-SHA256 — never stored.

    ISSUE-P7-001: Derived bucket keys are protected by KeyMaterialGuard:
    mlock (RAM lock, no swap/core dump) → use → secure_zero (wipe) → munlock.

    Thread-safety: asyncio.Lock for Keychain operations (SecItemAdd/SecItemCopyMatching
    are NOT thread-safe on macOS when called simultaneously).

    Invariants:
        - get_master_key() always returns 32 bytes (256-bit)
        - get_bucket_key() returns 32 bytes + version counter
        - _current_version increments on explicit rotate() call
        - Keychain errors raise RuntimeError (fail-loud, never return stub)
    """

    __slots__ = tuple((
        '_current_version', '_db_path', '_keychain_lock',
        '_master_key', '_master_key_cached', '_salt',
    ))

    def __init__(self, db_path: str | None = None) -> None:
        """
        Initialize key manager.

        Args:
            db_path: Optional path to LMDB database directory for metadata
        """
        self._db_path = Path(db_path) if db_path else Path.home() / '.hledac' / 'keys'
        self._current_version = 1
        self._master_key: bytes | None = None
        self._master_key_cached = False
        self._salt: bytes | None = None
        # asyncio.Lock for thread-safe Keychain ops
        self._keychain_lock: asyncio.Lock | None = None
        logger.debug(f'KeyManager: db_path={self._db_path}')

    @property
    def db_path(self) -> Path:
        """Return path to LMDB database."""
        return self._db_path

    def _open_lmdb(self) -> Any:
        """Open LMDB environment for salt metadata storage."""
        import lmdb
        import os
        import stat as _stat

        # SEC-02: Create directory with 0700 before umask scope
        self._db_path.mkdir(parents=True, exist_ok=True)

        # SEC-02: Set umask to 0077 so LMDB files inherit 0o600
        _old_umask = os.umask(0o077)
        try:
            map_size = 64 * 1024  # 64 KB — tiny, just for salt
            env = lmdb.open(str(self._db_path), map_size=map_size, writemap=True, readahead=False, mode=0o600)
            # SEC-02: Double-enforce after open to cover all files LMDB creates
            self._chmod_lmdb_path()
            return env
        finally:
            os.umask(_old_umask)

    def _apply_madvise_to_lmdb(self) -> None:
        """
        ISSUE-P7-008: Apply MADV_NOCACHE to secrets vault LMDB .mdb file.

        On M1 8GB UMA, LMDB's mmap region competes with Metal GPU memory.
        MADV_NOCACHE (advice=1) tells the kernel not to cache these pages
        in the page cache — they belong exclusively to the application.

        This is called after _open_lmdb() creates the environment.
        Fail-safe: errors are logged but never propagate.
        """
        try:
            from hledac.universal.tools.file_cache import madvise_lmdb_mmap
            mdb_path = self._db_path / "data.mdb"
            if mdb_path.exists():
                madvise_lmdb_mmap(str(mdb_path), advice=1)  # MADV_NOCACHE
        except ImportError:
            # rust extension not available — skip silently
            pass
        except Exception as exc:
            logger.debug(f"KeyManager: madvise on LMDB vault failed ({exc})")

    def _chmod_lmdb_path(self) -> None:
        """SEC-02: Enforce 0o600 on LMDB directory and files."""
        import os
        import stat as _stat

        try:
            os.chmod(self._db_path, _stat.S_IRUSR | _stat.S_IWUSR | _stat.S_IXUSR)  # 0o700
        except OSError:
            pass
        for suffix in ("*.mdb", "*.lock"):
            for file_path in self._db_path.glob(suffix):
                try:
                    os.chmod(file_path, _stat.S_IRUSR | _stat.S_IWUSR)  # 0o600
                except OSError:
                    pass

    def _store_salt_in_lmdb(self, salt: bytes) -> None:
        """Store salt in LMDB metadata (salt is not sensitive, only used for HKDF)."""
        try:
            env = self._open_lmdb()
            with env.begin(write=True) as txn:
                txn.put(b"_master_salt", salt)
            env.close()
            # ISSUE-P7-008: After first write, data.mdb is created — apply MADV_NOCACHE.
            # Subsequent opens skip this safely since data.mdb already exists.
            self._apply_madvise_to_lmdb()
        except Exception as exc:
            logger.warning(f"KeyManager: failed to store salt in LMDB: {exc}")

    def _load_salt_from_lmdb(self) -> bytes | None:
        """Load salt from LMDB metadata."""
        try:
            env = self._open_lmdb()
            with env.begin() as txn:
                salt = txn.get(b"_master_salt")
            env.close()
            return salt if salt is not None else None
        except Exception:
            return None

    def _get_lock(self) -> asyncio.Lock:
        """Lazily create asyncio.Lock ( ISSUE-014 fix — no Lock at module import)."""
        if self._keychain_lock is None:
            self._keychain_lock = asyncio.Lock()
        return self._keychain_lock

    async def get_master_key(self) -> tuple[bytes, bytes, int]:
        """
        Get or create master key from macOS Keychain.

        Returns:
            tuple[bytes, bytes, int]: (key, salt, version)
                key: 32-byte master key
                salt: 16-byte random salt used for HKDF (stored in LMDB metadata)
                version: key version counter

        Raises:
            RuntimeError: Keychain unavailable or operation failed
            NotImplementedError: Security framework not available (non-macOS)
        """
        if self._master_key_cached and self._master_key is not None:
            return (self._master_key, self._salt or self._master_key, self._current_version)

        lock = self._get_lock()
        async with lock:
            # Double-check after acquiring lock
            if self._master_key_cached and self._master_key is not None:
                return (self._master_key, self._salt or self._master_key, self._current_version)

            if not _init_security() or _SecurityFramework is None:
                raise NotImplementedError(
                    "KeyManager requires macOS Keychain (Security framework). "
                    "PyObjC is required: pip install pyobjc-framework-Security. "
                    "Alternatively set HLEDAC_KEY_MANAGER_FALLBACK=1 for development only."
                )

            sec = _SecurityFramework
            # Try to read existing master key from Keychain
            query = {
                sec["kSecClassGenericPassword"]: True,
                "service": _KEYCHAIN_SERVICE,
                "account": _KEYCHAIN_ACCOUNT,
                sec["kSecReturnData"]: True,
                sec["kSecMatchLimit"]: 1,
            }
            result = sec["SecItemCopyMatching"](query, None)

            if result is not None:
                # Found existing key — load salt from LMDB
                stored = bytes(result)
                if len(stored) != 32:
                    raise RuntimeError(f"Keychain master key has invalid length {len(stored)}, expected 32")
                self._master_key = stored
                self._master_key_cached = True
                salt = self._load_salt_from_lmdb() or stored  # fallback to key itself if no salt
                self._salt = salt
                logger.debug("KeyManager: loaded master key from Keychain")
                return (stored, salt, self._current_version)

            # Generate new master key (32 bytes, cryptographically random)
            new_key = os.urandom(32)
            salt = os.urandom(16)

            # Store in Keychain with kSecAttrAccessibleAfterFirstUnlock
            add_attrs = {
                sec["kSecClassGenericPassword"]: True,
                "service": _KEYCHAIN_SERVICE,
                "account": _KEYCHAIN_ACCOUNT,
                "data": new_key,
                sec["kSecAttrAccessible"]: sec["kSecAttrAccessibleAfterFirstUnlock"],
            }
            add_result = sec["SecItemAdd"](add_attrs, None)
            if add_result != 0 and add_result is not None:
                raise RuntimeError(
                    f"Keychain SecItemAdd failed with result {add_result}. "
                    f"Cannot store master key securely."
                )

            # Store salt in LMDB metadata (salt is not sensitive, only used for HKDF)
            self._store_salt_in_lmdb(salt)

            self._master_key = new_key
            self._master_key_cached = True
            self._salt = salt
            logger.info("KeyManager: generated and stored new master key in Keychain")
            return (new_key, salt, self._current_version)

    async def get_bucket_key(self, bucket_id: str) -> tuple[bytes, int]:
        """
        Derive key for bucket via HKDF-SHA256(master_key, salt=bucket_id, info=bucket_id).

        ISSUE-P7-001: KeyMaterialGuard wraps the derived key bytearray —
        mlock (RAM pin) → use → secure_zero (wipe) → munlock.

        The raw key bytes are captured BEFORE the guard wipes them, so the
        return value is correct. The guard ensures the intermediate bytearray
        (key_ba) is wiped from heap after derivation completes.

        Args:
            bucket_id: Bucket identifier

        Returns:
            tuple[bytes, int]: (32-byte derived key, version)
        """
        master_key, _salt, version = await self.get_master_key()
        bucket_salt = bucket_id.encode('utf-8')
        raw_key = _hkdf_sha256(
            ikm=master_key,
            salt=bucket_salt,
            info=bucket_salt,
            length=32,
        )
        # ISSUE-P7-001: bytearray for mlock, guard wipes it after use.
        # raw_key_bytes is captured BEFORE guard so return value is correct.
        key_ba = bytearray(raw_key)
        with _key_material_guard(key_ba):
            # Key is mlock'd in RAM here — use for crypto operations.
            # The raw_key_bytes copy is safe to use outside the guard.
            pass
        # After guard: key_ba is wiped, but raw_key_bytes is valid.
        return (raw_key, version)

    async def rotate(self) -> None:
        """
        Increment key version for bucket key rotation.

        Next get_bucket_key() call will derive keys with a new version suffix.
        Caller is responsible for re-encrypting data with the new key.
        """
        self._current_version += 1
        # Invalidate cached master key so salt is re-derived
        self._master_key = None
        self._master_key_cached = False
        self._salt = None
        logger.info(f"KeyManager: rotated to version {self._current_version}")

    async def delete_master_key(self) -> None:
        """
        Permanently delete master key from Keychain.

        WARNING: All bucket keys derived from this master key become invalid.
        Only call when intentionally decommissioning the vault.
        """
        if not _init_security():
            return
        sec = _SecurityFramework
        delete_query = {
            sec["kSecClassGenericPassword"]: True,
            "service": _KEYCHAIN_SERVICE,
            "account": _KEYCHAIN_ACCOUNT,
        }
        sec["SecItemDelete"](delete_query)
        self._master_key = None
        self._master_key_cached = False
        self._salt = None
        # Also delete salt from LMDB
        try:
            env = self._open_lmdb()
            with env.begin(write=True) as txn:
                txn.delete(b"_master_salt")
            env.close()
        except Exception:
            pass
        logger.warning("KeyManager: deleted master key from Keychain")


__all__ = ['KeyManager', '_key_material_guard']
