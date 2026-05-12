"""
Evidence Triage Coordinator — Sprint F202I.

Extracts bounded triage facets from PDF/image artifacts discovered in sprint runs.
Facets: title/author, EXIF/GPS, OCR snippets, file hashes, embedded URL/domain hits.

No VLM by default. Model load/unload only via brain/model_lifecycle.py.
OCR/metadata extraction timeout + fail-soft throughout.

Integration:
    from multimodal.evidence_triage import EvidenceTriageCoordinator

    coordinator = EvidenceTriageCoordinator(governor=None)
    await coordinator.initialize()
    facets = await coordinator.extract_triage_facets(file_path, source_type)
    await coordinator.close()
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from hledac.universal.tools.ocr_engine import VisionOCR, recognize_async

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_URL_HITS: int = 20
"""Max embedded URLs/domains extracted from OCR text."""

MAX_OCR_SNIPPETS: int = 10
"""Max OCR text snippets stored in facets."""

MAX_OCR_CHARS: int = 5000
"""Max total OCR characters per file."""

METADATA_TIMEOUT_S: float = 30.0
"""Timeout for metadata extraction per file."""

OCR_TIMEOUT_S: float = 30.0
"""Timeout for OCR per file."""

MAX_FILE_SIZE_FOR_TRIAGE: int = 100 * 1024 * 1024
"""Max file size (100MB) for triage processing."""


# ── URL/Domain extraction ─────────────────────────────────────────────────────

_URL_RE = re.compile(
    r"https?://[^\s<>\"]+", re.IGNORECASE
)
_DOMAIN_RE = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+"
    r"(?:com|org|net|io|co|gov|edu|mil|int|app|dev|xyz|info|biz|"
    r"[a-zA-Z]{2,})\b",
    re.IGNORECASE,
)
"""Matches URLs and domain names in OCR text."""


def _extract_urls_and_domains(text: str) -> tuple[list[str], list[str]]:
    """
    Extract URLs and domain names from text.

    Returns:
        (urls, domains) — bounded at MAX_URL_HITS each.
    """
    if not text:
        return [], []

    urls = list(set(_URL_RE.findall(text)))[:MAX_URL_HITS]
    # Filter out URLs from the domain list to avoid duplication
    url_domains = set()
    for u in urls:
        try:
            from urllib.parse import urlparse
            url_domains.add(urlparse(u).netloc.lower())
        except Exception:
            pass

    all_domains = _DOMAIN_RE.findall(text)
    unique_domains = [d.lower() for d in set(all_domains) if d.lower() not in url_domains]
    domains = unique_domains[:MAX_URL_HITS]

    return urls, domains


# ── Triage Facets ────────────────────────────────────────────────────────────

@dataclass
class TriageFacets:
    """
    Bounded triage facets extracted from a document/image artifact.

    Facets:
        title:       Document title (PDF metadata or filename)
        author:      Document author (PDF metadata)
        exif:        EXIF data dict (camera make/model, settings)
        gps:         GPS coordinates dict (lat/lon/altitude)
        ocr_snippets: List of OCR text snippets (max 10)
        file_hashes: File content hashes (md5, sha256)
        embedded_urls: Embedded URLs found in OCR text
        embedded_domains: Domain names found in OCR text
        metadata:    FOCA metadata dict (pptx, email, cad extended data)
        triage_complete: Whether triage finished successfully

    Fail-safe: all fields have safe defaults. Never raises.
    Bounded: collections capped at defined limits.
    """
    title: Optional[str] = None
    author: Optional[str] = None
    exif: dict[str, Any] = field(default_factory=dict)
    gps: dict[str, Any] = field(default_factory=dict)
    ocr_snippets: list[str] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)
    embedded_urls: list[str] = field(default_factory=list)
    embedded_domains: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    triage_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for evidence envelope."""
        return {
            "title": self.title,
            "author": self.author,
            "exif": self.exif,
            "gps": self.gps,
            "ocr_snippets": self.ocr_snippets,
            "file_hashes": self.file_hashes,
            "embedded_urls": self.embedded_urls,
            "embedded_domains": self.embedded_domains,
            "metadata": self.metadata,
            "triage_complete": self.triage_complete,
        }


