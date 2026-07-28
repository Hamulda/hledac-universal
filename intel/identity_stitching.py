"""Backward-compat stub — DEPRECATED: import from "recon.identity_stitching" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.identity_stitching.
"""
import warnings

warnings.warn(
    "intel.identity_stitching is deprecated — import from \"recon.identity_stitching\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.identity_stitching import *
