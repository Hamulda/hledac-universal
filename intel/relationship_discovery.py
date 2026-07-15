"""Backward-compat stub — canonical: recon.relationship_discovery. Auto-gen ISSUE #20 F2."""
from recon.relationship_discovery import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.relationship_discovery"), name)
