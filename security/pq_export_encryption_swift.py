"""
Swift-backed HPKE Export backend — calls the helper tool for HPKE X-Wing.

This module provides the actual HPKE encryption via the secure-enclave-helper
tool's HPKE commands when running on macOS 26+.

Architecture:
- HPKEExportBackend: calls helper tool for HPKE operations
- hpke-generate-recipient-key → key creation
- hpke-encrypt → HPKE X-Wing encryption
- hpke-decrypt → HPKE X-Wing decryption

Fail-soft throughout: any helper failure returns safe defaults.
"""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .pq_export_encryption import (
    Decryptability,
    ExportEncryptionEnvelope,
    HPKEAvailability,
    HPKEStatus,
    PostQuantumExportBackend,
    TestOnlyHPKERoundtripMaterial,
    compute_aad_hash,
)

logger = logging.getLogger(__name__)

# Path to the compiled Swift helper
HELPER_PATH = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tools/secure_enclave_helper/.build/release/secure-enclave-helper"


def _run_helper(command: list[str], timeout: float = 10.0) -> dict[str, Any] | None:
    """
    Run the secure-enclave-helper and return parsed JSON.

    Returns None on any failure (timeout, non-zero exit, bad JSON).
    """
    try:
        result = subprocess.run(
            [HELPER_PATH] + command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.debug(f"Helper exited {result.returncode}: {result.stderr}")
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        logger.debug(f"Helper failed: {e}")
        return None


@dataclass
class HPKEExportBackend:
    """
    HPKE export backend using the Swift secure-enclave-helper.

    Only active on macOS 26+ where HPKE X-Wing is available.
    Falls back gracefully when helper is unavailable or fails.
    """

    key_id: str = "com.hledac.pq.export.v1"
    _status: HPKEStatus = HPKEStatus(availability=HPKEAvailability.UNAVAILABLE)
    _encrypted_count: int = 0
    _decrypted_count: int = 0

    def is_available(self) -> bool:
        """Check if the Swift helper is available and HPKE X-Wing is supported."""
        result = _run_helper(["hpke-status"])
        if result is None:
            self._status = HPKEStatus(
                availability=HPKEAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message="Helper unavailable or HPKE X-Wing not supported",
            )
            return False

        if not result.get("ok", False):
            self._status = HPKEStatus(
                availability=HPKEAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message=result.get("message", "HPKE status check failed"),
            )
            return False

        hpke_available = result.get("data", {}).get("available", "false") == "true"
        pq_enabled = result.get("data", {}).get("pq", "false") == "true"
        if not hpke_available or not pq_enabled:
            self._status = HPKEStatus(
                availability=HPKEAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message="HPKE X-Wing not available on this macOS version",
            )
            return False

        self._status = HPKEStatus(
            availability=HPKEAvailability.AVAILABLE,
            backend_name="swift-helper",
            recipient_key_id=self.key_id,
        )
        return True

    def hpke_status(self) -> HPKEStatus:
        """Return current HPKE status snapshot."""
        return HPKEStatus(
            availability=self._status.availability,
            backend_name=self._status.backend_name,
            error_message=self._status.error_message,
            recipient_key_id=self._status.recipient_key_id,
            encrypted_count=self._encrypted_count,
            decrypted_count=self._decrypted_count,
        )

    def generate_recipient_key(self, key_id: str) -> tuple[str, str, str] | None:
        """
        Generate a recipient keypair and store in keychain.

        Args:
            key_id: Key identifier for the recipient key

        Returns:
            Tuple of (public_key_b64, key_id, fingerprint) or None on failure.
            The private key is stored in the keychain and referenced by key_id.
        """
        result = _run_helper(["hpke-generate-recipient-key", "--key-id", key_id])
        if result is None or not result.get("ok", False):
            return None

        public_key_b64 = result.get("data", {}).get("public_key_b64", "")
        returned_key_id = result.get("data", {}).get("key_id", key_id)
        fingerprint = result.get("data", {}).get("fingerprint", "")

        if not public_key_b64:
            return None

        return public_key_b64, returned_key_id, fingerprint

    def encrypt_hpke(
        self,
        plaintext: bytes,
        aad: bytes,
        recipient_public_key_b64: str,
        recipient_key_id: str = "",
    ) -> ExportEncryptionEnvelope | None:
        """
        Encrypt plaintext using HPKE X-Wing via the helper.

        Args:
            plaintext: Raw bytes to encrypt
            aad: Additional authenticated data for integrity binding
            recipient_public_key_b64: Recipient's public key
            recipient_key_id: Optional key identifier for persistent keychain key

        Returns:
            ExportEncryptionEnvelope or None on failure
        """
        import base64
        import hashlib

        result = _run_helper([
            "hpke-encrypt",
            "--plaintext-b64", base64.b64encode(plaintext).decode("ascii"),
            "--aad-b64", base64.b64encode(aad).decode("ascii"),
            "--recipient-key-b64", recipient_public_key_b64,
        ])
        if result is None or not result.get("ok", False):
            return None

        data = result.get("data", {})
        encapsulated_key = data.get("encapsulated_key_b64", "")
        ciphertext = data.get("ciphertext_b64", "")

        if not encapsulated_key or not ciphertext:
            return None

        # Compute public key fingerprint (SHA-256 hex of raw public key bytes)
        try:
            pubkey_bytes = base64.b64decode(recipient_public_key_b64)
            fingerprint = hashlib.sha256(pubkey_bytes).hexdigest()
        except Exception:
            fingerprint = ""

        envelope = ExportEncryptionEnvelope(
            mode="PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256",
            encapsulated_key_b64=encapsulated_key,
            aad_hash=compute_aad_hash(aad),
            aad_b64=base64.b64encode(aad).decode("ascii"),
            ciphertext_b64=ciphertext,
            recipient_public_key_b64=recipient_public_key_b64,
            recipient_key_id=recipient_key_id,
            recipient_public_key_fingerprint=fingerprint,
            decryptability=Decryptability.PERSISTENT_KEYCHAIN if recipient_key_id else Decryptability.UNSUPPORTED,
            pq=True,
            created_at=datetime.now(timezone.utc).isoformat(),
            backend=self.name,
        )
        self._encrypted_count += 1
        return envelope

    def decrypt_hpke(
        self,
        envelope: ExportEncryptionEnvelope,
        plaintext_placeholder: bytes,
        test_material: TestOnlyHPKERoundtripMaterial | None = None,
    ) -> bytes | None:
        """
        Decrypt HPKE-encrypted envelope via the helper.

        Production path: requires persistent keychain via envelope.recipient_key_id.
        Test path: requires explicit test_material with ephemeral private key.

        Args:
            envelope: Encrypted export envelope (production-safe, no private key)
            plaintext_placeholder: Expected plaintext size hint (for validation)
            test_material: Test-only roundtrip material for ephemeral decryption.

        Returns:
            Decrypted bytes or None on failure
        """
        import base64

        # Determine which private key to use
        if test_material is not None:
            # Test path: ephemeral private key from explicit test material
            private_key_b64 = test_material.private_key_b64
        else:
            # Production path: persistent keychain via recipient_key_id
            # The helper resolves recipient_key_id internally
            private_key_b64 = None

        # Build decrypt command
        cmd = [
            "hpke-decrypt",
            "--encapsulated-key-b64", envelope.encapsulated_key_b64,
            "--ciphertext-b64", envelope.ciphertext_b64,
            "--aad-b64", envelope.aad_b64,
        ]

        if private_key_b64 is not None:
            cmd.extend(["--recipient-private-key-b64", private_key_b64])
        elif envelope.recipient_key_id:
            cmd.extend(["--recipient-key-id", envelope.recipient_key_id])
        else:
            # No key source available — cannot decrypt
            return None

        result = _run_helper(cmd)
        if result is None or not result.get("ok", False):
            return None

        plaintext_b64 = result.get("data", {}).get("plaintext_b64", "")
        if not plaintext_b64:
            return None

        try:
            plaintext = base64.b64decode(plaintext_b64)
            self._decrypted_count += 1
            return plaintext
        except Exception:
            return None

    @property
    def name(self) -> str:
        return "swift-helper-hpke"
