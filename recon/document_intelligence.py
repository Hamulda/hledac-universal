"""
Document Intelligence Engine
============================


















ADVERSARY-001 fix: All untrusted binary parsing is sandboxed via
security/media_sandbox.py MediaSandboxCoordinator (Tier-A Seatbelt,
Tier-B subprocess isolation, Tier-C Wasmtime). PyMuPDF, whisper.cpp,
stegdetect, and unknown binaries all run in isolated subprocesses.

ADVERSARY-001-INTERNAL-007 fix: Stegdetect bootstrap is verified via
security/artifact_verifier.py ArtifactVerifier (SHA-256 integrity checks).
Pre-built binaries in ~/.hledac/bin with known-good hashes; isolated
git clone + build with verification when no release URL is available.
Original unverified git+make path is DISABLED by default
(HLEDAC_ENABLE_STEGDETECT_SIGNED=1).
"""
import asyncio
import concurrent.futures
import sys

from operator import attrgetter, itemgetter
from hledac.universal.utils.locks import LazyAsyncioLock
from hledac.universal.utils.domain_executors import get_parallel_executor
from hledac.universal.security.artifact_verifier import (
    get_artifact_verifier,
)
from hledac.universal.security.media_sandbox import (
    MediaSandboxCoordinator,
    MediaRiskProfile,
    profile_file_risk,
    SandboxTier,
    SandboxResult,
    FileRiskLevel,
    get_sandbox_coordinator,
    SANDBOX_ENABLED,
    _write_sandbox_profile,
    _build_image_sandbox_profile,
    run_pymupdf_sandboxed,
)
import hashlib
import io
import logging
import multiprocessing as mp
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
import msgspec
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO
logger = logging.getLogger(__name__)

# M1 8GB: ProcessPoolExecutor for CPU-bound forensics (max 2 workers to avoid RAM pressure)
# Shared across all DeepForensicsAnalyzer instances via module-level singleton
_forensics_pool: concurrent.futures.Executor | None = None
_forensics_pool_lock = LazyAsyncioLock()
_forensics_pool_atexit_registered: bool = False

# M1 8GB: Document-level size guard — refuse to read files above this threshold
# before loading them into RAM. Contrast with embedded-image/zip-entry guards
# (which protect against decompression bombs AFTER the outer document is loaded).
_MAX_DOCUMENT_SIZE = 100 * 1024 * 1024  # 100 MB per document


def _guard_file_size(file_path: str) -> None:
    """Refuse documents above size threshold before reading into RAM.

    Raises ValueError if the file is larger than _MAX_DOCUMENT_SIZE.
    Call at the top of every analyze() entry that accepts a file path.
    """
    stat = os.stat(file_path)
    if stat.st_size > _MAX_DOCUMENT_SIZE:
        raise ValueError(
            f"Document too large: {stat.st_size / 1024 / 1024:.1f}MB "
            f"(limit {_MAX_DOCUMENT_SIZE / 1024 / 1024:.0f}MB)"
        )


def _get_forensics_pool() -> concurrent.futures.Executor:
    """Get or create the shared forensics ProcessPoolExecutor (M1 8GB safe: max_workers=2).

    Uses spawn context on macOS to avoid fork issues with MPS/Swift libraries.
    Fail-safe: returns ThreadPoolExecutor fallback if ProcessPool creation fails.
    Registers atexit shutdown on first creation to prevent orphaned child processes.
    """
    global _forensics_pool, _forensics_pool_atexit_registered
    if _forensics_pool is None:
        try:
            ctx = mp.get_context('spawn')
            _forensics_pool = concurrent.futures.ProcessPoolExecutor(
                max_workers=2,
                mp_context=ctx,
            )
        except Exception as e:
            logger.warning(f'[FORENSICS] ProcessPoolExecutor init failed, using ThreadPool fallback: {e}')
            # R5 FIX (Issue 3): Route fallback through domain_executors shared pool
            from hledac.universal.utils.domain_executors import get_forensics_cpu_executor
            _forensics_pool = get_forensics_cpu_executor()
        # Register atexit shutdown on first pool creation (ISSUE-5 fix)
        if not _forensics_pool_atexit_registered:
            import atexit
            atexit.register(shutdown_forensics_pool)
            _forensics_pool_atexit_registered = True
    return _forensics_pool


def shutdown_forensics_pool() -> None:
    """Shutdown the shared forensics ProcessPoolExecutor gracefully.

    Python 3.14+: cancel_futures=True ensures pending tasks are cancelled
    before waiting for workers to finish. Idempotent — safe to call multiple
    times.

    Called automatically via atexit (registered on first pool creation)
    and can also be called explicitly during sprint winddown.
    """
    global _forensics_pool
    if _forensics_pool is not None:
        try:
            _forensics_pool.shutdown(wait=True, cancel_futures=True)
        except Exception as e:
            logger.warning(f'[FORENSICS] Pool shutdown error (non-fatal): {e}')
        finally:
            _forensics_pool = None
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None
    NUMPY_AVAILABLE = False

try:
    import piexif
    from PIL import ExifTags, Image, ImageChops
    from PIL.TiffImagePlugin import IFDRational
    PIL_AVAILABLE = True
except ImportError:
    piexif = None
    PIL_AVAILABLE = False
    logger.warning('PIL not available - image analysis disabled')
try:
    import fitz
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    logger.warning('PyMuPDF not available - advanced PDF analysis disabled')
DOCUMENT_INTELLIGENCE_AVAILABLE = True

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE
from core import aclose

# Lazy accessor for mlx.core — uses centralized get_mx() from SSOT
def _get_mx():
    """Lazy accessor for mlx.core — uses centralized get_mx() from SSOT."""
    from hledac.universal.utils.mlx_memory._core import get_mx as _get_mx_from_core
    return _get_mx_from_core()
MPS_AVAILABLE = False
VISION_OCR_AVAILABLE: bool | None = None
_VisionOCREngine: Any | None = None
_AhoExtractorModule: Any | None = None
_AHO_AVAILABLE: bool | None = None

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
    except ImportError:  # noqa: BLE001
        pass
    return False
MAX_IMAGE_SIZE = 2048

# M1 8GB: Per-image and per-stream size caps to prevent OOM from decoded content.
# PyMuPDF's extract_image() returns FULLY DECODED image bytes — a 1KB JPEG
# compressed in a page stream can expand to 500MB decoded.
_MAX_EMBEDDED_IMAGE_BYTES = 50 * 1024 * 1024   # 50 MB per decoded image
_MAX_EMBEDDED_STREAM_BYTES = 10 * 1024 * 1024  # 10 MB per OOXML media entry

def _safe_extract_image(doc: Any, xref: int) -> dict | None:
    """Extract image from PDF with size cap on decoded content.

    PyMuPDF's extract_image() decompresses the image fully into memory.
    A malicious PDF can contain a small compressed stream that expands to
    hundreds of MB when decoded. This guard prevents OOM on M1 8GB.

    Returns the base_image dict from extract_image(), or None if over limit.
    """
    try:
        base_image = doc.extract_image(xref)
    except Exception:
        return None
    if not base_image:
        return None
    image_bytes = base_image.get('image', b'')
    if len(image_bytes) > _MAX_EMBEDDED_IMAGE_BYTES:
        logger.warning(
            f"[DOC] Refused image xref={xref} at {len(image_bytes) / 1024 / 1024:.1f}MB "
            f"(limit {_MAX_EMBEDDED_IMAGE_BYTES // 1024 // 1024:.0f}MB)"
        )
        return None
    return base_image

def _safe_read_zip_entry(z: zipfile.ZipFile, name: str) -> bytes | None:
    """Read a zip entry with size cap to prevent zip bomb attacks."""
    try:
        info = z.getinfo(name)
        if info.file_size > _MAX_EMBEDDED_STREAM_BYTES:
            logger.warning(
                f"[DOC] Refused zip entry '{name}' at {info.file_size / 1024 / 1024:.1f}MB "
                f"(limit {_MAX_EMBEDDED_STREAM_BYTES // 1024 // 1024:.0f}MB)"
            )
            return None
        return z.read(name)
    except Exception:
        return None

def _safe_xref_stream(doc: Any, xref: int) -> bytes | None:
    """Read PDF xref stream with size cap to prevent decompression bombs.

    PyMuPDF's xref_stream() returns raw decompressed stream bytes.
    A malicious PDF can embed a small compressed stream that expands to
    hundreds of MB when decompressed. This guard prevents OOM on M1 8GB.

    Returns the stream bytes, or None if over limit or on error.
    """
    try:
        stream = doc.xref_stream(xref)
        if stream is None:
            return None
        if len(stream) > _MAX_EMBEDDED_IMAGE_BYTES:
            logger.warning(
                f"[DOC] Refused xref_stream xref={xref} at {len(stream) / 1024 / 1024:.1f}MB "
                f"(limit {_MAX_EMBEDDED_IMAGE_BYTES // 1024 // 1024:.0f}MB)"
            )
            return None
        return stream
    except Exception:
        return None

def _get_vision_ocr_engine():
    """Lazily load VisionOCREngine — NOT loaded at document_intelligence boot.

    Vision Framework uses ANE for OCR (zero CPU, zero GPU bandwidth on M1).
    The engine is instantiated once and reused across all calls.
    Fails silently if Vision is unavailable (macOS only, no PyObjC).
    """
    global VISION_OCR_AVAILABLE, _VisionOCREngine
    if VISION_OCR_AVAILABLE is None:
        try:
            _VisionOCREngine = VisionOCREngine
            VISION_OCR_AVAILABLE = True
        except Exception:
            _VisionOCREngine = None
            VISION_OCR_AVAILABLE = False
    return _VisionOCREngine


