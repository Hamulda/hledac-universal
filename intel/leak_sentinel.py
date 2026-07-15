"""Backward-compat stub — canonical: recon.leak_sentinel. Auto-gen ISSUE #20 F2."""
from recon.leak_sentinel import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.leak_sentinel"), name)
