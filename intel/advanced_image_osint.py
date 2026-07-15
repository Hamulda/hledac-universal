"""Backward-compat stub — canonical: recon.advanced_image_osint. Auto-gen ISSUE #20 F2."""
from recon.advanced_image_osint import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.advanced_image_osint"), name)
