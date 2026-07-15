"""Backward-compat stub — canonical: recon.dark_web_intelligence. Auto-gen ISSUE #20 F2."""
from recon.dark_web_intelligence import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.dark_web_intelligence"), name)
