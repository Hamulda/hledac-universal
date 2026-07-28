"""Backward-compat stub — DEPRECATED: import from "recon.intel_seed" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.intel_seed.
"""
import warnings

warnings.warn(
    "intel.intel_seed is deprecated — import from \"recon.intel_seed\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.intel_seed import *
