"""
Canonical Runtime State — Single Source of Truth for Global Runtime Flags

This module MUST NOT import from __main__ or session_runtime (no circular deps).

Sprint F266-UVLOOP: Unified uvloop state resolution.
- _uvloop_installed: set by __main__.py after successful uvloop.install()
- Consumed by session_runtime.py for get_session_runtime_status()["uvloop_enabled"]
- session_runtime.try_install_uvloop() is DEAD CODE — removed, replaced by this module
"""


# Canonical uvloop state — set once at boot, never modified after
_uvloop_installed: bool = False


def set_uvloop_installed() -> None:
    """
    Called by __main__.py after successful uvloop.install().

    This is the ONLY write path for _uvloop_installed.
    """
    global _uvloop_installed
    _uvloop_installed = True
