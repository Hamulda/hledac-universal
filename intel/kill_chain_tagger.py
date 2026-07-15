"""Backward-compat stub — canonical: recon.kill_chain_tagger. Auto-gen ISSUE #20 F2."""
from recon.kill_chain_tagger import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.kill_chain_tagger"), name)
