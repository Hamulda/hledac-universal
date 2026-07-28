"""Backward-compat stub — DEPRECATED: import from "recon.pattern_mining" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.pattern_mining.
"""
import warnings

warnings.warn(
    "intel.pattern_mining is deprecated — import from \"recon.pattern_mining\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.pattern_mining import *
