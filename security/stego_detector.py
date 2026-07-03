"""
Security Steganography Shim
==========================

RE-EXPORT MODULE: Canonical implementation moved to forensics/stego_detector.py.

This module exists for backward compatibility with existing callers that use
the hledac.universal.security.stego_detector import path.

All real implementation lives in forensics/stego_detector.py.
"""

# Re-export everything from canonical forensics implementation
from forensics.stego_detector import (  # noqa: F401, E402
    StatisticalStegoDetector,
    StegoConfig,
    StegoResult,
    ChiSquareResult,
    RSResult,
    DCTResult,
    create_stego_detector,
    quick_stego_check,
)

# Legacy aliases for backward compatibility
StegoDetector = StatisticalStegoDetector
StegoAnalysisResult = StegoResult

__all__ = [
    "StatisticalStegoDetector",
    "StegoConfig",
    "StegoResult",
    "ChiSquareResult",
    "RSResult",
    "DCTResult",
    "create_stego_detector",
    "quick_stego_check",
    # Legacy aliases
    "StegoDetector",
    "StegoAnalysisResult",
]
