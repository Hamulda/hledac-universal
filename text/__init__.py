"""
Universal Text Analysis Module

High-speed text analysis utilities for security research.
"""

# Lazy loading with availability flag
UNICODE_ANALYZER_AVAILABLE = False
try:
    from .unicode_analyzer import (
        BidiFinding,
        HomoglyphFinding,
        NormalizationFinding,
        UnicodeAnalysisResult,
        UnicodeAttackAnalyzer,
        UnicodeConfig,
        ZeroWidthFinding,
        create_and_initialize_unicode_analyzer,
        create_unicode_analyzer,
    )
    UNICODE_ANALYZER_AVAILABLE = True
except ImportError:
    UnicodeConfig = None  # type: ignore
    UnicodeAttackAnalyzer = None  # type: ignore
    UnicodeAnalysisResult = None  # type: ignore
    ZeroWidthFinding = None  # type: ignore
    HomoglyphFinding = None  # type: ignore
    BidiFinding = None  # type: ignore
    NormalizationFinding = None  # type: ignore
    create_unicode_analyzer = None  # type: ignore
    create_and_initialize_unicode_analyzer = None  # type: ignore

# Phase 8: Encoding Detector
ENCODING_DETECTOR_AVAILABLE = False
try:
    from .encoding_detector import (
        BaseEncodingDetector,
        EncodingChain,
        EncodingConfig,
        EncodingFinding,
        create_encoding_detector,
        detect_encodings,
    )
    ENCODING_DETECTOR_AVAILABLE = True
except ImportError:
    BaseEncodingDetector = None  # type: ignore
    EncodingFinding = None  # type: ignore
    EncodingChain = None  # type: ignore
    EncodingConfig = None  # type: ignore
    create_encoding_detector = None  # type: ignore
    detect_encodings = None  # type: ignore

# Phase 8: Hash Identifier
HASH_IDENTIFIER_AVAILABLE = False
try:
    from .hash_identifier import (
        HashConfig,
        HashFinding,
        HashIdentifier,
        HashMatch,
        create_hash_identifier,
        identify_hash,
    )
    HASH_IDENTIFIER_AVAILABLE = True
except ImportError:
    HashIdentifier = None  # type: ignore
    HashMatch = None  # type: ignore
    HashFinding = None  # type: ignore
    HashConfig = None  # type: ignore
    create_hash_identifier = None  # type: ignore
    identify_hash = None  # type: ignore

__all__ = [
    "UNICODE_ANALYZER_AVAILABLE",
    "ENCODING_DETECTOR_AVAILABLE",
    "HASH_IDENTIFIER_AVAILABLE",
]

if UNICODE_ANALYZER_AVAILABLE:
    __all__.extend([
        "UnicodeConfig",
        "UnicodeAttackAnalyzer",
        "UnicodeAnalysisResult",
        "ZeroWidthFinding",
        "HomoglyphFinding",
        "BidiFinding",
        "NormalizationFinding",
        "create_unicode_analyzer",
        "create_and_initialize_unicode_analyzer",
    ])

if ENCODING_DETECTOR_AVAILABLE:
    __all__.extend([
        "BaseEncodingDetector",
        "EncodingFinding",
        "EncodingChain",
        "EncodingConfig",
        "create_encoding_detector",
        "detect_encodings",
    ])

if HASH_IDENTIFIER_AVAILABLE:
    __all__.extend([
        "HashIdentifier",
        "HashMatch",
        "HashFinding",
        "HashConfig",
        "create_hash_identifier",
        "identify_hash",
    ])
