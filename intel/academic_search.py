"""Backward-compat stub — canonical: recon.academic_search. Auto-gen ISSUE #20 F2."""
from recon.academic_search import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.academic_search"), name)
