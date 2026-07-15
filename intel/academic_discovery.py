"""Backward-compat stub — canonical: recon.academic_discovery. Auto-gen ISSUE #20 F2."""
from recon.academic_discovery import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.academic_discovery"), name)
