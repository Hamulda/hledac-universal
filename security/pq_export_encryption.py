"""
Post-Quantum HPKE Export Encryption — X-Wing ML-KEM-768 X25519.

HPKE (Hybrid Public Key Encryption) provides post-quantum encryption for
exported evidence bundles using CryptoKit's X-Wing scheme on macOS 26+.

Architecture:
- PostQuantumExportBackend Protocol: interface for PQ export implementations
- NullPostQuantumExportBackend: always-unavailable stub (import-safe, sprint-safe)
- ExportEncryptionEnvelope dataclass: encrypted bundle metadata
- HPKEExportBackend: calls Swift helper for HPKE operations

Export semantic:
  - recipient_public_key_b64: identity key for designated recipient
  - encapsulated_key_b64: ML-KEM-768/KEX key encapsulation
  - ciphertext_b64: AES-256-GCM encrypted payload
  - aad_hash: SHA-256 of AAD (additional authenticated data) for binding
  - mode: X-Wing ML-KEM-768 X25519 SHA256 AES GCM 256

Fail-soft:
  - PQ unavailable → PQ_EXPORT_ENCRYPTION_UNAVAILABLE if policy requires PQ
  - Otherwise → unencrypted export only if policy allows
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class HPKEAvailability(Enum):
    """Truthful HPKE availability states."""
    DISABLED = "disabled"           # Feature flag off
    UNAVAILABLE = "unavailable"     # Backend not available/import failed
    AVAILABLE = "available"          # Backend loaded and functional
    ENCRYPTED = "encrypted"         # Backend encrypted export
    FAIL_SOFT = "fail_soft"        # Backend raised but was caught


class ExportPolicy(Enum):
    """Export encryption policy."""
    PQ_REQUIRED = "pq_required"     # Must encrypt with PQ, fail if unavailable
    PQ_PREFERRED = "pq_preferred"   # Encrypt with PQ if available, else unencrypted
    UNENCRYPTED_ONLY = "unencrypted_only"  # Never encrypt (legacy bundles)


class Decryptability(Enum):
    """Truthful decryptability states for production envelopes."""
    PERSISTENT_KEYCHAIN = "persistent_keychain"  # Key available in persistent keychain
    EPHEMERAL_TEST_ONLY = "ephemeral_test_only"  # Only for explicit test roundtrips
    UNSUPPORTED = "unsupported"  # PQ not available or key lifecycle unavailable


@dataclass
class ExportEncryptionEnvelope:
    """
    Encrypted export bundle envelope — PRODUCTION safe, no private key material.

    Fields:
    - mode: HPKE mode identifier (X-Wing ML-KEM-768 X25519 SHA256 AES GCM 256)
    - encapsulated_key_b64: ML-KEM-768 encapsulated key (base64)
    - aad_hash: SHA-256 of AAD for integrity binding (hex)
    - aad_b64: Original AAD bytes (base64) — needed for HPKE decrypt
    - ciphertext_b64: AES-256-GCM ciphertext (base64)
    - recipient_public_key_b64: recipient identity public key (base64) — for verification
    - recipient_key_id: key identifier for persistent keychain lookup
    - recipient_public_key_fingerprint: SHA-256 fingerprint of recipient public key (hex)
    - decryptability: truthy decryptability state (persistent_keychain | ephemeral_test_only | unsupported)
    - pq: whether post-quantum encryption was applied
    - created_at: ISO-8601 timestamp
    - backend: backend name that produced this envelope

    NEVER: recipient_private_key_b64, private key in to_dict(), private key in repr/logging
    """
    mode: str = "PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256"
    encapsulated_key_b64: str = ""
    aad_hash: str = ""
    aad_b64: str = ""
    ciphertext_b64: str = ""
    recipient_public_key_b64: str = ""
    recipient_key_id: str = ""
    recipient_public_key_fingerprint: str = ""
    decryptability: Decryptability = Decryptability.UNSUPPORTED
    pq: bool = False
    created_at: str = ""
    backend: str = "null"

    def to_dict(self) -> dict:
        """Serialize envelope — NEVER includes private key material."""
        return {
            "mode": self.mode,
            "encapsulated_key_b64": self.encapsulated_key_b64,
            "aad_hash": self.aad_hash,
            "aad_b64": self.aad_b64,
            "ciphertext_b64": self.ciphertext_b64,
            "recipient_public_key_b64": self.recipient_public_key_b64,
            "recipient_key_id": self.recipient_key_id,
            "recipient_public_key_fingerprint": self.recipient_public_key_fingerprint,
            "decryptability": self.decryptability.value,
            "pq": self.pq,
            "created_at": self.created_at,
            "backend": self.backend,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ExportEncryptionEnvelope:
        """Deserialize envelope — restores from production-safe dict."""
        decrypt_str = d.get("decryptability", "unsupported")
        try:
            decryptability = Decryptability(decrypt_str)
        except ValueError:
            decryptability = Decryptability.UNSUPPORTED

        return cls(
            mode=d.get("mode", "PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256"),
            encapsulated_key_b64=d.get("encapsulated_key_b64", ""),
            aad_hash=d.get("aad_hash", ""),
            aad_b64=d.get("aad_b64", ""),
            ciphertext_b64=d.get("ciphertext_b64", ""),
            recipient_public_key_b64=d.get("recipient_public_key_b64", ""),
            recipient_key_id=d.get("recipient_key_id", ""),
            recipient_public_key_fingerprint=d.get("recipient_public_key_fingerprint", ""),
            decryptability=decryptability,
            pq=d.get("pq", False),
            created_at=d.get("created_at", ""),
            backend=d.get("backend", "null"),
        )

    def is_encrypted(self) -> bool:
        """True if this envelope contains encrypted data."""
        return bool(self.ciphertext_b64 and self.encapsulated_key_b64)

    def __repr__(self) -> str:
        """Safe repr — no private key material."""
        return (
            f"ExportEncryptionEnvelope(mode={self.mode!r}, "
            f"pq={self.pq}, decryptability={self.decryptability.value}, "
            f"recipient_key_id={self.recipient_key_id!r}, "
            f"backend={self.backend!r})"
        )


@dataclass
class TestOnlyHPKERoundtripMaterial:
    """
    Test-only HPKE roundtrip material — NEVER used in production.

    This dataclass exists solely for local test roundtrips where the Swift helper
    generates a self-contained keypair. The private key is used only within the
    test process and is NEVER placed into a production ExportEncryptionEnvelope.

    Usage: explicit test helper path only.
    """
    public_key_b64: str
    private_key_b64: str  # test-only, ephemeral


@dataclass
class HPKEStatus:
    """Current status of the HPKE export backend."""
    availability: HPKEAvailability = HPKEAvailability.DISABLED
    backend_name: str = "null"
    error_message: str | None = None
    recipient_key_id: str | None = None
    encrypted_count: int = 0
    decrypted_count: int = 0


@runtime_checkable
class PostQuantumExportBackend(Protocol):
    """Protocol for post-quantum export backend implementations."""

    @property
    def name(self) -> str:
        """Backend identifier for telemetry."""
        ...

    def is_available(self) -> bool:
        """Check if backend is available (loaded and functional)."""
        ...

    def hpke_status(self) -> HPKEStatus:
        """Return current HPKE status snapshot."""
        ...

    def generate_recipient_key(self, key_id: str) -> tuple[str, str, str] | None:
        """
        Generate a recipient keypair and store in keychain.

        Args:
            key_id: Key identifier for the recipient key

        Returns:
            Tuple of (public_key_b64, key_id, fingerprint) or None on failure.
            Private key is stored in persistent keychain, never in envelope.
        """
        ...

    def encrypt_hpke(
        self,
        plaintext: bytes,
        aad: bytes,
        recipient_public_key_b64: str,
        recipient_key_id: str = "",
    ) -> ExportEncryptionEnvelope | None:
        """
        Encrypt plaintext using HPKE X-Wing.

        Args:
            plaintext: Raw bytes to encrypt
            aad: Additional authenticated data for integrity binding
            recipient_public_key_b64: Recipient's public key
            recipient_key_id: Optional key identifier for persistent keychain key

        Returns:
            ExportEncryptionEnvelope or None on failure
        """
        ...

    def decrypt_hpke(
        self,
        envelope: ExportEncryptionEnvelope,
        plaintext_placeholder: bytes,
        test_material: TestOnlyHPKERoundtripMaterial | None = None,
    ) -> bytes | None:
        """
        Decrypt HPKE-encrypted envelope.

        Args:
            envelope: Encrypted export envelope (production-safe, no private key)
            plaintext_placeholder: Expected plaintext size hint (for validation)
            test_material: Test-only roundtrip material. If provided, uses ephemeral
                          private key for local test decryption. Production path
                          requires persistent keychain (envelope.recipient_key_id).

        Returns:
            Decrypted bytes or None on failure
        """
        ...


class ExportEncryptionError(Exception):
    """Base exception for export encryption operations."""
    pass


class ExportEncryptionUnavailableError(ExportEncryptionError):
    """PQ export encryption is not available on this system."""
    pass


class NullPostQuantumExportBackend:
    """
    Always-unavailable stub backend.

    Used when:
    - Feature flag disabled
    - macOS < 26 (HPKE X-Wing not available)
    - External import failed
    - Hardware not supported

    This backend NEVER crashes on import and NEVER blocks sprint execution.
    """

    name: str = "null"
    _status: HPKEStatus = HPKEStatus(availability=HPKEAvailability.DISABLED)

    def is_available(self) -> bool:
        return False

    def hpke_status(self) -> HPKEStatus:
        return self._status

    def generate_recipient_key(self, key_id: str) -> tuple[str, str, str] | None:
        return None

    def encrypt_hpke(
        self,
        plaintext: bytes,
        aad: bytes,
        recipient_public_key_b64: str,
        recipient_key_id: str = "",
    ) -> ExportEncryptionEnvelope | None:
        return None

    def decrypt_hpke(
        self,
        envelope: ExportEncryptionEnvelope,
        plaintext_placeholder: bytes,
        test_material: TestOnlyHPKERoundtripMaterial | None = None,
    ) -> bytes | None:
        return None


def compute_aad_hash(aad: bytes) -> str:
    """Compute SHA-256 hash of additional authenticated data."""
    return hashlib.sha256(aad).hexdigest()


async def create_export_backend(
    enabled: bool = True,
    key_id: str = "com.hledac.pq.export.v1",
) -> tuple[PostQuantumExportBackend, HPKEStatus]:
    """
    Create appropriate HPKE export backend based on environment.

    Try loading Swift helper for HPKE X-Wing on macOS 26+.
    Fall back to NullPostQuantumExportBackend on any error.

    The Swift helper provides:
    - hpke-generate-recipient-key: Create recipient keypair
    - hpke-encrypt: Encrypt with HPKE X-Wing
    - hpke-decrypt: Decrypt with HPKE X-Wing

    Args:
        enabled: Whether to attempt loading real backend
        key_id: Default key ID for HPKE recipient operations

    Returns:
        Tuple of (backend, status) — always returns a valid backend
    """
    if not enabled:
        status = HPKEStatus(availability=HPKEAvailability.DISABLED)
        return NullPostQuantumExportBackend(), status

    # Try loading Swift helper-backed backend
    try:
        from .pq_export_encryption_swift import HPKEExportBackend

        backend = HPKEExportBackend(key_id=key_id)
        if backend.is_available():
            status = backend.hpke_status()
            return backend, status
        else:
            status = backend.hpke_status()
            return NullPostQuantumExportBackend(), status

    except ImportError as e:
        logger.debug(f"HPKEExportBackend not available: {e}")
        status = HPKEStatus(
            availability=HPKEAvailability.UNAVAILABLE,
            backend_name="null",
            error_message=f"Import failed: {e}",
        )
        return NullPostQuantumExportBackend(), status

    except Exception as e:
        logger.warning(f"HPKEExportBackend init failed: {e}")
        status = HPKEStatus(
            availability=HPKEAvailability.FAIL_SOFT,
            backend_name="null",
            error_message=str(e),
        )
        return NullPostQuantumExportBackend(), status


# Global backend instance for standalone functions
_export_backend: PostQuantumExportBackend | None = None
_export_status: HPKEStatus = HPKEStatus()


async def encrypt_export_bundle(
    plaintext: bytes,
    aad: bytes,
    recipient_public_key_b64: str,
    policy: ExportPolicy = ExportPolicy.PQ_REQUIRED,
    recipient_key_id: str = "",
) -> tuple[ExportEncryptionEnvelope | None, bool, str]:
    """
    Encrypt an export bundle using HPKE X-Wing.

    Args:
        plaintext: Raw bytes of the export bundle
        aad: Additional authenticated data for integrity binding
        recipient_public_key_b64: Base64-encoded recipient public key
        policy: Export policy controlling encryption behavior
        recipient_key_id: Optional key identifier for persistent keychain key

    Returns:
        Tuple of (envelope, was_encrypted, error_code_or_empty)
        - If encryption succeeded: (envelope, True, "")
        - If PQ unavailable with PQ_REQUIRED: (None, False, "PQ_EXPORT_ENCRYPTION_UNAVAILABLE")
        - If PQ unavailable with PQ_PREFERRED: (None, False, "")
        - If UNENCRYPTED_ONLY: (None, False, "")
    """
    global _export_backend, _export_status

    # Initialize backend if not done
    if _export_backend is None:
        _export_backend, _export_status = await create_export_backend()

    # Check policy
    if policy == ExportPolicy.UNENCRYPTED_ONLY:
        return None, False, ""

    # Check backend availability
    if not _export_backend.is_available():
        if policy == ExportPolicy.PQ_REQUIRED:
            return None, False, "PQ_EXPORT_ENCRYPTION_UNAVAILABLE"
        else:  # PQ_PREFERRED
            return None, False, ""

    # Attempt encryption
    envelope = _export_backend.encrypt_hpke(
        plaintext=plaintext,
        aad=aad,
        recipient_public_key_b64=recipient_public_key_b64,
        recipient_key_id=recipient_key_id,
    )

    if envelope is None:
        if policy == ExportPolicy.PQ_REQUIRED:
            return None, False, "PQ_EXPORT_ENCRYPTION_UNAVAILABLE"
        else:
            return None, False, ""

    return envelope, True, ""


async def decrypt_export_bundle(
    envelope: ExportEncryptionEnvelope,
    expected_size: int = 0,
    test_material: TestOnlyHPKERoundtripMaterial | None = None,
) -> tuple[bytes | None, str]:
    """
    Decrypt an HPKE-encrypted export bundle.

    Production path: requires envelope.recipient_key_id with persistent keychain.
    Test path: requires explicit test_material with ephemeral private key.

    Args:
        envelope: Encrypted export envelope (production-safe, no private key)
        expected_size: Expected plaintext size for validation (0 = no check)
        test_material: Test-only roundtrip material for ephemeral decryption.
                     Production envelopes must NOT carry private key material.

    Returns:
        Tuple of (plaintext, error_code_or_empty)
        - If decryption succeeded: (plaintext_bytes, "")
        - If envelope not encrypted: (None, "NOT_ENCRYPTED")
        - If persistence unavailable with no test material: (None, "PQ_HPKE_DECRYPT_PERSISTENCE_UNSUPPORTED")
        - If decryption failed: (None, "DECRYPTION_FAILED")
    """
    global _export_backend, _export_status

    # Initialize backend if not done
    if _export_backend is None:
        _export_backend, _export_status = await create_export_backend()

    # Check if envelope is encrypted
    if not envelope.is_encrypted():
        return None, "NOT_ENCRYPTED"

    # Production path: require persistent keychain
    if test_material is None:
        if envelope.decryptability != Decryptability.PERSISTENT_KEYCHAIN:
            return None, "PQ_HPKE_DECRYPT_PERSISTENCE_UNSUPPORTED"

    # Attempt decryption
    placeholder = b"\x00" * expected_size if expected_size > 0 else b""
    plaintext = _export_backend.decrypt_hpke(
        envelope=envelope,
        plaintext_placeholder=placeholder,
        test_material=test_material,
    )

    if plaintext is None:
        return None, "DECRYPTION_FAILED"

    # Size validation
    if expected_size > 0 and len(plaintext) != expected_size:
        return None, "SIZE_MISMATCH"

    return plaintext, ""
