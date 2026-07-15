"""Backward-compat stub — canonical: recon.browser_pool. Auto-gen ISSUE #20 F2."""
from recon.browser_pool import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.browser_pool"), name)
