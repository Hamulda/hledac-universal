"""Backward-compat stub — DEPRECATED: import from "recon.temporal_analysis" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.temporal_analysis.
"""
import warnings

warnings.warn(
    "intel.temporal_analysis is deprecated — import from \"recon.temporal_analysis\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.temporal_analysis import *
