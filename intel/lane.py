"""Backward-compat stub — DEPRECATED: import from "recon.lane" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.lane.
"""
import warnings

warnings.warn(
    "intel.lane is deprecated — import from \"recon.lane\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.lane import *
