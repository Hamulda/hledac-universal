"""Backward-compat stub — canonical: recon.network_reconnaissance. Auto-gen ISSUE #20 F2."""
from recon.network_reconnaissance import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.network_reconnaissance"), name)
