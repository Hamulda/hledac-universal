"""Backward-compat stub — canonical: recon.pattern_mining. Auto-gen ISSUE #20 F2."""
from recon.pattern_mining import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.pattern_mining"), name)
