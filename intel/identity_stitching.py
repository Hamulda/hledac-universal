"""Backward-compat stub — canonical: recon.identity_stitching. Auto-gen ISSUE #20 F2."""
from recon.identity_stitching import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.identity_stitching"), name)
