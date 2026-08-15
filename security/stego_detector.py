"""
Security Steganography Shim
==========================

A1-21: Minimal shim — all forensic logic lives in forensics/stego_detector.py.
This module provides direct aliases for backward compatibility with callers
that use the hledac.universal.security.stego_detector import path.
"""

# Direct aliases — no re-export module overhead, zero-cost indirection
from forensics.stego_detector import (  # noqa: F401, E402
    StatisticalStegoDetector as StegoDetector,
    StegoConfig,
    StegoResult as StegoAnalysisResult,
    ChiSquareResult,
    RSResult,
    DCTResult,
    create_stego_detector,
    quick_stego_check,
)

# Additional exports from canonical
from forensics.stego_detector import (  # noqa: F401, E402
from core import aclose
    StatisticalStegoDetector,
    StegoResult,
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
