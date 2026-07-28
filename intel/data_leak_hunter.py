"""Backward-compat stub — DEPRECATED: import from "recon.data_leak_hunter" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.data_leak_hunter.
"""
import warnings

warnings.warn(
    "intel.data_leak_hunter is deprecated — import from \"recon.data_leak_hunter\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.data_leak_hunter import *
