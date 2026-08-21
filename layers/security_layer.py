"""
Security Layer - DEPRECATED Wrapper
==================================

This module is DEPRECATED. Import from `layers.security` instead:

    from layers.security import SecurityLayer, MissionAudit, AuditEntry

This file exists for backward compatibility only and will be removed in a future version.
"""

import warnings

# Deprecation warning for direct imports
warnings.warn(
    "layers.security_layer is deprecated. Import from layers.security instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from consolidated module
from layers.security import (
    AuditEntry,
    MissionAudit,
    ResearchObfuscator,
    SecureDestructor,
    SecurityLayer,
    StringObfuscator,
)

__all__ = [
    "SecurityLayer",
    "MissionAudit",
    "AuditEntry",
    "StringObfuscator",
    "ResearchObfuscator",
    "SecureDestructor",
]
