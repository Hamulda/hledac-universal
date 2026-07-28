"""Backward-compat stub — DEPRECATED: import from "recon.onion_seed_manager" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.onion_seed_manager.
"""
import warnings

warnings.warn(
    "intel.onion_seed_manager is deprecated — import from \"recon.onion_seed_manager\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.onion_seed_manager import *
