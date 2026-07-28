"""Backward-compat stub — DEPRECATED: import from "recon.social_identity_miner" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.social_identity_miner.
"""
import warnings

warnings.warn(
    "intel.social_identity_miner is deprecated — import from \"recon.social_identity_miner\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.social_identity_miner import *
