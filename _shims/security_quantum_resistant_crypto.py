"""
QuantumResistantCrypto — Post-Quantum Cryptography wrapper.

Provides unified interface to PQ crypto operations:
- ML-DSA-65 signing via Swift helper (macOS 26+)
- Fallback to NullPostQuantumBackend if unavailable

Library: liboqs-python for Kyber/Dilithium on M1 (when available).

Interface expected by callers:
- __init__(*args, **kwargs)
- Instance methods delegated to PostQuantumBackend protocol
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.security.pq_crypto import PostQuantumBackend

logger = logging.getLogger(__name__)


class QuantumResistantCrypto:
    """
    Post-quantum cryptography wrapper.

    Wraps PostQuantumBackend from hledac.universal.security.pq_crypto:
    - Primary: ML-DSA-65 digital signatures
    - Backend: Swift helper (macOS 26+) or NullPostQuantumBackend

    Usage:
        qrc = QuantumResistantCrypto()
        backend = qrc.get_backend()  # PostQuantumBackend instance
        status = backend.pq_status()  # PQStatus dataclass
    """

    def __init__(self, *args, **kwargs) -> None:
        """
        Initialize PQ crypto backend.

        Args:
            enabled: Whether to load real backend (default True)
            key_id: Key identifier for ML-DSA operations
        """
        enabled = kwargs.get("enabled", True)
        key_id = kwargs.get("key_id", "com.hledac.pq.signing.v1")

        try:
            from hledac.universal.security.pq_crypto import create_post_quantum_backend

            # Synchronous wrapper for async factory
            import asyncio
            loop = asyncio.new_event_loop()
            self._backend, self._status = loop.run_until_complete(
                create_post_quantum_backend(enabled=enabled, key_id=key_id)
            )
            loop.close()

            if self._backend.is_available():
                logger.info(f"QuantumResistantCrypto: Backend available ({self._backend.name})")
            else:
                logger.warning(f"QuantumResistantCrypto: Backend unavailable — using null")

        except Exception as e:
            logger.warning(f"QuantumResistantCrypto: Init failed: {e}")
            from hledac.universal.security.pq_crypto import NullPostQuantumBackend, PQStatus, PQAvailability

            self._backend = NullPostQuantumBackend()
            self._status = PQStatus(availability=PQAvailability.UNAVAILABLE, error_message=str(e))

    def get_backend(self) -> "PostQuantumBackend":
        """Get the underlying PostQuantumBackend instance."""
        return self._backend

    def get_status(self) -> dict[str, Any]:
        """Get PQ status as dict for telemetry."""
        return {
            "availability": self._status.availability.value,
            "backend_name": self._status.backend_name,
            "error_message": self._status.error_message,
            "mldsa_key_id": self._status.mldsa_key_id,
            "mldsa_level": self._status.mldsa_level,
        }

    def is_available(self) -> bool:
        """Check if real PQ backend is available."""
        return self._backend.is_available()

    def __repr__(self) -> str:
        return f"QuantumResistantCrypto(backend={self._backend.name}, available={self._backend.is_available()})"


__all__ = ["QuantumResistantCrypto"]
