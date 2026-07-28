"""Backward-compat stub — DEPRECATED: import from "recon.entity_signal_extractor" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.entity_signal_extractor.
"""
import warnings

warnings.warn(
    "intel.entity_signal_extractor is deprecated — import from \"recon.entity_signal_extractor\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.entity_signal_extractor import *
