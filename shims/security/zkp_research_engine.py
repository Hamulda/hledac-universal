"""
Zero-Knowledge Proof Research Engine — STUB.

ZKP requires complex cryptographic primitives that cannot be trivially implemented:
- Groth16 or PLONK proof systems (requires libsnark or circom WASM binding)
- R1CS constraint generation for domain-specific relations
- Trusted setup ceremonies or transparent setup alternatives (e.g., Marlin)

This stub is a placeholder for future ZKP integration.
Gated by HLEDAC_ENABLE_ZKP=1 (shows warning instead of crashing).
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class ZKPError(Exception):
    """Raised when ZKP operations are attempted."""
    pass


class ZKPResearchEngine:
    """
    Zero-Knowledge Proof research engine — NOT IMPLEMENTED.

    To implement ZKP for OSINT evidence verification, you would need:
    1. A constraint system (R1CS) for the specific relation to prove
    2. A proof system (Groth16, PLONK, or STARK)
    3. A trusted setup or transparent setup
    4. A prover and verifier implementation

    This stub prevents import crashes but provides no cryptographic value.
    """

    name: str = "zkp_stub"

    def __init__(self) -> None:
        if os.environ.get("HLEDAC_ENABLE_ZKP") == "1":
            logger.warning(
                "ZKP not implemented — HLEDAC_ENABLE_ZKP=1 is set but "
                "ZKPResearchEngine is a stub. Real ZKP requires libsnark or "
                "circom WASM binding with a trusted setup ceremony."
            )

    def is_available(self) -> bool:
        """ZKP is never available from this stub."""
        return False

    def prove(self, witness: dict, statement: dict) -> bytes:
        """Stub — raises ZKPError."""
        raise ZKPError(
            "ZKP proof generation not implemented. "
            "Requires: libsnark/circom WASM, R1CS constraints, trusted setup."
        )

    def verify(self, proof: bytes, statement: dict) -> bool:
        """Stub — always returns False."""
        return False


# Module-level instance for compatibility
_engine = ZKPResearchEngine()


def get_zkp_engine() -> ZKPResearchEngine:
    """Get the ZKP engine instance."""
    return _engine
