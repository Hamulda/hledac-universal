"""
Universal Forensics Module
==========================

Digital forensics and metadata extraction capabilities for OSINT analysis.

Features:
- Universal metadata extraction from images, documents, audio, video
- EXIF parsing with GPS coordinate extraction (PIL + piexif)
- PDF and Office document metadata (pypdf + PyMuPDF)
- Steganography detection (chi-square, LSB, histogram analysis) — CANONICAL
- Archive structure analysis
- Scrubbing detection
- Timeline reconstruction
- Attribution analysis
- Digital ghost detection (deleted content, hidden data, tampering) — CANONICAL

Example:
    from hledac.universal.forensics import (
        UniversalMetadataExtractor,
        create_metadata_extractor,
        MetadataResult,
    )

    extractor = create_metadata_extractor()
    await extractor.initialize()

    result = await extractor.extract("/path/to/file.jpg")
    print(result.to_json())

    await extractor.close()
"""



# Availability flag
METADATA_EXTRACTOR_AVAILABLE = False

# Placeholder exports
UniversalMetadataExtractor = None
MetadataResult = None
ImageMetadata = None
PDFMetadata = None
DocxMetadata = None
AudioMetadata = None
VideoMetadata = None
ArchiveMetadata = None
GenericMetadata = None
GPSCoordinates = None
TimelineEvent = None
AttributionData = None
ScrubbingAnalysis = None
SteganalysisMetadata = None
create_metadata_extractor = None

# Steganography detector — canonical in forensics/stego_detector.py
STEGANOGRAPHY_AVAILABLE = False
StatisticalStegoDetector = None
StegoConfig = None
StegoResult = None
ChiSquareResult = None
RSResult = None
DCTResult = None
create_stego_detector = None
quick_stego_check = None

# Digital ghost detector — canonical in forensics/digital_ghost_detector.py
DIGITAL_GHOST_AVAILABLE = False
DigitalGhostDetector = None
DigitalGhostAnalysis = None
GhostSignal = None
RecoveredContent = None
detect_digital_ghosts = None


def _load_metadata_extractor() -> None:
    """Lazy load metadata extractor module."""
    global METADATA_EXTRACTOR_AVAILABLE
    global UniversalMetadataExtractor
    global MetadataResult
    global ImageMetadata
    global PDFMetadata
    global DocxMetadata
    global AudioMetadata
    global VideoMetadata
    global ArchiveMetadata
    global GenericMetadata
    global GPSCoordinates
    global TimelineEvent
    global AttributionData
    global ScrubbingAnalysis
    global SteganalysisMetadata
    global create_metadata_extractor

    if METADATA_EXTRACTOR_AVAILABLE:
        return

    try:
        from .metadata_extractor import (
            ArchiveMetadata,
            AttributionData,
            AudioMetadata,
            DocxMetadata,
            GenericMetadata,
            GPSCoordinates,
            ImageMetadata,
            MetadataResult,
            PDFMetadata,
            ScrubbingAnalysis,
            SteganalysisMetadata,
            TimelineEvent,
            UniversalMetadataExtractor,
            VideoMetadata,
            create_metadata_extractor,
        )
        METADATA_EXTRACTOR_AVAILABLE = True
    except ImportError:  # noqa: BLE001
        pass


def _load_steganography_detector() -> None:
    """Lazy load steganography detector module.

    CANONICAL implementation is forensics/stego_detector.py (StatisticalStegoDetector).
    This module provides the canonical exports for all forensics callers.
    """
    global STEGANOGRAPHY_AVAILABLE
    global StatisticalStegoDetector
    global StegoConfig
    global StegoResult
    global ChiSquareResult
    global RSResult
    global DCTResult
    global create_stego_detector
    global quick_stego_check

    if STEGANOGRAPHY_AVAILABLE:
        return

    try:
        from .stego_detector import (
            StatisticalStegoDetector,
            StegoConfig,
            StegoResult,
            ChiSquareResult,
            RSResult,
            DCTResult,
            create_stego_detector,
            quick_stego_check,
        )
        STEGANOGRAPHY_AVAILABLE = True
    except ImportError:  # noqa: BLE001
        pass


def _load_digital_ghost_detector() -> None:
    """Lazy load digital ghost detector module.

    CANONICAL implementation is forensics/digital_ghost_detector.py (DigitalGhostDetector).
    This module provides the canonical exports for all forensics callers.
    """
    global DIGITAL_GHOST_AVAILABLE
    global DigitalGhostDetector
    global DigitalGhostAnalysis
    global GhostSignal
    global RecoveredContent
    global detect_digital_ghosts

    if DIGITAL_GHOST_AVAILABLE:
        return

    try:
        from .digital_ghost_detector import (
            DigitalGhostDetector,
            DigitalGhostAnalysis,
            GhostSignal,
            RecoveredContent,
            detect_digital_ghosts,
        )
        DIGITAL_GHOST_AVAILABLE = True
    except ImportError:  # noqa: BLE001
        pass


# Auto-load on first import attempt
try:
    _load_metadata_extractor()
except Exception:  # noqa: BLE001
    pass

try:
    _load_steganography_detector()
except Exception:  # noqa: BLE001
    pass

try:
    _load_digital_ghost_detector()
except Exception:  # noqa: BLE001
    pass


__all__ = [
    "METADATA_EXTRACTOR_AVAILABLE",
    "UniversalMetadataExtractor",
    "MetadataResult",
    "ImageMetadata",
    "PDFMetadata",
    "DocxMetadata",
    "AudioMetadata",
    "VideoMetadata",
    "ArchiveMetadata",
    "GenericMetadata",
    "GPSCoordinates",
    "TimelineEvent",
    "AttributionData",
    "ScrubbingAnalysis",
    "SteganalysisMetadata",
    "create_metadata_extractor",
    # Steganography (canonical)
    "STEGANOGRAPHY_AVAILABLE",
    "StatisticalStegoDetector",
    "StegoConfig",
    "StegoResult",
    "ChiSquareResult",
    "RSResult",
    "DCTResult",
    "create_stego_detector",
    "quick_stego_check",
    # Digital Ghost (canonical)
    "DIGITAL_GHOST_AVAILABLE",
    "DigitalGhostDetector",
    "DigitalGhostAnalysis",
    "GhostSignal",
    "RecoveredContent",
    "detect_digital_ghosts",
]
