"""Backward-compat stub — canonical: recon.onion_seed_manager. Auto-gen ISSUE #20 F2."""
from recon.onion_seed_manager import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.onion_seed_manager"), name)
