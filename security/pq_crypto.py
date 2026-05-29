"""
Post-Quantum cryptography abstraction — ML-DSA-65 hybrid signature support.

ML-DSA (Module-Lattice Digital Signature Algorithm) is for signatures.
ML-KEM (Key Encapsulation Mechanism) is for encryption only — NOT used here.

Architecture:
- PostQuantumBackend Protocol: interface for PQ implementations
- NullPostQuantumBackend: always-unavailable stub (import-safe, sprint-safe)
- PQStatus, PQSignature, HybridSignatureSet dataclasses

Signing semantic (hybrid):
  - P-256 is the primary/required signature
  - ML-DSA-65 is optional (macOS 26+ only, feature-detected)
  - Both are produced from the same canonical batch digest
  - One HybridSignatureSet per batch, not per chunk
  - Sprint proceeds if ML-DSA is unavailable (fail-soft)

Verification semantic:
  - P-256 must verify if present and marked required
  - ML-DSA verifies if present and marked optional
  - Missing optional ML-DSA = degraded, not failed
  - Invalid ML-DSA present = verification fails
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class PQAvailability(Enum):
    """Truthful PQ availability states."""
    DISABLED = "disabled"           # Feature flag off
    UNAVAILABLE = "unavailable"     # Backend not available/import failed
    AVAILABLE = "available"         # Backend loaded and functional
    SIGNED = "signed"              # Backend signed batch digest
    FAIL_SOFT = "fail_soft"        # Backend raised but was caught


class PQSecurityLevel(Enum):
    """ML-DSA security levels (NIST FIPS 204)."""
    ML_DSA_65 = 65   # ~128-bit security, recommended


@dataclass
class PQStatus:
    """Current status of the post-quantum backend."""
    availability: PQAvailability = PQAvailability.DISABLED
    backend_name: str = "null"
    error_message: str | None = None
    mldsa_key_id: str | None = None
    mldsa_level: int | None = None
    signed_batch_digest: str | None = None
    chunk_count: int = 0


@dataclass
class PQSignature:
    """A single ML-DSA signature over a canonical batch digest."""
    algorithm: str                  # "ml-dsa-65" or similar
    signature: bytes                # Raw ML-DSA signature bytes
    backend_name: str               # Which backend produced this
    security_level: int             # 65 for ML-DSA-65


@dataclass
class HybridSignatureSet:
    """
    Hybrid signature set containing P-256 + optional ML-DSA.

    P-256 is primary and required.
    ML-DSA-65 is optional (present only when backend is available on macOS 26+).
    Both signatures cover the same canonical batch digest.
    """
    batch_digest: str                # SHA-256 of canonical manifest
    p256_signature: bytes | None     # ECDSA P-256 signature (primary)
    p256_backend: str               # Which backend produced P-256
    mldsa_signature: PQSignature | None  # ML-DSA-65 signature (optional)
    chunk_count: int                # How many chunks in the batch

    @property
    def has_mldsa(self) -> bool:
        """True if ML-DSA signature is present."""
        return self.mldsa_signature is not None

    @property
    def is_hybrid(self) -> bool:
        """True if both P-256 and ML-DSA are present."""
        return self.p256_signature is not None and self.mldsa_signature is not None


@runtime_checkable
class PostQuantumBackend(Protocol):
    """Protocol for post-quantum backend implementations."""

    @property
    def name(self) -> str:
        """Backend identifier for telemetry."""
        ...

    def is_available(self) -> bool:
        """Check if backend is available (loaded and functional)."""
        ...

    def pq_status(self) -> PQStatus:
        """Return current PQ status snapshot."""
        ...

    def ensure_mldsa_key(self, key_id: str, level: int = 65) -> bool:
        """
        Ensure ML-DSA key exists for the given key_id.

        Args:
            key_id: Key identifier (e.g., com.hledac.pq.signing.v1)
            level: Security level (65 for ML-DSA-65)

        Returns:
            True if key is ready, False otherwise
        """
        ...

    def sign_mldsa_digest(self, key_id: str, digest: str, level: int = 65) -> PQSignature:
        """
        Sign a digest with ML-DSA.

        Args:
            key_id: Key identifier
            digest: Hex-encoded SHA-256 digest (64 chars)
            level: Security level (65 for ML-DSA-65)

        Returns:
            PQSignature with ML-DSA signature bytes

        Raises:
            PostQuantumError: On signing failure
        """
        ...

    def verify_mldsa_signature(
        self,
        digest: str,
        signature: bytes,
        public_key_bytes: bytes,
        level: int = 65
    ) -> bool:
        """
        Verify an ML-DSA signature.

        Args:
            digest: Hex-encoded SHA-256 digest
            signature: Raw ML-DSA signature bytes
            public_key_bytes: Raw public key bytes
            level: Security level (65 for ML-DSA-65)

        Returns:
            True if valid, False otherwise
        """
        ...


class PostQuantumError(Exception):
    """Base exception for post-quantum operations."""
    pass


class NullPostQuantumBackend:
    """
    Always-unavailable stub backend.

    Used when:
    - Feature flag disabled
    - macOS < 26 (ML-DSA not available)
    - External import failed
    - Hardware not supported

    This backend NEVER crashes on import and NEVER blocks sprint execution.
    """

    name: str = "null"
    _status: PQStatus = PQStatus(availability=PQAvailability.DISABLED)

    def is_available(self) -> bool:
        return False

    def pq_status(self) -> PQStatus:
        return self._status

    def ensure_mldsa_key(self, key_id: str, level: int = 65) -> bool:
        return False

    def sign_mldsa_digest(self, key_id: str, digest: str, level: int = 65) -> PQSignature:
        raise PostQuantumError("Null backend cannot sign ML-DSA")

    def verify_mldsa_signature(
        self,
        digest: str,
        signature: bytes,
        public_key_bytes: bytes,
        level: int = 65
    ) -> bool:
        return False


async def create_post_quantum_backend(
    enabled: bool = True,
    key_id: str = "com.hledac.pq.signing.v1",
) -> tuple[PostQuantumBackend, PQStatus]:
    """
    Create appropriate post-quantum backend based on environment.

    Try loading Swift helper for ML-DSA-65 on macOS 26+.
    Fall back to NullPostQuantumBackend on any error.

    The Swift helper (tools/secure_enclave_helper/) provides:
    - pq-status: Check ML-DSA availability
    - ensure-mldsa-key: Create ML-DSA key if needed
    - mldsa-sign-digest: Sign with ML-DSA-65
    - mldsa-verify: Verify ML-DSA-65 signature

    Args:
        enabled: Whether to attempt loading real backend
        key_id: Default key ID for ML-DSA operations

    Returns:
        Tuple of (backend, status) — always returns a valid backend
    """
    if not enabled:
        status = PQStatus(availability=PQAvailability.DISABLED)
        return NullPostQuantumBackend(), status

    # Try loading Swift helper-backed backend
    try:
        from .pq_crypto_swift import SwiftPostQuantumBackend

        backend = SwiftPostQuantumBackend(key_id=key_id)
        if backend.is_available():
            status = backend.pq_status()
            return backend, status
        else:
            status = backend.pq_status()
            return NullPostQuantumBackend(), status

    except ImportError as e:
        logger.debug(f"SwiftPostQuantumBackend not available: {e}")
        status = PQStatus(
            availability=PQAvailability.UNAVAILABLE,
            backend_name="null",
            error_message=f"Import failed: {e}",
        )
        return NullPostQuantumBackend(), status

    except Exception as e:
        logger.warning(f"SwiftPostQuantumBackend init failed: {e}")
        status = PQStatus(
            availability=PQAvailability.FAIL_SOFT,
            backend_name="null",
            error_message=str(e),
        )
        return NullPostQuantumBackend(), status
