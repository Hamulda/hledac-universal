"""Backward-compat stub — canonical: recon.web_intelligence. Auto-gen ISSUE #20 F2."""
from recon.web_intelligence import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.web_intelligence"), name)
