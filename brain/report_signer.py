"""
SOVEREIGN-009: Ed25519 Cryptographic Report Signing.

Provides deterministic, always-on Ed25519 signing for forensic reports.

Every exported forensic report is signed at export time, producing a
base64-encoded Ed25519 signature that can be independently verified.

Architecture:
- ReportSigner: singleton-style class managing keypair lifecycle
- Ed25519 via cryptography.hazmat (already a project dependency)
- Keypair stored in ~/.hledac/keys/report_signer_ed25519.{priv,pub}
- Signature appended as <signature base64="..." /> XML fragment
- Fail-soft: signing failure never blocks report export

Key design decisions (M1 8GB, Python 3.14+):
- Lazy keypair generation (no I/O at import time)
- Thread-safe via module-level lock (keypair gen is rare, once per process)
- Signature covers canonical UTF-8 bytes of report content
- Public key embedded in signature block for self-contained verification

Integration points:
- export/export_manager.py: export_research_report()
- export/sprint_exporter.py: sprint report JSON export
- report/engine.py: multi-format report rendering
- layers/security_layer.py: MissionAudit chain (optional co-signing)
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ALGORITHM = "ed25519"
_SCHEMA_VERSION = "1"
_KEY_DIR_NAME = "keys"
_KEY_BASENAME = "report_signer_ed25519"

# Signature XML template - appended to Markdown reports or embedded in JSON
_SIGNATURE_TEMPLATE = (
    '\n\n<!-- SOVEREIGN-009: Ed25519 Report Signature -->\n'
    '<signature\n'
    '  algorithm="{algorithm}"\n'
    '  schema_version="{schema_version}"\n'
    '  timestamp="{timestamp}"\n'
    '  report_hash="{report_hash}"\n'
    '  public_key="{public_key_b64}"\n'
    '  base64="{signature_b64}"\n'
    '/>\n'
)

# JSON signature block for structured exports
_JSON_SIGNATURE_TEMPLATE = {
    "sovereign_signature": {
        "algorithm": _ALGORITHM,
        "schema_version": _SCHEMA_VERSION,
        "timestamp": "",
        "report_hash": "",
        "public_key_b64": "",
        "signature_b64": "",
    }
}


# ---------------------------------------------------------------------------
# ReportSigner
# ---------------------------------------------------------------------------


class ReportSigner:
    """
    Ed25519 cryptographic signer for forensic reports.

    Usage:
        signer = ReportSigner()
        signed_content = signer.sign_report(report_content)
        is_valid = signer.verify_report(report_content, signature_b64)

    Thread-safe: keypair generation is protected by a lock.
    Fail-soft: all operations return original content on failure.
    """

    __slots__ = (
        "_key_dir",
        "_private_key",
        "_public_key",
        "_public_key_b64",
        "_lock",
        "_initialized",
    )

    def __init__(self, key_dir: Path | str | None = None) -> None:
        """
        Initialize ReportSigner.

        Args:
            key_dir: Directory for key storage. Defaults to ~/.hledac/keys/
        """
        if key_dir is None:
            key_dir = Path.home() / ".hledac" / _KEY_DIR_NAME
        self._key_dir = Path(key_dir)
        self._private_key: Any = None
        self._public_key: Any = None
        self._public_key_b64: str = ""
        self._lock = threading.Lock()
        self._initialized = False
        logger.debug("ReportSigner initialized (lazy keypair generation)")

    def _ensure_initialized(self) -> bool:
        """
        Lazy initialization: load or generate Ed25519 keypair.

        Returns:
            True if keypair is ready, False on failure.
        """
        if self._initialized:
            return True

        with self._lock:
            # Double-check after acquiring lock
            if self._initialized:
                return True

            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                    Ed25519PrivateKey,
                )
                from cryptography.hazmat.primitives import serialization

                # Try to load existing keypair
                priv_path = self._key_dir / f"{_KEY_BASENAME}.priv"
                pub_path = self._key_dir / f"{_KEY_BASENAME}.pub"

                if priv_path.exists() and pub_path.exists():
                    # Load existing keys
                    priv_bytes = priv_path.read_bytes()
                    self._private_key = Ed25519PrivateKey.from_private_bytes(priv_bytes)
                    self._public_key = self._private_key.public_key()
                    pub_bytes = self._public_key.public_bytes(
                        serialization.Encoding.Raw,
                        serialization.PublicFormat.Raw,
                    )
                    self._public_key_b64 = base64.b64encode(pub_bytes).decode("ascii")
                    logger.info("ReportSigner: loaded existing Ed25519 keypair")
                else:
                    # Generate new keypair
                    self._private_key = Ed25519PrivateKey.generate()
                    self._public_key = self._private_key.public_key()

                    # Export public key
                    pub_bytes = self._public_key.public_bytes(
                        serialization.Encoding.Raw,
                        serialization.PublicFormat.Raw,
                    )
                    self._public_key_b64 = base64.b64encode(pub_bytes).decode("ascii")

                    # Persist keys
                    self._key_dir.mkdir(parents=True, exist_ok=True)
                    priv_bytes = self._private_key.private_bytes(
                        serialization.Encoding.Raw,
                        serialization.PrivateFormat.Raw,
                        serialization.NoEncryption(),
                    )
                    priv_path.write_bytes(priv_bytes)
                    pub_path.write_bytes(pub_bytes)

                    # Restrict permissions (Unix only)
                    try:
                        priv_path.chmod(0o600)
                        pub_path.chmod(0o644)
                    except (OSError, NotImplementedError):
                        pass  # Windows or restricted filesystem

                    logger.info("ReportSigner: generated new Ed25519 keypair")

                self._initialized = True
                return True

            except Exception as e:
                logger.error(f"ReportSigner: keypair initialization failed: {e}")
                return False

    def sign_report(self, report_content: str) -> str:
        """
        Sign a forensic report and append signature block.

        Args:
            report_content: Report content (Markdown, JSON, or plain text)

        Returns:
            Report content with signature appended, or original content on failure.
        """
        if not self._ensure_initialized():
            logger.warning("ReportSigner: signing skipped (keypair unavailable)")
            return report_content

        try:
            import hashlib
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            # Canonical representation: UTF-8 bytes
            report_bytes = report_content.encode("utf-8")

            # Compute report hash (SHA-256) for integrity verification
            report_hash = hashlib.sha256(report_bytes).hexdigest()

            # Sign the report bytes
            signature_bytes = self._private_key.sign(report_bytes)
            signature_b64 = base64.b64encode(signature_bytes).decode("ascii")

            # Generate timestamp
            timestamp = int(time.time())

            # Append signature block
            signature_block = _SIGNATURE_TEMPLATE.format(
                algorithm=_ALGORITHM,
                schema_version=_SCHEMA_VERSION,
                timestamp=timestamp,
                report_hash=report_hash,
                public_key_b64=self._public_key_b64,
                signature_b64=signature_b64,
            )

            signed_content = report_content + signature_block
            logger.debug(f"ReportSigner: signed report ({len(report_bytes)} bytes)")
            return signed_content

        except Exception as e:
            logger.error(f"ReportSigner: signing failed: {e}")
            return report_content

    def sign_json_report(self, report_dict: dict[str, Any]) -> dict[str, Any]:
        """
        Sign a JSON report by adding signature metadata.

        Args:
            report_dict: Report dictionary

        Returns:
            Report dict with sovereign_signature field added, or original on failure.
        """
        if not self._ensure_initialized():
            logger.warning("ReportSigner: signing skipped (keypair unavailable)")
            return report_dict

        try:
            import hashlib
            import orjson

            # Canonical JSON representation (sorted keys, no whitespace)
            canonical_bytes = orjson.dumps(
                report_dict,
                option=orjson.OPT_SORT_KEYS,
            )

            # Compute report hash
            report_hash = hashlib.sha256(canonical_bytes).hexdigest()

            # Sign the canonical bytes
            signature_bytes = self._private_key.sign(canonical_bytes)
            signature_b64 = base64.b64encode(signature_bytes).decode("ascii")

            # Generate timestamp
            timestamp = int(time.time())

            # Add signature to report dict
            signed_dict = dict(report_dict)  # shallow copy
            signed_dict["sovereign_signature"] = {
                "algorithm": _ALGORITHM,
                "schema_version": _SCHEMA_VERSION,
                "timestamp": timestamp,
                "report_hash": report_hash,
                "public_key_b64": self._public_key_b64,
                "signature_b64": signature_b64,
            }

            logger.debug(f"ReportSigner: signed JSON report ({len(canonical_bytes)} bytes)")
            return signed_dict

        except Exception as e:
            logger.error(f"ReportSigner: JSON signing failed: {e}")
            return report_dict

    def verify_report(self, report_content: str, signature_b64: str) -> bool:
        """
        Verify a signed forensic report.

        Args:
            report_content: Original report content (without signature block)
            signature_b64: Base64-encoded Ed25519 signature

        Returns:
            True if signature is valid, False otherwise.
        """
        if not self._ensure_initialized():
            logger.warning("ReportSigner: verification skipped (keypair unavailable)")
            return False

        try:
            signature_bytes = base64.b64decode(signature_b64)
            report_bytes = report_content.encode("utf-8")
            self._public_key.verify(signature_bytes, report_bytes)
            logger.debug("ReportSigner: signature verification succeeded")
            return True
        except Exception as e:
            logger.warning(f"ReportSigner: signature verification failed: {e}")
            return False

    def verify_json_report(self, report_dict: dict[str, Any]) -> bool:
        """
        Verify a signed JSON report.

        Args:
            report_dict: Report dict with sovereign_signature field

        Returns:
            True if signature is valid, False otherwise.
        """
        if not self._ensure_initialized():
            logger.warning("ReportSigner: verification skipped (keypair unavailable)")
            return False

        try:
            import orjson

            sig_block = report_dict.get("sovereign_signature")
            if not sig_block:
                logger.warning("ReportSigner: no sovereign_signature field found")
                return False

            signature_b64 = sig_block.get("signature_b64", "")
            if not signature_b64:
                return False

            # Reconstruct canonical JSON (without signature field)
            report_copy = dict(report_dict)
            report_copy.pop("sovereign_signature", None)
            canonical_bytes = orjson.dumps(report_copy, option=orjson.OPT_SORT_KEYS)

            signature_bytes = base64.b64decode(signature_b64)
            self._public_key.verify(signature_bytes, canonical_bytes)
            logger.debug("ReportSigner: JSON signature verification succeeded")
            return True

        except Exception as e:
            logger.warning(f"ReportSigner: JSON verification failed: {e}")
            return False

    def get_public_key_b64(self) -> str:
        """
        Get the public key in base64 format.

        Returns:
            Base64-encoded public key, or empty string if unavailable.
        """
        if not self._ensure_initialized():
            return ""
        return self._public_key_b64


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_signer_instance: ReportSigner | None = None
_signer_lock = threading.Lock()


def get_report_signer() -> ReportSigner:
    """
    Get the global ReportSigner singleton.

    Thread-safe lazy initialization.

    Returns:
        ReportSigner instance
    """
    global _signer_instance
    if _signer_instance is None:
        with _signer_lock:
            if _signer_instance is None:
                _signer_instance = ReportSigner()
    return _signer_instance


def sign_forensic_report(report_content: str) -> str:
    """
    Convenience function: sign a forensic report using the global signer.

    Args:
        report_content: Report content to sign

    Returns:
        Signed report content, or original on failure.
    """
    return get_report_signer().sign_report(report_content)


def sign_forensic_json(report_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Convenience function: sign a JSON forensic report using the global signer.

    Args:
        report_dict: Report dict to sign

    Returns:
        Signed report dict, or original on failure.
    """
    return get_report_signer().sign_json_report(report_dict)
