"""Backward-compat stub — canonical: recon.lane. Auto-gen ISSUE #20 F2."""
from recon.lane import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.lane"), name)
