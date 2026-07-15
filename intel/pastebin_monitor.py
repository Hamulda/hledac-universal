"""Backward-compat stub — canonical: recon.pastebin_monitor. Auto-gen ISSUE #20 F2."""
from recon.pastebin_monitor import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.pastebin_monitor"), name)
