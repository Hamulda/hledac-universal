"""Backward-compat stub — DEPRECATED: import from "recon.censys_lane" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.censys_lane.
"""
import warnings

warnings.warn(
    "intel.censys_lane is deprecated — import from \"recon.censys_lane\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.censys_lane import *
