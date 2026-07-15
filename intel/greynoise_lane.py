"""Backward-compat stub — canonical: recon.greynoise_lane. Auto-gen ISSUE #20 F2."""
from recon.greynoise_lane import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.greynoise_lane"), name)
