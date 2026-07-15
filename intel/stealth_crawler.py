"""Backward-compat stub — canonical: recon.stealth_crawler. Auto-gen ISSUE #20 F2."""
from recon.stealth_crawler import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.stealth_crawler"), name)
