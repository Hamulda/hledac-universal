"""
Swift-backed Post-Quantum backend — calls the helper tool for ML-DSA-65.

This module provides the actual ML-DSA signing via the secure-enclave-helper
tool's PQ commands when running on macOS 26+.

Architecture:
- SwiftPostQuantumBackend: calls helper tool for PQ operations
- pq-status → availability check
- ensure-mldsa-key → key creation
- mldsa-sign-digest → signing
- mldsa-verify → verification

Fail-soft throughout: any helper failure returns safe defaults.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

from .pq_crypto import (
    PQAvailability,
    PQSecurityLevel,
    PQStatus,
    PQSignature,
    PostQuantumBackend,
    PostQuantumError,
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
class SwiftPostQuantumBackend:
    """
    Post-quantum backend using the Swift secure-enclave-helper.

    Only active on macOS 26+ where ML-DSA-65 is available.
    Falls back gracefully when helper is unavailable or fails.
    """

    key_id: str = "com.hledac.pq.signing.v1"
    _status: PQStatus = field(default_factory=lambda: PQStatus(
        availability=PQAvailability.UNAVAILABLE
    ))

    def is_available(self) -> bool:
        """Check if the Swift helper is available and ML-DSA is supported."""
        result = _run_helper(["pq-status"])
        if result is None:
            self._status = PQStatus(
                availability=PQAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message="Helper unavailable or ML-DSA not supported",
            )
            return False

        if not result.get("ok", False):
            self._status = PQStatus(
                availability=PQAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message=result.get("message", "PQ status check failed"),
            )
            return False

        mldsa_available = result.get("data", {}).get("mldsa_available", "false") == "true"
        if not mldsa_available:
            self._status = PQStatus(
                availability=PQAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message="ML-DSA not available on this macOS version",
            )
            return False

        self._status = PQStatus(
            availability=PQAvailability.AVAILABLE,
            backend_name="swift-helper",
            mldsa_key_id=self.key_id,
            mldsa_level=65,
        )
        return True

    def pq_status(self) -> PQStatus:
        """Return current PQ status snapshot."""
        return self._status

    def ensure_mldsa_key(self, key_id: str, level: int = 65) -> bool:
        """
        Ensure ML-DSA key exists via the helper.

        Returns True if key is ready or already exists.
        """
        result = _run_helper(["ensure-mldsa-key", "--key-id", key_id])
        if result is None:
            return False
        return result.get("ok", False)

    def sign_mldsa_digest(self, key_id: str, digest: str, level: int = 65) -> PQSignature:
        """
        Sign a digest with ML-DSA-65 via the helper.

        Args:
            key_id: Key identifier
            digest: 64-character hex string (SHA-256 digest)
            level: Security level (65 for ML-DSA-65)

        Returns:
            PQSignature with ML-DSA signature bytes

        Raises:
            PostQuantumError: If signing fails
        """
        result = _run_helper([
            "mldsa-sign-digest",
            "--key-id", key_id,
            "--digest-hex", digest,
        ])
        if result is None or not result.get("ok", False):
            msg = result.get("message", "ML-DSA signing failed") if result else "Helper unavailable"
            raise PostQuantumError(msg)

        sig_hex = result.get("data", {}).get("signature_hex", "")
        if not sig_hex:
            raise PostQuantumError("No signature in helper response")

        return PQSignature(
            algorithm="ml-dsa-65",
            signature=bytes.fromhex(sig_hex),
            backend_name=self.name,
            security_level=level,
        )

    def verify_mldsa_signature(
        self,
        digest: str,
        signature: bytes,
        public_key_bytes: bytes,
        level: int = 65
    ) -> bool:
        """
        Verify an ML-DSA-65 signature via the helper.

        Args:
            digest: 64-character hex string
            signature: Raw ML-DSA signature bytes
            public_key_bytes: Raw public key bytes
            level: Security level (65 for ML-DSA-65)

        Returns:
            True if valid, False otherwise
        """
        result = _run_helper([
            "mldsa-verify",
            "--digest-hex", digest,
            "--signature-hex", signature.hex(),
            "--public-key-hex", public_key_bytes.hex(),
        ])
        if result is None:
            return False
        return result.get("ok", False)

    @property
    def name(self) -> str:
        return "swift-helper-mldsa"