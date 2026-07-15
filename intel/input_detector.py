"""Backward-compat stub — canonical: recon.input_detector. Auto-gen ISSUE #20 F2."""
from recon.input_detector import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.input_detector"), name)
