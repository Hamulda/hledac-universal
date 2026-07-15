"""Backward-compat stub — canonical: recon.cryptographic_intelligence. Auto-gen ISSUE #20 F2."""
from recon.cryptographic_intelligence import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.cryptographic_intelligence"), name)
