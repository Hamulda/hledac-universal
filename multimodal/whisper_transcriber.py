"""
multimodal/whisper_transcriber.py — Intelligent Transcription Router
====================================================================




SILICON-02b: Two-engine transcription with automatic routing.

Routes between:
  - SFSpeechRecognizer (SILICON-02): Apple's on-device ANE recognizer.
    Fastest, zero-RAM model, 60+ languages.
  - WhisperEngine (SILICON-02b): whisper.cpp + CoreML/ANE.
    99 languages, fully offline, fine-tunable.

Routing strategy (TranscriptionRouter.select_engine):
  1. Language check: if not in SFSpeechRecognizer's supported set → WhisperEngine
  2. Offline requirement: if HLEDAC_OFFLINE=1 → WhisperEngine
  3. Audio quality: noisy/domain-specific → WhisperEngine
  4. Default: SFSpeechRecognizer (fastest, lowest resource)

Architecture:
    TranscriptionRouter
    ├── select_engine(audio, lang, opts) → EngineChoice
    ├── transcribe(audio, **opts) → TranscriptionResult
    │   ├── Primary engine attempt
    │   ├── Fallback engine on failure
    │   └── Empty result (never raises)
    └── extract_iocs(result) → list[CanonicalFinding]
        └── Rust regex IOC extractor pipeline

M1 8GB bounds:
    - SFSpeechRecognizer: ~0 MB (ANE dedicated memory)
    - WhisperEngine tiny: ~70 MB peak (model + runtime)
    - Only one engine active at a time
    - Models cached on APFS, loaded on-demand

Usage:
    from hledac.universal.multimodal.whisper_transcriber import (
        TranscriptionRouter, transcribe_audio
    )
    router = TranscriptionRouter()
    result = await router.transcribe("podcast.mp3")
    # result.text, result.engine, result.iocs
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum, auto
from pathlib import Path
from typing import Any, Literal

import msgspec
from _core import aclose

logger = logging.getLogger(__name__)

# ─── Sandbox Integration ───────────────────────────────────────────────────────
# ADVERSARY-001: Unified sandbox integration via MediaSandboxCoordinator.
# Lazy-load to avoid circular dependency; media_sandbox.py is optional.
_coordinator: Any | None = None
_coordinator_available: bool | None = None


def _get_sandbox_coordinator():
    """
    Lazy-load MediaSandboxCoordinator singleton from media_sandbox.py.
    
    Returns (coordinator, is_available) tuple.
    Uses the unified coordinator for all sandbox operations including whisper.
    """
    global _coordinator, _coordinator_available
    if _coordinator_available is not None:
        return _coordinator, _coordinator_available
    
    _coordinator_available = False
    try:
        from hledac.universal.security.media_sandbox import (
            MediaSandboxCoordinator,
            SANDBOX_ENABLED,
    )
        _coordinator = MediaSandboxCoordinator(enabled=SANDBOX_ENABLED)
        _coordinator_available = True
        
        if _coordinator._seatbelt_available:
            logger.info(
                "[TranscriptionRouter] MediaSandboxCoordinator ready — "
                "whisper transcription will be sandboxed (ADVERSARY-001)"
    )
        else:
            logger.warning(
                "[TranscriptionRouter] Seatbelt unavailable — "
                "whisper will run without kernel isolation (ADVERSARY-001 RISK)"
    )
    except ImportError as exc:
        logger.warning(
            "[TranscriptionRouter] MediaSandboxCoordinator not available — "
            "whisper will run without sandbox isolation (ADVERSARY-001 RISK): %s",
            exc,
    )
        _coordinator = None
        _coordinator_available = False
    except Exception as exc:
        logger.warning(
            "[TranscriptionRouter] MediaSandboxCoordinator init failed — "
            "whisper will run without sandbox isolation (ADVERSARY-001 RISK): %s",
            exc,
    )
        _coordinator = None
        _coordinator_available = False
    
    return _coordinator, _coordinator_available

# ─── M1 8GB bounds ───────────────────────────────────────────────────────────

_MAX_AUDIO_FILE_BYTES = 100 * 1024 * 1024      # 100 MB
_TRANSCRIBE_TIMEOUT_S = 600.0                   # 10 min

# Languages where SFSpeechRecognizer needs network (prefer whisper offline)
_SFS_NETWORK_DEPENDENT_LANGUAGES: frozenset[str] = frozenset({
    "ar", "cs", "da", "el", "fi", "he", "hi", "hu", "id",
    "ms", "nb", "nl", "pl", "ro", "ru", "sk", "sv", "th",
    "tr", "uk", "vi",
})

# Languages SFSpeechRecognizer supports fully offline (on-device model)
_SFS_OFFLINE_LANGUAGES: frozenset[str] = frozenset({
    "en", "de", "fr", "es", "it", "pt", "ja", "ko", "zh",
    "yue", "ca", "hr",
})


# ─── Public types ────────────────────────────────────────────────────────────

class EngineChoice(Enum):
    """Which transcription engine to use."""
    SFS_SPEECH = auto()         # Apple SFSpeechRecognizer (SILICON-02)
    WHISPER_CPP = auto()        # whisper.cpp + CoreML (SILICON-02b)
    AUTO = auto()               # Let the router decide


class TranscriptionEngine(Enum):
    """Which engine actually performed the transcription."""
    SFS_SPEECH = "sfspeech"
    RUST_WHISPER = "rust_whisper"  # SILICON-02: Rust whisper with CoreML/ANE
    WHISPER_CPP = "whisper_cpp"    # Python whispercpp fallback
    NONE = "none"


class TranscriptionSegment(msgspec.Struct, frozen=True, gc=False):
    """Single transcribed segment."""
    start_s: float = 0.0
    end_s: float = 0.0
    text: str = ""
    confidence: float = 0.0


class TranscriptionResult(msgspec.Struct, frozen=True, gc=False):
    """Unified transcription result from any engine."""
    text: str = ""
    language: str = "en"
    duration_s: float = 0.0
    confidence: float = 0.0
    segments: list[TranscriptionSegment] = msgspec.field(default_factory=list)
    engine: TranscriptionEngine = TranscriptionEngine.NONE
    engine_detail: str = ""
    iocs_extracted: int = 0
    iocs: list[str] = msgspec.field(default_factory=list)


# ─── TranscriptionRouter ─────────────────────────────────────────────────────

class TranscriptionRouter:
    """
    Intelligent routing between SFSpeechRecognizer and WhisperEngine.

    Selects the best engine based on:
    - Language availability
    - Offline/online constraints
    - Audio characteristics
    - User preference

    Thread-safe. All methods are async and coordinated.
    """

    __slots__ = (
        '_media_decoder',
        '_whisper_engine',
        '_router_lock',
        '_sf_speech_available',
        '_whisper_available',
        '_rust_whisper_available',
        '_probed',
    )

    def __init__(self) -> None:
        self._media_decoder: Any = None
        self._whisper_engine: Any = None
        self._router_lock: asyncio.Lock | None = None
        self._sf_speech_available: bool = False
        self._whisper_available: bool = False
        self._rust_whisper_available: bool = False
        self._probed: bool = False

    def _get_lock(self) -> asyncio.Lock:
        if self._router_lock is None:
            self._router_lock = asyncio.Lock()
        return self._router_lock

    async def _probe_engines(self) -> None:
        """Lazy-probe available engines. Runs once."""
        if self._probed:
            return
        async with self._get_lock():
            if self._probed:
                return

            # Probe SFSpeechRecognizer
            try:
                from hledac.universal.multimodal.media_engine import (
                    MediaDecoder,
    )
                decoder = MediaDecoder()
                await decoder.initialize()
                self._sf_speech_available = decoder._speech_available
                self._media_decoder = decoder
                if self._sf_speech_available:
                    logger.info("[TranscriptionRouter] SFSpeechRecognizer available")
                else:
                    logger.debug("[TranscriptionRouter] SFSpeechRecognizer unavailable")
            except ImportError:
                logger.debug("[TranscriptionRouter] MediaDecoder not importable")
            except Exception as exc:
                logger.debug("[TranscriptionRouter] MediaDecoder probe failed: %s", exc)

            # Probe Rust whisper (CoreML/ANE acceleration) - PRIORITY PATH
            # SILICON-02: Rust whisper.cpp with CoreML/ANE backend
            try:
                from hledac.universal._core.rust_backend import rust
                self._rust_whisper_available = rust.whisper.is_available()
                if self._rust_whisper_available:
                    logger.info("[TranscriptionRouter] Rust whisper (CoreML/ANE) available")
                else:
                    logger.debug("[TranscriptionRouter] Rust whisper unavailable (no model cached)")
            except ImportError:
                logger.debug("[TranscriptionRouter] Rust whisper module not importable")
            except Exception as exc:
                logger.debug("[TranscriptionRouter] Rust whisper probe failed: %s", exc)

            # Probe WhisperEngine (Python whispercpp fallback)
            try:
                from hledac.universal.brain.whisper_engine import is_whisper_available
                self._whisper_available = is_whisper_available()
                if self._whisper_available:
                    logger.info("[TranscriptionRouter] WhisperEngine (whispercpp) available")
                else:
                    logger.debug("[TranscriptionRouter] WhisperEngine unavailable")
            except ImportError:
                logger.debug("[TranscriptionRouter] WhisperEngine not importable")

            self._probed = True

            available = []
            if self._sf_speech_available:
                available.append("SFSpeechRecognizer")
            if self._rust_whisper_available:
                available.append("Rust whisper (CoreML/ANE)")
            if self._whisper_available:
                available.append("WhisperEngine (whispercpp)")
            logger.info(
                "[TranscriptionRouter] Probed: %s",
                ", ".join(available) if available else "no engines available",
    )

    def select_engine(
        self,
        language: str | None = None,
        force_offline: bool = False,
        force_engine: EngineChoice = EngineChoice.AUTO,
    ) -> EngineChoice:
        """
        Select the best transcription engine.

        Decision tree:
        1. User-forced engine → use it
        2. Language not in SFS offline set → WhisperEngine
        3. Force offline mode → WhisperEngine
        4. Default → SFSpeechRecognizer (fastest)
        """
        if force_engine == EngineChoice.SFS_SPEECH:
            return EngineChoice.SFS_SPEECH
        if force_engine == EngineChoice.WHISPER_CPP:
            # User forced whisper — prefer Rust whisper (CoreML/ANE) if available
            if self._rust_whisper_available:
                return EngineChoice.WHISPER_CPP  # Will route to Rust in _transcribe_whisper_direct
            elif self._whisper_available:
                return EngineChoice.WHISPER_CPP
            else:
                logger.warning("[TranscriptionRouter] WhisperEngine forced but no engine available")
                return EngineChoice.AUTO

        # AUTO routing logic
        lang = (language or "en").lower().split("-")[0]

        # Any whisper engine available (Rust or Python)?
        any_whisper = self._rust_whisper_available or self._whisper_available

        # Language-based routing
        if lang in _SFS_NETWORK_DEPENDENT_LANGUAGES:
            if any_whisper:
                engine = "Rust whisper" if self._rust_whisper_available else "WhisperEngine"
                logger.debug(
                    "[TranscriptionRouter] Language '%s' → %s (offline)",
                    lang,
                    engine,
    )
                return EngineChoice.WHISPER_CPP
            else:
                logger.debug(
                    "[TranscriptionRouter] Language '%s' → SFSpeechRecognizer "
                    "(whisper unavailable, may need network)",
                    lang,
    )
                return EngineChoice.SFS_SPEECH

        if force_offline:
            if any_whisper:
                return EngineChoice.WHISPER_CPP
            elif lang in _SFS_OFFLINE_LANGUAGES:
                return EngineChoice.SFS_SPEECH
            else:
                logger.warning(
                    "[TranscriptionRouter] Force-offline but no offline engine "
                    "for language '%s'",
                    lang,
    )
                return EngineChoice.SFS_SPEECH  # best effort

        # Default: SFSpeechRecognizer (fastest, lowest RAM)
        if self._sf_speech_available:
            return EngineChoice.SFS_SPEECH
        elif self._rust_whisper_available:
            return EngineChoice.WHISPER_CPP
        elif self._whisper_available:
            return EngineChoice.WHISPER_CPP

        # Nothing available
        logger.warning("[TranscriptionRouter] No transcription engine available")
        return EngineChoice.AUTO

    async def transcribe(
        self,
        source: str | Path,
        language: str | None = None,
        force_engine: EngineChoice = EngineChoice.AUTO,
        force_offline: bool = False,
        extract_iocs: bool = True,
        model_size: Literal["tiny", "base"] = "tiny",
    ) -> TranscriptionResult:
        """
        Transcribe audio with automatic engine routing + fallback.

        Args:
            source: Audio file path.
            language: ISO-639-1 code or None for auto-detect.
            force_engine: Override engine selection.
            force_offline: Prefer offline-first engine.
            extract_iocs: Run IOC extraction on transcription result.
            model_size: whisper model size if WhisperEngine is used.

        Returns:
            TranscriptionResult (empty on total failure — never raises).
        """
        await self._probe_engines()

        # Validate input
        source_path = Path(str(source))
        if not source_path.exists():
            logger.warning("[TranscriptionRouter] File not found: %s", source_path)
            return TranscriptionResult()

        try:
            file_size = source_path.stat().st_size
            if file_size > _MAX_AUDIO_FILE_BYTES:
                logger.warning(
                    "[TranscriptionRouter] File too large: %d MB",
                    file_size // (1024 * 1024),
    )
                return TranscriptionResult()
            if file_size == 0:
                logger.warning("[TranscriptionRouter] Empty file")
                return TranscriptionResult()
        except OSError:
            return TranscriptionResult()

        # Select primary engine
        chosen = self.select_engine(
            language=language,
            force_offline=force_offline,
            force_engine=force_engine,
    )

        # Try primary engine
        result_raw: TranscriptionResult | None = None

        if chosen == EngineChoice.SFS_SPEECH and self._sf_speech_available:
            result_raw = await self._transcribe_sfspeech(source_path, language)
            if result_raw is None and (self._rust_whisper_available or self._whisper_available):
                logger.info("[TranscriptionRouter] SFSpeechRecognizer failed, "
                          "falling back to whisper")
                result_raw = await self._transcribe_whisper(
                    source_path, language, model_size,
    )

        elif chosen == EngineChoice.WHISPER_CPP:
            # Whisper path: prefer Rust whisper (CoreML/ANE), fallback to Python
            if self._rust_whisper_available or self._whisper_available:
                result_raw = await self._transcribe_whisper(
                    source_path, language, model_size,
    )
                if result_raw is None and self._sf_speech_available:
                    logger.info("[TranscriptionRouter] Whisper failed, "
                              "falling back to SFSpeechRecognizer")
                    result_raw = await self._transcribe_sfspeech(source_path, language)
            else:
                # No whisper available, try SFSpeechRecognizer
                logger.warning("[TranscriptionRouter] Whisper forced but no engine available")
                if self._sf_speech_available:
                    result_raw = await self._transcribe_sfspeech(source_path, language)

        elif chosen == EngineChoice.SFS_SPEECH:
            # User forced SFSpeechRecognizer but it's unavailable
            logger.warning("[TranscriptionRouter] SFSpeechRecognizer forced but unavailable")
            if self._rust_whisper_available or self._whisper_available:
                result_raw = await self._transcribe_whisper(
                    source_path, language, model_size,
    )

        # No engine available
        if result_raw is None:
            return TranscriptionResult(
                engine=TranscriptionEngine.NONE,
                engine_detail="no engine available or all failed",
    )

        # Extract IoCs
        if extract_iocs and result_raw.text:
            result_raw = await self._extract_iocs_from_text(result_raw)

        return result_raw

    async def _transcribe_sfspeech(
        self,
        source_path: Path,
        language: str | None,
    ) -> TranscriptionResult | None:
        """Transcribe via SFSpeechRecognizer. Returns None on failure."""
        try:
            if self._media_decoder is None:
                return None

            from hledac.universal.multimodal.media_engine import (
                _SPEECH_LOCALE,
    )

            # Map ISO-639-1 to Apple locale
            locale_map = {
                "en": "en-US", "de": "de-DE", "fr": "fr-FR", "es": "es-ES",
                "it": "it-IT", "pt": "pt-BR", "ja": "ja-JP", "ko": "ko-KR",
                "zh": "zh-CN", "ru": "ru-RU", "ar": "ar-SA", "nl": "nl-NL",
                "pl": "pl-PL", "tr": "tr-TR", "sv": "sv-SE", "da": "da-DK",
                "fi": "fi-FI", "nb": "nb-NO", "cs": "cs-CZ", "sk": "sk-SK",
                "ro": "ro-RO", "hu": "hu-HU", "el": "el-GR", "he": "he-IL",
                "hi": "hi-IN", "th": "th-TH", "id": "id-ID", "ms": "ms-MY",
                "uk": "uk-UA", "vi": "vi-VN", "ca": "ca-ES", "hr": "hr-HR",
                "yue": "yue-CN",
            }
            locale = locale_map.get(
                (language or "en").lower().split("-")[0],
                _SPEECH_LOCALE,
    )

            # If locale changed, re-init recognizer
            if (self._media_decoder._speech_locale != locale
                    and self._media_decoder._speech_available):
                await self._media_decoder.close()
                self._media_decoder._speech_locale = locale
                await self._media_decoder.initialize()

            raw = await self._media_decoder.transcribe(
                str(source_path),
    )

            if raw is None or not raw.text:
                return None

            segments = [
                TranscriptionSegment(
                    start_s=s.get("start_s", 0.0),
                    end_s=s.get("end_s", 0.0),
                    text=s.get("text", ""),
                    confidence=s.get("confidence", 0.0),
    )
                for s in (raw.segments or [])
            ]

            return TranscriptionResult(
                text=raw.text,
                language=language or raw.locale.split("-")[0],
                duration_s=raw.duration_s,
                confidence=raw.confidence,
                segments=segments,
                engine=TranscriptionEngine.SFS_SPEECH,
                engine_detail=f"SFSpeechRecognizer (locale={locale})",
    )

        except Exception as exc:
            logger.debug("[TranscriptionRouter] SFSpeechRecognizer error: %s", exc)
            return None

    async def _transcribe_whisper(
        self,
        source_path: Path,
        language: str | None,
        model_size: Literal["tiny", "base"],
    ) -> TranscriptionResult | None:
        """
        Transcribe via WhisperEngine with intelligent routing.

        PRIORITY PATH:
        1. Rust whisper (CoreML/ANE) - direct, no subprocess overhead
        2. Sandboxed subprocess - when coordinator available
        3. Direct Python whispercpp - final fallback

        ADVERSARY-001: Delegates to MediaSandboxCoordinator for subprocess isolation
        when Rust whisper is unavailable.

        Returns None on failure.
        """
        # ── PRIORITY 1: Direct Rust whisper (CoreML/ANE) — fastest path ──────
        # No subprocess overhead, direct ANE acceleration
        if self._rust_whisper_available:
            direct_result = await self._transcribe_whisper_direct(
                source_path, language, model_size
    )
            if direct_result is not None and direct_result.text:
                # Ensure engine type is RUST_WHISPER
                return msgspec.structs.replace(
                    direct_result,
                    engine=TranscriptionEngine.RUST_WHISPER,
    )
            # Rust whisper failed, continue to subprocess fallback

        # ── ADVERSARY-001: Sandboxed subprocess via MediaSandboxCoordinator ────
        coordinator, coordinator_available = _get_sandbox_coordinator()
        
        if coordinator is not None:
            try:
                # Delegate to coordinator's unified whisper transcription
                whisper_result = await coordinator.run_whisper_transcription(
                    audio_path=str(source_path),
                    model_size=model_size,
                    language=language,
                    timeout_s=_TRANSCRIBE_TIMEOUT_S,
    )
                
                # Parse coordinator result
                if whisper_result.text:
                    seatbelt_note = " +Seatbelt" if whisper_result.seatbelt_used else ""
                    return TranscriptionResult(
                        text=whisper_result.text,
                        language=whisper_result.language or language or "en",
                        duration_s=whisper_result.duration_s,
                        confidence=whisper_result.confidence,
                        segments=[
                            TranscriptionSegment(
                                start_s=s.get("start_s", 0.0),
                                end_s=s.get("end_s", 0.0),
                                text=s.get("text", ""),
                                confidence=s.get("confidence", 0.0),
    )
                            for s in whisper_result.segments
                        ],
                        engine=TranscriptionEngine.WHISPER_CPP,
                        engine_detail=(
                            f"whisper.sandbox::{model_size}{seatbelt_note}"
                        ),
    )
                elif whisper_result.error:
                    logger.warning(
                        "[TranscriptionRouter] Subprocess whisper failed: %s",
                        whisper_result.error,
    )
                    return None
                    
            except Exception as exc:
                logger.warning(
                    "[TranscriptionRouter] Coordinator error, trying direct engine: %s",
                    exc,
    )
        else:
            if not self._rust_whisper_available:
                logger.warning(
                    "[TranscriptionRouter] ADVERSARY-001: whisper running "
                    "WITHOUT sandbox isolation (security risk!)"
    )
        
        # ── Final Fallback: Direct whisper engine (no sandbox) ───────────────
        # Only used when subprocess failed and Rust whisper unavailable
        if not self._rust_whisper_available:
            return await self._transcribe_whisper_direct(source_path, language, model_size)
        return None

    async def _transcribe_whisper_direct(
        self,
        source_path: Path,
        language: str | None,
        model_size: Literal["tiny", "base"],
    ) -> TranscriptionResult | None:
        """
        Direct whisper execution without sandbox (final fallback).
        
        PRIORITY: Rust whisper (CoreML/ANE) → Python whispercpp
        
        Used when:
        1. Subprocess sandboxing fails
        2. Direct mode (no sandbox desired)
        
        Engine detail format: "rust.whisper::tiny +CoreML/ANE" or "whisper.cpp::base CPU"
        """
        # ── Priority 1: Rust whisper (CoreML/ANE acceleration) ──────────────────
        # SILICON-02: Rust whisper.cpp with dedicated ANE memory, M1 8GB safe
        if self._rust_whisper_available:
            try:
                from hledac.universal._core.rust_backend import rust

                # Run Rust whisper in thread pool (CPU-bound decoder)
                raw = await asyncio.to_thread(
                    rust.whisper.transcribe,
                    str(source_path),
                    model_size=model_size,
                    language=language,
    )

                if raw and raw.get("text"):
                    coreml_note = " +CoreML/ANE" if raw.get("coreml_used") else " CPU"
                    segments = [
                        TranscriptionSegment(
                            start_s=s.get("start_s", 0.0),
                            end_s=s.get("end_s", 0.0),
                            text=s.get("text", ""),
                            confidence=s.get("confidence", 0.85),
    )
                        for s in raw.get("segments", [])
                    ]
                    logger.info(
                        "[TranscriptionRouter] rust.whisper::%s: %d chars, coreml=%s, latency=%.2fs",
                        model_size, len(raw.get("text", "")),
                        raw.get("coreml_used", False),
                        raw.get("latency_s", 0.0),
    )
                    return TranscriptionResult(
                        text=raw.get("text", ""),
                        language=raw.get("language", language or "en"),
                        duration_s=raw.get("duration_s", 0.0),
                        confidence=raw.get("confidence", 0.85),
                        segments=segments,
                        engine=TranscriptionEngine.RUST_WHISPER,
                        engine_detail=f"rust.whisper::{model_size}{coreml_note}",
    )
            except ImportError:
                logger.debug("[TranscriptionRouter] Rust whisper not available")
            except Exception as exc:
                logger.debug("[TranscriptionRouter] Rust whisper error: %s", exc)

        # ── Priority 2: Python whispercpp (fallback) ──────────────────────────
        try:
            from hledac.universal.brain.whisper_engine import (
                get_whisper_engine,
    )

            engine = await get_whisper_engine()
            raw = await engine.transcribe(
                str(source_path),
                model_size=model_size,
                language=language,
    )

            if raw is None or not raw.text:
                return None

            segments = [
                TranscriptionSegment(
                    start_s=s.start_s,
                    end_s=s.end_s,
                    text=s.text,
                    confidence=s.confidence,
    )
                for s in raw.segments
            ]

            coreml_note = " +CoreML/ANE" if raw.coreml_used else " CPU"
            logger.info(
                "[TranscriptionRouter] whisper.cpp::%s: %d chars, coreml=%s",
                model_size, len(raw.text), raw.coreml_used,
    )
            return TranscriptionResult(
                text=raw.text,
                language=raw.language,
                duration_s=raw.duration_s,
                confidence=raw.confidence,
                segments=segments,
                engine=TranscriptionEngine.WHISPER_CPP,
                engine_detail=f"whisper.cpp::{model_size}{coreml_note}",
    )

        except Exception as exc:
            logger.debug("[TranscriptionRouter] WhisperEngine direct error: %s", exc)
            return None

    async def _extract_iocs_from_text(
        self,
        result: TranscriptionResult,
    ) -> TranscriptionResult:
        """Extract IoCs from transcription text using the Rust regex pipeline."""
        try:
            from hledac.universal.rust.ioc import extract_iocs_flat
            iocs = extract_iocs_flat(result.text)
            ioc_strings = [str(ioc) for ioc in iocs]
            return msgspec.structs.replace(
                result,
                iocs_extracted=len(ioc_strings),
                iocs=ioc_strings,
    )
        except ImportError:
            logger.debug("[TranscriptionRouter] Rust IOC extractor not available")
            return result
        except Exception as exc:
            logger.debug("[TranscriptionRouter] IOC extraction failed: %s", exc)
            return result


# ─── Module-level singleton ──────────────────────────────────────────────────

_router: TranscriptionRouter | None = None
_router_lock = asyncio.Lock()


async def get_transcription_router() -> TranscriptionRouter:
    """Get or create the TranscriptionRouter singleton."""
    global _router
    if _router is None:
        async with _router_lock:
            if _router is None:
                _router = TranscriptionRouter()
    return _router


# ─── Convenience functions ───────────────────────────────────────────────────

async def transcribe_audio(
    source: str | Path,
    language: str | None = None,
    force_offline: bool = False,
    force_engine: EngineChoice = EngineChoice.AUTO,
    model_size: Literal["tiny", "base"] = "tiny",
) -> TranscriptionResult:
    """
    One-shot audio transcription with automatic engine routing.

    Args:
        source: Audio file path.
        language: ISO-639-1 code or None for auto-detect.
        force_offline: Prefer offline-first engine.
        force_engine: Override engine selection.
        model_size: whisper model size (if WhisperEngine is used).

    Returns:
        TranscriptionResult (empty result on failure — never raises).
    """
    router = await get_transcription_router()
    return await router.transcribe(
        source,
        language=language,
        force_engine=force_engine,
        force_offline=force_offline,
        model_size=model_size,
    )


async def transcribe_and_extract_iocs(
    source: str | Path,
    language: str | None = None,
    model_size: Literal["tiny", "base"] = "tiny",
) -> list[str]:
    """
    Transcribe audio and extract IoCs in one call.

    Convenience function for the OSINT pipeline.

    Returns:
        List of IOC strings (domains, IPs, emails, etc.) found in audio.
        Empty list on failure.
    """
    result = await transcribe_audio(
        source,
        language=language,
        model_size=model_size,
    )
    if result is None:
        return []
    return result.iocs


# ============================================================================
# NEXTGEN-03: Voiceprint Extraction
# ============================================================================

async def extract_voiceprint(
    source: str | Path,
    model_size: Literal["tiny", "base"] = "tiny",
) -> dict[str, Any]:
    """
    NEXTGEN-03: Extract speaker voiceprint embedding from audio file.

    Uses Rust whisper backend with speaker embedding extraction to generate
    a 256-dimensional voiceprint vector for identity matching.

    Args:
        source: Audio file path (WAV 16kHz mono recommended)
        model_size: Whisper model size ("tiny" or "base")

    Returns:
        Dict with:
            - embedding: 256-dim speaker embedding vector
            - duration_s: Audio duration in seconds
            - quality_score: Voice quality confidence (0-1)
            - cached: Whether result was cached
            - error: Error message if failed (str)

    Example:
        vp = await extract_voiceprint("speaker_audio.wav")
        if vp and "embedding" in vp:
            print(f"Voiceprint quality: {vp['quality_score']:.2f}")
    """
    source_path = Path(str(source))
    if not source_path.exists():
        return {"error": f"File not found: {source}"}

    try:
        from hledac.universal._core.rust_backend import rust
        if not hasattr(rust, "whisper"):
            return {"error": "Rust whisper module not available"}

        whisper_mod = rust.whisper
        if not hasattr(whisper_mod, "extract_voiceprint"):
            return {"error": "extract_voiceprint not available in Rust whisper"}

        # Run voiceprint extraction on thread pool (may take time)
        def _extract_sync():
            return whisper_mod.extract_voiceprint(
                str(source_path),
                model_size=model_size,
                n_segments=3,
    )

        result = await asyncio.to_thread(_extract_sync)

        if result is None:
            return {"error": "Voiceprint extraction returned None"}

        return {
            "embedding": list(result.get("embedding", [])),
            "duration_s": result.get("duration_s", 0.0),
            "quality_score": result.get("quality_score", 0.0),
            "cached": result.get("cached", False),
        }

    except ImportError:
        return {"error": "Rust backend not available"}
    except Exception as exc:
        logger.warning("[NEXTGEN-03] Voiceprint extraction failed: %s", exc)
        return {"error": str(exc)}


async def extract_voiceprint_and_transcribe(
    source: str | Path,
    model_size: Literal["tiny", "base"] = "tiny",
) -> tuple[dict[str, Any], TranscriptionResult]:
    """
    NEXTGEN-03: Extract voiceprint and transcribe audio in parallel.

    Optimized for identity fusion: runs both operations concurrently
    to minimize latency.

    Args:
        source: Audio file path
        model_size: Whisper model size

    Returns:
        Tuple of (voiceprint_result, transcription_result)
    """
    vp_task = extract_voiceprint(source, model_size)
    transcribe_task = transcribe_audio(source, model_size=model_size)

    voiceprint_result, transcription_result = await asyncio.gather(
        vp_task, transcribe_task, return_exceptions=True
    )

    # Handle exceptions
    if isinstance(voiceprint_result, Exception):
        voiceprint_result = {"error": str(voiceprint_result)}
    if isinstance(transcription_result, Exception):
        transcription_result = TranscriptionResult()

    return voiceprint_result, transcription_result
