"""
Universal Forensics Module
==========================

Digital forensics and metadata extraction capabilities for OSINT analysis.

Features:
- Universal metadata extraction from images, documents, audio, video
- EXIF parsing with GPS coordinate extraction (PIL + piexif)
- PDF and Office document metadata (pypdf + PyMuPDF)
- Steganography detection (chi-square, LSB, histogram analysis)
- Archive structure analysis
- Scrubbing detection
- Timeline reconstruction
- Attribution analysis
- Digital ghost detection (deleted content, hidden data, tampering)

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

from __future__ import annotations

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

# Steganography detector
STEGANOGRAPHY_AVAILABLE = False
analyze_image_steganography = None
SteganalysisResult = None

# Digital ghost detector
DIGITAL_GHOST_AVAILABLE = False
analyze_file_ghosts = None
DigitalGhostResult = None
GhostArtifact = None


def _load_metadata_extractor():
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
    except ImportError:
        pass


def _load_steganography_detector():
    """Lazy load steganography detector module."""
    global STEGANOGRAPHY_AVAILABLE
    global analyze_image_steganography
    global SteganalysisResult

    if STEGANOGRAPHY_AVAILABLE:
        return

    try:
        from .steganography_detector import (
            SteganalysisResult,
            analyze_image_steganography,
        )
        STEGANOGRAPHY_AVAILABLE = True
    except ImportError:
        pass


def _load_digital_ghost_detector():
    """Lazy load digital ghost detector module."""
    global DIGITAL_GHOST_AVAILABLE
    global analyze_file_ghosts
    global DigitalGhostResult
    global GhostArtifact

    if DIGITAL_GHOST_AVAILABLE:
        return

    try:
        from .digital_ghost_detector import (
            GhostArtifact,
            DigitalGhostResult,
            analyze_file_ghosts,
        )
        DIGITAL_GHOST_AVAILABLE = True
    except ImportError:
        pass


# Auto-load on first import attempt
try:
    _load_metadata_extractor()
except Exception:
    pass

try:
    _load_steganography_detector()
except Exception:
    pass

try:
    _load_digital_ghost_detector()
except Exception:
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
    # Steganography
    "STEGANOGRAPHY_AVAILABLE",
    "analyze_image_steganography",
    "SteganalysisResult",
    # Digital Ghost
    "DIGITAL_GHOST_AVAILABLE",
    "analyze_file_ghosts",
    "DigitalGhostResult",
    "GhostArtifact",
]
