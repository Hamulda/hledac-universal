"""
SHA-256 hardware acceleration via crypto_accelerate Rust module.

Hardware path (M1/M2/M3/M4 Apple Silicon):
    - sha2 crate with ARM NEON crypto instructions (sha256g, sha256h)
    - ~3× faster than pure-Scalar SHA-256
    - No FIPS compliance cost — sha2 is NIST compliant

Note: CommonCrypto (CC_SHA256) was removed in macOS 26+. The sha2 crate's
ARM NEON ASM path is hardware-accelerated and available on all Apple Silicon chips.

Python fallback: hashlib.sha256() for compatibility.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "batch_sha256",
    "batch_sha256_hw",
    "batch_sha256_hw_sync",
    "sha256_hex",
    "sha256_hex_sync",
    "get_crypto_domain",
]

logger = logging.getLogger(__name__)

# Lazy singleton for Rust raw accessor
_rust_crypto: Any = None


def _get_rust_crypto() -> Any:
    """Get the crypto_accelerate Rust module lazily."""
    global _rust_crypto
    if _rust_crypto is None:
        try:
            from _core.rust_backend import rust

            _rust_crypto = rust.raw.crypto_accelerate
        except Exception:
            _rust_crypto = None
    return _rust_crypto


class _RustCryptoDomain:
    """
    Hardware-accelerated SHA-256 domain.

    Uses the sha2 crate with ARM NEON crypto instructions on Apple Silicon,
    providing ~3× speedup over pure Python hashlib.

    Falls back to Python hashlib when Rust is unavailable.
    """

    __slots__ = ("_raw",)

    def __init__(self, ext: Any) -> None:
        # ext is probe.ext — may be None if Rust unavailable
        self._raw = ext.crypto_accelerate if ext is not None else None

    def batch_sha256_hw(self, items: Sequence[str]) -> list[str]:
        """
        Batch SHA-256 hardware acceleration.

        Args:
            items: List of strings to hash

        Returns:
            List of 64-char hex SHA-256 digests

        Performance:
            - < 128 items: serial (no thread pool overhead)
            - >= 128 items: rayon parallel, releases GIL
        """
        if self._raw is None:
            # Python fallback
            return [hashlib.sha256(item.encode()).hexdigest() for item in items]
        try:
            return self._raw.batch_sha256_hw(list(items))
        except Exception:
            # Graceful fallback on any Rust error
            return [hashlib.sha256(item.encode()).hexdigest() for item in items]

    def batch_encrypt_aes_gcm(self, password: str, salt: bytes, items: Sequence[str]) -> list[bytes]:
        """
        Batch AES-256-GCM encryption.

        Args:
            password: Encryption password
            salt: 16-byte salt (prepends zeros if shorter, truncates if longer)
            items: List of plaintext strings to encrypt

        Returns:
            List of encrypted blobs: nonce (12) || tag (16) || ciphertext

        Performance:
            - < 32 items: serial
            - >= 32 items: rayon parallel
        """
        if self._raw is None:
            return []
        try:
            return self._raw.batch_encrypt_aes_gcm(password, list(salt), list(items))
        except Exception:
            return []

    def batch_decrypt_aes_gcm(self, password: str, salt: bytes, items: Sequence[bytes]) -> list[str | None]:
        """
        Batch AES-256-GCM decryption.

        Args:
            password: Decryption password
            salt: 16-byte salt (same processing as encrypt)
            items: List of encrypted blobs

        Returns:
            List of decrypted plaintext strings on success, None on failure.
            Item-level error handling — one bad item doesn't fail the batch.
        """
        if self._raw is None:
            return [None] * len(items)
        try:
            return self._raw.batch_decrypt_aes_gcm(password, list(salt), list(items))
        except Exception:
            return [None] * len(items)


class _PythonCryptoDomain:
    """
    Pure Python SHA-256 fallback when Rust is unavailable.
    """

    __slots__ = ()

    def batch_sha256_hw(self, items: Sequence[str]) -> list[str]:
        """Batch SHA-256 via hashlib (pure Python fallback)."""
        return [hashlib.sha256(item.encode()).hexdigest() for item in items]

    def batch_encrypt_aes_gcm(self, password: str, salt: bytes, items: Sequence[str]) -> list[bytes]:
        """AES-GCM not available in Python fallback."""
        return []

    def batch_decrypt_aes_gcm(self, password: str, salt: bytes, items: Sequence[bytes]) -> list[str | None]:
        """AES-GCM not available in Python fallback."""
        return [None] * len(items)


def get_domain(ext: Any) -> _RustCryptoDomain | _PythonCryptoDomain:
    """
    Get the appropriate crypto domain based on Rust availability.

    Args:
        ext: The Rust extension module (probe.ext), or None

    Returns:
        _RustCryptoDomain if Rust crypto_accelerate is available,
        _PythonCryptoDomain otherwise
    """
    if ext is not None and hasattr(ext, "crypto_accelerate"):
        try:
            # Verify the module has the expected functions
            ca = ext.crypto_accelerate
            if hasattr(ca, "batch_sha256_hw"):
                logger.debug("[crypto] Using hardware-accelerated SHA-256 (sha2 ARM NEON)")
                return _RustCryptoDomain(ext)
        except Exception:
            pass

    logger.debug("[crypto] Using pure Python SHA-256 fallback (hashlib)")
    return _PythonCryptoDomain()


async def batch_sha256(items: Sequence[str]) -> list[str]:
    """
    Async batch SHA-256 with hardware acceleration.

    This is the RECOMMENDED entry point for async code paths.

    Args:
        items: List of strings to hash

    Returns:
        List of 64-char hex SHA-256 digests

    Example:
        >>> hashes = await batch_sha256(["hello", "world"])
        >>> len(hashes)
        2
    """
    domain = get_domain(_get_rust_crypto())
    return await asyncio.to_thread(domain.batch_sha256_hw, list(items))


async def batch_sha256_hw(items: Sequence[str]) -> list[str]:
    """
    Alias for batch_sha256 — hardware-accelerated batch SHA-256.

    Kept for backward compatibility with existing callers.
    """
    return await batch_sha256(items)


def batch_sha256_hw_sync(items: Sequence[str]) -> list[str]:
    """
    Synchronous batch SHA-256 with hardware acceleration.

    This is the RECOMMENDED entry point for synchronous code paths
    (e.g., in non-async functions, __init__, or callbacks).

    Args:
        items: List of strings to hash

    Returns:
        List of 64-char hex SHA-256 digests

    Performance:
        - < 128 items: serial (no thread pool overhead)
        - >= 128 items: rayon parallel (releases GIL)

    Example:
        >>> hashes = batch_sha256_hw_sync(["hello", "world"])
        >>> len(hashes)
        2
    """
    domain = get_domain(_get_rust_crypto())
    return domain.batch_sha256_hw(list(items))


def sha256_hex_sync(data: str | bytes, truncate: int | None = None) -> str:
    """
    Synchronous SHA-256 hex digest.

    This is the RECOMMENDED entry point for single-item synchronous hashing.

    Args:
        data: String or bytes to hash
        truncate: Optional truncation length (truncates from 64 to N chars)

    Returns:
        SHA-256 hex digest (64 chars, or N if truncate is set)
    """
    if isinstance(data, str):
        data = data.encode()
    result = hashlib.sha256(data).hexdigest()
    if truncate is not None:
        return result[:truncate]
    return result


def sha256_hex(data: str | bytes, truncate: int | None = None) -> str:
    """
    Compute SHA-256 hex digest.

    Args:
        data: String or bytes to hash
        truncate: Optional truncation length (truncates from 64 to N chars)

    Returns:
        SHA-256 hex digest (64 chars, or N if truncate is set)
    """
    if isinstance(data, str):
        data = data.encode()
    result = hashlib.sha256(data).hexdigest()
    if truncate is not None:
        return result[:truncate]
    return result
