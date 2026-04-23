"""
Stealth komponenty pro UniversalResearchOrchestrator.

Obsahuje:
- StealthManager: Rate limiting, fingerprint rotation, headers
"""

# Canonical exports — stealth_session.py is the canonical stealth surface
from .stealth_session import StealthSession, StealthResponse

# Full system (for advanced use)
from .stealth_manager import StealthManager

__all__ = [
    # Canonical
    "StealthSession",
    "StealthResponse",
    # Full system
    "StealthManager",
]