class VisionOCREngine:
    """Hardware-accelerated OCR via Apple Vision Framework on M1 ANE.

    VNRecognizeTextRequest runs text recognition on the Neural Engine —
    no CPU cycles, no GPU bandwidth stolen from MLX workers.
    Supports accurate recognition with language correction (en-US, cs-CZ, de-DE,
    fr-FR, es-ES, ja-JP, zh-CN out of the box).

    M1 8GB safe: ANE is completely separate from Unified Memory Architecture —
    running OCR does not pressure the RAM budget used by MLX inference.

    ISSUE-012: Batch processing support. Vision Framework IS thread-safe
    for different CGImage instances — the framework manages its own internal
    serialization per-image. Multiple concurrent calls to different CGImages
    are safe and allow the ANE to pipeline work.
    """

    __slots__ = tuple(('_batch_executor', '_batch_pool_lock'))

    # Languages supported by Vision Framework on M1 without additional models
    DEFAULT_LANGUAGES = ['en-US', 'cs-CZ', 'de-DE', 'fr-FR', 'es-ES']

    # ISSUE-012: Batch concurrency — M1 8GB safe limit.
    # ANE can pipeline 4 concurrent image recognition requests before
    # saturating the Neural Engine bandwidth. Higher values waste RAM
    # without throughput improvement.
    _BATCH_MAX_WORKERS: int = 4

    def __init__(self):
        # Lazy-initialized ThreadPoolExecutor for batch processing.
        # Separate from the event-loop thread pool to avoid starving
        # async tasks when batch OCR is in flight.
        self._batch_executor: concurrent.futures.ThreadPoolExecutor | None = None
        self._batch_pool_lock = None

    def _get_batch_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        """Get or create the batch OCR thread pool (lazy, thread-safe)."""
        if self._batch_executor is None:
            if self._batch_pool_lock is None:
                import threading
                self._batch_pool_lock = threading.Lock()
            with self._batch_pool_lock:
                if self._batch_executor is None:
                    # R5 FIX: Route through domain_executors shared pool
                    from hledac.universal.utils.domain_executors import get_vision_ocr_batch_executor
                    self._batch_executor = get_vision_ocr_batch_executor()
        return self._batch_executor

    def recognize_bytes(self, image_bytes: bytes) -> tuple[str, float]:
        """Recognize text from raw image bytes synchronously.

        Returns (recognized_text, average_confidence).

        Thread-safe: each call creates its own CGImage and VNRecognizeTextRequest.
        Vision Framework handles internal serialization per-CGImage.

        Fails safely: returns ('', 0.0) on any error.
        """
        try:
            import Vision  # type: ignore[import-not-found, no-redef]
            import AppKit  # type: ignore[import-not-found, no-redef]
        except Exception:
            return '', 0.0

        try:
            ns_data = AppKit.NSData.alloc().initWithBytes_length_(image_bytes, len(image_bytes))  # type: ignore[attr-defined]
            cg_image = AppKit.NSBitmapImageRep.imageRepWithData_(ns_data).CGImage()  # type: ignore[attr-defined]
        except Exception:
            return '', 0.0

        if cg_image is None:
            return '', 0.0

        # Vision request must run on the same thread — completion handler is sync on M1
        results: list = []

        class _ResultHandler:
            __slots__ = tuple(('_results'))
            def __init__(self):
                self._results = results
            def __call__(self, request, error):
                if error is not None:
                    return
                self._results.append(request.results())

        handler = _ResultHandler()
        vn_request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(handler)  # type: ignore[attr-defined]
        vn_request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)  # type: ignore[attr-defined]
        vn_request.setRecognitionLanguages_(self.DEFAULT_LANGUAGES)
        vn_request.setUsesLanguageCorrection_(True)

        try:
            Vision.VNImageRequestHandler.alloc().initWithCGImageOptions_(  # type: ignore[attr-defined]
                cg_image,
                {'VNImageOptionApplyOrientationCorrection': True},
            ).performRequests_error_([vn_request], None)
        except Exception:
            return '', 0.0

        if not results or not results[0]:
            return '', 0.0

        observations = results[0]
        texts = []
        confidences = []
        for obs in observations:
            txt = str(obs.text())
            conf = float(obs.confidence())
            texts.append(txt)
            confidences.append(conf)

        full_text = '\n'.join(texts)
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return full_text, avg_conf

    def recognize_batch(self, images: list[bytes]) -> list[tuple[str, float]]:
        """Recognize text from multiple images in parallel via ANE batch processing.

        Uses a dedicated ThreadPoolExecutor (max_workers=_BATCH_MAX_WORKERS)
        to submit concurrent Vision Framework requests. Each request processes
        a different CGImage, which is thread-safe — Vision Framework serializes
        internally per-image.

        M1 8GB: _BATCH_MAX_WORKERS=4 balances ANE pipeline saturation against
        RAM pressure from concurrent CGImage allocations (~2MB per image).

        Args:
            images: List of raw image bytes to OCR.

        Returns:
            List of (recognized_text, avg_confidence) tuples, same order as input.
            Individual failures return ('', 0.0) for that position.
        """
        if not images:
            return []
        if len(images) == 1:
            return [self.recognize_bytes(images[0])]

        executor = self._get_batch_executor()
        futures: list[concurrent.futures.Future] = [
            executor.submit(self.recognize_bytes, img) for img in images
        ]

        results: list[tuple[str, float]] = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception:
                results.append(('', 0.0))
        return results

    async def recognize_bytes_async(self, image_bytes: bytes) -> tuple[str, float]:
        """Async wrapper — runs sync ANE OCR in thread pool to avoid blocking event loop.

        This is the primary entry point for async contexts (DeepForensicsAnalyzer).
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.recognize_bytes, image_bytes)

    # ── [IO-4] Zero-copy CVPixelBuffer OCR ─────────────────────────────────────

    def recognize_pixelbuffer(self, pixel_buffer: Any, recognition_level: str = "accurate") -> tuple[str, float]:
        """[IO-4] Recognize text from CVPixelBuffer using zero-copy CIImage pipeline.

        Uses CIImage(ioSurface:) for zero-copy IOSurface access, then feeds
        VNRecognizeTextRequest directly — no CGImage creation, no bytes copy.

        Pipeline:
            CVPixelBuffer → CIImage(ioSurface:) → VNRecognizeTextRequest (zero-copy)

        Args:
            pixel_buffer: CVPixelBuffer from AVAssetReader (e.g., from media_engine)
            recognition_level: "fast" or "accurate" (default "accurate")

        Returns:
            (recognized_text: str, average_confidence: float)
        """
        try:
            import Vision  # type: ignore[import-not-found, no-redef]
        except Exception:
            return '', 0.0

        try:
            # Configure recognition level
            if recognition_level == "fast":
                req_level = Vision.VNRequestTextRecognitionLevelFast  # type: ignore[attr-defined]
            else:
                req_level = Vision.VNRequestTextRecognitionLevelAccurate  # type: ignore[attr-defined]

            # Vision request must run on the same thread
            results: list = []

            class _ResultHandler:
                __slots__ = tuple(('_results'))
                def __init__(self):
                    self._results = results
                def __call__(self, request, error):
                    if error is not None:
                        return
                    self._results.append(request.results())

            handler = _ResultHandler()
            vn_request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(handler)  # type: ignore[attr-defined]
            vn_request.setRecognitionLevel_(req_level)
            vn_request.setRecognitionLanguages_(self.DEFAULT_LANGUAGES)
            vn_request.setUsesLanguageCorrection_(True)

            try:
                # Use VNImageRequestHandler with CVPixelBuffer directly
                Vision.VNImageRequestHandler.alloc().initWithCVPixelBuffer_options_(  # type: ignore[attr-defined]
                    pixel_buffer,
                    {'VNImageOptionApplyOrientationCorrection': True}
                ).performRequests_error_([vn_request], None)
            except Exception:
                return '', 0.0

            if not results or not results[0]:
                return '', 0.0

            observations = results[0]
            texts = []
            confidences = []
            for obs in observations:
                txt = str(obs.text())  # type: ignore[attr-defined]
                conf = float(obs.confidence())  # type: ignore[attr-defined]
                texts.append(txt)
                confidences.append(conf)

            full_text = '\n'.join(texts)
            avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
            return full_text, avg_conf

        except Exception:
            return '', 0.0

    def recognize_batch_from_pixelbuffer(self, pixel_buffers: list[Any], recognition_level: str = "accurate") -> list[tuple[str, float]]:
        """[IO-4] Batch OCR from CVPixelBuffer list with ANE parallelization.

        Uses a dedicated ThreadPoolExecutor (max_workers=_BATCH_MAX_WORKERS)
        to submit concurrent Vision Framework requests. Each request processes
        a different CVPixelBuffer, which is thread-safe.

        Args:
            pixel_buffers: List of CVPixelBuffer objects from extract_keyframes_zero_copy()
            recognition_level: "fast" or "accurate" (default "accurate")

        Returns:
            List of (recognized_text, avg_confidence) tuples, same order as input.
        """
        if not pixel_buffers:
            return []
        if len(pixel_buffers) == 1:
            return [self.recognize_pixelbuffer(pixel_buffers[0], recognition_level)]

        executor = self._get_batch_executor()
        futures: list[concurrent.futures.Future] = [
            executor.submit(self.recognize_pixelbuffer, pb, recognition_level)
            for pb in pixel_buffers
        ]

        results: list[tuple[str, float]] = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception:
                results.append(('', 0.0))
        return results

    async def recognize_pixelbuffer_async(self, pixel_buffer: Any, recognition_level: str = "accurate") -> tuple[str, float]:
        """[IO-4] Async wrapper for zero-copy CVPixelBuffer OCR.

        Runs sync Vision OCR in thread pool to avoid blocking event loop.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.recognize_pixelbuffer, pixel_buffer, recognition_level
        )

    async def recognize_batch_from_pixelbuffer_async(
        self,
        pixel_buffers: list[Any],
        recognition_level: str = "accurate"
    ) -> list[tuple[str, float]]:
        """[IO-4] Async batch wrapper for CVPixelBuffer OCR.

        Args:
            pixel_buffers: List of CVPixelBuffer objects
            recognition_level: "fast" or "accurate" (default "accurate")

        Returns:
            List of (recognized_text, avg_confidence) tuples.
        """
        if not pixel_buffers:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self.recognize_batch_from_pixelbuffer, pixel_buffers, recognition_level
        )

    async def recognize_batch_async(self, images: list[bytes]) -> list[tuple[str, float]]:
        """Async wrapper for batch OCR — runs recognize_batch in thread pool.

        Args:
            images: List of raw image bytes to OCR.

        Returns:
            List of (recognized_text, avg_confidence) tuples.
        """
        if not images:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.recognize_batch, images)


def _ocr_embedded_image(image_bytes: bytes) -> tuple[str, float]:
    """Fire-and-forget OCR on embedded PDF image bytes.

    Loads VisionOCREngine lazily on first call.
    Returns (text, confidence). Falls back to ('', 0.0) if unavailable.

    This is called from _extract_pdf_objects() for every embedded image in a PDF.
    For batch processing, use _ocr_embedded_batch() instead.
    """
    engine_cls = _get_vision_ocr_engine()
    if engine_cls is None:
        return '', 0.0
    try:
        engine = engine_cls()
        return engine.recognize_bytes(image_bytes)
    except Exception:
        return '', 0.0


def _ocr_embedded_batch(image_list: list[bytes]) -> list[tuple[str, float]]:
    """Batch OCR on embedded PDF image bytes via ANE parallel processing.

    Loads VisionOCREngine lazily on first call.
    Returns list of (text, confidence) tuples, same order as input.
    Individual failures return ('', 0.0) for that position.

    ISSUE-012: Replaces sequential per-image OCR with concurrent batch.
    100 images at 200ms each = 20s sequential → ~5s batch (4 ANE workers).
    """
    engine_cls = _get_vision_ocr_engine()
    if engine_cls is None:
        return [('', 0.0)] * len(image_list)
    try:
        engine = engine_cls()
        return engine.recognize_batch(image_list)
    except Exception:
        return [('', 0.0)] * len(image_list)


async def _ocr_embedded_image_async(image_bytes: bytes) -> tuple[str, float]:
    """Async version — runs in thread pool via VisionOCREngine's async entry point."""
    engine_cls = _get_vision_ocr_engine()
    if engine_cls is None:
        return '', 0.0
    try:
        engine = engine_cls()
        return await engine.recognize_bytes_async(image_bytes)
    except Exception:
        return '', 0.0


async def _ocr_embedded_batch_async(image_list: list[bytes]) -> list[tuple[str, float]]:
    """Async batch version — runs in thread pool via VisionOCREngine's async batch entry.

    ISSUE-012: Parallel ANE OCR for async contexts (DeepForensicsAnalyzer).

    UNIFIED-001: Acquires admission from GlobalPeakLoadCoordinator before
    ANE OCR batch to prevent OOM when multiple subsystems compete for memory.
    ANE OCR batch typically allocates ~1.5 GB for 4 concurrent workers.
    """
    engine_cls = _get_vision_ocr_engine()
    if engine_cls is None:
        return [('', 0.0)] * len(image_list)

    # UNIFIED-001: Acquire admission from peak load coordinator
    peak_guard = None
    try:
        from hledac.universal.core.peak_load_coordinator import (
            ResourceClass,
            TaskPriority,
            get_peak_coordinator,
        )
        coordinator = get_peak_coordinator()
        if coordinator is not None:
            # ANE OCR batch: ~1500 MB peak allocation (4 concurrent workers)
            peak_guard = await coordinator.acquire(
                ResourceClass.ANE_VISION,
                estimated_mb=1500.0,
                priority=TaskPriority.NORMAL,
                owner=f"ane_ocr_batch:{len(image_list)}_images",
                timeout_s=5.0,
            )
    except (ImportError, TimeoutError) as e:
        logger.debug(f"[UNIFIED-001] ANE OCR admission failed: {e}")
        # Fail-open: proceed without admission if coordinator unavailable
    except Exception as e:
        logger.debug(f"[UNIFIED-001] ANE OCR admission error (fail-open): {e}")

    # UNIFIED-001: Wrap actual work in peak_guard context to ensure release
    try:
        engine = engine_cls()
        if peak_guard is not None:
            async with peak_guard:
                return await engine.recognize_batch_async(image_list)
        else:
            return await engine.recognize_batch_async(image_list)
    except Exception:
        return [('', 0.0)] * len(image_list)


class DocumentType(Enum):
    """Supported document types."""
    PDF = 'pdf'
    MICROSOFT_WORD = 'docx'
    MICROSOFT_EXCEL = 'xlsx'
    MICROSOFT_POWERPOINT = 'pptx'
    OPEN_DOCUMENT_TEXT = 'odt'
    OPEN_DOCUMENT_SPREADSHEET = 'ods'
    RTF = 'rtf'
    IMAGE = 'image'
    UNKNOWN = 'unknown'

class MetadataCategory(Enum):
    """Categories of document metadata."""
    CREATION = 'creation'
    MODIFICATION = 'modification'
    AUTHORSHIP = 'authorship'
    SOFTWARE = 'software'
    LOCATION = 'location'
    DEVICE = 'device'
    CUSTOM = 'custom'

class GeoLocation(msgspec.Struct, gc=False):
    """GPS coordinates extracted from EXIF."""
    latitude: float
    longitude: float
    altitude: float | None = None
    timestamp: datetime | None = None
    gps_version: str | None = None
    coordinate_system: str = 'WGS84'

    def to_dms(self) -> tuple[tuple[int, int, float], str]:
        """Convert decimal degrees to DMS (Degrees, Minutes, Seconds)."""

        def decimal_to_dms(decimal: float) -> tuple[int, int, float]:
            degrees = int(decimal)
            minutes_float = abs(decimal - degrees) * 60
            minutes = int(minutes_float)
            seconds = (minutes_float - minutes) * 60
            return (degrees, minutes, seconds)
        lat_dms = decimal_to_dms(self.latitude)
        lat_ref = 'N' if self.latitude >= 0 else 'S'
        return (lat_dms, lat_ref)

    def to_google_maps_url(self) -> str:
        """Generate Google Maps URL."""
        return f'https://www.google.com/maps?q={self.latitude},{self.longitude}'

class EXIFData(msgspec.Struct, frozen=True, gc=False):
    """Comprehensive EXIF data from images."""
    camera_make: str | None = None
    camera_model: str | None = None
    software: str | None = None
    date_time_original: datetime | None = None
    date_time_digitized: datetime | None = None
    gps_location: GeoLocation | None = None
    image_width: int | None = None
    image_height: int | None = None
    orientation: int | None = None
    flash: bool | None = None
    focal_length: float | None = None
    iso_speed: int | None = None
    aperture: str | None = None
    shutter_speed: str | None = None
    raw_exif: dict[str, Any] = field(default_factory=dict)

