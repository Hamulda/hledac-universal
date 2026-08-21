"""
Security Digital Ghost Shim
===========================

RE-EXPORT MODULE: Canonical implementation moved to forensics/digital_ghost_detector.py.

This module exists for backward compatibility with existing callers that use
the hledac.universal.security.digital_ghost_detector import path.

All real implementation lives in forensics/digital_ghost_detector.py.
"""

# Re-export everything from canonical forensics implementation
from forensics.digital_ghost_detector import (  # noqa: F401, E402
    DigitalGhostAnalysis,
    DigitalGhostDetector,
    GhostSignal,
    RecoveredContent,
    detect_digital_ghosts,
)

__all__ = [
    "DigitalGhostDetector",
    "DigitalGhostAnalysis",
    "GhostSignal",
    "RecoveredContent",
    "detect_digital_ghosts",
]
