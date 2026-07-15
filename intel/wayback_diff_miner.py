"""Backward-compat stub — canonical: recon.wayback_diff_miner. Auto-gen ISSUE #20 F2."""
from recon.wayback_diff_miner import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.wayback_diff_miner"), name)
