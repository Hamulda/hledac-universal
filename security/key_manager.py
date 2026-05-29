import asyncio
import logging
import os
import secrets
from pathlib import Path

import orjson

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    CRYPTO_AVAILABLE = True
except ImportError:
    Cipher = None
    algorithms = None
    modes = None
    HKDF = None
    hashes = None
    default_backend = None
    CRYPTO_AVAILABLE = False

logger = logging.getLogger(__name__)

KEY_VERSION = 1

# Sprint 0A: Bootstrap-safe mlock for mutable key buffers
_HAS_MLOCK = False


def _try_mlock(buf: bytearray) -> bool:
    """
    Attempt to mlock a mutable buffer to prevent swapping.
    Fail-open: returns False if mlock unavailable or denied.
    Never use on Python str.
    """
    global _HAS_MLOCK
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        # mlock(addr, len) - returns 0 on success
        result = libc.mlock(ctypes.addressof(buf), ctypes.sizeof(buf))
        if result == 0:
            _HAS_MLOCK = True
            return True
        return False
    except Exception:
        return False


class KeyManager:
    def __init__(self, db_path: str | None = None, master_key_id: str = "master"):
        if not CRYPTO_AVAILABLE:
            raise ImportError("cryptography package is required for KeyManager")
        from hledac.universal.paths import KEYS_ROOT
        if db_path is None:
            self.db_path = KEYS_ROOT / "keys.lmdb"
        else:
            self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Sprint 2B: use env-driven map_size via paths helper
        from hledac.universal.paths import open_lmdb
        self.env = open_lmdb(self.db_path.parent, map_size=10 * 1024 * 1024)
        self.master_key_id = master_key_id
        self._master_keys: dict[int, bytes] = {}
        self._master_salts: dict[int, bytes] = {}
        self._bucket_key_cache: dict[str, bytes] = {}
        self._lock = asyncio.Lock()
        self._current_version = 0  # 0 znamená, že ještě není načteno/vygenerováno

    async def _load_master_keys(self) -> None:
        """Načte všechny verze master klíčů z LMDB."""
        def _load():
            with self.env.begin() as txn:
                cursor = txn.cursor()
                keys = {}
                salts = {}
                for k, v in cursor:
                    if k.startswith(self.master_key_id.encode()):
                        key_str = k.decode()
                        if ':' in key_str:
                            _, version_str = key_str.split(':', 1)
                            version = int(version_str)
                        else:
                            version = 0
                        data = orjson.loads(v)
                        keys[version] = bytes(data['key'])
                        salts[version] = bytes(data.get('salt', b''))
                return keys, salts

        loop = asyncio.get_running_loop()
        keys, salts = await loop.run_in_executor(None, _load)
        self._master_keys = keys
        self._master_salts = salts
        if keys:
            self._current_version = max(keys.keys())
        else:
            # Žádný klíč – vygenerujeme nový s verzí 1
            await self._generate_new_master_key()

    async def _generate_new_master_key(self) -> int:
        """Vygeneruje nový master klíč s novou verzí a salt."""
        new_version = self._current_version + 1
        new_key = secrets.token_bytes(32)
        new_salt = os.urandom(16)

        # Sprint 0A: mlock key material (bootstrap-safe, fail-open)
        key_buf = bytearray(new_key)
        _try_mlock(key_buf)

        def _save():
            with self.env.begin(write=True) as txn:
                key_id = f"{self.master_key_id}:{new_version}".encode()
                data = {'key': list(new_key), 'salt': list(new_salt)}
                txn.put(key_id, orjson.dumps(data))

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _save)
        self._master_keys[new_version] = new_key
        self._master_salts[new_version] = new_salt
        self._current_version = new_version
        logger.info(f"Generated new master key (version {new_version})")
        return new_version

    async def get_master_key(self, version: int | None = None) -> tuple[bytes, bytes, int]:
        """
        Vrátí (klíč, salt, verze) pro požadovanou verzi (nebo aktuální, pokud None).
        """
        async with self._lock:
            if not self._master_keys:
                await self._load_master_keys()
            if not self._master_keys:
                await self._generate_new_master_key()
            if version is None:
                version = self._current_version
            if version not in self._master_keys:
                raise ValueError(f"Master key version {version} not found")
            return self._master_keys[version], self._master_salts.get(version, b''), version

    async def get_bucket_key(self, bucket_id: str, version: int | None = None) -> tuple[bytes, int]:
        """
        Odvodí klíč pro bucket z master klíče dané verze.
        Vrací (klíč, skutečná verze). Výsledek je cachován.
        """
        if not CRYPTO_AVAILABLE:
            raise ImportError("cryptography package is required for KeyManager.get_bucket_key")
        master, salt, resolved_version = await self.get_master_key(version)
        cache_key = f"{bucket_id}:{resolved_version}"
        if cache_key in self._bucket_key_cache:
            return self._bucket_key_cache[cache_key], resolved_version

        hkdf = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt if salt else None,
            info=f"{bucket_id}:{resolved_version}".encode(),
            backend=default_backend()
        )
        key = hkdf.derive(master)
        self._bucket_key_cache[cache_key] = key
        return key, resolved_version

    async def rotate_master_key(self, migrate: bool = False):
        """
        Rotace master klíče – vytvoří novou verzi.
        """
        async with self._lock:
            new_version = await self._generate_new_master_key()
            if not migrate:
                logger.info(f"New master key version {new_version} created; old data will be unreadable")
            else:
                logger.info(f"New master key version {new_version} created; old keys retained for reading")
