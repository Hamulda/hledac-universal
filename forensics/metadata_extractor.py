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
- DuckDB caching (ISSUE-001 Phase 2)
- Batch processing

M1 8GB Optimized:
- Streaming for files >100MB
- Memory limit: 500MB per extraction
- Lazy loading of heavy dependencies
- DuckDB caching for M1 performance

ISSUE-001 Phase 2: SQLite3 → DuckDB Migration
- MetadataCache now uses DuckDB via ForensicsMetadataStore
- Falls back to SQLite for backward compatibility if DuckDB unavailable
"""
from __future__ import annotations
import msgspec

from operator import attrgetter, itemgetter
import asyncio
import hashlib
import logging
import orjson
import math
import os
import re
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from xml.etree import ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec.json as _json

from hledac.universal.utils.asyncx import parallel
from hledac.universal._core.capabilities import CAPS, OLEVBA

logger = logging.getLogger(__name__)

# Lazy mlx.core singleton
_MLX_CORE: Any | None = None


def _get_mx() -> Any | None:
    """Lazy accessor for mlx.core — imports once and caches. Returns None if unavailable."""
    global _MLX_CORE
    if _MLX_CORE is None:
        try:
            import mlx.core as mx
            _MLX_CORE = mx
        except ImportError:
            _MLX_CORE = False
    return _MLX_CORE if _MLX_CORE is not False else None


def _copy_if_missing(target: Any, attr: str, source_value: Any) -> None:
    """Copy source_value to target.attr only if target.attr is falsy and source_value is truthy."""
    if source_value and not getattr(target, attr, None):
        setattr(target, attr, source_value)


if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_forensics_store import ForensicsMetadataStore

# ISSUE-001 Phase 2: SQLite3 → DuckDB Migration
# ForensicsMetadataStore replaces local SQLite cache with DuckDB for better M1 performance.
import functools
from _core import aclose


@functools.cache
def _get_duckdb_store_sync() -> "ForensicsMetadataStore | None":
    """Get or create singleton DuckDB forensics metadata store (sync cache for async context).

    Thread-safe via functools.cache internal lock (PEP 603 memoization).
    """
    try:
        from hledac.universal.knowledge.duckdb_forensics_store import ForensicsMetadataStore

        return ForensicsMetadataStore()
    except ImportError:
        return None


async def _get_duckdb_store() -> "ForensicsMetadataStore | None":
    """Get or create singleton DuckDB forensics metadata store."""
    store = _get_duckdb_store_sync()
    if store is not None:
        await store.initialize()
    return store

def _exif_to_float(val):
    """Handle EXIF rational (num, denom) tuples and plain numeric values."""
    if isinstance(val, tuple):
        return val[0] / val[1]
    return float(val)
_URL_PATTERN = re.compile(b'https?://[^\\s<>\'\\"]+', re.IGNORECASE)

def _extract_macro_urls(zf: zipfile.ZipFile, metadata: PPTXMetadata) -> None:
    """Extract C2 URLs from VBA macros in Office documents.

    Uses olevba if available, otherwise falls back to raw ZIP/bytes scanning.
    """
    olevba_available, olevba_mod = CAPS.try_import(OLEVBA)
    if olevba_available:
        for name in zf.namelist():
            if 'vbaProject.bin' in name:
                try:
                    vba_data = zf.read(name)
                    vba_parser = olevba_mod.VBALogicalLinesExtractor(vba_data)
                    for _, vba_line in vba_parser.extract_macros():
                        if vba_line:
                            urls = _URL_PATTERN.findall(vba_line.encode('utf-8', errors='ignore') if isinstance(vba_line, str) else vba_line)
                            for url in urls[:MAX_MACRO_URLS]:
                                if len(metadata.macro_urls) >= MAX_MACRO_URLS:
                                    break
                                metadata.macro_urls.append(url.decode('utf-8', errors='ignore'))
                    metadata.has_macros = True
                except Exception:  # noqa: BLE001
                    pass
                break
    else:
        for name in zf.namelist():
            if 'vbaProject.bin' in name or name.startswith('ppt/macros/'):
                metadata.has_macros = True
                try:
                    vba_data = zf.read(name)
                    urls = _URL_PATTERN.findall(vba_data)
                    for url in urls[:MAX_MACRO_URLS]:
                        if len(metadata.macro_urls) >= MAX_MACRO_URLS:
                            break
                        metadata.macro_urls.append(url.decode('utf-8', errors='ignore'))
                except Exception:  # noqa: BLE001
                    pass
                break
MAX_SPEAKER_NOTES: int = 50
MAX_HIDDEN_SLIDES: int = 100
MAX_EMBEDDED_FONTS: int = 100
MAX_INTERNAL_PATHS: int = 500
MAX_RECEIVED_HEADERS: int = 20
MAX_EMAIL_HEADERS: int = 200
MAX_MACRO_URLS: int = 50

class GPSCoordinates(msgspec.Struct, gc=False):
    """GPS coordinates with accuracy information."""
    latitude: float
    longitude: float
    altitude: float | None = None
    accuracy: float | None = None
    timestamp: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'latitude': self.latitude, 'longitude': self.longitude, 'altitude': self.altitude, 'accuracy': self.accuracy, 'timestamp': self.timestamp.isoformat() if self.timestamp else None}

class TimelineEvent(msgspec.Struct, gc=False):
    """Single timeline event from metadata."""
    timestamp: datetime
    event_type: str
    source: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'timestamp': self.timestamp.isoformat(), 'event_type': self.event_type, 'source': self.source, 'confidence': self.confidence}

class AttributionData(msgspec.Struct, gc=False):
    """Attribution data extracted from metadata."""
    software: str | None = None
    device: str | None = None
    device_serial: str | None = None
    author: str | None = None
    copyright: str | None = None
    organization: str | None = None
    version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'software': self.software, 'device': self.device, 'device_serial': self.device_serial, 'author': self.author, 'copyright': self.copyright, 'organization': self.organization, 'version': self.version}

class ScrubbingAnalysis(msgspec.Struct, gc=False):
    """Analysis of potential metadata scrubbing."""
    is_scrubbed: bool
    confidence: float
    indicators: list[str] = field(default_factory=list)
    missing_expected_fields: list[str] = field(default_factory=list)
    suspicious_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'is_scrubbed': self.is_scrubbed, 'confidence': self.confidence, 'indicators': self.indicators, 'missing_expected_fields': self.missing_expected_fields, 'suspicious_patterns': self.suspicious_patterns}

class ImageMetadata(msgspec.Struct, gc=False):
    """Image-specific metadata."""
    width: int | None = None
    height: int | None = None
    format: str | None = None
    mode: str | None = None
    exif: dict[str, Any] = field(default_factory=dict)
    gps: GPSCoordinates | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    lens: str | None = None
    focal_length: float | None = None
    exposure_time: str | None = None
    f_number: float | None = None
    iso: int | None = None
    flash: bool | None = None
    orientation: int | None = None
    caption: str | None = None
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'width': self.width, 'height': self.height, 'format': self.format, 'mode': self.mode, 'exif': self.exif, 'gps': self.gps.to_dict() if self.gps else None, 'camera_make': self.camera_make, 'camera_model': self.camera_model, 'lens': self.lens, 'focal_length': self.focal_length, 'exposure_time': self.exposure_time, 'f_number': self.f_number, 'iso': self.iso, 'flash': self.flash, 'orientation': self.orientation, 'caption': self.caption, 'tags': self.tags}

class PDFMetadata(msgspec.Struct, gc=False):
    """PDF document metadata."""
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    creator: str | None = None
    producer: str | None = None
    creation_date: datetime | None = None
    modification_date: datetime | None = None
    num_pages: int | None = None
    pdf_version: str | None = None
    is_encrypted: bool = False
    permissions: dict[str, bool] = field(default_factory=dict)
    embedded_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'title': self.title, 'author': self.author, 'subject': self.subject, 'creator': self.creator, 'producer': self.producer, 'creation_date': self.creation_date.isoformat() if self.creation_date else None, 'modification_date': self.modification_date.isoformat() if self.modification_date else None, 'num_pages': self.num_pages, 'pdf_version': self.pdf_version, 'is_encrypted': self.is_encrypted, 'permissions': self.permissions, 'embedded_files': self.embedded_files}

class DocxMetadata(msgspec.Struct, gc=False):
    """DOCX document metadata."""
    title: str | None = None
    author: str | None = None
    subject: str | None = None
    keywords: str | None = None
    category: str | None = None
    comments: str | None = None
    created: datetime | None = None
    modified: datetime | None = None
    last_modified_by: str | None = None
    revision: int | None = None
    company: str | None = None
    manager: str | None = None
    template: str | None = None
    total_editing_time: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'title': self.title, 'author': self.author, 'subject': self.subject, 'keywords': self.keywords, 'category': self.category, 'comments': self.comments, 'created': self.created.isoformat() if self.created else None, 'modified': self.modified.isoformat() if self.modified else None, 'last_modified_by': self.last_modified_by, 'revision': self.revision, 'company': self.company, 'manager': self.manager, 'template': self.template, 'total_editing_time': self.total_editing_time}

class AudioMetadata(msgspec.Struct, gc=False):
    """Audio file metadata."""
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    genre: str | None = None
    year: int | None = None
    track_number: int | None = None
    total_tracks: int | None = None
    disc_number: int | None = None
    total_discs: int | None = None
    composer: str | None = None
    publisher: str | None = None
    copyright: str | None = None
    comments: str | None = None
    lyrics: str | None = None
    duration: float | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    codec: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'title': self.title, 'artist': self.artist, 'album': self.album, 'album_artist': self.album_artist, 'genre': self.genre, 'year': self.year, 'track_number': self.track_number, 'total_tracks': self.total_tracks, 'disc_number': self.disc_number, 'total_discs': self.total_discs, 'composer': self.composer, 'publisher': self.publisher, 'copyright': self.copyright, 'comments': self.comments, 'lyrics': self.lyrics, 'duration': self.duration, 'bitrate': self.bitrate, 'sample_rate': self.sample_rate, 'channels': self.channels, 'codec': self.codec}

class VideoMetadata(msgspec.Struct, gc=False):
    """Video file metadata."""
    title: str | None = None
    duration: float | None = None
    bitrate: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    video_bitrate: int | None = None
    audio_codec: str | None = None
    audio_bitrate: int | None = None
    audio_channels: int | None = None
    audio_sample_rate: int | None = None
    container_format: str | None = None
    creation_time: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'title': self.title, 'duration': self.duration, 'bitrate': self.bitrate, 'width': self.width, 'height': self.height, 'fps': self.fps, 'video_codec': self.video_codec, 'video_bitrate': self.video_bitrate, 'audio_codec': self.audio_codec, 'audio_bitrate': self.audio_bitrate, 'audio_channels': self.audio_channels, 'audio_sample_rate': self.audio_sample_rate, 'container_format': self.container_format, 'creation_time': self.creation_time.isoformat() if self.creation_time else None}

class ArchiveMetadata(msgspec.Struct, gc=False):
    """Archive file metadata."""
    archive_type: str | None = None
    num_files: int | None = None
    uncompressed_size: int | None = None
    is_encrypted: bool = False
    compression_ratio: float | None = None
    comment: str | None = None
    files: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'archive_type': self.archive_type, 'num_files': self.num_files, 'uncompressed_size': self.uncompressed_size, 'is_encrypted': self.is_encrypted, 'compression_ratio': self.compression_ratio, 'comment': self.comment, 'files': self.files}

class PPTXMetadata(msgspec.Struct, gc=False):
    """Presentation metadata (PPTX/ODP) - FOCA-style forensics."""
    author: str | None = None
    last_modified_by: str | None = None
    title: str | None = None
    subject: str | None = None
    company: str | None = None
    template_path: str | None = None
    slide_count: int | None = None
    has_macros: bool | None = None
    macro_urls: list[str] = field(default_factory=list)
    speaker_notes: list[str] = field(default_factory=list)
    hidden_slides: list[dict[str, Any]] = field(default_factory=list)
    macro_analysis: dict[str, Any] = field(default_factory=dict)
    embedded_fonts: list[dict[str, str]] = field(default_factory=list)
    internal_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {'author': self.author, 'last_modified_by': self.last_modified_by, 'title': self.title, 'subject': self.subject, 'company': self.company, 'template_path': self.template_path, 'slide_count': self.slide_count, 'has_macros': self.has_macros, 'macro_urls': self.macro_urls, 'speaker_notes': self.speaker_notes, 'hidden_slides': self.hidden_slides, 'macro_analysis': self.macro_analysis, 'embedded_fonts': self.embedded_fonts, 'internal_paths': self.internal_paths}

class EmailMetadata(msgspec.Struct, gc=False):
    """Email header forensics - FOCA-style infrastructure analysis."""
    from_addr: str | None = None
    reply_to: str | None = None
    subject: str | None = None
    date: str | None = None
    message_id_domain: str | None = None
    originating_ip: str | None = None
    dkim_domain: str | None = None
    spf_result: str | None = None
    received_chain: list[dict[str, Any]] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    has_attachments: bool = False
    attachment_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {'from_addr': self.from_addr, 'reply_to': self.reply_to, 'subject': self.subject, 'date': self.date, 'message_id_domain': self.message_id_domain, 'originating_ip': self.originating_ip, 'dkim_domain': self.dkim_domain, 'spf_result': self.spf_result, 'received_chain': self.received_chain, 'headers': self.headers, 'has_attachments': self.has_attachments, 'attachment_count': self.attachment_count}

class CADMetadata(msgspec.Struct, gc=False):
    """CAD/technical drawing metadata (DXF, DWG, SVG) - FOCA-style."""
    author: str | None = None
    title: str | None = None
    description: str | None = None
    autocad_version: str | None = None
    insertion_base: dict[str, float] | None = None
    coordinate_extents: dict[str, Any] | None = None
    viewBox: str | None = None
    width: str | None = None
    height: str | None = None
    internal_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {'author': self.author, 'title': self.title, 'description': self.description, 'autocad_version': self.autocad_version, 'insertion_base': self.insertion_base, 'coordinate_extents': self.coordinate_extents, 'viewBox': self.viewBox, 'width': self.width, 'height': self.height, 'internal_paths': self.internal_paths}

class GenericMetadata(msgspec.Struct, gc=False):
    """Generic file metadata from filesystem."""
    file_name: str
    file_path: str
    file_size: int
    file_extension: str
    mime_type: str | None = None
    created: datetime | None = None
    modified: datetime | None = None
    accessed: datetime | None = None
    permissions: int | None = None
    owner: str | None = None
    group: str | None = None
    inode: int | None = None
    device_id: int | None = None
    hard_links: int | None = None
    blocks: int | None = None
    block_size: int | None = None
    md5_hash: str | None = None
    sha256_hash: str | None = None
    sha1_hash: str | None = None
    entropy: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'file_name': self.file_name, 'file_path': self.file_path, 'file_size': self.file_size, 'file_extension': self.file_extension, 'mime_type': self.mime_type, 'created': self.created.isoformat() if self.created else None, 'modified': self.modified.isoformat() if self.modified else None, 'accessed': self.accessed.isoformat() if self.accessed else None, 'permissions': self.permissions, 'owner': self.owner, 'group': self.group, 'inode': self.inode, 'device_id': self.device_id, 'hard_links': self.hard_links, 'blocks': self.blocks, 'block_size': self.block_size, 'md5_hash': self.md5_hash, 'sha256_hash': self.sha256_hash, 'sha1_hash': self.sha1_hash, 'entropy': self.entropy}

class SteganalysisMetadata(msgspec.Struct, gc=False):
    """Steganalysis results for images."""
    lsb_suspicious: bool = False
    lsb_score: float = 0.0
    histogram_suspicious: bool = False
    histogram_score: float = 0.0
    chi_square_score: float = 0.0
    stegdetect_result: str | None = None
    stegdetect_available: bool = False
    overall_suspicious: bool = False
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {'lsb_suspicious': self.lsb_suspicious, 'lsb_score': self.lsb_score, 'histogram_suspicious': self.histogram_suspicious, 'histogram_score': self.histogram_score, 'chi_square_score': self.chi_square_score, 'stegdetect_result': self.stegdetect_result, 'stegdetect_available': self.stegdetect_available, 'overall_suspicious': self.overall_suspicious, 'confidence': self.confidence}

class MetadataResult(msgspec.Struct, gc=False):
    """Complete metadata extraction result."""
    file_path: str
    success: bool
    error: str | None = None
    generic: GenericMetadata | None = None
    image: ImageMetadata | None = None
    pdf: PDFMetadata | None = None
    docx: DocxMetadata | None = None
    audio: AudioMetadata | None = None
    video: VideoMetadata | None = None
    archive: ArchiveMetadata | None = None
    pptx: PPTXMetadata | None = None
    email: EmailMetadata | None = None
    cad: CADMetadata | None = None
    steganalysis: SteganalysisMetadata | None = None
    timeline: list[TimelineEvent] = field(default_factory=list)
    attribution: AttributionData | None = None
    scrubbing: ScrubbingAnalysis | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    extraction_time: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'file_path': self.file_path, 'success': self.success, 'error': self.error, 'generic': self.generic.to_dict() if self.generic else None, 'image': self.image.to_dict() if self.image else None, 'pdf': self.pdf.to_dict() if self.pdf else None, 'docx': self.docx.to_dict() if self.docx else None, 'audio': self.audio.to_dict() if self.audio else None, 'video': self.video.to_dict() if self.video else None, 'archive': self.archive.to_dict() if self.archive else None, 'pptx': self.pptx.to_dict() if self.pptx else None, 'email': self.email.to_dict() if self.email else None, 'cad': self.cad.to_dict() if self.cad else None, 'steganalysis': self.steganalysis.to_dict() if self.steganalysis else None, 'timeline': [e.to_dict() for e in self.timeline], 'attribution': self.attribution.to_dict() if self.attribution else None, 'scrubbing': self.scrubbing.to_dict() if self.scrubbing else None, 'raw_metadata': self.raw_metadata, 'extraction_time': self.extraction_time}

    def to_json(self) -> str:
        """Convert to JSON string."""
        return _json.encode(self.to_dict()).decode('utf-8')

class MetadataCache:
    """
    DuckDB-backed metadata cache with SQLite fallback.

    ISSUE-001 Phase 2: SQLite3 → DuckDB Migration
    Uses DuckDB via ForensicsMetadataStore for M1 optimization.
    Falls back to SQLite if DuckDB unavailable.
    """
    MAX_ENTRIES = 10000
    __slots__ = tuple(('_duckdb_store', '_conn', '_lock', 'db_path'))

    def __init__(self, db_path: str | None=None):
        """Initialize cache.

        Args:
            db_path: Path to SQLite database (fallback only). If None, uses in-memory.
        """
        self.db_path = db_path or ':memory:'
        self._duckdb_store: "ForensicsMetadataStore | None" = None
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize DuckDB store and SQLite fallback (idempotent)."""
        async with self._lock:
            # Try DuckDB first
            self._duckdb_store = await _get_duckdb_store()

            # Initialize SQLite fallback
            if self._conn is None:
                from hledac.universal.runtime.worker_pool import io_bound
                self._conn = await io_bound(lambda: sqlite3.connect(self.db_path, check_same_thread=False))
                await io_bound(lambda: self._conn.execute("""
                    CREATE TABLE IF NOT EXISTS metadata_cache (
                        file_hash TEXT PRIMARY KEY,
                        mod_time REAL,
                        file_size INTEGER,
                        metadata TEXT,
                        extracted_at REAL
    )
                """))
                await io_bound(lambda: self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_extracted_at ON metadata_cache(extracted_at)"
                ))
                await io_bound(lambda: self._conn.commit())

    async def get(self, file_hash: str, mod_time: float, file_size: int) -> dict[str, Any] | None:
        """Get cached metadata if valid (DuckDB primary, SQLite fallback).

        Args:
            file_hash: Hash of file content
            mod_time: File modification time
            file_size: File size in bytes

        Returns:
            Cached metadata dict or None
        """
        # Try DuckDB first
        if self._duckdb_store is not None:
            result = await self._duckdb_store.get(file_hash, mod_time, file_size)
            if result is not None:
                logger.debug(f"[FORENSICS] DuckDB cache hit for {file_hash[:16]}...")
                return result

        # SQLite fallback
        if self._conn:
            from hledac.universal.runtime.worker_pool import io_bound
            cursor = await io_bound(lambda: self._conn.execute(
                'SELECT metadata FROM metadata_cache WHERE file_hash = ? AND mod_time = ? AND file_size = ?',
                (file_hash, mod_time, file_size)
            ))
            row = await io_bound(lambda: cursor.fetchone())
            if row:
                return _json.decode(row[0])
        return None

    async def set(self, file_hash: str, mod_time: float, file_size: int, metadata: dict[str, Any]) -> None:
        """Cache metadata (DuckDB primary, SQLite fallback).

        Args:
            file_hash: Hash of file content
            mod_time: File modification time
            file_size: File size in bytes
            metadata: Metadata dict to cache
        """
        # DuckDB primary
        if self._duckdb_store is not None:
            await self._duckdb_store.set(file_hash, mod_time, file_size, "generic", metadata)

        # SQLite fallback
        if self._conn:
            from hledac.universal.runtime.worker_pool import io_bound
            cursor = await io_bound(lambda: self._conn.execute('SELECT COUNT(*) FROM metadata_cache'))
            count = (await io_bound(lambda: cursor.fetchone()))[0]
            if count >= self.MAX_ENTRIES:
                await io_bound(lambda: self._conn.execute(
                    'DELETE FROM metadata_cache WHERE file_hash IN (SELECT file_hash FROM metadata_cache ORDER BY extracted_at ASC LIMIT ?)',
                    (self.MAX_ENTRIES // 10,)
                ))
            await io_bound(lambda: self._conn.execute(
                'INSERT OR REPLACE INTO metadata_cache (file_hash, mod_time, file_size, metadata, extracted_at) VALUES (?, ?, ?, ?, ?)',
                (file_hash, mod_time, file_size, orjson.dumps(metadata).decode('utf-8'), datetime.now(UTC).timestamp())
            ))
            await io_bound(lambda: self._conn.commit())

    async def clear(self) -> None:
        """Clear all cached entries."""
        async with self._lock:
            if self._conn:
                await asyncio.to_thread(lambda: self._conn.execute('DELETE FROM metadata_cache'))
                await asyncio.to_thread(lambda: self._conn.commit())

    async def close(self) -> None:
        """Close database connections."""
        async with self._lock:
            if self._duckdb_store:
                await self._duckdb_store.close()
                self._duckdb_store = None
            if self._conn:
                await asyncio.to_thread(lambda: self._conn.close())
                self._conn = None

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
    __slots__ = tuple(('_initialized', '_semaphore', 'batch_size', 'cache', 'calculate_hashes', 'enable_audio', 'enable_exif', 'enable_gps', 'enable_reverse_geocode', 'enable_video', 'hash_algorithms', 'max_file_size'))

    def __init__(self, cache_path: str | None=None, enable_exif: bool=True, enable_gps: bool=True, enable_reverse_geocode: bool=False, enable_audio: bool=True, enable_video: bool=False, calculate_hashes: bool=True, hash_algorithms: list[str] | None=None, max_file_size: int=1073741824, batch_size: int=100):
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
        self.hash_algorithms = hash_algorithms or ['md5', 'sha256']
        self.max_file_size = max_file_size
        self.batch_size = batch_size
        self._initialized = False
        from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
        self._semaphore = get_semaphore(ConcurrencyCategory.GRAPH_RAG)

    async def initialize(self) -> None:
        """Initialize extractor and cache."""
        await self.cache.initialize()
        self._initialized = True

    async def close(self) -> None:
        """Close extractor and cleanup resources."""
        await self.cache.close()
        self._initialized = False

    def _get_file_hash(self, file_path: str) -> tuple[str, float, int]:
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
        hasher = hashlib.md5()
        with open(file_path, 'rb') as f:
            if file_size <= 2 * 1024 * 1024:
                hasher.update(f.read())
            else:
                hasher.update(f.read(1024 * 1024))
                f.seek(-1024 * 1024, 2)
                hasher.update(f.read())
        return (hasher.hexdigest(), mod_time, file_size)

    def _calculate_full_hashes(self, file_path: str) -> dict[str, str]:
        """Calculate full file hashes.

        Args:
            file_path: Path to file

        Returns:
            Dict of algorithm -> hash
        """
        hashes = {}
        hashers = {}
        for algo in self.hash_algorithms:
            if algo == 'md5':
                hashers[algo] = hashlib.md5()
            elif algo == 'sha1':
                hashers[algo] = hashlib.sha256()
            elif algo == 'sha256':
                hashers[algo] = hashlib.sha256()
        with open(file_path, 'rb') as f:
            while (chunk := f.read(8192)):
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
        with open(file_path, 'rb') as f:
            while (chunk := f.read(65536)):
                for byte in chunk:
                    byte_counts[byte] += 1
                    total_bytes += 1
        if total_bytes == 0:
            return 0.0
        entropy = 0.0
        for count in byte_counts:
            if count > 0:
                p = count / total_bytes
                entropy -= p * math.log2(p)
        return entropy

    # -------------------------------------------------------------------------
    # Extension-based extraction dispatch (Strategy Pattern)
    # Maps extension groups to their extractor methods
    # -------------------------------------------------------------------------
    _IMAGE_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.gif', '.webp'})
    _AUDIO_EXTS = frozenset({'.mp3', '.flac', '.ogg', '.m4a', '.wav', '.wma'})
    _VIDEO_EXTS = frozenset({'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv'})
    _ARCHIVE_EXTS = frozenset({'.zip', '.tar', '.gz', '.bz2', '.7z', '.rar'})
    _PPTX_EXTS = frozenset({'.pptx', '.odp'})
    _EMAIL_EXTS = frozenset({'.eml', '.msg'})

    def _merge_piexif_metadata(self, image: ImageMetadata, piexif_metadata: Any) -> None:
        """Merge piexif metadata into image metadata (in-place)."""
        if not piexif_metadata:
            return
        _copy_if_missing(image, 'exif', piexif_metadata.exif)
        _copy_if_missing(image, 'gps', piexif_metadata.gps)
        _copy_if_missing(image, 'camera_make', piexif_metadata.camera_make)
        _copy_if_missing(image, 'camera_model', piexif_metadata.camera_model)
        _copy_if_missing(image, 'lens', piexif_metadata.lens)
        if piexif_metadata.focal_length is not None and image.focal_length is None:
            image.focal_length = piexif_metadata.focal_length
        if piexif_metadata.f_number is not None and image.f_number is None:
            image.f_number = piexif_metadata.f_number
        if piexif_metadata.iso is not None and image.iso is None:
            image.iso = piexif_metadata.iso

    def _merge_pdf_mupdf_metadata(self, pdf: PdfMetadata, mupdf_metadata: Any) -> None:
        """Merge MuPDF metadata into PDF metadata (in-place)."""
        if not mupdf_metadata:
            return
        if not pdf.pdf_version and mupdf_metadata.pdf_version:
            pdf.pdf_version = mupdf_metadata.pdf_version
        if not pdf.is_encrypted:
            pdf.is_encrypted = mupdf_metadata.is_encrypted
        if not pdf.permissions and mupdf_metadata.permissions:
            pdf.permissions = mupdf_metadata.permissions
        if not pdf.embedded_files and mupdf_metadata.embedded_files:
            pdf.embedded_files = mupdf_metadata.embedded_files

    async def _extract_image_metadata(self, file_path: str) -> ImageMetadata | None:
        """Extract and merge all image metadata."""
        image = await self._extract_image_exif(file_path)
        if not image:
            return None
        piexif_metadata = await self._extract_image_piexif(file_path)
        self._merge_piexif_metadata(image, piexif_metadata)
        stego = await self._extract_steganography(file_path)
        if stego:
            image.steganalysis = stego
        caption, tags = await self.extract_image_caption(file_path)
        if caption:
            image.caption = caption
            image.tags = tags
        return image

    async def _extract_pdf_with_mupdf(self, file_path: str) -> PdfMetadata | None:
        """Extract PDF with MuPDF merge."""
        pdf = await self._extract_pdf_metadata(file_path)
        if not pdf:
            return None
        mupdf_metadata = await self._extract_pdf_mupdf(file_path)
        self._merge_pdf_mupdf_metadata(pdf, mupdf_metadata)
        return pdf

    def _classify_extension(self, ext: str) -> str:
        """Classify extension into extraction group."""
        if ext in self._IMAGE_EXTS:
            return 'image'
        if ext == '.pdf':
            return 'pdf'
        if ext == '.docx':
            return 'docx'
        if ext in self._AUDIO_EXTS and self.enable_audio:
            return 'audio'
        if ext in self._VIDEO_EXTS and self.enable_video:
            return 'video'
        if ext in self._ARCHIVE_EXTS:
            return 'archive'
        if ext in self._PPTX_EXTS:
            return 'pptx'
        if ext in {'.svg', '.dxf'}:
            return 'cad'
        if ext in self._EMAIL_EXTS:
            return 'email'
        return 'unknown'

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
                return MetadataResult(file_path=file_path, success=False, error='File not found')
            try:
                file_hash, mod_time, file_size = self._get_file_hash(file_path)
                cached = await self.cache.get(file_hash, mod_time, file_size)
                if cached:
                    result = self._result_from_dict(cached)
                    result.extraction_time = time.time() - start_time
                    return result
                if file_size > self.max_file_size:
                    return MetadataResult(file_path=file_path, success=False, error=f'File too large: {file_size} bytes (max: {self.max_file_size})')
                generic = await self._extract_generic_metadata(file_path)
                ext = path.suffix.lower()
                result = MetadataResult(file_path=file_path, success=True, generic=generic)

                # Strategy dispatch by extension group
                ext_type = self._classify_extension(ext)
                match ext_type:
                    case 'image':
                        result.image = await self._extract_image_metadata(file_path)
                    case 'pdf':
                        result.pdf = await self._extract_pdf_with_mupdf(file_path)
                    case 'docx':
                        result.docx = await self._extract_docx_metadata(file_path)
                    case 'audio':
                        result.audio = await self._extract_audio_metadata(file_path)
                    case 'video':
                        result.video = await self._extract_video_metadata(file_path)
                    case 'archive':
                        result.archive = await self._extract_archive_metadata(file_path)
                    case 'pptx':
                        result.pptx = await self._extract_pptx_metadata(file_path)
                    case 'cad':
                        result.cad = await (self._extract_svg_metadata if ext == '.svg' else self._extract_dxf_metadata)(file_path)
                    case 'email':
                        result.email = await self._extract_email_metadata(file_path)

                result.timeline = self._build_timeline(result)
                result.attribution = self._build_attribution(result)
                result.scrubbing = self._detect_scrubbing(result)
                await self.cache.set(file_hash, mod_time, file_size, result.to_dict())
                result.extraction_time = time.time() - start_time
                return result
            except Exception as e:
                return MetadataResult(file_path=file_path, success=False, error=str(e), extraction_time=time.time() - start_time)

    async def extract_batch(self, file_paths: list[str]) -> list[MetadataResult]:
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
            # P4-5 FIX: policy="log" returns list[T], not ParallelResult.
            # Use results directly as they already contain only successes.
            batch_results = await parallel(tasks, policy="log", ctx='metadata_extractor:1131')
            for path, result in zip(batch, batch_results, strict=False):
                if isinstance(result, Exception):
                    results.append(MetadataResult(file_path=path, success=False, error=str(result)))
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
        hashes = {}
        if self.calculate_hashes:
            hashes = self._calculate_full_hashes(file_path)
        entropy = self._calculate_entropy(file_path)
        owner = None
        group = None
        try:
            import grp
            import pwd
            owner = pwd.getpwuid(stat.st_uid).pw_name
            group = grp.getgrgid(stat.st_gid).gr_name
        except (ImportError, KeyError):  # noqa: BLE001
            pass
        mime_type = None
        try:
            import mimetypes
            mime_type, _ = mimetypes.guess_type(file_path)
        except ImportError:  # noqa: BLE001
            pass
        return GenericMetadata(file_name=path.name, file_path=str(path.absolute()), file_size=stat.st_size, file_extension=path.suffix.lower(), mime_type=mime_type, created=datetime.fromtimestamp(stat.st_ctime), modified=datetime.fromtimestamp(stat.st_mtime), accessed=datetime.fromtimestamp(stat.st_atime), permissions=stat.st_mode, owner=owner, group=group, inode=stat.st_ino, device_id=stat.st_dev, hard_links=stat.st_nlink, blocks=getattr(stat, 'st_blocks', None), block_size=getattr(stat, 'st_blksize', None), md5_hash=hashes.get('md5'), sha256_hash=hashes.get('sha256'), sha1_hash=hashes.get('sha1'), entropy=entropy)

    def _parse_exif_tag(self, tag: str, value: Any, metadata: ImageMetadata) -> None:
        """Parse a single EXIF tag into metadata."""
        try:
            if tag == 'FocalLength' and (fl := metadata.focal_length) is None:
                metadata.focal_length = _exif_to_float(value)
            elif tag == 'ExposureTime':
                metadata.exposure_time = str(value)
            elif tag == 'FNumber' and metadata.f_number is None:
                metadata.f_number = _exif_to_float(value)
            elif tag == 'ISOSpeedRatings' and metadata.iso is None:
                metadata.iso = int(_exif_to_float(value))
            elif tag == 'Flash':
                metadata.flash = bool(int(value)) if not isinstance(value, bool) else value
            elif tag == 'Orientation' and metadata.orientation is None:
                metadata.orientation = int(value)
        except (ValueError, TypeError):  # noqa: BLE001
            pass

    def _extract_exif_to_metadata(self, exif_data: dict, metadata: ImageMetadata) -> None:
        """Extract all EXIF data to metadata."""
        from PIL.ExifTags import GPSTAGS
        metadata.camera_make = exif_data.get('Make')
        metadata.camera_model = exif_data.get('Model')
        metadata.lens = exif_data.get('LensModel')
        for tag in ('FocalLength', 'ExposureTime', 'FNumber', 'ISOSpeedRatings', 'Flash', 'Orientation'):
            if tag in exif_data:
                self._parse_exif_tag(tag, exif_data[tag], metadata)
        if self.enable_gps:
            if gps_info := exif_data.get(34853) or exif_data.get('GPSInfo'):
                gps_data = {GPSTAGS.get(key, key): gps_info[key] for key in gps_info}
                metadata.gps = self._parse_gps_data(gps_data)

    async def _extract_image_exif(self, file_path: str) -> ImageMetadata | None:
        """Extract EXIF metadata from image.

        Args:
            file_path: Path to image file

        Returns:
            ImageMetadata object or None
        """
        from hledac.universal._core.capabilities import CAPS, PIL
        pil_mod = CAPS.require(PIL)
        if pil_mod is None:
            return None
        Image = pil_mod.Image
        from PIL.ExifTags import TAGS
        try:
            with Image.open(file_path) as img:
                metadata = ImageMetadata(width=img.width, height=img.height, format=img.format, mode=img.mode)
                if not self.enable_exif:
                    return metadata
                if exif := img._getexif():
                    exif_data = {TAGS.get(tag_id, tag_id): value for tag_id, value in exif.items()}
                    self._extract_exif_to_metadata(exif_data, metadata)
                return metadata
        except Exception:
            return None

    def _parse_gps_data(self, gps_data: dict[str, Any]) -> GPSCoordinates | None:
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
                if ref in ['S', 'W']:
                    decimal = -decimal
                return decimal
            lat = None
            lon = None
            alt = None
            if 'GPSLatitude' in gps_data and 'GPSLatitudeRef' in gps_data:
                lat = dms_to_decimal(gps_data['GPSLatitude'], gps_data['GPSLatitudeRef'])
            if 'GPSLongitude' in gps_data and 'GPSLongitudeRef' in gps_data:
                lon = dms_to_decimal(gps_data['GPSLongitude'], gps_data['GPSLongitudeRef'])
            if 'GPSAltitude' in gps_data:
                alt_raw = gps_data['GPSAltitude']
                alt = _exif_to_float(alt_raw) if isinstance(alt_raw, tuple) else float(alt_raw)
            if lat is not None and lon is not None:
                return GPSCoordinates(latitude=lat, longitude=lon, altitude=alt)
            return None
        except Exception:
            return None

    async def _reverse_geocode(self, lat: float, lon: float) -> str | None:
        """Reverse geocode coordinates to address.

        Args:
            lat: Latitude
            lon: Longitude

        Returns:
            Address string or None
        """
        if not self.enable_reverse_geocode:
            return None
        return None

    async def _extract_pdf_metadata(self, file_path: str) -> PDFMetadata | None:
        """Extract metadata from PDF file.

        Args:
            file_path: Path to PDF file

        Returns:
            PDFMetadata object or None
        """
        # R-21: Try Rust lopdf first (~10× faster than pypdf)
        try:
            from rust_extensions import pdf as rust_pdf
            with open(file_path, 'rb') as f:
                data = f.read()
            rust_meta = rust_pdf.extract_metadata_from_bytes(data)
            # Convert Rust PdfMetadata to Python PDFMetadata
            metadata = PDFMetadata(
                title=rust_meta.title,
                author=rust_meta.author,
                subject=rust_meta.subject,
                creator=rust_meta.creator,
                producer=rust_meta.producer,
                creation_date=datetime.fromisoformat(rust_meta.creation_date) if rust_meta.creation_date else None,
                modification_date=datetime.fromisoformat(rust_meta.modification_date) if rust_meta.modification_date else None,
                num_pages=rust_meta.num_pages,
                pdf_version=rust_meta.pdf_version,
                is_encrypted=rust_meta.is_encrypted,
    )
            return metadata
        except Exception:  # noqa: BLE001
            pass

        # Fallback to pypdf
        from hledac.universal._core.capabilities import CAPS, PYPDF, PYPDF2
        pypdf_mod = CAPS.require(PYPDF)
        if pypdf_mod is None:
            pypdf_mod = CAPS.require(PYPDF2)
        if pypdf_mod is None:
            return None
        try:
            with open(file_path, 'rb') as f:
                reader = pypdf_mod.PdfReader(f)
                info = reader.metadata
                metadata = PDFMetadata(num_pages=len(reader.pages), is_encrypted=reader.is_encrypted)
                if info:
                    metadata.title = info.get('/Title')
                    metadata.author = info.get('/Author')
                    metadata.subject = info.get('/Subject')
                    metadata.creator = info.get('/Creator')
                    metadata.producer = info.get('/Producer')
                    if '/CreationDate' in info:
                        metadata.creation_date = self._parse_pdf_date(info['/CreationDate'])
                    if '/ModDate' in info:
                        metadata.modification_date = self._parse_pdf_date(info['/ModDate'])
                if hasattr(reader, 'pdf_header'):
                    header = reader.pdf_header
                    if header:
                        metadata.pdf_version = header.replace('%PDF-', '')
                return metadata
        except Exception:
            return None

    async def _extract_pdf_mupdf(self, file_path: str) -> PDFMetadata | None:
        """Extract metadata from PDF using PyMuPDF (fitz).

        PyMuPDF provides more detailed metadata than pypdf including
        metadata from document info streams and embedded files.

        Args:
            file_path: Path to PDF file

        Returns:
            PDFMetadata object or None
        """
        from hledac.universal._core.capabilities import CAPS, FITZ
        fitz_mod = CAPS.require(FITZ)
        if fitz_mod is None:
            return None
        try:
            file_size = Path(file_path).stat().st_size
            if file_size > 5 * 1024 * 1024:
                with open(file_path, 'rb') as f:
                    data = f.read()[:5 * 1024 * 1024]
                with fitz_mod.open(file_path, stream=data) as doc:
                    metadata = PDFMetadata(num_pages=len(doc))
                    return metadata
            with fitz_mod.open(file_path) as doc:
                metadata = PDFMetadata(num_pages=len(doc), pdf_version=doc.pdf_version() if hasattr(doc, 'pdf_version') else None)
                info = doc.metadata
                if info:
                    metadata.title = info.get('title')
                    metadata.author = info.get('author')
                    metadata.subject = info.get('subject')
                    metadata.creator = info.get('creator')
                    metadata.producer = info.get('producer')
                    creation_date = info.get('creationDate')
                    if creation_date:
                        metadata.creation_date = self._parse_pdf_date(creation_date)
                    mod_date = info.get('modDate')
                    if mod_date:
                        metadata.modification_date = self._parse_pdf_date(mod_date)
                metadata.is_encrypted = doc.is_encrypted
                if not metadata.is_encrypted:
                    try:
                        permissions = doc.permissions
                        metadata.permissions = {'read': bool(permissions & 1), 'write': bool(permissions & 2), 'print': bool(permissions & 4), 'copy': bool(permissions & 8)}
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    for xref in range(1, doc.xref_length()):
                        if doc.xref_get_key(xref, 'Type') == '/EmbeddedFiles':
                            metadata.embedded_files.append(f'xref:{xref}')
                except Exception:  # noqa: BLE001
                    pass
                return metadata
        except Exception:
            return None

    def _extract_zeroth_ifd(self, exif_dict, piexif_mod) -> dict:
        """Extract 0th IFD metadata."""
        zeroth = exif_dict.get('0th', {})
        return {
            'camera_make': zeroth.get(piexif_mod.ImageIFD.Make),
            'camera_model': zeroth.get(piexif_mod.ImageIFD.Model),
            'software': zeroth.get(piexif_mod.ImageIFD.Software),
            'orientation': zeroth.get(piexif_mod.ImageIFD.Orientation),
        }

    def _extract_exif_ifd(self, exif_ifd, piexif_mod):
        """Extract Exif IFD metadata."""
        result = {}
        def safe_int(val):
            try: return int(_exif_to_float(val))
            except: return None
        def safe_bool(val):
            try: return bool(int(val)) if not isinstance(val, bool) else val
            except: return None
        mappings = [
            ('focal_length', piexif_mod.ExifIFD.FocalLength, _exif_to_float),
            ('exposure_time', piexif_mod.ExifIFD.ExposureTime, str),
            ('f_number', piexif_mod.ExifIFD.FNumber, _exif_to_float),
            ('iso', piexif_mod.ExifIFD.ISOSpeedRatings, safe_int),
            ('flash', piexif_mod.ExifIFD.Flash, safe_bool),
            ('lens', piexif_mod.ExifIFD.LensModel, None),
        ]
        for key, ifd_tag, converter in mappings:
            if ifd_tag in exif_ifd:
                result[key] = converter(exif_ifd[ifd_tag]) if converter else exif_ifd[ifd_tag]
        return result

    def _populate_image_metadata_from_zeroth(self, metadata: ImageMetadata, zeroth_data: dict) -> None:
        """Populate image metadata from zeroth IFD data."""
        metadata.camera_make = zeroth_data.get('camera_make')
        metadata.camera_model = zeroth_data.get('camera_model')
        metadata.software = zeroth_data.get('software')
        metadata.orientation = zeroth_data.get('orientation')

    def _populate_image_metadata_from_exif(self, metadata: ImageMetadata, exif_data: dict) -> None:
        """Populate image metadata from Exif IFD data."""
        metadata.focal_length = exif_data.get('focal_length')
        metadata.exposure_time = exif_data.get('exposure_time')
        metadata.f_number = exif_data.get('f_number')
        metadata.iso = exif_data.get('iso')
        metadata.flash = exif_data.get('flash')
        metadata.lens = exif_data.get('lens')

    def _serialize_exif_dict(self, exif_dict: dict) -> dict:
        """Serialize EXIF dictionary to JSON-safe format."""
        return {
            k: {kk: vv for kk, vv in v.items() if isinstance(vv, (str, int, float, tuple, bytes))}
            for k, v in exif_dict.items() if v
        }

    async def _extract_image_piexif(self, file_path: str) -> ImageMetadata | None:
        """Extract EXIF metadata using piexif for enhanced accuracy."""
        from hledac.universal._core.capabilities import CAPS, PIEXIF
        piexif_mod = CAPS.require(PIEXIF)
        if piexif_mod is None:
            return None
        try:
            exif_dict = piexif.load(file_path)
            if not exif_dict or not any(exif_dict.get(ifd) for ifd in exif_dict):
                return None
            metadata = ImageMetadata()

            # Extract and populate zeroth IFD data
            zeroth_data = self._extract_zeroth_ifd(exif_dict, piexif_mod)
            self._populate_image_metadata_from_zeroth(metadata, zeroth_data)

            # Extract and populate Exif IFD data
            exif_data = self._extract_exif_ifd(exif_dict.get('Exif', {}), piexif_mod)
            self._populate_image_metadata_from_exif(metadata, exif_data)

            # GPS data
            if gps_ifd := exif_dict.get('GPS', {}):
                metadata.gps = self._parse_piexif_gps(gps_ifd)

            metadata.exif = self._serialize_exif_dict(exif_dict)
            return metadata
        except Exception:
            return None

    def _parse_piexif_gps(self, gps_ifd: dict) -> GPSCoordinates | None:
        """Parse GPS data from piexif GPS IFD.

        Args:
            gps_ifd: GPS IFD dict from piexif

        Returns:
            GPSCoordinates object or None
        """
        try:

            def dms_to_decimal(dms, ref):
                degrees = _exif_to_float(dms[0])
                minutes = _exif_to_float(dms[1]) / 60.0
                seconds = _exif_to_float(dms[2]) / 3600.0
                decimal = degrees + minutes + seconds
                if ref in ['S', 'W']:
                    decimal = -decimal
                return decimal
            lat = None
            lon = None
            alt = None
            if piexif.GPSIFD.GPSLatitude in gps_ifd and piexif.GPSIFD.GPSLatitudeRef in gps_ifd:
                lat = dms_to_decimal(gps_ifd[piexif.GPSIFD.GPSLatitude], gps_ifd[piexif.GPSIFD.GPSLatitudeRef])
            if piexif.GPSIFD.GPSLongitude in gps_ifd and piexif.GPSIFD.GPSLongitudeRef in gps_ifd:
                lon = dms_to_decimal(gps_ifd[piexif.GPSIFD.GPSLongitude], gps_ifd[piexif.GPSIFD.GPSLongitudeRef])
            if piexif.GPSIFD.GPSAltitude in gps_ifd:
                alt_raw = gps_ifd[piexif.GPSIFD.GPSAltitude]
                alt = _exif_to_float(alt_raw) if isinstance(alt_raw, tuple) else float(alt_raw)
            if lat is not None and lon is not None:
                return GPSCoordinates(latitude=lat, longitude=lon, altitude=alt)
            return None
        except Exception:
            return None

    async def _extract_steganography(self, file_path: str) -> SteganalysisMetadata | None:
        """Extract steganography analysis for images.

        Performs chi-square, histogram, and LSB analysis to detect
        hidden data in images. Uses stegdetect if available.

        Args:
            file_path: Path to image file

        Returns:
            SteganalysisMetadata object or None
        """
        try:
            from .steganography_detector import STEGDETECT_AVAILABLE, analyze_image_steganography
        except ImportError:
            return None
        try:
            result = analyze_image_steganography(file_path)
            if result is None:
                return None
            metadata = SteganalysisMetadata(lsb_suspicious=result.lsb_suspicious, lsb_score=result.lsb_score, histogram_suspicious=result.histogram_suspicious, histogram_score=result.histogram_score, chi_square_score=result.chi_square_score, stegdetect_result=result.stegdetect_result, stegdetect_available=result.stegdetect_available, overall_suspicious=result.overall_suspicious, confidence=result.confidence)
            return metadata
        except Exception:
            return None

    async def extract_image_caption(self, file_path: str) -> tuple[str | None, list[str]]:
        """Extract image caption and tags using MLX-VLM.

        Uses mlx-vlm or qwen2.5vl-3b-mlx for image captioning.
        Lazy import to avoid loading MLX models unless needed.

        Args:
            file_path: Path to image file

        Returns:
            Tuple of (caption, tags)
        """
        from hledac.universal._core.capabilities import CAPS, MLX_VLM
        mlx_vlm_mod = CAPS.require(MLX_VLM)
        MLX_VLM_AVAILABLE = mlx_vlm_mod is not None
        if not MLX_VLM_AVAILABLE:
            return (None, [])
        file_size = Path(file_path).stat().st_size
        if file_size > 50 * 1024 * 1024:
            return (None, [])
        import os as _os
        model_name = _os.environ.get('MLX_VLM_MODEL', 'qwen2.5vl-3b-mlx')
        try:
            model = mlx_vlm_mod.load(model_name)
            processor = model.processor
        except Exception:
            for alt_model in ['mlx-vlm/qwen2.5vl-3b-mlx', 'qwen2.5-vl-3b-mlx']:
                try:
                    model = mlx_vlm_mod.load(alt_model)
                    processor = model.processor
                    break
                except Exception:
                    continue
            else:
                return (None, [])
            from PIL import Image
            with Image.open(file_path) as img:
                max_size = 1024
                if max(img.size) > max_size:
                    ratio = max_size / max(img.size)
                    new_size = tuple((int(dim * ratio) for dim in img.size))
                    img = img.resize(new_size, Image.LANCZOS)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                import io
                img_bytes = io.BytesIO()
                img.save(img_bytes, format='JPEG', quality=85)
                img_bytes.seek(0)
            prompt = 'Describe this image in detail. What are the main objects, scene, text, and activities visible?'
            caption = mlx_vlm_mod.generate(model, processor, img_bytes, prompt=prompt)
            tag_prompt = 'List 5-10 comma-separated keywords that describe this image:'
            tags_text = mlx_vlm_mod.generate(model, processor, img_bytes, prompt=tag_prompt)
            tags = [t.strip() for t in tags_text.split(',') if t.strip()]
            try:
                mx = _get_mx()
                if mx is None:
                    return (caption, tags[:10])
                mx.eval([])
                import gc
                gc.collect()
                if hasattr(mx, 'clear_cache'):
                    mx.clear_cache()
                elif hasattr(mx.metal, 'clear_cache'):
                    mx.metal.clear_cache()
            except Exception:  # noqa: BLE001
                pass
            return (caption, tags[:10])
        except Exception:
            return (None, [])

    def _parse_pdf_date(self, date_str: str) -> datetime | None:
        """Parse PDF date string.

        Args:
            date_str: PDF date string (D:YYYYMMDDHHmmSS)

        Returns:
            datetime object or None
        """
        try:
            if date_str.startswith('D:'):
                date_str = date_str[2:]
            if '+' in date_str:
                date_str = date_str.split('+')[0]
            if '-' in date_str and date_str.index('-') > 4:
                date_str = date_str.split('-')[0]
            if 'Z' in date_str:
                date_str = date_str.replace('Z', '')
            if len(date_str) >= 14:
                return datetime(int(date_str[0:4]), int(date_str[4:6]), int(date_str[6:8]), int(date_str[8:10]), int(date_str[10:12]), int(date_str[12:14]))
            elif len(date_str) >= 8:
                return datetime(int(date_str[0:4]), int(date_str[4:6]), int(date_str[6:8]))
            return None
        except Exception:
            return None

    async def _extract_docx_metadata(self, file_path: str) -> DocxMetadata | None:
        """Extract metadata from DOCX file.

        Args:
            file_path: Path to DOCX file

        Returns:
            DocxMetadata object or None
        """
        from hledac.universal._core.capabilities import CAPS, DOCX
        docx_mod = CAPS.require(DOCX)
        if docx_mod is None:
            return None
        try:
            doc = docx_mod.Document(file_path)
            props = doc.core_properties
            return DocxMetadata(title=props.title, author=props.author, subject=props.subject, keywords=props.keywords, category=props.category, comments=props.comments, created=props.created, modified=props.modified, last_modified_by=props.last_modified_by, revision=props.revision, company=props.company, manager=props.manager, template=props.template, total_editing_time=props.total_editing_time)
        except Exception:
            return None

    async def _extract_audio_metadata(self, file_path: str) -> AudioMetadata | None:
        """Extract metadata from audio file.

        Args:
            file_path: Path to audio file

        Returns:
            AudioMetadata object or None
        """
        from hledac.universal._core.capabilities import CAPS, MUTAGEN
        mutagen_mod = CAPS.require(MUTAGEN)
        if mutagen_mod is None:
            return None
        MutagenFile = mutagen_mod.File
        MP3 = mutagen_mod.mp3.MP3
        try:
            audio = MutagenFile(file_path)
            if not audio:
                return None
            metadata = AudioMetadata()
            if hasattr(audio.info, 'length'):
                metadata.duration = audio.info.length
            if hasattr(audio.info, 'bitrate'):
                metadata.bitrate = audio.info.bitrate // 1000
            if hasattr(audio.info, 'sample_rate'):
                metadata.sample_rate = audio.info.sample_rate
            if hasattr(audio.info, 'channels'):
                metadata.channels = audio.info.channels
            metadata.codec = type(audio).__name__.lower()
            if audio.tags:
                tag_mapping = {'TIT2': 'title', 'TPE1': 'artist', 'TALB': 'album', 'TPE2': 'album_artist', 'TCON': 'genre', 'TYER': 'year', 'TDRC': 'year', 'TRCK': 'track_number', 'TPOS': 'disc_number', 'TCOM': 'composer', 'TPUB': 'publisher', 'TCOP': 'copyright', 'COMM': 'comments', 'USLT': 'lyrics'}
                for tag, field in tag_mapping.items():
                    if tag in audio.tags:
                        value = str(audio.tags[tag])
                        if field == 'year':
                            try:
                                setattr(metadata, field, int(str(value)[:4]))
                            except ValueError:  # noqa: BLE001
                                pass
                        elif field in ['track_number', 'disc_number']:
                            try:
                                num = str(value).split('/')[0]
                                setattr(metadata, field, int(num))
                            except ValueError:  # noqa: BLE001
                                pass
                        else:
                            setattr(metadata, field, value)
            return metadata
        except Exception:
            return None

    async def _extract_video_metadata(self, file_path: str) -> VideoMetadata | None:
        """Extract metadata from video file.

        Args:
            file_path: Path to video file

        Returns:
            VideoMetadata object or None
        """
        try:
            import os
            os.stat(file_path)
            return VideoMetadata(container_format=Path(file_path).suffix.lower().lstrip('.'))
        except Exception:
            return None

    async def _extract_archive_metadata(self, file_path: str) -> ArchiveMetadata | None:
        """Extract metadata from archive file.

        Args:
            file_path: Path to archive file

        Returns:
            ArchiveMetadata object or None
        """
        ext = Path(file_path).suffix.lower()
        if ext == '.zip':
            return await self._extract_zip_metadata(file_path)
        elif ext in {'.tar', '.gz', '.bz2'}:
            return await self._extract_tar_metadata(file_path)
        return ArchiveMetadata(archive_type=ext.lstrip('.'))

    async def _extract_zip_metadata(self, file_path: str) -> ArchiveMetadata:
        """Extract ZIP archive metadata.

        Args:
            file_path: Path to ZIP file

        Returns:
            ArchiveMetadata object
        """
        metadata = ArchiveMetadata(archive_type='zip')
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                metadata.num_files = len(zf.namelist())
                metadata.comment = zf.comment.decode('utf-8', errors='ignore') if zf.comment else None
                total_uncompressed = 0
                total_compressed = 0
                files = []
                for info in zf.infolist():
                    total_uncompressed += info.file_size
                    total_compressed += info.compress_size
                    files.append({'name': info.filename, 'size': info.file_size, 'compressed_size': info.compress_size, 'is_directory': info.is_dir(), 'modified': datetime(*info.date_time), 'crc': info.CRC})
                metadata.uncompressed_size = total_uncompressed
                metadata.files = files
                if total_uncompressed > 0:
                    metadata.compression_ratio = total_compressed / total_uncompressed
                for info in zf.infolist():
                    if info.flag_bits & 1:
                        metadata.is_encrypted = True
                        break
        except Exception:  # noqa: BLE001
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
        metadata = ArchiveMetadata(archive_type='tar')
        try:
            with tarfile.open(file_path, 'r:*') as tf:
                members = tf.getmembers()
                metadata.num_files = len(members)
                total_size = 0
                files = []
                for member in members:
                    total_size += member.size
                    files.append({'name': member.name, 'size': member.size, 'is_directory': member.isdir(), 'modified': datetime.fromtimestamp(member.mtime), 'mode': member.mode, 'uid': member.uid, 'gid': member.gid})
                metadata.uncompressed_size = total_size
                metadata.files = files
        except Exception:  # noqa: BLE001
            pass
        return metadata

    async def _extract_pptx_metadata(self, file_path: str) -> PPTXMetadata | None:
        """Extract metadata from PPTX/ODP presentation files.

        Args:
            file_path: Path to presentation file

        Returns:
            PPTXMetadata object or None
        """
        import zipfile
        from xml.etree import ElementTree as ET
        metadata = PPTXMetadata()
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                if 'docProps/core.xml' in zf.namelist():
                    self._parse_pptx_core_xml(zf.read('docProps/core.xml'), metadata)
                if 'docProps/app.xml' in zf.namelist():
                    self._parse_pptx_app_xml(zf.read('docProps/app.xml'), metadata)
                if 'ppt/presentation.xml' in zf.namelist():
                    self._parse_pptx_presentation(zf.read('ppt/presentation.xml'), metadata)
                    self._extract_pptx_hidden_slides(zf, metadata)
                self._extract_pptx_speaker_notes(zf, metadata)
                _extract_macro_urls(zf, metadata)
                self._extract_pptx_fonts(zf, metadata)
                metadata.internal_paths = [n for n in zf.namelist() if n.startswith('ppt/')][:MAX_INTERNAL_PATHS]
        except Exception:  # noqa: BLE001
            pass
        return metadata

    def _parse_pptx_core_xml(self, core_xml: bytes, metadata: PPTXMetadata) -> None:
        """Parse docProps/core.xml for title, author, subject."""
        try:
            root = ET.fromstring(core_xml)
            ns = {'dc': 'http://purl.org/dc/elements/1.1/', 'cp': 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties'}
            metadata.title = root.find('.//dc:title', ns).text if root.find('.//dc:title', ns) is not None else None
            metadata.author = root.find('.//dc:creator', ns).text if root.find('.//dc:creator', ns) is not None else None
            subject_el = root.find('.//dc:subject', ns)
            if subject_el is not None:
                metadata.subject = subject_el.text
        except Exception:  # noqa: BLE001
            pass

    def _parse_pptx_app_xml(self, app_xml: bytes, metadata: PPTXMetadata) -> None:
        """Parse docProps/app.xml for company, template, last_modified_by."""
        try:
            root = ET.fromstring(app_xml)
            ns = {'xp': 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'}
            company_el = root.find('.//xp:Company', ns)
            if company_el is not None:
                metadata.company = company_el.text
            template_el = root.find('.//xp:Template', ns)
            if template_el is not None:
                metadata.template_path = template_el.text
            last_mod_el = root.find('.//xp:LastModifiedBy', ns)
            if last_mod_el is not None:
                metadata.last_modified_by = last_mod_el.text
        except Exception:  # noqa: BLE001
            pass

    def _parse_pptx_presentation(self, pres_xml: bytes, metadata: PPTXMetadata) -> None:
        """Parse ppt/presentation.xml for slide count."""
        try:
            root = ET.fromstring(pres_xml)
            slides = root.findall('.//{http://schemas.openxmlformats.org/presentationml/2006/main}sldId')
            metadata.slide_count = len(slides) if slides else 0
        except Exception:  # noqa: BLE001
            pass

    def _extract_pptx_speaker_notes(self, zf: zipfile.ZipFile, metadata: PPTXMetadata) -> None:
        """Extract speaker notes from notesSlides."""
        try:
            for name in zf.namelist():
                if name.startswith('ppt/notesSlides/') and name.endswith('.xml'):
                    if len(metadata.speaker_notes) >= MAX_SPEAKER_NOTES:
                        break
                    try:
                        notes_xml = zf.read(name)
                        root = ET.fromstring(notes_xml)
                        texts = []
                        for elem in root.iter():
                            if elem.text and elem.text.strip():
                                texts.append(elem.text.strip())
                        if texts:
                            metadata.speaker_notes.append(' '.join(texts[:5]))
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass

    def _extract_pptx_hidden_slides(self, zf: zipfile.ZipFile, metadata: PPTXMetadata) -> None:
        """Extract hidden slides from ppt/presentation.xml."""
        try:
            if 'ppt/presentation.xml' in zf.namelist():
                pres_xml = zf.read('ppt/presentation.xml')
                root = ET.fromstring(pres_xml)
                ns = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main', 'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
                for sld in root.findall('.//p:sld', ns):
                    show = sld.get('show')
                    if show == '0':
                        idx = sld.get('id')
                        metadata.hidden_slides.append({'id': idx, 'hidden': True})
        except Exception:  # noqa: BLE001
            pass

    def _extract_pptx_fonts(self, zf: zipfile.ZipFile, metadata: PPTXMetadata) -> None:
        """Extract embedded fonts from ppt/font/."""
        try:
            for name in zf.namelist():
                if len(metadata.embedded_fonts) >= MAX_EMBEDDED_FONTS:
                    break
                if name.startswith('ppt/font/') and name.endswith('.xml'):
                    try:
                        font_xml = zf.read(name)
                        root = ET.fromstring(font_xml)
                        font_name = root.get('name')
                        if font_name:
                            metadata.embedded_fonts.append({'name': font_name, 'file': name})
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            pass

    async def _extract_svg_metadata(self, file_path: str) -> CADMetadata | None:
        """Extract metadata from SVG vector graphics.

        Args:
            file_path: Path to SVG file

        Returns:
            CADMetadata object or None
        """
        from xml.etree import ElementTree as ET
        metadata = CADMetadata()
        try:
            with open(file_path, encoding='utf-8', errors='ignore') as f:
                content = f.read()
            root = ET.fromstring(content)
            ns = {'svg': 'http://www.w3.org/2000/svg'}
            metadata.width = root.get('width')
            metadata.height = root.get('height')
            metadata.viewBox = root.get('viewBox')
            title_el = root.find('.//svg:title', ns)
            if title_el is not None and title_el.text:
                metadata.title = title_el.text
            desc_el = root.find('.//svg:desc', ns)
            if desc_el is not None and desc_el.text:
                metadata.description = desc_el.text
            for elem in root.iter():
                if elem.tag.endswith('}meta') or elem.tag == 'metadata':
                    for child in elem:
                        if 'creator' in child.tag.lower():
                            metadata.author = child.text
                        elif 'title' in child.tag.lower() and (not metadata.title):
                            metadata.title = child.text
        except Exception:  # noqa: BLE001
            pass
        return metadata

    async def _extract_dxf_metadata(self, file_path: str) -> CADMetadata | None:
        """Extract metadata from DXF CAD files.

        Args:
            file_path: Path to DXF file

        Returns:
            CADMetadata object or None
        """
        metadata = CADMetadata()
        try:
            with open(file_path, encoding='utf-8', errors='ignore') as f:
                content = f.read()
            in_header = False
            for line in content.split('\n'):
                line = line.strip()
                if line == 'SECTION' and 'HEADER' in content[content.find(line) + len(line):content.find(line) + len(line) + 20]:
                    in_header = True
                elif line == 'ENDSEC':
                    in_header = False
                elif in_header:
                    if line == '$ACADVER':
                        continue
                    if line.startswith('$'):
                        var_name = line[1:]
                        idx = content.find(line)
                        next_pos = content.find('\n', idx + len(line))
                        if next_pos > 0:
                            value = content[idx + len(line) + 1:next_pos].strip()
                            if var_name == 'TITLE':
                                metadata.title = value
                            elif var_name == 'AUTHOR':
                                metadata.author = value
                            elif var_name == 'DESCRIPTION':
                                metadata.description = value
        except Exception:  # noqa: BLE001
            pass
        return metadata

    def _parse_eml_headers(self, msg, metadata: EmailMetadata) -> None:
        """Parse EML headers into metadata."""
        import re
        from email.parser import Parser
        metadata.from_addr = msg.get('From')
        metadata.reply_to = msg.get('Reply-To')
        metadata.subject = msg.get('Subject')
        metadata.date = msg.get('Date')
        if msg_id := msg.get('Message-ID'):
            if match := re.search('@([^>]+)', msg_id):
                metadata.message_id_domain = match.group(1)
        for header in msg.keys():
            header_lower = header.lower()
            if header_lower == 'x-originating-ip':
                metadata.originating_ip = msg.get(header)
            elif header_lower == 'dkim-signature':
                if match := re.search('d=([^;\\s]+)', msg.get(header) or ''):
                    metadata.dkim_domain = match.group(1)
            elif header_lower == 'authentication-results':
                auth = msg.get(header, '').lower()
                if 'spf=pass' in auth:
                    metadata.spf_result = 'pass'
                elif 'spf=fail' in auth:
                    metadata.spf_result = 'fail'

    def _parse_received_chain(self, msg) -> list[dict]:
        """Parse received headers chain."""
        received_headers = []
        for i in range(MAX_RECEIVED_HEADERS):
            received = msg.get(f'Received-{i}' if i > 0 else 'Received')
            if received:
                received_headers.append({'header': received, 'index': i})
            else:
                break
        return received_headers

    def _count_attachments(self, msg) -> tuple[bool, int]:
        """Count attachments from multipart message."""
        count = 0
        has_attachments = False
        for part in msg.walk():
            if 'attachment' in part.get('Content-Disposition', '').lower():
                has_attachments = True
                count += 1
        return has_attachments, count

    async def _extract_email_metadata(self, file_path: str) -> EmailMetadata | None:
        """Extract metadata from email files (EML/MSG).

        Args:
            file_path: Path to email file

        Returns:
            EmailMetadata object or None
        """
        from email.parser import Parser
        ext = Path(file_path).suffix.lower()
        metadata = EmailMetadata()
        try:
            if ext == '.eml':
                with open(file_path, encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                msg = Parser().parsestr(content)
                self._parse_eml_headers(msg, metadata)
                metadata.received_chain = self._parse_received_chain(msg)
                metadata.headers = dict(list(dict(msg.items()).items())[:MAX_EMAIL_HEADERS])
                if msg.is_multipart():
                    metadata.has_attachments, metadata.attachment_count = self._count_attachments(msg)
            elif ext == '.msg':
                self._extract_msg_metadata(file_path, metadata)
        except Exception:  # noqa: BLE001
            pass
        return metadata

    def _extract_msg_metadata(self, file_path: str, metadata: EmailMetadata) -> None:
        """Extract metadata from MSG (Outlook) files."""
        try:
            import olefile
            if olefile.isOleFile(file_path):
                ole = olefile.OleFileIO(file_path)
                if ole.exists('__substg1.0_0042001F'):
                    metadata.subject = ole.openstream('__substg1.0_0042001F').read().decode('utf-16-le', errors='ignore').rstrip('\x00')
                if ole.exists('__substg1.0_0C1F001F'):
                    metadata.from_addr = ole.openstream('__substg1.0_0C1F001F').read().decode('utf-16-le', errors='ignore').rstrip('\x00')
                ole.close()
        except ImportError:  # noqa: BLE001
            pass

    def _add_timeline_event(self, events: list, timestamp, event_type: str, source: str) -> None:
        """Add a timeline event if timestamp is valid."""
        if timestamp:
            events.append(TimelineEvent(timestamp=timestamp, event_type=event_type, source=source))

    def _parse_exif_datetime(self, exif: dict, key: str, event_type: str):
        """Parse EXIF datetime field and return TimelineEvent or None."""
        if key in exif:
            try:
                dt = datetime.strptime(exif[key], '%Y:%m:%d %H:%M:%S')
                return TimelineEvent(timestamp=dt, event_type=event_type, source='exif')
            except ValueError:
                return None
        return None

    def _extract_exif_timeline(self, exif: dict) -> list[TimelineEvent]:
        """Extract timeline events from EXIF data."""
        events = []
        for key, event_type in [('DateTime', 'captured'), ('DateTimeOriginal', 'captured_original'), ('DateTimeDigitized', 'digitized')]:
            if event := self._parse_exif_datetime(exif, key, event_type):
                events.append(event)
        return events

    def _build_timeline(self, result: MetadataResult) -> list[TimelineEvent]:
        """Build timeline from all extracted metadata."""
        events: list[TimelineEvent] = []

        # Generic filesystem events
        if result.generic:
            self._add_timeline_event(events, result.generic.created, 'created', 'filesystem')
            self._add_timeline_event(events, result.generic.modified, 'modified', 'filesystem')
            self._add_timeline_event(events, result.generic.accessed, 'accessed', 'filesystem')

        # Image EXIF events
        if result.image and result.image.exif:
            events.extend(self._extract_exif_timeline(result.image.exif))

        # PDF events
        if result.pdf:
            self._add_timeline_event(events, result.pdf.creation_date, 'created', 'pdf_metadata')
            self._add_timeline_event(events, result.pdf.modification_date, 'modified', 'pdf_metadata')

        # DOCX events
        if result.docx:
            self._add_timeline_event(events, result.docx.created, 'created', 'docx_core_properties')
            self._add_timeline_event(events, result.docx.modified, 'modified', 'docx_core_properties')

        events.sort(key=attrgetter("timestamp") or datetime.min)
        return events

    def _build_attribution(self, result: MetadataResult) -> AttributionData:
        """Build attribution data from all extracted metadata.

        Args:
            result: MetadataResult with extracted data

        Returns:
            AttributionData object
        """
        attr = AttributionData()
        if result.image:
            if result.image.camera_make or result.image.camera_model:
                attr.device = ' '.join(filter(None, [result.image.camera_make, result.image.camera_model]))
            if result.image.exif.get('Software'):
                attr.software = result.image.exif.get('Software')
        if result.pdf:
            attr.author = result.pdf.author
            attr.software = result.pdf.creator or result.pdf.producer
        if result.docx:
            attr.author = result.docx.author
            attr.software = result.docx.template
            attr.organization = result.docx.company
        if result.audio:
            attr.author = result.audio.artist or result.audio.composer
            attr.software = result.audio.publisher
            attr.copyright = result.audio.copyright
        if result.video:
            attr.software = result.video.container_format
        return attr

    def _check_image_scrubbing(self, result, indicators, missing):
        """Check for image metadata scrubbing."""
        if not result.image.exif:
            indicators.append('No EXIF data found in image')
            missing.append('EXIF')
        else:
            for field in ['Make', 'Model', 'DateTime']:
                if field not in result.image.exif:
                    missing.append(f'EXIF:{field}')

    def _compute_confidence(self, missing, indicators, suspicious):
        """Compute scrubbing confidence score."""
        return min((len(missing) * 0.1 + len(indicators) * 0.15 + len(suspicious) * 0.1), 1.0)

    def _detect_scrubbing(self, result: MetadataResult) -> ScrubbingAnalysis:
        """Detect potential metadata scrubbing."""
        indicators, missing, suspicious = [], [], []
        if result.image:
            self._check_image_scrubbing(result, indicators, missing)
        if result.pdf and not any([result.pdf.author, result.pdf.creator, result.pdf.producer]):
            indicators.append('No attribution metadata in PDF')
            missing.extend(['Author', 'Creator', 'Producer'])
        if result.docx:
            if not result.docx.author:
                indicators.append('No author in DOCX')
                missing.append('Author')
            if not result.docx.created:
                indicators.append('No creation date in DOCX')
                missing.append('Created')
        if result.generic and result.generic.created and result.generic.modified:
            if result.generic.created == result.generic.modified:
                suspicious.append('Creation and modification timestamps are identical')
        confidence = self._compute_confidence(missing, indicators, suspicious)
        return ScrubbingAnalysis(is_scrubbed=confidence > 0.5, confidence=confidence,
            indicators=indicators, missing_expected_fields=missing, suspicious_patterns=suspicious)

    def _result_from_dict(self, data: dict[str, Any]) -> MetadataResult:
        """Reconstruct MetadataResult from dictionary.

        Args:
            data: Dictionary from to_dict()

        Returns:
            MetadataResult object
        """
        result = MetadataResult(file_path=data.get('file_path', ''), success=data.get('success', False), error=data.get('error'), extraction_time=data.get('extraction_time', 0.0), raw_metadata=data.get('raw_metadata', {}))
        if data.get('generic'):
            g = data['generic']
            result.generic = GenericMetadata(file_name=g.get('file_name', ''), file_path=g.get('file_path', ''), file_size=g.get('file_size', 0), file_extension=g.get('file_extension', ''), mime_type=g.get('mime_type'), created=datetime.fromisoformat(g['created']) if g.get('created') else None, modified=datetime.fromisoformat(g['modified']) if g.get('modified') else None, accessed=datetime.fromisoformat(g['accessed']) if g.get('accessed') else None, permissions=g.get('permissions'), owner=g.get('owner'), group=g.get('group'), inode=g.get('inode'), device_id=g.get('device_id'), hard_links=g.get('hard_links'), blocks=g.get('blocks'), block_size=g.get('block_size'), md5_hash=g.get('md5_hash'), sha256_hash=g.get('sha256_hash'), sha1_hash=g.get('sha1_hash'), entropy=g.get('entropy'))
        if data.get('image'):
            img = data['image']
            gps = None
            if img.get('gps'):
                gps_data = img['gps']
                ts = gps_data.get('timestamp')
                gps = GPSCoordinates(latitude=gps_data.get('latitude', 0.0), longitude=gps_data.get('longitude', 0.0), altitude=gps_data.get('altitude'), accuracy=gps_data.get('accuracy'), timestamp=datetime.fromisoformat(ts) if ts else None)
            result.image = ImageMetadata(width=img.get('width'), height=img.get('height'), format=img.get('format'), mode=img.get('mode'), exif=img.get('exif', {}), gps=gps, camera_make=img.get('camera_make'), camera_model=img.get('camera_model'), lens=img.get('lens'), focal_length=img.get('focal_length'), exposure_time=img.get('exposure_time'), f_number=img.get('f_number'), iso=img.get('iso'), flash=img.get('flash'), orientation=img.get('orientation'))
        if data.get('pdf'):
            pdf = data['pdf']
            result.pdf = PDFMetadata(title=pdf.get('title'), author=pdf.get('author'), subject=pdf.get('subject'), creator=pdf.get('creator'), producer=pdf.get('producer'), creation_date=datetime.fromisoformat(pdf['creation_date']) if pdf.get('creation_date') else None, modification_date=datetime.fromisoformat(pdf['modification_date']) if pdf.get('modification_date') else None, num_pages=pdf.get('num_pages'), pdf_version=pdf.get('pdf_version'), is_encrypted=pdf.get('is_encrypted', False), permissions=pdf.get('permissions', {}), embedded_files=pdf.get('embedded_files', []))
        if data.get('docx'):
            d = data['docx']
            result.docx = DocxMetadata(title=d.get('title'), author=d.get('author'), subject=d.get('subject'), keywords=d.get('keywords'), category=d.get('category'), comments=d.get('comments'), created=datetime.fromisoformat(d['created']) if d.get('created') else None, modified=datetime.fromisoformat(d['modified']) if d.get('modified') else None, last_modified_by=d.get('last_modified_by'), revision=d.get('revision'), company=d.get('company'), manager=d.get('manager'), template=d.get('template'), total_editing_time=d.get('total_editing_time'))
        if data.get('audio'):
            a = data['audio']
            result.audio = AudioMetadata(**a)
        if data.get('video'):
            v = data['video']
            result.video = VideoMetadata(title=v.get('title'), duration=v.get('duration'), bitrate=v.get('bitrate'), width=v.get('width'), height=v.get('height'), fps=v.get('fps'), video_codec=v.get('video_codec'), video_bitrate=v.get('video_bitrate'), audio_codec=v.get('audio_codec'), audio_bitrate=v.get('audio_bitrate'), audio_channels=v.get('audio_channels'), audio_sample_rate=v.get('audio_sample_rate'), container_format=v.get('container_format'), creation_time=datetime.fromisoformat(v['creation_time']) if v.get('creation_time') else None)
        if data.get('archive'):
            a = data['archive']
            result.archive = ArchiveMetadata(archive_type=a.get('archive_type'), num_files=a.get('num_files'), uncompressed_size=a.get('uncompressed_size'), is_encrypted=a.get('is_encrypted', False), compression_ratio=a.get('compression_ratio'), comment=a.get('comment'), files=a.get('files', []))
        if data.get('steganalysis'):
            s = data['steganalysis']
            result.steganalysis = SteganalysisMetadata(lsb_suspicious=s.get('lsb_suspicious', False), lsb_score=s.get('lsb_score', 0.0), histogram_suspicious=s.get('histogram_suspicious', False), histogram_score=s.get('histogram_score', 0.0), chi_square_score=s.get('chi_square_score', 0.0), stegdetect_result=s.get('stegdetect_result'), stegdetect_available=s.get('stegdetect_available', False), overall_suspicious=s.get('overall_suspicious', False), confidence=s.get('confidence', 0.0))
        if data.get('timeline'):
            result.timeline = [TimelineEvent(timestamp=datetime.fromisoformat(e['timestamp']), event_type=e['event_type'], source=e['source'], confidence=e.get('confidence', 1.0)) for e in data['timeline']]
        if data.get('attribution'):
            result.attribution = AttributionData(**data['attribution'])
        if data.get('scrubbing'):
            s = data['scrubbing']
            result.scrubbing = ScrubbingAnalysis(is_scrubbed=s.get('is_scrubbed', False), confidence=s.get('confidence', 0.0), indicators=s.get('indicators', []), missing_expected_fields=s.get('missing_expected_fields', []), suspicious_patterns=s.get('suspicious_patterns', []))
        return result

def create_metadata_extractor(cache_path: str | None=None, config: Any | None=None) -> UniversalMetadataExtractor:
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
    kwargs = {'cache_path': cache_path}
    if config:
        if hasattr(config, 'enable_metadata_extraction'):
            kwargs['enable_exif'] = getattr(config, 'metadata_extract_exif', True)
            kwargs['enable_gps'] = getattr(config, 'metadata_extract_gps', True)
            kwargs['enable_reverse_geocode'] = getattr(config, 'metadata_reverse_geocode', False)
            kwargs['enable_audio'] = getattr(config, 'metadata_extract_audio', True)
            kwargs['enable_video'] = getattr(config, 'metadata_extract_video', False)
            kwargs['calculate_hashes'] = getattr(config, 'metadata_calculate_hashes', True)
            kwargs['hash_algorithms'] = getattr(config, 'metadata_hash_algorithms', ['md5', 'sha256'])
            kwargs['max_file_size'] = getattr(config, 'metadata_max_file_size', 1073741824)
            kwargs['batch_size'] = getattr(config, 'metadata_batch_size', 100)
        elif isinstance(config, dict):
            kwargs.update(config)
    return UniversalMetadataExtractor(**kwargs)
