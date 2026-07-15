"""Backward-compat stub — canonical: recon.social_identity_miner. Auto-gen ISSUE #20 F2."""
from recon.social_identity_miner import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.social_identity_miner"), name)