# ── Evidence Triage Coordinator ───────────────────────────────────────────────

class EvidenceTriageCoordinator:
    """
    Coordinates triage extraction from PDF/image artifacts.

    Orchestrates:
    - Metadata extraction (forensic metadata: title, author, EXIF, GPS)
    - OCR text extraction (via VisionOCR / macOS Vision)
    - URL/domain hit detection in OCR text

    Fail-safe: all methods return empty/partial facets on failure — never raises.
    Bounded: explicit max on all collections, timeouts on all async ops.

    Usage:
        coordinator = EvidenceTriageCoordinator(governor=None)
        await coordinator.initialize()
        facets = await coordinator.extract_triage_facets(file_path, source_type)
        await coordinator.close()
    """

    def __init__(self, governor: Any | None = None):
        """
        Initialize coordinator.

        Args:
            governor: Optional ResourceGovernor for RAM checks.
        """
        self._governor = governor
        self._initialized = False
        self._lock = asyncio.Lock()
        self._metadata_extractor: Optional[Any] = None
        self._ocr = VisionOCR()

    async def initialize(self) -> None:
        """Lazily initialize metadata extractor on first use."""
        async with self._lock:
            if self._initialized:
                return
            try:
                from hledac.universal.forensics.metadata_extractor import (
                    create_metadata_extractor,
                )
                self._metadata_extractor = create_metadata_extractor()
                await self._metadata_extractor.initialize()
            except Exception as e:
                logger.debug("[EvidenceTriage] Metadata extractor unavailable: %s", e)
                self._metadata_extractor = None
            self._initialized = True

    async def close(self) -> None:
        """Cleanup resources."""
        async with self._lock:
            if self._metadata_extractor is not None:
                try:
                    await self._metadata_extractor.close()
                except Exception:
                    pass
                self._metadata_extractor = None
            self._initialized = False

    def _check_ram_guard(self) -> bool:
        """Check if RAM permits triage processing."""
        try:
            if self._governor is None:
                return True
            if hasattr(self._governor, "is_critical") and self._governor.is_critical():
                return False
            if hasattr(self._governor, "is_emergency") and self._governor.is_emergency():
                return False
            return True
        except Exception:
            return True  # Fail-open

    async def extract_triage_facets(
        self,
        file_path: str,
        source_type: str,
    ) -> TriageFacets:
        """
        Extract triage facets from a document/image artifact.

        Args:
            file_path:  Path to the artifact file.
            source_type: Source type of the finding (e.g., "document").

        Returns:
            TriageFacets with extracted metadata, OCR text, and URL/domain hits.
            Partial facets returned on any failure — never raises.
        """
        if not self._initialized:
            await self.initialize()

        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return TriageFacets()

        # Size guard
        try:
            file_size = path.stat().st_size
            if file_size > MAX_FILE_SIZE_FOR_TRIAGE:
                logger.debug(
                    "[EvidenceTriage] File too large for triage: %s (%d bytes)",
                    file_path, file_size,
                )
                return TriageFacets()
        except Exception:
            return TriageFacets()

        # RAM guard
        if not self._check_ram_guard():
            logger.debug("[EvidenceTriage] RAM guard denied for: %s", file_path)
            return TriageFacets()

        facets = TriageFacets()

        # Run metadata and OCR extraction concurrently with timeout
        try:
            metadata_task = asyncio.create_task(
                self._extract_metadata(path)
            )
            ocr_task = asyncio.create_task(
                self._extract_ocr_with_timeout(path)
            )

            results = await asyncio.wait_for(
                asyncio.gather(
                    metadata_task, ocr_task,
                    return_exceptions=True,
                ),
                timeout=METADATA_TIMEOUT_S + OCR_TIMEOUT_S,
            )
            md_result, ocr_text = results

            if md_result and not isinstance(md_result, BaseException):
                self._apply_metadata_to_facets(md_result, path, facets)

            if ocr_text and not isinstance(ocr_text, BaseException):
                self._apply_ocr_to_facets(ocr_text, facets)

            facets.triage_complete = True

        except asyncio.CancelledError:
            logger.debug("[EvidenceTriage] Triage cancelled for: %s", file_path)
        except Exception as e:
            logger.debug("[EvidenceTriage] Triage failed for %s: %s", file_path, e)

        return facets

    async def _extract_metadata(self, path: Path) -> Optional[Any]:
        """Extract forensic metadata with timeout."""
        if self._metadata_extractor is None:
            return None
        try:
            return await asyncio.wait_for(
                self._metadata_extractor.extract(str(path)),
                timeout=METADATA_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.debug("[EvidenceTriage] Metadata extraction timeout: %s", path)
            return None
        except Exception as e:
            logger.debug("[EvidenceTriage] Metadata extraction error: %s", e)
            return None

    async def _extract_ocr_with_timeout(self, path: Path) -> str:
        """Extract OCR text with timeout."""
        try:
            text = await asyncio.wait_for(
                self._run_ocr(path),
                timeout=OCR_TIMEOUT_S,
            )
            return text
        except asyncio.TimeoutError:
            logger.debug("[EvidenceTriage] OCR timeout: %s", path)
            return ""
        except Exception as e:
            logger.debug("[EvidenceTriage] OCR error: %s", e)
            return ""

    async def _run_ocr(self, path: Path) -> str:
        """Run OCR on an image file or page."""
        ext = path.suffix.lower()
        if ext == ".pdf":
            # For PDFs, extract first page as image for OCR
            return await self._ocr_pdf_page(path)
        elif ext in {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp"}:
            return await self._ocr_image(path)
        return ""

    async def _ocr_image(self, path: Path) -> str:
        """Run OCR on an image file."""
        try:
            snippets = await recognize_async(str(path))
            return "\n".join(snippets[:MAX_OCR_SNIPPETS])
        except Exception as e:
            logger.debug("[EvidenceTriage] Image OCR failed: %s", e)
            return ""

    async def _ocr_pdf_page(self, path: Path) -> str:
        """Extract text from first PDF page via PyPDF2 for OCR."""
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(str(path))
            if not reader.pages:
                return ""
            first_page = reader.pages[0]
            text = first_page.extract_text() or ""
            # If PDF has extractable text, return it directly
            if text.strip():
                return text[:MAX_OCR_CHARS]
            # Otherwise fall back to image OCR on first page render
            # (For now, return the extracted text - image rendering would require
            # additional libraries like pdf2image which are optional)
            return text[:MAX_OCR_CHARS]
        except ImportError:
            logger.debug("[EvidenceTriage] PyPDF2 not available for PDF OCR")
            return ""
        except Exception as e:
            logger.debug("[EvidenceTriage] PDF OCR failed: %s", e)
            return ""

    def _apply_metadata_to_facets(
        self,
        metadata_result: Any,
        path: Path,
        facets: TriageFacets,
    ) -> None:
        """Apply metadata extraction results to facets."""
        try:
            # Generic: file hashes, file info
            if metadata_result.generic:
                gm = metadata_result.generic
                hashes = {}
                if gm.md5_hash:
                    hashes["md5"] = gm.md5_hash
                if gm.sha256_hash:
                    hashes["sha256"] = gm.sha256_hash
                if gm.sha1_hash:
                    hashes["sha1"] = gm.sha1_hash
                facets.file_hashes = hashes

            # PDF metadata: title, author
            if metadata_result.pdf:
                pm = metadata_result.pdf
                facets.title = pm.title or None
                facets.author = pm.author or None

            # Image metadata: EXIF, GPS
            if metadata_result.image:
                im = metadata_result.image
                exif_dict = {}
                if im.camera_make:
                    exif_dict["camera_make"] = im.camera_make
                if im.camera_model:
                    exif_dict["camera_model"] = im.camera_model
                if im.lens:
                    exif_dict["lens"] = im.lens
                if im.focal_length:
                    exif_dict["focal_length"] = im.focal_length
                if im.f_number:
                    exif_dict["f_number"] = im.f_number
                if im.iso:
                    exif_dict["iso"] = im.iso
                if im.exposure_time:
                    exif_dict["exposure_time"] = im.exposure_time
                facets.exif = exif_dict

                # GPS coordinates
                if im.gps:
                    facets.gps = {
                        "latitude": im.gps.latitude if hasattr(im.gps, "latitude") else None,
                        "longitude": im.gps.longitude if hasattr(im.gps, "longitude") else None,
                        "altitude": im.gps.altitude if hasattr(im.gps, "altitude") else None,
                    }

            # PPTX/ODP presentation metadata: author, company, template
            if metadata_result.pptx:
                pm = metadata_result.pptx
                if pm.author and not facets.author:
                    facets.author = pm.author
                if pm.company:
                    facets.metadata["company"] = pm.company
                if pm.template_path:
                    facets.metadata["template_path"] = pm.template_path
                if pm.slide_count is not None:
                    facets.metadata["slide_count"] = pm.slide_count
                if pm.speaker_notes:
                    facets.metadata["speaker_notes"] = pm.speaker_notes[:3]
                if pm.hidden_slides:
                    facets.metadata["hidden_slides_count"] = len(pm.hidden_slides)
                if pm.has_macros is not None:
                    facets.metadata["has_macros"] = pm.has_macros

            # Email metadata: from_addr, message_id_domain, originating_ip
            if metadata_result.email:
                em = metadata_result.email
                if em.from_addr:
                    facets.metadata["from_addr"] = em.from_addr
                if em.reply_to:
                    facets.metadata["reply_to"] = em.reply_to
                if em.message_id_domain:
                    facets.metadata["message_id_domain"] = em.message_id_domain
                if em.originating_ip:
                    facets.metadata["originating_ip"] = em.originating_ip
                if em.received_chain:
                    facets.metadata["received_chain"] = em.received_chain[:3]
                if em.has_attachments:
                    facets.metadata["attachment_count"] = em.attachment_count

            # CAD metadata: author, coordinate system
            if metadata_result.cad:
                cm = metadata_result.cad
                if cm.author and not facets.author:
                    facets.author = cm.author
                if cm.title:
                    facets.title = cm.title
                if cm.autocad_version:
                    facets.metadata["cad_version"] = cm.autocad_version
                if cm.viewBox:
                    facets.metadata["viewbox"] = cm.viewBox
                if cm.width and cm.height:
                    facets.metadata["dimensions"] = f"{cm.width}x{cm.height}"

            # Fallback: use filename as title if no title found
            if facets.title is None:
                facets.title = path.name

        except Exception as e:
            logger.debug("[EvidenceTriage] Failed to apply metadata: %s", e)

    def _apply_ocr_to_facets(self, ocr_text: str, facets: TriageFacets) -> None:
        """Apply OCR text results to facets (URLs, domains, snippets)."""
        try:
            # Cap OCR text
            text = ocr_text[:MAX_OCR_CHARS]

            # Split into snippets
            lines = [l.strip() for l in text.split("\n") if l.strip()]
            facets.ocr_snippets = lines[:MAX_OCR_SNIPPETS]

            # Extract URLs and domains
            urls, domains = _extract_urls_and_domains(text)
            facets.embedded_urls = urls
            facets.embedded_domains = domains

        except Exception as e:
            logger.debug("[EvidenceTriage] Failed to apply OCR: %s", e)


# ── Facade factory ────────────────────────────────────────────────────────────

async def extract_triage_facets(
    file_path: str,
    source_type: str = "document",
    governor: Any | None = None,
) -> TriageFacets:
    """
    Top-level facade for triage facet extraction.

    Args:
        file_path:  Path to the artifact file.
        source_type: Source type of the finding.
        governor: Optional ResourceGovernor.

    Returns:
        TriageFacets with extracted metadata, OCR text, and URL/domain hits.
        Fail-safe: returns partial facets on any error — never raises.
    """
    coordinator = EvidenceTriageCoordinator(governor=governor)
    try:
        await coordinator.initialize()
        facets = await coordinator.extract_triage_facets(file_path, source_type)
        return facets
    finally:
        await coordinator.close()
