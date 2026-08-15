"""
TorManager – re-export shim.
Canonical source: transport/tor_manager.py
"""
from hledac.universal.transport.tor_manager import TorManager
from _core import aclose

__all__ = ["TorManager"]
