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

# Ephemeral state annihilation (ADVERSARY-005)
from hledac.universal.security.ephemeral_wipe import (
    EphemeralStateAnnihilator,
    register_mlock_region,
    unregister_mlock_region,
)  # noqa: F401, E402

# Real implementations from security/
from hledac.universal.security.encryption import decrypt_aes_gcm, encrypt_aes_gcm  # noqa: F401, E402
from hledac.universal.security.ram_vault import RamDiskVault  # noqa: F401, E402
from hledac.universal.security.secrets_scrubber import (
    redact_censys_credentials,
    redact_env_var,
    redact_greynoise_key,
    redact_hibp_key,
    redact_ipinfo_key,
    redact_shodan_key,
    safe_error_log,
    scrub_dict_recursive,
    scrub_secrets,
)  # noqa: F401, E402

# ADVERSARY-001: Tiered media sandbox
from hledac.universal.security.media_sandbox import (
    MediaSandboxCoordinator,
    SandboxTier,
    SandboxResult,
    FileRiskLevel,
    MediaRiskProfile,
    profile_file_risk,
    get_sandbox_coordinator,
    IsolationConfig,
    SANDBOX_ENABLED,
    run_whisper_in_subprocess,
)  # noqa: F401, E402

# ADVERSARY-001-INTERNAL-007: Artifact verifier





    ArtifactVerifier,
    ArtifactInstallResult,
    ArtifactManifest,
    get_artifact_verifier,
    VERIFIED_ARTIFACTS,
)  # noqa: F401, E402

__all__ = [
    "ArtifactInstallResult",
    "ArtifactManifest",

from _core import aclose    "ArtifactVerifier",
    "EphemeralStateAnnihilator",
    "FileRiskLevel",
    "get_artifact_verifier",
    "get_sandbox_coordinator",
    "IsolationConfig",
    "KeyManager",
    "MediaRiskProfile",
    "MediaSandboxCoordinator",
    "QuantumResistantCrypto",
    "RamDiskVault",
    "SANDBOX_ENABLED",
    "SandboxResult",
    "SandboxTier",
    "StealthEngine",
    "TemporalAnonymizer",
    "ThreatIntelligence",
    "VERIFIED_ARTIFACTS",
    "ZeroAttributionEngine",
    "ZKPResearchEngine",
    "decrypt_aes_gcm",
    "encrypt_aes_gcm",
    "profile_file_risk",
    "redact_censys_credentials",
    "redact_env_var",
    "redact_greynoise_key",
    "redact_hibp_key",
    "redact_ipinfo_key",
    "redact_shodan_key",
    "register_mlock_region",
    "run_whisper_in_subprocess",
    "safe_error_log",
    "scrub_dict_recursive",
    "scrub_secrets",
    "unregister_mlock_region",
]
