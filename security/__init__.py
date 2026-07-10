"""
hledac.security — Security primitives for Hledac OSINT orchestrator.

All implementations live in this package directly.
The compat/ layer has been eliminated.
"""


# Key management
from hledac.universal.security.key_manager import KeyManager  # noqa: F401, E402

# Post-Quantum Cryptography
from hledac.universal.security.quantum_resistant_crypto import QuantumResistantCrypto  # noqa: F401, E402

# Stealth / anonymity
from hledac.universal.security.stealth_engine import StealthEngine  # noqa: F401, E402
from hledac.universal.security.temporal_anonymizer import TemporalAnonymizer  # noqa: F401, E402
from hledac.universal.security.zero_attribution_engine import ZeroAttributionEngine  # noqa: F401, E402

# Threat intelligence
from hledac.universal.security.threat_intelligence import ThreatIntelligence  # noqa: F401, E402

# ZKP research (simulation mode on M1)
from hledac.universal.security.zkp_research_engine import ZKPResearchEngine  # noqa: F401, E402

# Real implementations from security/
from hledac.universal.security.encryption import decrypt_aes_gcm, encrypt_aes_gcm  # noqa: F401, E402
from hledac.universal.security.ram_vault import RamDiskVault  # noqa: F401, E402

__all__ = [
    "KeyManager",
    "QuantumResistantCrypto",
    "StealthEngine",
    "TemporalAnonymizer",
    "ThreatIntelligence",
    "ZeroAttributionEngine",
    "ZKPResearchEngine",
    "decrypt_aes_gcm",
    "encrypt_aes_gcm",
    "RamDiskVault",
]
