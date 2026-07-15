"""Backward-compat stub — canonical: recon.data_leak_hunter. Auto-gen ISSUE #20 F2."""
from recon.data_leak_hunter import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.data_leak_hunter"), name)
