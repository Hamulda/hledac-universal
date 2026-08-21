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

    __slots__ = ("_backend", "_status")

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
            from hledac.universal.utils.sync_bridge import run_sync_async

            # D4-1-FIX: Use run_sync_async() instead of new_event_loop/run_until_complete
            # run_sync_async() uses asyncio.Runner() (PEP 654) for Python 3.11+
            # and handles both running and non-running event loop cases
            self._backend, self._status = run_sync_async(create_post_quantum_backend(enabled=enabled, key_id=key_id))
            if self._backend.is_available():
                logger.info(f"QuantumResistantCrypto: Backend available ({self._backend.name})")
            else:
                logger.warning("QuantumResistantCrypto: Backend unavailable — using null")
        except Exception as e:
            logger.warning(f"QuantumResistantCrypto: Init failed: {e}")
            from hledac.universal.security.pq_crypto import NullPostQuantumBackend, PQAvailability, PQStatus

            self._backend = NullPostQuantumBackend()
            self._status = PQStatus(availability=PQAvailability.UNAVAILABLE, error_message=str(e))

    def get_backend(self) -> PostQuantumBackend:
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
