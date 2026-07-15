"""Backward-compat stub — canonical: recon.wayback_cdx. Auto-gen ISSUE #20 F2."""
from recon.wayback_cdx import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.wayback_cdx"), name)
