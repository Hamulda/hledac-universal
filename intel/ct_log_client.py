"""Backward-compat stub — DEPRECATED: import from "recon.ct_log_client" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.ct_log_client.
"""
import warnings

warnings.warn(
    "intel.ct_log_client is deprecated — import from \"recon.ct_log_client\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.ct_log_client import *
