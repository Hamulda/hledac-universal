"""Backward-compat stub — canonical: recon.bgp_lane. Auto-gen ISSUE #20 F2."""
from recon.bgp_lane import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.bgp_lane"), name)
