"""Backward-compat stub — DEPRECATED: import from "recon.shodan_lane" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.shodan_lane.
"""
import warnings

warnings.warn(
    "intel.shodan_lane is deprecated — import from \"recon.shodan_lane\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.shodan_lane import *
