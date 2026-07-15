"""Backward-compat stub — canonical: recon.shodan_lane. Auto-gen ISSUE #20 F2."""
from recon.shodan_lane import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.shodan_lane"), name)
