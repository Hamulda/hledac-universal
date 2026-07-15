"""Backward-compat stub — canonical: recon.pattern_mining_canonical. Auto-gen ISSUE #20 F2."""
from recon.pattern_mining_canonical import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.pattern_mining_canonical"), name)
