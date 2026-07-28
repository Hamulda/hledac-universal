"""
Re-export from recon.passive_fingerprint — canonical passive fingerprinting.

K2 (F350M-R): network/ is infrastructure facade.
Canonical passive fingerprinting is recon.passive_fingerprint.
"""
from hledac.universal.recon.passive_fingerprint import (  # noqa: F401, E402
    PassiveFingerprintAdapter,
    PassiveTechStackAdapter,
    ServiceFingerprint,
    FingerprintResult,
    TechStack,
)

__all__ = [
    "PassiveFingerprintAdapter",
    "PassiveTechStackAdapter",
    "ServiceFingerprint",
    "FingerprintResult",
    "TechStack",
]
