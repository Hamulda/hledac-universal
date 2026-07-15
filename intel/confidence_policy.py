"""Backward-compat stub — canonical: recon.confidence_policy. Auto-gen ISSUE #20 F2."""
from recon.confidence_policy import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.confidence_policy"), name)
