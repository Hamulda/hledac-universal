"""
runtime/patterns/__init__.py
============================

Pattern modules for Hledac OSINT orchestrator.

Submodules:
    discovery.py  — URL/IP/regex patterns for discovery
"""
from __future__ import annotations

__all__: list[str] = []

def __getattr__(name: str):
    if name == "discovery":
        from hledac.universal.runtime.patterns import discovery
        return discovery
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
