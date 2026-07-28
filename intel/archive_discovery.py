"""Backward-compat stub — DEPRECATED: import from "recon.archive_discovery" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.archive_discovery.
"""
import warnings

warnings.warn(
    "intel.archive_discovery is deprecated — import from \"recon.archive_discovery\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.archive_discovery import *