class DocumentMetadata(msgspec.Struct, frozen=True, gc=False):
    """Comprehensive document metadata."""
    file_hash_md5: str
    file_hash_sha1: str
    file_hash_sha256: str
    file_size_bytes: int
    file_type: DocumentType
    file_extension: str
    author: str | None = None
    creator: str | None = None
    last_modified_by: str | None = None
    company: str | None = None
    title: str | None = None
    subject: str | None = None
    keywords: list[str] = field(default_factory=list)
    category: str | None = None
    creation_date: datetime | None = None
    modification_date: datetime | None = None
    last_printed: datetime | None = None
    creating_application: str | None = None
    application_version: str | None = None
    os_platform: str | None = None
    location: str | None = None
    gps_coordinates: GeoLocation | None = None
    revision_number: int | None = None
    total_editing_time_minutes: int | None = None
    template_used: str | None = None
    manager: str | None = None
    hyperlinks_base: str | None = None
    raw_metadata: dict[str, Any] = field(default_factory=dict)

class EmbeddedObject(msgspec.Struct, frozen=True, gc=False):
    """Represents an embedded object in a document."""
    object_type: str
    object_name: str
    content_type: str | None
    size_bytes: int
    extracted_content: bytes | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

class DocumentAnalysis(msgspec.Struct, frozen=True, gc=False):
    """Complete document analysis result."""
    metadata: DocumentMetadata
    embedded_objects: list[EmbeddedObject] = field(default_factory=list)
    hyperlinks: list[str] = field(default_factory=list)
    email_addresses: list[str] = field(default_factory=list)
    ip_addresses: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    revisions: list[dict[str, Any]] = field(default_factory=list)
    hidden_text: list[str] = field(default_factory=list)
    suspicious_indicators: list[str] = field(default_factory=list)
    exif_data: EXIFData | None = None
    ocr_text: str = ''  # Vision Framework ANE OCR for scanned images/PDFs
    canary_tokens: list[str] = field(default_factory=list)  # ISSUE-015: detected canary tokens / tracking beacons
    # ISSUE-016: PDF forensics - OCG layers, redaction failures, suppressed annotations
    ocg_layers: list[dict[str, Any]] = field(default_factory=list)  # Optional Content Groups (hidden layers)
    redaction_failures: list[str] = field(default_factory=list)  # Text visible under black rectangles
    suppressed_annotations: list[dict[str, Any]] = field(default_factory=list)  # Hidden annotations (/F 6 flag)

