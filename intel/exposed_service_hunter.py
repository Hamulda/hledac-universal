"""Backward-compat stub — canonical: recon.exposed_service_hunter. Auto-gen ISSUE #20 F2."""
from recon.exposed_service_hunter import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.exposed_service_hunter"), name)
