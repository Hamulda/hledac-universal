"""Backward-compat stub — DEPRECATED: import from "recon.cert.ct_log_scanner" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.cert.ct_log_scanner.
"""
import warnings

warnings.warn(
    "intel.ct_log_scanner is deprecated — import from \"recon.cert.ct_log_scanner\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.cert.ct_log_scanner import *
