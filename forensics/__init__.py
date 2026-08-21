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

# PEP 562: Module-level __getattr__ for lazy imports
# Pattern: Dictionary dispatch tables → no global declarations needed
#
# REFACTOR: 2026-08-18
# Before: 50+ global declarations + _load_*() functions + auto-import on startup
# After:  PEP 562 __getattr__ with lazy loading on first access
# Benefit: ~200ms faster import, no eager module loading, better memory for M1 8GB


# ── Metadata Extractor Dispatch ────────────────────────────────────────────────

_METADATA_DISPATCH: dict[str, tuple[str, tuple[str, ...]]] = {
    # Core extractor
    "UniversalMetadataExtractor": (".metadata_extractor", ("UniversalMetadataExtractor",)),
    "create_metadata_extractor": (".metadata_extractor", ("create_metadata_extractor",)),
    "MetadataResult": (".metadata_extractor", ("MetadataResult",)),
    # Image metadata
    "ImageMetadata": (".metadata_extractor", ("ImageMetadata",)),
    "GPSCoordinates": (".metadata_extractor", ("GPSCoordinates",)),
    # Document metadata
    "PDFMetadata": (".metadata_extractor", ("PDFMetadata",)),
    "DocxMetadata": (".metadata_extractor", ("DocxMetadata",)),
    # Media metadata
    "AudioMetadata": (".metadata_extractor", ("AudioMetadata",)),
    "VideoMetadata": (".metadata_extractor", ("VideoMetadata",)),
    # Archive & generic
    "ArchiveMetadata": (".metadata_extractor", ("ArchiveMetadata",)),
    "GenericMetadata": (".metadata_extractor", ("GenericMetadata",)),
    # Analysis types
    "TimelineEvent": (".metadata_extractor", ("TimelineEvent",)),
    "AttributionData": (".metadata_extractor", ("AttributionData",)),
    "ScrubbingAnalysis": (".metadata_extractor", ("ScrubbingAnalysis",)),
    "SteganalysisMetadata": (".metadata_extractor", ("SteganalysisMetadata",)),
}


# ── Steganography Dispatch (Canonical) ─────────────────────────────────────────

_STEGO_DISPATCH: dict[str, tuple[str, tuple[str, ...]]] = {
    "StatisticalStegoDetector": (".stego_detector", ("StatisticalStegoDetector",)),
    "StegoConfig": (".stego_detector", ("StegoConfig",)),
    "StegoResult": (".stego_detector", ("StegoResult",)),
    "ChiSquareResult": (".stego_detector", ("ChiSquareResult",)),
    "RSResult": (".stego_detector", ("RSResult",)),
    "DCTResult": (".stego_detector", ("DCTResult",)),
    "create_stego_detector": (".stego_detector", ("create_stego_detector",)),
    "quick_stego_check": (".stego_detector", ("quick_stego_check",)),
}


# ── Digital Ghost Dispatch (Canonical) ────────────────────────────────────────

_GHOST_DISPATCH: dict[str, tuple[str, tuple[str, ...]]] = {
    "DigitalGhostDetector": (".digital_ghost_detector", ("DigitalGhostDetector",)),
    "DigitalGhostAnalysis": (".digital_ghost_detector", ("DigitalGhostAnalysis",)),
    "GhostSignal": (".digital_ghost_detector", ("GhostSignal",)),
    "RecoveredContent": (".digital_ghost_detector", ("RecoveredContent",)),
    "detect_digital_ghosts": (".digital_ghost_detector", ("detect_digital_ghosts",)),
}


# ── Git Forensics Dispatch (Rust-accelerated) ──────────────────────────────────

_GIT_DISPATCH: dict[str, tuple[str, tuple[str, ...]]] = {
    "GitForensicsDetector": (".git_forensics", ("GitForensicsDetector",)),
    "GitForensicsResult": (".git_forensics", ("GitForensicsResult",)),
    "GitForensicRecord": (".git_forensics", ("GitForensicRecord",)),
    "GitForensicsStats": (".git_forensics", ("GitForensicsStats",)),
    "quick_git_analysis": (".git_forensics", ("quick_git_analysis",)),
}


