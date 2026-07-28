"""Backward-compat stub — DEPRECATED: import from "recon.network_reconnaissance" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.network_reconnaissance.
"""
import warnings

warnings.warn(
    "intel.network_reconnaissance is deprecated — import from \"recon.network_reconnaissance\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.network_reconnaissance import *
