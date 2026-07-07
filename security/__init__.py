"""
hledac.security — Shim package re-exporting from hledac.universal.security

REDIRECT: All callers use hledac.security.* but the real path is hledac.universal.security.*
This shim preserves backward compatibility for any future callers.

DO NOT add logic here — re-export only.
"""
from __future__ import annotations


from hledac.universal.compat.security_key_manager import KeyManager  # noqa: F401
from hledac.universal.compat.security_quantum_resistant_crypto import QuantumResistantCrypto  # noqa: F401

# Shim adapters (wrap real implementations)
from hledac.universal.compat.security_stealth_engine import StealthEngine  # noqa: F401

# Stub implementations from compat (no circular import)
from hledac.universal.compat.security_temporal_anonymizer import TemporalAnonymizer  # noqa: F401
from hledac.universal.compat.security_threat_intelligence import ThreatIntelligence  # noqa: F401
from hledac.universal.compat.security_zero_attribution_engine import ZeroAttributionEngine  # noqa: F401
from hledac.universal.compat.security_zkp_research_engine import ZKPResearchEngine  # noqa: F401

# Real implementations from security/
from hledac.universal.security.encryption import decrypt_aes_gcm, encrypt_aes_gcm  # noqa: F401
from hledac.universal.security.ram_vault import RamDiskVault  # noqa: F401

__all__ = [
    # Stub implementations
    "TemporalAnonymizer",
    "ZeroAttributionEngine",
    "KeyManager",
    # Shim adapters
    "StealthEngine",
    "ThreatIntelligence",
    "QuantumResistantCrypto",
    "ZKPResearchEngine",
    # Real implementations
    "decrypt_aes_gcm",
    "encrypt_aes_gcm",
    "RamDiskVault",
]
