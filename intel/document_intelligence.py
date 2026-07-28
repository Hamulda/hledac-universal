"""Backward-compat stub — DEPRECATED: import from "recon.document_intelligence" directly.

Auto-gen ISSUE #20 F2.
DEPRECATED (F350M-R A4): all intel/ stubs emit DeprecationWarning.
Migrate to canonical path: recon.document_intelligence.
"""
import warnings

warnings.warn(
    "intel.document_intelligence is deprecated — import from \"recon.document_intelligence\" directly instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.recon.document_intelligence import *
