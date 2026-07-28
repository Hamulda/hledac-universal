"""Backward-compat stub — DEPRECATED: import from "recon.kill_chain_tagger" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.kill_chain_tagger.
"""
import warnings

warnings.warn(
    "intel.kill_chain_tagger is deprecated — import from \"recon.kill_chain_tagger\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.kill_chain_tagger import *
