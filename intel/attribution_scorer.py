"""Backward-compat stub — canonical: recon.attribution_scorer. Auto-gen ISSUE #20 F2."""
from recon.attribution_scorer import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.attribution_scorer"), name)
