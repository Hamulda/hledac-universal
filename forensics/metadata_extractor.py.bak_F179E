"""
Universal Metadata Extractor
============================

Comprehensive metadata extraction module for OSINT analysis.
Supports images, PDFs, DOCX, audio, video, and archive files.

Features:
- EXIF extraction with GPS coordinates
- PDF document metadata
- Office document properties
- Audio/Video codec information
- Archive structure analysis
- Scrubbing detection
- SQLite caching
- Batch processing

M1 8GB Optimized:
- Streaming for files >100MB
- Memory limit: 500MB per extraction
- Lazy loading of heavy dependencies
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import sqlite3
import struct
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# Optional dependencies - imported lazily inside methods
# PIL, pypdf, docx, mutagen, ffmpeg


# =============================================================================
# HELPERS
# =============================================================================

def _exif_to_float(val):
    """Handle EXIF rational (num, denom) tuples and plain numeric values."""
    if isinstance(val, tuple):
        return val[0] / val[1]
    return float(val)


# =============================================================================
# DATACLASSES
# =============================================================================

@dataclass
class GPSCoordinates:
    """GPS coordinates with accuracy information."""
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    accuracy: Optional[float] = None  # meters
    timestamp: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "accuracy": self.accuracy,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }


@dataclass
class TimelineEvent:
    """Single timeline event from metadata."""
    timestamp: datetime
    event_type: str  # created, modified, accessed, captured, etc.
    source: str  # exif, filesystem, xmp, etc.
    confidence: float = 1.0  # 0.0-1.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass
class AttributionData:
    """Attribution data extracted from metadata."""
    software: Optional[str] = None
    device: Optional[str] = None  # Camera model, phone, etc.
    device_serial: Optional[str] = None
    author: Optional[str] = None
    copyright: Optional[str] = None
    organization: Optional[str] = None
    version: Optional[str] = None  # Software version

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "software": self.software,
            "device": self.device,
            "device_serial": self.device_serial,
            "author": self.author,
            "copyright": self.copyright,
            "organization": self.organization,
            "version": self.version,
        }


@dataclass
class ScrubbingAnalysis:
    """Analysis of potential metadata scrubbing."""
    is_scrubbed: bool
    confidence: float  # 0.0-1.0
    indicators: List[str] = field(default_factory=list)
    missing_expected_fields: List[str] = field(default_factory=list)
    suspicious_patterns: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "is_scrubbed": self.is_scrubbed,
            "confidence": self.confidence,
            "indicators": self.indicators,
            "missing_expected_fields": self.missing_expected_fields,
            "suspicious_patterns": self.suspicious_patterns,
        }


@dataclass
class ImageMetadata:
    """Image-specific metadata."""
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None
    mode: Optional[str] = None  # RGB, RGBA, etc.
    exif: Dict[str, Any] = field(default_factory=dict)
    gps: Optional[GPSCoordinates] = None
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    lens: Optional[str] = None
    focal_length: Optional[float] = None
    exposure_time: Optional[str] = None
    f_number: Optional[float] = None
    iso: Optional[int] = None
    flash: Optional[bool] = None
    orientation: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "mode": self.mode,
            "exif": self.exif,
            "gps": self.gps.to_dict() if self.gps else None,
            "camera_make": self.camera_make,
            "camera_model": self.camera_model,
            "lens": self.lens,
            "focal_length": self.focal_length,
            "exposure_time": self.exposure_time,
            "f_number": self.f_number,
            "iso": self.iso,
            "flash": self.flash,
            "orientation": self.orientation,
        }


@dataclass
class PDFMetadata:
    """PDF document metadata."""
    title: Optional[str] = None
    author: Optional[str] = None
    subject: Optional[str] = None
    creator: Optional[str] = None
    producer: Optional[str] = None
    creation_date: Optional[datetime] = None
    modification_date: Optional[datetime] = None
    num_pages: Optional[int] = None
    pdf_version: Optional[str] = None
    is_encrypted: bool = False
    permissions: Dict[str, bool] = field(default_factory=dict)
    embedded_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "author": self.author,
            "subject": self.subject,
            "creator": self.creator,
            "producer": self.producer,
            "creation_date": self.creation_date.isoformat() if self.creation_date else None,
            "modification_date": self.modification_date.isoformat() if self.modification_date else None,
            "num_pages": self.num_pages,
            "pdf_version": self.pdf_version,
            "is_encrypted": self.is_encrypted,
            "permissions": self.permissions,
            "embedded_files": self.embedded_files,
        }


@dataclass
class DocxMetadata:
    """DOCX document metadata."""
    title: Optional[str] = None
    author: Optional[str] = None
    subject: Optional[str] = None
    keywords: Optional[str] = None
    category: Optional[str] = None
    comments: Optional[str] = None
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
    last_modified_by: Optional[str] = None
    revision: Optional[int] = None
    company: Optional[str] = None
    manager: Optional[str] = None
    template: Optional[str] = None
    total_editing_time: Optional[int] = None  # minutes

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "author": self.author,
            "subject": self.subject,
            "keywords": self.keywords,
            "category": self.category,
            "comments": self.comments,
            "created": self.created.isoformat() if self.created else None,
            "modified": self.modified.isoformat() if self.modified else None,
            "last_modified_by": self.last_modified_by,
            "revision": self.revision,
            "company": self.company,
            "manager": self.manager,
            "template": self.template,
            "total_editing_time": self.total_editing_time,
        }


@dataclass
class AudioMetadata:
    """Audio file metadata."""
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    album_artist: Optional[str] = None
    genre: Optional[str] = None
    year: Optional[int] = None
    track_number: Optional[int] = None
    total_tracks: Optional[int] = None
    disc_number: Optional[int] = None
    total_discs: Optional[int] = None
    composer: Optional[str] = None
    publisher: Optional[str] = None
    copyright: Optional[str] = None
    comments: Optional[str] = None
    lyrics: Optional[str] = None
    duration: Optional[float] = None  # seconds
    bitrate: Optional[int] = None  # kbps
    sample_rate: Optional[int] = None  # Hz
    channels: Optional[int] = None
    codec: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "album_artist": self.album_artist,
            "genre": self.genre,
            "year": self.year,
            "track_number": self.track_number,
            "total_tracks": self.total_tracks,
            "disc_number": self.disc_number,
            "total_discs": self.total_discs,
            "composer": self.composer,
            "publisher": self.publisher,
            "copyright": self.copyright,
            "comments": self.comments,
            "lyrics": self.lyrics,
            "duration": self.duration,
            "bitrate": self.bitrate,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "codec": self.codec,
        }


@dataclass
class VideoMetadata:
    """Video file metadata."""
    title: Optional[str] = None
    duration: Optional[float] = None  # seconds
    bitrate: Optional[int] = None  # kbps
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None
    video_codec: Optional[str] = None
    video_bitrate: Optional[int] = None
    audio_codec: Optional[str] = None
    audio_bitrate: Optional[int] = None
    audio_channels: Optional[int] = None
    audio_sample_rate: Optional[int] = None
    container_format: Optional[str] = None
    creation_time: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "duration": self.duration,
            "bitrate": self.bitrate,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "video_codec": self.video_codec,
            "video_bitrate": self.video_bitrate,
            "audio_codec": self.audio_codec,
            "audio_bitrate": self.audio_bitrate,
            "audio_channels": self.audio_channels,
            "audio_sample_rate": self.audio_sample_rate,
            "container_format": self.container_format,
            "creation_time": self.creation_time.isoformat() if self.creation_time else None,
        }


@dataclass
class ArchiveMetadata:
    """Archive file metadata."""
    archive_type: Optional[str] = None  # zip, rar, 7z, tar, etc.
    num_files: Optional[int] = None
    uncompressed_size: Optional[int] = None  # bytes
    is_encrypted: bool = False
    compression_ratio: Optional[float] = None
    comment: Optional[str] = None
    files: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "archive_type": self.archive_type,
            "num_files": self.num_files,
            "uncompressed_size": self.uncompressed_size,
            "is_encrypted": self.is_encrypted,
            "compression_ratio": self.compression_ratio,
            "comment": self.comment,
            "files": self.files,
        }


@dataclass
class GenericMetadata:
    """Generic file metadata from filesystem."""
    file_name: str
    file_path: str
    file_size: int
    file_extension: str
    mime_type: Optional[str] = None
    created: Optional[datetime] = None
    modified: Optional[datetime] = None
    accessed: Optional[datetime] = None
    permissions: Optional[int] = None
    owner: Optional[str] = None
    group: Optional[str] = None
    inode: Optional[int] = None
    device_id: Optional[int] = None
    hard_links: Optional[int] = None
    blocks: Optional[int] = None
    block_size: Optional[int] = None
    md5_hash: Optional[str] = None
    sha256_hash: Optional[str] = None
    sha1_hash: Optional[str] = None
    entropy: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "file_name": self.file_name,
            "file_path": self.file_path,
            "file_size": self.file_size,
            "file_extension": self.file_extension,
            "mime_type": self.mime_type,
            "created": self.created.isoformat() if self.created else None,
            "modified": self.modified.isoformat() if self.modified else None,
            "accessed": self.accessed.isoformat() if self.accessed else None,
            "permissions": self.permissions,
            "owner": self.owner,
            "group": self.group,
            "inode": self.inode,
            "device_id": self.device_id,
            "hard_links": self.hard_links,
            "blocks": self.blocks,
            "block_size": self.block_size,
            "md5_hash": self.md5_hash,
            "sha256_hash": self.sha256_hash,
            "sha1_hash": self.sha1_hash,
            "entropy": self.entropy,
        }


@dataclass
class MetadataResult:
    """Complete metadata extraction result."""
    file_path: str
    success: bool
    error: Optional[str] = None
    generic: Optional[GenericMetadata] = None
    image: Optional[ImageMetadata] = None
    pdf: Optional[PDFMetadata] = None
    docx: Optional[DocxMetadata] = None
    audio: Optional[AudioMetadata] = None
    video: Optional[VideoMetadata] = None
    archive: Optional[ArchiveMetadata] = None
    timeline: List[TimelineEvent] = field(default_factory=list)
    attribution: Optional[AttributionData] = None
    scrubbing: Optional[ScrubbingAnalysis] = None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)
    extraction_time: float = 0.0  # seconds

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "file_path": self.file_path,
            "success": self.success,
            "error": self.error,
            "generic": self.generic.to_dict() if self.generic else None,
            "image": self.image.to_dict() if self.image else None,
            "pdf": self.pdf.to_dict() if self.pdf else None,
            "docx": self.docx.to_dict() if self.docx else None,
            "audio": self.audio.to_dict() if self.audio else None,
            "video": self.video.to_dict() if self.video else None,
            "archive": self.archive.to_dict() if self.archive else None,
            "timeline": [e.to_dict() for e in self.timeline],
            "attribution": self.attribution.to_dict() if self.attribution else None,
            "scrubbing": self.scrubbing.to_dict() if self.scrubbing else None,
            "raw_metadata": self.raw_metadata,
            "extraction_time": self.extraction_time,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2, default=str)


# =============================================================================
# CACHE MANAGER
# =============================================================================

class MetadataCache:
    """SQLite cache for extracted metadata."""

    MAX_ENTRIES = 10000

    def __init__(self, db_path: Optional[str] = None):
        """Initialize cache.

        Args:
            db_path: Path to SQLite database. If None, uses in-memory cache.
        """
        self.db_path = db_path or ":memory:"
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize database tables (idempotent: safe to call multiple times)."""
        async with self._lock:
            if self._conn is not None:
                return  # Already initialized
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS metadata_cache (
                    file_hash TEXT PRIMARY KEY,
                    mod_time REAL,
                    file_size INTEGER,
                    metadata TEXT,
                    extracted_at REAL
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_extracted_at ON metadata_cache(extracted_at)
            """)
            self._conn.commit()

    async def get(self, file_hash: str, mod_time: float, file_size: int) -> Optional[Dict[str, Any]]:
        """Get cached metadata if valid.

        Args:
            file_hash: Hash of file content
            mod_time: File modification time
            file_size: File size in bytes

        Returns:
            Cached metadata dict or None
        """
        async with self._lock:
            if not self._conn:
                return None

            cursor = self._conn.execute(
                "SELECT metadata FROM metadata_cache WHERE file_hash = ? AND mod_time = ? AND file_size = ?",
                (file_hash, mod_time, file_size)
            )
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            return None

    async def set(self, file_hash: str, mod_time: float, file_size: int, metadata: Dict[str, Any]) -> None:
        """Cache metadata.

        Args:
            file_hash: Hash of file content
            mod_time: File modification time
            file_size: File size in bytes
            metadata: Metadata dict to cache
        """
        async with self._lock:
            if not self._conn:
                return

            # Check size and cleanup if needed
            cursor = self._conn.execute("SELECT COUNT(*) FROM metadata_cache")
            count = cursor.fetchone()[0]
            if count >= self.MAX_ENTRIES:
                # Remove oldest entries
                self._conn.execute(
                    "DELETE FROM metadata_cache WHERE file_hash IN (SELECT file_hash FROM metadata_cache ORDER BY extracted_at ASC LIMIT ?)",
                    (self.MAX_ENTRIES // 10,)
                )

            self._conn.execute(
                """INSERT OR REPLACE INTO metadata_cache
                   (file_hash, mod_time, file_size, metadata, extracted_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (file_hash, mod_time, file_size, json.dumps(metadata), datetime.now().timestamp())
            )
            self._conn.commit()

    async def clear(self) -> None:
        """Clear all cached entries."""
        async with self._lock:
            if self._conn:
                self._conn.execute("DELETE FROM metadata_cache")
                self._conn.commit()

    async def close(self) -> None:
        """Close database connection."""
        async with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


# =============================================================================
# MAIN EXTRACTOR CLASS
# =============================================================================

class UniversalMetadataExtractor:
    """Universal metadata extractor for OSINT analysis.

    Extracts comprehensive metadata from various file types including
    images, PDFs, documents, audio, video, and archives.

    M1 8GB Optimized:
    - Streaming for files >100MB
    - Max 500MB memory per extraction
    - Lazy loading of heavy dependencies
    - SQLite caching for performance

    Example:
        extractor = UniversalMetadataExtractor()
        await extractor.initialize()

        result = await extractor.extract("/path/to/file.jpg")
        print(result.to_json())

        await extractor.close()
    """

    # =========================================================================
    # ENRICHMENT LAYER — NOT AUTHORITY
    # -------------------------------------------------------------------------
    # This module is an ENRICHMENT layer only. It extracts and enriches metadata
    # from files. It does NOT hold authority over:
    #
    # - Retrieval: does not fetch or resolve data from external sources
    # - Export:    does not own export pipelines or output formats beyond
    #              returning plain dict/json from its own dataclasses
    # - Vault:     does not manage sensitive data storage or access control
    # - PII Gate:  does not perform sanitization; extracted raw data may
    #              contain PII which callers are responsible for handling
    # - Network:   enable_reverse_geocode is a NO-OP stub (always returns None);
    #              no implicit network calls or background geocoding services
    # - Orchestrator: no pipeline ownership or decision-making authority
    #
    # Heavy optional dependencies (PIL, pypdf, docx, mutagen, tarfile, zipfile)
    # are lazy-loaded inside extraction methods, never at module level.
    # =========================================================================

    def __init__(
        self,
        cache_path: Optional[str] = None,
        enable_exif: bool = True,
        enable_gps: bool = True,
        enable_reverse_geocode: bool = False,
        enable_audio: bool = True,
        enable_video: bool = False,
        calculate_hashes: bool = True,
        hash_algorithms: Optional[List[str]] = None,
        max_file_size: int = 1073741824,  # 1GB
        batch_size: int = 100,
    ):
        """Initialize extractor.

        Args:
            cache_path: Path to SQLite cache database
            enable_exif: Enable EXIF extraction from images
            enable_gps: Enable GPS coordinate extraction
            enable_reverse_geocode: Enable reverse geocoding (no-op stub: always returns None)
            enable_audio: Enable audio metadata extraction
            enable_video: Enable video metadata extraction (requires ffmpeg)
            calculate_hashes: Calculate file hashes
            hash_algorithms: List of hash algorithms (md5, sha1, sha256)
            max_file_size: Maximum file size to process (bytes)
            batch_size: Batch size for batch processing
        """
        self.cache = MetadataCache(cache_path)
        self.enable_exif = enable_exif
        self.enable_gps = enable_gps
        self.enable_reverse_geocode = enable_reverse_geocode
        self.enable_audio = enable_audio
        self.enable_video = enable_video
        self.calculate_hashes = calculate_hashes
        self.hash_algorithms = hash_algorithms or ["md5", "sha256"]
        self.max_file_size = max_file_size
        self.batch_size = batch_size

        self._initialized = False
        self._semaphore = asyncio.Semaphore(5)  # Limit concurrent extractions

    async def initialize(self) -> None:
        """Initialize extractor and cache."""
        await self.cache.initialize()
        self._initialized = True

    async def close(self) -> None:
        """Close extractor and cleanup resources."""
        await self.cache.close()
        self._initialized = False

    def _get_file_hash(self, file_path: str) -> Tuple[str, float, int]:
        """Calculate a partial content hash and get modification time.

        For files larger than 2MB, this hashes only the first 1MB and the last 1MB.
        For files 2MB or smaller, the full content is hashed.
        This is a bounded strategy to avoid reading entire large files into memory.

        Args:
            file_path: Path to file

        Returns:
            Tuple of (partial_content_hash, mod_time, file_size)
            Note: partial_content_hash is md5 of first+last 1MB for large files
        """
        stat = os.stat(file_path)
        mod_time = stat.st_mtime
        file_size = stat.st_size

        # Partial hash: first and last 1MB for large files (bounded I/O, M1-safe)
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            if file_size <= 2 * 1024 * 1024:
                hasher.update(f.read())
            else:
                hasher.update(f.read(1024 * 1024))
                f.seek(-1024 * 1024, 2)
                hasher.update(f.read())

        return hasher.hexdigest(), mod_time, file_size

    def _calculate_full_hashes(self, file_path: str) -> Dict[str, str]:
        """Calculate full file hashes.

        Args:
            file_path: Path to file

        Returns:
            Dict of algorithm -> hash
        """
        hashes = {}
        hashers = {}

        for algo in self.hash_algorithms:
            if algo == "md5":
                hashers[algo] = hashlib.md5()
            elif algo == "sha1":
                hashers[algo] = hashlib.sha1()
            elif algo == "sha256":
                hashers[algo] = hashlib.sha256()

        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                for hasher in hashers.values():
                    hasher.update(chunk)

        for algo, hasher in hashers.items():
            hashes[algo] = hasher.hexdigest()

        return hashes

    def _calculate_entropy(self, file_path: str) -> float:
        """Calculate Shannon entropy of file.

        Args:
            file_path: Path to file

        Returns:
            Shannon entropy in bits (0-8)
        """
        byte_counts = [0] * 256
        total_bytes = 0

        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                for byte in chunk:
                    byte_counts[byte] += 1
                    total_bytes += 1

        if total_bytes == 0:
            return 0.0

        entropy = 0.0
        for count in byte_counts:
            if count > 0:
                p = count / total_bytes
                entropy -= p * math.log2(p)  # correct Shannon entropy

        return entropy

    async def extract(self, file_path: str) -> MetadataResult:
        """Extract metadata from a single file.

        Args:
            file_path: Path to file to analyze

        Returns:
            MetadataResult with all extracted metadata
        """
        import time
        start_time = time.time()

        async with self._semaphore:
            path = Path(file_path)

            if not path.exists():
                return MetadataResult(
                    file_path=file_path,
                    success=False,
                    error="File not found"
                )

            try:
                # Check cache
                file_hash, mod_time, file_size = self._get_file_hash(file_path)
                cached = await self.cache.get(file_hash, mod_time, file_size)
                if cached:
                    result = self._result_from_dict(cached)
                    result.extraction_time = time.time() - start_time
                    return result

                # Check file size
                if file_size > self.max_file_size:
                    return MetadataResult(
                        file_path=file_path,
                        success=False,
                        error=f"File too large: {file_size} bytes (max: {self.max_file_size})"
                    )

                # Extract generic metadata
                generic = await self._extract_generic_metadata(file_path)

                # Determine file type and extract specific metadata
                ext = path.suffix.lower()
                result = MetadataResult(
                    file_path=file_path,
                    success=True,
                    generic=generic
                )

                # Image files
                if ext in {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp"}:
                    result.image = await self._extract_image_exif(file_path)

                # PDF files
                elif ext == ".pdf":
                    result.pdf = await self._extract_pdf_metadata(file_path)

                # DOCX files
                elif ext == ".docx":
                    result.docx = await self._extract_docx_metadata(file_path)

                # Audio files
                elif ext in {".mp3", ".flac", ".ogg", ".m4a", ".wav", ".wma"} and self.enable_audio:
                    result.audio = await self._extract_audio_metadata(file_path)

                # Video files
                elif ext in {".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv"} and self.enable_video:
                    result.video = await self._extract_video_metadata(file_path)

                # Archive files
                elif ext in {".zip", ".tar", ".gz", ".bz2", ".7z", ".rar"}:
                    result.archive = await self._extract_archive_metadata(file_path)

                # Build timeline and attribution
                result.timeline = self._build_timeline(result)
                result.attribution = self._build_attribution(result)
                result.scrubbing = self._detect_scrubbing(result)

                # Cache result
                await self.cache.set(file_hash, mod_time, file_size, result.to_dict())

                result.extraction_time = time.time() - start_time
                return result

            except Exception as e:
                return MetadataResult(
                    file_path=file_path,
                    success=False,
                    error=str(e),
                    extraction_time=time.time() - start_time
                )

    async def extract_batch(self, file_paths: List[str]) -> List[MetadataResult]:
        """Extract metadata from multiple files in batches.

        Args:
            file_paths: List of file paths to analyze

        Returns:
            List of MetadataResult objects
        """
        results = []

        for i in range(0, len(file_paths), self.batch_size):
            batch = file_paths[i:i + self.batch_size]
            tasks = [self.extract(path) for path in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for path, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    results.append(MetadataResult(
                        file_path=path,
                        success=False,
                        error=str(result)
                    ))
                else:
                    results.append(result)

        return results

    async def _extract_generic_metadata(self, file_path: str) -> GenericMetadata:
        """Extract generic filesystem metadata.

        Args:
            file_path: Path to file

        Returns:
            GenericMetadata object
        """
        path = Path(file_path)
        stat = os.stat(file_path)

        # Calculate hashes if enabled
        hashes = {}
        if self.calculate_hashes:
            hashes = self._calculate_full_hashes(file_path)

        # Calculate entropy
        entropy = self._calculate_entropy(file_path)

        # Try to get owner/group names
        owner = None
        group = None
        try:
            import pwd
            import grp
            owner = pwd.getpwuid(stat.st_uid).pw_name
            group = grp.getgrgid(stat.st_gid).gr_name
        except (ImportError, KeyError):
            pass

        # Guess MIME type
        mime_type = None
        try:
            import mimetypes
            mime_type, _ = mimetypes.guess_type(file_path)
        except ImportError:
            pass

        return GenericMetadata(
            file_name=path.name,
            file_path=str(path.absolute()),
            file_size=stat.st_size,
            file_extension=path.suffix.lower(),
            mime_type=mime_type,
            created=datetime.fromtimestamp(stat.st_ctime),
            modified=datetime.fromtimestamp(stat.st_mtime),
            accessed=datetime.fromtimestamp(stat.st_atime),
            permissions=stat.st_mode,
            owner=owner,
            group=group,
            inode=stat.st_ino,
            device_id=stat.st_dev,
            hard_links=stat.st_nlink,
            blocks=getattr(stat, 'st_blocks', None),
            block_size=getattr(stat, 'st_blksize', None),
            md5_hash=hashes.get("md5"),
            sha256_hash=hashes.get("sha256"),
            sha1_hash=hashes.get("sha1"),
            entropy=entropy,
        )

    async def _extract_image_exif(self, file_path: str) -> Optional[ImageMetadata]:
        """Extract EXIF metadata from image.

        Args:
            file_path: Path to image file

        Returns:
            ImageMetadata object or None
        """
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS, GPSTAGS
        except ImportError:
            return None

        try:
            with Image.open(file_path) as img:
                metadata = ImageMetadata(
                    width=img.width,
                    height=img.height,
                    format=img.format,
                    mode=img.mode,
                )

                if not self.enable_exif:
                    return metadata

                # Extract EXIF
                exif = img._getexif()
                if exif:
                    exif_data = {}
                    for tag_id, value in exif.items():
                        tag = TAGS.get(tag_id, tag_id)
                        # Preserve numeric shape for rationals/tuples so downstream
                        # parsers (FocalLength, FNumber, ISOSpeedRatings, GPSAltitude)
                        # can reconstruct the value correctly. Convert simple types only.
                        if isinstance(value, tuple):
                            exif_data[tag] = value
                        else:
                            exif_data[tag] = value

                    # Extract specific fields
                    metadata.camera_make = exif_data.get("Make")
                    metadata.camera_model = exif_data.get("Model")
                    metadata.lens = exif_data.get("LensModel")

                    if "FocalLength" in exif_data:
                        try:
                            fl = exif_data["FocalLength"]
                            metadata.focal_length = _exif_to_float(fl)
                        except (ValueError, TypeError):
                            pass

                    if "ExposureTime" in exif_data:
                        metadata.exposure_time = str(exif_data["ExposureTime"])

                    if "FNumber" in exif_data:
                        try:
                            fn = exif_data["FNumber"]
                            metadata.f_number = _exif_to_float(fn)
                        except (ValueError, TypeError):
                            pass

                    if "ISOSpeedRatings" in exif_data:
                        try:
                            iso = exif_data["ISOSpeedRatings"]
                            metadata.iso = int(_exif_to_float(iso))
                        except (ValueError, TypeError):
                            pass

                    if "Flash" in exif_data:
                        flash = exif_data["Flash"]
                        # Flash is typically 0/1 int, not string "0"; handle both cases
                        try:
                            metadata.flash = bool(int(flash)) if not isinstance(flash, bool) else flash
                        except (ValueError, TypeError):
                            pass

                    if "Orientation" in exif_data:
                        try:
                            metadata.orientation = int(exif_data["Orientation"])
                        except ValueError:
                            pass

                    # Extract GPS
                    # img._getexif() returns dict with INTEGER tag IDs (e.g., 34853 for GPSInfo),
                    # not string "GPSInfo" keys. The lookup must use the integer tag ID.
                    if self.enable_gps:
                        gps_info = exif.get(34853) or exif.get("GPSInfo")
                        if gps_info:
                            gps_data = {}
                            for key in gps_info.keys():
                                decode = GPSTAGS.get(key, key)
                                gps_data[decode] = gps_info[key]

                            metadata.gps = self._parse_gps_data(gps_data)

                return metadata

        except Exception:
            return None

    def _parse_gps_data(self, gps_data: Dict[str, Any]) -> Optional[GPSCoordinates]:
        """Parse GPS data from EXIF.

        Args:
            gps_data: GPS data dict from EXIF

        Returns:
            GPSCoordinates object or None
        """
        try:
            def dms_to_decimal(dms, ref):
                """Convert DMS to decimal degrees. Handles EXIF rationals (num, denom) and floats."""
                degrees = _exif_to_float(dms[0])
                minutes = _exif_to_float(dms[1]) / 60.0
                seconds = _exif_to_float(dms[2]) / 3600.0
                decimal = degrees + minutes + seconds
                if ref in ["S", "W"]:
                    decimal = -decimal
                return decimal

            lat = None
            lon = None
            alt = None

            if "GPSLatitude" in gps_data and "GPSLatitudeRef" in gps_data:
                lat = dms_to_decimal(gps_data["GPSLatitude"], gps_data["GPSLatitudeRef"])

            if "GPSLongitude" in gps_data and "GPSLongitudeRef" in gps_data:
                lon = dms_to_decimal(gps_data["GPSLongitude"], gps_data["GPSLongitudeRef"])

            if "GPSAltitude" in gps_data:
                alt_raw = gps_data["GPSAltitude"]
                alt = _exif_to_float(alt_raw) if isinstance(alt_raw, tuple) else float(alt_raw)

            if lat is not None and lon is not None:
                return GPSCoordinates(latitude=lat, longitude=lon, altitude=alt)

            return None

        except Exception:
            return None

    async def _reverse_geocode(self, lat: float, lon: float) -> Optional[str]:
        """Reverse geocode coordinates to address.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Address string or None
        """
        if not self.enable_reverse_geocode:
            return None

        # This would require an external service
        # For now, return None to avoid external dependencies
        return None

    async def _extract_pdf_metadata(self, file_path: str) -> Optional[PDFMetadata]:
        """Extract metadata from PDF file.

        Args:
            file_path: Path to PDF file

        Returns:
            PDFMetadata object or None
        """
        try:
            import pypdf
        except ImportError:
            try:
                import PyPDF2 as pypdf
            except ImportError:
                return None

        try:
            with open(file_path, "rb") as f:
                reader = pypdf.PdfReader(f)
                info = reader.metadata

                metadata = PDFMetadata(
                    num_pages=len(reader.pages),
                    is_encrypted=reader.is_encrypted,
                )

                if info:
                    metadata.title = info.get("/Title")
                    metadata.author = info.get("/Author")
                    metadata.subject = info.get("/Subject")
                    metadata.creator = info.get("/Creator")
                    metadata.producer = info.get("/Producer")

                    # Parse dates
                    if "/CreationDate" in info:
                        metadata.creation_date = self._parse_pdf_date(info["/CreationDate"])
                    if "/ModDate" in info:
                        metadata.modification_date = self._parse_pdf_date(info["/ModDate"])

                # Get PDF version
                if hasattr(reader, "pdf_header"):
                    header = reader.pdf_header
                    if header:
                        metadata.pdf_version = header.replace("%PDF-", "")

                return metadata

        except Exception:
            return None

    def _parse_pdf_date(self, date_str: str) -> Optional[datetime]:
        """Parse PDF date string.

        Args:
            date_str: PDF date string (D:YYYYMMDDHHmmSS)

        Returns:
            datetime object or None
        """
        try:
            if date_str.startswith("D:"):
                date_str = date_str[2:]

            # Remove timezone offset if present
            if "+" in date_str:
                date_str = date_str.split("+")[0]
            if "-" in date_str and date_str.index("-") > 4:
                date_str = date_str.split("-")[0]
            if "Z" in date_str:
                date_str = date_str.replace("Z", "")

            # Parse
            if len(date_str) >= 14:
                return datetime(
                    int(date_str[0:4]),
                    int(date_str[4:6]),
                    int(date_str[6:8]),
                    int(date_str[8:10]),
                    int(date_str[10:12]),
                    int(date_str[12:14])
                )
            elif len(date_str) >= 8:
                return datetime(
                    int(date_str[0:4]),
                    int(date_str[4:6]),
                    int(date_str[6:8])
                )

            return None

        except Exception:
            return None

    async def _extract_docx_metadata(self, file_path: str) -> Optional[DocxMetadata]:
        """Extract metadata from DOCX file.

        Args:
            file_path: Path to DOCX file

        Returns:
            DocxMetadata object or None
        """
        try:
            import docx
        except ImportError:
            return None

        try:
            doc = docx.Document(file_path)
            props = doc.core_properties

            return DocxMetadata(
                title=props.title,
                author=props.author,
                subject=props.subject,
                keywords=props.keywords,
                category=props.category,
                comments=props.comments,
                created=props.created,
                modified=props.modified,
                last_modified_by=props.last_modified_by,
                revision=props.revision,
                company=props.company,
                manager=props.manager,
                template=props.template,
                total_editing_time=props.total_editing_time,
            )

        except Exception:
            return None

    async def _extract_audio_metadata(self, file_path: str) -> Optional[AudioMetadata]:
        """Extract metadata from audio file.

        Args:
            file_path: Path to audio file

        Returns:
            AudioMetadata object or None
        """
        try:
            from mutagen import File as MutagenFile
            from mutagen.mp3 import MP3
        except ImportError:
            return None

        try:
            audio = MutagenFile(file_path)
            if not audio:
                return None

            metadata = AudioMetadata()

            # Duration and technical info
            if hasattr(audio.info, "length"):
                metadata.duration = audio.info.length
            if hasattr(audio.info, "bitrate"):
                metadata.bitrate = audio.info.bitrate // 1000
            if hasattr(audio.info, "sample_rate"):
                metadata.sample_rate = audio.info.sample_rate
            if hasattr(audio.info, "channels"):
                metadata.channels = audio.info.channels

            # Codec
            metadata.codec = type(audio).__name__.lower()

            # Tags
            if audio.tags:
                tag_mapping = {
                    "TIT2": "title",
                    "TPE1": "artist",
                    "TALB": "album",
                    "TPE2": "album_artist",
                    "TCON": "genre",
                    "TYER": "year",
                    "TDRC": "year",
                    "TRCK": "track_number",
                    "TPOS": "disc_number",
                    "TCOM": "composer",
                    "TPUB": "publisher",
                    "TCOP": "copyright",
                    "COMM": "comments",
                    "USLT": "lyrics",
                }

                for tag, field in tag_mapping.items():
                    if tag in audio.tags:
                        value = str(audio.tags[tag])
                        if field == "year":
                            try:
                                setattr(metadata, field, int(str(value)[:4]))
                            except ValueError:
                                pass
                        elif field in ["track_number", "disc_number"]:
                            try:
                                num = str(value).split("/")[0]
                                setattr(metadata, field, int(num))
                            except ValueError:
                                pass
                        else:
                            setattr(metadata, field, value)

            return metadata

        except Exception:
            return None

    async def _extract_video_metadata(self, file_path: str) -> Optional[VideoMetadata]:
        """Extract metadata from video file.

        Args:
            file_path: Path to video file

        Returns:
            VideoMetadata object or None
        """
        # Video extraction requires ffmpeg-python or similar
        # This is a placeholder that returns basic info
        try:
            import os
            stat = os.stat(file_path)

            return VideoMetadata(
                container_format=Path(file_path).suffix.lower().lstrip("."),
            )

        except Exception:
            return None

    async def _extract_archive_metadata(self, file_path: str) -> Optional[ArchiveMetadata]:
        """Extract metadata from archive file.

        Args:
            file_path: Path to archive file

        Returns:
            ArchiveMetadata object or None
        """
        ext = Path(file_path).suffix.lower()

        if ext == ".zip":
            return await self._extract_zip_metadata(file_path)
        elif ext in {".tar", ".gz", ".bz2"}:
            return await self._extract_tar_metadata(file_path)

        # RAR and 7Z require optional dependencies
        return ArchiveMetadata(archive_type=ext.lstrip("."))

    async def _extract_zip_metadata(self, file_path: str) -> ArchiveMetadata:
        """Extract ZIP archive metadata.

        Args:
            file_path: Path to ZIP file

        Returns:
            ArchiveMetadata object
        """
        metadata = ArchiveMetadata(archive_type="zip")

        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                metadata.num_files = len(zf.namelist())
                metadata.comment = zf.comment.decode("utf-8", errors="ignore") if zf.comment else None

                total_uncompressed = 0
                total_compressed = 0

                files = []
                for info in zf.infolist():
                    total_uncompressed += info.file_size
                    total_compressed += info.compress_size

                    files.append({
                        "name": info.filename,
                        "size": info.file_size,
                        "compressed_size": info.compress_size,
                        "is_directory": info.is_dir(),
                        "modified": datetime(*info.date_time),
                        "crc": info.CRC,
                    })

                metadata.uncompressed_size = total_uncompressed
                metadata.files = files

                if total_uncompressed > 0:
                    metadata.compression_ratio = total_compressed / total_uncompressed

                # Check for encryption
                for info in zf.infolist():
                    if info.flag_bits & 0x1:
                        metadata.is_encrypted = True
                        break

        except Exception:
            pass

        return metadata

    async def _extract_tar_metadata(self, file_path: str) -> ArchiveMetadata:
        """Extract TAR archive metadata.

        Args:
            file_path: Path to TAR file

        Returns:
            ArchiveMetadata object
        """
        import tarfile

        metadata = ArchiveMetadata(archive_type="tar")

        try:
            with tarfile.open(file_path, "r:*") as tf:
                members = tf.getmembers()
                metadata.num_files = len(members)

                total_size = 0
                files = []

                for member in members:
                    total_size += member.size
                    files.append({
                        "name": member.name,
                        "size": member.size,
                        "is_directory": member.isdir(),
                        "modified": datetime.fromtimestamp(member.mtime),
                        "mode": member.mode,
                        "uid": member.uid,
                        "gid": member.gid,
                    })

                metadata.uncompressed_size = total_size
                metadata.files = files

        except Exception:
            pass

        return metadata

    def _build_timeline(self, result: MetadataResult) -> List[TimelineEvent]:
        """Build timeline from all extracted metadata.

        Args:
            result: MetadataResult with extracted data

        Returns:
            List of TimelineEvent objects
        """
        events = []

        # Generic filesystem times
        if result.generic:
            if result.generic.created:
                events.append(TimelineEvent(
                    timestamp=result.generic.created,
                    event_type="created",
                    source="filesystem",
                ))
            if result.generic.modified:
                events.append(TimelineEvent(
                    timestamp=result.generic.modified,
                    event_type="modified",
                    source="filesystem",
                ))
            if result.generic.accessed:
                events.append(TimelineEvent(
                    timestamp=result.generic.accessed,
                    event_type="accessed",
                    source="filesystem",
                ))

        # Image EXIF times
        if result.image and result.image.exif:
            exif = result.image.exif
            if "DateTime" in exif:
                try:
                    dt = datetime.strptime(exif["DateTime"], "%Y:%m:%d %H:%M:%S")
                    events.append(TimelineEvent(
                        timestamp=dt,
                        event_type="captured",
                        source="exif",
                    ))
                except ValueError:
                    pass
            if "DateTimeOriginal" in exif:
                try:
                    dt = datetime.strptime(exif["DateTimeOriginal"], "%Y:%m:%d %H:%M:%S")
                    events.append(TimelineEvent(
                        timestamp=dt,
                        event_type="captured_original",
                        source="exif",
                    ))
                except ValueError:
                    pass
            if "DateTimeDigitized" in exif:
                try:
                    dt = datetime.strptime(exif["DateTimeDigitized"], "%Y:%m:%d %H:%M:%S")
                    events.append(TimelineEvent(
                        timestamp=dt,
                        event_type="digitized",
                        source="exif",
                    ))
                except ValueError:
                    pass

        # PDF times
        if result.pdf:
            if result.pdf.creation_date:
                events.append(TimelineEvent(
                    timestamp=result.pdf.creation_date,
                    event_type="created",
                    source="pdf_metadata",
                ))
            if result.pdf.modification_date:
                events.append(TimelineEvent(
                    timestamp=result.pdf.modification_date,
                    event_type="modified",
                    source="pdf_metadata",
                ))

        # DOCX times
        if result.docx:
            if result.docx.created:
                events.append(TimelineEvent(
                    timestamp=result.docx.created,
                    event_type="created",
                    source="docx_core_properties",
                ))
            if result.docx.modified:
                events.append(TimelineEvent(
                    timestamp=result.docx.modified,
                    event_type="modified",
                    source="docx_core_properties",
                ))

        # Sort by timestamp
        events.sort(key=lambda e: e.timestamp or datetime.min)

        return events

    def _build_attribution(self, result: MetadataResult) -> AttributionData:
        """Build attribution data from all extracted metadata.

        Args:
            result: MetadataResult with extracted data

        Returns:
            AttributionData object
        """
        attr = AttributionData()

        # Image attribution
        if result.image:
            if result.image.camera_make or result.image.camera_model:
                attr.device = " ".join(filter(None, [result.image.camera_make, result.image.camera_model]))
            if result.image.exif.get("Software"):
                attr.software = result.image.exif.get("Software")

        # PDF attribution
        if result.pdf:
            attr.author = result.pdf.author
            attr.software = result.pdf.creator or result.pdf.producer

        # DOCX attribution
        if result.docx:
            attr.author = result.docx.author
            attr.software = result.docx.template
            attr.organization = result.docx.company

        # Audio attribution
        if result.audio:
            attr.author = result.audio.artist or result.audio.composer
            attr.software = result.audio.publisher
            attr.copyright = result.audio.copyright

        # Video attribution
        if result.video:
            attr.software = result.video.container_format

        return attr

    def _detect_scrubbing(self, result: MetadataResult) -> ScrubbingAnalysis:
        """Detect potential metadata scrubbing.

        Args:
            result: MetadataResult with extracted data

        Returns:
            ScrubbingAnalysis object
        """
        indicators = []
        missing = []
        suspicious = []
        confidence = 0.0

        # Check for missing expected fields per file type
        if result.image:
            if not result.image.exif:
                indicators.append("No EXIF data found in image")
                missing.append("EXIF")
            else:
                expected = ["Make", "Model", "DateTime"]
                for field in expected:
                    if field not in result.image.exif:
                        missing.append(f"EXIF:{field}")

            if result.image.gps is None and self.enable_gps:
                # GPS commonly stripped, not strong indicator alone
                pass

        if result.pdf:
            if not any([result.pdf.author, result.pdf.creator, result.pdf.producer]):
                indicators.append("No attribution metadata in PDF")
                missing.extend(["Author", "Creator", "Producer"])

        if result.docx:
            if not result.docx.author:
                indicators.append("No author in DOCX")
                missing.append("Author")
            if not result.docx.created:
                indicators.append("No creation date in DOCX")
                missing.append("Created")

        # Check for suspicious patterns
        if result.generic:
            # Identical timestamps
            if result.generic.created and result.generic.modified:
                if result.generic.created == result.generic.modified:
                    suspicious.append("Creation and modification timestamps are identical")
                    confidence += 0.2

        # Calculate confidence
        if missing:
            confidence += min(len(missing) * 0.1, 0.5)
        if indicators:
            confidence += min(len(indicators) * 0.15, 0.4)
        if suspicious:
            confidence += min(len(suspicious) * 0.1, 0.2)

        confidence = min(confidence, 1.0)

        return ScrubbingAnalysis(
            is_scrubbed=confidence > 0.5,
            confidence=confidence,
            indicators=indicators,
            missing_expected_fields=missing,
            suspicious_patterns=suspicious,
        )

    def _result_from_dict(self, data: Dict[str, Any]) -> MetadataResult:
        """Reconstruct MetadataResult from dictionary.

        Args:
            data: Dictionary from to_dict()

        Returns:
            MetadataResult object
        """
        result = MetadataResult(
            file_path=data.get("file_path", ""),
            success=data.get("success", False),
            error=data.get("error"),
            extraction_time=data.get("extraction_time", 0.0),
            raw_metadata=data.get("raw_metadata", {}),
        )

        # Reconstruct sub-objects
        if data.get("generic"):
            g = data["generic"]
            result.generic = GenericMetadata(
                file_name=g.get("file_name", ""),
                file_path=g.get("file_path", ""),
                file_size=g.get("file_size", 0),
                file_extension=g.get("file_extension", ""),
                mime_type=g.get("mime_type"),
                created=datetime.fromisoformat(g["created"]) if g.get("created") else None,
                modified=datetime.fromisoformat(g["modified"]) if g.get("modified") else None,
                accessed=datetime.fromisoformat(g["accessed"]) if g.get("accessed") else None,
                permissions=g.get("permissions"),
                owner=g.get("owner"),
                group=g.get("group"),
                inode=g.get("inode"),
                device_id=g.get("device_id"),
                hard_links=g.get("hard_links"),
                blocks=g.get("blocks"),
                block_size=g.get("block_size"),
                md5_hash=g.get("md5_hash"),
                sha256_hash=g.get("sha256_hash"),
                sha1_hash=g.get("sha1_hash"),
                entropy=g.get("entropy"),
            )

        if data.get("image"):
            img = data["image"]
            gps = None
            if img.get("gps"):
                gps_data = img["gps"]
                ts = gps_data.get("timestamp")
                gps = GPSCoordinates(
                    latitude=gps_data.get("latitude", 0.0),
                    longitude=gps_data.get("longitude", 0.0),
                    altitude=gps_data.get("altitude"),
                    accuracy=gps_data.get("accuracy"),
                    timestamp=datetime.fromisoformat(ts) if ts else None,
                )
            result.image = ImageMetadata(
                width=img.get("width"),
                height=img.get("height"),
                format=img.get("format"),
                mode=img.get("mode"),
                exif=img.get("exif", {}),
                gps=gps,
                camera_make=img.get("camera_make"),
                camera_model=img.get("camera_model"),
                lens=img.get("lens"),
                focal_length=img.get("focal_length"),
                exposure_time=img.get("exposure_time"),
                f_number=img.get("f_number"),
                iso=img.get("iso"),
                flash=img.get("flash"),
                orientation=img.get("orientation"),
            )

        if data.get("pdf"):
            pdf = data["pdf"]
            result.pdf = PDFMetadata(
                title=pdf.get("title"),
                author=pdf.get("author"),
                subject=pdf.get("subject"),
                creator=pdf.get("creator"),
                producer=pdf.get("producer"),
                creation_date=datetime.fromisoformat(pdf["creation_date"]) if pdf.get("creation_date") else None,
                modification_date=datetime.fromisoformat(pdf["modification_date"]) if pdf.get("modification_date") else None,
                num_pages=pdf.get("num_pages"),
                pdf_version=pdf.get("pdf_version"),
                is_encrypted=pdf.get("is_encrypted", False),
                permissions=pdf.get("permissions", {}),
                embedded_files=pdf.get("embedded_files", []),
            )

        if data.get("docx"):
            d = data["docx"]
            result.docx = DocxMetadata(
                title=d.get("title"),
                author=d.get("author"),
                subject=d.get("subject"),
                keywords=d.get("keywords"),
                category=d.get("category"),
                comments=d.get("comments"),
                created=datetime.fromisoformat(d["created"]) if d.get("created") else None,
                modified=datetime.fromisoformat(d["modified"]) if d.get("modified") else None,
                last_modified_by=d.get("last_modified_by"),
                revision=d.get("revision"),
                company=d.get("company"),
                manager=d.get("manager"),
                template=d.get("template"),
                total_editing_time=d.get("total_editing_time"),
            )

        if data.get("audio"):
            a = data["audio"]
            result.audio = AudioMetadata(**a)

        if data.get("video"):
            v = data["video"]
            result.video = VideoMetadata(
                title=v.get("title"),
                duration=v.get("duration"),
                bitrate=v.get("bitrate"),
                width=v.get("width"),
                height=v.get("height"),
                fps=v.get("fps"),
                video_codec=v.get("video_codec"),
                video_bitrate=v.get("video_bitrate"),
                audio_codec=v.get("audio_codec"),
                audio_bitrate=v.get("audio_bitrate"),
                audio_channels=v.get("audio_channels"),
                audio_sample_rate=v.get("audio_sample_rate"),
                container_format=v.get("container_format"),
                creation_time=datetime.fromisoformat(v["creation_time"]) if v.get("creation_time") else None,
            )

        if data.get("archive"):
            a = data["archive"]
            result.archive = ArchiveMetadata(
                archive_type=a.get("archive_type"),
                num_files=a.get("num_files"),
                uncompressed_size=a.get("uncompressed_size"),
                is_encrypted=a.get("is_encrypted", False),
                compression_ratio=a.get("compression_ratio"),
                comment=a.get("comment"),
                files=a.get("files", []),
            )

        if data.get("timeline"):
            result.timeline = [
                TimelineEvent(
                    timestamp=datetime.fromisoformat(e["timestamp"]),
                    event_type=e["event_type"],
                    source=e["source"],
                    confidence=e.get("confidence", 1.0),
                )
                for e in data["timeline"]
            ]

        if data.get("attribution"):
            result.attribution = AttributionData(**data["attribution"])

        if data.get("scrubbing"):
            s = data["scrubbing"]
            result.scrubbing = ScrubbingAnalysis(
                is_scrubbed=s.get("is_scrubbed", False),
                confidence=s.get("confidence", 0.0),
                indicators=s.get("indicators", []),
                missing_expected_fields=s.get("missing_expected_fields", []),
                suspicious_patterns=s.get("suspicious_patterns", []),
            )

        return result


# =============================================================================
# FACTORY FUNCTION
# =============================================================================

def create_metadata_extractor(
    cache_path: Optional[str] = None,
    config: Optional[Any] = None,
) -> UniversalMetadataExtractor:
    """Create a configured metadata extractor.

    Args:
        cache_path: Path to SQLite cache database
        config: Configuration object (UniversalConfig or dict)

    Returns:
        Configured UniversalMetadataExtractor instance

    Example:
        extractor = create_metadata_extractor(
            cache_path="/tmp/metadata_cache.db",
            config={"enable_gps": True, "enable_reverse_geocode": False}
        )
    """
    kwargs = {"cache_path": cache_path}

    if config:
        if hasattr(config, "enable_metadata_extraction"):
            kwargs["enable_exif"] = getattr(config, "metadata_extract_exif", True)
            kwargs["enable_gps"] = getattr(config, "metadata_extract_gps", True)
            kwargs["enable_reverse_geocode"] = getattr(config, "metadata_reverse_geocode", False)
            kwargs["enable_audio"] = getattr(config, "metadata_extract_audio", True)
            kwargs["enable_video"] = getattr(config, "metadata_extract_video", False)
            kwargs["calculate_hashes"] = getattr(config, "metadata_calculate_hashes", True)
            kwargs["hash_algorithms"] = getattr(config, "metadata_hash_algorithms", ["md5", "sha256"])
            kwargs["max_file_size"] = getattr(config, "metadata_max_file_size", 1073741824)
            kwargs["batch_size"] = getattr(config, "metadata_batch_size", 100)
        elif isinstance(config, dict):
            kwargs.update(config)

    return UniversalMetadataExtractor(**kwargs)
