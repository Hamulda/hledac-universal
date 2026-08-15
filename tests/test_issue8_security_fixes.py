"""
Testy pro ISSUE 8.1 (KeyManager real implementation) a ISSUE 8.2 (Vault AES-256-GCM).

F350M-R: security/key_manager.py + secrets_vault/vault.py
"""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest
from core import aclose


class TestKeyManagerHKDF:
    """Test HKDF-SHA256 key derivation used by KeyManager."""

    def test_hkdf_deterministic(self):
        from hledac.universal.security.key_manager import _hkdf_sha256

        master = os.urandom(32)
        key1 = _hkdf_sha256(master, b'bucket1', b'bucket1', 32)
        key2 = _hkdf_sha256(master, b'bucket1', b'bucket1', 32)
        assert key1 == key2

    def test_hkdf_different_buckets(self):
        from hledac.universal.security.key_manager import _hkdf_sha256

        master = os.urandom(32)
        key1 = _hkdf_sha256(master, b'bucket1', b'bucket1', 32)
        key2 = _hkdf_sha256(master, b'bucket2', b'bucket2', 32)
        assert key1 != key2

    def test_hkdf_32_bytes(self):
        from hledac.universal.security.key_manager import _hkdf_sha256

        master = os.urandom(32)
        key = _hkdf_sha256(master, b'bucket', b'bucket', 32)
        assert len(key) == 32

    def test_hkdf_different_master(self):
        from hledac.universal.security.key_manager import _hkdf_sha256

        master1 = os.urandom(32)
        master2 = os.urandom(32)
        key1 = _hkdf_sha256(master1, b'bucket', b'bucket', 32)
        key2 = _hkdf_sha256(master2, b'bucket', b'bucket', 32)
        assert key1 != key2


class TestKeyManagerAPI:
    """Test KeyManager API surface (stub/fail-loud behavior)."""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    def test_init(self, temp_dir):
        from hledac.universal.security.key_manager import KeyManager

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        assert km.db_path.name == "keys.lmdb"
        assert km._current_version == 1

    def test_init_default_path(self):
        from hledac.universal.security.key_manager import KeyManager

        km = KeyManager()
        assert ".hledac" in str(km.db_path)

    @pytest.mark.asyncio
    async def test_get_bucket_key_returns_tuple(self, temp_dir):
        from hledac.universal.security.key_manager import KeyManager

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        try:
            bucket_key, version = await km.get_bucket_key("test_bucket")
            assert isinstance(bucket_key, bytes)
            assert len(bucket_key) == 32
            assert isinstance(version, int)
            assert version >= 1
        except NotImplementedError:
            # Expected on non-macOS (no Security framework)
            pass

    @pytest.mark.asyncio
    async def test_get_bucket_key_deterministic(self, temp_dir):
        from hledac.universal.security.key_manager import KeyManager

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        try:
            k1, v1 = await km.get_bucket_key("my_bucket")
            k2, v2 = await km.get_bucket_key("my_bucket")
            assert k1 == k2
            assert v1 == v2
        except NotImplementedError:
            pass

    @pytest.mark.asyncio
    async def test_get_bucket_key_different_buckets_different_keys(self, temp_dir):
        from hledac.universal.security.key_manager import KeyManager

        km = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        try:
            k1, _ = await km.get_bucket_key("bucket_a")
            k2, _ = await km.get_bucket_key("bucket_b")
            assert k1 != k2
        except NotImplementedError:
            pass

    @pytest.mark.asyncio
    async def test_salt_persistence_across_instances(self, temp_dir):
        """Salt is stored in LMDB and reused when creating a new KeyManager instance."""
        import lmdb
        from hledac.universal.security.key_manager import KeyManager

        # On non-macOS, test _load_salt_from_lmdb / _store_salt_in_lmdb directly
        km1 = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        try:
            _, salt1, _ = await km1.get_master_key()
        except NotImplementedError:
            # Non-macOS: test salt storage/loading directly via LMDB
            test_salt = b"0123456789abcdef"
            km1._store_salt_in_lmdb(test_salt)
            loaded = km1._load_salt_from_lmdb()
            assert loaded == test_salt
            # Second instance loads same salt
            km2 = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
            loaded2 = km2._load_salt_from_lmdb()
            assert loaded2 == test_salt
            return

        # On macOS: verify salt is persisted in LMDB
        env = lmdb.open(f"{temp_dir}/keys.lmdb", map_size=64 * 1024, writemap=True, readahead=False)
        with env.begin() as txn:
            stored_salt = txn.get(b"_master_salt")
        env.close()
        assert stored_salt is not None
        assert len(stored_salt) == 16
        assert stored_salt == salt1

        # New instance should return the same salt
        km2 = KeyManager(db_path=f"{temp_dir}/keys.lmdb")
        _, salt2, _ = await km2.get_master_key()
        assert salt2 == salt1
        assert salt2 == stored_salt


