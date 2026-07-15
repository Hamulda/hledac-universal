"""Backward-compat stub — canonical: recon.intel_seed. Auto-gen ISSUE #20 F2."""
from recon.intel_seed import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.intel_seed"), name)
