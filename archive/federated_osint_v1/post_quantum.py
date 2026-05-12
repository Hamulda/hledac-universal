"""
Post‑kvantová kryptografie s detekcí dostupných algoritmů.
Podpora ML-DSA (Dilithium), FALCON, Ed25519; Kyber, X25519.
"""

import logging
from typing import Tuple, Optional
import cryptography.hazmat.primitives.serialization

logger = logging.getLogger(__name__)

# Detekce dostupnosti liboqs
try:
    import oqs
    OQS_AVAILABLE = True
except ImportError:
    OQS_AVAILABLE = False
    logger.warning("liboqs not available, using classical crypto fallback")


class PQCProvider:
    """Poskytuje post‑kvantové operace s fallbackem na klasickou kryptografii."""

    _SIG_ALGOS = ['ML-DSA-44', 'ML-DSA-65', 'ML-DSA-87', 'FALCON-512', 'FALCON-1024']
    _KEM_ALGOS = ['Kyber512', 'Kyber768', 'Kyber1024']

    def __init__(self):
        self._pq_available = OQS_AVAILABLE
        self._sig_impl = None
        self._kem_impl = None
        self._sign_public_key = None
        self._sign_secret_key = None
        self._kem_public_key = None
        self._kem_secret_key = None
        self._sig_name = None
        self._kem_name = None
        self._shared = None

        if OQS_AVAILABLE:
            self._init_pqc()
        else:
            self._init_fallback()

    def _init_pqc(self):
        """Inicializace post‑kvantové kryptografie."""
        # Detekce podpisů
        available_sigs = oqs.get_enabled_signature_mechanisms()
        for alg in self._SIG_ALGOS:
            if alg in available_sigs:
                self._sig_name = alg
                self._sig_impl = oqs.Signature(alg)
                self._sign_public_key = self._sig_impl.generate_keypair()
                self._sign_secret_key = self._sig_impl.export_secret_key()
                logger.info(f"Using signature: {alg}")
                break

        if self._sig_impl is None:
            logger.warning("No PQC signature available, using Ed25519 fallback")
            self._init_fallback_signature()
            return

        # Detekce KEM
        available_kems = oqs.get_enabled_kem_mechanisms()
        for alg in self._KEM_ALGOS:
            if alg in available_kems:
                self._kem_name = alg
                self._kem_impl = oqs.KeyEncapsulation(alg)
                logger.info(f"Using KEM: {alg}")
                break

        if self._kem_impl is None:
            logger.warning("No PQC KEM available, using X25519 fallback")
            self._init_fallback_kem()

    def _init_fallback(self):
        """Inicializace fallback klasické kryptografie."""
        self._init_fallback_signature()
        self._init_fallback_kem()

    def _init_fallback_signature(self):
        """Fallback na Ed25519."""
        from cryptography.hazmat.primitives.asymmetric import ed25519

        self._sig_name = 'ED25519'
        self._ed25519_private = ed25519.Ed25519PrivateKey.generate()
        self._ed25519_public = self._ed25519_private.public_key()
        logger.info("Using Ed25519 signature (fallback)")

    def _init_fallback_kem(self):
        """Fallback na X25519."""
        from cryptography.hazmat.primitives.asymmetric import x25519

        self._kem_name = 'X25519'
        self._x25519_private = x25519.X25519PrivateKey.generate()
        self._x25519_public = self._x25519_private.public_key()
        logger.info("Using X25519 KEM (fallback)")

    def sign(self, message: bytes) -> bytes:
        """Podepíše zprávu."""
        if self._sig_name == 'ED25519':
            return self._ed25519_private.sign(message)
        else:
            return self._sig_impl.sign(message, self._sign_secret_key)

    def verify(self, public_key: bytes, message: bytes, signature: bytes) -> bool:
        """Ověří podpis."""
        if self._sig_name == 'ED25519':
            from cryptography.hazmat.primitives.asymmetric import ed25519
            try:
                ed25519.Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
                return True
            except Exception:
                return False
        else:
            return self._sig_impl.verify(message, signature, public_key)

    def get_sign_public_key(self) -> bytes:
        """Vrátí veřejný podpisový klíč."""
        if self._sig_name == 'ED25519':
            return self._ed25519_public.public_bytes(
                encoding=cryptography.hazmat.primitives.serialization.Encoding.Raw,
                format=cryptography.hazmat.primitives.serialization.PublicFormat.Raw
            )
        else:
            return self._sign_public_key

    def generate_kem_keypair(self) -> Tuple[bytes, bytes]:
        """Vygeneruje KEM pár klíčů."""
        if self._kem_name == 'X25519':
            public = self._x25519_public.public_bytes(
                encoding=cryptography.hazmat.primitives.serialization.Encoding.Raw,
                format=cryptography.hazmat.primitives.serialization.PublicFormat.Raw
            )
            return public, b''
        else:
            public_key = self._kem_impl.generate_keypair()
            secret_key = self._kem_impl.export_secret_key()
            return public_key, secret_key

    def encapsulate(self, public_key: bytes) -> Tuple[bytes, bytes]:
        """Zapouzdří shared secret pro daný veřejný klíč."""
        if self._kem_name == 'X25519':
            from cryptography.hazmat.primitives.asymmetric import x25519
            peer_pub = x25519.X25519PublicKey.from_public_bytes(public_key)
            shared = self._x25519_private.exchange(peer_pub)
            return b'', shared
        else:
            ciphertext = self._kem_impl.encapsulate(public_key)
            secret = self._kem_impl.export_shared_secret()
            self._shared = secret
            return ciphertext, secret

    def decapsulate(self, ciphertext: bytes, secret_key: bytes) -> bytes:
        """Od zapouzdří shared secret."""
        if self._kem_name == 'X25519':
            return ciphertext
        else:
            self._kem_impl.decapsulate(ciphertext, secret_key)
            return self._kem_impl.export_shared_secret()

    def get_kem_public_key(self) -> bytes:
        """Vrátí veřejný KEM klíč."""
        if self._kem_name == 'X25519':
            return self._x25519_public.public_bytes(
                encoding=cryptography.hazmat.primitives.serialization.Encoding.Raw,
                format=cryptography.hazmat.primitives.serialization.PublicFormat.Raw
            )
        else:
            public, _ = self.generate_kem_keypair()
            return public
