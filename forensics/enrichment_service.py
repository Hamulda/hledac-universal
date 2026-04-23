"""
Forensics Enrichment Service
============================

Enriches accepted CanonicalFindings with forensics analysis.
Wraps UniversalMetadataExtractor, steganography_detector, and digital_ghost_detector.

Fail-safe: enrichment failures never crash the caller sprint.
Enrichment is best-effort — absence of forensics data is not an error.

Accepted findings with file-path in payload_text can be enriched with:
- Metadata extraction (EXIF, PDF, DOCX, audio, video, archive)
- Steganography analysis (LSB, histogram, chi-square)
- Digital ghost detection (deleted content, tampering, hidden data)

Integration:
    from forensics.enrichment_service import ForensicsEnricher

    enricher = ForensicsEnricher()
    await enricher.initialize()

    # enrich() returns enrichment dict or None (not a finding object)
    # Callers store the dict themselves (e.g., in LMDB keyed by finding_id)
    enrichment = await enricher.enrich(finding)
    if enrichment:
        await lmdb_store.put(finding.finding_id.encode(), enrichment)

    await enricher.close()

M1 8GB: All heavy dependencies (PIL, pypdf, docx, mutagen) are lazy-loaded
inside enrichment methods. Max 500MB memory per extraction.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Lazy-loaded forensics modules
_MetadataExtractor: Optional[type] = None
_METADATA_EXTRACTOR_AVAILABLE = False

_SteganalysisResult: Optional[type] = None
_STEGANOGRAPHY_AVAILABLE = False

_DigitalGhostResult: Optional[type] = None
_DIGITAL_GHOST_AVAILABLE = False


def _lazy_load_modules() -> None:
    """Load forensics modules lazily on first use."""
    global _MetadataExtractor, _METADATA_EXTRACTOR_AVAILABLE
    global _SteganalysisResult, _STEGANOGRAPHY_AVAILABLE
    global _DigitalGhostResult, _DIGITAL_GHOST_AVAILABLE

    if _MetadataExtractor is not None:
        return  # Already loaded

    # UniversalMetadataExtractor
    try:
        from forensics.metadata_extractor import UniversalMetadataExtractor
        _MetadataExtractor = UniversalMetadataExtractor
        _METADATA_EXTRACTOR_AVAILABLE = True
    except ImportError:
        _MetadataExtractor = None
        _METADATA_EXTRACTOR_AVAILABLE = False

    # SteganalysisResult
    try:
        from forensics.steganography_detector import SteganalysisResult
        _SteganalysisResult = SteganalysisResult
        _STEGANOGRAPHY_AVAILABLE = True
    except ImportError:
        _SteganalysisResult = None
        _STEGANOGRAPHY_AVAILABLE = False

    # DigitalGhostResult
    try:
        from forensics.digital_ghost_detector import DigitalGhostResult
        _DigitalGhostResult = DigitalGhostResult
        _DIGITAL_GHOST_AVAILABLE = True
    except ImportError:
        _DigitalGhostResult = None
        _DIGITAL_GHOST_AVAILABLE = False


# ---------------------------------------------------------------------------
# URL / path extraction from payload_text
# ---------------------------------------------------------------------------

# Supported file extensions for forensics enrichment
_SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp",
    ".pdf", ".docx", ".doc",
    ".mp3", ".flac", ".ogg", ".m4a", ".wav", ".wma",
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
}


def _extract_file_path_from_payload(payload_text: str | None) -> Optional[str]:
    """
    Extract a local file path from payload_text.

    Handles:
    - Direct local paths: /Users/.../file.jpg
    - file:// URLs: file:///tmp/file.pdf
    - Paths with query strings stripped

    Returns None if no suitable file path found or file doesn't exist.
    """
    if not payload_text:
        return None

    # Try file:// URL
    if payload_text.startswith("file://"):
        path_str = payload_text[7:]
        # Strip query/fragment
        path_str = path_str.split("?")[0].split("#")[0]
        path = Path(path_str)
        if path.exists() and path.is_file():
            return str(path)

    # Try direct path
    path = Path(payload_text)
    if not path.is_absolute():
        # Try as relative path from current dir
        path = Path.cwd() / path
    if path.exists() and path.is_file():
        return str(path)

    # Try stripping query strings from URL paths
    clean = payload_text.split("?")[0].split("#")[0]
    if clean != payload_text:
        return _extract_file_path_from_payload(clean)

    return None


def _file_has_forensics_support(file_path: str) -> bool:
    """Check if file extension is supported by forensics enrichment."""
    ext = Path(file_path).suffix.lower()
    return ext in _SUPPORTED_EXTENSIONS


# ---------------------------------------------------------------------------
# ForensicsEnricher
# ---------------------------------------------------------------------------

class ForensicsEnricher:
    """
    Forensics enrichment for CanonicalFindings.

    Enriches findings with file-path in payload_text via:
    - UniversalMetadataExtractor: EXIF, PDF, DOCX, audio, video, archive metadata
    - Steganography analysis: LSB, histogram, chi-square for images
    - Digital ghost detection: deleted content, tampering, hidden data

    Fail-safe: all methods are wrapped in try/except.
    Enrichment failures log a warning and return None — never raise.

    M1 8GB: Extractor uses streaming for large files, bounded memory.
    """

    def __init__(
        self,
        cache_path: Optional[str] = None,
        enable_gps: bool = True,
        enable_audio: bool = True,
        enable_video: bool = False,
    ):
        """
        Initialize enricher.

        Args:
            cache_path: Path to SQLite cache for metadata (None = in-memory).
            enable_gps: Extract GPS coordinates from EXIF.
            enable_audio: Extract audio metadata.
            enable_video: Extract video metadata (requires ffmpeg).
        """
        self._extractor: Optional[Any] = None
        self._cache_path = cache_path
        self._enable_gps = enable_gps
        self._enable_audio = enable_audio
        self._enable_video = enable_video
        self._initialized = False
        self._lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        """Ensure extractor is initialized (idempotent)."""
        if self._initialized and self._extractor is not None:
            return
        async with self._lock:
            if self._initialized and self._extractor is not None:
                return
            _lazy_load_modules()
            if _MetadataExtractor is not None:
                self._extractor = _MetadataExtractor(
                    cache_path=self._cache_path,
                    enable_exif=True,
                    enable_gps=self._enable_gps,
                    enable_reverse_geocode=False,
                    enable_audio=self._enable_audio,
                    enable_video=self._enable_video,
                    calculate_hashes=True,
                )
                await self._extractor.initialize()  # type: ignore[optional-member]
            self._initialized = True

    async def initialize(self) -> None:
        """Public initialize — delegates to _ensure_initialized."""
        await self._ensure_initialized()

    async def close(self) -> None:
        """Close extractor and cleanup resources."""
        async with self._lock:
            if self._extractor is not None:
                await self._extractor.close()
                self._extractor = None
            self._initialized = False

    async def enrich(self, finding: Any) -> Optional[dict[str, Any]]:
        """
        Enrich a CanonicalFinding with forensics analysis.

        Extracts file path from finding.payload_text and runs:
        1. Metadata extraction (UniversalMetadataExtractor)
        2. Steganography analysis (images only)
        3. Digital ghost detection

        Args:
            finding: A CanonicalFinding (or any object with
                     finding_id, payload_text, source_type attributes).

        Returns:
            Enrichment dict with keys:
            - "metadata": MetadataResult.to_dict() or None
            - "steganography": SteganalysisResult.to_dict() or None
            - "ghosts": DigitalGhostResult.to_dict() or None
            - "file_path": the extracted file path or None
            - "enrichment_available": True if file was processable, False otherwise

            Returns None if no file path found or all enrichment failed.
            Never raises — failures return None with a warning log.
        """
        if not self._initialized:
            await self._ensure_initialized()

        # Extract file path from payload_text
        payload_text = getattr(finding, "payload_text", None)
        file_path = _extract_file_path_from_payload(payload_text)

        if not file_path:
            return None

        if not _file_has_forensics_support(file_path):
            return None

        finding_id = getattr(finding, "finding_id", "unknown")
        enrichment: dict[str, Any] = {
            "finding_id": finding_id,
            "file_path": file_path,
            "metadata": None,
            "steganography": None,
            "ghosts": None,
            "enrichment_available": False,
        }

        # 1. Metadata extraction
        if self._extractor is not None:
            try:
                result = await self._extractor.extract(file_path)
                if result is not None:
                    enrichment["metadata"] = result.to_dict()
            except Exception as exc:
                log.debug("Forensics metadata extraction failed for %s: %s", finding_id, exc)

        # 2. Steganography analysis (images only)
        if _STEGANOGRAPHY_AVAILABLE:
            ext = Path(file_path).suffix.lower()
            if ext in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff", ".tif", ".webp"}:
                try:
                    from forensics.steganography_detector import analyze_image_steganography
                    stego_result = analyze_image_steganography(file_path)
                    if stego_result is not None:
                        enrichment["steganography"] = stego_result.to_dict()
                except Exception as exc:
                    log.debug("Steganography analysis failed for %s: %s", finding_id, exc)

        # 3. Digital ghost detection
        if _DIGITAL_GHOST_AVAILABLE:
            try:
                from forensics.digital_ghost_detector import analyze_file_ghosts
                ghost_result = analyze_file_ghosts(file_path)
                if ghost_result is not None:
                    enrichment["ghosts"] = ghost_result.to_dict()
            except Exception as exc:
                log.debug("Digital ghost detection failed for %s: %s", finding_id, exc)

        # Mark enrichment available if any module produced data
        if any(v is not None for k, v in enrichment.items() if k not in ("finding_id", "file_path", "enrichment_available")):
            enrichment["enrichment_available"] = True

        if not enrichment["enrichment_available"]:
            return None

        return enrichment

    async def enrich_batch(self, findings: list[Any]) -> dict[str, dict[str, Any]]:
        """
        Enrich multiple findings concurrently.

        Args:
            findings: List of CanonicalFinding objects.

        Returns:
            Dict mapping finding_id -> enrichment dict (or empty if failed).
            Failures are silent — only successful enrichments are returned.
        """
        if not findings:
            return {}

        semaphore = asyncio.Semaphore(3)  # Max 3 concurrent enrichments (M1 8GB safe)

        async def enrich_one(finding: Any) -> tuple[str, Optional[dict[str, Any]]]:
            async with semaphore:
                finding_id = getattr(finding, "finding_id", "unknown")
                try:
                    result = await self.enrich(finding)
                    return (finding_id, result)
                except Exception as exc:
                    log.debug("Batch enrichment failed for %s: %s", finding_id, exc)
                    return (finding_id, None)

        tasks = [enrich_one(f) for f in findings]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out = {}
        for item in results:
            if isinstance(item, Exception):
                continue
            fid, enrich_data = item
            if enrich_data is not None:
                out[fid] = enrich_data

        return out
