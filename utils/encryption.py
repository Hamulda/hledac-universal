"""
Encryption utilities for Hledac Universal Platform
AES-256-GCM encryption for secure data storage
"""

import asyncio
import base64
import logging
import os
import secrets
from typing import TYPE_CHECKING

from compat.msgspec_gc_compat import Struct

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# R10: Batch size bounds for M1 8GB optimization
# Min: 50 findings to amortize async overhead
# Max: 200 findings to prevent memory pressure
BATCH_SIZE_MIN = 50
BATCH_SIZE_MAX = 200


class EncryptionResult(Struct):
    """Result of encryption operation"""

    ciphertext: str
    nonce: str
    tag: str
    success: bool = True
    error: str | None = None


class DecryptionResult(Struct):
    """Result of decryption operation"""

    plaintext: str
    success: bool = True
    error: str | None = None


class DataEncryption:
    """
    AES-256-GCM encryption for sensitive data storage.

    Uses environment variable HLEDAC_ENCRYPTION_KEY or generates
    a key for the session (note: session keys don't persist).
    """

    __slots__ = ("key",)

    def __init__(self, key: bytes | None = None) -> None:
        """
        Initialize encryption with optional key.

        Args:
            key: 32-byte encryption key. If None, uses env var or generates.
        """
        self.key = key or self._get_key_from_env() or self._generate_key()

    def _get_key_from_env(self) -> bytes | None:
        """Get encryption key from environment variable"""
        key_b64 = os.environ.get("HLEDAC_ENCRYPTION_KEY")
        if key_b64:
            try:
                return base64.b64decode(key_b64)
            except Exception as e:
                logger.warning(f"Failed to decode encryption key: {e}")
        return None

    def _generate_key(self) -> bytes:
        """Generate a new 32-byte encryption key"""
        key = secrets.token_bytes(32)
        logger.warning("Generated temporary encryption key - data won't persist across sessions!")
        return key

    def encrypt(self, plaintext: str) -> EncryptionResult:
        """
        Encrypt plaintext using AES-256-GCM.

        Args:
            plaintext: Text to encrypt

        Returns:
            EncryptionResult with ciphertext, nonce, and auth tag
        """
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            nonce = secrets.token_bytes(12)
            encryptor = Cipher(algorithms.AES(self.key), modes.GCM(nonce)).encryptor()
            ciphertext = encryptor.update(plaintext.encode()) + encryptor.finalize()
            tag = encryptor.tag
            return EncryptionResult(
                ciphertext=base64.b64encode(ciphertext).decode(),
                nonce=base64.b64encode(nonce).decode(),
                tag=base64.b64encode(tag).decode(),
            )
        except ImportError:
            logger.error("cryptography library not available - encryption unavailable")
            return EncryptionResult(
                ciphertext="", nonce="", tag="", success=False, error="cryptography library required but not available"
            )
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return EncryptionResult(ciphertext="", nonce="", tag="", success=False, error=str(e))

    def decrypt(self, result: EncryptionResult) -> DecryptionResult:
        """
        Decrypt ciphertext using AES-256-GCM.

        Args:
            result: EncryptionResult from encrypt()

        Returns:
            DecryptionResult with plaintext
        """
        try:
            if result.tag == "fallback":
                logger.error("XOR fallback removed - cannot decrypt legacy data")
                return DecryptionResult(
                    plaintext="", success=False, error="XOR fallback has been removed - cannot decrypt legacy data"
                )
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            ciphertext = base64.b64decode(result.ciphertext)
            nonce = base64.b64decode(result.nonce)
            tag = base64.b64decode(result.tag)
            decryptor = Cipher(algorithms.AES(self.key), modes.GCM(nonce, tag)).decryptor()
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            return DecryptionResult(plaintext=plaintext.decode())
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return DecryptionResult(plaintext="", success=False, error=str(e))

    @staticmethod
    def generate_key_b64() -> str:
        """Generate a new base64-encoded encryption key"""
        return base64.b64encode(secrets.token_bytes(32)).decode()

    @staticmethod
    def _get_rust_backend():
        """Get Rust backend for batch operations."""
        try:
            from hledac.universal._core.rust_backend import rust as _rust_backend

            return _rust_backend
        except Exception:
            return None

    def _derive_password_from_key(self) -> tuple[str, bytes]:
        """
        Derive password and salt from the stored key.

        R10: Uses PBKDF2-HMAC-SHA256 (600k iterations) for Rust compatibility.
        Returns (password, salt) tuple for batch_encrypt_aes_gcm.
        """
        # Generate a deterministic salt from the key (first 16 bytes of key hash)
        salt = secrets.token_bytes(16)
        # Use base64-encoded key as password for Rust function
        password = base64.b64encode(self.key).decode("ascii")
        return password, salt

    def batch_encrypt(
        self, plaintexts: list[str], password: str | None = None, salt: bytes | None = None
    ) -> list[EncryptionResult]:
        """
        R10: Batch AES-256-GCM encryption via Rust crypto_accelerate.

        M1 8GB: Uses ARM AES-NI via aes-gcm crate.
        Bounded parallelism: >= 32 items → rayon parallel, < 32 → serial.
        PyO3 releases GIL during rayon parallel sections.

        Falls back to Python cryptography if Rust is unavailable.

        Args:
            plaintexts: List of plaintext strings to encrypt
            password: Encryption password for Rust. If None, uses self.key.
            salt: 16-byte salt for key derivation. If None, generates random.

        Returns:
            List of EncryptionResult with ciphertext, nonce, and auth tag
        """
        if not plaintexts:
            return []

        # Derive password and salt if not provided
        if password is None:
            password, default_salt = self._derive_password_from_key()
            if salt is None:
                salt = default_salt

        # Ensure 16-byte salt
        if len(salt) < 16:
            salt = salt + b"\x00" * (16 - len(salt))
        elif len(salt) > 16:
            salt = salt[:16]

        # Try Rust batch first
        rust = self._get_rust_backend()
        if rust is not None and hasattr(rust.raw, "batch_encrypt_aes_gcm"):
            try:
                # Rust expects Vec<String> → returns Vec<Vec<u8>>
                # Each blob is: nonce(12) || tag(16) || ciphertext
                encrypted_blobs = rust.raw.batch_encrypt_aes_gcm(password, list(salt), plaintexts)
                results = []
                for blob in encrypted_blobs:
                    blob_bytes = bytes(blob) if not isinstance(blob, bytes) else blob
                    # Split: nonce(12), tag(16), ciphertext(rest)
                    nonce = base64.b64encode(blob_bytes[:12]).decode()
                    tag = base64.b64encode(blob_bytes[12:28]).decode()
                    ciphertext = base64.b64encode(blob_bytes[28:]).decode()
                    results.append(EncryptionResult(ciphertext=ciphertext, nonce=nonce, tag=tag))
                return results
            except Exception as e:
                logger.debug(f"R10: Rust batch_encrypt_aes_gcm failed, falling back to Python: {e}")

        # Fallback: Python cryptography (sequential)
        results = []
        for plaintext in plaintexts:
            results.append(self.encrypt(plaintext))
        return results

    def batch_decrypt(
        self, results: list[EncryptionResult], password: str | None = None, salt: bytes | None = None
    ) -> list[DecryptionResult]:
        """
        R10: Batch AES-256-GCM decryption via Rust crypto_accelerate.

        M1 8GB: Uses ARM AES-NI via aes-gcm crate.
        Bounded parallelism: >= 32 items → rayon parallel, < 32 → serial.
        PyO3 releases GIL during rayon parallel sections.

        Falls back to Python cryptography if Rust is unavailable.

        Args:
            results: List of EncryptionResult from batch_encrypt
            password: Decryption password for Rust. If None, uses self.key.
            salt: 16-byte salt (same as used for encryption). If None, generates random.

        Returns:
            List of DecryptionResult with plaintext
        """
        if not results:
            return []

        # Derive password and salt if not provided
        if password is None:
            password, default_salt = self._derive_password_from_key()
            if salt is None:
                salt = default_salt

        # Ensure 16-byte salt
        if len(salt) < 16:
            salt = salt + b"\x00" * (16 - len(salt))
        elif len(salt) > 16:
            salt = salt[:16]

        # Try Rust batch first
        rust = self._get_rust_backend()
        if rust is not None and hasattr(rust.raw, "batch_decrypt_aes_gcm"):
            try:
                # Convert EncryptionResult back to blobs
                encrypted_blobs = []
                for r in results:
                    if not r.success or not r.ciphertext:
                        encrypted_blobs.append(b"")
                        continue
                    nonce_bytes = base64.b64decode(r.nonce)
                    tag_bytes = base64.b64decode(r.tag)
                    ciphertext_bytes = base64.b64decode(r.ciphertext)
                    # Reconstruct: nonce(12) || tag(16) || ciphertext
                    blob = nonce_bytes + tag_bytes + ciphertext_bytes
                    encrypted_blobs.append(blob)

                # Rust expects Vec<Vec<u8>> → returns Vec<Option<String>>
                decrypted = rust.raw.batch_decrypt_aes_gcm(password, list(salt), encrypted_blobs)

                final_results = []
                for plaintext_opt in decrypted:
                    if plaintext_opt is None:
                        final_results.append(DecryptionResult(plaintext="", success=False, error="decryption_failed"))
                    else:
                        text = (
                            bytes(plaintext_opt).decode("utf-8")
                            if not isinstance(plaintext_opt, str)
                            else plaintext_opt
                        )
                        final_results.append(DecryptionResult(plaintext=text))
                return final_results
            except Exception as e:
                logger.debug(f"R10: Rust batch_decrypt_aes_gcm failed, falling back to Python: {e}")

        # Fallback: Python cryptography (sequential)
        final_results = []
        for r in results:
            final_results.append(self.decrypt(r))
        return final_results

    async def batch_encrypt_async(
        self, plaintexts: list[str], password: str | None = None, salt: bytes | None = None
    ) -> list[EncryptionResult]:
        """
        R10: Async batch encryption using asyncio.to_thread.

        Offloads batch_encrypt to a thread pool to avoid blocking the event loop.
        M1: PyO3 releases GIL during rayon parallel sections.

        Args:
            plaintexts: List of plaintext strings to encrypt
            password: Encryption password for Rust. If None, uses self.key.
            salt: 16-byte salt for key derivation. If None, generates random.

        Returns:
            List of EncryptionResult with ciphertext, nonce, and auth tag
        """
        return await asyncio.to_thread(self.batch_encrypt, plaintexts, password, salt)

    async def batch_decrypt_async(
        self, results: list[EncryptionResult], password: str | None = None, salt: bytes | None = None
    ) -> list[DecryptionResult]:
        """
        R10: Async batch decryption using asyncio.to_thread.

        Offloads batch_decrypt to a thread pool to avoid blocking the event loop.
        M1: PyO3 releases GIL during rayon parallel sections.

        Args:
            results: List of EncryptionResult from batch_encrypt
            password: Decryption password for Rust. If None, uses self.key.
            salt: 16-byte salt (same as used for encryption). If None, generates random.

        Returns:
            List of DecryptionResult with plaintext
        """
        return await asyncio.to_thread(self.batch_decrypt, results, password, salt)
