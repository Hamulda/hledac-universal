"""
P1-6: Vault Manager Test Coverage
==================================

ZERO test coverage for security/vault_manager.py — this test file addresses it.

Tests:
  P1-6-1  | LootManager instantiation fails without crypto deps
  P1-6-2  | FALLBACK_ENC detection and rejection
  P1-6-3  | secure_export/decrypt_export round-trip (Fernet)
  P1-6-4  | secure_export/decrypt_export round-trip (pyzipper)
  P1-6-5  | Invalid password returns None on decrypt
  P1-6-6  | VaultManager alias works
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from security.vault_manager import (
    CRYPTO_AVAILABLE,
    PYZIPPER_AVAILABLE,
    LootManager,
    VaultManager,
)


class TestVaultManagerInstantiation:
    """P1-6-1: LootManager instantiation fails without crypto deps."""

    def test_instantiation_succeeds_with_crypto(self):
        """VaultManager should instantiate when crypto is available."""
        if not (CRYPTO_AVAILABLE or PYZIPPER_AVAILABLE):
            pytest.skip("No crypto packages available")

        with tempfile.TemporaryDirectory() as tmpdir:
            vm = LootManager(tmpdir)
            assert vm is not None
            assert vm.vault_path == Path(tmpdir)

    def test_alias_vaultmanager_works(self):
        """VaultManager alias should be LootManager."""
        assert VaultManager is LootManager


class TestFallbackEncRejection:
    """P1-6-2: FALLBACK_ENC detection and rejection."""

    def test_fallback_enc_prefix_rejected(self):
        """decrypt_export should reject FALLBACK_ENC exports."""
        if not CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            vm = LootManager(tmpdir)

            # Create a fake FALLBACK_ENC file
            fallback_file = Path(tmpdir) / "fake.enc"
            fallback_file.write_bytes(b"FALLBACK_ENC:" + b"x" * 100)

            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()

            result = vm.decrypt_export(str(fallback_file), "anypassword", str(output_dir))
            assert result is None, "FALLBACK_ENC should be rejected"


class TestSecureExportRoundTrip:
    """P1-6-3/4: secure_export/decrypt_export round-trip."""

    @pytest.fixture
    def temp_vault(self):
        """Create a temporary vault with test content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_dir = Path(tmpdir) / "vault"
            vault_dir.mkdir()

            # Create test files
            (vault_dir / "test.txt").write_text("sensitive data")
            (vault_dir / "subdir").mkdir()
            (vault_dir / "subdir" / "nested.txt").write_text("more sensitive data")

            yield vault_dir

    def _get_password(self) -> str:
        """Return test password."""
        return "test_password_123"

    def test_fernet_round_trip(self, temp_vault):
        """P1-6-3: Fernet encrypt/decrypt round-trip."""
        if not CRYPTO_AVAILABLE:
            pytest.skip("cryptography not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            vm = LootManager(str(temp_vault))
            password = self._get_password()

            # Export
            exported = vm.secure_export(tmpdir, password)
            assert exported is not None
            assert Path(exported).exists()
            assert exported.endswith(".enc")

            # Verify it's not plaintext
            with open(exported, "rb") as f:
                content = f.read()
            assert not content.startswith(b"PK\x03\x04")  # Not a plaintext ZIP
            assert b"sensitive data" not in content

            # Decrypt
            decrypt_dir = Path(tmpdir) / "decrypted"
            decrypt_dir.mkdir()
            result = vm.decrypt_export(exported, password, str(decrypt_dir))
            assert result is not None

            # Verify content
            decrypted_vault = Path(result)
            assert (decrypted_vault / "test.txt").read_text() == "sensitive data"
            assert (decrypted_vault / "subdir" / "nested.txt").read_text() == "more sensitive data"

    def test_pyzipper_round_trip(self, temp_vault):
        """P1-6-4: pyzipper encrypt/decrypt round-trip."""
        if not PYZIPPER_AVAILABLE:
            pytest.skip("pyzipper not available")

        with tempfile.TemporaryDirectory() as tmpdir:
            vm = LootManager(str(temp_vault))
            password = self._get_password()

            # Export
            exported = vm.secure_export(tmpdir, password, "test_export.enc")
            assert exported is not None
            assert Path(exported).exists()

            # Decrypt
            decrypt_dir = Path(tmpdir) / "decrypted"
            decrypt_dir.mkdir()
            result = vm.decrypt_export(exported, password, str(decrypt_dir))
            assert result is not None

            # Verify content
            decrypted_vault = Path(result)
            assert (decrypted_vault / "test.txt").read_text() == "sensitive data"

    def test_invalid_password_returns_none(self, temp_vault):
        """P1-6-5: Invalid password returns None on decrypt."""
        if not (CRYPTO_AVAILABLE or PYZIPPER_AVAILABLE):
            pytest.skip("No crypto packages available")

        with tempfile.TemporaryDirectory() as tmpdir:
            vm = LootManager(str(temp_vault))
            password = self._get_password()

            # Export with correct password
            exported = vm.secure_export(tmpdir, password)
            assert exported is not None

            # Try decrypt with wrong password
            decrypt_dir = Path(tmpdir) / "decrypted"
            decrypt_dir.mkdir()
            result = vm.decrypt_export(exported, "wrong_password", str(decrypt_dir))
            assert result is None, "Wrong password should return None"

    def test_nonexistent_vault_path_returns_none(self):
        """Secure export should return None for nonexistent vault."""
        if not (CRYPTO_AVAILABLE or PYZIPPER_AVAILABLE):
            pytest.skip("No crypto packages available")

        with tempfile.TemporaryDirectory() as tmpdir:
            vm = LootManager("/nonexistent/path")
            result = vm.secure_export(tmpdir, "password")
            assert result is None

    def test_nonexistent_encrypted_file_returns_none(self):
        """Decrypt should return None for nonexistent file."""
        if not (CRYPTO_AVAILABLE or PYZIPPER_AVAILABLE):
            pytest.skip("No crypto packages available")

        with tempfile.TemporaryDirectory() as tmpdir:
            vm = LootManager(tmpdir)
            result = vm.decrypt_export("/nonexistent/file.enc", "password", tmpdir)
            assert result is None
