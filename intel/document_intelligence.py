"""Backward-compat stub — canonical: recon.document_intelligence. Auto-gen ISSUE #20 F2."""
from recon.document_intelligence import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.document_intelligence"), name)
