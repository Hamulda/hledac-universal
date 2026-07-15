"""
Re-export from recon.cert.ct_log_scanner — canonical implementation.

Migration (F350M-R):
  - network/ct_log_scanner.py was a near-duplicate (80.6% similar)
  - recon/cert/ct_log_scanner.py is the canonical source (async, msgspec)
  - This file is kept for backward compatibility with code that
    imports from network.ct_log_scanner

All production code should import from recon.cert.ct_log_scanner directly.
"""
from hledac.universal.recon.cert.ct_log_scanner import (  # noqa: F401, E402
    _CTLogScanner,
)

__all__ = [
    "_CTLogScanner",
]
