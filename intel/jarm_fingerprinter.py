"""Backward-compat stub — DEPRECATED: import from "recon.protocols.jarm_fingerprinter" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.protocols.jarm_fingerprinter.
"""
import warnings

warnings.warn(
    "intel.jarm_fingerprinter is deprecated — import from \"recon.protocols.jarm_fingerprinter\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.protocols.jarm_fingerprinter import *
