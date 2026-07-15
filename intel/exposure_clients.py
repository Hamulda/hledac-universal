"""Backward-compat stub — canonical: recon.exposure_clients. Auto-gen ISSUE #20 F2."""
from recon.exposure_clients import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.exposure_clients"), name)
