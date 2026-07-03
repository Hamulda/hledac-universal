"""Quantum-safe cryptography (PQ only — neuromorphic crypto moved to brain/experimental_neuro_crypto.py)."""
from __future__ import annotations


import base64
import logging
import secrets
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# ── PQ Availability Flag ──────────────────────────────────────────────────────
try:
    import oqs as _oqs

    REAL_PQ_AVAILABLE = True
    _KYBER_ALG = "ML-KEM-768"
    _DILITHIUM_ALG = "ML-DSA-65"
except ImportError:
    REAL_PQ_AVAILABLE = False
    _KYBER_ALG = None
    _DILITHIUM_ALG = None
    _oqs = None

if not REAL_PQ_AVAILABLE:
    logger.warning(
        "PQ crypto running in SIMULATION mode — NOT cryptographically secure. "
        "Install liboqs-python: pip install oqs"
    )


class SecurityLevel(Enum):
    """Úrovně zabezpečení"""
    STANDARD = "standard"      # 128-bit security
    HIGH = "high"            # 192-bit security
    MAXIMUM = "maximum"      # 256-bit security


@dataclass
class EncryptedContainer:
    """Šifrovaný kontejner"""
    ciphertext: bytes
    encapsulated_key: bytes
    nonce: bytes
    algorithm: str
    security_level: SecurityLevel

    def to_dict(self) -> dict[str, str]:
        """Export jako slovník"""
        return {
            "ciphertext": base64.b64encode(self.ciphertext).decode(),
            "encapsulated_key": base64.b64encode(self.encapsulated_key).decode(),
            "nonce": base64.b64encode(self.nonce).decode(),
            "algorithm": self.algorithm,
            "security_level": self.security_level.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> EncryptedContainer:
        """Import ze slovníku"""
        return cls(
            ciphertext=base64.b64decode(data["ciphertext"]),
            encapsulated_key=base64.b64decode(data["encapsulated_key"]),
            nonce=base64.b64decode(data["nonce"]),
            algorithm=data["algorithm"],
            security_level=SecurityLevel(data["security_level"]),
        )


class QuantumSafeVault:
    """
    Trezor s quantum-safe kryptografií.
    Používá ML-KEM (Kyber) pro šifrování a ML-DSA (Dilithium)
    pro digitální podpisy. Odolné vůči kvantovým útokům.
    """

    def __init__(self, security_level: SecurityLevel = SecurityLevel.HIGH):
        self.security_level = security_level
        self._keypair = None
        self._signing_keypair = None
        self._initialized = False

    async def initialize(self) -> None:
        """Inicializovat vault - vygenerovat klíče"""
        logger.info(f"Initializing QuantumSafeVault ({self.security_level.value})")

        if not REAL_PQ_AVAILABLE:
            logger.warning("PQ crypto SIMULATION MODE — not cryptographically secure")
            self._keypair = {
                "public": secrets.token_bytes(32),
                "secret": secrets.token_bytes(32),
            }
            self._signing_keypair = {
                "public": secrets.token_bytes(32),
                "secret": secrets.token_bytes(64),
            }
        else:
            with _oqs.KeyEncapsulation(_KYBER_ALG) as kem:
                self._keypair = {
                    "public": kem.generate_keypair(),
                    "secret": kem.export_secret_key(),
                }
            with _oqs.Signature(_DILITHIUM_ALG) as sig:
                self._signing_keypair = {
                    "public": sig.generate_keypair(),
                    "secret": sig.export_secret_key(),
                }

        self._initialized = True
        logger.info("✓ QuantumSafeVault initialized")

    async def encrypt(
        self,
        plaintext: bytes,
        associated_data: bytes | None = None
    ) -> EncryptedContainer:
        """Zašifrovat data pomocí ML-KEM."""
        if not self._initialized:
            raise RuntimeError("Vault not initialized")
        if self._keypair is None:
            raise RuntimeError("Keypair not available")

        nonce = secrets.token_bytes(12)

        if REAL_PQ_AVAILABLE:
            with _oqs.KeyEncapsulation(_KYBER_ALG, self._keypair["secret"]) as kem:
                encapsulated_key, shared_secret = kem.encap_secret(self._keypair["public"])
        else:
            logger.warning("PQ crypto SIMULATION MODE — not cryptographically secure")
            shared_secret = secrets.token_bytes(32)
            encapsulated_key = secrets.token_bytes(32)

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(shared_secret)
        ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data)

        return EncryptedContainer(
            ciphertext=ciphertext,
            encapsulated_key=encapsulated_key,
            nonce=nonce,
            algorithm="ML-KEM-768+AES-256-GCM",
            security_level=self.security_level,
        )

    async def decrypt(
        self,
        container: EncryptedContainer,
        associated_data: bytes | None = None
    ) -> bytes:
        """Dešifrovat data."""
        if not self._initialized:
            raise RuntimeError("Vault not initialized")
        if self._keypair is None:
            raise RuntimeError("Keypair not available")

        if REAL_PQ_AVAILABLE:
            with _oqs.KeyEncapsulation(_KYBER_ALG, self._keypair["secret"]) as kem:
                shared_secret = kem.decap_secret(container.encapsulated_key)
        else:
            logger.warning("PQ crypto SIMULATION MODE — not cryptographically secure")
            shared_secret = secrets.token_bytes(32)

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        aesgcm = AESGCM(shared_secret)
        plaintext = aesgcm.decrypt(container.nonce, container.ciphertext, associated_data)

        return plaintext

    async def sign(self, message: bytes) -> bytes:
        """Podepsat zprávu pomocí ML-DSA (Dilithium)."""
        if not self._initialized:
            raise RuntimeError("Vault not initialized")

        if REAL_PQ_AVAILABLE:
            with _oqs.Signature(_DILITHIUM_ALG, self._signing_keypair["secret"]) as sig:
                return sig.sign(message)
        else:
            logger.warning("PQ crypto SIMULATION MODE — not cryptographically secure")
            return secrets.token_bytes(64)

    async def verify(self, message: bytes, signature: bytes) -> bool:
        """Ověřit podpis"""
        if not self._initialized:
            raise RuntimeError("Vault not initialized")

        if REAL_PQ_AVAILABLE:
            with _oqs.Signature(_DILITHIUM_ALG, self._signing_keypair["secret"]) as sig:
                return sig.verify(message, signature, self._signing_keypair["public"])
        else:
            logger.warning("PQ crypto SIMULATION MODE — not cryptographically secure")
            return True
