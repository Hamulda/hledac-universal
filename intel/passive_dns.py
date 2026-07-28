"""Backward-compat stub — DEPRECATED: import from "recon.dns.passive_dns" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.dns.passive_dns.
"""
import warnings

warnings.warn(
    "intel.passive_dns is deprecated — import from \"recon.dns.passive_dns\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from recon.dns.passive_dns import *
