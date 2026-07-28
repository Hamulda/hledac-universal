"""Backward-compat stub — DEPRECATED: import from "recon.exposed_service_hunter" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.exposed_service_hunter.
"""
import warnings

warnings.warn(
    "intel.exposed_service_hunter is deprecated — import from \"recon.exposed_service_hunter\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.exposed_service_hunter import *
