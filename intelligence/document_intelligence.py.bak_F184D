"""
Document Intelligence Engine
============================

Advanced document analysis for OSINT research.
Extracts metadata, hidden content, and forensic artifacts from documents.
Self-hosted on M1 8GB - no external services.

Features:
- PDF metadata and hidden layer extraction
- Microsoft Office document analysis (OLE, OOXML)
- Image metadata (EXIF, XMP) with GPS extraction
- Embedded object detection
- Document versioning analysis
- Author/organization tracking
- Hidden text and comment extraction
- Font and encoding analysis

M1 Optimized: Streaming processing, MLX-accelerated where possible
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import struct
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional, Set, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

# Optional dependencies with graceful fallback
try:
    import piexif
    from PIL import Image, ExifTags, ImageChops
    from PIL.TiffImagePlugin import IFDRational
    PIL_AVAILABLE = True
except ImportError:
    piexif = None
    PIL_AVAILABLE = False
    logger.warning("PIL not available - image analysis disabled")


try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning("PyMuPDF not available - advanced PDF analysis disabled")

# Sprint 29: Progressive document parsing
DOCUMENT_INTELLIGENCE_AVAILABLE = True

# MLX availability check for semantic scoring
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    logger.warning("MLX not available - semantic scoring disabled")

# Sprint 53: MPS (Metal Performance Shaders) detection
# NOTE: torch import moved to function scope to avoid loading 659 torch modules at import time
MPS_AVAILABLE = False

# Sprint 8AW: Aho-Corasick lazy integration for suspicious_keywords
_AhoExtractorModule: Optional[Any] = None
_AHO_AVAILABLE: Optional[bool] = None


def _get_aho_extractor():
    """Lazy import of aho_extractor — NOT loaded at document_intelligence boot."""
    global _AhoExtractorModule, _AHO_AVAILABLE
    if _AHO_AVAILABLE is None:
        try:
            from hledac.universal.utils import aho_extractor
            _AhoExtractorModule = aho_extractor
            _AHO_AVAILABLE = True
        except Exception:
            _AhoExtractorModule = None
            _AHO_AVAILABLE = False
    return _AhoExtractorModule


def _check_mps_available():
    """Check MPS availability lazily - only when actually needed."""
    global MPS_AVAILABLE
    if MPS_AVAILABLE:
        return True
    try:
        import torch
        if torch.backends.mps.is_available():
            MPS_AVAILABLE = True
            return True
    except ImportError:
        pass
    return False

# Maximum image size for MPS analysis (protect against OOM)
MAX_IMAGE_SIZE = 2048


class DocumentType(Enum):
    """Supported document types."""
    PDF = "pdf"
    MICROSOFT_WORD = "docx"
    MICROSOFT_EXCEL = "xlsx"
    MICROSOFT_POWERPOINT = "pptx"
    OPEN_DOCUMENT_TEXT = "odt"
    OPEN_DOCUMENT_SPREADSHEET = "ods"
    RTF = "rtf"
    IMAGE = "image"
    UNKNOWN = "unknown"


class MetadataCategory(Enum):
    """Categories of document metadata."""
    CREATION = "creation"
    MODIFICATION = "modification"
    AUTHORSHIP = "authorship"
    SOFTWARE = "software"
    LOCATION = "location"
    DEVICE = "device"
    CUSTOM = "custom"


@dataclass
class GeoLocation:
    """GPS coordinates extracted from EXIF."""
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    timestamp: Optional[datetime] = None
    gps_version: Optional[str] = None
    coordinate_system: str = "WGS84"

    def to_dms(self) -> Tuple[Tuple[int, int, float], str]:
        """Convert decimal degrees to DMS (Degrees, Minutes, Seconds)."""
        def decimal_to_dms(decimal: float) -> Tuple[int, int, float]:
            degrees = int(decimal)
            minutes_float = abs(decimal - degrees) * 60
            minutes = int(minutes_float)
            seconds = (minutes_float - minutes) * 60
            return degrees, minutes, seconds

        lat_dms = decimal_to_dms(self.latitude)
        lat_ref = "N" if self.latitude >= 0 else "S"

        return lat_dms, lat_ref

    def to_google_maps_url(self) -> str:
        """Generate Google Maps URL."""
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"


@dataclass
class EXIFData:
    """Comprehensive EXIF data from images."""
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    software: Optional[str] = None
    date_time_original: Optional[datetime] = None
    date_time_digitized: Optional[datetime] = None
    gps_location: Optional[GeoLocation] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    orientation: Optional[int] = None
    flash: Optional[bool] = None
    focal_length: Optional[float] = None
    iso_speed: Optional[int] = None
    aperture: Optional[str] = None
    shutter_speed: Optional[str] = None
    raw_exif: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentMetadata:
    """Comprehensive document metadata."""
    file_hash_md5: str
    file_hash_sha1: str
    file_hash_sha256: str
    file_size_bytes: int
    file_type: DocumentType
    file_extension: str

    # Author metadata
    author: Optional[str] = None
    creator: Optional[str] = None
    last_modified_by: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    subject: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    category: Optional[str] = None

    # Temporal metadata
    creation_date: Optional[datetime] = None
    modification_date: Optional[datetime] = None
    last_printed: Optional[datetime] = None

    # Software metadata
    creating_application: Optional[str] = None
    application_version: Optional[str] = None
    os_platform: Optional[str] = None

    # Location metadata
    location: Optional[str] = None
    gps_coordinates: Optional[GeoLocation] = None

    # Advanced metadata
    revision_number: Optional[int] = None
    total_editing_time_minutes: Optional[int] = None
    template_used: Optional[str] = None
    manager: Optional[str] = None
    hyperlinks_base: Optional[str] = None

    # Raw metadata storage
    raw_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EmbeddedObject:
    """Represents an embedded object in a document."""
    object_type: str
    object_name: str
    content_type: Optional[str]
    size_bytes: int
    extracted_content: Optional[bytes] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DocumentAnalysis:
    """Complete document analysis result."""
    metadata: DocumentMetadata
    embedded_objects: List[EmbeddedObject] = field(default_factory=list)
    hyperlinks: List[str] = field(default_factory=list)
    email_addresses: List[str] = field(default_factory=list)
    ip_addresses: List[str] = field(default_factory=list)
    comments: List[str] = field(default_factory=list)
    revisions: List[Dict[str, Any]] = field(default_factory=list)
    hidden_text: List[str] = field(default_factory=list)
    suspicious_indicators: List[str] = field(default_factory=list)
    exif_data: Optional[EXIFData] = None


class PDFAnalyzer:
    """
    Advanced PDF document analyzer.

    Extracts metadata, text, embedded objects, and forensic artifacts.
    """

    # Regex patterns for data extraction
    EMAIL_PATTERN = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    IP_PATTERN = re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
    URL_PATTERN = re.compile(r"https?://[^\s<>\"{}|\\^`\[\]]+")

    def __init__(self):
        self.suspicious_keywords = [
            "confidential", "classified", "secret", "proprietary",
            "internal use only", "do not distribute", "draft",
            "redacted", "sensitive"
        ]

    def analyze(self, file_path: Union[str, bytes, BinaryIO]) -> DocumentAnalysis:
        """
        Analyze PDF document.

        Args:
            file_path: Path to PDF file, bytes, or file-like object

        Returns:
            DocumentAnalysis with all extracted data
        """
        if not PYMUPDF_AVAILABLE:
            return self._basic_pdf_analysis(file_path)

        try:
            doc = fitz.open(file_path)

            # Extract metadata
            metadata = self._extract_pdf_metadata(doc, file_path)

            # Progressive PDF analysis: probe first, deepen only on high signal
            probe_result = self._probe_pdf(doc)

            # Use deep parse only if signal is high (>= 0.5)
            SIGNAL_THRESHOLD = 0.5
            full_text = ""

            if probe_result["signal_score"] >= SIGNAL_THRESHOLD:
                # High signal - do deep parse on top pages
                deep_texts = self._deep_parse_pages(doc, probe_result["candidate_pages"])
                full_text = " ".join(deep_texts)
            else:
                # Low signal - just probe (quick extraction)
                for page_num in probe_result["candidate_pages"]:
                    if page_num < len(doc):
                        page = doc[page_num]
                        full_text += page.get_text()

            # Extract embedded objects
            embedded_objects = self._extract_pdf_objects(doc)

            # Extract hyperlinks
            hyperlinks = self.URL_PATTERN.findall(full_text)

            # Extract emails
            emails = self.EMAIL_PATTERN.findall(full_text)

            # Extract IP addresses
            ip_addresses = self.IP_PATTERN.findall(full_text)

            # Check for suspicious content
            suspicious = self._detect_suspicious_content(full_text)

            doc.close()

            return DocumentAnalysis(
                metadata=metadata,
                embedded_objects=embedded_objects,
                hyperlinks=hyperlinks,
                email_addresses=emails,
                ip_addresses=ip_addresses,
                suspicious_indicators=suspicious
            )

        except Exception as e:
            logger.warning(f"PDF analysis failed: {e}")
            return DocumentAnalysis(
                metadata=DocumentMetadata(),
                embedded_objects=[],
                hyperlinks=[],
                email_addresses=[],
                ip_addresses=[],
                suspicious_indicators=[]
            )

    def _probe_pdf(self, doc) -> dict:
        """
        Probe PDF to estimate signal score and identify candidate pages.

        Args:
            doc: PyMuPDF document object

        Returns:
            dict with "signal_score" (float) and "candidate_pages" (List[int])
        """
        MAX_DEEP_PDF_PAGES = 12

        if not PYMUPDF_AVAILABLE:
            return {"signal_score": 0.5, "candidate_pages": list(range(min(10, len(doc) if hasattr(doc, '__len__') else 10)))}

        try:
            total_pages = len(doc)
            if total_pages == 0:
                return {"signal_score": 0.0, "candidate_pages": []}

            # Sample pages to estimate signal
            sample_size = min(5, total_pages)
            sample_indices = [int(i * total_pages / sample_size) for i in range(sample_size)]

            text_lengths = []
            has_images = 0

            for page_num in sample_indices:
                page = doc[page_num]
                text = page.get_text()
                text_lengths.append(len(text))

                # Check for images (indicates rich content)
                image_list = page.get_images()
                if image_list:
                    has_images += 1

            # Calculate signal score based on content richness
            avg_text_length = sum(text_lengths) / len(text_lengths) if text_lengths else 0
            image_ratio = has_images / len(sample_indices) if sample_indices else 0

            # Signal is high if there's substantial text or images
            signal_score = min(1.0, (avg_text_length / 500.0) + (image_ratio * 0.3))

            # Rank pages by text length (most content = higher priority)
            page_scores = []
            for page_num in range(total_pages):
                try:
                    page = doc[page_num]
                    text = page.get_text()
                    images = len(page.get_images()) if PYMUPDF_AVAILABLE else 0
                    score = len(text) + (images * 100)
                    page_scores.append((page_num, score))
                except Exception:
                    page_scores.append((page_num, 0))

            # Sort by score descending and take top pages
            page_scores.sort(key=lambda x: x[1], reverse=True)
            candidate_pages = [p[0] for p in page_scores[:MAX_DEEP_PDF_PAGES]]

            return {
                "signal_score": signal_score,
                "candidate_pages": candidate_pages
            }

        except Exception as e:
            logger.warning(f"PDF probing failed: {e}")
            return {"signal_score": 0.5, "candidate_pages": list(range(5))}

    def _deep_parse_pages(self, doc, page_indices: List[int]) -> List[str]:
        """
        Deep parse specific pages of the PDF.

        Args:
            doc: PyMuPDF document object
            page_indices: List of page indices to parse

        Returns:
            List of extracted text strings for each page
        """
        if not PYMUPDF_AVAILABLE:
            return []

        results = []
        try:
            for page_num in page_indices:
                if page_num < len(doc):
                    page = doc[page_num]
                    text = page.get_text()
                    results.append(text)
        except Exception as e:
            logger.warning(f"Deep PDF parsing failed: {e}")

        return results

    def _extract_pdf_metadata(self, doc: fitz.Document, file_path) -> DocumentMetadata:
        """Extract PDF metadata."""
        pdf_metadata = doc.metadata

        # Calculate file hashes
        if isinstance(file_path, str):
            with open(file_path, "rb") as f:
                content = f.read()
        elif isinstance(file_path, bytes):
            content = file_path
        else:
            content = file_path.read()

        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha1(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()

        # Parse dates
        creation_date = self._parse_pdf_date(pdf_metadata.get("creationDate"))
        mod_date = self._parse_pdf_date(pdf_metadata.get("modDate"))

        return DocumentMetadata(
            file_hash_md5=md5_hash,
            file_hash_sha1=sha1_hash,
            file_hash_sha256=sha256_hash,
            file_size_bytes=len(content),
            file_type=DocumentType.PDF,
            file_extension=".pdf",
            author=pdf_metadata.get("author"),
            creator=pdf_metadata.get("creator"),
            title=pdf_metadata.get("title"),
            subject=pdf_metadata.get("subject"),
            keywords=pdf_metadata.get("keywords", "").split(",") if pdf_metadata.get("keywords") else [],
            creation_date=creation_date,
            modification_date=mod_date,
            creating_application=pdf_metadata.get("producer"),
            application_version=pdf_metadata.get("format"),
            raw_metadata=pdf_metadata
        )

    def _parse_pdf_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse PDF date string format."""
        if not date_str:
            return None

        try:
            # PDF date format: D:YYYYMMDDHHmmSSOHH'mm'
            if date_str.startswith("D:"):
                date_str = date_str[2:]

            # Extract components
            year = int(date_str[:4])
            month = int(date_str[4:6]) if len(date_str) >= 6 else 1
            day = int(date_str[6:8]) if len(date_str) >= 8 else 1
            hour = int(date_str[8:10]) if len(date_str) >= 10 else 0
            minute = int(date_str[10:12]) if len(date_str) >= 12 else 0
            second = int(date_str[12:14]) if len(date_str) >= 14 else 0

            return datetime(year, month, day, hour, minute, second)
        except Exception:
            return None

    def _extract_pdf_objects(self, doc: fitz.Document) -> List[EmbeddedObject]:
        """Extract embedded objects from PDF."""
        objects = []

        for xref in range(1, doc.xref_length()):
            try:
                obj_type = doc.xref_get_key(xref, "Type")
                subtype = doc.xref_get_key(xref, "Subtype")

                if subtype[1] == "Image":
                    # Extract image
                    base_image = doc.extract_image(xref)
                    if base_image:
                        objects.append(EmbeddedObject(
                            object_type="image",
                            object_name=f"image_{xref}",
                            content_type=base_image.get("ext"),
                            size_bytes=len(base_image.get("image", b"")),
                            extracted_content=base_image.get("image"),
                            metadata={
                                "width": base_image.get("width"),
                                "height": base_image.get("height"),
                                "colorspace": base_image.get("colorspace")
                            }
                        ))

                elif subtype[1] in ["FileAttachment", "EmbeddedFile"]:
                    # Extract embedded file
                    stream = doc.xref_stream(xref)
                    if stream:
                        name = doc.xref_get_key(xref, "F")
                        objects.append(EmbeddedObject(
                            object_type="file_attachment",
                            object_name=name[1] if name else f"attachment_{xref}",
                            content_type=None,
                            size_bytes=len(stream),
                            extracted_content=stream
                        ))

            except Exception:
                continue

        return objects

    def _detect_suspicious_content(self, text: str) -> List[str]:
        """Detect suspicious keywords in text using Aho-Corasick if available.

        Lazy integration (Sprint 8AW): ahocorasick is NOT loaded on boot.
        On first call, the automaton is built once and reused.
        Falls back to substring scan if aho_extractor is unavailable.
        """
        aho_mod = _get_aho_extractor()
        if aho_mod is not None:
            # Aho-Corasick path: uses cached singleton automaton
            return aho_mod.scan_suspicious_keywords_list(text)
        # Fallback: pure substring scan (original semantics preserved)
        text_lower = text.lower()
        return [kw for kw in self.suspicious_keywords if kw in text_lower]

    def _basic_pdf_analysis(self, file_path) -> DocumentAnalysis:
        """Fallback basic analysis without PyMuPDF."""
        if isinstance(file_path, str):
            with open(file_path, "rb") as f:
                content = f.read()
        elif isinstance(file_path, bytes):
            content = file_path
        else:
            content = file_path.read()

        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha1(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()

        # Extract text from PDF (basic)
        text = content.decode("utf-8", errors="ignore")

        metadata = DocumentMetadata(
            file_hash_md5=md5_hash,
            file_hash_sha1=sha1_hash,
            file_hash_sha256=sha256_hash,
            file_size_bytes=len(content),
            file_type=DocumentType.PDF,
            file_extension=".pdf"
        )

        return DocumentAnalysis(
            metadata=metadata,
            hyperlinks=self.URL_PATTERN.findall(text),
            email_addresses=self.EMAIL_PATTERN.findall(text)
        )


class OfficeDocumentAnalyzer:
    """
    Analyzer for Microsoft Office and OpenDocument files.
    """

    def analyze(self, file_path: Union[str, bytes]) -> DocumentAnalysis:
        """Analyze Office document."""
        if isinstance(file_path, str):
            with open(file_path, "rb") as f:
                content = f.read()
        else:
            content = file_path

        # Check if it's a ZIP-based format (modern Office)
        if content[:4] == b"PK\x03\x04":
            return self._analyze_ooxml(content)
        else:
            # Legacy binary format (OLE)
            return self._analyze_ole(content)

    def _analyze_ooxml(self, content: bytes) -> DocumentAnalysis:
        """Analyze Office Open XML format (docx, xlsx, pptx)."""
        embedded_objects = []
        hyperlinks = []
        comments = []

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                # Extract core properties
                metadata = self._extract_ooxml_core_props(z, content)

                # Extract comments
                if "word/comments.xml" in z.namelist():
                    comments_xml = z.read("word/comments.xml").decode("utf-8", errors="ignore")
                    comments = self._extract_comments_from_xml(comments_xml)

                # Extract hyperlinks
                if "word/document.xml" in z.namelist():
                    doc_xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
                    hyperlinks = PDFAnalyzer.URL_PATTERN.findall(doc_xml)

                # Find embedded objects
                for name in z.namelist():
                    if name.startswith("word/media/"):
                        data = z.read(name)
                        embedded_objects.append(EmbeddedObject(
                            object_type="media",
                            object_name=name.split("/")[-1],
                            content_type=None,
                            size_bytes=len(data),
                            extracted_content=data
                        ))

        except Exception as e:
            logger.error(f"OOXML analysis error: {e}")

        return DocumentAnalysis(
            metadata=metadata,
            embedded_objects=embedded_objects,
            hyperlinks=hyperlinks,
            comments=comments
        )

    def _extract_ooxml_core_props(self, z: zipfile.ZipFile, content: bytes) -> DocumentMetadata:
        """Extract core properties from OOXML."""
        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha1(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()

        props = {}

        try:
            if "docProps/core.xml" in z.namelist():
                core_xml = z.read("docProps/core.xml").decode("utf-8", errors="ignore")
                props = self._parse_core_xml(core_xml)
        except Exception:
            pass

        # Determine document type
        if "word/document.xml" in z.namelist():
            doc_type = DocumentType.MICROSOFT_WORD
            ext = ".docx"
        elif "xl/workbook.xml" in z.namelist():
            doc_type = DocumentType.MICROSOFT_EXCEL
            ext = ".xlsx"
        elif "ppt/presentation.xml" in z.namelist():
            doc_type = DocumentType.MICROSOFT_POWERPOINT
            ext = ".pptx"
        else:
            doc_type = DocumentType.UNKNOWN
            ext = ".unknown"

        return DocumentMetadata(
            file_hash_md5=md5_hash,
            file_hash_sha1=sha1_hash,
            file_hash_sha256=sha256_hash,
            file_size_bytes=len(content),
            file_type=doc_type,
            file_extension=ext,
            author=props.get("creator"),
            last_modified_by=props.get("lastModifiedBy"),
            title=props.get("title"),
            subject=props.get("subject"),
            keywords=props.get("keywords", "").split() if props.get("keywords") else [],
            creation_date=props.get("created"),
            modification_date=props.get("modified"),
            application_version=props.get("version"),
            raw_metadata=props
        )

    def _parse_core_xml(self, xml_content: str) -> Dict[str, Any]:
        """Parse core.xml properties."""
        props = {}

        # Simple regex extraction (without full XML parsing)
        patterns = {
            "creator": r"<dc:creator>(.*?)</dc:creator>",
            "lastModifiedBy": r"<cp:lastModifiedBy>(.*?)</cp:lastModifiedBy>",
            "title": r"<dc:title>(.*?)</dc:title>",
            "subject": r"<dc:subject>(.*?)</dc:subject>",
            "keywords": r"<cp:keywords>(.*?)</cp:keywords>",
            "created": r"<dcterms:created.*?>(.*?)</dcterms:created>",
            "modified": r"<dcterms:modified.*?>(.*?)</dcterms:modified>",
            "version": r"<cp:version>(.*?)</cp:version>"
        }

        for key, pattern in patterns.items():
            match = re.search(pattern, xml_content)
            if match:
                value = match.group(1)
                # Parse dates
                if key in ["created", "modified"]:
                    try:
                        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    except Exception:
                        pass
                props[key] = value

        return props

    def _extract_comments_from_xml(self, xml_content: str) -> List[str]:
        """Extract comments from Word XML."""
        comments = []
        # Simple regex for comment text
        pattern = r"<w:t>(.*?)</w:t>"
        for match in re.finditer(pattern, xml_content):
            text = match.group(1)
            if len(text) > 5:  # Filter out short fragments
                comments.append(text)
        return comments

    def _analyze_ole(self, content: bytes) -> DocumentAnalysis:
        """Analyze legacy OLE format."""
        # Basic OLE analysis without external libraries
        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha1(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()

        metadata = DocumentMetadata(
            file_hash_md5=md5_hash,
            file_hash_sha1=sha1_hash,
            file_hash_sha256=sha256_hash,
            file_size_bytes=len(content),
            file_type=DocumentType.UNKNOWN,
            file_extension=".doc"
        )

        return DocumentAnalysis(metadata=metadata)


class ImageAnalyzer:
    """
    Advanced image analysis for OSINT.

    Extracts EXIF data, GPS coordinates, and performs image forensics.
    """

    def analyze(self, file_path: Union[str, bytes]) -> DocumentAnalysis:
        """Analyze image file."""
        if not PIL_AVAILABLE:
            logger.warning("PIL not available - cannot analyze image")
            return self._basic_image_analysis(file_path)

        try:
            if isinstance(file_path, str):
                img = Image.open(file_path)
                with open(file_path, "rb") as f:
                    content = f.read()
            else:
                img = Image.open(io.BytesIO(file_path))
                content = file_path if isinstance(file_path, bytes) else file_path.read()

            # Calculate hashes
            md5_hash = hashlib.md5(content).hexdigest()
            sha1_hash = hashlib.sha1(content).hexdigest()
            sha256_hash = hashlib.sha256(content).hexdigest()

            # Extract EXIF
            exif_data = self._extract_exif(img)

            metadata = DocumentMetadata(
                file_hash_md5=md5_hash,
                file_hash_sha1=sha1_hash,
                file_hash_sha256=sha256_hash,
                file_size_bytes=len(content),
                file_type=DocumentType.IMAGE,
                file_extension=f".{img.format.lower()}" if img.format else ".unknown",
                image_width=img.width,
                image_height=img.height,
                gps_coordinates=exif_data.gps_location if exif_data else None,
                raw_metadata={"format": img.format, "mode": img.mode}
            )

            img.close()

            return DocumentAnalysis(
                metadata=metadata,
                exif_data=exif_data
            )

        except Exception as e:
            logger.error(f"Image analysis error: {e}")
            return self._basic_image_analysis(file_path)

    def _extract_exif(self, img: Image.Image) -> Optional[EXIFData]:
        """Extract EXIF data from image."""
        try:
            exif = img._getexif()
            if not exif:
                return None

            data = EXIFData()
            raw_exif = {}

            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                raw_exif[tag] = value

                if tag == "Make":
                    data.camera_make = value
                elif tag == "Model":
                    data.camera_model = value
                elif tag == "Software":
                    data.software = value
                elif tag == "DateTimeOriginal":
                    data.date_time_original = self._parse_exif_datetime(value)
                elif tag == "DateTimeDigitized":
                    data.date_time_digitized = self._parse_exif_datetime(value)
                elif tag == "ExifImageWidth":
                    data.image_width = value
                elif tag == "ExifImageHeight":
                    data.image_height = value
                elif tag == "Orientation":
                    data.orientation = value
                elif tag == "Flash":
                    data.flash = bool(value & 1) if value else None
                elif tag == "FocalLength":
                    data.focal_length = float(value) if isinstance(value, (int, float, IFDRational)) else None
                elif tag == "ISOSpeedRatings":
                    data.iso_speed = value[0] if isinstance(value, tuple) else value
                elif tag == "FNumber":
                    data.aperture = f"f/{value}"
                elif tag == "ExposureTime":
                    if isinstance(value, (int, float)):
                        data.shutter_speed = f"1/{1/value:.0f}s" if value < 1 else f"{value}s"
                elif tag == "GPSInfo":
                    data.gps_location = self._extract_gps(exif)

            data.raw_exif = raw_exif
            return data

        except Exception as e:
            logger.error(f"EXIF extraction error: {e}")
            return None

    def _parse_exif_datetime(self, value) -> Optional[datetime]:
        """Parse EXIF datetime string."""
        if isinstance(value, str):
            try:
                return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")
            except ValueError:
                pass
        return None

    def _extract_gps(self, exif: Dict) -> Optional[GeoLocation]:
        """Extract GPS coordinates from EXIF."""
        try:
            gps_info = exif.get("GPSInfo")
            if not gps_info:
                return None

            def convert_dms(dms) -> float:
                """Convert DMS tuple to decimal degrees."""
                if isinstance(dms, tuple):
                    degrees = dms[0]
                    minutes = dms[1]
                    seconds = dms[2]
                    return float(degrees) + float(minutes) / 60 + float(seconds) / 3600
                return float(dms)

            # Latitude
            lat_ref = gps_info.get(1)  # N or S
            lat_dms = gps_info.get(2)  # (degrees, minutes, seconds)

            # Longitude
            lon_ref = gps_info.get(3)  # E or W
            lon_dms = gps_info.get(4)

            # Altitude
            altitude = gps_info.get(6)

            if lat_dms and lon_dms:
                lat = convert_dms(lat_dms)
                lon = convert_dms(lon_dms)

                if lat_ref == "S":
                    lat = -lat
                if lon_ref == "W":
                    lon = -lon

                return GeoLocation(
                    latitude=lat,
                    longitude=lon,
                    altitude=float(altitude) if altitude else None,
                    timestamp=None  # Could extract from GPSDateStamp
                )

        except Exception as e:
            logger.error(f"GPS extraction error: {e}")

        return None

    def _basic_image_analysis(self, file_path) -> DocumentAnalysis:
        """Basic analysis without PIL."""
        if isinstance(file_path, str):
            with open(file_path, "rb") as f:
                content = f.read()
        else:
            content = file_path if isinstance(file_path, bytes) else file_path.read()

        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha1(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()

        metadata = DocumentMetadata(
            file_hash_md5=md5_hash,
            file_hash_sha1=sha1_hash,
            file_hash_sha256=sha256_hash,
            file_size_bytes=len(content),
            file_type=DocumentType.IMAGE,
            file_extension=".unknown"
        )

        return DocumentAnalysis(metadata=metadata)


# Sprint 44: Deep Forensics Analyzer
class DeepForensicsAnalyzer:
    """Advanced forensics for images - EXIF, ELA, steganography detection."""

    def __init__(self, orch: Any = None):
        """Initialize DeepForensicsAnalyzer.

        Args:
            orch: Optional orchestrator reference for graph integration (S49-C)
        """
        import concurrent.futures

        self._orch = orch  # S49-C: Reference to orchestrator
        self._stegdetect_path = Path.home() / '.hledac' / 'bin' / 'stegdetect'
        # Sprint 45: Persistent server for fast analysis
        self._stegdetect_server = StegdetectServer()
        # Sprint 53: Thread pool for MPS operations
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    async def _ensure_stegdetect(self):
        """Compile and install stegdetect if missing."""
        if self._stegdetect_path.exists():
            return

        os.makedirs(self._stegdetect_path.parent, exist_ok=True)

        # Clone repo
        src_dir = Path.home() / '.hledac' / 'src' / 'stegdetect'
        os.makedirs(src_dir, exist_ok=True)

        try:
            proc = await asyncio.create_subprocess_exec(
                'git', 'clone', 'https://github.com/abeluck/stegdetect.git', str(src_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()

            # Compile
            proc = await asyncio.create_subprocess_exec(
                'make', '-C', str(src_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()

            # Copy binary
            import shutil
            shutil.copy(src_dir / 'stegdetect', self._stegdetect_path)
            os.chmod(self._stegdetect_path, 0o755)
        except Exception as e:
            logger.warning(f"[STEGDETECT] Compilation failed: {e}")

    def _parse_gps(self, gps_dict):
        """Parse GPS data from EXIF."""
        try:
            lat = gps_dict.get(2)
            lon = gps_dict.get(4)
            lat_ref = gps_dict.get(1)
            lon_ref = gps_dict.get(3)

            if lat and lon:
                lat_dec = lat[0] + lat[1]/60 + lat[2]/3600
                lon_dec = lon[0] + lon[1]/60 + lon[2]/3600

                if lat_ref == 'S':
                    lat_dec = -lat_dec
                if lon_ref == 'W':
                    lon_dec = -lon_dec

                return {'lat': lat_dec, 'lon': lon_dec}
        except:
            pass
        return None

    async def analyze_image(self, content: bytes, url: str = None) -> Dict[str, Any]:
        """Analyze image for forensic artifacts.

        Args:
            content: Image bytes
            url: Optional URL of the image for graph integration (S49-C)

        Returns:
            Dict with analysis results including ela_score, suspicious flag, etc.
        """
        result = {}

        # 1. EXIF / GPS extraction (always for JPEG)
        if content.startswith(b'\xff\xd8') and piexif:
            try:
                exif = piexif.load_from_bytes(content)
                gps = exif.get('GPS')
                if gps:
                    gps_coords = self._parse_gps(gps)
                    if gps_coords:
                        result['gps_coords'] = gps_coords
            except Exception:
                pass

        # 2. ELA analysis (always)
        try:
            ela_score = await self._ela_analysis(content)
            result['ela_score'] = ela_score
            if ela_score > 0.3:
                result['suspicious'] = True

            # S49-C: Flag high ELA scores in graph
            if self._orch and ela_score > 0.7 and url:
                try:
                    if hasattr(self._orch, '_research_mgr') and self._orch._research_mgr:
                        rd = self._orch._research_mgr.relationship_discovery
                        if rd and hasattr(rd, 'flag_manipulated_image'):
                            await rd.flag_manipulated_image(url=url, ela_score=ela_score)
                except Exception as e:
                    logger.warning(f"ELA→Graph forward failed: {e}")
        except Exception:
            pass

        # 3. Steganography detection (>10KB)
        if len(content) > 10_000:
            try:
                stego_prob = await self._stegdetect(content)
                result['stego_probability'] = stego_prob
                if stego_prob > 0.1:
                    result['suspicious'] = True
            except Exception:
                pass

        return result

    async def _ela_analysis(self, content: bytes) -> float:
        """Error Level Analysis - returns manipulation probability 0-1.

        Uses MPS if available, otherwise falls back to CPU.
        """
        if _check_mps_available():
            return await self._ela_analysis_mps(content)
        else:
            return await self._ela_analysis_cpu(content)

    async def _ela_analysis_mps(self, content: bytes) -> float:
        """MPS-accelerated ELA analysis."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread_pool,
            self._ela_analysis_mps_sync,
            content
        )

    def _ela_analysis_mps_sync(self, content: bytes) -> float:
        """Synchronous MPS implementation of ELA."""
        import torch
        from PIL import Image

        try:
            # Load image
            img = Image.open(io.BytesIO(content)).convert('RGB')

            # Size limit - resize if needed
            if img.width > MAX_IMAGE_SIZE or img.height > MAX_IMAGE_SIZE:
                ratio = min(MAX_IMAGE_SIZE / img.width, MAX_IMAGE_SIZE / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
                logger.debug(f"Image resized to {new_size} for MPS ELA")

            # Convert to tensor and move to MPS
            tensor = torch.from_numpy(np.array(img)).float().permute(2, 0, 1).unsqueeze(0) / 255.0
            tensor = tensor.to('mps')

            with torch.no_grad():
                # Simulate JPEG compression via avg pool and upscale
                compressed = torch.nn.functional.avg_pool2d(tensor, 2)
                upscaled = torch.nn.functional.interpolate(compressed, scale_factor=2, mode='nearest')
                diff = torch.abs(tensor - upscaled)
                ela_score = diff.mean().item()

            return ela_score
        except Exception as e:
            logger.warning(f"MPS ELA failed, falling back to CPU: {e}")
            # Fallback to CPU on error
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(self._ela_analysis_cpu_sync, content)
                return future.result()
        finally:
            # Clear GPU cache
            if hasattr(torch.mps, 'empty_cache'):
                try:
                    torch.mps.empty_cache()
                except Exception:
                    pass

    async def _ela_analysis_cpu(self, content: bytes) -> float:
        """CPU-based ELA analysis."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread_pool,
            self._ela_analysis_cpu_sync,
            content
        )

    def _ela_analysis_cpu_sync(self, content: bytes) -> float:
        """Synchronous CPU implementation of ELA."""
        # Import here for thread safety
        from PIL import Image, ImageChops

        img = Image.open(io.BytesIO(content))

        # Save with JPEG quality 95
        tmp = io.BytesIO()
        img.save(tmp, format='JPEG', quality=95)
        tmp.seek(0)
        compressed = Image.open(tmp)

        # Difference image
        diff = ImageChops.difference(img, compressed)
        diff_np = np.array(diff.convert('L'))

        # Normalize
        ela_score = np.mean(diff_np) / 255.0
        return ela_score

    async def _stegdetect(self, content: bytes) -> float:
        """Run stegdetect on image using persistent server."""
        return await self._stegdetect_server.analyze(content)


# Sprint 45: Persistent Stegdetect Server
# Sprint 47: Added semaphore pool for concurrent analysis
class StegdetectServer:
    """Persistent stegdetect process with semaphore pool for concurrent analysis."""

    def __init__(self, max_workers: int = 4):
        self._procs: List[asyncio.subprocess.Process] = []
        self._bin_path = Path.home() / '.hledac' / 'bin' / 'stegdetect'
        self._semaphore = asyncio.Semaphore(max_workers)
        self._lock = asyncio.Lock()
        self._max_workers = max_workers
        self._initialized = False

    async def _ensure_processes(self):
        """Ensure worker processes are running (pool instead of single server)."""
        if self._initialized and all(p.returncode is None for p in self._procs if p):
            return

        # Ensure binary exists
        fa = DeepForensicsAnalyzer()
        await fa._ensure_stegdetect()

        async with self._lock:
            # Start pool of worker processes
            self._procs = []
            for _ in range(self._max_workers):
                proc = await asyncio.create_subprocess_exec(
                    str(self._bin_path), "-r", "-s",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                self._procs.append(proc)
            self._initialized = True

    # Sprint 45 compatibility alias
    async def ensure_running(self):
        """Alias for _ensure_processes (Sprint 45 compatibility)."""
        return await self._ensure_processes()

    async def analyze(self, content: bytes) -> float:
        """Analyze image content for steganography using semaphore pool."""
        async with self._semaphore:
            await self._ensure_processes()

            # Find available process
            proc = None
            for p in self._procs:
                if p.returncode is None:
                    proc = p
                    break

            if proc is None:
                # All processes dead, restart
                await self._ensure_processes()
                proc = self._procs[0]

            # Write to temp file (stegdetect needs file)
            tmp = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            tmp.write(content)
            tmp.close()

            try:
                # Send file path to stegdetect
                proc.stdin.write(f"{tmp.name}\n".encode())
                await proc.stdin.drain()

                line = await proc.stdout.readline()
                return 0.8 if b'positive' in line else 0.0
            except Exception as e:
                logger.warning(f"[STEGDETECT SERVER] Failed: {e}")
                return 0.0
            finally:
                try:
                    os.unlink(tmp.name)
                except FileNotFoundError:
                    pass

    async def restart(self):
        """Restart all stegdetect processes."""
        async with self._lock:
            for proc in self._procs:
                try:
                    proc.kill()
                    await proc.wait()
                except:
                    pass
            self._procs = []
            self._initialized = False
        self._proc = None
        await self.ensure_running()


class DocumentIntelligenceEngine:
    """
    Main engine for document intelligence analysis.

    Provides unified interface for analyzing all document types.
    """

    def __init__(self):
        self.pdf_analyzer = PDFAnalyzer()
        self.office_analyzer = OfficeDocumentAnalyzer()
        self.image_analyzer = ImageAnalyzer()
        # Sprint 44: Deep Forensics
        self._forensics = DeepForensicsAnalyzer()

    def analyze(self, file_path: str) -> DocumentAnalysis:
        """
        Analyze any supported document type.

        Args:
            file_path: Path to document file

        Returns:
            DocumentAnalysis with all extracted intelligence
        """
        extension = file_path.lower().split(".")[-1] if "." in file_path else ""

        if extension == "pdf":
            return self.pdf_analyzer.analyze(file_path)
        elif extension in ["docx", "xlsx", "pptx", "odt", "ods"]:
            return self.office_analyzer.analyze(file_path)
        elif extension in ["jpg", "jpeg", "png", "tiff", "tif", "gif", "bmp", "webp"]:
            analysis = self.image_analyzer.analyze(file_path)
            # Sprint 44: Add forensics for images
            try:
                with open(file_path, "rb") as f:
                    content = f.read()
                if hasattr(self, '_forensics'):
                    forensics = asyncio.run(self._forensics.analyze_image(content))
                    if forensics:
                        analysis.metadata.raw_metadata['forensics'] = forensics
            except Exception:
                pass
            return analysis
        else:
            # Try to detect type
            with open(file_path, "rb") as f:
                header = f.read(8)

            if header[:4] == b"%PDF":
                return self.pdf_analyzer.analyze(file_path)
            elif header[:4] == b"PK\x03\x04":
                return self.office_analyzer.analyze(file_path)
            else:
                logger.warning(f"Unknown file type: {file_path}")
                return self._create_unknown_analysis(file_path)

    def _create_unknown_analysis(self, file_path: str) -> DocumentAnalysis:
        """Create analysis for unknown file type."""
        with open(file_path, "rb") as f:
            content = f.read()

        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha1(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()

        metadata = DocumentMetadata(
            file_hash_md5=md5_hash,
            file_hash_sha1=sha1_hash,
            file_hash_sha256=sha256_hash,
            file_size_bytes=len(content),
            file_type=DocumentType.UNKNOWN,
            file_extension=f".{file_path.split('.')[-1]}" if "." in file_path else ".unknown"
        )

        return DocumentAnalysis(metadata=metadata)

    def batch_analyze(self, file_paths: List[str]) -> Dict[str, DocumentAnalysis]:
        """Analyze multiple documents."""
        results = {}
        for path in file_paths:
            try:
                results[path] = self.analyze(path)
            except Exception as e:
                logger.error(f"Error analyzing {path}: {e}")
                results[path] = None
        return results

    # ============================================================================
    # Sprint 29: Progressive Document Parsing
    # ============================================================================

    def probe(self, url: str, preview_bytes: bytes, query: str = "") -> Dict[str, Any]:
        """
        Probe document to estimate value score for progressive parsing.

        Args:
            url: Document URL
            preview_bytes: Preview content bytes (first ~256KB)
            query: Optional search query for semantic scoring

        Returns:
            dict with heuristic_score, semantic_score (if computed), final_score, keywords, entities
        """
        result: Dict[str, Any] = {
            "url": url,
            "heuristic_score": 0.5,
            "final_score": 0.5,
            "keywords": [],
            "entities": []
        }

        # Try to decode preview text
        try:
            text = preview_bytes.decode("utf-8", errors="ignore")
        except Exception:
            text = ""

        if not text:
            return result

        # Compute heuristic score based on content analysis
        heuristic_score = self._compute_heuristic_score(text)
        result["heuristic_score"] = heuristic_score

        # Sprint 29: Optional semantic scoring if query provided and MLX available
        if query and MLX_AVAILABLE:
            try:
                semantic_score = self._compute_semantic_score(text, query)
                if semantic_score is not None:
                    result["semantic_score"] = semantic_score
                    # Blend scores: 50% heuristic + 50% semantic
                    result["final_score"] = 0.5 * heuristic_score + 0.5 * semantic_score
            except Exception as e:
                logger.debug(f"Semantic scoring failed: {e}")
                result["final_score"] = heuristic_score
        else:
            result["final_score"] = heuristic_score

        # Extract keywords for intelligence
        result["keywords"] = self._extract_keywords(text)

        return result

    def _compute_heuristic_score(self, text: str) -> float:
        """
        Compute heuristic value score based on content analysis.
        """
        if not text:
            return 0.5

        score = 0.0

        # High-value keywords density
        high_value_keywords = [
            "analysis", "research", "report", "study", "data", "results",
            "findings", "conclusion", "method", "evidence", "case", "review",
            "assessment", "evaluation", "detection", "identification", "model"
        ]
        text_lower = text.lower()
        keyword_count = sum(1 for kw in high_value_keywords if kw in text_lower)
        score += min(0.4, keyword_count * 0.05)

        # Text density (more content = potentially more valuable)
        text_length = len(text)
        if text_length > 1000:
            score += 0.2
        elif text_length > 500:
            score += 0.1

        # Check for structured content (numbers, lists)
        if re.search(r'\d+[\.,]\d+', text):  # Has numbers with decimals
            score += 0.1
        if re.search(r'^\s*[-*•]\s+', text, re.MULTILINE):  # Has bullet points
            score += 0.1

        # Penalize low-value content
        if "cookie" in text_lower or "privacy policy" in text_lower:
            score -= 0.2

        return max(0.0, min(1.0, score))

    def _compute_semantic_score(self, text: str, query: str) -> Optional[float]:
        """
        Compute semantic similarity score between text and query using ModernBERT.
        """
        try:
            # Lazy import ModelManager
            from ...brain.model_manager import get_model_manager

            mm = get_model_manager()

            # Check if ModernBERT is available
            if not mm.has_model("modernbert"):
                return None

            # Get embedding model
            embedder = mm.get_embedding_model("modernbert")

            try:
                # Split preview into chunks
                chunks = self._split_preview_into_chunks(text.encode("utf-8"), max_chunks=5, max_tokens=512)

                if not chunks:
                    return None

                # Get embeddings
                query_emb = embedder.embed(query)
                chunk_embs = embedder.embed_chunks(chunks)

                # Compute cosine similarity
                if not chunk_embs or not query_emb:
                    return None

                similarities = []
                for chunk_emb in chunk_embs:
                    sim = self._cosine_similarity(query_emb, chunk_emb)
                    similarities.append(sim)

                if similarities:
                    return sum(similarities) / len(similarities)
                return None

            finally:
                # Ensure model is released
                mm.release_model("modernbert")

        except Exception as e:
            logger.debug(f"Semantic scoring error: {e}")
            return None

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0

        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)

    def _split_preview_into_chunks(self, bytes_data: bytes, max_chunks: int = 5, max_tokens: int = 512) -> List[str]:
        """
        Split preview bytes into chunks for embedding.

        Args:
            bytes_data: Preview bytes
            max_chunks: Maximum number of chunks
            max_tokens: Maximum tokens per chunk (approximated by word count)

        Returns:
            List of text chunks
        """
        try:
            text = bytes_data.decode("utf-8", errors="ignore")
        except Exception:
            return []

        # Split on double newlines to get paragraphs
        paragraphs = text.split("\n\n")

        chunks = []
        for para in paragraphs:
            # Approximate token count by word count
            words = para.split()
            if len(words) > max_tokens:
                # Truncate
                para = " ".join(words[:max_tokens])

            if para.strip():
                chunks.append(para.strip())

            if len(chunks) >= max_chunks:
                break

        return chunks

    def _extract_keywords(self, text: str) -> List[str]:
        """
        Extract high-value keywords from text.
        """
        # Simple keyword extraction based on common patterns
        keywords = set()

        # Extract capitalized phrases (potential entities)
        capitalized = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
        keywords.update([w.lower() for w in capitalized[:10]])

        # Extract technical terms
        tech_terms = re.findall(r'\b\w+(?:tion|ing|ed|ness|ment|ance|ity)\b', text.lower())
        keywords.update(tech_terms[:10])

        return list(keywords)[:20]


# ============================================================================
# MLX Long-Context Document Analyzer
# ============================================================================

@dataclass
class EntityMention:
    """Mention of an entity in text."""
    text: str
    entity_type: str  # person, organization, location, email, phone, etc.
    start_pos: int
    end_pos: int
    confidence: float
    context: str  # surrounding text


@dataclass
class CrossDocumentLink:
    """Link between entities across documents."""
    entity_type: str
    value: str
    documents: List[str]
    confidence: float
    first_seen: str
    last_seen: str


@dataclass
class TimelineEvent:
    """Event extracted from document with temporal information."""
    date: Optional[datetime]
    description: str
    source_document: str
    entities_involved: List[str]
    confidence: float


@dataclass
class LongContextAnalysis:
    """Results from MLX long-context analysis."""
    total_chunks: int
    total_tokens: int
    entities: List[EntityMention]
    cross_document_links: List[CrossDocumentLink]
    timeline: List[TimelineEvent]
    summary: str
    key_findings: List[str]
    memory_usage_mb: float
    processing_time_seconds: float


class MLXLongContextAnalyzer:
    """
    MLX-powered analysis for ultra-large documents on M1 8GB.

    Capabilities:
    - Chunking with intelligent overlap for context preservation
    - Cross-document entity resolution
    - Timeline reconstruction from large datasets
    - MLX-accelerated similarity matching
    - Memory-efficient streaming processing

    M1 Optimized:
    - Streaming processing to keep memory < 5.5GB
    - MLX lazy evaluation for efficiency
    - Smart chunk sizing based on available RAM
    """

    def __init__(self, chunk_size: int = 4096, overlap: int = 512):
        """
        Initialize MLX Long-Context Analyzer.

        Args:
            chunk_size: Tokens per chunk (default 4096 for M1 8GB)
            overlap: Overlap between chunks for context continuity
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.chunk_embeddings: Optional[mx.array] = None
        self.chunk_texts: List[str] = []

        # Entity patterns for extraction
        self.patterns = {
            'email': re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
            'phone': re.compile(r'\b(?:\+?1[-.\s]?)?\(?[0-9]{3}\)?[-.\s]?[0-9]{3}[-.\s]?[0-9]{4}\b'),
            'ip_address': re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'),
            'url': re.compile(r'https?://(?:[-\w.])+(?:[:\d]+)?(?:/(?:[\w/_.])*(?:\?(?:[\w&=%.])*)?(?:#(?:[\w.])*)?)?'),
            'btc_address': re.compile(r'\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b|\bbc1[a-z0-9]{39,59}\b'),
            'credit_card': re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b'),
            'date': re.compile(r'\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}[/-]\d{1,2}[/-]\d{1,2})\b'),
        }

        # Check MLX availability
        self.mlx_available = self._check_mlx()

    def _check_mlx(self) -> bool:
        """Check if MLX is available."""
        try:
            import mlx.core as mx
            logger.info(f"MLX available on device: {mx.default_device()}")
            return True
        except ImportError:
            logger.warning("MLX not available - falling back to CPU processing")
            return False

    def _estimate_optimal_chunk_size(self, available_ram_gb: float = 5.5) -> int:
        """
        Estimate optimal chunk size based on available RAM.

        M1 8GB optimization: Target < 5.5GB to leave room for system
        """
        # Rough estimate: 1 token ≈ 4 bytes for embeddings
        # We need 2x for processing overhead
        safe_tokens = int((available_ram_gb * 0.25 * 1024 * 1024 * 1024) / 4 / 2)
        return min(self.chunk_size, safe_tokens)

    def chunk_text(self, text: str, source: str = "unknown") -> List[Dict]:
        """
        Split text into overlapping chunks with metadata.

        Args:
            text: Large text to chunk
            source: Source identifier (filename, URL, etc.)

        Returns:
            List of chunks with metadata
        """
        chunks = []
        effective_chunk_size = self._estimate_optimal_chunk_size()
        step = effective_chunk_size - self.overlap

        start = 0
        chunk_id = 0

        while start < len(text):
            end = min(start + effective_chunk_size, len(text))

            # Extend to word boundary
            if end < len(text):
                while end > start and text[end] not in ' \n\t':
                    end -= 1

            chunk_text = text[start:end].strip()
            if len(chunk_text) > 100:  # Minimum chunk size
                chunks.append({
                    'id': chunk_id,
                    'text': chunk_text,
                    'source': source,
                    'start_pos': start,
                    'end_pos': end,
                    'token_estimate': len(chunk_text) // 4,  # Rough estimate
                    'overlap_with_previous': self.overlap if chunk_id > 0 else 0
                })
                chunk_id += 1

            start += step

        return chunks

    def extract_entities(self, text: str, source: str = "unknown", chunk_id: int = 0) -> List[EntityMention]:
        """
        Extract entities from text using pattern matching.

        Args:
            text: Text to analyze
            source: Source document
            chunk_id: Chunk identifier

        Returns:
            List of extracted entities
        """
        entities = []

        for entity_type, pattern in self.patterns.items():
            for match in pattern.finditer(text):
                entity = EntityMention(
                    text=match.group(),
                    entity_type=entity_type,
                    start_pos=match.start() + chunk_id * (self.chunk_size - self.overlap),
                    end_pos=match.end() + chunk_id * (self.chunk_size - self.overlap),
                    confidence=1.0,
                    context=text[max(0, match.start() - 50):min(len(text), match.end() + 50)]
                )
                entities.append(entity)

        return entities

    def compute_embeddings_mlx(self, chunks: List[str]) -> Optional[mx.array]:
        """
        Compute MLX embeddings for chunks.

        Args:
            chunks: List of text chunks

        Returns:
            MLX array of embeddings or None if MLX unavailable
        """
        if not self.mlx_available or not chunks:
            return None

        try:
            # Simple embedding: mean of token IDs (placeholder for real embeddings)
            # In production, use ModernBERT embeddings via dedicated embedder
            embeddings = []

            for chunk in chunks:
                # Convert to token IDs (simplified)
                tokens = [ord(c) % 256 for c in chunk[:1024]]
                tokens_mx = mx.array(tokens, dtype=mx.float32)

                # Normalize
                if len(tokens) > 0:
                    embedding = mx.mean(tokens_mx) / 255.0
                    embeddings.append(embedding)
                else:
                    embeddings.append(mx.array(0.0))

            return mx.stack(embeddings)

        except Exception as e:
            logger.error(f"MLX embedding computation failed: {e}")
            return None

    def find_similar_chunks_mlx(self, query: str, top_k: int = 5) -> List[Tuple[int, float]]:
        """
        Find most similar chunks to query using MLX.

        Args:
            query: Search query
            top_k: Number of results to return

        Returns:
            List of (chunk_index, similarity_score) tuples
        """
        if self.chunk_embeddings is None or not self.chunk_texts:
            return []

        try:
            # Compute query embedding
            query_tokens = [ord(c) % 256 for c in query[:1024]]
            query_mx = mx.array(query_tokens, dtype=mx.float32)
            query_embedding = mx.mean(query_mx) / 255.0

            # Compute similarities
            similarities = mx.abs(self.chunk_embeddings - query_embedding)
            similarities_eval = mx.eval(similarities)

            # Get top-k (lowest distance = highest similarity)
            # For simplicity, return all sorted
            indices = mx.argsort(similarities_eval)[:top_k]

            results = []
            for idx in indices:
                idx_int = int(idx.item())
                sim_score = float(1.0 - similarities_eval[idx_int].item())
                results.append((idx_int, sim_score))

            return results

        except Exception as e:
            logger.error(f"MLX similarity search failed: {e}")
            return []

    def cross_reference_entities(self, all_entities: List[EntityMention]) -> List[CrossDocumentLink]:
        """
        Find entities that appear across multiple documents.

        Args:
            all_entities: All entities extracted from all documents

        Returns:
            List of cross-document links
        """
        # Group by value
        by_value: Dict[Tuple[str, str], List[EntityMention]] = {}

        for entity in all_entities:
            key = (entity.entity_type, entity.text.lower())
            if key not in by_value:
                by_value[key] = []
            by_value[key].append(entity)

        # Find cross-document links
        links = []
        for (entity_type, value), mentions in by_value.items():
            sources = list(set(m.context[:50] for m in mentions))  # Unique sources

            if len(sources) > 1:
                link = CrossDocumentLink(
                    entity_type=entity_type,
                    value=value,
                    documents=sources[:10],  # Limit to 10
                    confidence=min(1.0, len(mentions) / 10),
                    first_seen="unknown",
                    last_seen="unknown"
                )
                links.append(link)

        # Sort by confidence
        links.sort(key=lambda x: x.confidence, reverse=True)
        return links

    def reconstruct_timeline(self, entities: List[EntityMention], chunks: List[Dict]) -> List[TimelineEvent]:
        """
        Reconstruct timeline from temporal entities.

        Args:
            entities: Extracted entities
            chunks: Document chunks

        Returns:
            List of timeline events
        """
        timeline = []
        date_entities = [e for e in entities if e.entity_type == 'date']

        for date_entity in date_entities:
            try:
                # Parse date (simplified)
                date_str = date_entity.text
                # Look for surrounding context
                context = date_entity.context

                # Extract event description (text around date)
                event_desc = context.replace(date_str, '[DATE]').strip()

                event = TimelineEvent(
                    date=None,  # Would need proper date parsing
                    description=event_desc[:200],
                    source_document=date_entity.context[:50],
                    entities_involved=[date_entity.text],
                    confidence=date_entity.confidence
                )
                timeline.append(event)

            except Exception as e:
                logger.debug(f"Failed to parse date {date_entity.text}: {e}")

        # Sort by position in document (proxy for time)
        timeline.sort(key=lambda x: x.confidence, reverse=True)
        return timeline[:100]  # Limit results

    def analyze_massive_dump(
        self,
        text: str,
        source: str = "unknown",
        extract_entities: bool = True,
        build_timeline: bool = True,
        cross_reference: bool = True
    ) -> LongContextAnalysis:
        """
        Analyze massive text dump using MLX acceleration.

        Args:
            text: Large text to analyze (can be millions of tokens)
            source: Source identifier
            extract_entities: Whether to extract entities
            build_timeline: Whether to build timeline
            cross_reference: Whether to cross-reference entities

        Returns:
            LongContextAnalysis with all findings
        """
        import time
        start_time = time.time()

        # Step 1: Chunk text
        chunks = self.chunk_text(text, source)
        self.chunk_texts = [c['text'] for c in chunks]

        logger.info(f"Split text into {len(chunks)} chunks (size: {self.chunk_size}, overlap: {self.overlap})")

        # Step 2: Compute embeddings if MLX available
        if self.mlx_available:
            logger.info("Computing MLX embeddings...")
            self.chunk_embeddings = self.compute_embeddings_mlx(self.chunk_texts)

        # Step 3: Extract entities from each chunk
        all_entities = []
        if extract_entities:
            logger.info("Extracting entities...")
            for chunk in chunks:
                entities = self.extract_entities(
                    chunk['text'],
                    chunk['source'],
                    chunk['id']
                )
                all_entities.extend(entities)

        # Step 4: Cross-reference entities
        cross_links = []
        if cross_reference and all_entities:
            logger.info("Cross-referencing entities...")
            cross_links = self.cross_reference_entities(all_entities)

        # Step 5: Build timeline
        timeline = []
        if build_timeline:
            logger.info("Building timeline...")
            timeline = self.reconstruct_timeline(all_entities, chunks)

        # Step 6: Generate summary
        key_findings = []
        if all_entities:
            entity_types = {}
            for e in all_entities:
                entity_types[e.entity_type] = entity_types.get(e.entity_type, 0) + 1

            for etype, count in sorted(entity_types.items(), key=lambda x: -x[1])[:10]:
                key_findings.append(f"Found {count} {etype} entities")

        if cross_links:
            key_findings.append(f"{len(cross_links)} cross-document entity links identified")

        processing_time = time.time() - start_time

        # Estimate memory usage
        memory_usage = len(text) / (1024 * 1024)  # Rough estimate in MB
        if self.chunk_embeddings is not None:
            memory_usage += self.chunk_embeddings.size * 4 / (1024 * 1024)  # float32 = 4 bytes

        return LongContextAnalysis(
            total_chunks=len(chunks),
            total_tokens=len(text) // 4,  # Rough estimate
            entities=all_entities,
            cross_document_links=cross_links,
            timeline=timeline,
            summary=f"Analyzed {len(chunks)} chunks, found {len(all_entities)} entities",
            key_findings=key_findings,
            memory_usage_mb=memory_usage,
            processing_time_seconds=processing_time
        )

    def analyze_multiple_dumps(
        self,
        dumps: Dict[str, str],
        cross_correlate: bool = True
    ) -> Dict[str, LongContextAnalysis]:
        """
        Analyze multiple document dumps and optionally cross-correlate.

        Args:
            dumps: Dict of {source_name: text_content}
            cross_correlate: Whether to find links between dumps

        Returns:
            Dict of analyses per dump
        """
        results = {}
        all_entities = []

        for source, text in dumps.items():
            logger.info(f"Analyzing dump from {source}...")
            analysis = self.analyze_massive_dump(text, source)
            results[source] = analysis
            all_entities.extend(analysis.entities)

        if cross_correlate:
            logger.info("Cross-correlating all dumps...")
            global_links = self.cross_reference_entities(all_entities)

            # Add global links to each result
            for source in results:
                # Filter links relevant to this source
                source_links = [
                    link for link in global_links
                    if any(source in doc for doc in link.documents)
                ]
                # Create new analysis with updated links
                analysis = results[source]
                results[source] = LongContextAnalysis(
                    total_chunks=analysis.total_chunks,
                    total_tokens=analysis.total_tokens,
                    entities=analysis.entities,
                    cross_document_links=source_links,
                    timeline=analysis.timeline,
                    summary=analysis.summary,
                    key_findings=analysis.key_findings + [f"Linked to {len(source_links)} other sources"],
                    memory_usage_mb=analysis.memory_usage_mb,
                    processing_time_seconds=analysis.processing_time_seconds
                )

        return results

    def search_across_dumps(self, query: str, dumps: Dict[str, str], top_k_per_dump: int = 3) -> Dict[str, List[Dict]]:
        """
        Search for query across multiple dumps using MLX similarity.

        Args:
            query: Search query
            dumps: Dict of {source_name: text_content}
            top_k_per_dump: Number of results per dump

        Returns:
            Dict of search results per dump
        """
        results = {}

        for source, text in dumps.items():
            # Analyze this dump
            analysis = self.analyze_massive_dump(text, source)

            # Search for similar chunks
            similar = self.find_similar_chunks_mlx(query, top_k_per_dump)

            source_results = []
            for idx, score in similar:
                if idx < len(self.chunk_texts):
                    source_results.append({
                        'chunk_id': idx,
                        'text': self.chunk_texts[idx][:500],
                        'similarity': score
                    })

            results[source] = source_results

        return results


# Export
__all__ = [
    "DocumentIntelligenceEngine",
    "PDFAnalyzer",
    "OfficeDocumentAnalyzer",
    "ImageAnalyzer",
    "DocumentAnalysis",
    "DocumentMetadata",
    "EXIFData",
    "GeoLocation",
    "EmbeddedObject",
    "DocumentType",
    "MLXLongContextAnalyzer",
    "LongContextAnalysis",
    "EntityMention",
    "CrossDocumentLink",
    "TimelineEvent"
]
