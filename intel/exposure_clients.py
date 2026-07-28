"""Backward-compat stub — DEPRECATED: import from "recon.exposure_clients" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.exposure_clients.
"""
import warnings

warnings.warn(
    "intel.exposure_clients is deprecated — import from \"recon.exposure_clients\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.exposure_clients import *