# ── Combined Dispatch Tables ────────────────────────────────────────────────────

_ALL_DISPATCHES: tuple[dict[str, tuple[str, tuple[str, ...]]], ...] = (
    _METADATA_DISPATCH,
    _STEGO_DISPATCH,
    _GHOST_DISPATCH,
    _GIT_DISPATCH,
)

# ── Availability Flag Names ────────────────────────────────────────────────────

_AVAILABILITY_FLAGS: frozenset[str] = frozenset(
    {
        "METADATA_EXTRACTOR_AVAILABLE",
        "STEGANOGRAPHY_AVAILABLE",
        "DIGITAL_GHOST_AVAILABLE",
        "GIT_FORENSICS_AVAILABLE",
    }
)

# ── Module-level Import Cache ──────────────────────────────────────────────────

_IMPORT_CACHE: dict[str, object] = {}


# ── PEP 562: Unified Lazy Import ───────────────────────────────────────────────


def __getattr__(name: str):
    """Unified PEP 562 lazy import — handles class exports and availability flags.

    Performance: Cache hit is O(1) dict lookup.
    Memory: Only imported modules are cached, not None lookups.
    Thread-safety: First access wins; subsequent accesses use cache.
    """
    # 1. Check import cache first (hot path)
    cached = _IMPORT_CACHE.get(name)
    if cached is not None:
        return cached

    # 2. Handle availability flags (lazy module probe)
    if name in _AVAILABILITY_FLAGS:
        if name == "METADATA_EXTRACTOR_AVAILABLE":
            try:
                from . import metadata_extractor as _m

                result = hasattr(_m, "UniversalMetadataExtractor")
            except ImportError:
                result = False
        elif name == "STEGANOGRAPHY_AVAILABLE":
            try:
                from . import stego_detector as _m

                result = hasattr(_m, "StatisticalStegoDetector")
            except ImportError:
                result = False
        elif name == "DIGITAL_GHOST_AVAILABLE":
            try:
                from . import digital_ghost_detector as _m

                result = hasattr(_m, "DigitalGhostDetector")
            except ImportError:
                result = False
        else:  # GIT_FORENSICS_AVAILABLE
            try:
                from . import git_forensics as _m

                result = hasattr(_m, "GitForensicsDetector")
            except ImportError:
                result = False

        _IMPORT_CACHE[name] = result
        return result

    # 3. Search all dispatch tables
    for dispatch_table in _ALL_DISPATCHES:
        if name in dispatch_table:
            submodule, imports = dispatch_table[name]
            try:
                module = __import__(submodule, fromlist=imports)
                for import_name in imports:
                    obj = getattr(module, import_name)
                    _IMPORT_CACHE[import_name] = obj
                    if import_name == name:
                        return obj
            except ImportError:
                # Module not available — cache None for future lookups
                _IMPORT_CACHE[name] = None
                return None

    # 4. Not found in any dispatch table
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── Canonical Exports ───────────────────────────────────────────────────────────

__all__ = [
    # Availability flags
    "METADATA_EXTRACTOR_AVAILABLE",
    "STEGANOGRAPHY_AVAILABLE",
    "DIGITAL_GHOST_AVAILABLE",
    "GIT_FORENSICS_AVAILABLE",
    # Metadata extractor
    "UniversalMetadataExtractor",
    "create_metadata_extractor",
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
    # Steganography (canonical)
    "StatisticalStegoDetector",
    "StegoConfig",
    "StegoResult",
    "ChiSquareResult",
    "RSResult",
    "DCTResult",
    "create_stego_detector",
    "quick_stego_check",
    # Digital Ghost (canonical)
    "DigitalGhostDetector",
    "DigitalGhostAnalysis",
    "GhostSignal",
    "RecoveredContent",
    "detect_digital_ghosts",
    # Git Forensics (canonical, Rust-accelerated)
    "GitForensicsDetector",
    "GitForensicsResult",
    "GitForensicRecord",
    "GitForensicsStats",
    "quick_git_analysis",
]
