"""Backward-compat stub — DEPRECATED: import from "recon.leak_sentinel" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.leak_sentinel.
"""
import warnings

warnings.warn(
    "intel.leak_sentinel is deprecated — import from \"recon.leak_sentinel\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.leak_sentinel import *
