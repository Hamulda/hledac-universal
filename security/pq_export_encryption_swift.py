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
    ExportEncryptionEnvelope,
    HPKEAvailability,
    HPKEStatus,
    PostQuantumExportBackend,
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

    def generate_recipient_key(self, key_id: str) -> tuple[str, str] | None:
        """
        Generate a recipient keypair via the helper.

        Args:
            key_id: Key identifier for the recipient key

        Returns:
            Tuple of (public_key_b64, private_key_ref) or None on failure
        """
        result = _run_helper(["hpke-generate-recipient-key", "--key-id", key_id])
        if result is None or not result.get("ok", False):
            return None

        public_key_b64 = result.get("data", {}).get("public_key_b64", "")
        private_key_b64 = result.get("data", {}).get("private_key_b64", "")

        if not public_key_b64:
            return None

        return public_key_b64, private_key_b64

    def encrypt_hpke(
        self,
        plaintext: bytes,
        aad: bytes,
        recipient_public_key_b64: str,
    ) -> ExportEncryptionEnvelope | None:
        """
        Encrypt plaintext using HPKE X-Wing via the helper.

        Args:
            plaintext: Raw bytes to encrypt
            aad: Additional authenticated data for integrity binding
            recipient_public_key_b64: Recipient's public key

        Returns:
            ExportEncryptionEnvelope or None on failure
        """
        import base64

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

        envelope = ExportEncryptionEnvelope(
            mode="PQ-HPKE-XWingMLKEM768X25519-SHA256-AES-GCM-256",
            encapsulated_key_b64=encapsulated_key,
            aad_hash=compute_aad_hash(aad),
            aad_b64=base64.b64encode(aad).decode("ascii"),
            ciphertext_b64=ciphertext,
            recipient_public_key_b64=recipient_public_key_b64,
            created_at=datetime.now(timezone.utc).isoformat(),
            backend=self.name,
        )
        self._encrypted_count += 1
        return envelope

    def decrypt_hpke(
        self,
        envelope: ExportEncryptionEnvelope,
        plaintext_placeholder: bytes,
    ) -> bytes | None:
        """
        Decrypt HPKE-encrypted envelope via the helper.

        Note: For local/self-contained envelopes, the recipient_private_key_b64
        travels in the envelope. For distributed HPKE, the private key should be
        stored in a secrets manager and referenced by key_id instead.

        Args:
            envelope: Encrypted export envelope
            plaintext_placeholder: Expected plaintext size hint (for validation)

        Returns:
            Decrypted bytes or None on failure
        """
        import base64

        result = _run_helper([
            "hpke-decrypt",
            "--encapsulated-key-b64", envelope.encapsulated_key_b64,
            "--ciphertext-b64", envelope.ciphertext_b64,
            "--aad-b64", envelope.aad_b64,
            "--recipient-private-key-b64", envelope.recipient_private_key_b64,
        ])
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
