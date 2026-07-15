"""Backward-compat stub — canonical: recon.bgp_passive_dns_adapter. Auto-gen ISSUE #20 F2."""
from recon.bgp_passive_dns_adapter import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.bgp_passive_dns_adapter"), name)
