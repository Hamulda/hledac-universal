"""
Secure Enclave abstraction — hardware-backed key/signing backend.

Apple Secure Enclave on M1 is NOT a text processing engine.
It provides hardware-backed key storage, signing, and audit integrity.
This module provides a fail-soft internal abstraction.

Architecture:
- SecureEnclaveBackend Protocol: interface for enclave implementations
- NullSecureEnclaveBackend: always-unavailable stub for production
- RealSecureEnclaveBackend: optional import from hledac.ultra_context

Usage in sprint path:
  1. Build canonical batch manifest (chunk_count, per-chunk hashes, batch_digest)
  2. Request one signature for the batch_digest (not per-chunk)
  3. Store signature in telemetry/sidecar
  4. Return chunks unchanged (chunks are NOT mutated)
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class EnclaveAvailability(Enum):
    """Truthful enclave availability states."""
    DISABLED = "disabled"           # Feature flag off
    UNAVAILABLE = "unavailable"     # Backend not available/import failed
    AVAILABLE = "available"         # Backend loaded
    SIGNED = "signed"              # Backend signed batch digest
    FAIL_SOFT = "fail_soft"        # Backend raised but was caught


@dataclass
class EnclaveStatus:
    """Current status of the secure enclave backend."""
    availability: EnclaveAvailability = EnclaveAvailability.DISABLED
    backend_name: str = "null"
    error_message: str | None = None
    signed_batch_digest: str | None = None
    chunk_count: int = 0


@dataclass
class SignedDigest:
    """A single Secure Enclave signature over a canonical batch digest."""
    batch_digest: str          # SHA-256 of canonical manifest
    signature: bytes           # Raw signature bytes
    backend_name: str          # Which backend produced this
    chunk_count: int           # How many chunks in the batch


@dataclass
class BatchManifest:
    """Canonical manifest for a chunk batch — used for signing."""
    chunk_count: int
    chunk_hashes: list[str]    # SHA-256 of each chunk (hex)
    batch_digest: str           # SHA-256 of concatenated hashes


@runtime_checkable
class SecureEnclaveBackend(Protocol):
    """Protocol for Secure Enclave backend implementations."""

    @property
    def name(self) -> str:
        """Backend identifier for telemetry."""
        ...

    def is_available(self) -> bool:
        """Check if backend is available (loaded and functional)."""
        ...

    async def sign_batch_digest(self, manifest: BatchManifest) -> SignedDigest:
        """
        Sign a canonical batch digest.

        The enclave signs exactly ONE digest per batch, not one per chunk.
        This is the correct semantic: hardware-backed attestation of
        "these N chunks existed at this point in time".

        Args:
            manifest: Canonical manifest with chunk hashes

        Returns:
            SignedDigest with signature bytes

        Raises:
            SecureEnclaveError: On signing failure (fail-soft callers must catch)
        """
        ...


class SecureEnclaveError(Exception):
    """Base exception for Secure Enclave operations."""
    pass


class NullSecureEnclaveBackend:
    """
    Always-unavailable stub backend.

    Used when:
    - Feature flag disabled
    - External import failed
    - Hardware not supported
    """

    name: str = "null"
    _status: EnclaveStatus = field(default_factory=lambda: EnclaveStatus(
        availability=EnclaveAvailability.DISABLED
    ))

    def is_available(self) -> bool:
        return False

    async def sign_batch_digest(self, manifest: BatchManifest) -> SignedDigest:
        raise SecureEnclaveError("Null backend cannot sign")


def build_batch_manifest(chunks: list[str]) -> BatchManifest:
    """
    Build canonical batch manifest from chunk list.

    Each chunk is hashed individually, then all hashes are concatenated
    and hashed again to produce a deterministic batch_digest.

    This is the input to sign_batch_digest().
    """
    chunk_hashes = []
    for chunk in chunks:
        h = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        chunk_hashes.append(h)

    # Canonical ordering: sorted by hash to ensure deterministic digest
    sorted_hashes = sorted(chunk_hashes)
    concat = "".join(sorted_hashes).encode("utf-8")
    batch_digest = hashlib.sha256(concat).hexdigest()

    return BatchManifest(
        chunk_count=len(chunks),
        chunk_hashes=chunk_hashes,
        batch_digest=batch_digest,
    )


async def create_secure_enclave_backend(
    enabled: bool = True,
) -> tuple[SecureEnclaveBackend, EnclaveStatus]:
    """
    Create appropriate Secure Enclave backend based on environment.

    Try loading hledac.ultra_context SecureEnclaveManager if available.
    Fall back to NullSecureEnclaveBackend on any error.

    Returns:
        Tuple of (backend, status) — always returns a valid backend
    """
    if not enabled:
        status = EnclaveStatus(availability=EnclaveAvailability.DISABLED)
        return NullSecureEnclaveBackend(), status

    # Try loading real backend
    try:
        from hledac.ultra_context.secure_enclave_manager import SecureEnclaveManager

        # Real backend is available
        backend = SecureEnclaveManager()
        status = EnclaveStatus(
            availability=EnclaveAvailability.AVAILABLE,
            backend_name="hledac.ultra_context.secure_enclave_manager",
        )
        return backend, status

    except ImportError as e:
        logger.debug(f"SecureEnclaveManager not available: {e}")
        status = EnclaveStatus(
            availability=EnclaveAvailability.UNAVAILABLE,
            backend_name="null",
            error_message=f"Import failed: {e}",
        )
        return NullSecureEnclaveBackend(), status

    except Exception as e:
        logger.warning(f"SecureEnclaveManager init failed: {e}")
        status = EnclaveStatus(
            availability=EnclaveAvailability.FAIL_SOFT,
            backend_name="null",
            error_message=str(e),
        )
        return NullSecureEnclaveBackend(), status