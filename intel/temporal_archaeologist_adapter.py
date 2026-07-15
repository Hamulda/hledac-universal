"""Backward-compat stub — canonical: recon.temporal_archaeologist_adapter. Auto-gen ISSUE #20 F2."""
from recon.temporal_archaeologist_adapter import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.temporal_archaeologist_adapter"), name)
