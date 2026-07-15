"""Backward-compat stub — canonical: recon.identity_stitching_canonical. Auto-gen ISSUE #20 F2."""
from recon.identity_stitching_canonical import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.identity_stitching_canonical"), name)
