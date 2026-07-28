"""Backward-compat stub — DEPRECATED: import from "recon.input_detector" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.input_detector.
"""
import warnings

warnings.warn(
    "intel.input_detector is deprecated — import from \"recon.input_detector\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.input_detector import *
