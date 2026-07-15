"""Backward-compat stub — canonical: recon.commoncrawl_adapter. Auto-gen ISSUE #20 F2."""
from recon.commoncrawl_adapter import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.commoncrawl_adapter"), name)
