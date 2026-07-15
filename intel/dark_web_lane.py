"""Backward-compat stub — canonical: recon.dark_web_lane. Auto-gen ISSUE #20 F2."""
from recon.dark_web_lane import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.dark_web_lane"), name)
