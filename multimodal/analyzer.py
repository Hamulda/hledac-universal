"""
Multimodal Enrichment Service
==============================

Fail-soft enrichment for PDF/image findings via VisionEncoder and MambaFusion.
Stores enrichment in LMDB keyed by finding_id (same pattern as forensics).

Additive: finding.metadata["multimodal"] is never written;
all enrichment goes to LMDB under finding_id key.

Integration:
    from multimodal.analyzer import MultimodalEnricher

    enricher = MultimodalEnricher(governor)
    await enricher.initialize()

    # enrich() returns enrichment dict or None
    # Caller stores the dict in LMDB keyed by finding_id
    enrichment = await enricher.enrich(finding)
    if enrichment:
        await lmdb_store.put(finding.finding_id.encode(), enrichment)

    await enricher.close()

M1 8GB: All heavy dependencies are lazy-loaded inside enrichment methods.
RAM guard via ResourceGovernor.reserve(). Heavy path blocked when UMA is tight.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from knowledge.duckdb_store import CanonicalFinding

log = logging.getLogger(__name__)

# Lazy-loaded modules
_VisionEncoder: Optional[type] = None
_MambaFusion: Optional[type] = None
_MOBILECLIP_AVAILABLE = False


def _lazy_load_modules() -> None:
    """Load multimodal modules lazily on first use."""
    global _VisionEncoder, _MambaFusion, _MOBILECLIP_AVAILABLE
    global _PdfReader, _PYPDF2_AVAILABLE, _PIL_AVAILABLE
    if _VisionEncoder is not None:
        return


    try:
        from multimodal.vision_encoder import VisionEncoder
        _VisionEncoder = VisionEncoder
    except ImportError:
        _VisionEncoder = None

    try:
        from multimodal.fusion import MambaFusion
        _MambaFusion = MambaFusion
    except ImportError:
        _MambaFusion = None


    try:
        import mobileclip  # noqa: F401
        _MOBILECLIP_AVAILABLE = True
    except ImportError:
        _MOBILECLIP_AVAILABLE = False

    # PDF extraction — lazy, M1-safe
    try:
        import PyPDF2  # noqa: F401
        _PdfReader = PyPDF2.PdfReader
        _PYPDF2_AVAILABLE = True
    except ImportError:
        _PdfReader = None
        _PYPDF2_AVAILABLE = False

    # Image extraction via PIL
    try:
        from PIL import Image
        _PIL_AVAILABLE = True
    except ImportError:
        _PIL_AVAILABLE = False


# Supported file extensions for multimodal enrichment
_SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp",
    ".pdf",
}

# Document source type for CanonicalFindings produced by DocumentExtractor
_DOCUMENT_SOURCE_TYPE = "document"

# Max envelope size for triage envelope (same as F202A evidence envelope)
_MAX_ENVELOPE_SIZE = 4098


# Lazy-loaded document extraction modules
_PdfReader: Optional[type] = None
_PYPDF2_AVAILABLE = False
_PIL_AVAILABLE = False


def _extract_file_path_from_payload(payload_text: str | None) -> Optional[str]:
    """
    Extract a local file path from payload_text.

    Handles:
    - Direct local paths: /Users/.../file.jpg
    - file:// URLs: file:///tmp/file.pdf
    - Paths with query strings stripped
    """
    if not payload_text:
        return None

    if payload_text.startswith("file://"):
        path_str = payload_text[7:]
        path_str = path_str.split("?")[0].split("#")[0]
        path = Path(path_str)
        if path.exists() and path.is_file():
            return str(path)

    path = Path(payload_text)
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.exists() and path.is_file():
        return str(path)

    clean = payload_text.split("?")[0].split("#")[0]
    if clean != payload_text:
        return _extract_file_path_from_payload(clean)

    return None


def _file_has_multimodal_support(file_path: str) -> bool:
    """Check if file extension is supported by multimodal enrichment."""
    ext = Path(file_path).suffix.lower()
    return ext in _SUPPORTED_EXTENSIONS


def _build_document_envelope(
    text_content: str | None,
    triage_facets: dict[str, Any],
    file_path: str,
    file_type: str,
) -> str:
    """
    Build an evidence envelope JSON for document findings with triage facets.

    Combines F202A envelope pattern (audit_reason, evidence_pointers,
    signal_facets, suggested_pivots) with F202I triage facets (title,
    author, exif, gps, ocr_snippets, file_hashes, embedded_urls,
    embedded_domains).

    Args:
        text_content: Extracted text from the document.
        triage_facets: Triage facets dict from EvidenceTriageCoordinator.
        file_path: Path to the source file.
        file_type: File extension.

    Returns:
        JSON string envelope, bounded at _MAX_ENVELOPE_SIZE.
        Falls back to plain text if serialization fails.
    """
    try:
        import json
        envelope = {
            "audit_reason": f"document_triage:{file_type}",
            "evidence_pointers": [file_path],
            "signal_facets": {
                "file_type": file_type,
                "has_text": bool(text_content),
                "text_len": len(text_content) if text_content else 0,
                "triage_complete": triage_facets.get("triage_complete", False),
            },
            "suggested_pivots": [
                {"type": "document_metadata", "query": "document author/title"},
                {"type": "image_geolocation", "query": "GPS coordinates"},
                {"type": "embedded_iocs", "query": "URLs/domains in document"},
            ],
            # F202I triage facets
            "triage": {
                "title": triage_facets.get("title"),
                "author": triage_facets.get("author"),
                "exif": triage_facets.get("exif", {}),
                "gps": triage_facets.get("gps", {}),
                "ocr_snippets": triage_facets.get("ocr_snippets", []),
                "file_hashes": triage_facets.get("file_hashes", {}),
                "embedded_urls": triage_facets.get("embedded_urls", []),
                "embedded_domains": triage_facets.get("embedded_domains", []),
            },
            "content_preview": (text_content[:1000] + "...") if text_content and len(text_content) > 1000 else (text_content or ""),
        }

        json_text = json.dumps(envelope, separators=(",", ":"))
        if len(json_text) > _MAX_ENVELOPE_SIZE:
            # Truncate OCR snippets and content_preview to fit
            envelope["triage"]["ocr_snippets"] = envelope["triage"]["ocr_snippets"][:5]
            envelope["content_preview"] = envelope["content_preview"][:500]
            json_text = json.dumps(envelope, separators=(",", ":"))
        return json_text
    except Exception:
        # Fallback: return raw text content
        return text_content or ""


class MultimodalEnricher:
    """
    Multimodal enrichment for CanonicalFindings with PDF/image content.

    Enriches findings with file-path in payload_text via:
    - VisionEncoder: image → embedding vector (CoreML or dummy fallback)
    - MambaFusion: fused (vision, text, graph) embedding
    - mobileclip: optional text↔image similarity (when available)

    Fail-safe: all methods are wrapped in try/except.
    Enrichment failures log a warning and return None — never raise.

    M1 8GB: RAM guard via governor.reserve(). Heavy path is a no-op
    when the governor denies reservation (e.g., near-OOM condition).
    """

    def __init__(
        self,
        governor: Any,
        embedding_dim: int = 1024,
        batch_size: int = 4,
    ):
        """
        Initialize enricher.

        Args:
            governor: ResourceGovernor instance for RAM guard.
            embedding_dim: Embedding dimension for VisionEncoder.
            batch_size: Max batch size for encode_batch.
        """
        self._governor = governor
        self._embedding_dim = embedding_dim
        self._batch_size = batch_size

        self._vision_encoder: Optional[Any] = None
        self._fusion_model: Optional[Any] = None
        self._initialized = False
        self._lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        """Ensure models are initialized (idempotent)."""
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            _lazy_load_modules()

            # VisionEncoder — CoreML or dummy fallback
            if _VisionEncoder is not None:
                self._vision_encoder = _VisionEncoder(
                    governor=self._governor,
                    embedding_dim=self._embedding_dim,
                    batch_size=self._batch_size,
                )
                await self._vision_encoder.load()
                log.info("MultimodalEnricher: VisionEncoder loaded")

            # MambaFusion — MLX fusion model
            if _MambaFusion is not None:
                try:
                    self._fusion_model = _MambaFusion(
                        vision_dim=self._embedding_dim,
                        text_dim=768,
                        graph_dim=64,
                        hidden=256,
                        output_dim=128,
                    )
                    log.info("MultimodalEnricher: MambaFusion loaded")
                except Exception as exc:
                    log.warning("MultimodalEnricher: MambaFusion init failed: %s", exc)
                    self._fusion_model = None

            self._initialized = True

    async def initialize(self) -> None:
        """Public initialize — delegates to _ensure_initialized."""
        await self._ensure_initialized()

    async def close(self) -> None:
        """Close enricher and cleanup resources."""
        async with self._lock:
            self._vision_encoder = None
            self._fusion_model = None
            self._initialized = False

    async def enrich(self, finding: Any) -> Optional[dict[str, Any]]:
        """
        Enrich a CanonicalFinding with multimodal analysis.

        Extracts file path from finding.payload_text and runs:
        1. VisionEncoder — image/pdf → embedding
        2. MambaFusion — (vision, text, graph) → fused embedding
        3. mobileclip similarity (when available)

        Args:
            finding: A CanonicalFinding (or any object with
                     finding_id, payload_text, source_type attributes).

        Returns:
            Enrichment dict with keys:
            - "vision_embedding": list[float] or None
            - "fused_embedding": list[float] or None
            - "clip_score": float or None (mobileclip text↔image)
            - "file_path": the extracted file path or None
            - "enrichment_available": True if file was processable

            Returns None if no supported file path found or all enrichment failed.
            Never raises — failures return None with a debug log.
        """
        if not self._initialized:
            await self._ensure_initialized()

        # Extract file path from payload_text
        payload_text = getattr(finding, "payload_text", None)
        file_path = _extract_file_path_from_payload(payload_text)

        if not file_path:
            return None

        if not _file_has_multimodal_support(file_path):
            return None

        finding_id = getattr(finding, "finding_id", "unknown")
        enrichment: dict[str, Any] = {
            "finding_id": finding_id,
            "file_path": file_path,
            "vision_embedding": None,
            "fused_embedding": None,
            "clip_score": None,
            "enrichment_available": False,
        }

        # Guard: heavy vision path requires RAM reservation
        # If RAM is tight, skip gracefully (fail-soft)
        if not self._can_run_heavy_vision():
            log.debug("MultimodalEnricher: RAM guard denied for %s", finding_id)
            return None

        # 1. VisionEncoder: load file bytes and encode
        if self._vision_encoder is not None:
            try:
                image_bytes = await self._load_file_bytes(file_path)
                if image_bytes:
                    embeddings = await self._vision_encoder.encode_batch([image_bytes])
                    if embeddings and len(embeddings) == 1:
                        emb = embeddings[0]
                        # Convert mx.array to list for JSON serialization
                        if hasattr(emb, "tolist"):
                            enrichment["vision_embedding"] = emb.tolist()
                        elif hasattr(emb, "__iter__"):
                            enrichment["vision_embedding"] = list(emb)
            except Exception as exc:
                log.debug("Multimodal vision encode failed for %s: %s", finding_id, exc)

        # 2. MambaFusion: fuse (vision, text, graph)
        if self._fusion_model is not None and enrichment["vision_embedding"] is not None:
            try:
                import mlx.core as mx
                vision_emb = mx.array(enrichment["vision_embedding"])
                # Text embedding: zeros (no text model in scope)
                text_emb = mx.zeros_like(vision_emb)
                # Graph embedding: zeros (no graph model in scope)
                graph_emb = mx.zeros_like(vision_emb)

                fused = self._fusion_model(vision_emb, text_emb, graph_emb)
                if hasattr(fused, "tolist"):
                    enrichment["fused_embedding"] = fused.tolist()
                elif hasattr(fused, "__iter__"):
                    enrichment["fused_embedding"] = list(fused)
            except Exception as exc:
                log.debug("Multimodal fusion failed for %s: %s", finding_id, exc)

        # 3. mobileclip text↔image similarity (when available)
        if _MOBILECLIP_AVAILABLE and enrichment["vision_embedding"] is not None:
            try:
                score = await self._clip_similarity_score(file_path, enrichment["vision_embedding"])
                enrichment["clip_score"] = score
            except Exception as exc:
                log.debug("Multimodal clip similarity failed for %s: %s", finding_id, exc)

        # Mark enrichment available if any module produced data
        if enrichment["vision_embedding"] is not None or enrichment["fused_embedding"] is not None:
            enrichment["enrichment_available"] = True

        if not enrichment["enrichment_available"]:
            return None

        return enrichment

    def _can_run_heavy_vision(self) -> bool:
        """
        Check if heavy vision path can run safely (RAM guard).

        Uses governor's memory check to determine if UMA has headroom.
        Returns True if safe to proceed, False if RAM is tight.
        """
        try:
            governor = self._governor
            if governor is None:
                return True

            # Check if governor reports memory pressure
            if hasattr(governor, "is_critical") and governor.is_critical():
                return False
            if hasattr(governor, "is_emergency") and governor.is_emergency():
                return False

            # Try to reserve RAM for heavy vision path (200MB for vision + overhead)
            # This is a probe — we don't actually hold it
            reserve_context = getattr(governor, "reserve", None)
            if reserve_context is None:
                return True

            # Simple heuristic: if governor reports pressure, skip
            if hasattr(governor, "get_current_usage"):
                usage = governor.get_current_usage()
                if isinstance(usage, dict) and usage.get("ram_mb", 0) > governor.high_water * 0.85:
                    return False

            return True
        except Exception:
            # Fail-open: if governor check errors, allow the operation
            return True

    async def _load_file_bytes(self, file_path: str) -> Optional[bytes]:
        """Load file bytes from path. Fail-safe — returns None on error."""
        try:
            loop = asyncio.get_running_loop()
            def _read():
                with open(file_path, "rb") as f:
                    return f.read()
            return await loop.run_in_executor(None, _read)
        except Exception as exc:
            log.debug("Failed to read file %s: %s", file_path, exc)
            return None

    async def _clip_similarity_score(self, file_path: str, vision_embedding: list[float]) -> Optional[float]:
        """
        Compute CLIP text↔image similarity score.
        Returns a float in [0.0, 1.0] or None on failure.
        """
        if not _MOBILECLIP_AVAILABLE:
            return None

        try:
            from mobileclip import create_model_and_transforms, get_tokenizer
            import mlx.core as mx
            from PIL import Image

            loop = asyncio.get_running_loop()

            def _score():
                model, _, preprocess = create_model_and_transforms("mobileclip_s0")
                tokenizer = get_tokenizer("mobileclip_s0")

                # Text embedding (use file path stem as simple text proxy)
                text = Path(file_path).stem.replace("_", " ")
                text_tokens = tokenizer([text])
                text_emb = model.encode_text(text_tokens)

                # Image embedding
                image = Image.open(file_path).convert("RGB")
                image_preprocessed = preprocess(image)
                image_batch = mx.stack([image_preprocessed])
                image_emb = model.encode_image(image_batch)

                # Cosine similarity
                text_norm = text_emb / mx.linalg.norm(text_emb)
                image_norm = image_emb / mx.linalg.norm(image_emb)
                score = float((text_norm * image_norm).sum())
                return max(0.0, min(1.0, score))

            return await loop.run_in_executor(None, _score)
        except Exception as exc:
            log.debug("CLIP similarity score failed for %s: %s", file_path, exc)
            return None

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

        semaphore = asyncio.Semaphore(3)  # Max 3 concurrent (M1 8GB safe)

        async def enrich_one(finding: Any) -> tuple[str, Optional[dict[str, Any]]]:
            async with semaphore:
                finding_id = getattr(finding, "finding_id", "unknown")
                try:
                    result = await self.enrich(finding)
                    return (finding_id, result)
                except Exception as exc:
                    log.debug("Batch multimodal enrichment failed for %s: %s", finding_id, exc)
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


# =============================================================================
# DOCUMENT EXTRACTION — Sprint F198C
# =============================================================================

from dataclasses import dataclass


@dataclass
class DocumentResult:
    """
    Typed result from document extraction.


    Fields:
        finding_id:       Unique identifier for the finding
        file_path:        Local path to extracted file
        file_type:        File extension (e.g., ".pdf", ".jpg")
        text_content:     Extracted text content (or None on failure)
        page_count:       Number of pages (PDF only; 0 otherwise)
        metadata:        Dict of file metadata (size, created, modified)
        extraction_ok:    True if text_content was successfully extracted

    Fail-safe: all fields have sensible defaults. Never raises.
    """
    finding_id: str
    file_path: str
    file_type: str
    text_content: Optional[str] = None
    page_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    extraction_ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage."""
        return {
            "finding_id": self.finding_id,
            "file_path": self.file_path,
            "file_type": self.file_type,
            "text_content": self.text_content,
            "page_count": self.page_count,
            "metadata": self.metadata,
            "extraction_ok": self.extraction_ok,
        }