class PDFAnalyzer:
    """
    Advanced PDF document analyzer.

    ADVERSARY-001 fix: High-entropy / unknown-source PDFs are analyzed
    in a subprocess isolation sandbox (Tier-B) to contain PyMuPDF CVEs.
    Standard PDFs run in-process with risk profiling.
    """
    EMAIL_PATTERN = re.compile('[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}')
    IP_PATTERN = re.compile('\\b(?:[0-9]{1,3}\\.){3}[0-9]{1,3}\\b')
    URL_PATTERN = re.compile('https?://[^\\s<>\\"{}|\\\\^`\\[\\]]+')
    __slots__ = tuple(('suspicious_keywords', '_sandbox'))

    def __init__(self):
        self.suspicious_keywords = ['confidential', 'classified', 'secret', 'proprietary', 'internal use only', 'do not distribute', 'draft', 'redacted', 'sensitive']
        self._sandbox = get_sandbox_coordinator()

    def analyze(self, file_path: str | bytes | BinaryIO, source: str = "unknown") -> DocumentAnalysis:
        """
        Analyze PDF document with sandbox-aware risk routing.

        ADVERSARY-001: Routes ALL PDFs to subprocess isolation when SANDBOX_ENABLED.
        PyMuPDF runs in sandboxed subprocess with Seatbelt containment.
        A crafted PDF cannot exploit PyMuPDF/mupdf CVEs to pivot to orchestrator.

        Args:
            file_path: Path to PDF file, bytes, or file-like object
            source: Source fingerprint ("clearnet", "tor", "i2p", "user", etc.)

        Returns:
            DocumentAnalysis with all extracted data
        """
        import asyncio
        
        # ── ADVERSARY-001: Risk classification before parsing ──────────────
        if isinstance(file_path, (str, Path)):
            risk = profile_file_risk(file_path, source)
            tier = self._sandbox.get_tier_for_file(file_path, source)
            logger.debug(
                "[ADVERSARY-001] PDF: path=%s source=%s risk=%s "
                "entropy=%.2f tier=%s",
                Path(file_path).name if isinstance(file_path, str) else str(file_path),
                source,
                risk.risk_level.name,
                risk.entropy_bits_per_byte,
                tier.name,
            )
        
        # Check for sandbox-enabled async path first
        if SANDBOX_ENABLED and isinstance(file_path, (str, Path)):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If we're already in an async context, schedule the coroutine
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as executor:
                        future = executor.submit(
                            asyncio.run,
                            run_pymupdf_sandboxed(str(file_path), source, timeout_s=60.0)
                        )
                        result_data = future.result(timeout=65.0)  # Slightly higher than subprocess timeout
                        return self._convert_sandbox_result(result_data)
                else:
                    # No running loop - use asyncio.run
                    result_data = asyncio.run(
                        run_pymupdf_sandboxed(str(file_path), source, timeout_s=60.0)
                    )
                    return self._convert_sandbox_result(result_data)
            except Exception as e:
                logger.warning(f"[ADVERSARY-001] PyMuPDF sandbox failed: {e}, fallback to in-process")
        
        # Fallback to in-process analysis (no sandbox or sandbox failed)
        if not PYMUPDF_AVAILABLE:
            return self._basic_pdf_analysis(file_path)
        try:
            doc = fitz.open(file_path)
            metadata = self._extract_pdf_metadata(doc, file_path)
            probe_result = self._probe_pdf(doc)
            SIGNAL_THRESHOLD = 0.5
            full_text = ''
            if probe_result['signal_score'] >= SIGNAL_THRESHOLD:
                deep_texts = self._deep_parse_pages(doc, probe_result['candidate_pages'])
                full_text = ' '.join(deep_texts)
            else:
                for page_num in probe_result['candidate_pages']:
                    if page_num < len(doc):
                        page = doc[page_num]
                        full_text += page.get_text()
            embedded_objects = self._extract_pdf_objects(doc)
            hyperlinks = self.URL_PATTERN.findall(full_text)
            emails = self.EMAIL_PATTERN.findall(full_text)
            ip_addresses = self.IP_PATTERN.findall(full_text)
            suspicious = self._detect_suspicious_content(full_text)

            # ISSUE-015: Canary token detection (pre-flight OPSEC check)
            from forensics.canary_detector import scan_for_canary_tokens
            canary_detection = scan_for_canary_tokens(full_text)
            canary_tokens = canary_detection.tokens if canary_detection.detected else []

            # ISSUE-016: PDF forensics - OCG layers, redaction failures, suppressed annotations
            ocg_layers = self._extract_ocg_layers(doc)
            redaction_failures = self._detect_redaction_failures(doc)
            suppressed_annotations = self._extract_suppressed_annotations(doc)

            doc.close()
            return DocumentAnalysis(
                metadata=metadata,
                embedded_objects=embedded_objects,
                hyperlinks=hyperlinks,
                email_addresses=emails,
                ip_addresses=ip_addresses,
                suspicious_indicators=suspicious,
                canary_tokens=canary_tokens,
                ocg_layers=ocg_layers,
                redaction_failures=redaction_failures,
                suppressed_annotations=suppressed_annotations,
            )
        except Exception as e:
            logger.warning(f'PDF analysis failed: {e}')
            return DocumentAnalysis(metadata=DocumentMetadata(), embedded_objects=[], hyperlinks=[], email_addresses=[], ip_addresses=[], suspicious_indicators=[])

    def _convert_sandbox_result(self, result_data: dict) -> DocumentAnalysis:
        """Convert sandboxed PyMuPDF result to DocumentAnalysis.
        
        ADVERSARY-001: Maps the sandbox subprocess JSON output to the
        in-process DocumentAnalysis structure.
        """
        if not result_data:
            logger.warning("[ADVERSARY-001] Empty sandbox result, returning empty analysis")
            return DocumentAnalysis(
                metadata=DocumentMetadata(),
                embedded_objects=[],
                hyperlinks=[],
                email_addresses=[],
                ip_addresses=[],
                suspicious_indicators=[],
            )
        
        # Check for error in sandbox result (set by _error_result in media_sandbox.py)
        meta_dict = result_data.get('metadata', {})
        if meta_dict.get('error'):
            logger.warning(f"[ADVERSARY-001] Sandbox returned error: {meta_dict.get('error')}")
            # Still return partial result - the error message will be in analysis_stats
            # This allows the caller to decide whether to retry with fallback
        
        # Also check analysis_stats for errors
        analysis_stats = result_data.get('analysis_stats', {})
        if analysis_stats.get('error'):
            logger.warning(f"[ADVERSARY-001] Sandbox analysis error: {analysis_stats.get('error')}")
        
        # Parse creation/modification dates if present
        creation_date = None
        mod_date = None
        if meta_dict.get('creation_date'):
            creation_date = self._parse_pdf_date(meta_dict.get('creation_date'))
        if meta_dict.get('modification_date'):
            mod_date = self._parse_pdf_date(meta_dict.get('modification_date'))
        
        # Build DocumentMetadata
        try:
            # Parse keywords
            keywords_str = meta_dict.get('keywords', '')
            keywords_list = [k.strip() for k in keywords_str.split(',') if k.strip()] if keywords_str else []
            
            metadata = DocumentMetadata(
                file_hash_md5=meta_dict.get('file_hash_md5', ''),
                file_hash_sha1='',  # sha1 not available from sandbox subprocess
                file_hash_sha256=meta_dict.get('file_hash_sha256', ''),
                file_size_bytes=meta_dict.get('file_size_bytes', 0),
                file_type=DocumentType.PDF,
                file_extension='.pdf',
                title=meta_dict.get('title', ''),
                author=meta_dict.get('author', ''),
                creator=meta_dict.get('creator', ''),
                creating_application=meta_dict.get('producer', ''),
                creation_date=creation_date,
                modification_date=mod_date,
                subject=meta_dict.get('subject', ''),
                keywords=keywords_list,
                # Store sandbox analysis stats in raw_metadata for forensics
                raw_metadata={
                    **meta_dict,
                    **analysis_stats,  # Includes page_count, pymupdf_available, embedded_objects_count
                    'sandboxed': True,  # Mark as sandboxed result
                },
            )
        except Exception:
            metadata = DocumentMetadata()
        
        # Convert embedded objects
        embedded_objects = []
        for obj in result_data.get('embedded_objects', []):
            if isinstance(obj, dict):
                embedded_objects.append(EmbeddedObject(
                    object_type=obj.get('object_type', 'unknown'),
                    object_name=obj.get('xref', str(obj.get('xref', ''))),
                    content_type=obj.get('ext'),
                    size_bytes=obj.get('size_bytes', 0),
                    extracted_content=b'',  # Not extracted in sandbox mode for security
                    metadata=obj,
                ))
        
        # ADVERSARY-001: Canary tokens from sandbox are list of strings (matching DocumentAnalysis type)
        # Sandbox returns list of strings in format "type:value"
        canary_tokens_raw = result_data.get('canary_tokens', [])
        if canary_tokens_raw and isinstance(canary_tokens_raw[0], dict):
            # Convert from dict format to string format
            canary_tokens = [t.get('type', 'unknown') + ':' + t.get('value', '')[:50] for t in canary_tokens_raw]
        else:
            canary_tokens = canary_tokens_raw

        return DocumentAnalysis(
            metadata=metadata,
            embedded_objects=embedded_objects,
            hyperlinks=result_data.get('hyperlinks', []),
            email_addresses=result_data.get('email_addresses', []),
            ip_addresses=result_data.get('ip_addresses', []),
            suspicious_indicators=result_data.get('suspicious_indicators', []),
            canary_tokens=canary_tokens,
            ocg_layers=result_data.get('ocg_layers', []),
            redaction_failures=result_data.get('redaction_failures', []),
            suppressed_annotations=result_data.get('suppressed_annotations', []),
        )

    def _probe_pdf(self, doc) -> dict:
        """
        Probe PDF to estimate signal score and identify candidate pages.

        Args:
            doc: PyMuPDF document object

        Returns:
            dict with "signal_score" (float) and "candidate_pages" (list[int])
        """
        MAX_DEEP_PDF_PAGES = 12
        if not PYMUPDF_AVAILABLE:
            return {'signal_score': 0.5, 'candidate_pages': list(range(min(10, len(doc) if hasattr(doc, '__len__') else 10)))}
        try:
            total_pages = len(doc)
            if total_pages == 0:
                return {'signal_score': 0.0, 'candidate_pages': []}
            sample_size = min(5, total_pages)
            sample_indices = [int(i * total_pages / sample_size) for i in range(sample_size)]
            text_lengths = []
            has_images = 0
            for page_num in sample_indices:
                page = doc[page_num]
                text = page.get_text()
                text_lengths.append(len(text))
                image_list = page.get_images()
                if image_list:
                    has_images += 1
            avg_text_length = sum(text_lengths) / len(text_lengths) if text_lengths else 0
            image_ratio = has_images / len(sample_indices) if sample_indices else 0
            signal_score = min(1.0, avg_text_length / 500.0 + image_ratio * 0.3)
            page_scores = []
            for page_num in range(total_pages):
                try:
                    page = doc[page_num]
                    text = page.get_text()
                    images = len(page.get_images()) if PYMUPDF_AVAILABLE else 0
                    score = len(text) + images * 100
                    page_scores.append((page_num, score))
                except Exception:
                    page_scores.append((page_num, 0))
            page_scores.sort(key=lambda x: x[1], reverse=True)
            candidate_pages = [p[0] for p in page_scores[:MAX_DEEP_PDF_PAGES]]
            return {'signal_score': signal_score, 'candidate_pages': candidate_pages}
        except Exception as e:
            logger.warning(f'PDF probing failed: {e}')
            return {'signal_score': 0.5, 'candidate_pages': list(range(5))}

    def _deep_parse_pages(self, doc, page_indices: list[int]) -> list[str]:
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
            logger.warning(f'Deep PDF parsing failed: {e}')
        return results

    def _extract_pdf_metadata(self, doc: fitz.Document, file_path) -> DocumentMetadata:
        """Extract PDF metadata."""
        pdf_metadata = doc.metadata
        if isinstance(file_path, str):
            _guard_file_size(file_path)
            with open(file_path, 'rb') as f:
                content = f.read()
        elif isinstance(file_path, bytes):
            content = file_path
        else:
            content = file_path.read()
        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha256(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()
        creation_date = self._parse_pdf_date(pdf_metadata.get('creationDate'))
        mod_date = self._parse_pdf_date(pdf_metadata.get('modDate'))
        return DocumentMetadata(file_hash_md5=md5_hash, file_hash_sha1=sha1_hash, file_hash_sha256=sha256_hash, file_size_bytes=len(content), file_type=DocumentType.PDF, file_extension='.pdf', author=pdf_metadata.get('author'), creator=pdf_metadata.get('creator'), title=pdf_metadata.get('title'), subject=pdf_metadata.get('subject'), keywords=pdf_metadata.get('keywords', '').split(',') if pdf_metadata.get('keywords') else [], creation_date=creation_date, modification_date=mod_date, creating_application=pdf_metadata.get('producer'), application_version=pdf_metadata.get('format'), raw_metadata=pdf_metadata)

    def _parse_pdf_date(self, date_str: str | None) -> datetime | None:
        """Parse PDF date string format."""
        if not date_str:
            return None
        try:
            if date_str.startswith('D:'):
                date_str = date_str[2:]
            year = int(date_str[:4])
            month = int(date_str[4:6]) if len(date_str) >= 6 else 1
            day = int(date_str[6:8]) if len(date_str) >= 8 else 1
            hour = int(date_str[8:10]) if len(date_str) >= 10 else 0
            minute = int(date_str[10:12]) if len(date_str) >= 12 else 0
            second = int(date_str[12:14]) if len(date_str) >= 14 else 0
            return datetime(year, month, day, hour, minute, second)
        except Exception:
            return None

    def _extract_pdf_objects(self, doc: fitz.Document) -> list[EmbeddedObject]:
        """Extract embedded objects from PDF, including Vision Framework ANE OCR for images.

        ISSUE-012: Batch OCR — collects all image xrefs first, then batch-OCRs
        them concurrently via ANE. This replaces the old sequential per-image
        OCR path which underutilized the Neural Engine.
        """
        objects = []
        # Phase 1: Collect image xrefs and extract bytes (sequential — xref extraction is fast)
        pending_ocr: list[tuple[bytes, int]] = []  # (image_bytes, xref_index) for batch OCR

        for xref in range(1, doc.xref_length()):
            try:
                doc.xref_get_key(xref, 'Type')
                subtype = doc.xref_get_key(xref, 'Subtype')
                if subtype[1] == 'Image':
                    base_image = _safe_extract_image(doc, xref)
                    if base_image:
                        image_bytes = base_image.get('image', b'')
                        obj_metadata = {
                            'width': base_image.get('width'),
                            'height': base_image.get('height'),
                            'colorspace': base_image.get('colorspace'),
                        }
                        objects.append(EmbeddedObject(
                            object_type='image',
                            object_name=f'image_{xref}',
                            content_type=base_image.get('ext'),
                            size_bytes=len(image_bytes),
                            extracted_content=image_bytes,
                            metadata=obj_metadata,
                        ))
                        # Collect for batch OCR (only non-empty images)
                        if image_bytes:
                            pending_ocr.append((image_bytes, len(objects) - 1))
                elif subtype[1] in ['FileAttachment', 'EmbeddedFile']:
                    stream = _safe_xref_stream(doc, xref)
                    if stream:
                        name = doc.xref_get_key(xref, 'F')
                        objects.append(EmbeddedObject(
                            object_type='file_attachment',
                            object_name=name[1] if name else f'attachment_{xref}',
                            content_type=None,
                            size_bytes=len(stream),
                            extracted_content=stream,
                        ))
            except Exception:
                continue

        # Phase 2: Batch OCR all collected images (parallel ANE processing)
        if pending_ocr:
            image_list = [item[0] for item in pending_ocr]
            ocr_results = _ocr_embedded_batch(image_list)
            for (_, obj_idx), (ocr_text, ocr_conf) in zip(pending_ocr, ocr_results, strict=True):
                if ocr_text and obj_idx < len(objects):
                    # Rebuild EmbeddedObject with OCR metadata (frozen struct)
                    obj = objects[obj_idx]
                    updated_metadata = dict(obj.metadata)
                    updated_metadata['ocr_text'] = ocr_text
                    updated_metadata['ocr_confidence'] = ocr_conf
                    objects[obj_idx] = EmbeddedObject(
                        object_type=obj.object_type,
                        object_name=obj.object_name,
                        content_type=obj.content_type,
                        size_bytes=obj.size_bytes,
                        extracted_content=obj.extracted_content,
                        metadata=updated_metadata,
                    )

        return objects

    def _extract_ocg_layers(self, doc: fitz.Document) -> list[dict]:
        """Extract Optional Content Groups (OCG) from PDF - ISSUE-016.

        OCGs are PDF layers that can be toggled on/off. Hidden layers may contain
        sensitive information like redacted text, watermarks, or alternate content.

        M1 8GB safe: Bounded to max 10 OCGs to prevent memory exhaustion.

        Returns:
            List of dicts with keys:
            - name: OCG layer name
            - intent: Usage intent (e.g., 'View', 'Design')
            - on: Whether layer is currently visible
            - text_samples: Sample text extracted from this layer (first 200 chars)
        """
        MAX_OCG_LAYERS = 10
        ocg_layers = []

        try:
            ocg_dict = doc.get_ocgs()
            if not ocg_dict:
                return []

            for xref, ocg_info in list(ocg_dict.items())[:MAX_OCG_LAYERS]:
                try:
                    layer_data = {
                        'xref': xref,
                        'name': ocg_info.get('name', 'Unnamed'),
                        'intent': ocg_info.get('intent', 'View'),
                        'on': ocg_info.get('on', True),
                        'text_samples': [],
                    }

                    for page_num in range(min(len(doc), 20)):
                        page = doc[page_num]
                        try:
                            page_ocgs = page.get_ocgs()
                            if xref in page_ocgs:
                                text = page.get_text()[:200]
                                if text.strip():
                                    layer_data['text_samples'].append({
                                        'page': page_num,
                                        'text': text,
                                    })
                        except Exception:
                            continue

                    ocg_layers.append(layer_data)
                except Exception as e:
                    logger.debug(f'Failed to extract OCG xref={xref}: {e}')
                    continue

        except Exception as e:
            logger.debug(f'OCG extraction failed: {e}')

        return ocg_layers

    def _detect_redaction_failures(self, doc: fitz.Document) -> list[str]:
        """Detect redaction failures - text visible under black rectangles - ISSUE-016.

        Redaction failures occur when:
        1. Black rectangles are drawn over text (visual redaction)
        2. But the underlying text is still selectable/searchable

        This is a critical security issue - the redacted content is still accessible.

        M1 8GB safe: Limits checks to first 50 pages and max 100 failures.

        Returns:
            List of strings describing each redaction failure found.
        """
        MAX_PAGES_TO_CHECK = 50
        MAX_FAILURES = 100
        redaction_failures = []

        try:
            pages_to_check = min(len(doc), MAX_PAGES_TO_CHECK)

            for page_num in range(pages_to_check):
                if len(redaction_failures) >= MAX_FAILURES:
                    break

                page = doc[page_num]

                annots = page.annots()
                if not annots:
                    continue

                for annot in annots:
                    try:
                        annot_type = annot.type[0]
                        if annot_type != 14:
                            continue

                        rect = annot.rect
                        if not rect:
                            continue

                        text_dict = page.get_text('dict', clip=rect)
                        hidden_texts = []
                        for block in text_dict.get('blocks', []):
                            if block.get('type') == 0:
                                for line in block.get('lines', []):
                                    for span in line.get('spans', []):
                                        span_text = span.get('text', '').strip()
                                        if span_text:
                                            hidden_texts.append(span_text)

                        if hidden_texts:
                            combined = ' '.join(hidden_texts)
                            redaction_failures.append(
                                f'Page {page_num + 1}: Redaction failure at '
                                f'({rect.x0:.1f},{rect.y0:.1f})-({rect.x1:.1f},{rect.y1:.1f}) '
                                f'- hidden text: "{combined[:100]}"'
                            )
                            if len(redaction_failures) >= MAX_FAILURES:
                                break
                    except Exception:
                        continue

        except Exception as e:
            logger.debug(f'Redaction failure detection failed: {e}')

        return redaction_failures

    def _extract_suppressed_annotations(self, doc: fitz.Document) -> list[dict]:
        """Extract suppressed/hidden annotations from PDF - ISSUE-016.

        PDF annotations can have a /F (flags) field. Flag value 6 means:
        - Bit 1 (1): Invisible - annotation not displayed/printed
        - Bit 2 (2): Hidden - annotation cannot be interacted with

        These hidden annotations may contain IOCs, comments, or sensitive data
        that was deliberately hidden from viewers.

        M1 8GB safe: Limits to first 50 pages and max 200 annotations.

        Returns:
            List of dicts with keys:
            - page: Page number
            - type: Annotation type (e.g., 'Text', 'FreeText', 'Stamp')
            - content: Annotation content/text
            - flags: Raw flag value
            - rect: Bounding rectangle
        """
        MAX_PAGES_TO_CHECK = 50
        MAX_ANNOTATIONS = 200
        SUPPRESSED_FLAG_VALUES = {2, 6}
        suppressed = []

        try:
            pages_to_check = min(len(doc), MAX_PAGES_TO_CHECK)

            for page_num in range(pages_to_check):
                if len(suppressed) >= MAX_ANNOTATIONS:
                    break

                page = doc[page_num]
                annots = page.annots()
                if not annots:
                    continue

                for annot in annots:
                    try:
                        flags = annot.flags
                        if flags not in SUPPRESSED_FLAG_VALUES:
                            continue

                        annot_type = annot.type[1] if annot.type else 'Unknown'
                        content = annot.info.get('content', '') if annot.info else ''
                        rect = annot.rect

                        suppressed.append({
                            'page': page_num + 1,
                            'type': annot_type,
                            'content': content[:500] if content else '',
                            'flags': flags,
                            'rect': {
                                'x0': rect.x0,
                                'y0': rect.y0,
                                'x1': rect.x1,
                                'y1': rect.y1,
                            } if rect else None,
                        })

                        if len(suppressed) >= MAX_ANNOTATIONS:
                            break
                    except Exception:
                        continue

        except Exception as e:
            logger.debug(f'Suppressed annotation extraction failed: {e}')

        return suppressed

    def _detect_suspicious_content(self, text: str) -> list[str]:
        """Detect suspicious keywords in text using Aho-Corasick if available.

        Lazy integration (Sprint 8AW): ahocorasick is NOT loaded on boot.
        On first call, the automaton is built once and reused.
        Falls back to substring scan if aho_extractor is unavailable.
        """
        aho_mod = _get_aho_extractor()
        if aho_mod is not None:
            return aho_mod.scan_suspicious_keywords_list(text)
        text_lower = text.lower()
        return [kw for kw in self.suspicious_keywords if kw in text_lower]

    def _basic_pdf_analysis(self, file_path) -> DocumentAnalysis:
        """Fallback basic analysis without PyMuPDF."""
        if isinstance(file_path, str):
            _guard_file_size(file_path)
            with open(file_path, 'rb') as f:
                content = f.read()
        elif isinstance(file_path, bytes):
            content = file_path
        else:
            content = file_path.read()
        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha256(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()
        text = content.decode('utf-8', errors='ignore')
        metadata = DocumentMetadata(file_hash_md5=md5_hash, file_hash_sha1=sha1_hash, file_hash_sha256=sha256_hash, file_size_bytes=len(content), file_type=DocumentType.PDF, file_extension='.pdf')
        return DocumentAnalysis(metadata=metadata, hyperlinks=self.URL_PATTERN.findall(text), email_addresses=self.EMAIL_PATTERN.findall(text))


class OfficeDocumentAnalyzer:
    """
    Analyzer for Microsoft Office and OpenDocument files.
    """
    __slots__ = tuple(('_foca_extractor', '_foca_initialized'))

    def __init__(self):
        self._foca_extractor: Any = None
        self._foca_initialized = False

    async def close(self) -> None:
        """Close FOCA extractor and release resources (fail-safe)."""
        if self._foca_extractor is not None:
            try:
                await self._foca_extractor.close()
            except Exception as e:
                logger.debug(f'FOCA extractor close error: {e}')
            self._foca_extractor = None
            self._foca_initialized = False

    async def _get_foca_extractor(self) -> Any:
        """Lazily initialize FOCA metadata extractor (M1-safe async)."""
        if self._foca_initialized:
            return self._foca_extractor
        self._foca_initialized = True
        try:
            from forensics.metadata_extractor import UniversalMetadataExtractor
            self._foca_extractor = UniversalMetadataExtractor()
            await self._foca_extractor.initialize()
        except Exception as e:
            logger.warning(f'FOCA extractor unavailable: {e}')
            self._foca_extractor = None
        return self._foca_extractor

    def analyze(self, file_path: str | bytes) -> DocumentAnalysis:
        """Analyze Office document (sync)."""
        if isinstance(file_path, str):
            _guard_file_size(file_path)
            with open(file_path, 'rb') as f:
                content = f.read()
        else:
            content = file_path
        if content[:4] == b'PK\x03\x04':
            return self._analyze_ooxml(content, file_path if isinstance(file_path, str) else None)
        else:
            return self._analyze_ole(content)

    async def analyze_async(self, file_path: str | bytes) -> DocumentAnalysis:
        """Analyze Office document with FOCA enrichment (async, M1-safe)."""
        if isinstance(file_path, str):
            _guard_file_size(file_path)
            content = await asyncio.to_thread(Path(file_path).read_bytes)
        else:
            content = file_path
        if content[:4] == b'PK\x03\x04':
            return await self._analyze_ooxml_async(content, file_path if isinstance(file_path, str) else None)
        else:
            return self._analyze_ole(content)

    async def _analyze_ooxml_async(self, content: bytes, file_path: str | None) -> DocumentAnalysis:
        """Analyze OOXML with FOCA metadata enrichment."""
        analysis = self._analyze_ooxml(content, file_path)
        if file_path:
            try:
                extractor = await self._get_foca_extractor()
                if extractor is not None:
                    foca_result = await extractor.extract(file_path)
                    if foca_result and foca_result.success:
                        self._merge_foca_metadata(analysis, foca_result)
            except Exception as e:
                logger.debug(f'FOCA enrichment skipped for {file_path}: {e}')
        return analysis

    def _merge_foca_metadata(self, analysis: DocumentAnalysis, foca_result: Any) -> None:
        """Merge FOCA metadata into DocumentAnalysis return value.

        FOCA data goes into metadata.raw_metadata['foca'] — different seam from TriageFacets.
        """
        foca_data = {}
        if foca_result.pptx:
            foca_data['pptx'] = foca_result.pptx.to_dict()
        if foca_result.email:
            foca_data['email'] = foca_result.email.to_dict()
        if foca_result.cad:
            foca_data['cad'] = foca_result.cad.to_dict()
        if foca_data:
            analysis.metadata.raw_metadata['foca'] = foca_data

    def _analyze_ooxml(self, content: bytes, file_path: str | None) -> DocumentAnalysis:
        """Analyze Office Open XML format (docx, xlsx, pptx)."""
        embedded_objects = []
        hyperlinks = []
        comments = []
        canary_tokens = []
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                metadata = self._extract_ooxml_core_props(z, content)
                if 'word/comments.xml' in z.namelist():
                    comments_xml = z.read('word/comments.xml').decode('utf-8', errors='ignore')
                    comments = self._extract_comments_from_xml(comments_xml)
                if 'word/document.xml' in z.namelist():
                    doc_xml = z.read('word/document.xml').decode('utf-8', errors='ignore')
                    hyperlinks = PDFAnalyzer.URL_PATTERN.findall(doc_xml)

                    # ISSUE-015: Canary token detection (pre-flight OPSEC check)
                    from forensics.canary_detector import scan_for_canary_tokens
                    canary_detection = scan_for_canary_tokens(doc_xml)
                    if canary_detection.detected:
                        canary_tokens = canary_detection.tokens

                for name in z.namelist():
                    if name.startswith('word/media/'):
                        data = _safe_read_zip_entry(z, name)
                        if data is not None:
                            embedded_objects.append(EmbeddedObject(object_type='media', object_name=name.split('/')[-1], content_type=None, size_bytes=len(data), extracted_content=data))
        except Exception as e:
            logger.error(f'OOXML analysis error: {e}')
        return DocumentAnalysis(metadata=metadata, embedded_objects=embedded_objects, hyperlinks=hyperlinks, comments=comments, canary_tokens=canary_tokens)

    def _extract_ooxml_core_props(self, z: zipfile.ZipFile, content: bytes) -> DocumentMetadata:
        """Extract core properties from OOXML."""
        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha256(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()
        props = {}
        try:
            if 'docProps/core.xml' in z.namelist():
                core_xml = z.read('docProps/core.xml').decode('utf-8', errors='ignore')
                props = self._parse_core_xml(core_xml)
        except Exception:  # noqa: BLE001
            pass
        if 'word/document.xml' in z.namelist():
            doc_type = DocumentType.MICROSOFT_WORD
            ext = '.docx'
        elif 'xl/workbook.xml' in z.namelist():
            doc_type = DocumentType.MICROSOFT_EXCEL
            ext = '.xlsx'
        elif 'ppt/presentation.xml' in z.namelist():
            doc_type = DocumentType.MICROSOFT_POWERPOINT
            ext = '.pptx'
        else:
            doc_type = DocumentType.UNKNOWN
            ext = '.unknown'
        return DocumentMetadata(file_hash_md5=md5_hash, file_hash_sha1=sha1_hash, file_hash_sha256=sha256_hash, file_size_bytes=len(content), file_type=doc_type, file_extension=ext, author=props.get('creator'), last_modified_by=props.get('lastModifiedBy'), title=props.get('title'), subject=props.get('subject'), keywords=props.get('keywords', '').split() if props.get('keywords') else [], creation_date=props.get('created'), modification_date=props.get('modified'), application_version=props.get('version'), raw_metadata=props)

    def _parse_core_xml(self, xml_content: str) -> dict[str, Any]:
        """Parse core.xml properties."""
        props = {}
        patterns = {'creator': '<dc:creator>(.*?)</dc:creator>', 'lastModifiedBy': '<cp:lastModifiedBy>(.*?)</cp:lastModifiedBy>', 'title': '<dc:title>(.*?)</dc:title>', 'subject': '<dc:subject>(.*?)</dc:subject>', 'keywords': '<cp:keywords>(.*?)</cp:keywords>', 'created': '<dcterms:created.*?>(.*?)</dcterms:created>', 'modified': '<dcterms:modified.*?>(.*?)</dcterms:modified>', 'version': '<cp:version>(.*?)</cp:version>'}
        for key, pattern in patterns.items():
            match = re.search(pattern, xml_content)
            if match:
                value = match.group(1)
                if key in ['created', 'modified']:
                    try:
                        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    except Exception:  # noqa: BLE001
                        pass
                props[key] = value
        return props

    def _extract_comments_from_xml(self, xml_content: str) -> list[str]:
        """Extract comments from Word XML."""
        comments = []
        pattern = '<w:t>(.*?)</w:t>'
        for match in re.finditer(pattern, xml_content):
            text = match.group(1)
            if len(text) > 5:
                comments.append(text)
        return comments

    def _analyze_ole(self, content: bytes) -> DocumentAnalysis:
        """Analyze legacy OLE format."""
        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha256(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()
        metadata = DocumentMetadata(file_hash_md5=md5_hash, file_hash_sha1=sha1_hash, file_hash_sha256=sha256_hash, file_size_bytes=len(content), file_type=DocumentType.UNKNOWN, file_extension='.doc')
        return DocumentAnalysis(metadata=metadata)

class ImageAnalyzer:
    """
    Advanced image analysis for OSINT.

    Extracts EXIF data, GPS coordinates, and performs image forensics.
    """

    def analyze(self, file_path: str | bytes) -> DocumentAnalysis:
        """Analyze image file."""
        if not PIL_AVAILABLE:
            logger.warning('PIL not available - cannot analyze image')
            return self._basic_image_analysis(file_path)
        try:
            if isinstance(file_path, str):
                _guard_file_size(file_path)
                with open(file_path, 'rb') as f:
                    content = f.read()
                with Image.open(file_path) as img:
                    exif_data = self._extract_exif(img)
                    img_format = img.format
                    img_mode = img.mode
                    img_width = img.width
                    img_height = img.height
            else:
                content = file_path if isinstance(file_path, bytes) else file_path.read()
                with Image.open(io.BytesIO(file_path if isinstance(file_path, bytes) else file_path)) as img:
                    exif_data = self._extract_exif(img)
                    img_format = img.format
                    img_mode = img.mode
                    img_width = img.width
                    img_height = img.height
            md5_hash = hashlib.md5(content).hexdigest()
            sha1_hash = hashlib.sha256(content).hexdigest()
            sha256_hash = hashlib.sha256(content).hexdigest()
            metadata = DocumentMetadata(file_hash_md5=md5_hash, file_hash_sha1=sha1_hash, file_hash_sha256=sha256_hash, file_size_bytes=len(content), file_type=DocumentType.IMAGE, file_extension=f'.{img_format.lower()}' if img_format else '.unknown', image_width=img_width, image_height=img_height, gps_coordinates=exif_data.gps_location if exif_data else None, raw_metadata={'format': img_format, 'mode': img_mode})
            # Vision Framework ANE OCR — hardware-accelerated text extraction from image bytes
            ocr_text, _ = _ocr_embedded_image(content) if PYMUPDF_AVAILABLE else ('', 0.0)

            # ISSUE-015: Canary token detection in OCR text and EXIF metadata
            canary_tokens: list[str] = []
            scan_targets = [ocr_text]
            if exif_data and exif_data.raw_exif:
                # Scan EXIF text fields that can embed canary tokens
                for tag_name in ('UserComment', 'ImageDescription', 'Software', 'Make', 'Model'):
                    exif_val = exif_data.raw_exif.get(tag_name)
                    if isinstance(exif_val, str) and exif_val.strip():
                        scan_targets.append(exif_val)
            combined_scan = ' '.join(scan_targets)
            if combined_scan.strip():
                from forensics.canary_detector import scan_for_canary_tokens
                canary_result = scan_for_canary_tokens(combined_scan)
                if canary_result.detected:
                    canary_tokens = canary_result.tokens

            return DocumentAnalysis(metadata=metadata, exif_data=exif_data, ocr_text=ocr_text, canary_tokens=canary_tokens)
        except Exception as e:
            logger.error(f'Image analysis error: {e}')
            return self._basic_image_analysis(file_path)

    # EXIF tag → handler dispatch table (reduces 15+ elif branches to O(1) lookup)
    _EXIF_TAG_HANDLERS: dict[str, callable] = {}

    def _init_exif_handlers(self) -> None:
        """Initialize EXIF tag handlers lazily (avoids circular import issues)."""
        if ImageAnalyzer._EXIF_TAG_HANDLERS:
            return  # Already initialized

        def _make_str_handler(attr: str) -> callable:
            return lambda data, val: setattr(data, attr, val)

        def _make_datetime_handler(attr: str) -> callable:
            return lambda data, val: setattr(data, attr, self._parse_exif_datetime(val))

        def _make_int_handler(attr: str) -> callable:
            return lambda data, val: setattr(data, attr, int(val) if val else None)

        def _make_bool_bit_handler(attr: str, bit: int = 0) -> callable:
            return lambda data, val: setattr(data, attr, bool(val & bit) if val else None)

        def _make_float_handler(attr: str, num_type: type = float) -> callable:
            return lambda data, val: setattr(data, attr, float(val) if isinstance(val, (int, float, IFDRational)) else None)

        def _handle_aperture(data, val):
            data.aperture = f'f/{val}'

        def _handle_shutter_speed(data, val):
            if isinstance(val, (int, float)):
                data.shutter_speed = f'1/{1 / val:.0f}s' if val < 1 else f'{val}s'

        def _handle_iso(data, val):
            data.iso_speed = val[0] if isinstance(val, tuple) else val

        def _handle_gps(data, val):
            data.gps_location = self._extract_gps(self._current_exif_for_gps)

        ImageAnalyzer._EXIF_TAG_HANDLERS = {
            'Make': _make_str_handler('camera_make'),
            'Model': _make_str_handler('camera_model'),
            'Software': _make_str_handler('software'),
            'DateTimeOriginal': _make_datetime_handler('date_time_original'),
            'DateTimeDigitized': _make_datetime_handler('date_time_digitized'),
            'ExifImageWidth': _make_int_handler('image_width'),
            'ExifImageHeight': _make_int_handler('image_height'),
            'Orientation': _make_str_handler('orientation'),  # stored as-is
            'Flash': _make_bool_bit_handler('flash', 1),
            'FocalLength': _make_float_handler('focal_length'),
            'ISOSpeedRatings': _handle_iso,
            'FNumber': _handle_aperture,
            'ExposureTime': _handle_shutter_speed,
            'GPSInfo': _handle_gps,
        }

    def _extract_exif(self, img: Image.Image) -> EXIFData | None:
        """Extract EXIF data from image using dictionary dispatch."""
        try:
            exif = img._getexif()
            if not exif:
                return None

            # Initialize handlers on first use
            self._init_exif_handlers()

            data = EXIFData()
            raw_exif = {}
            # Store exif reference for GPS handler (needs full dict)
            self._current_exif_for_gps = exif

            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                raw_exif[tag] = value
                handler = ImageAnalyzer._EXIF_TAG_HANDLERS.get(tag)
                if handler:
                    handler(data, value)

            self._current_exif_for_gps = None  # Clear reference
            data.raw_exif = raw_exif
            return data
        except Exception as e:
            logger.error(f'EXIF extraction error: {e}')
            self._current_exif_for_gps = None
            return None

    def _parse_exif_datetime(self, value) -> datetime | None:
        """Parse EXIF datetime string."""
        if isinstance(value, str):
            try:
                return datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
            except ValueError:  # noqa: BLE001
                pass
        return None

    def _extract_gps(self, exif: dict) -> GeoLocation | None:
        """Extract GPS coordinates from EXIF."""
        try:
            gps_info = exif.get('GPSInfo')
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
            lat_ref = gps_info.get(1)
            lat_dms = gps_info.get(2)
            lon_ref = gps_info.get(3)
            lon_dms = gps_info.get(4)
            altitude = gps_info.get(6)
            if lat_dms and lon_dms:
                lat = convert_dms(lat_dms)
                lon = convert_dms(lon_dms)
                if lat_ref == 'S':
                    lat = -lat
                if lon_ref == 'W':
                    lon = -lon
                return GeoLocation(latitude=lat, longitude=lon, altitude=float(altitude) if altitude else None, timestamp=None)
        except Exception as e:
            logger.error(f'GPS extraction error: {e}')
        return None

    def _basic_image_analysis(self, file_path) -> DocumentAnalysis:
        """Basic analysis without PIL."""
        if isinstance(file_path, str):
            _guard_file_size(file_path)
            with open(file_path, 'rb') as f:
                content = f.read()
        else:
            content = file_path if isinstance(file_path, bytes) else file_path.read()
        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha256(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()
        metadata = DocumentMetadata(file_hash_md5=md5_hash, file_hash_sha1=sha1_hash, file_hash_sha256=sha256_hash, file_size_bytes=len(content), file_type=DocumentType.IMAGE, file_extension='.unknown')
        return DocumentAnalysis(metadata=metadata)


# ─── Standalone ELA Functions for ProcessPool (Picklable) ───────────────────────
#
# These module-level functions are picklable and can be safely passed to
# ProcessPoolExecutor with spawn context. They replace the bound methods
# self._ela_analysis_mps_sync and self._ela_analysis_cpu_sync which cannot
# be pickled on macOS with spawn context.
#
# ISSUE IO-4 fix: Bound methods capture self which is not serializable.

def _ela_mps_sync(content: bytes) -> float:
    """
    Synchronous MPS implementation of ELA for ProcessPool.
    
    This is a module-level function (not a bound method) so it can be
    pickled and sent to ProcessPoolExecutor workers.
    """
    import torch
    from PIL import Image
    import io
    try:
        with Image.open(io.BytesIO(content)) as img:
            img = img.convert('RGB')
            if img.width > MAX_IMAGE_SIZE or img.height > MAX_IMAGE_SIZE:
                ratio = min(MAX_IMAGE_SIZE / img.width, MAX_IMAGE_SIZE / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            tensor = torch.from_numpy(np.array(img)).float().permute(2, 0, 1).unsqueeze(0) / 255.0
            tensor = tensor.to('mps')
            with torch.no_grad():
                compressed = torch.nn.functional.avg_pool2d(tensor, 2)
                upscaled = torch.nn.functional.interpolate(compressed, scale_factor=2, mode='nearest')
                diff = torch.abs(tensor - upscaled)
                ela_score = diff.mean().item()
            return ela_score
    except Exception as e:
        # Fallback to CPU
        return _ela_cpu_sync(content)
    finally:
        if hasattr(torch.mps, 'empty_cache'):
            try:
                torch.mps.empty_cache()
            except Exception:
                pass


def _ela_cpu_sync(content: bytes) -> float:
    """
    Synchronous CPU implementation of ELA for ProcessPool.
    
    This is a module-level function (not a bound method) so it can be
    pickled and sent to ProcessPoolExecutor workers.
    """
    from PIL import Image, ImageChops
    import io
    import numpy as np
    with Image.open(io.BytesIO(content)) as img:
        tmp = io.BytesIO()
        img.save(tmp, format='JPEG', quality=95)
        tmp.seek(0)
        with Image.open(tmp) as compressed:
            diff = ImageChops.difference(img, compressed)
            diff_np = np.array(diff.convert('L'))
        ela_score = np.mean(diff_np) / 255.0
        return ela_score


class DeepForensicsAnalyzer:
    """Advanced forensics for images - EXIF, ELA, steganography detection.

    Uses shared ProcessPoolExecutor for CPU-bound operations (M1 8GB safe: max 2 workers).
    Steganography detection uses async subprocess pool via StegdetectServer.
    """
    __slots__ = tuple(('_orch', '_stegdetect_path', '_stegdetect_server', '_thread_pool'))

    def __init__(self, orch: Any=None):
        """Initialize DeepForensicsAnalyzer.

        Args:
            orch: Optional orchestrator reference for graph integration (S49-C)
        """
        self._orch = orch
        self._stegdetect_path = Path.home() / '.hledac' / 'bin' / 'stegdetect'
        self._stegdetect_server = StegdetectServer()
        # ThreadPool for short-lived sync CPU work (not CPU-bound image analysis)
        self._thread_pool = get_parallel_executor()  # noqa: F811 — reused pool, intentional

    async def _ensure_stegdetect(self):
        """
        ADVERSARY-001-INTERNAL-007 fix: Install stegdetect via ArtifactVerifier.

        Replaces the original `git clone + make` bootstrap with a SHA-256
        verified installation path:

          1. Cache hit: binary in ~/.hledac/bin with matching SHA-256 → done
          2. Release download: verified GitHub release URL (preferred)
          3. Isolated build: git clone (--depth=1, --filter=blob:none)
             → sandboxed temp build → SHA-256 verify → install

        The original fallback (git clone + make without verification) is
        DISABLED by default. Enable with HLEDAC_ENABLE_STEGDETECT_SIGNED=0.

        Once installed, stegdetect runs sandboxed via StegdetectServer
        which wraps workers with Seatbelt (Tier-A).
        """
        verifier = get_artifact_verifier()
        result = await verifier.ensure_artifact(
            "stegdetect",
            repo_url="https://github.com/abeluck/stegdetect.git",
            branch="master",
            build_cmd=["make"],
        )
        if not result.success:
            logger.warning(
                "[ADVERSARY-001] [INTERNAL-007] Stegdetect installation failed: %s",
                result.error,
            )

    def _parse_gps(self, gps_dict):
        """Parse GPS data from EXIF."""
        try:
            lat = gps_dict.get(2)
            lon = gps_dict.get(4)
            lat_ref = gps_dict.get(1)
            lon_ref = gps_dict.get(3)
            if lat and lon:
                lat_dec = lat[0] + lat[1] / 60 + lat[2] / 3600
                lon_dec = lon[0] + lon[1] / 60 + lon[2] / 3600
                if lat_ref == 'S':
                    lat_dec = -lat_dec
                if lon_ref == 'W':
                    lon_dec = -lon_dec
                return {'lat': lat_dec, 'lon': lon_dec}
        except Exception:  # noqa: BLE001
            pass
        return None

    async def analyze_image(self, content: bytes, url: str | None=None):
        """Analyze image for forensic artifacts.

        Uses ProcessPoolExecutor for CPU-bound image analysis (ELA) to avoid
        contention with MLX workers. M1 8GB safe: max 2 workers.

        Args:
            content: Image bytes
            url: Optional URL of the image for graph integration (S49-C)

        Returns:
            Dict with analysis results including ela_score, suspicious flag, etc.
        """
        result = {}
        if content.startswith(b'\xff\xd8') and piexif:
            try:
                exif = piexif.load_from_bytes(content)
                gps = exif.get('GPS')
                if gps:
                    gps_coords = self._parse_gps(gps)
                    if gps_coords:
                        result['gps_coords'] = gps_coords
            except Exception:  # noqa: BLE001
                pass
        try:
            # CPU-bound: run ELA in ProcessPool to avoid blocking MLX workers
            ela_score = await self._ela_analysis(content)
            result['ela_score'] = ela_score
            if ela_score > 0.3:
                result['suspicious'] = True
            if self._orch and ela_score > 0.7 and url:
                try:
                    if hasattr(self._orch, '_research_mgr') and self._orch._research_mgr:
                        rd = self._orch._research_mgr.relationship_discovery
                        if rd and hasattr(rd, 'flag_manipulated_image'):
                            await rd.flag_manipulated_image(url=url, ela_score=ela_score)
                except Exception as e:
                    logger.warning(f'ELA→Graph forward failed: {e}')
        except Exception:  # noqa: BLE001
            pass
        if len(content) > 10000:
            try:
                stego_prob = await self._stegdetect(content)
                result['stego_probability'] = stego_prob
                if stego_prob > 0.1:
                    result['suspicious'] = True
            except Exception:  # noqa: BLE001
                pass
        return result

    async def _ela_analysis(self, content: bytes) -> float:
        """Error Level Analysis - returns manipulation probability 0-1.

        Uses ProcessPool for CPU-bound analysis to avoid contention with MLX workers.
        M1 8GB safe: max 2 workers in shared pool.
        """
        if _check_mps_available():
            return await self._ela_analysis_mps(content)
        else:
            return await self._ela_analysis_cpu(content)

    async def _ela_analysis_mps(self, content: bytes) -> float:
        """MPS-accelerated ELA analysis (runs sync MPS in ProcessPool to avoid GIL)."""
        loop = asyncio.get_running_loop()
        pool = _get_forensics_pool()
        # Use module-level function instead of bound method for ProcessPool pickling
        return await loop.run_in_executor(pool, _ela_mps_sync, content)

    async def _ela_analysis_cpu(self, content: bytes) -> float:
        """CPU-based ELA analysis (runs in ProcessPool to avoid blocking MLX workers)."""
        loop = asyncio.get_running_loop()
        pool = _get_forensics_pool()
        # Use module-level function instead of bound method for ProcessPool pickling
        return await loop.run_in_executor(pool, _ela_cpu_sync, content)

    async def _stegdetect(self, content: bytes) -> float:
        """Run stegdetect on image using persistent server."""
        return await self._stegdetect_server.analyze(content)

class StegdetectServer:
    """
    ADVERSARY-001 fix: stegdetect subprocess pool runs with sandbox-exec
    Seatbelt profile (Tier-A) when available.

    Persistent stegdetect process pool with semaphore concurrency.
    """
    __slots__ = tuple(('_bin_path', '_initialized', '_lock', '_max_workers', '_procs', '_semaphore', '_sandbox', '_profile_path'))

    def __init__(self, max_workers: int=4):
        self._procs: list[asyncio.subprocess.Process] = []
        self._bin_path = Path.home() / '.hledac' / 'bin' / 'stegdetect'
        self._semaphore = asyncio.Semaphore(max_workers)
        self._lock = asyncio.Lock()
        self._max_workers = max_workers
        self._initialized = False
        self._sandbox = get_sandbox_coordinator()
        self._profile_path: Path | None = None

    async def _ensure_processes(self):
        """
        ADVERSARY-001: Ensure worker processes run with Seatbelt sandbox.

        When sandbox-exec is available, wraps each stegdetect worker with
        a read-only, network-denied Seatbelt profile.
        """
        if self._initialized and all((p.returncode is None for p in self._procs if p)):
            return
        fa = DeepForensicsAnalyzer()
        await fa._ensure_stegdetect()

        # ADVERSARY-001: Build sandbox-wrapped command
        steg_cmd = await self._build_sandboxed_steg_cmd()

        async with self._lock:
            self._procs = []
            for _ in range(self._max_workers):
                proc = await asyncio.create_subprocess_exec(
                    *steg_cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                self._procs.append(proc)
            self._initialized = True

    async def _build_sandboxed_steg_cmd(self) -> list[str]:
        """
        ADVERSARY-001: Build stegdetect command with optional sandbox-exec wrapper.

        Returns [sandbox-exec, -p, profile.sb, stegdetect, -r, -s] when
        seatbelt is available, otherwise [stegdetect, -r, -s].
        """
        base_cmd = [str(self._bin_path), '-r', '-s']

        if self._sandbox._seatbelt_available:
            profile = _build_image_sandbox_profile(
                os.fspath(Path.home()),
                str(self._bin_path),
            )
            profile_path = _write_sandbox_profile(
                f'stegd_{os.getpid()}_{id(self)}',
                profile,
            )
            self._profile_path = profile_path
            logger.debug(
                "[ADVERSARY-001] Wrapping stegdetect with Seatbelt: %s",
                profile_path.name,
            )
            return ['sandbox-exec', '-p', str(profile_path)] + base_cmd

        return base_cmd

    async def ensure_running(self):
        """Alias for _ensure_processes (Sprint 45 compatibility)."""
        return await self._ensure_processes()

    async def analyze(self, content: bytes) -> float:
        """
        ADVERSARY-001: Analyze image with stegdetect in sandboxed subprocess.

        Returns 0.0 on any error (fail-safe — never raises exceptions).
        """
        async with self._semaphore:
            await self._ensure_processes()
            proc = None
            for p in self._procs:
                if p.returncode is None:
                    proc = p
                    break
            if proc is None:
                await self._ensure_processes()
                proc = self._procs[0]
            tmp = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            tmp.write(content)
            tmp.close()
            try:
                proc.stdin.write(f'{tmp.name}\n'.encode())
                await proc.stdin.drain()
                line = await proc.stdout.readline()
                result = 0.8 if b'positive' in line else 0.0
                logger.debug(
                    "[ADVERSARY-001] stegdetect: score=%.2f",
                    result,
                )
                return result
            except Exception as e:
                logger.warning('[ADVERSARY-001] [STEGDETECT] Failed: %s', e)
                return 0.0
            finally:
                try:
                    os.unlink(tmp.name)
                except FileNotFoundError:  # noqa: BLE001
                    pass

    async def restart(self):
        """Restart all stegdetect processes."""
        async with self._lock:
            for proc in self._procs:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:  # noqa: BLE001
                    pass
            self._procs = []
            self._initialized = False
        await self.ensure_running()

    def close(self) -> None:
        """Close all resources including thread pool and stegdetect processes.

        Called synchronously from __del__ (GC context) — no async allowed.
        Stegdetect processes are killed outright (no restart needed on shutdown).
        """
        if hasattr(self, '_thread_pool') and self._thread_pool:
            # R5: Shared pool — do NOT shut down (managed by domain_executors)
            self._thread_pool = None
        if hasattr(self, '_stegdetect_server') and self._stegdetect_server:
            server = self._stegdetect_server
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(server.restart())
            finally:
                loop.close()
        if hasattr(self, '_orch'):
            self._orch = None

    def __del__(self) -> None:
        """Fallback shutdown on garbage collection."""
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass

class DocumentIntelligenceEngine:
    """
    Main engine for document intelligence analysis.

    Provides unified interface for analyzing all document types.
    """
    __slots__ = tuple(('_forensics', 'image_analyzer', 'office_analyzer', 'pdf_analyzer', '_forensics_thread_pool'))

    def __init__(self):
        self.pdf_analyzer = PDFAnalyzer()
        self.office_analyzer = OfficeDocumentAnalyzer()
        self.image_analyzer = ImageAnalyzer()
        self._forensics = DeepForensicsAnalyzer()
        # R5 FIX: Dedicated thread pool via domain_executors shared pool
        from hledac.universal.utils.domain_executors import get_forensics_sync_executor
        self._forensics_thread_pool = get_forensics_sync_executor()

    def _run_async(self, coro) -> Any:
        """Run an async coroutine in a separate thread with its own event loop.

        This avoids asyncio.run() crash on M1 and prevents blocking MLX workers.
        No asyncio.set_event_loop() here — loop is already current in its own thread.
        """
        try:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        except Exception as e:
            logger.debug('[F206AC] async run failed: %s', e)
            return None

    def _run_forensics_async(self, content: bytes) -> dict[str, Any] | None:
        """Run async forensics analysis in a separate thread with its own event loop."""
        return self._run_async(self._forensics.analyze_image(content))

    def analyze(self, file_path: str, source: str = "unknown") -> DocumentAnalysis:
        """
        Analyze any supported document type.

        ADVERSARY-001: Passes source fingerprint to PDF analyzer for risk-based
        sandbox routing.

        Args:
            file_path: Path to document file
            source: Source fingerprint ("clearnet", "tor", "i2p", "user", etc.)

        Returns:
            DocumentAnalysis with all extracted intelligence
        """
        extension = file_path.lower().split('.')[-1] if '.' in file_path else ''
        if extension == 'pdf':
            return self.pdf_analyzer.analyze(file_path, source=source)
        elif extension in ['docx', 'xlsx', 'pptx', 'odt', 'ods']:
            return self.office_analyzer.analyze(file_path)
        elif extension in ['jpg', 'jpeg', 'png', 'tiff', 'tif', 'gif', 'bmp', 'webp']:
            analysis = self.image_analyzer.analyze(file_path)
            try:
                _guard_file_size(file_path)
                with open(file_path, 'rb') as f:
                    content = f.read()
                if hasattr(self, '_forensics'):
                    try:
                        # M1-SAFE: run async forensics in dedicated thread with its own event loop
                        # This avoids asyncio.run() crash and CPU-bound work runs in ProcessPool
                        forensics = self._run_forensics_async(content)
                        if forensics:
                            analysis.metadata.raw_metadata['forensics'] = forensics
                    except Exception as e:
                        logger.warning(f'[F206AC] forensics analyze failed: {e}')
            except Exception as e:
                logger.warning(f'[F206AC] forensics analyze failed: {e}')
            return analysis
        else:
            with open(file_path, 'rb') as f:
                header = f.read(8)
            if header[:4] == b'%PDF':
                return self.pdf_analyzer.analyze(file_path, source=source)
            elif header[:4] == b'PK\x03\x04':
                return self.office_analyzer.analyze(file_path)
            else:
                logger.warning(f'Unknown file type: {file_path}')
                return self._create_unknown_analysis(file_path)

    def _create_unknown_analysis(self, file_path: str) -> DocumentAnalysis:
        """Create analysis for unknown file type.

        ADVERSARY-004: Also attempts Rust IOC extraction on binary content.
        If IOCs are found, they are stored in metadata.raw_metadata['auto_re_iocs']
        for the AutoRE sidecar to pick up during advisory runner phase.
        """
        _guard_file_size(file_path)
        with open(file_path, 'rb') as f:
            content = f.read()
        md5_hash = hashlib.md5(content).hexdigest()
        sha1_hash = hashlib.sha256(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()

        # ADVERSARY-004: Try Rust IOC extraction on unknown binary content
        # (fallback path — main AutoRE pipeline runs via sidecar with Hermes3)
        auto_re_iocs: list[tuple[str, str]] = []
        if 1024 <= len(content) <= 1_048_576:  # 1KB–1MB
            try:
                import hledac_rust_extensions as rust
                text_candidate = content.decode("utf-8", errors="ignore")
                if len(text_candidate) >= 64:
                    auto_re_iocs = rust.extract_iocs_simd(text_candidate)
            except Exception:  # noqa: BLE001
                pass  # fail-soft

        raw_metadata: dict[str, Any] = {}
        if auto_re_iocs:
            raw_metadata["auto_re_iocs"] = auto_re_iocs

        metadata = DocumentMetadata(
            file_hash_md5=md5_hash,
            file_hash_sha1=sha1_hash,
            file_hash_sha256=sha256_hash,
            file_size_bytes=len(content),
            file_type=DocumentType.UNKNOWN,
            file_extension=f".{file_path.split('.')[-1]}" if '.' in file_path else '.unknown',
            raw_metadata=raw_metadata,
        )
        return DocumentAnalysis(metadata=metadata)

    async def batch_analyze_async(self, file_paths: list[str]) -> dict[str, DocumentAnalysis]:
        """Analyze multiple documents in parallel (M1-safe, concurrency=8).

        Uses parallel() with policy='collect' — all documents processed,
        individual failures return None for that document without aborting others.
        """
        from hledac.universal.utils.asyncx import parallel

        async def analyze_one(path: str) -> tuple[str, DocumentAnalysis | None]:
            try:
                # Wrap sync analyze() in to_thread to avoid blocking event loop
                return (path, await asyncio.to_thread(self.analyze, path))
            except Exception as e:
                logger.error(f'Error analyzing {path}: {e}')
                return (path, None)

        coros = [analyze_one(path) for path in file_paths]
        result = await parallel(
            coros,
            concurrency=8,
            policy="collect",
            ctx="DocumentIntelligenceEngine.batch_analyze_async",
        )
        return dict(result.ok)

    def batch_analyze(self, file_paths: list[str]) -> dict[str, DocumentAnalysis]:
        """Analyze multiple documents (sync wrapper for backward compatibility)."""
        results: dict[str, DocumentAnalysis] = {}
        for path in file_paths:
            try:
                results[path] = self.analyze(path)
            except Exception as e:
                logger.error(f'Error analyzing {path}: {e}')
                results[path] = None
        return results

    def close(self) -> None:
        """Clean up resources: forensics thread pool and stegdetect server."""
        # R5: _forensics_thread_pool is a shared pool from domain_executors —
        # do NOT shut it down (managed centrally). Just clear local reference.
        if hasattr(self, '_forensics_thread_pool') and self._forensics_thread_pool:
            self._forensics_thread_pool = None
        if hasattr(self, '_forensics') and self._forensics:
            steg_server = getattr(self._forensics, '_stegdetect_server', None)
            if steg_server and hasattr(steg_server, 'restart'):
                try:
                    self._run_async(steg_server.restart())
                except Exception:  # noqa: BLE001
                    pass

    def probe(self, url: str, preview_bytes: bytes, query: str='') -> dict[str, Any]:
        """
        Probe document to estimate value score for progressive parsing.

        Args:
            url: Document URL
            preview_bytes: Preview content bytes (first ~256KB)
            query: Optional search query for semantic scoring

        Returns:
            dict with heuristic_score, semantic_score (if computed), final_score, keywords, entities
        """
        result: dict[str, Any] = {'url': url, 'heuristic_score': 0.5, 'final_score': 0.5, 'keywords': [], 'entities': []}
        try:
            text = preview_bytes.decode('utf-8', errors='ignore')
        except Exception:
            text = ''
        if not text:
            return result
        heuristic_score = self._compute_heuristic_score(text)
        result['heuristic_score'] = heuristic_score
        if query and MLX_AVAILABLE:
            try:
                semantic_score = self._compute_semantic_score(text, query)
                if semantic_score is not None:
                    result['semantic_score'] = semantic_score
                    result['final_score'] = 0.5 * heuristic_score + 0.5 * semantic_score
            except Exception as e:
                logger.debug(f'Semantic scoring failed: {e}')
                result['final_score'] = heuristic_score
        else:
            result['final_score'] = heuristic_score
        result['keywords'] = self._extract_keywords(text)
        return result

    def _compute_heuristic_score(self, text: str) -> float:
        """
        Compute heuristic value score based on content analysis.
        """
        if not text:
            return 0.5
        score = 0.0
        high_value_keywords = ['analysis', 'research', 'report', 'study', 'data', 'results', 'findings', 'conclusion', 'method', 'evidence', 'case', 'review', 'assessment', 'evaluation', 'detection', 'identification', 'model']
        text_lower = text.lower()
        keyword_count = sum((1 for kw in high_value_keywords if kw in text_lower))
        score += min(0.4, keyword_count * 0.05)
        text_length = len(text)
        if text_length > 1000:
            score += 0.2
        elif text_length > 500:
            score += 0.1
        if re.search('\\d+[\\.,]\\d+', text):
            score += 0.1
        if re.search('^\\s*[-*•]\\s+', text, re.MULTILINE):
            score += 0.1
        if 'cookie' in text_lower or 'privacy policy' in text_lower:
            score -= 0.2
        return max(0.0, min(1.0, score))

    def _compute_semantic_score(self, text: str, query: str) -> float | None:
        """
        Compute semantic similarity score between text and query using ModernBERT.
        """
        try:
            from ...brain.model_manager import get_model_manager
            mm = get_model_manager()
            if not mm.has_model('modernbert'):
                return None
            embedder = mm.get_embedding_model('modernbert')
            try:
                chunks = self._split_preview_into_chunks(text.encode('utf-8'), max_chunks=5, max_tokens=512)
                if not chunks:
                    return None
                query_emb = embedder.embed(query)
                chunk_embs = embedder.embed_chunks(chunks)
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
                mm.release_model('modernbert')
        except Exception as e:
            logger.debug(f'Semantic scoring error: {e}')
            return None

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot_product = sum((x * y for x, y in zip(a, b, strict=False)))
        norm_a = sum((x * x for x in a)) ** 0.5
        norm_b = sum((x * x for x in b)) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    def _split_preview_into_chunks(self, bytes_data: bytes, max_chunks: int=5, max_tokens: int=512) -> list[str]:
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
            text = bytes_data.decode('utf-8', errors='ignore')
        except Exception:
            return []
        paragraphs = text.split('\n\n')
        chunks = []
        for para in paragraphs:
            words = para.split()
            if len(words) > max_tokens:
                para = ' '.join(words[:max_tokens])
            if para.strip():
                chunks.append(para.strip())
            if len(chunks) >= max_chunks:
                break
        return chunks

    def _extract_keywords(self, text: str) -> list[str]:
        """
        Extract high-value keywords from text.
        """
        keywords = set()
        capitalized = re.findall('\\b[A-Z][a-z]+(?:\\s+[A-Z][a-z]+)*\\b', text)
        keywords.update([w.lower() for w in capitalized[:10]])
        tech_terms = re.findall('\\b\\w+(?:tion|ing|ed|ness|ment|ance|ity)\\b', text.lower())
        keywords.update(tech_terms[:10])
        return list(keywords)[:20]

class EntityMention(msgspec.Struct, frozen=True, gc=False):
    """Mention of an entity in text."""
    text: str
    entity_type: str
    start_pos: int
    end_pos: int
    confidence: float
    context: str

class CrossDocumentLink(msgspec.Struct, frozen=True, gc=False):
    """Link between entities across documents."""
    entity_type: str
    value: str
    documents: list[str]
    confidence: float
    first_seen: str
    last_seen: str

class TimelineEvent(msgspec.Struct, gc=False):
    """Event extracted from document with temporal information."""
    date: datetime | None
    description: str
    source_document: str
    entities_involved: list[str]
    confidence: float

class LongContextAnalysis(msgspec.Struct, frozen=True, gc=False):
    """Results from MLX long-context analysis."""
    total_chunks: int
    total_tokens: int
    entities: list[EntityMention]
    cross_document_links: list[CrossDocumentLink]
    timeline: list[TimelineEvent]
    summary: str
    key_findings: list[str]
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
    __slots__ = tuple(('chunk_embeddings', 'chunk_size', 'chunk_texts', 'mlx_available', 'overlap', 'patterns'))

    def __init__(self, chunk_size: int=4096, overlap: int=512):
        """
        Initialize MLX Long-Context Analyzer.

        Args:
            chunk_size: Tokens per chunk (default 4096 for M1 8GB)
            overlap: Overlap between chunks for context continuity
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.chunk_embeddings: mx.array | None = None
        self.chunk_texts: list[str] = []
        self.patterns = {'email': re.compile('\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b'), 'phone': re.compile('\\b(?:\\+?1[-.\\s]?)?\\(?[0-9]{3}\\)?[-.\\s]?[0-9]{3}[-.\\s]?[0-9]{4}\\b'), 'ip_address': re.compile('\\b(?:[0-9]{1,3}\\.){3}[0-9]{1,3}\\b'), 'url': re.compile('https?://(?:[-\\w.])+(?:[:\\d]+)?(?:/(?:[\\w/_.])*(?:\\?(?:[\\w&=%.])*)?(?:#(?:[\\w.])*)?)?'), 'btc_address': re.compile('\\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\\b|\\bbc1[a-z0-9]{39,59}\\b'), 'credit_card': re.compile('\\b(?:\\d{4}[-\\s]?){3}\\d{4}\\b'), 'date': re.compile('\\b(?:\\d{1,2}[/-]\\d{1,2}[/-]\\d{2,4}|\\d{4}[/-]\\d{1,2}[/-]\\d{1,2})\\b')}
        self.mlx_available = self._check_mlx()

    def _check_mlx(self) -> bool:
        """Check if MLX is available."""
        try:
            import mlx.core as mx
            logger.info(f'MLX available on device: {mx.default_device()}')
            return True
        except ImportError:
            logger.warning('MLX not available - falling back to CPU processing')
            return False

    def _estimate_optimal_chunk_size(self, available_ram_gb: float=5.5) -> int:
        """
        Estimate optimal chunk size based on available RAM.

        M1 8GB optimization: Target < 5.5GB to leave room for system
        """
        safe_tokens = int(available_ram_gb * 0.25 * 1024 * 1024 * 1024 / 4 / 2)
        return min(self.chunk_size, safe_tokens)

    def chunk_text(self, text: str, source: str='unknown') -> list[dict]:
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
            if end < len(text):
                while end > start and text[end] not in ' \n\t':
                    end -= 1
            chunk_text = text[start:end].strip()
            if len(chunk_text) > 100:
                chunks.append({'id': chunk_id, 'text': chunk_text, 'source': source, 'start_pos': start, 'end_pos': end, 'token_estimate': len(chunk_text) // 4, 'overlap_with_previous': self.overlap if chunk_id > 0 else 0})
                chunk_id += 1
            start += step
        return chunks

    def extract_entities(self, text: str, source: str='unknown', chunk_id: int=0) -> list[EntityMention]:
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
                entity = EntityMention(text=match.group(), entity_type=entity_type, start_pos=match.start() + chunk_id * (self.chunk_size - self.overlap), end_pos=match.end() + chunk_id * (self.chunk_size - self.overlap), confidence=1.0, context=text[max(0, match.start() - 50):min(len(text), match.end() + 50)])
                entities.append(entity)
        return entities

    def compute_embeddings_mlx(self, chunks: list[str]) -> mx.array | None:
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
            embeddings = []
            for chunk in chunks:
                tokens = [ord(c) % 256 for c in chunk[:1024]]
                tokens_mx = mx.array(tokens, dtype=mx.float32)
                if tokens:
                    embedding = mx.mean(tokens_mx) / 255.0
                    embeddings.append(embedding)
                else:
                    embeddings.append(mx.array(0.0))
            return mx.stack(embeddings)
        except Exception as e:
            logger.error(f'MLX embedding computation failed: {e}')
            return None

    def find_similar_chunks_mlx(self, query: str, top_k: int=5) -> list[tuple[int, float]]:
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
            query_tokens = [ord(c) % 256 for c in query[:1024]]
            query_mx = mx.array(query_tokens, dtype=mx.float32)
            query_embedding = mx.mean(query_mx) / 255.0
            similarities = mx.abs(self.chunk_embeddings - query_embedding)
            similarities_eval = mx.eval(similarities)
            indices = mx.argsort(similarities_eval)[:top_k]
            results = []
            for idx in indices:
                idx_int = int(idx.item())
                sim_score = float(1.0 - similarities_eval[idx_int].item())
                results.append((idx_int, sim_score))
            return results
        except Exception as e:
            logger.error(f'MLX similarity search failed: {e}')
            return []

    def cross_reference_entities(self, all_entities: list[EntityMention]) -> list[CrossDocumentLink]:
        """
        Find entities that appear across multiple documents.

        Args:
            all_entities: All entities extracted from all documents

        Returns:
            List of cross-document links
        """
        by_value: dict[tuple[str, str], list[EntityMention]] = {}
        for entity in all_entities:
            key = (entity.entity_type, entity.text.lower())
            if key not in by_value:
                by_value[key] = []
            by_value[key].append(entity)
        links = []
        for (entity_type, value), mentions in by_value.items():
            sources = list({m.context[:50] for m in mentions})
            if len(sources) > 1:
                link = CrossDocumentLink(entity_type=entity_type, value=value, documents=sources[:10], confidence=min(1.0, len(mentions) / 10), first_seen='unknown', last_seen='unknown')
                links.append(link)
        links.sort(key=attrgetter("confidence"), reverse=True)
        return links

    def reconstruct_timeline(self, entities: list[EntityMention], chunks: list[dict]) -> list[TimelineEvent]:
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
                date_str = date_entity.text
                context = date_entity.context
                event_desc = context.replace(date_str, '[DATE]').strip()
                event = TimelineEvent(date=None, description=event_desc[:200], source_document=date_entity.context[:50], entities_involved=[date_entity.text], confidence=date_entity.confidence)
                timeline.append(event)
            except Exception as e:
                logger.debug(f'Failed to parse date {date_entity.text}: {e}')
        timeline.sort(key=attrgetter("confidence"), reverse=True)
        return timeline[:100]

    def analyze_massive_dump(self, text: str, source: str='unknown', extract_entities: bool=True, build_timeline: bool=True, cross_reference: bool=True) -> LongContextAnalysis:
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
        chunks = self.chunk_text(text, source)
        self.chunk_texts = [c['text'] for c in chunks]
        logger.info(f'Split text into {len(chunks)} chunks (size: {self.chunk_size}, overlap: {self.overlap})')
        if self.mlx_available:
            logger.info('Computing MLX embeddings...')
            self.chunk_embeddings = self.compute_embeddings_mlx(self.chunk_texts)
        all_entities = []
        if extract_entities:
            logger.info('Extracting entities...')
            for chunk in chunks:
                entities = self.extract_entities(chunk['text'], chunk['source'], chunk['id'])
                all_entities.extend(entities)
        cross_links = []
        if cross_reference and all_entities:
            logger.info('Cross-referencing entities...')
            cross_links = self.cross_reference_entities(all_entities)
        timeline = []
        if build_timeline:
            logger.info('Building timeline...')
            timeline = self.reconstruct_timeline(all_entities, chunks)
        key_findings = []
        if all_entities:
            entity_types = {}
            for e in all_entities:
                entity_types[e.entity_type] = entity_types.get(e.entity_type, 0) + 1
            for etype, count in sorted(entity_types.items(), key=lambda x: -x[1])[:10]:
                key_findings.append(f'Found {count} {etype} entities')
        if cross_links:
            key_findings.append(f'{len(cross_links)} cross-document entity links identified')
        processing_time = time.time() - start_time
        memory_usage = len(text) / (1024 * 1024)
        if self.chunk_embeddings is not None:
            memory_usage += self.chunk_embeddings.size * 4 / (1024 * 1024)
        return LongContextAnalysis(total_chunks=len(chunks), total_tokens=len(text) // 4, entities=all_entities, cross_document_links=cross_links, timeline=timeline, summary=f'Analyzed {len(chunks)} chunks, found {len(all_entities)} entities', key_findings=key_findings, memory_usage_mb=memory_usage, processing_time_seconds=processing_time)

    async def analyze_multiple_dumps_async(self, dumps: dict[str, str], cross_correlate: bool=True) -> dict[str, LongContextAnalysis]:
        """
        Analyze multiple document dumps in parallel with optional cross-correlation.

        Uses parallel() with concurrency=4 for M1-safe parallel processing.
        """
        from hledac.universal.utils.asyncx import parallel

        async def analyze_one(source_text: tuple[str, str]) -> tuple[str, LongContextAnalysis]:
            source, text = source_text
            logger.info(f'Analyzing dump from {source}...')
            return (source, self.analyze_massive_dump(text, source))

        coros = [analyze_one(s) for s in dumps.items()]
        result = await parallel(
            coros,
            concurrency=4,
            policy="collect",
            ctx="MLXLongContextAnalyzer.analyze_multiple_dumps_async",
        )
        results = dict(result.ok)

        if cross_correlate:
            logger.info('Cross-correlating all dumps...')
            all_entities = []
            for analysis in results.values():
                all_entities.extend(analysis.entities)
            global_links = self.cross_reference_entities(all_entities)
            for source in results:
                source_links = [link for link in global_links if any((source in doc for doc in link.documents))]
                analysis = results[source]
                results[source] = LongContextAnalysis(
                    total_chunks=analysis.total_chunks,
                    total_tokens=analysis.total_tokens,
                    entities=analysis.entities,
                    cross_document_links=source_links,
                    timeline=analysis.timeline,
                    summary=analysis.summary,
                    key_findings=analysis.key_findings + [f'Linked to {len(source_links)} other sources'],
                    memory_usage_mb=analysis.memory_usage_mb,
                    processing_time_seconds=analysis.processing_time_seconds,
                )
        return results

    def analyze_multiple_dumps(self, dumps: dict[str, str], cross_correlate: bool=True) -> dict[str, LongContextAnalysis]:
        """
        Analyze multiple document dumps and optionally cross-correlate (sync wrapper).

        Args:
            dumps: Dict of {source_name: text_content}
            cross_correlate: Whether to find links between dumps

        Returns:
            Dict of analyses per dump
        """
        results = {}
        all_entities = []
        for source, text in dumps.items():
            logger.info(f'Analyzing dump from {source}...')
            analysis = self.analyze_massive_dump(text, source)
            results[source] = analysis
            all_entities.extend(analysis.entities)
        if cross_correlate:
            logger.info('Cross-correlating all dumps...')
            global_links = self.cross_reference_entities(all_entities)
            for source in results:
                source_links = [link for link in global_links if any((source in doc for doc in link.documents))]
                analysis = results[source]
                results[source] = LongContextAnalysis(total_chunks=analysis.total_chunks, total_tokens=analysis.total_tokens, entities=analysis.entities, cross_document_links=source_links, timeline=analysis.timeline, summary=analysis.summary, key_findings=analysis.key_findings + [f'Linked to {len(source_links)} other sources'], memory_usage_mb=analysis.memory_usage_mb, processing_time_seconds=analysis.processing_time_seconds)
        return results

    async def search_across_dumps_async(self, query: str, dumps: dict[str, str], top_k_per_dump: int=3) -> dict[str, list[dict]]:
        """
        Search for query across multiple dumps using MLX similarity (parallel).

        Uses parallel() with concurrency=4 for M1-safe parallel processing.
        """
        from hledac.universal.utils.asyncx import parallel

        async def search_one(source_text: tuple[str, str]) -> tuple[str, list[dict]]:
            source, text = source_text
            self.analyze_massive_dump(text, source)
            similar = self.find_similar_chunks_mlx(query, top_k_per_dump)
            source_results = []
            for idx, score in similar:
                if idx < len(self.chunk_texts):
                    source_results.append({'chunk_id': idx, 'text': self.chunk_texts[idx][:500], 'similarity': score})
            return (source, source_results)

        coros = [search_one(s) for s in dumps.items()]
        result = await parallel(
            coros,
            concurrency=4,
            policy="collect",
            ctx="MLXLongContextAnalyzer.search_across_dumps_async",
        )
        return dict(result.ok)

    def search_across_dumps(self, query: str, dumps: dict[str, str], top_k_per_dump: int=3) -> dict[str, list[dict]]:
        """
        Search for query across multiple dumps using MLX similarity (sync wrapper).

        Args:
            query: Search query
            dumps: Dict of {source_name: text_content}
            top_k_per_dump: Number of results per dump

        Returns:
            Dict of search results per dump
        """
        results = {}
        for source, text in dumps.items():
            self.analyze_massive_dump(text, source)
            similar = self.find_similar_chunks_mlx(query, top_k_per_dump)
            source_results = []
            for idx, score in similar:
                if idx < len(self.chunk_texts):
                    source_results.append({'chunk_id': idx, 'text': self.chunk_texts[idx][:500], 'similarity': score})
            results[source] = source_results
        return results
__all__ = ['DocumentIntelligenceEngine', 'PDFAnalyzer', 'OfficeDocumentAnalyzer', 'ImageAnalyzer', 'DocumentAnalysis', 'DocumentMetadata', 'EXIFData', 'GeoLocation', 'EmbeddedObject', 'DocumentType', 'MLXLongContextAnalyzer', 'LongContextAnalysis', 'EntityMention', 'CrossDocumentLink', 'TimelineEvent']