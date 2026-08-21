"""
Security Steganography Shim
==========================

A1-21: Minimal shim — all forensic logic lives in forensics/stego_detector.py.
This module provides direct aliases for backward compatibility with callers
that use the hledac.universal.security.stego_detector import path.
"""

# Direct aliases — no re-export module overhead, zero-cost indirection
# Additional exports from canonical
from forensics.stego_detector import (  # noqa: F401, E402
    ChiSquareResult,
    DCTResult,
    RSResult,
    StatisticalStegoDetector,
    StegoConfig,
    StegoResult,
    create_stego_detector,
    quick_stego_check,
)
from forensics.stego_detector import (  # noqa: F401, E402
    StatisticalStegoDetector as StegoDetector,
)
from forensics.stego_detector import (
    StegoResult as StegoAnalysisResult,
)

__all__ = [
    "StegoDetector",
    "StegoConfig",
    "StegoAnalysisResult",
    "StatisticalStegoDetector",
    "StegoResult",
    "ChiSquareResult",
    "RSResult",
    "DCTResult",
    "create_stego_detector",
    "quick_stego_check",
]
