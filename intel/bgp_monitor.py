"""Backward-compat stub — DEPRECATED: import from "network.bgp_monitor" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: network.bgp_monitor.
"""
import warnings

warnings.warn(
    "intel.bgp_monitor is deprecated — import from \"network.bgp_monitor\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from network.bgp_monitor import *
