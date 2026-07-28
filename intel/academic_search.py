"""Backward-compat stub — DEPRECATED: import from "recon.academic_search" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.academic_search.
"""
import warnings

warnings.warn(
    "intel.academic_search is deprecated — import from \"recon.academic_search\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.academic_search import *
