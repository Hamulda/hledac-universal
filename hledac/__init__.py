# hledac/__init__.py — Hledac Package
"""
Hledac OSINT platform package.

This package provides the core components of the Hledac system.
"""

from __future__ import annotations

__version__ = "0.1.0"

# Re-export from rust facade for convenience
from hledac.rust import (
    get_preset_for_profile,
    get_safari18_settings,
    get_safari17_settings,
    get_safari16_settings,
    needs_webkit_preset,
    get_webkit_window_increment,
    get_webkit_initial_window_size,
    get_curl_default_initial_window_size,
    validate_safari_fingerprint,
    get_webkit_profiles,
)

__all__ = [
    "__version__",
    "get_preset_for_profile",
    "get_safari18_settings",
    "get_safari17_settings",
    "get_safari16_settings",
    "needs_webkit_preset",
    "get_webkit_window_increment",
    "get_webkit_initial_window_size",
    "get_curl_default_initial_window_size",
    "validate_safari_fingerprint",
    "get_webkit_profiles",
]
