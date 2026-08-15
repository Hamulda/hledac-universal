"""
⚠️ DEPRECATED: This file is a re-export shim for backward compatibility.

K2 (F350M-R): This file has been REMOVED. Please update your imports:

OLD (deprecated):
    from hledac.universal.network.passive_fingerprint import ...

NEW (canonical):
    from hledac.universal.recon.passive_fingerprint import ...

This stub will be removed in a future release.
"""

import warnings

warnings.warn(
    "network.passive_fingerprint is deprecated — "
    "import from 'hledac.universal.recon.passive_fingerprint' instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from canonical location for backward compatibility
from hledac.universal.recon.passive_fingerprint import (  # noqa: F401, E402
from _core import aclose
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
