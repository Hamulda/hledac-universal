"""Backward-compat stub — DEPRECATED: import from "recon.browser_pool" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.browser_pool.
"""
import warnings

warnings.warn(
    "intel.browser_pool is deprecated — import from \"recon.browser_pool\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.browser_pool import *
