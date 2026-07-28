"""Backward-compat stub — DEPRECATED: import from "network.passive_fingerprint" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: network.passive_fingerprint.
"""
import warnings

warnings.warn(
    "intel.passive_fingerprint is deprecated — import from \"network.passive_fingerprint\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from network.passive_fingerprint import *
