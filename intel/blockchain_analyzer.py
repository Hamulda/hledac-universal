"""Backward-compat stub — DEPRECATED: import from "recon.blockchain_analyzer" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.blockchain_analyzer.
"""
import warnings

warnings.warn(
    "intel.blockchain_analyzer is deprecated — import from \"recon.blockchain_analyzer\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.blockchain_analyzer import *
