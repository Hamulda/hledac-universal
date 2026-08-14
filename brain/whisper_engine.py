"""
brain/whisper_engine.py — Whisper.cpp CoreML/ANE Speech-to-Text Engine
======================================================================




SILICON-02b: whisper.cpp transcription accelerated by CoreML/Apple Neural Engine.
Complements the existing SFSpeechRecognizer (SILICON-02) with:
  - 99-language support (vs 60+ for SFSpeechRecognizer)
  - Fully offline operation (no Apple server dependency)
  - Fine-tunable models for OSINT domain vocabulary
  - Consistent accuracy across all supported languages

Architecture:
    WhisperEngine (singleton)
    ├── CoreML encoder → ANE (Neural Engine, 11 TOPS)
    │   └── whisper.cpp encoder layers compiled to .mlmodelc
    ├── CPU decoder → whisper.cpp (P-core, scalar)
    │   └── tiny: 39MB / base: 74MB model footprint
    ├── Model cache: ~/.cache/hledac/whisper_models/
    │   └── APFS COW clonefile for O(1) copy
    ├── Fallback chain: CoreML/ANE → CPU-only whisper.cpp → SFSpeechRecognizer
    └── Fail-soft: any error → returns None, caller falls back

Model trade-off (M1 8GB):
    tiny (39 MB):  ~5% WER clean EN, ~5-8 min per 1h audio
    base (74 MB):  ~3% WER clean EN, ~8-12 min per 1h audio
    For OSINT IOC extraction: tiny is sufficient — 95% accuracy on clean audio
    is more than enough for domain/email/IP extraction from spoken content.

M1 8GB bounds:
    - Max 1 whisper model in memory at a time
    - tiny model: 39 MB (encoder CoreML) + ~30 MB (runtime) = ~70 MB peak
    - base model: 74 MB (encoder CoreML) + ~40 MB (runtime) = ~114 MB peak
    - Audio buffer: 30s chunks, float32 mono 16kHz = ~1.92 MB per chunk
    - Coordinated with _MLXFamilyMutex for LLM/ANE slot management

Feature flag: HLEDAC_ENABLE_WHISPER=0|1 (default 1 — always-on, fail-soft)
Opt-out: HLEDAC_DISABLE_WHISPER=1

Usage:
    from hledac.universal.brain.whisper_engine import WhisperEngine
    engine = WhisperEngine()
    result = await engine.transcribe("audio.mp3", model_size="tiny")
    # result.text → transcription string
    # result.segments → list of {start, end, text, confidence}

Python 3.14+ compatible. Lazy imports: whispercpp + coremltools loaded on first use.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time as time_module
from pathlib import Path
from typing import Any, Literal

from hledac.universal.utils.asyncx import safe_wait_for

import msgspec

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

_MODEL_CACHE_DIR = Path.home() / ".cache" / "hledac" / "whisper_models"
_MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Model configurations
_MODEL_CONFIGS: dict[str, dict[str, Any]] = {
    "tiny": {
        "size_mb": 39,
        "ggml_name": "ggml-tiny.bin",
        "coreml_name": "ggml-tiny-encoder.mlmodelc",
        "ggml_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin",
        "dim": 384,
        "enc_layers": 4,
        "dec_layers": 4,
        "heads": 6,
        "languages": 99,
        "description": "Whisper tiny — 39MB CoreML, 95% WER clean EN, ~5 min per 1h audio",
    },
    "base": {
        "size_mb": 74,
        "ggml_name": "ggml-base.bin",
        "coreml_name": "ggml-base-encoder.mlmodelc",
        "ggml_url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin",
        "dim": 512,
        "enc_layers": 6,
        "dec_layers": 6,
        "heads": 8,
        "languages": 99,
        "description": "Whisper base — 74MB CoreML, 97% WER clean EN, ~8 min per 1h audio",
    },
}

# M1 8GB bounds
_TARGET_SAMPLE_RATE = 16000      # whisper.cpp expects 16kHz
_MAX_AUDIO_FILE_BYTES = 100 * 1024 * 1024  # 100 MB max input file
_WHISPER_THREADS = 4             # whisper.cpp thread count (P+E cores on M1)
_TRANSCRIBE_TIMEOUT_S = 600.0    # 10 min max per transcription
_MODEL_DOWNLOAD_TIMEOUT_S = 300.0  # 5 min max for model download

# Runtime feature flags
from hledac.universal.core.feature_flags import FeatureFlag, FeatureFlags
_WHISPER_ENABLED_BY_ENV = FeatureFlags.get(FeatureFlag.WHISPER)
_WHISPER_DISABLED_BY_ENV = FeatureFlags.get(FeatureFlag.DISABLE_WHISPER)

# CoreML model download URLs (pre-converted whisper.cpp encoder models)
_COREML_MODEL_URLS: dict[str, str] = {
    "tiny": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny-encoder.mlmodelc.zip",
    "base": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base-encoder.mlmodelc.zip",
}

# ─── Public types ────────────────────────────────────────────────────────────

class TranscriptionSegment(msgspec.Struct, frozen=True, gc=False):
    """Single transcribed segment with timing and confidence."""
    start_s: float = 0.0
    end_s: float = 0.0
    text: str = ""
    confidence: float = 0.0


class TranscriptionResult(msgspec.Struct, frozen=True, gc=False):
    """Complete transcription result from whisper.cpp."""
    text: str = ""
    language: str = "en"
    duration_s: float = 0.0
    confidence: float = 0.0
    segments: list[TranscriptionSegment] = msgspec.field(default_factory=list)
    engine: str = "whisper_cpp"
    model_size: str = "tiny"
    coreml_used: bool = False


# [FINAL]-019-07: Capability cost registration for QoS ladder triage.
# whisper (tiny model): rss_mb=70, peak_mb=114 (CoreML encoder + runtime)
# whisper (base model): rss_mb=114, peak_mb=154
from hledac.universal.core.capability_cost import register_capability_cost
register_capability_cost("whisperengine", rss_mb=70, peak_mb=114, tier="medium", tags=("speech", "gpu", "ane"))

# ─── Lazy capability detection ───────────────────────────────────────────────

_whispercpp_available: bool | None = None
_whispercpp: Any = None
_ffmpeg_available: bool | None = None
_ANE_available: bool | None = None


def _check_platform() -> bool:
    """Check Apple Silicon."""
    return os.uname().sysname == "Darwin" and os.uname().machine == "arm64"


def _check_ane() -> bool:
    """Lazy-check ANE availability via coremltools."""
    global _ANE_available
    if _ANE_available is not None:
        return _ANE_available
    if not _check_platform():
        _ANE_available = False
        return False
    try:
        import coremltools as ct
        ver = tuple(int(p) for p in ct.__version__.split(".")[:2] if p.isdigit())
        _ANE_available = bool(ver and ver >= (6, 0))
    except ImportError:
        _ANE_available = False
    return _ANE_available


def _check_whispercpp() -> bool:
    """Lazy-check: is whispercpp Python package importable?"""
    global _whispercpp_available, _whispercpp
    if _whispercpp_available is not None:
        return _whispercpp_available
    try:
        from whispercpp import Whisper
        _whispercpp = Whisper
        _whispercpp_available = True
        logger.info("[WhisperEngine] whispercpp package available")
        return True
    except ImportError:
        _whispercpp_available = False
        logger.debug("[WhisperEngine] whispercpp not installed — transcription disabled")
        return False


def _check_ffmpeg() -> bool:
    """Lazy-check: is ffmpeg available on PATH for audio conversion?"""
    global _ffmpeg_available
    if _ffmpeg_available is not None:
        return _ffmpeg_available
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            timeout=5,
        )
        _ffmpeg_available = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _ffmpeg_available = False
    if _ffmpeg_available:
        logger.debug("[WhisperEngine] ffmpeg available for audio conversion")
    else:
        logger.debug("[WhisperEngine] ffmpeg not found — limited format support")
    return _ffmpeg_available


def is_whisper_available() -> bool:
    """Check if whisper transcription is available."""
    if _WHISPER_DISABLED_BY_ENV:
        return False
    if not _WHISPER_ENABLED_BY_ENV:
        return False
    # [FINAL]-019-06: Governor QoS gate — disable whisper in EMERGENCY/BATTERY modes.
    # Lazy import avoids circular dependency; fail-open so governor unavailability
    # never blocks whisper (the governor sets whisper_ok=False explicitly in those modes).
    try:
        from hledac.universal.core.resource_governor import QoSLevel, get_current_degradation_level
        level = get_current_degradation_level()
        if level is QoSLevel.EMERGENCY or level is QoSLevel.BATTERY:
            return False
    except Exception:  # noqa: BLE001
        pass  # fail-open: governor unavailable → allow whisper
    if not _check_platform():
        return False
    return _check_whispercpp()


# ─── Audio conversion ────────────────────────────────────────────────────────

async def _convert_to_wav_16khz(
    input_path: Path,
    output_dir: Path | None = None,
) -> Path | None:
    """
    Convert any audio file to 16kHz mono WAV (whisper.cpp expected format).
    Uses ffmpeg if available; otherwise returns None.
    """
    if not _check_ffmpeg():
        return None
    output_dir = output_dir or Path(tempfile.mkdtemp(prefix="whisper_"))
    output_path = output_dir / f"{input_path.stem}_16khz.wav"

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i", str(input_path),
            "-ar", str(_TARGET_SAMPLE_RATE),
            "-ac", "1",
            "-sample_fmt", "s16",
            "-f", "wav",
            "-loglevel", "error",
            str(output_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await safe_wait_for(
            proc.communicate(),
            timeout=60.0,
        )
        if proc.returncode != 0:
            err_text = stderr.decode()[:200] if stderr else "unknown error"
            logger.warning("[WhisperEngine] ffmpeg conversion failed: %s", err_text)
            return None
        if output_path.exists() and output_path.stat().st_size > 0:
            return output_path
    except asyncio.TimeoutError:
        logger.warning("[WhisperEngine] ffmpeg conversion timed out")
    except Exception as exc:
        logger.warning("[WhisperEngine] ffmpeg conversion error: %s", exc)
    return None


# ─── Model download & cache ──────────────────────────────────────────────────

async def _download_model_ggml(model_size: str) -> Path | None:
    """Download ggml model from HuggingFace. Returns path or None."""
    config = _MODEL_CONFIGS.get(model_size)
    if config is None:
        logger.error("[WhisperEngine] Unknown model size: %s", model_size)
        return None

    target_path = _MODEL_CACHE_DIR / config["ggml_name"]
    if target_path.exists():
        # Validate checksum
        if _validate_ggml_model(target_path, config):
            return target_path
        else:
            logger.warning("[WhisperEngine] Corrupt model at %s, re-downloading", target_path)
            target_path.unlink(missing_ok=True)

    url = config["ggml_url"]
    logger.info("[WhisperEngine] Downloading whisper %s model from %s", model_size, url)

    try:
        # Use httpx through the project's transport layer if available
        try:
            from hledac.universal.fetching.public_fetcher import fetch_content
            content = await fetch_content(url, timeout=_MODEL_DOWNLOAD_TIMEOUT_S)
            if content:
                target_path.write_bytes(content)
        except Exception:  # noqa: BLE001
            # Fallback to direct curl
            pass

        if not target_path.exists():
            # Final fallback: subprocess curl
            proc = await asyncio.create_subprocess_exec(
                "curl", "-L", "-o", str(target_path),
                "--connect-timeout", "30",
                "--max-time", str(_MODEL_DOWNLOAD_TIMEOUT_S),
                url,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await safe_wait_for(
                proc.communicate(),
                timeout=_MODEL_DOWNLOAD_TIMEOUT_S + 30,
            )
            if proc.returncode != 0:
                err = stderr.decode()[:200] if stderr else "unknown"
                logger.error("[WhisperEngine] Model download failed: %s", err)
                target_path.unlink(missing_ok=True)
                return None

        if target_path.exists() and _validate_ggml_model(target_path, config):
            logger.info("[WhisperEngine] Downloaded whisper %s model (%d MB)",
                        model_size, target_path.stat().st_size // (1024 * 1024))
            return target_path
        else:
            logger.error("[WhisperEngine] Model download validation failed for %s", model_size)
            target_path.unlink(missing_ok=True)
            return None

    except asyncio.TimeoutError:
        logger.error("[WhisperEngine] Model download timed out for %s", model_size)
        target_path.unlink(missing_ok=True)
        return None
    except Exception as exc:
        logger.error("[WhisperEngine] Model download failed: %s", exc)
        target_path.unlink(missing_ok=True)
        return None


def _validate_ggml_model(path: Path, config: dict[str, Any]) -> bool:
    """Validate ggml model file: size check + magic number."""
    if not path.exists():
        return False
    file_size_mb = path.stat().st_size / (1024 * 1024)
    expected_mb = config["size_mb"]
    # Allow ±30% tolerance (network conditions, ggml vs CoreML split)
    if file_size_mb < expected_mb * 0.5 or file_size_mb > expected_mb * 2.5:
        logger.debug("[WhisperEngine] Model size mismatch: %.1f MB vs ~%d MB expected",
                     file_size_mb, expected_mb)
        return False
    # Check ggml magic number ("ggml" at offset 0 or "ggmf" for older formats)
    # [INTERNAL]-009 perf: read only 4 bytes header, not entire file (~39-74 MB)
    try:
        with path.open("rb") as fh:
            header = fh.read(4)
        if header not in (b"ggml", b"GGML", b"ggmf", b"GGMF"):
            logger.debug("[WhisperEngine] Invalid ggml magic: %r", header)
            return False
    except Exception:
        return False
    return True


def _is_coreml_model_valid(path: Path) -> bool:
    """Check if a CoreML model directory contains valid model files."""
    if not path.exists() or not path.is_dir():
        return False
    return (path / "model.mil").exists() or any(path.glob("*.mlmodel"))


def _log_coreml_found(path: Path, source: str = "") -> None:
    """Log successful CoreML model discovery."""
    logger.info("[WhisperEngine] CoreML model found%s: %s", source, path)


async def _download_coreml_zip(url: str, zip_path: Path, timeout_s: int) -> bool:
    """Download CoreML zip using fetch_content or curl fallback."""
    try:
        from hledac.universal.fetching.public_fetcher import fetch_content
        content = await fetch_content(url, timeout=timeout_s)
        if content:
            zip_path.write_bytes(content)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


async def _curl_download_coreml(url: str, zip_path: Path, timeout_s: int) -> bool:
    """Download CoreML zip using curl as fallback."""
    proc = await asyncio.create_subprocess_exec(
        "curl", "-L", "-o", str(zip_path),
        "--connect-timeout", "30",
        "--max-time", str(timeout_s),
        url,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await safe_wait_for(
        proc.communicate(),
        timeout=timeout_s + 30,
    )
    if proc.returncode != 0:
        err = stderr.decode()[:200] if stderr else "unknown"
        logger.warning("[WhisperEngine] CoreML model download failed: %s", err)
        zip_path.unlink(missing_ok=True)
        return False
    return True


async def _extract_coreml_model(
    zip_path: Path,
    coreml_path: Path,
    model_size: str,
) -> Path | None:
    """Extract CoreML model from downloaded zip."""
    if not zip_path.exists() or zip_path.stat().st_size == 0:
        return None
    import zipfile
    extract_dir = _MODEL_CACHE_DIR / f"_extract_{model_size}"
    extract_dir.mkdir(exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
    zip_path.unlink(missing_ok=True)
    # Find the .mlmodelc directory
    for root, dirs, _files in os.walk(str(extract_dir)):
        root_path = Path(root)
        for d in dirs:
            candidate = root_path / d
            if candidate.name.endswith(".mlmodelc"):
                if coreml_path.exists():
                    shutil.rmtree(coreml_path, ignore_errors=True)
                shutil.move(str(candidate), str(coreml_path))
                shutil.rmtree(extract_dir, ignore_errors=True)
                _log_coreml_found(coreml_path, " installed")
                return coreml_path
    shutil.rmtree(extract_dir, ignore_errors=True)
    return None


async def _ensure_coreml_model(
    ggml_path: Path,
    model_size: str,
) -> Path | None:
    """Ensure CoreML model (.mlmodelc) is available for ANE acceleration."""
    config = _MODEL_CONFIGS.get(model_size)
    if config is None:
        return None

    coreml_name = config["coreml_name"]
    coreml_path = _MODEL_CACHE_DIR / coreml_name

    # Strategy 1: Check cache location
    if _is_coreml_model_valid(coreml_path):
        _log_coreml_found(coreml_path)
        return coreml_path

    # Also check next to ggml file (whisper.cpp convention)
    sibling_path = ggml_path.parent / coreml_name
    if _is_coreml_model_valid(sibling_path):
        _log_coreml_found(sibling_path, " (sibling)")
        return sibling_path

    # Strategy 2: Download pre-converted CoreML model
    if not _check_ane():
        logger.debug("[WhisperEngine] ANE unavailable — skipping CoreML download")
        return None

    coreml_url = _COREML_MODEL_URLS.get(model_size)
    if coreml_url is None:
        return None

    logger.info(
        "[WhisperEngine] Downloading pre-converted CoreML %s model...", model_size
    )
    zip_path = _MODEL_CACHE_DIR / f"{coreml_name}.zip"
    if not await _download_coreml_zip(coreml_url, zip_path, _MODEL_DOWNLOAD_TIMEOUT_S):
        if not await _curl_download_coreml(coreml_url, zip_path, _MODEL_DOWNLOAD_TIMEOUT_S):
            return None

    # Extract and install
    if extracted := await _extract_coreml_model(zip_path, coreml_path, model_size):
        return extracted

    # Strategy 3: Log manual instructions
    logger.info(
        "[WhisperEngine] CoreML model not available. To enable ANE acceleration:\n"
        "  1. Install whisper.cpp: git clone https://github.com/ggerganov/whisper.cpp\n"
        "  2. Generate CoreML model: cd whisper.cpp && ./models/generate-coreml-model.sh %s\n"
        "  3. Copy to cache: cp -r models/ggml-%s-encoder.mlmodelc %s/",
        model_size,
        model_size,
        _MODEL_CACHE_DIR,
    )
    return None


# ─── WhisperEngine ───────────────────────────────────────────────────────────

class WhisperEngine:
    """
    whisper.cpp transcription engine with CoreML/ANE acceleration.

    Thread-safe singleton. Coordinates with _MLXFamilyMutex for M1 memory budget.

    Usage:
        engine = WhisperEngine()
        await engine.initialize()
        result = await engine.transcribe("audio.wav", model_size="tiny")
        # result.text, result.segments, result.language
        await engine.close()
    """

    __slots__ = (
        '_model',
        '_model_size',
        '_coreml_available',
        '_coreml_loaded',
        '_whisper_params',
        '_init_lock',
        '_transcribe_lock',
        '_initialized',
        '_ggml_path',
        '_coreml_path',
        '_temp_dirs',   # [INTERNAL]-009: was dynamic attribute leak — added to __slots__
    )

    def __init__(self) -> None:
        self._model: Any = None
        self._model_size: str = "tiny"
        self._coreml_available: bool = False
        self._coreml_loaded: bool = False
        self._whisper_params: Any = None
        self._init_lock: asyncio.Lock | None = None
        self._transcribe_lock: asyncio.Lock | None = None
        self._initialized: bool = False
        self._ggml_path: Path | None = None
        self._coreml_path: Path | None = None
        self._temp_dirs: list[str] = []   # [INTERNAL]-009: proper init in __slots__

    def _get_init_lock(self) -> asyncio.Lock:
        if self._init_lock is None:
            self._init_lock = asyncio.Lock()
        return self._init_lock

    def _get_transcribe_lock(self) -> asyncio.Lock:
        if self._transcribe_lock is None:
            self._transcribe_lock = asyncio.Lock()
        return self._transcribe_lock

    async def initialize(
        self,
        model_size: Literal["tiny", "base"] = "tiny",
        force_cpu: bool = False,
    ) -> bool:
        """
        Lazy-init whisper.cpp with model download + CoreML setup.
        Idempotent — subsequent calls are no-ops.

        Args:
            model_size: "tiny" (39MB, fast) or "base" (74MB, accurate).
            force_cpu: Skip CoreML/ANE even if available.

        Returns:
            True if engine is ready for transcription.
        """
        if self._initialized and self._model_size == model_size:
            return self._model is not None

        async with self._get_init_lock():
            if self._initialized and self._model_size == model_size:
                return self._model is not None

            if not _check_whispercpp():
                logger.warning("[WhisperEngine] whispercpp not installed — "
                             "install with: uv pip install whispercpp")
                self._initialized = True
                return False

            # Acquire memory slot via MLX family mutex
            try:
                from hledac.universal.brain.ane_embedder import (
                    get_mlx_family_mutex,
                )
                mutex = get_mlx_family_mutex()
                config = _MODEL_CONFIGS.get(model_size, _MODEL_CONFIGS["tiny"])
                if not mutex.try_acquire_embed_ane(config["size_mb"]):
                    logger.warning(
                        "[WhisperEngine] ANE slot busy (LLM active) — retry later"
                    )
                    self._initialized = True
                    return False
                self._coreml_available = _check_ane()
            except ImportError:
                self._coreml_available = False

            self._model_size = model_size

            # Step 1: Download ggml model
            ggml_path = await _download_model_ggml(model_size)
            if ggml_path is None:
                logger.error("[WhisperEngine] Failed to download %s model", model_size)
                self._initialized = True
                return False
            self._ggml_path = ggml_path

            # Step 2: Set up CoreML model if ANE available
            if self._coreml_available and not force_cpu:
                coreml_path = await _ensure_coreml_model(ggml_path, model_size)
                if coreml_path is not None and coreml_path.exists():
                    self._coreml_path = coreml_path
                    logger.info("[WhisperEngine] CoreML model ready: %s", coreml_path)

            # Step 3: Free old model if switching sizes
            if self._model is not None:
                try:
                    if hasattr(self._model, 'free'):
                        self._model.free()
                except Exception:  # noqa: BLE001
                    pass
                self._model = None
                logger.debug(
                    "[WhisperEngine] Freed old %s model for %s switch",
                    self._model_size if self._model_size != model_size else model_size,
                    model_size,
                )

            # Step 4: Initialize whisper.cpp model
            try:
                Whisper = _whispercpp
                self._model = Whisper(str(ggml_path))

                # Set up whisper params
                if hasattr(self._model, 'params'):
                    self._whisper_params = self._model.params
                    # CoreML auto-detection: whisper.cpp checks for
                    # ggml-{size}-encoder.mlmodelc next to the model file
                    if self._coreml_path is not None:
                        logger.info(
                            "[WhisperEngine] whisper.cpp initialized — "
                            "%s model + CoreML ANE encoder",
                            model_size,
                        )
                    else:
                        logger.info(
                            "[WhisperEngine] whisper.cpp initialized — "
                            "%s model (CPU-only)",
                            model_size,
                        )
                else:
                    self._whisper_params = None
                    logger.info("[WhisperEngine] whisper.cpp initialized — %s", model_size)

                self._coreml_loaded = self._coreml_path is not None
                self._initialized = True
                return True

            except Exception as exc:
                logger.error("[WhisperEngine] whisper.cpp init failed: %s", exc)
                self._model = None
                self._initialized = True
                return False

    async def close(self) -> None:
        """Release whisper model, ANE slot, and clean up temp files."""
        async with self._get_init_lock():
            if self._model is not None:
                try:
                    if hasattr(self._model, 'free'):
                        self._model.free()
                except Exception:  # noqa: BLE001
                    pass
                self._model = None
            self._whisper_params = None
            self._initialized = False
            self._coreml_loaded = False

            # Clean up temp dirs from audio conversion
            for tmp_dir in self._temp_dirs:
                try:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:  # noqa: BLE001
                    pass
            self._temp_dirs.clear()

            # Release ANE slot
            try:
                from hledac.universal.brain.ane_embedder import (
                    get_mlx_family_mutex,
                )
                get_mlx_family_mutex().release('embed_ane')
            except ImportError:  # noqa: BLE001
                pass

    async def transcribe(
        self,
        source: str | Path | bytes,
        model_size: Literal["tiny", "base"] = "tiny",
        language: str | None = None,
        translate: bool = False,
        word_timestamps: bool = True,
    ) -> TranscriptionResult | None:
        """
        Transcribe audio to text via whisper.cpp with CoreML/ANE.

        Args:
            source: File path (str/Path) or raw PCM16 audio bytes.
            model_size: "tiny" (default, fastest) or "base" (more accurate).
            language: ISO-639-1 language code (e.g. "en", "ru", "zh").
                     None = auto-detect.
            translate: If True, translate to English (non-English → en).
            word_timestamps: If True, include per-word/sub-segment timestamps.

        Returns:
            TranscriptionResult with text + segments, or None on failure.
        """
        if not self._initialized or self._model_size != model_size:
            ok = await self.initialize(model_size)
            if not ok:
                return None

        async with self._get_transcribe_lock():
            try:
                start_time = time_module.monotonic()

                # Resolve audio source to file path
                audio_path = await self._resolve_audio_path(source)
                if audio_path is None:
                    return None

                # Check file size
                try:
                    file_size = audio_path.stat().st_size
                    if file_size > _MAX_AUDIO_FILE_BYTES:
                        logger.warning(
                            "[WhisperEngine] Audio file too large: %d MB (max %d MB)",
                            file_size // (1024 * 1024),
                            _MAX_AUDIO_FILE_BYTES // (1024 * 1024),
                        )
                        return None
                    if file_size == 0:
                        logger.warning("[WhisperEngine] Empty audio file")
                        return None
                except OSError as exc:
                    logger.warning("[WhisperEngine] Cannot stat audio file: %s", exc)
                    return None

                # Run transcription with timeout
                result = await safe_wait_for(
                    asyncio.to_thread(
                        self._transcribe_sync,
                        str(audio_path),
                        language,
                        translate,
                        word_timestamps,
                    ),
                    timeout=_TRANSCRIBE_TIMEOUT_S,
                )

                duration = time_module.monotonic() - start_time
                if result:
                    result = msgspec.structs.replace(
                        result,
                        duration_s=duration,
                        engine="whisper_cpp",
                        model_size=model_size,
                        coreml_used=self._coreml_loaded,
                    )
                    logger.info(
                        "[WhisperEngine] Transcribed %.1fs audio in %.1fs "
                        "(CoreML=%s, lang=%s, confidence=%.2f)",
                        result.duration_s,
                        duration,
                        self._coreml_loaded,
                        result.language,
                        result.confidence,
                    )
                return result

            except asyncio.TimeoutError:
                logger.warning(
                    "[WhisperEngine] Transcription timed out after %.0fs",
                    _TRANSCRIBE_TIMEOUT_S,
                )
                return None
            except Exception as exc:
                logger.warning("[WhisperEngine] Transcription failed: %s", exc)
                return None

    def _transcribe_sync(
        self,
        audio_path_str: str,
        language: str | None,
        translate: bool,
        word_timestamps: bool,
    ) -> TranscriptionResult | None:
        """Synchronous transcription — runs in thread pool via asyncio.to_thread."""
        try:
            if self._model is None:
                return None

            # Build params
            params_kwargs: dict[str, Any] = {
                "language": language or "auto",
                "translate": translate,
                "word_timestamps": word_timestamps,
                "print_realtime": False,
                "print_progress": False,
                "n_threads": _WHISPER_THREADS,
            }

            # Run whisper.cpp transcription
            # The whispercpp package API:
            #   model.transcribe(file_path, **params) → list[dict]
            transcribe_fn = getattr(self._model, 'transcribe', None)
            if transcribe_fn is None:
                logger.error("[WhisperEngine] Model has no transcribe method")
                return None

            segments_raw = transcribe_fn(audio_path_str, **params_kwargs)

            if not segments_raw:
                return TranscriptionResult(text="", language=language or "en")

            # Parse segments
            segments: list[TranscriptionSegment] = []
            full_text_parts: list[str] = []
            total_confidence = 0.0
            detected_language = language or "en"
            audio_duration = 0.0

            for seg in segments_raw:
                text = str(seg.get("text", "")).strip()
                if not text:
                    continue
                start = float(seg.get("t0", 0)) / 100.0  # ms → s
                end = float(seg.get("t1", 0)) / 100.0
                conf = float(seg.get("confidence", 0.0))

                full_text_parts.append(text)
                segments.append(TranscriptionSegment(
                    start_s=start,
                    end_s=end,
                    text=text,
                    confidence=conf,
                ))
                total_confidence += conf
                if end > audio_duration:
                    audio_duration = end

                # Capture detected language from first segment
                if "language" in seg:
                    detected_language = str(seg["language"])

            avg_confidence = (
                total_confidence / len(segments) if segments else 0.0
            )

            return TranscriptionResult(
                text=" ".join(full_text_parts),
                language=detected_language,
                duration_s=audio_duration,
                confidence=avg_confidence,
                segments=segments,
            )

        except Exception as exc:
            logger.warning("[WhisperEngine] _transcribe_sync error: %s", exc)
            return None

    async def _resolve_audio_path(
        self,
        source: str | Path | bytes,
    ) -> Path | None:
        """Resolve audio source to a 16kHz WAV file path.

        Returns a Path to the audio file. For bytes input, writes to a temp
        file that the caller should clean up after transcription completes.
        """
        # Track temp dirs for cleanup — stored in instance to survive method return
        # NOTE: _temp_dirs is in __slots__ and initialized to [] in __init__
        # [INTERNAL]-009: no hasattr check needed — attribute is guaranteed by __slots__

        # Case 1: Already a file path
        if isinstance(source, (str, Path)):
            source_path = Path(str(source))
            if not source_path.exists():
                logger.warning("[WhisperEngine] Audio file not found: %s", source_path)
                return None

            # Check if already in correct format
            suffix = source_path.suffix.lower()
            if suffix == ".wav":
                return source_path

            # Convert to WAV 16kHz
            converted = await _convert_to_wav_16khz(source_path)
            if converted is not None:
                # [INTERNAL]-009: _temp_dirs is in __slots__ — guaranteed to exist
                self._temp_dirs.append(str(converted.parent))
                return converted

            # If ffmpeg unavailable, try to use original file
            logger.debug("[WhisperEngine] No ffmpeg, passing original file to whisper")
            return source_path

        # Case 2: Raw bytes — write to temp file
        if isinstance(source, bytes):
            try:
                tmp_dir = Path(tempfile.mkdtemp(prefix="whisper_audio_"))
                self._temp_dirs.append(str(tmp_dir))
                tmp_path = tmp_dir / "audio_input.pcm"
                tmp_path.write_bytes(source)
                # Convert PCM to WAV if ffmpeg available
                if _check_ffmpeg():
                    converted = await _convert_to_wav_16khz(tmp_path, output_dir=tmp_dir)
                    if converted is not None:
                        return converted
                return tmp_path
            except Exception as exc:
                logger.warning("[WhisperEngine] Failed to write temp audio: %s", exc)
                return None

        logger.warning("[WhisperEngine] Unsupported audio source type: %s", type(source))
        return None


# ─── Module-level singleton accessor ─────────────────────────────────────────

_whisper_engine: WhisperEngine | None = None
_engine_lock = asyncio.Lock()


async def get_whisper_engine() -> WhisperEngine:
    """Get or create the WhisperEngine singleton (async DCLP)."""
    global _whisper_engine
    if _whisper_engine is None:
        async with _engine_lock:
            if _whisper_engine is None:
                _whisper_engine = WhisperEngine()
    return _whisper_engine


# ─── Convenience function ────────────────────────────────────────────────────

async def transcribe_audio(
    source: str | Path | bytes,
    model_size: Literal["tiny", "base"] = "tiny",
    language: str | None = None,
) -> TranscriptionResult | None:
    """
    One-shot audio transcription via whisper.cpp + CoreML/ANE.

    Convenience wrapper — handles engine lifecycle internally.

    Args:
        source: Audio file path or raw PCM bytes.
        model_size: "tiny" (default, 39MB, fast) or "base" (74MB, accurate).
        language: ISO-639-1 code or None for auto-detect.

    Returns:
        TranscriptionResult or None on failure.
    """
    if not is_whisper_available():
        return None
    engine = await get_whisper_engine()
    return await engine.transcribe(source, model_size=model_size, language=language)
