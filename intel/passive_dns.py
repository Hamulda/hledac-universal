"""Backward-compat stub — canonical: recon.dns.passive_dns. Auto-gen ISSUE #20 F2."""
import importlib

# Target recon.dns.passive_dns directly — avoids network.__init__.py re-export chain
_target = "recon.dns.passive_dns"

def __getattr__(name):
    return getattr(importlib.import_module(_target), name)
