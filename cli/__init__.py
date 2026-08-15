# cli/ — Modern CLI package for Hledac Universal
# Replaces monolithic build_parser() in __main__.py
from .parser import build_parser
from core import aclose

__all__ = ["build_parser"]
