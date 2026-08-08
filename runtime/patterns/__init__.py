"""
runtime/patterns/__init__.py
============================

Pattern modules for Hledac OSINT orchestrator.

Submodules:
    discovery.py  — URL/IP/regex patterns for discovery
"""
from __future__ import annotations

__all__: list[str] = []

# F330-DUP: Refactored to use lazy_module_getter from utils/_patterns.py
__getattr__ = __import__("hledac.universal.utils._patterns", fromlist=["lazy_module_getter"]).lazy_module_getter(
    "hledac.universal.runtime.patterns.discovery",
    {"discovery": "discovery"},
)
