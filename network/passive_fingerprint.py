"""
Re-export from recon.network.passive_fingerprint — canonical implementation.

Migration (F350M-R):
  - network/passive_fingerprint.py was a near-duplicate (99.9% similar)
  - recon/network/passive_fingerprint.py is the canonical source
  - This file is kept for backward compatibility with code that
    imports from network.passive_fingerprint

All production code should import from recon.network.passive_fingerprint directly.
"""
from hledac.universal.recon.network.passive_fingerprint import (  # noqa: F401, E402
    PassiveFingerprint,
    PassiveFingerprintAdapter,
)

__all__ = [
    "PassiveFingerprint",
    "PassiveFingerprintAdapter",
]
