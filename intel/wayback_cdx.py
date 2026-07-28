"""Backward-compat stub — DEPRECATED: import from "recon.wayback_cdx" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.wayback_cdx.
"""
import warnings

warnings.warn(
    "intel.wayback_cdx is deprecated — import from \"recon.wayback_cdx\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.wayback_cdx import *