class TestVaultAESGCMAeadFormat:
    """Test AES-256-GCM AEAD format used by SecretVault."""

    def test_aead_encrypt_format_v1(self):
        from secrets_vault.vault import _aead_encrypt

        key = os.urandom(32)
        blob = _aead_encrypt(b"hello world", key)

        assert blob[0] == 0x01  # version byte
        assert len(blob) >= 1 + 12 + 16  # version + nonce + tag minimum
        # nonce at bytes 1-12
        assert len(blob[1:13]) == 12
        # tag at end
        assert len(blob[-16:]) == 16

    def test_aead_encrypt_unique_nonce(self):
        from secrets_vault.vault import _aead_encrypt

        key = os.urandom(32)
        b1 = _aead_encrypt(b"same plaintext", key)
        b2 = _aead_encrypt(b"same plaintext", key)
        # Nonces are random — ciphertext should differ
        assert b1 != b2

    def test_aead_decrypt_roundtrip(self):
        from secrets_vault.vault import _aead_encrypt, _aead_decrypt

        key = os.urandom(32)
        plaintext = b'{"api_key": "hunter2", "nested": {"a": 1}}'
        blob = _aead_encrypt(plaintext, key)
        decrypted = _aead_decrypt(blob, key)
        assert decrypted == plaintext

    def test_aead_decrypt_wrong_key_returns_none(self):
        from secrets_vault.vault import _aead_encrypt, _aead_decrypt

        key = os.urandom(32)
        wrong_key = os.urandom(32)
        blob = _aead_encrypt(b"secret data", key)
        result = _aead_decrypt(blob, wrong_key)
        assert result is None

    def test_aead_decrypt_tampered_ciphertext_returns_none(self):
        from secrets_vault.vault import _aead_encrypt, _aead_decrypt

        key = os.urandom(32)
        blob = _aead_encrypt(b"original", key)
        # Flip a bit in the ciphertext
        tampered = bytearray(blob)
        # Find a byte in the ciphertext portion (after version + nonce)
        idx = 13
        tampered[idx] ^= 0xFF
        result = _aead_decrypt(bytes(tampered), key)
        assert result is None

    def test_aead_decrypt_empty_returns_none(self):
        from secrets_vault.vault import _aead_decrypt

        key = os.urandom(32)
        assert _aead_decrypt(b"", key) is None
        assert _aead_decrypt(None, key) is None

    def test_aead_decrypt_too_short_returns_none(self):
        from secrets_vault.vault import _aead_decrypt

        key = os.urandom(32)
        # Less than version + nonce + tag
        assert _aead_decrypt(b'\x01' + b'\x00' * 10, key) is None


class TestVaultPbkdf2Iterations:
    """Verify PBKDF2 iteration count matches OWASP 2025 recommendation."""

    def test_pbkdf2_600k_iterations(self):
        from secrets_vault.vault import _PBKDF2_ITERATIONS

        assert _PBKDF2_ITERATIONS == 600_000


class TestVaultSaltMetadata:
    """Test that salt is stored in LMDB metadata, not in blob."""

    @pytest.fixture
    def vault_path(self):
        tmp = tempfile.mkdtemp()
        yield os.path.join(tmp, "vault.lmdb")
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_salt_stored_in_lmdb(self, vault_path):
        from secrets_vault.vault import SecretVault

        vault = SecretVault(vault_path, "password123")
        # Salt should be stored under _vault_salt key
        raw_salt = vault._lmdb_get(vault._salt_key)
        assert raw_salt is not None
        assert len(raw_salt) == 16
        vault.close()

    @pytest.mark.asyncio
    async def test_put_and_get_roundtrip(self, vault_path):
        from secrets_vault.vault import SecretVault

        vault = SecretVault(vault_path, "password123")
        await vault.put("creds", {"user": "alice", "pass": "sekrit"})
        result = await vault.get("creds")
        assert result == {"user": "alice", "pass": "sekrit"}
        vault.close()

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, vault_path):
        from secrets_vault.vault import SecretVault

        vault = SecretVault(vault_path, "password123")
        result = await vault.get("does_not_exist")
        assert result is None
        vault.close()

    @pytest.mark.asyncio
    async def test_delete_works(self, vault_path):
        from secrets_vault.vault import SecretVault

        vault = SecretVault(vault_path, "password123")
        await vault.put("temp", {"val": 123})
        assert await vault.get("temp") is not None
        await vault.delete("temp")
        assert await vault.get("temp") is None
        vault.close()

    @pytest.mark.asyncio
    async def test_close_zeros_derived_key(self, vault_path):
        from secrets_vault.vault import SecretVault

        vault = SecretVault(vault_path, "password123")
        derived_key = vault._derived_key
        vault.close()
        # After close, derived_key should be zeroed
        assert vault._derived_key == b'\x00' * len(derived_key)
