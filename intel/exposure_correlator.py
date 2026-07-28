"""Backward-compat stub — DEPRECATED: import from "recon.exposure_correlator" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.exposure_correlator.
"""
import warnings

warnings.warn(
    "intel.exposure_correlator is deprecated — import from \"recon.exposure_correlator\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.exposure_correlator import *
