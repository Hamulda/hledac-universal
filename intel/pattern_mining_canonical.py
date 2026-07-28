"""Backward-compat stub — DEPRECATED: import from "recon.pattern_mining_canonical" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.pattern_mining_canonical.
"""
import warnings

warnings.warn(
    "intel.pattern_mining_canonical is deprecated — import from \"recon.pattern_mining_canonical\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.pattern_mining_canonical import *
