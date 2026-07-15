"""Backward-compat stub — canonical: recon.network.passive_fingerprint. Auto-gen ISSUE #20 F2."""
import importlib

# Target recon.network.passive_fingerprint directly — avoids network.__init__.py re-export chain
_target = "recon.network.passive_fingerprint"

def __getattr__(name):
    return getattr(importlib.import_module(_target), name)
