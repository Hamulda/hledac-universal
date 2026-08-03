"""
Multimodal module — Apple Silicon media pipeline (SILICON-02/07).

Shared helpers:
  - check_ram_guard(governor) — canonical M1 UMA headroom check
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def check_ram_guard(governor: Any | None = None) -> bool:
    """Check UMA headroom for heavy media operations (canonical, shared).

    Returns True if safe to proceed, False if memory is critical/emergency.
    This is the SINGLE source of truth — all callers in the multimodal
    package MUST use this instead of inlining their own check.

    Callers (all migrated):
      - MediaDecoder._check_ram_guard
      - MediaIocPipeline._check_ram_guard
      - MultimodalEnricher._can_run_heavy_vision
      - DocumentExtractor._check_ram_guard
      - EvidenceTriageCoordinator._check_ram_guard
    """
    if governor is None:
        return True
    try:
        try:
            if governor.is_critical():
                return False
        except AttributeError:
            pass
        try:
            if governor.is_emergency():
                return False
        except AttributeError:
            pass
        return True
    except Exception:
        return True  # fail-open


from .analyzer import DocumentExtractor, DocumentResult, MultimodalEnricher
from .fusion import MambaFusion, MobileCLIPFusion
from .media_engine import (  # [SILICON-02]
    MediaDecoder,
    MediaFormatInfo,
    TranscriptionResult,
    VideoTranscriptionResult,
    get_media_decoder,
    is_audio_file,
    is_media_file,
    is_video_file,
)
from .media_ioc_pipeline import (  # [SILICON-07]
    MediaIocPipeline,
    MediaIocResult,
    scan_text_for_iocs,
    get_pipeline,
    close_pipeline,
)
from .whisper_transcriber import (  # [SILICON-02b]
    TranscriptionRouter,
    EngineChoice,
    TranscriptionEngine,
    get_transcription_router,
    transcribe_audio,
    transcribe_and_extract_iocs,
)
from .vision_encoder import VisionEncoder

__all__ = [
    # Shared helpers
    "check_ram_guard",
    # Modules
    "VisionEncoder",
    "MambaFusion",
    "MobileCLIPFusion",
    "DocumentExtractor",
    "DocumentResult",
    "MultimodalEnricher",
    # [SILICON-02] Media Engine
    "MediaDecoder",
    "MediaFormatInfo",
    "TranscriptionResult",
    "VideoTranscriptionResult",
    "get_media_decoder",
    "is_audio_file",
    "is_media_file",
    "is_video_file",
    # [SILICON-07] Media IOC Pipeline
    "MediaIocPipeline",
    "MediaIocResult",
    "scan_text_for_iocs",
    "get_pipeline",
    "close_pipeline",
    # [SILICON-02b] Whisper.cpp Transcription Router
    "TranscriptionRouter",
    "EngineChoice",
    "TranscriptionEngine",
    "get_transcription_router",
    "transcribe_audio",
    "transcribe_and_extract_iocs",
]
