"""Backward-compat stub — canonical: recon.timeline_synthesizer. Auto-gen ISSUE #20 F2."""
from recon.timeline_synthesizer import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.timeline_synthesizer"), name)
