"""Backward-compat stub — canonical: recon.entity_signal_extractor. Auto-gen ISSUE #20 F2."""
from recon.entity_signal_extractor import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.entity_signal_extractor"), name)
