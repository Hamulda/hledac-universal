"""Backward-compat stub — canonical: recon.temporal_analysis. Auto-gen ISSUE #20 F2."""
from recon.temporal_analysis import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.temporal_analysis"), name)