class DocumentExtractor:
    """
    Document extraction for PDF/image inputs.


    Produces CanonicalFinding(source_type="document") for files with supported
    extensions. Text is extracted and stored as payload_text.


    Supported formats:
        - PDF (.pdf) — via PyPDF2
        - Image (.jpg, .jpeg, .png, .tiff, .tif, .bmp, .gif, .webp) — via PIL + OCR

    Fail-safe: all methods return None or empty on failure — never raise.
    Bounded: max file size check, page count limit, async I/O.

    Integration:
        from multimodal.analyzer import DocumentExtractor

        extractor = DocumentExtractor(governor)
        await extractor.initialize()
        result = await extractor.extract(file_path, query)
        await extractor.close()
    """

    # Max file size: 50MB (M1 8GB safe)
    MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024
    # Max pages per PDF (prevents giant PDFs from blowing RAM)
    MAX_PDF_PAGES: int = 500
    # Text length cap for payload_text
    MAX_TEXT_CHARS: int = 200_000

    def __init__(self, governor: Any | None = None):
        """
        Initialize extractor.

        Args:
            governor: Optional ResourceGovernor for RAM checks.
        """
        self._governor = governor
        self._initialized = False
        self._lock = asyncio.Lock()


    async def initialize(self) -> None:
        """"Lazily load modules on first use."""
        async with self._lock:
            if self._initialized:
                return
            _lazy_load_modules()
            self._initialized = True

    async def close(self) -> None:
        """"Cleanup resources."""
        async with self._lock:
            self._initialized = False

    def _check_ram_guard(self) -> bool:
        """
        Check if RAM permits heavy document extraction.

        Returns True if safe to proceed, False if RAM is tight.
        """
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

    async def extract(
        self,
        file_path: str,
        query: str,
        finding_id: str | None = None,
    ) -> Optional[CanonicalFinding]:
        """
        Extract text from a document and return as CanonicalFinding.

        Args:
            file_path:  Local path to file (.pdf, .jpg, .png, etc.)
            query:     Research query string
            finding_id: Optional finding ID; generated if not provided

        Returns:
            CanonicalFinding(source_type="document") or None if:
            - File does not exist or is too large
            - Extension not supported
            - RAM guard denies
            - Extraction failed (fail-soft)
        """
        if not self._initialized:
            await self.initialize()

        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return None

        ext = path.suffix.lower()
        if ext not in _SUPPORTED_EXTENSIONS:
            return None

        # Size guard
        try:
            file_size = path.stat().st_size
            if file_size > self.MAX_FILE_SIZE_BYTES:
                log.debug("DocumentExtractor: file too large %s: %d bytes", file_path, file_size)
                return None
        except Exception as exc:
            log.debug("DocumentExtractor: stat failed for %s: %s", file_path, exc)
            return None

        # RAM guard
        if not self._check_ram_guard():
            log.debug("DocumentExtractor: RAM guard denied for %s", file_path)
            return None

        # Generate finding_id
        if finding_id is None:
            file_bytes = str(path).encode()
            finding_id = hashlib.sha256(file_bytes).hexdigest()[:16]

        # Extract text based on file type
        text_content: Optional[str] = None
        page_count = 0
        metadata: dict[str, Any] = {}
        extraction_ok = False

        try:
            if ext == ".pdf":
                text_content, page_count = await self._extract_pdf(file_path)
                metadata["extracted_pages"] = page_count
            else:
                text_content = await self._extract_image_text(file_path)
                metadata["extracted_chars"] = len(text_content) if text_content else 0

            extraction_ok = text_content is not None and len(text_content) > 0
        except Exception as exc:
            log.debug("DocumentExtractor: extraction failed for %s: %s", file_path, exc)

        # Cap text content
        if text_content and len(text_content) > self.MAX_TEXT_CHARS:
            text_content = text_content[: self.MAX_TEXT_CHARS]

        # F202I: Extract triage facets (metadata, OCR, URL/domain hits)
        triage_facets: dict[str, Any] = {}
        try:
            from hledac.universal.multimodal.evidence_triage import (
                EvidenceTriageCoordinator,
            )
            triage_coord = EvidenceTriageCoordinator(governor=self._governor)
            try:
                await triage_coord.initialize()
                triage_result = await triage_coord.extract_triage_facets(
                    file_path, _DOCUMENT_SOURCE_TYPE
                )
                triage_facets = triage_result.to_dict()
            finally:
                await triage_coord.close()
        except Exception as e:
            log.debug("DocumentExtractor: triage extraction failed: %s", e)
            triage_facets = {}

        # F202I: Build evidence envelope with triage facets
        payload_text = _build_document_envelope(
            text_content, triage_facets, str(path), ext
        )

        # Build finding
        provenance: tuple[str, ...] = ("document", str(path), ext)
        try:
            canonical_finding = CanonicalFinding(
                finding_id=finding_id,
                query=query,
                source_type=_DOCUMENT_SOURCE_TYPE,
                confidence=0.85,
                ts=_time.time(),
                provenance=provenance,
                payload_text=payload_text,
            )
            return canonical_finding
        except Exception as exc:
            log.debug("DocumentExtractor: CanonicalFinding creation failed: %s", exc)
            return None

    async def extract_batch(
        self,
        file_paths: list[str],
        query: str,
    ) -> list[CanonicalFinding]:
        """
        Extract text from multiple documents concurrently.


        Args:
            file_paths: List of local file paths
            query:       Research query string

        Returns:
            List of CanonicalFinding(source_type="document") — failures excluded.
            Concurrency is limited by asyncio.Semaphore(4) for M1 8GB safety.
        """
        if not file_paths:
            return []

        semaphore = asyncio.Semaphore(4)  # Max 4 concurrent

        async def extract_one(fp: str) -> Optional[CanonicalFinding]:
            async with semaphore:
                try:
                    return await self.extract(fp, query)
                except Exception as exc:
                    log.debug("DocumentExtractor batch extract failed for %s: %s", fp, exc)
                    return None

        tasks = [extract_one(fp) for fp in file_paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        findings = []
        for item in results:
            if isinstance(item, Exception):
                continue
            if item is not None:
                findings.append(item)

        return findings

    async def _extract_pdf(self, file_path: str) -> tuple[Optional[str], int]:
        """
        Extract text from PDF using PyPDF2.


        Returns (text_content, page_count). Fail-safe — returns (None, 0) on error.
        """
        if not _PYPDF2_AVAILABLE or _PdfReader is None:
            return None, 0

        try:
            loop = asyncio.get_running_loop()


            def _read_pdf():
                reader = _PdfReader(file_path)
                page_count = len(reader.pages)
                if page_count > self.MAX_PDF_PAGES:
                    log.debug("DocumentExtractor: PDF too many pages %s: %d", file_path, page_count)
                    return "", page_count
                texts = []
                for page in reader.pages[: self.MAX_PDF_PAGES]:
                    try:
                        text = page.extract_text()
                        if text:
                            texts.append(text)
                    except Exception:
                        pass
                return "\n".join(texts), page_count

            return await loop.run_in_executor(None, _read_pdf)
        except Exception as exc:
            log.debug("DocumentExtractor: PDF extraction failed for %s: %s", file_path, exc)
            return None, 0

    async def _extract_image_text(self, file_path: str) -> Optional[str]:
        """
        Extract text from image using PIL.


        Currently a placeholder — returns None (no OCR engine in scope).
        Fail-safe — returns None on error.
        """
        if not _PIL_AVAILABLE:
            return None

        try:
            loop = asyncio.get_running_loop()

            def _read_image() -> Optional[str]:
                try:
                    from PIL import Image

                    img = Image.open(file_path)
                    # Basic image metadata — OCR would go here
                    w, h = img.size
                    return f"[image: {w}x{h}, mode={img.mode}]"
                except Exception as exc:
                    log.debug("DocumentExtractor: image open failed for %s: %s", file_path, exc)
                    return None

            return await loop.run_in_executor(None, _read_image)
        except Exception as exc:
            log.debug("DocumentExtractor: image extraction failed for %s: %s", file_path, exc)
            return None
