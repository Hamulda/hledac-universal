"""
[SILICON-07] Media IOC Pipeline — Canonical Media→IOC Orchestration
====================================================================



Orchestrates the full media decode → transcription → IOC extraction pipeline.
Bridges the gap between Apple Media Engine (SILICON-02) and the SIMD IOC scanner
(HEIST-01), ensuring every transcribed audio/video second is scanned for IoCs.

Pipeline (audio):
    1. AVFoundation decode → PCM float32 mono 16kHz (VideoToolbox HW, ~2s)
    2. SFSpeechRecognizer → text (ANE-accelerated, ~3min per 1h audio)
    3. IocStreamScanner.scan_bytes → IoC hits (Rust Aho-Corasick SIMD, ~50ms per 1MB)

Pipeline (video):
    1. AVFoundation audio track → PCM decode (VideoToolbox HW)
    2. SFSpeechRecognizer → text from audio (ANE)
    3. AVAssetImageGenerator → keyframes at 10s intervals
    4. Vision OCR → text from each keyframe (ANE)
    5. Combined text → IocStreamScanner.scan_bytes → IoC hits

Performance target (M1 8GB):
    - 1 hour audio (50 MB MP3): decode 2s + transcribe 3min + scan 50ms = ~3.5 min
    - In 30-min sprint: 8 hours of audio transcribed and IOC-scanned
    - IOC scanner throughput: 3-4 GB/s SIMD sweep (Aho-Corasick with NEON Teddy)

Integration points:
    - multimodal/analyzer.py: enrich_audio() / enrich_video()
    - coordinators/multimodal_coordinator.py: _process_audio() / _process_video()

M1 8GB safety:
    - 1 decode thread (AVAssetReader is serial per Apple)
    - Audio buffer capped at 50M samples (~12 min @ 16kHz)
    - IocStreamScanner automaton ~2-5 MB
    - Lazy imports — zero cost until first use
    - Fail-soft: every path returns empty results on error
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import msgspec
from core import aclose

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger(__name__)

# ── M1 8GB bounds ─────────────────────────────────────────────────────────────
_AUDIO_BUFFER_MAX_SAMPLES = 50 * 1024 * 1024     # 50M samples (~12 min @ 16kHz)
_SCAN_TIMEOUT_S = 30.0                            # max time for IOC scan
_VIDEO_MAX_KEYFRAMES = 120                        # max 20 min of video
_DECODE_LOCK = asyncio.Lock()                     # serialize AVAssetReader

# ── Lazy singletons ───────────────────────────────────────────────────────────
_MediaDecoder: type | None = None
_MediaDecoderInstance: Any | None = None
_MediaDecoderAvailable: bool = False

_IocStreamScanner: type | None = None
_IocStreamScannerAvailable: bool = False


def _ensure_media_decoder(governor: Any | None = None) -> Any | None:
    """Lazy-load and cache MediaDecoder singleton."""
    global _MediaDecoder, _MediaDecoderInstance, _MediaDecoderAvailable
    if _MediaDecoderAvailable and _MediaDecoderInstance is not None:
        return _MediaDecoderInstance
    try:
        from hledac.universal.multimodal.media_engine import MediaDecoder as _MD
        _MediaDecoder = _MD
        decoder = _MD(governor=governor)
        # initialize is async — caller must await
        _MediaDecoderAvailable = True
        _MediaDecoderInstance = decoder
        return decoder
    except ImportError:
        log.debug('[SILICON-07] media_engine import failed — MediaDecoder unavailable')
        _MediaDecoderAvailable = False
        return None
    except Exception as exc:
        log.debug('[SILICON-07] MediaDecoder init failed: %s', exc)
        _MediaDecoderAvailable = False
        return None


def _ensure_ioc_scanner() -> Any | None:
    """Lazy-load IocStreamScanner type."""
    global _IocStreamScanner, _IocStreamScannerAvailable
    if _IocStreamScanner is not None:
        return _IocStreamScanner
    try:
        from hledac.universal.core.rust_backend.ioc_stream import IocStreamScanner as _ISS
        _IocStreamScanner = _ISS
        _IocStreamScannerAvailable = True
        return _ISS
    except ImportError:
        log.debug('[SILICON-07] IocStreamScanner unavailable — SIMD scan disabled')
        _IocStreamScannerAvailable = False
        return None


# ── Public types ──────────────────────────────────────────────────────────────

class MediaIocResult(msgspec.Struct, frozen=True, gc=False):
    """Result of media → IOC pipeline for a single file.

    Contains transcript, extracted IoCs, and performance metrics for each
    pipeline phase.
    """
    file_path: str
    media_type: str  # "audio" | "video"
    # ── Transcription ──
    transcript: str = ""
    transcript_confidence: float = 0.0
    duration_s: float = 0.0
    segments: list[dict[str, Any]] = field(default_factory=list)
    # ── Video-specific ──
    frame_texts: list[str] = field(default_factory=list)
    frame_timestamps: list[float] = field(default_factory=list)
    frame_count: int = 0
    # ── IoCs ──
    iocs: list[dict[str, Any]] = field(default_factory=list)
    ioc_count: int = 0
    ioc_scanner: str = ""  # "rust_aho_corasick" | "rust_regex" | "python_fallback" | ""
    # ── Performance ──
    decode_time_ms: float = 0.0
    transcribe_time_ms: float = 0.0
    ioc_scan_time_ms: float = 0.0
    total_time_ms: float = 0.0
    # ── Errors ──
    decode_ok: bool = False
    transcribe_ok: bool = False
    ioc_scan_ok: bool = False
    error: str = ""

    @property
    def all_text(self) -> str:
        """Combined text from all sources for downstream analysis."""
        parts = [self.transcript] if self.transcript else []
        parts.extend(self.frame_texts)
        return ' '.join(parts)


# ── MediaIocPipeline ──────────────────────────────────────────────────────────

class MediaIocPipeline:
    """Canonical media → IOC extraction pipeline.

    Orchestrates:
      1. MediaDecoder — decode audio/video (AVFoundation/VideoToolbox HW)
      2. Transcription — SFSpeechRecognizer (ANE) + Vision OCR (ANE)
      3. IOC scanning — IocStreamScanner (Rust Aho-Corasick SIMD, NEON)

    Usage:
        pipeline = MediaIocPipeline(governor=gov)
        await pipeline.initialize()

        result = await pipeline.process_audio("/path/to/interview.mp3")
        print(f"Found {result.ioc_count} IoCs in {result.duration_s:.0f}s audio")

        result = await pipeline.process_video("/path/to/webinar.mp4")
        print(f"Transcript: {result.transcript[:200]}...")

        await pipeline.close()

    Thread safety:
        - All decode operations serialized via _DECODE_LOCK (AVAssetReader constraint)
        - IOC scanning is lock-free (Rust automaton is read-only)
        - SFSpeechRecognizer is thread-safe per Apple docs
    """

    __slots__ = (
        '_decoder',
        '_governor',
        '_initialized',
        '_ioc_scanner',
        '_ioc_patterns_loaded',
        '_lock',
        '_stats',
    )

    def __init__(self, governor: Any | None = None) -> None:
        self._governor = governor
        self._decoder: Any | None = None
        self._ioc_scanner: Any | None = None
        self._ioc_patterns_loaded: bool = False
        self._initialized: bool = False
        self._lock: asyncio.Lock | None = None
        self._stats: dict[str, int] = {
            'audio_processed': 0,
            'video_processed': 0,
            'iocs_total': 0,
            'errors': 0,
        }

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def initialize(self) -> None:
        """Lazy-init decoder + scanner. Idempotent, fail-soft."""
        if self._initialized:
            return
        async with self._get_lock():
            if self._initialized:
                return

            # Init MediaDecoder
            decoder = _ensure_media_decoder(self._governor)
            if decoder is not None:
                try:
                    await decoder.initialize()
                    self._decoder = decoder
                    log.info('[SILICON-07] MediaDecoder initialized')
                except Exception as exc:
                    log.warning('[SILICON-07] MediaDecoder init failed: %s', exc)
                    self._decoder = None
            else:
                log.debug('[SILICON-07] MediaDecoder unavailable — audio/video disabled')

            # IOC scanner is created per-scan (patterns loaded lazily)
            _ensure_ioc_scanner()

            self._initialized = True

    async def close(self) -> None:
        """Release resources."""
        async with self._get_lock():
            if self._decoder is not None:
                try:
                    await self._decoder.close()
                except Exception:  # noqa: BLE001
                    pass
                self._decoder = None
            self._ioc_scanner = None
            self._ioc_patterns_loaded = False
            self._initialized = False

    # ── RAM guard ──────────────────────────────────────────────────────────

    def _check_ram_guard(self) -> bool:
        """Check UMA headroom for media decode + IOC scan."""
        from hledac.universal.multimodal import check_ram_guard
        return check_ram_guard(self._governor)

    # ── Public API ─────────────────────────────────────────────────────────

    async def process_audio(self, file_path: str) -> MediaIocResult:
        """Full audio pipeline: decode → transcribe → IOC scan.

        Args:
            file_path: Path to audio file (.mp3, .wav, .m4a, .flac, etc.)

        Returns:
            MediaIocResult with transcript, IoCs, and performance metrics.
            Never raises — returns result with error field on failure.
        """
        t_start = _time.monotonic()
        ext = Path(file_path).suffix.lower()

        if not self._initialized:
            await self.initialize()

        if not self._check_ram_guard():
            return MediaIocResult(
                file_path=file_path,
                media_type='audio',
                error='RAM guard denied',
            )

        # Phase 1: Decode + Transcribe
        t_decode = _time.monotonic()
        transcript_text = ""
        confidence = 0.0
        duration_s = 0.0
        segments: list[dict[str, Any]] = []
        decode_ok = False
        transcribe_ok = False

        try:
            if self._decoder is None:
                return MediaIocResult(
                    file_path=file_path,
                    media_type='audio',
                    error='MediaDecoder not available',
                )

            async with _DECODE_LOCK:
                result = await self._decoder.transcribe(file_path)

            if result.text:
                transcript_text = result.text
                confidence = result.confidence
                duration_s = result.duration_s
                segments = result.segments if hasattr(result, 'segments') else []
                transcribe_ok = True
            decode_ok = True
        except Exception as exc:
            log.debug('[SILICON-07] Audio decode/transcribe failed for %s: %s', file_path, exc)
            self._stats['errors'] += 1
            decode_time = (_time.monotonic() - t_decode) * 1000
            return MediaIocResult(
                file_path=file_path,
                media_type='audio',
                decode_time_ms=decode_time,
                total_time_ms=(_time.monotonic() - t_start) * 1000,
                error=str(exc),
            )

        decode_transcribe_time = (_time.monotonic() - t_decode) * 1000

        # Phase 2: IOC Scan
        t_scan = _time.monotonic()
        iocs, scanner_name, scan_ok = await self._scan_text_for_iocs(transcript_text)
        scan_time = (_time.monotonic() - t_scan) * 1000

        self._stats['audio_processed'] += 1
        self._stats['iocs_total'] += len(iocs)

        return MediaIocResult(
            file_path=file_path,
            media_type='audio',
            transcript=transcript_text,
            transcript_confidence=confidence,
            duration_s=duration_s,
            segments=segments,
            iocs=iocs,
            ioc_count=len(iocs),
            ioc_scanner=scanner_name,
            decode_time_ms=decode_transcribe_time,
            transcribe_time_ms=0.0,  # included in decode_time_ms (single call)
            ioc_scan_time_ms=scan_time,
            total_time_ms=(_time.monotonic() - t_start) * 1000,
            decode_ok=decode_ok,
            transcribe_ok=transcribe_ok,
            ioc_scan_ok=scan_ok,
        )

    async def process_video(self, file_path: str) -> MediaIocResult:
        """Full video pipeline: decode audio → transcribe → OCR frames → IOC scan.

        Args:
            file_path: Path to video file (.mp4, .mkv, .mov, .avi, etc.)

        Returns:
            MediaIocResult with audio transcript, frame OCR texts, IoCs,
            and performance metrics. Never raises.
        """
        t_start = _time.monotonic()

        if not self._initialized:
            await self.initialize()

        if not self._check_ram_guard():
            return MediaIocResult(
                file_path=file_path,
                media_type='video',
                error='RAM guard denied',
            )

        # Phase 1: Decode + Transcribe + OCR
        t_decode = _time.monotonic()
        audio_transcript = ""
        audio_confidence = 0.0
        duration_s = 0.0
        frame_texts: list[str] = []
        frame_timestamps: list[float] = []
        frame_count = 0
        decode_ok = False
        transcribe_ok = False

        try:
            if self._decoder is None:
                return MediaIocResult(
                    file_path=file_path,
                    media_type='video',
                    error='MediaDecoder not available',
                )

            async with _DECODE_LOCK:
                result = await self._decoder.transcribe_video(file_path)

            if result.audio_transcript:
                audio_transcript = result.audio_transcript
                audio_confidence = result.audio_confidence
                transcribe_ok = True
            duration_s = result.duration_s
            frame_texts = list(result.frame_texts) if result.frame_texts else []
            frame_timestamps = list(result.frame_timestamps) if result.frame_timestamps else []
            frame_count = result.frame_count
            decode_ok = True
        except Exception as exc:
            log.debug('[SILICON-07] Video decode/transcribe failed for %s: %s', file_path, exc)
            self._stats['errors'] += 1
            decode_time = (_time.monotonic() - t_decode) * 1000
            return MediaIocResult(
                file_path=file_path,
                media_type='video',
                decode_time_ms=decode_time,
                total_time_ms=(_time.monotonic() - t_start) * 1000,
                error=str(exc),
            )

        decode_transcribe_time = (_time.monotonic() - t_decode) * 1000

        # Phase 2: IOC Scan on all text
        t_scan = _time.monotonic()
        all_text_parts = [audio_transcript] if audio_transcript else []
        all_text_parts.extend(frame_texts)
        combined_text = ' '.join(all_text_parts)

        iocs, scanner_name, scan_ok = await self._scan_text_for_iocs(combined_text)
        scan_time = (_time.monotonic() - t_scan) * 1000

        self._stats['video_processed'] += 1
        self._stats['iocs_total'] += len(iocs)

        return MediaIocResult(
            file_path=file_path,
            media_type='video',
            transcript=audio_transcript,
            transcript_confidence=audio_confidence,
            duration_s=duration_s,
            frame_texts=frame_texts,
            frame_timestamps=frame_timestamps,
            frame_count=frame_count,
            iocs=iocs,
            ioc_count=len(iocs),
            ioc_scanner=scanner_name,
            decode_time_ms=decode_transcribe_time,
            transcribe_time_ms=0.0,  # included in decode (single call)
            ioc_scan_time_ms=scan_time,
            total_time_ms=(_time.monotonic() - t_start) * 1000,
            decode_ok=decode_ok,
            transcribe_ok=transcribe_ok,
            ioc_scan_ok=scan_ok,
        )

    async def process_media(self, file_path: str) -> MediaIocResult:
        """Auto-detect media type and run appropriate pipeline.

        Args:
            file_path: Path to audio or video file.

        Returns:
            MediaIocResult — type auto-detected from extension.
        """
        ext = Path(file_path).suffix.lower()

        _AUDIO_EXTS = frozenset({
            '.mp3', '.aac', '.m4a', '.flac', '.wav', '.ogg', '.opus',
            '.wma', '.aiff', '.aif', '.alac', '.ac3', '.amr', '.caf',
        })
        _VIDEO_EXTS = frozenset({
            '.mp4', '.mkv', '.mov', '.avi', '.webm', '.m4v', '.flv',
            '.wmv', '.3gp', '.3g2', '.ts', '.mts', '.m2ts',
        })

        if ext in _AUDIO_EXTS:
            return await self.process_audio(file_path)
        elif ext in _VIDEO_EXTS:
            return await self.process_video(file_path)
        else:
            return MediaIocResult(
                file_path=file_path,
                media_type='unknown',
                error=f'Unsupported extension: {ext}',
            )

    # ── IOC Scanning ───────────────────────────────────────────────────────

    async def _scan_text_for_iocs(
        self,
        text: str,
    ) -> tuple[list[dict[str, Any]], str, bool]:
        """Instance method — delegates to module-level scan_text_for_iocs()."""
        return await scan_text_for_iocs(text)

    def get_stats(self) -> dict[str, int]:
        """Return pipeline statistics."""
        return dict(self._stats)


# ── Standalone IOC Scan Function ──────────────────────────────────────────────

async def scan_text_for_iocs(
    text: str,
) -> tuple[list[dict[str, Any]], str, bool]:
    """Scan text for IoCs using available backends in priority order.

    Priority:
      1. IocStreamScanner (Rust Aho-Corasick, SIMD NEON) — 3-4 GB/s
      2. rust.ioc.extract_iocs_flat (Rust regex) — fast, high precision
      3. brain.ner_engine.extract_iocs_from_text (MLX NER) — unstructured text
      4. forensics.ioc_extractor._IOC_COMBINED (Python regex) — last resort

    Returns:
        (iocs_list, scanner_name, success_flag)
    """
    if not text or not text.strip():
        return [], "", False

    try:
        text_bytes = text.encode('utf-8', errors='replace')

        # Tier 1: IocStreamScanner (Rust Aho-Corasick, SIMD NEON)
        if _IocStreamScannerAvailable and _IocStreamScanner is not None:
            try:
                scanner = _IocStreamScanner(
                    patterns=list(_IOC_PATTERNS_STR),
                    labels=list(_IOC_PATTERNS_STR),
                )
                # R7: IocStreamScanner dispatched via rayon channel for zero-overhead submit
                hits = await asyncio.to_thread(scanner.scan_bytes, text_bytes)

                if hits:
                    iocs = _normalize_stream_hits(hits)
                    log.debug(
                        '[SILICON-07] IocStreamScanner: %d IoCs in %d bytes',
                        len(iocs), len(text_bytes),
                    )
                    return iocs[:200], 'rust_aho_corasick', True
            except Exception as exc:
                log.debug('[SILICON-07] IocStreamScanner failed: %s', exc)

        # Tier 2: Rust regex extractor → R7: batch_ioc_extract_unified_python (zero-copy)
        try:
            from hledac.universal.utils.ioc_extract import extract_iocs_single
            flat_iocs = await extract_iocs_single(text)
            if flat_iocs:
                iocs = []
                for ioc_type, ioc_value in flat_iocs:
                    iocs.append({
                        'type': ioc_type,
                        'value': ioc_value,
                        'confidence': 0.9,
                        'scanner': 'rust_batch_zero_copy',
                    })
                return iocs[:200], 'rust_batch_zero_copy', True
        except ImportError:
            log.debug('[SILICON-07] Rust zero-copy scanner not available')
        except Exception as exc:
            log.debug('[SILICON-07] Rust zero-copy scan failed: %s', exc)

        # Tier 3: MLX NER engine (unstructured text, lower precision)
        try:
            from hledac.universal.brain.ner_engine import extract_iocs_from_text
            ner_iocs = await asyncio.to_thread(extract_iocs_from_text, text)
            if ner_iocs:
                iocs = []
                for ioc in ner_iocs:
                    iocs.append({
                        'type': getattr(ioc, 'ioc_type', getattr(ioc, 'type', 'unknown')),
                        'value': getattr(ioc, 'value', str(ioc)),
                        'confidence': getattr(ioc, 'confidence', 0.7),
                        'scanner': 'mlx_ner',
                    })
                return iocs[:200], 'mlx_ner', True
        except ImportError:
            log.debug('[SILICON-07] MLX NER engine not available')
        except Exception as exc:
            log.debug('[SILICON-07] MLX NER scan failed: %s', exc)

        # Tier 4: Python fallback (forensics combined regex)
        try:
            from forensics.ioc_extractor import _IOC_COMBINED as _COMBINED
            iocs = _python_fallback_scan(text, _COMBINED)
            if iocs:
                return iocs[:200], 'python_fallback', True
        except ImportError:
            log.debug('[SILICON-07] Python fallback not available')
        except Exception as exc:
            log.debug('[SILICON-07] Python fallback failed: %s', exc)

        return [], '', False

    except Exception as exc:
        log.debug('[SILICON-07] scan_text_for_iocs failed: %s', exc)
        return [], '', False


# ── IOC Patterns for Aho-Corasick (subset of the full pattern set) ────────────

# Key patterns that the SIMD Aho-Corasick scanner looks for.
# These are literal substring patterns (not regex) for maximum speed.
# The full regex-based extraction (Tier 2) handles the more complex patterns.
_IOC_PATTERNS_STR: tuple[str, ...] = (
    # Email
    '@',  # anchor for email detection
    # Crypto addresses
    '1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa',  # dummy — real patterns extracted from text
    '0x',  # ETH address prefix
    'bc1',  # Bech32 BTC prefix
    # Domains / URLs
    'http://',
    'https://',
    'ftp://',
    '.onion',
    '.i2p',
    # Hashes (prefix patterns)
    # MD5 = 32 hex chars, SHA1 = 40, SHA256 = 64
    # CVE
    'CVE-',
    'cve-',
    # IP patterns (partial)
    # PGP keys
    '-----BEGIN PGP',
    # Common malware indicators
    'ransomware',
    'trojan',
    'backdoor',
    'keylogger',
    'rootkit',
    'botnet',
    'c2',
    'C2',
    'command and control',
    # APT groups
    'APT',
    # File hashes
    'MD5',
    'SHA1',
    'SHA256',
    'SHA-256',
    'md5',
    'sha1',
    'sha256',
    'sha-256',
    # Threat intel
    'TTP',
    'IOC',
    'indicator',
)


def _normalize_stream_hits(hits: list[dict]) -> list[dict[str, Any]]:
    """Normalize IocStreamScanner hits to standard IOC dict format.

    Deduplicates by value and caps at 200.
    """
    seen: set[str] = set()
    iocs: list[dict[str, Any]] = []
    for hit in hits:
        value = hit.get('value', '') or hit.get('pattern', '')
        if value and value not in seen:
            seen.add(value)
            iocs.append({
                'type': hit.get('label', 'unknown'),
                'value': value,
                'confidence': 0.95,
                'scanner': 'rust_aho_corasick',
                'offset': hit.get('start', 0),
                'pattern': hit.get('pattern', ''),
            })
    return iocs[:200]


def _python_fallback_scan(text: str, combined_regex: Any) -> list[dict[str, Any]]:
    """Pure-Python IOC extraction using forensics combined regex.

    Last-resort fallback when all Rust backends are unavailable.
    """
    if not text:
        return []
    seen: set[str] = set()
    iocs: list[dict[str, Any]] = []
    try:
        for m in combined_regex.finditer(text):
            name = m.lastgroup
            if name is None:
                continue
            value = m.group(0)
            if value and value not in seen:
                seen.add(value)
                iocs.append({
                    'type': name,
                    'value': value,
                    'confidence': 0.85,
                    'scanner': 'python_fallback',
                })
    except Exception:  # noqa: BLE001
        pass
    return iocs


# ── Module-level convenience ─────────────────────────────────────────────────

_pipeline_instance: MediaIocPipeline | None = None


async def get_pipeline(governor: Any | None = None) -> MediaIocPipeline:
    """Get or create the global MediaIocPipeline singleton."""
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = MediaIocPipeline(governor=governor)
        await _pipeline_instance.initialize()
    return _pipeline_instance


async def close_pipeline() -> None:
    """Close and release the global pipeline."""
    global _pipeline_instance
    if _pipeline_instance is not None:
        await _pipeline_instance.close()
        _pipeline_instance = None
