"""Backward-compat stub — DEPRECATED: import from "recon.commoncrawl_adapter" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.commoncrawl_adapter.
"""
import warnings

warnings.warn(
    "intel.commoncrawl_adapter is deprecated — import from \"recon.commoncrawl_adapter\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.commoncrawl_adapter import *
