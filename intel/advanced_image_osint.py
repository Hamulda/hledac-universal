"""Backward-compat stub — DEPRECATED: import from "recon.advanced_image_osint" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.advanced_image_osint.
"""
import warnings

warnings.warn(
    "intel.advanced_image_osint is deprecated — import from \"recon.advanced_image_osint\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.advanced_image_osint import *
