"""Backward-compat stub — canonical: recon.github_secret_scanner. Auto-gen ISSUE #20 F2."""
from recon.github_secret_scanner import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.github_secret_scanner"), name)
