"""
Encryption utilities for Hledac Universal Platform
AES-256-GCM encryption for secure data storage
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class EncryptionResult:
    """Result of encryption operation"""
    ciphertext: str
    nonce: str
    tag: str
    success: bool = True
    error: str | None = None


@dataclass
class DecryptionResult:
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

    def __init__(self, key: bytes | None = None):
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
            # Use cryptography library - XOR fallback has been removed
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            nonce = secrets.token_bytes(12)  # 96-bit nonce for GCM
            encryptor = Cipher(
                algorithms.AES(self.key),
                modes.GCM(nonce)
            ).encryptor()

            ciphertext = encryptor.update(plaintext.encode()) + encryptor.finalize()
            tag = encryptor.tag  # 128-bit auth tag

            return EncryptionResult(
                ciphertext=base64.b64encode(ciphertext).decode(),
                nonce=base64.b64encode(nonce).decode(),
                tag=base64.b64encode(tag).decode()
            )

        except ImportError:
            logger.error("cryptography library not available - encryption unavailable")
            return EncryptionResult(
                ciphertext="",
                nonce="",
                tag="",
                success=False,
                error="cryptography library required but not available"
            )
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return EncryptionResult(
                ciphertext="",
                nonce="",
                tag="",
                success=False,
                error=str(e)
            )

    def decrypt(self, result: EncryptionResult) -> DecryptionResult:
        """
        Decrypt ciphertext using AES-256-GCM.

        Args:
            result: EncryptionResult from encrypt()

        Returns:
            DecryptionResult with plaintext
        """
        try:
            # XOR fallback has been removed - only AES-GCM supported
            if result.tag == "fallback":
                logger.error("XOR fallback removed - cannot decrypt legacy data")
                return DecryptionResult(
                    plaintext="",
                    success=False,
                    error="XOR fallback has been removed - cannot decrypt legacy data"
                )

            # AES-GCM decryption
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            ciphertext = base64.b64decode(result.ciphertext)
            nonce = base64.b64decode(result.nonce)
            tag = base64.b64decode(result.tag)

            decryptor = Cipher(
                algorithms.AES(self.key),
                modes.GCM(nonce, tag)
            ).decryptor()

            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
            return DecryptionResult(plaintext=plaintext.decode())

        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return DecryptionResult(
                plaintext="",
                success=False,
                error=str(e)
            )

    @staticmethod
    def generate_key_b64() -> str:
        """Generate a new base64-encoded encryption key"""
        return base64.b64encode(secrets.token_bytes(32)).decode()
