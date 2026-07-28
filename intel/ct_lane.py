"""Backward-compat stub — DEPRECATED: import from "recon.ct_lane" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.ct_lane.
"""
import warnings

warnings.warn(
    "intel.ct_lane is deprecated — import from \"recon.ct_lane\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.ct_lane import *
