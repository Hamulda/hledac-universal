"""Backward-compat stub — canonical: recon.exposure_correlator. Auto-gen ISSUE #20 F2."""
from recon.exposure_correlator import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.exposure_correlator"), name)
