"""Backward-compat stub — canonical: recon.blockchain_analyzer. Auto-gen ISSUE #20 F2."""
from recon.blockchain_analyzer import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.blockchain_analyzer"), name)
