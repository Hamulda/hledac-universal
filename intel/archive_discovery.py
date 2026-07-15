"""Backward-compat stub — canonical: recon.archive_discovery. Auto-gen ISSUE #20 F2."""
from recon.archive_discovery import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.archive_discovery"), name)
