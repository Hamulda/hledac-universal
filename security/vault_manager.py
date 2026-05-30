import logging
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING


# Sprint 0A: RAMDISK tempfile dir (lazy, reads tempfile.tempdir at call time)
def _get_tempdir() -> str:
    """Return tempfile.gettempdir() - reads current value at call time."""
    return tempfile.gettempdir()

try:
    import base64

    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

# Type-only import for pyzipper (may not have type stubs)
if TYPE_CHECKING:
    import pyzipper

try:
    import pyzipper
    PYZIPPER_AVAILABLE = True
except ImportError:
    PYZIPPER_AVAILABLE = False

# ── CryptoKit Native Backend (M1 hardware-accelerated) ───────────────────────
# Uses Security.framework CryptoKit for AES-GCM on Apple Silicon
# Provides ~3x faster encryption than pure Python cryptography on M1
CRYPTOKIT_AVAILABLE = False


def _check_cryptokit() -> bool:
    """Check if CryptoKit AES-GCM is available via Swift helper."""
    try:
        import subprocess
        import json as _json

        repo_root = Path(__file__).parent.parent
        helper_path = repo_root / "tools" / "secure_enclave_helper" / ".build" / "release" / "secure-enclave-helper"
        if not helper_path.exists():
            return False
        result = subprocess.run(
            [str(helper_path), "cryptokit-status"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            data = _json.loads(result.stdout)
            return data.get("ok", False) and data.get("data", {}).get("aes_gcm_available", False) == "true"
    except Exception:
        pass
    return False


CRYPTOKIT_AVAILABLE = _check_cryptokit()

logger = logging.getLogger(__name__)


class LootManager:
    """
    Encrypted vault export manager.

    Canonical name: VaultManager (alias below).
    LootManager is preserved for compat only; VaultManager is the authoritative name.

    AUTHORITY SCOPE (this module):
        - secure_export(): encrypted ZIP archive of vault_path → .enc file
        - decrypt_export(): reverse operation
        - _shred_directory(): secure deletion after export

    SECURE EXPORT PATH (priority order):
        1. pyzipper AES (WZ_AES) — requires pyzipper
        2. Fernet (cryptography) — requires cryptography

    SECURITY INVARIANT:
        - XOR fallback REMOVED (P0-3/P0-5 fix) — no obfuscation, real encryption required
        - Module FAILS at instantiation if neither cryptography nor pyzipper is available

    NON-AUTHORITY (NOT this module):
        - PII detection/sanitization (see pii_gate.py)
        - Content blocking/rejection (early gate = detection only)
        - Metadata extraction (see metadata_extractor.py)
        - Steganography detection (see stego_detector.py)
        - Sprint report export (see export/sprint_exporter.py)
    """

    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)

        # P0-3/P0-5 fix: FAIL FAST if no real encryption available
        if not (CRYPTO_AVAILABLE or PYZIPPER_AVAILABLE):
            raise RuntimeError(
                "vault_manager requires 'cryptography' or 'pyzipper' for real encryption. "
                "XOR fallback has been removed (P0-3/P0-5 fix). "
                "Install: pip install cryptography pyzipper"
            )

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        # SEC-06: OWASP 2023 minimum is 310,000 iterations for PBKDF2-HMAC-SHA256
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=310000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return key

    def _encrypt_with_fernet(self, data: bytes, password: str) -> bytes:
        salt = os.urandom(16)
        key = self._derive_key(password, salt)
        fernet = Fernet(key)
        encrypted_data = fernet.encrypt(data)
        return salt + encrypted_data

    def _decrypt_with_fernet(self, encrypted_data: bytes, password: str) -> bytes | None:
        try:
            salt = encrypted_data[:16]
            encrypted = encrypted_data[16:]
            key = self._derive_key(password, salt)
            fernet = Fernet(key)
            return fernet.decrypt(encrypted)
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return None

    def _create_zip(self, source_path: Path, output_path: Path) -> bool:
        try:
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _dirs, files in os.walk(source_path):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(source_path)
                        zipf.write(file_path, arcname)
            return True
        except Exception as e:
            logger.error(f"Failed to create ZIP: {e}")
            return False

    def _create_encrypted_zip(self, source_path: Path, output_path: Path, password: str) -> bool:
        # Priority: CryptoKit (M1 native) > pyzipper > Fernet
        if CRYPTOKIT_AVAILABLE:
            return self._create_zip_cryptokit(source_path, output_path, password)
        elif PYZIPPER_AVAILABLE:
            return self._create_zip_pyzipper(source_path, output_path, password)
        elif CRYPTO_AVAILABLE:
            return self._create_zip_fernet(source_path, output_path, password)
        else:
            # This should never happen due to __init__ check, but guard anyway
            raise RuntimeError("No encryption backend available")

    def _create_zip_pyzipper(self, source_path: Path, output_path: Path, password: str) -> bool:
        try:
            with pyzipper.AESZipFile(
                output_path,
                'w',
                encryption=pyzipper.WZ_AES,
                compression=pyzipper.ZIP_DEFLATED
            ) as zipf:
                zipf.setpassword(password.encode())
                for root, _dirs, files in os.walk(source_path):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(source_path)
                        zipf.write(file_path, arcname)
            return True
        except Exception as e:
            logger.error(f"Failed to create encrypted ZIP with pyzipper: {e}")
            return False

    def _create_zip_cryptokit(self, source_path: Path, output_path: Path, password: str) -> bool:
        """
        M1-native encryption using CryptoKit AES-GCM via Swift helper.

        Hardware-accelerated AES-GCM provides:
        - ~3x faster than pure Python cryptography on M1
        - Constant-time decryption verification
        - Hardware-backed key derivation
        """
        import subprocess

        temp_path = None
        try:
            # Create temporary ZIP
            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip', dir=_get_tempdir()) as temp_file:
                temp_path = Path(temp_file.name)

            if not self._create_zip(source_path, temp_path):
                if temp_path and temp_path.exists():
                    os.unlink(temp_path)
                return False

            # Read ZIP and encrypt via CryptoKit
            with open(temp_path, 'rb') as f:
                zip_data = f.read()

            # Call Swift helper for CryptoKit AES-GCM encryption (reads plaintext from stdin)
            repo_root = Path(__file__).parent.parent
            helper_path = repo_root / "tools" / "secure_enclave_helper" / ".build" / "release" / "secure-enclave-helper"

            cmd = [str(helper_path), "cryptokit-encrypt", "--password", password, "--output", str(output_path)]
            result = subprocess.run(
                cmd,
                input=zip_data,
                capture_output=True,
                timeout=30
            )

            os.unlink(temp_path)

            if result.returncode != 0:
                logger.error(f"CryptoKit encryption failed: {result.stderr}")
                return False

            return True

        except subprocess.TimeoutExpired:
            logger.error("CryptoKit encryption timed out")
            if temp_path and temp_path.exists():
                os.unlink(temp_path)
            return False
        except Exception as e:
            logger.error(f"Failed to create encrypted ZIP with CryptoKit: {e}")
            if temp_path and temp_path.exists():
                os.unlink(temp_path)
            return False

    def _create_zip_fernet(self, source_path: Path, output_path: Path, password: str) -> bool:
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip', dir=_get_tempdir()) as temp_file:
                temp_path = Path(temp_file.name)

            if not self._create_zip(source_path, temp_path):
                # temp_path created but zip failed — clean up before returning
                if temp_path is not None and temp_path.exists():
                    os.unlink(temp_path)
                return False

            with open(temp_path, 'rb') as f:
                zip_data = f.read()

            encrypted_data = self._encrypt_with_fernet(zip_data, password)

            with open(output_path, 'wb') as f:
                f.write(encrypted_data)

            os.unlink(temp_path)
            return True
        except Exception as e:
            logger.error(f"Failed to create encrypted ZIP with fernet: {e}")
            if temp_path is not None and temp_path.exists():
                os.unlink(temp_path)
            return False

    def _shred_directory(self, path: Path, passes: int = 3) -> bool:
        if not path.exists():
            return True

        try:
            for root, dirs, files in os.walk(path, topdown=False):
                for file in files:
                    file_path = Path(root) / file
                    try:
                        size = file_path.stat().st_size
                        with open(file_path, 'r+b') as f:
                            for _ in range(passes):
                                f.seek(0)
                                f.write(os.urandom(size))
                                f.flush()
                                os.fsync(f.fileno())
                        os.unlink(file_path)
                    except Exception as e:
                        logger.warning(f"Failed to shred {file_path}: {e}")
                        os.unlink(file_path)

                for dir_name in dirs:
                    dir_path = Path(root) / dir_name
                    try:
                        os.rmdir(dir_path)
                    except Exception:
                        pass

            try:
                os.rmdir(path)
            except Exception as e:
                logger.warning(f"Failed to remove directory {path}: {e}")

            return True
        except Exception as e:
            logger.error(f"Error shredding directory: {e}")
            return False

    def secure_export(self, output_dir: str, password: str, archive_name: str | None = None) -> str | None:
        """
        Create encrypted ZIP archive of vault contents and shred original.

        Encrypts vault_path contents using (in priority order):
          1. CryptoKit AES-GCM (M1 native) — requires Swift helper
          2. pyzipper AES (WZ_AES) — requires pyzipper
          3. Fernet (cryptography) — requires cryptography

        Result is a .enc file, then original vault directory is securely shredded.

        P0-3/P0-5 fix: XOR fallback removed — module fails at init if no real crypto available.

        Args:
            output_dir: Destination directory for the encrypted archive
            password: Encryption password
            archive_name: Optional output filename (default: ghostvault_{timestamp}.enc)

        Returns:
            Path to encrypted archive, or None on failure
        """
        if not self.vault_path.exists():
            logger.error(f"Vault path does not exist: {self.vault_path}")
            return None

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        if archive_name is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archive_name = f"ghostvault_{timestamp}.enc"

        output_file = output_path / archive_name

        if not self._create_encrypted_zip(self.vault_path, output_file, password):
            logger.error("Failed to create encrypted export")
            return None

        if not self._shred_directory(self.vault_path):
            logger.warning("Failed to completely shred vault contents")

        logger.info(f"Secure export completed: {output_file}")
        return str(output_file)

    def decrypt_export(self, encrypted_path: str, password: str, output_dir: str) -> str | None:
        encrypted_file = Path(encrypted_path)
        if not encrypted_file.exists():
            logger.error(f"Encrypted file does not exist: {encrypted_file}")
            return None

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        try:
            with open(encrypted_file, 'rb') as f:
                encrypted_data = f.read()

            # P0-3/P0-5 fix: removed FALLBACK_ENC support — XOR-encrypted exports no longer valid
            if encrypted_data.startswith(b'FALLBACK_ENC:'):
                logger.error("FALLBACK_ENC export detected — XOR fallback removed, cannot decrypt")
                return None

            # Format sniffing: ZIP AES vs CryptoKit vs Fernet blob
            # Priority: ZIP (pyzipper) → CryptoKit → Fernet
            # - ZIP: PK\x03\x04 header
            # - CryptoKit: salt(16) + combined (nonce+ciphertext+tag)
            # - Fernet: base64-like (starts with 'gAAAAA' or similar)
            if encrypted_data[:4] == b'PK\x03\x04':
                # ZIP container — try pyzipper first if available
                if PYZIPPER_AVAILABLE:
                    return self._decrypt_pyzipper(encrypted_file, password, output_path)
                else:
                    logger.error("ZIP archive but pyzipper unavailable — cannot decrypt")
                    return None
            elif CRYPTOKIT_AVAILABLE and len(encrypted_data) > 16:
                # CryptoKit format: salt (16 bytes) at start, not base64
                # Check for CryptoKit by verifying salt + nonce structure
                try:
                    result = self._decrypt_cryptokit(encrypted_data, password, output_path)
                    if result:
                        return result
                except Exception:
                    pass  # Fall through to next format
            if CRYPTO_AVAILABLE:
                # Fernet blob or other cryptography format
                return self._decrypt_fernet(encrypted_data, password, output_path)
            else:
                logger.error("No decryption method available")
                return None
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return None

    @staticmethod
    def _safe_extractall(zf, extract_to: Path) -> None:
        """
        Extract ZIP with zip-slip and path-traversal protection.

        Works with zipfile.ZipFile and pyzipper.AESZipFile (both share namelist/extractall).

        Rejects:
        - NUL bytes in member names
        - Absolute paths
        - Any ".." path segment
        - Resolved paths outside extract_to
        """
        extract_to = extract_to.resolve()
        for member in zf.namelist():
            if "\x00" in member:
                raise zipfile.BadZipFile(f"NUL byte in member name: {member!r}")
            if os.path.isabs(member):
                raise zipfile.BadZipFile(f"Absolute path in ZIP: {member}")
            parts = member.split("/")
            if ".." in parts:
                raise zipfile.BadZipFile(f"Path traversal attempt: {member}")
            member_path = (extract_to / member).resolve()
            if not member_path.is_relative_to(extract_to):
                raise zipfile.BadZipFile(f"Path traversal attempt: {member}")
        zf.extractall(extract_to)

    def _decrypt_fernet(self, encrypted_data: bytes, password: str, output_path: Path) -> str | None:
        temp_path = None
        try:
            decrypted = self._decrypt_with_fernet(encrypted_data, password)
            if not decrypted:
                return None

            extract_path = output_path / "decrypted_vault"
            extract_path.mkdir(exist_ok=True)

            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip', dir=_get_tempdir()) as temp_file:
                temp_path = Path(temp_file.name)

            temp_path.write_bytes(decrypted)

            with zipfile.ZipFile(temp_path, 'r') as zipf:
                LootManager._safe_extractall(zipf, extract_path)

            os.unlink(temp_path)
            return str(extract_path)
        except Exception as e:
            logger.error(f"Fernet decryption failed: {e}")
            if temp_path is not None and temp_path.exists():
                os.unlink(temp_path)
            return None

    def _decrypt_cryptokit(self, encrypted_data: bytes, password: str, output_path: Path) -> str | None:
        """Decrypt data encrypted with CryptoKit AES-GCM via Swift helper."""
        import subprocess
        temp_path = None
        try:
            extract_path = output_path / "decrypted_vault"
            extract_path.mkdir(exist_ok=True)

            # Write encrypted data to temp file for Swift helper
            with tempfile.NamedTemporaryFile(delete=False, suffix='.enc', dir=_get_tempdir()) as temp_file:
                temp_file.write(encrypted_data)
                temp_path = Path(temp_file.name)

            # Call Swift helper for CryptoKit AES-GCM decryption
            repo_root = Path(__file__).parent.parent
            helper_path = repo_root / "tools" / "secure_enclave_helper" / ".build" / "release" / "secure-enclave-helper"
            decrypt_output = output_path / "decrypted.zip"

            cmd = [
                str(helper_path), "cryptokit-decrypt",
                "--password", password,
                "--input", str(temp_path),
                "--output", str(decrypt_output)
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)

            os.unlink(temp_path)

            if result.returncode != 0:
                logger.error(f"CryptoKit decryption failed: {result.stderr}")
                return None

            # Extract ZIP
            with zipfile.ZipFile(decrypt_output, 'r') as zipf:
                LootManager._safe_extractall(zipf, extract_path)

            os.unlink(decrypt_output)
            return str(extract_path)
        except subprocess.TimeoutExpired:
            logger.error("CryptoKit decryption timed out")
            if temp_path and temp_path.exists():
                os.unlink(temp_path)
            return None
        except Exception as e:
            logger.error(f"CryptoKit decryption failed: {e}")
            if temp_path and temp_path.exists():
                os.unlink(temp_path)
            return None

    def _decrypt_pyzipper(self, encrypted_file: Path, password: str, output_path: Path) -> str | None:
        try:
            extract_path = output_path / "decrypted_vault"
            extract_path.mkdir(exist_ok=True)

            with pyzipper.AESZipFile(encrypted_file) as zipf:
                zipf.setpassword(password.encode())
                LootManager._safe_extractall(zipf, extract_path)

            return str(extract_path)
        except Exception as e:
            logger.error(f"Pyzipper decryption failed: {e}")
            return None



# =============================================================================
# ALIAS — Authority clarity
# =============================================================================
# "LootManager" evokes loot/stolen goods, not secure vault export.
# VaultManager is the semantically correct name; LootManager preserved for compat.
VaultManager = LootManager
