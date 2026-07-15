"""Backward-compat stub — canonical: recon.ct_lane. Auto-gen ISSUE #20 F2."""
from recon.ct_lane import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.ct_lane"), name)
