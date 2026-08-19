"""
Multimodal Enrichment Service
==============================




Fail-soft enrichment for PDF/image findings via VisionEncoder and MambaFusion.
Stores enrichment in LMDB keyed by finding_id (same pattern as forensics).

Additive: finding.metadata["multimodal"] is never written;
all enrichment goes to LMDB under finding_id key.

Integration:
    from hledac.universal.multimodal.analyzer import MultimodalEnricher

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
import asyncio
import hashlib
import logging
import time as _time
from dataclasses import dataclass, field
import msgspec
from compat.msgspec_gc_compat import Struct
from pathlib import Path
from utils._patterns import extract_file_path_from_payload as _extract_file_path_from_payload
from typing import TYPE_CHECKING, Any
from hledac.universal.utils.asyncx import parallel
from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
from _core import aclose
if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
log = logging.getLogger(__name__)
_VisionEncoder: type | None = None
_MambaFusion: type | None = None
_MOBILECLIP_AVAILABLE = False
_MLX_CORE: Any | None = None  # lazy singleton for mlx.core
# F-17 FIX: lazy CLIP model singleton — loaded once, reused across calls
_CLIP_MODEL: Any | None = None  # (model, tokenizer, preprocess) tuple
_CLIP_TOKENIZER: Any | None = None


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


def _lazy_load_modules() -> None:
    """Load multimodal modules lazily on first use."""
    global _VisionEncoder, _MambaFusion, _MOBILECLIP_AVAILABLE
    global _PdfReader, _PYPDF_AVAILABLE, _PIL_AVAILABLE
    if _VisionEncoder is not None:
        return
    try:
        from hledac.universal.multimodal.vision_encoder import VisionEncoder
        _VisionEncoder = VisionEncoder
    except ImportError:
        _VisionEncoder = None
    try:
        from hledac.universal.multimodal.fusion import MambaFusion
        _MambaFusion = MambaFusion
    except ImportError:
        _MambaFusion = None
    try:
        import mobileclip
        _MOBILECLIP_AVAILABLE = True
    except ImportError:
        _MOBILECLIP_AVAILABLE = False
    try:
        import pypdf
        _PdfReader = pypdf.PdfReader
        _PYPDF_AVAILABLE = True
    except ImportError:
        _PdfReader = None
        _PYPDF_AVAILABLE = False
    try:
        from PIL import Image
        _PIL_AVAILABLE = True
    except ImportError:
        _PIL_AVAILABLE = False


_SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.bmp', '.gif', '.webp', '.pdf'}
# [SILICON-02]: Audio/video extensions added for MediaEngine decode + transcription
_AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    '.mp3', '.aac', '.m4a', '.flac', '.wav', '.ogg', '.opus',
    '.wma', '.aiff', '.aif', '.alac', '.ac3', '.amr', '.caf',
})
_VIDEO_EXTENSIONS: frozenset[str] = frozenset({
    '.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.flv',
    '.wmv', '.3gp', '.3g2', '.ts', '.mts', '.m2ts',
})
_MEDIA_EXTENSIONS = _AUDIO_EXTENSIONS | _VIDEO_EXTENSIONS
_DOCUMENT_SOURCE_TYPE = 'document'
_MAX_ENVELOPE_SIZE = 4098
_PdfReader: type | None = None
_PYPDF_AVAILABLE = False
_PIL_AVAILABLE = False
def _file_has_multimodal_support(file_path: str) -> bool:
    """Check if file extension is supported by multimodal enrichment."""
    ext = Path(file_path).suffix.lower()
    return ext in _SUPPORTED_EXTENSIONS or ext in _MEDIA_EXTENSIONS

def _file_is_audio(file_path: str) -> bool:
    """Check if file is an audio file needing MediaDecoder transcription."""
    return Path(file_path).suffix.lower() in _AUDIO_EXTENSIONS

def _file_is_video(file_path: str) -> bool:
    """Check if file is a video file needing MediaDecoder transcription."""
    return Path(file_path).suffix.lower() in _VIDEO_EXTENSIONS

def _build_document_envelope(text_content: str | None, triage_facets: dict[str, Any], file_path: str, file_type: str) -> str:
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
        envelope = {'audit_reason': f'document_triage:{file_type}', 'evidence_pointers': [file_path], 'signal_facets': {'file_type': file_type, 'has_text': bool(text_content), 'text_len': len(text_content) if text_content else 0, 'triage_complete': triage_facets.get('triage_complete', False)}, 'suggested_pivots': [{'type': 'document_metadata', 'query': 'document author/title'}, {'type': 'image_geolocation', 'query': 'GPS coordinates'}, {'type': 'embedded_iocs', 'query': 'URLs/domains in document'}], 'triage': {'title': triage_facets.get('title'), 'author': triage_facets.get('author'), 'exif': triage_facets.get('exif', {}), 'gps': triage_facets.get('gps', {}), 'ocr_snippets': triage_facets.get('ocr_snippets', []), 'file_hashes': triage_facets.get('file_hashes', {}), 'embedded_urls': triage_facets.get('embedded_urls', []), 'embedded_domains': triage_facets.get('embedded_domains', [])}, 'content_preview': text_content[:1000] + '...' if text_content and len(text_content) > 1000 else text_content or ''}
        json_text = json.dumps(envelope)
        if len(json_text) > _MAX_ENVELOPE_SIZE:
            envelope['triage']['ocr_snippets'] = envelope['triage']['ocr_snippets'][:5]
            envelope['content_preview'] = envelope['content_preview'][:500]
            json_text = json.dumps(envelope)
        return json_text
    except Exception:
        return text_content or ''

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
    __slots__ = tuple(('_batch_size', '_embedding_dim', '_fusion_model', '_governor', '_initialized', '_lock', '_vision_encoder', '_pool'))

    def __init__(self, governor: Any, embedding_dim: int=1024, batch_size: int=4, pool: Any=None):
        """
        Initialize enricher.

        Args:
            governor: ResourceGovernor instance for RAM guard.
            embedding_dim: Embedding dimension for VisionEncoder.
            batch_size: Max batch size for encode_batch.
            pool: MLXModelPool instance for model sharing (Issue #32).
        """
        self._governor = governor
        self._embedding_dim = embedding_dim
        self._batch_size = batch_size
        self._vision_encoder: Any | None = None
        self._fusion_model: Any | None = None
        self._initialized = False
        self._lock: asyncio.Lock | None = None
        self._pool = pool

    def _get_lock(self) -> asyncio.Lock:
        """ISSUE-014 FIX: Lazily create lock in the current event loop."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def _ensure_initialized(self) -> None:
        """Ensure models are initialized (idempotent)."""
        if self._initialized:
            return
        async with self._get_lock():
            if self._initialized:
                return
            _lazy_load_modules()
            if _VisionEncoder is not None:
                self._vision_encoder = _VisionEncoder(governor=self._governor, embedding_dim=self._embedding_dim, batch_size=self._batch_size)
                await self._vision_encoder.load()
                log.info('MultimodalEnricher: VisionEncoder loaded')
            if _MambaFusion is not None:
                try:
                    # Issue #32: Pool integration for MambaFusion
                    self._fusion_model = _MambaFusion(pool=self._pool)
                    await self._fusion_model.initialize()
                    log.info('MultimodalEnricher: MambaFusion loaded (pooled)')
                except Exception as exc:
                    log.warning('MultimodalEnricher: MambaFusion init failed: %s', exc)
                    self._fusion_model = None
            self._initialized = True

    async def initialize(self) -> None:
        """Public initialize — delegates to _ensure_initialized."""
        await self._ensure_initialized()

    async def close(self) -> None:
        """Close enricher and cleanup resources."""
        async with self._get_lock():
            if self._fusion_model is not None:
                try:
                    await self._fusion_model.release()
                except Exception:  # noqa: BLE001
                    pass
                self._fusion_model = None
            self._vision_encoder = None
            self._initialized = False

    async def enrich(self, finding: Any) -> dict[str, Any] | None:
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
        file_path = self._extract_file_path(finding)
        if not file_path:
            return None

        if _file_is_audio(file_path):
            return await self.enrich_audio(finding, file_path)
        if _file_is_video(file_path):
            return await self.enrich_video(finding, file_path)

        return await self._enrich_image(finding, file_path)

    def _extract_file_path(self, finding: Any) -> str | None:
        """Extract and validate file path from finding."""
        payload_text = getattr(finding, 'payload_text', None)
        file_path = _extract_file_path_from_payload(payload_text)
        if not file_path or not _file_has_multimodal_support(file_path):
            return None
        return file_path

    async def _enrich_image(self, finding: Any, file_path: str) -> dict[str, Any] | None:
        """Enrich image file with vision embeddings and fusion."""
        finding_id = getattr(finding, 'finding_id', 'unknown')
        enrichment: dict[str, Any] = {'finding_id': finding_id, 'file_path': file_path,
                                      'vision_embedding': None, 'fused_embedding': None,
                                      'clip_score': None, 'enrichment_available': False}
        if not self._can_run_heavy_vision():
            log.debug('MultimodalEnricher: RAM guard denied for %s', finding_id)
            return None

        await self._run_vision_encode(enrichment, file_path, finding_id)
        await self._run_fusion(enrichment, finding_id)
        await self._run_clip_similarity(enrichment, file_path, finding_id)

        if enrichment['vision_embedding'] is not None or enrichment['fused_embedding'] is not None:
            enrichment['enrichment_available'] = True

        return enrichment if enrichment['enrichment_available'] else None

    async def _run_vision_encode(self, enrichment: dict, file_path: str, finding_id: str) -> None:
        """Run vision encoder and update enrichment."""
        if self._vision_encoder is None:
            return
        try:
            image_bytes = await self._load_file_bytes(file_path)
            if not image_bytes:
                return
            embeddings = await self._vision_encoder.encode_batch([image_bytes])
            if embeddings and len(embeddings) == 1:
                emb = embeddings[0]
                enrichment['vision_embedding'] = self._convert_embedding(emb)
        except Exception as exc:
            log.debug('Multimodal vision encode failed for %s: %s', finding_id, exc)

    async def _run_fusion(self, enrichment: dict, finding_id: str) -> None:
        """Run MambaFusion and update enrichment."""
        if self._fusion_model is None or enrichment['vision_embedding'] is None:
            return
        try:
            mx = _get_mx()
            if mx is None:
                raise ImportError("mlx.core unavailable")
            vision_emb = mx.array(enrichment['vision_embedding'])
            text_emb = mx.zeros_like(vision_emb)
            graph_emb = mx.zeros_like(vision_emb)
            fused = self._fusion_model(vision_emb, text_emb, graph_emb)
            enrichment['fused_embedding'] = self._convert_embedding(fused)
        except Exception as exc:
            log.debug('Multimodal fusion failed for %s: %s', finding_id, exc)

    async def _run_clip_similarity(self, enrichment: dict, file_path: str, finding_id: str) -> None:
        """Run CLIP similarity if available."""
        if not _MOBILECLIP_AVAILABLE or enrichment['vision_embedding'] is None:
            return
        try:
            score = await self._clip_similarity_score(file_path, enrichment['vision_embedding'])
            enrichment['clip_score'] = score
        except Exception as exc:
            log.debug('Multimodal clip similarity failed for %s: %s', finding_id, exc)

    def _convert_embedding(self, emb: Any) -> list | None:
        """Convert embedding to list format."""
        try:
            return emb.tolist()
        except AttributeError:
            try:
                return list(emb)
            except Exception:
                return None

    def _can_run_heavy_vision(self) -> bool:
        """
        Check if heavy vision path can run safely (RAM guard + UMA headroom).
        Extends shared check_ram_guard with vision-specific reserve/usage checks.
        Returns True if safe to proceed, False if RAM is tight.
        """
        from hledac.universal.multimodal import check_ram_guard

        governor = self._governor
        if not check_ram_guard(governor):
            return False
        if governor is None:
            return True
        try:
            _ = governor.reserve
        except AttributeError:
            return True
        try:
            usage = governor.get_current_usage()
            if isinstance(usage, dict) and usage.get('ram_mb', 0) > governor.high_water * 0.85:
                return False
        except (AttributeError, TypeError, ValueError):  # noqa: BLE001
            pass
        return True

    async def _load_file_bytes(self, file_path: str) -> bytes | None:
        """Load file bytes from path. Fail-safe — returns None on error."""
        try:

            def _read():
                with open(file_path, 'rb') as f:
                    return f.read()
            return await asyncio.to_thread(_read)
        except Exception as exc:
            log.debug('Failed to read file %s: %s', file_path, exc)
            return None

    async def _get_clip_model(self) -> tuple[Any, Any, Any] | None:
        """
        F-17 FIX: Lazy CLIP model singleton — loaded once, reused across calls.
        Returns (model, tokenizer, preprocess) or None on failure.
        """
        global _CLIP_MODEL, _CLIP_TOKENIZER
        if _CLIP_MODEL is not None:
            return _CLIP_MODEL
        try:
            from mobileclip import create_model_and_transforms, get_tokenizer
            model, _, preprocess = create_model_and_transforms('mobileclip_s0')
            tokenizer = get_tokenizer('mobileclip_s0')
            _CLIP_TOKENIZER = tokenizer
            _CLIP_MODEL = (model, tokenizer, preprocess)
            return _CLIP_MODEL
        except Exception as exc:
            log.debug('CLIP model load failed: %s', exc)
            _CLIP_MODEL = ()  # sentinel — don't retry
            return None

    async def _clip_similarity_score(self, file_path: str, vision_embedding: list[float]) -> float | None:
        """
        Compute CLIP text↔image similarity score.
        Returns a float in [0.0, 1.0] or None on failure.

        F-17 FIXES:
          - Lazy CLIP singleton: model loaded once per process, reused
          - with Image.open(): prevents fd leak
          - Correct semaphore: MULTIMODAL_ENRICHMENT
        """
        if not _MOBILECLIP_AVAILABLE:
            return None
        try:
            mx = _get_mx()
            if mx is None:
                return None
            from PIL import Image

            model_tuple = await self._get_clip_model()
            if model_tuple is None:
                return None
            model, tokenizer, preprocess = model_tuple

            def _score():
                text = Path(file_path).stem.replace('_', ' ')
                text_tokens = tokenizer([text])
                text_emb = model.encode_text(text_tokens)
                with Image.open(file_path) as image:  # F-17: with prevents fd leak
                    image_preprocessed = preprocess(image.convert('RGB'))
                image_batch = mx.stack([image_preprocessed])
                image_emb = model.encode_image(image_batch)
                text_norm = text_emb / mx.linalg.norm(text_emb)
                image_norm = image_emb / mx.linalg.norm(image_emb)
                score = float((text_norm * image_norm).sum())
                return max(0.0, min(1.0, score))
            return await asyncio.to_thread(_score)
        except Exception as exc:
            log.debug('CLIP similarity score failed for %s: %s', file_path, exc)
            return None

    # ── [SILICON-02] Audio/Video enrichment ────────────────────────────────

    async def enrich_audio(self, finding: Any, file_path: str) -> dict[str, Any] | None:
        """
        [SILICON-07] Enrich audio finding via canonical MediaIocPipeline.

        Pipeline:
          1. AVFoundation decode → PCM float32 mono 16kHz (VideoToolbox HW)
          2. SFSpeechRecognizer → text (ANE-accelerated, on-device)
          3. IocStreamScanner → IoC scanning on transcribed text (Rust Aho-Corasick SIMD)
          4. Returns enrichment dict with transcript + iocs + performance metrics

        M1 8GB: Audio buffer capped at 50 MB (~12 min @ 16kHz mono).
                MediaIocPipeline singleton manages lifecycle.
        """
        if not self._check_ram_guard():
            log.debug('[SILICON-02] RAM guard denied audio enrichment for %s', file_path)
            return None

        from hledac.universal.multimodal import get_pipeline

        pipeline = await get_pipeline(self._governor)
        result = await pipeline.process_audio(file_path)

        finding_id = getattr(finding, 'finding_id', 'unknown')

        return {
            'finding_id': finding_id,
            'file_path': file_path,
            'media_type': 'audio',
            'transcript': result.transcript,
            'transcript_confidence': result.transcript_confidence,
            'duration_s': result.duration_s,
            'segments': result.segments,
            'iocs_extracted': result.iocs,
            'ioc_count': result.ioc_count,
            'ioc_scanner': result.ioc_scanner,
            'decode_time_ms': result.decode_time_ms,
            'ioc_scan_time_ms': result.ioc_scan_time_ms,
            'total_time_ms': result.total_time_ms,
            'error': result.error if result.error else None,
            'enrichment_available': True,
        }

    async def enrich_video(self, finding: Any, file_path: str) -> dict[str, Any] | None:
        """
        [SILICON-07] Enrich video finding via canonical MediaIocPipeline.

        Pipeline:
          1. AVFoundation audio track → PCM decode (VideoToolbox HW)
          2. SFSpeechRecognizer → text (ANE)
          3. AVAssetImageGenerator → keyframes at 10s intervals
          4. Vision OCR → text from each keyframe (ANE)
          5. IocStreamScanner → IoC scanning on all combined text (Rust Aho-Corasick SIMD)
          6. Returns enrichment dict with transcript + frame_texts + iocs

        M1 8GB: Keyframes capped at 120 (20 min @ 10s intervals).
                Audio buffer capped at 50 MB. Max 500 MB video file.
        """
        if not self._check_ram_guard():
            log.debug('[SILICON-02] RAM guard denied video enrichment for %s', file_path)
            return None

        from hledac.universal.multimodal import get_pipeline

        pipeline = await get_pipeline(self._governor)
        result = await pipeline.process_video(file_path)

        finding_id = getattr(finding, 'finding_id', 'unknown')

        return {
            'finding_id': finding_id,
            'file_path': file_path,
            'media_type': 'video',
            'audio_transcript': result.transcript,
            'audio_confidence': result.transcript_confidence,
            'duration_s': result.duration_s,
            'frame_texts': result.frame_texts,
            'frame_timestamps': result.frame_timestamps,
            'frame_count': result.frame_count,
            'combined_text': result.all_text[:10000],  # bounded preview
            'iocs_extracted': result.iocs,
            'ioc_count': result.ioc_count,
            'ioc_scanner': result.ioc_scanner,
            'decode_time_ms': result.decode_time_ms,
            'ioc_scan_time_ms': result.ioc_scan_time_ms,
            'total_time_ms': result.total_time_ms,
            'error': result.error if result.error else None,
            'enrichment_available': True,
        }

    # ── Batch enrichment ───────────────────────────────────────────────────

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
        semaphore = get_semaphore(ConcurrencyCategory.MULTIMODAL_ENRICHMENT)

        async def enrich_one(finding: Any) -> tuple[str, dict[str, Any] | None]:
            async with semaphore:
                finding_id = getattr(finding, 'finding_id', 'unknown')
                try:
                    result = await self.enrich(finding)
                    return (finding_id, result)
                except Exception as exc:
                    log.debug('Batch multimodal enrichment failed for %s: %s', finding_id, exc)
                    return (finding_id, None)
        # E2-FIX: parallel replaces safe_gather_ok — semaphore inside enrich_one (MULTIMODAL_ENRICHMENT=4) is the concurrency gate
        results = await parallel(
            [enrich_one(f) for f in findings],
            policy="collect",
            ctx="multimodal_enrichment_batch",
    )
        out = {}
        for item in results.ok:
            if isinstance(item, Exception):
                continue
            fid, enrich_data = item
            if enrich_data is not None:
                out[fid] = enrich_data
        return out

class DocumentResult(Struct):
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
    text_content: str | None = None
    page_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    extraction_ok: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage."""
        return {'finding_id': self.finding_id, 'file_path': self.file_path, 'file_type': self.file_type, 'text_content': self.text_content, 'page_count': self.page_count, 'metadata': self.metadata, 'extraction_ok': self.extraction_ok}

class DocumentExtractor:
    """
    Document extraction for PDF/image inputs.


    Produces CanonicalFinding(source_type="document") for files with supported
    extensions. Text is extracted and stored as payload_text.


    Supported formats:
        - PDF (.pdf) — via pypdf
        - Image (.jpg, .jpeg, .png, .tiff, .tif, .bmp, .gif, .webp) — via PIL + OCR

    Fail-safe: all methods return None or empty on failure — never raise.
    Bounded: max file size check, page count limit, async I/O.

    Integration:
        from hledac.universal.multimodal.analyzer import DocumentExtractor

        extractor = DocumentExtractor(governor)
        await extractor.initialize()
        result = await extractor.extract(file_path, query)
        await extractor.close()
    """
    MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024
    MAX_PDF_PAGES: int = 500
    MAX_TEXT_CHARS: int = 200000
    __slots__ = tuple(('_governor', '_initialized', '_lock'))

    def __init__(self, governor: Any | None=None):
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
        """Check if RAM permits heavy document extraction."""
        from hledac.universal.multimodal import check_ram_guard
        return check_ram_guard(self._governor)

    async def extract(self, file_path: str, query: str, finding_id: str | None=None) -> CanonicalFinding | None:
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
        try:
            file_size = path.stat().st_size
            if file_size > self.MAX_FILE_SIZE_BYTES:
                log.debug('DocumentExtractor: file too large %s: %d bytes', file_path, file_size)
                return None
        except Exception as exc:
            log.debug('DocumentExtractor: stat failed for %s: %s', file_path, exc)
            return None
        if not self._check_ram_guard():
            log.debug('DocumentExtractor: RAM guard denied for %s', file_path)
            return None
        if finding_id is None:
            file_bytes = str(path).encode()
            finding_id = hashlib.sha256(file_bytes).hexdigest()[:16]
        text_content: str | None = None
        page_count = 0
        metadata: dict[str, Any] = {}
        try:
            if ext == '.pdf':
                text_content, page_count = await self._extract_pdf(file_path)
                metadata['extracted_pages'] = page_count
            elif ext in _MEDIA_EXTENSIONS:
                # [SILICON-02]: Audio/video → transcribe via MediaDecoder
                text_content = await self._extract_media_text(file_path)
                metadata['media_type'] = 'audio' if ext in _AUDIO_EXTENSIONS else 'video'
                metadata['extracted_chars'] = len(text_content) if text_content else 0
            else:
                text_content = await self._extract_image_text(file_path)
                metadata['extracted_chars'] = len(text_content) if text_content else 0
            text_content is not None and text_content
        except Exception as exc:
            log.debug('DocumentExtractor: extraction failed for %s: %s', file_path, exc)
        if text_content and len(text_content) > self.MAX_TEXT_CHARS:
            text_content = text_content[:self.MAX_TEXT_CHARS]
        triage_facets: dict[str, Any] = {}
        try:
            from hledac.universal.multimodal.evidence_triage import EvidenceTriageCoordinator
            triage_coord = EvidenceTriageCoordinator(governor=self._governor)
            try:
                await triage_coord.initialize()
                triage_result = await triage_coord.extract_triage_facets(file_path, _DOCUMENT_SOURCE_TYPE)
                triage_facets = triage_result.to_dict()
            finally:
                await triage_coord.close()
        except Exception as e:
            log.debug('DocumentExtractor: triage extraction failed: %s', e)
            triage_facets = {}
        payload_text = _build_document_envelope(text_content, triage_facets, str(path), ext)
        provenance: tuple[str, ...] = ('document', str(path), ext)
        try:
            canonical_finding = CanonicalFinding(finding_id=finding_id, query=query, source_type=_DOCUMENT_SOURCE_TYPE, confidence=0.85, ts=_time.time(), provenance=provenance, payload_text=payload_text)
            return canonical_finding
        except Exception as exc:
            log.debug('DocumentExtractor: CanonicalFinding creation failed: %s', exc)
            return None

    async def extract_batch(self, file_paths: list[str], query: str) -> list[CanonicalFinding]:
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
        semaphore = get_semaphore(ConcurrencyCategory.MULTIMODAL_ENRICHMENT)

        async def extract_one(fp: str) -> CanonicalFinding | None:
            async with semaphore:
                try:
                    return await self.extract(fp, query)
                except Exception as exc:
                    log.debug('DocumentExtractor batch extract failed for %s: %s', fp, exc)
                    return None
        tasks = [extract_one(fp) for fp in file_paths]
        result = await parallel(tasks, policy="collect")
        findings = []
        for item in result.ok:
            if item is not None:
                findings.append(item)
        return findings

    async def _extract_pdf(self, file_path: str) -> tuple[str | None, int]:
        """
        Extract text from PDF using pypdf.


        Returns (text_content, page_count). Fail-safe — returns (None, 0) on error.
        """
        if not _PYPDF_AVAILABLE or _PdfReader is None:
            return (None, 0)
        try:

            def _read_pdf():
                assert _PdfReader is not None  # narrowed by outer check
                reader = _PdfReader(file_path)
                page_count = len(reader.pages)
                if page_count > self.MAX_PDF_PAGES:
                    log.debug('DocumentExtractor: PDF too many pages %s: %d', file_path, page_count)
                    return ('', page_count)
                texts = []
                for page in reader.pages[:self.MAX_PDF_PAGES]:
                    try:
                        text = page.extract_text()
                        if text:
                            texts.append(text)
                    except Exception:  # noqa: BLE001
                        pass
                return ('\n'.join(texts), page_count)
            return await asyncio.to_thread(_read_pdf)
        except Exception as exc:
            log.debug('DocumentExtractor: PDF extraction failed for %s: %s', file_path, exc)
            return (None, 0)

    async def _extract_image_text(self, file_path: str) -> str | None:
        """
        Extract text from image using PIL.


        Currently a placeholder — returns None (no OCR engine in scope).
        Fail-safe — returns None on error.
        """
        if not _PIL_AVAILABLE:
            return None
        try:

            def _read_image() -> str | None:
                try:
                    from PIL import Image
                    with Image.open(file_path) as img:  # F-17: with prevents fd leak
                        w, h = img.size
                        return f'[image: {w}x{h}, mode={img.mode}]'
                except Exception as exc:
                    log.debug('DocumentExtractor: image open failed for %s: %s', file_path, exc)
                    return None
            return await asyncio.to_thread(_read_image)
        except Exception as exc:
            log.debug('DocumentExtractor: image extraction failed for %s: %s', file_path, exc)
            return None

    async def _extract_media_text(self, file_path: str) -> str | None:
        """
        [SILICON-07] Extract text from audio/video via canonical MediaIocPipeline.

        Routes through the MediaIocPipeline singleton (shared with all other
        audio/video callers), ensuring a single MediaDecoder instance per sprint.

        For audio: MediaIocPipeline.process_audio() → transcript.
        For video: MediaIocPipeline.process_video() → combined audio + frame OCR text.

        Returns transcribed text or None on failure.
        Fail-safe — returns None on error.
        """
        from hledac.universal.multimodal import get_pipeline

        pipeline = await get_pipeline(self._governor)
        ext = Path(file_path).suffix.lower()
        try:
            if ext in _VIDEO_EXTENSIONS:
                result = await pipeline.process_video(file_path)
                return result.all_text if result.all_text else None
            else:
                result = await pipeline.process_audio(file_path)
                return result.transcript if result.transcript else None
        except Exception as exc:
            log.debug('DocumentExtractor: media extraction failed for %s: %s', file_path, exc)
            return None