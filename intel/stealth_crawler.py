"""Backward-compat stub — DEPRECATED: import from "recon.stealth_crawler" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.stealth_crawler.
"""
import warnings

warnings.warn(
    "intel.stealth_crawler is deprecated — import from \"recon.stealth_crawler\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.stealth_crawler import *
