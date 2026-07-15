"""Backward-compat stub — canonical: recon.censys_lane. Auto-gen ISSUE #20 F2."""
from recon.censys_lane import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.censys_lane"), name)
