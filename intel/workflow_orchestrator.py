"""Backward-compat stub — canonical: recon.workflow_orchestrator. Auto-gen ISSUE #20 F2."""
from recon.workflow_orchestrator import *
from importlib import import_module

def __getattr__(name):
    return getattr(import_module("recon.workflow_orchestrator"), name)
