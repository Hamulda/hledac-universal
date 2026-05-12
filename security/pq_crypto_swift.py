"""
Swift-backed Post-Quantum backend — calls the helper tool for ML-DSA-65.

This module provides the actual ML-DSA signing via the secure-enclave-helper
tool's PQ commands when running on macOS 26+.

Helper path discovery (priority order):
  a) HLEDAC_SECURE_ENCLAVE_HELPER env var
  b) repo-root/tools/secure_enclave_helper/.build/release/secure-enclave-helper
  c) None (fail-soft, truthful HELPER_MISSING)

Fail-soft throughout: any helper failure returns safe defaults.
Never spawns subprocess at import time.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
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

# ---------------------------------------------------------------------------
# Typed helper errors
# ---------------------------------------------------------------------------

HELPER_MISSING = "HELPER_MISSING"
HELPER_NOT_EXECUTABLE = "HELPER_NOT_EXECUTABLE"
HELPER_TIMEOUT = "HELPER_TIMEOUT"
HELPER_BAD_JSON = "HELPER_BAD_JSON"
HELPER_NONZERO_EXIT = "HELPER_NONZERO_EXIT"

# ---------------------------------------------------------------------------
# Repo root detection via __file__
# ---------------------------------------------------------------------------

_REPO_ROOT: Path | None = None

_STATUS_CACHE_TTL_SECONDS = 30.0  # short TTL for hpke-status / pq-status


def _detect_repo_root() -> Path | None:
    """Detect repo root from this file's location.

    This module lives at: .../security/pq_crypto_swift.py
    Repo root is three levels up from this file's parent directory.
    """
    global _REPO_ROOT
    if _REPO_ROOT is not None:
        return _REPO_ROOT

    try:
        self_path = Path(__file__).resolve()
        # security/ → universal/ (repo-root); helper lives at universal/tools/secure_enclave_helper/
        repo_root = self_path.parent.parent
        if (repo_root / "tools" / "secure_enclave_helper").exists():
            _REPO_ROOT = repo_root
            return _REPO_ROOT
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Helper path resolver
# ---------------------------------------------------------------------------


def get_secure_enclave_helper_path() -> Path | None:
    """
    Resolve secure-enclave-helper path with priority:
      a) HLEDAC_SECURE_ENCLAVE_HELPER env var
      b) repo-root/tools/secure_enclave_helper/.build/release/secure-enclave-helper
      c) None (fail-soft)
    """
    # (a) env override
    env_path = os.environ.get("HLEDAC_SECURE_ENCLAVE_HELPER")
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return p
        return None

    # (b) repo-relative fallback
    repo_root = _detect_repo_root()
    if repo_root is not None:
        repo_helper = (
            repo_root
            / "tools"
            / "secure_enclave_helper"
            / ".build"
            / "release"
            / "secure-enclave-helper"
        )
        if repo_helper.exists() and repo_helper.is_file():
            return repo_helper

    # (c) None — fail-soft, truthful HELPER_MISSING
    return None


def _get_helper_path() -> Path | None:
    """Internal alias for get_secure_enclave_helper_path()."""
    return get_secure_enclave_helper_path()


# ---------------------------------------------------------------------------
# Sync helper runner (blocking, for use in asyncio.to_thread)
# ---------------------------------------------------------------------------


def _run_helper_sync(command: list[str], timeout: float = 10.0) -> dict[str, Any] | None:
    """
    Run the secure-enclave-helper synchronously and return parsed JSON.
    Returns None on any failure (timeout, non-zero exit, bad JSON).
    """
    helper_path = _get_helper_path()
    if helper_path is None:
        return None

    try:
        result = subprocess.run(
            [str(helper_path)] + command,
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


# ---------------------------------------------------------------------------
# Async helper runner (uses asyncio.to_thread to avoid blocking event loop)
# ---------------------------------------------------------------------------


async def _run_helper_async(command: list[str], timeout: float = 10.0) -> dict[str, Any] | None:
    """
    Run the secure-enclave-helper asynchronously via asyncio.to_thread.
    Returns None on any failure.
    """
    return await asyncio.to_thread(_run_helper_sync, command, timeout)


# ---------------------------------------------------------------------------
# Status cache (short TTL)
# ---------------------------------------------------------------------------


@dataclass
class _CachedStatus:
    """Bounded status cache entry with short TTL."""
    status: PQStatus
    until: float  # monotonic time when this cache entry expires


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
    _cache: _CachedStatus | None = None

    def is_available(self, force_refresh: bool = False) -> bool:
        """
        Check if the Swift helper is available and ML-DSA is supported.

        Returns True only when:
        - macOS >= 15.0 (Swift helper checks via pq-status)
        - platform.machine() == "arm64" (M1/M2/M3 native)
        - CryptoKit ML-DSA-65 available (via Swift helper probe)

        Args:
            force_refresh: If True, bypass status cache and re-query helper.
        """
        # Check cache
        if not force_refresh and self._cache is not None:
            if time.monotonic() < self._cache.until:
                self._status = self._cache.status
                return self._status.availability == PQAvailability.AVAILABLE

        # Pre-flight: arm64 check before helper call
        if platform.machine() != "arm64":
            self._status = PQStatus(
                availability=PQAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message="ML-DSA requires arm64 (M1/M2/M3)",
            )
            self._cache = _CachedStatus(self._status, time.monotonic() + _STATUS_CACHE_TTL_SECONDS)
            return False

        # Helper must be present
        if _get_helper_path() is None:
            self._status = PQStatus(
                availability=PQAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message="secure-enclave-helper not found",
            )
            self._cache = _CachedStatus(self._status, time.monotonic() + _STATUS_CACHE_TTL_SECONDS)
            return False

        result = _run_helper_sync(["pq-status"])
        if result is None:
            self._status = PQStatus(
                availability=PQAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message="Helper unavailable or ML-DSA not supported",
            )
            self._cache = _CachedStatus(self._status, time.monotonic() + _STATUS_CACHE_TTL_SECONDS)
            return False

        if not result.get("ok", False):
            self._status = PQStatus(
                availability=PQAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message=result.get("message", "PQ status check failed"),
            )
            self._cache = _CachedStatus(self._status, time.monotonic() + _STATUS_CACHE_TTL_SECONDS)
            return False

        mldsa_available = result.get("data", {}).get("mldsa_available", "false") == "true"
        if not mldsa_available:
            self._status = PQStatus(
                availability=PQAvailability.UNAVAILABLE,
                backend_name="swift-helper",
                error_message="ML-DSA not available on this macOS version",
            )
            self._cache = _CachedStatus(self._status, time.monotonic() + _STATUS_CACHE_TTL_SECONDS)
            return False

        self._status = PQStatus(
            availability=PQAvailability.AVAILABLE,
            backend_name="swift-helper",
            mldsa_key_id=self.key_id,
            mldsa_level=65,
        )
        self._cache = _CachedStatus(self._status, time.monotonic() + _STATUS_CACHE_TTL_SECONDS)
        return True

    def pq_status(self) -> PQStatus:
        """Return current PQ status snapshot."""
        return self._status

    def ensure_mldsa_key(self, key_id: str, level: int = 65) -> bool:
        """
        Ensure ML-DSA key exists via the helper.

        Returns True if key is ready or already exists.
        """
        result = _run_helper_sync(["ensure-mldsa-key", "--key-id", key_id])
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
        result = _run_helper_sync([
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
        result = _run_helper_sync([
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
