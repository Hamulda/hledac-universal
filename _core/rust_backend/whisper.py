# whisper.py — Whisper ASR domain (SILICON-02)
"""
Rust whisper.cpp transcription via CoreML/ANE backend.

SILICON-02: ANE Speech-to-Text via Whisper.cpp CoreML Backend
===============================================================

Provides high-performance speech-to-text via Apple Neural Engine (ANE) on M1.

Architecture:
    Audio → whisper.cpp (Rust) → WHISPER_COREML=1 → ANE encoder inference → text

M1 8GB Constraints:
    - tiny (39 MB) and base (74 MB) models by default
    - medium (148 MB) model with whisper_medium feature gate
    - Bounded concurrent inference: 1 for tiny/base, 1 for medium (ANE memory)
    - CoreML/ANE uses dedicated memory — does NOT compete with main RAM

Usage:
    from hledac.universal._core.rust_backend import rust
    
    # Check availability
    rust.whisper.is_available()
    
    # Transcribe audio
    result = rust.whisper.transcribe("/path/to/audio.wav", model_size="tiny")
    # result: {text, language, duration_s, confidence, segments, coreml_used, ...}
    
    # Batch transcription (for multi-page PDF audio)
    results = rust.whisper.batch_transcribe(["audio1.wav", "audio2.wav"], model_size="tiny")
    # results: {results: [...], total_files: 2, successful: 2, ...}
    
    # Verify ANE usage
    verification = rust.whisper.verify_ane()
    # verification: {ane_available: True, hardware_path: 'Apple Neural Engine (ANE)', ...}
    
    # Get model cache directory
    cache_dir = rust.whisper.get_cache_dir()
    
    # Get available models
    models = rust.whisper.get_available_models()
    # models: ["tiny", "base"] or ["tiny", "base", "medium"] with feature gate
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from _core._util import aclose


if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _RustWhisperDomain:
    """Rust whisper.cpp transcription via CoreML/ANE."""

    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext.whisper

    def is_available(self) -> bool:
        """Check if whisper models are available."""
        return self._ext.is_available()

    def get_available_models(self) -> list[str]:
        """Get list of available (cached) model sizes."""
        return self._ext.get_available_models()

    def get_cache_dir(self) -> str:
        """Get the model cache directory path."""
        return self._ext.get_cache_dir()

    def transcribe(
        self,
        audio_path: str,
        model_size: str = "tiny",
        language: str | None = None,
        n_threads: int = 4,
    ) -> dict[str, Any]:
        """
        Transcribe audio file to text.

        Args:
            audio_path: Path to audio file (WAV 16kHz mono recommended)
            model_size: "tiny" (39 MB), "base" (74 MB), or "medium" (148 MB, feature-gated)
            language: Language code (e.g., "en") or None for auto-detect
            n_threads: Number of threads for CPU decoder (default: 4)

        Returns:
            Dict with: text, language, duration_s, confidence, segments,
                      coreml_used, model_size, latency_s
        """
        return self._ext.transcribe(audio_path, model_size, language, n_threads)

    def transcribe_with_timestamps(
        self,
        audio_path: str,
        model_size: str = "tiny",
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Transcribe audio with segment timestamps.

        Args:
            audio_path: Path to audio file
            model_size: "tiny", "base", or "medium" (feature-gated)
            language: Language code or None for auto-detect

        Returns:
            List of segment dicts: [{text, start_s, end_s, confidence}, ...]
        """
        return self._ext.transcribe_with_timestamps(audio_path, model_size, language)

    def batch_transcribe(
        self,
        audio_paths: list[str],
        model_size: str = "tiny",
        language: str | None = None,
        n_threads: int = 4,
        max_concurrent: int = 2,
    ) -> dict[str, Any]:
        """
        Batch transcribe multiple audio files.

        Optimized for multi-page PDF audio extraction with bounded concurrency.

        Args:
            audio_paths: List of audio file paths
            model_size: "tiny", "base", or "medium" (feature-gated)
            language: Language code or None for auto-detect
            n_threads: Number of threads for CPU decoder (default: 4)
            max_concurrent: Max concurrent transcriptions (default: 2 for M1 8GB)

        Returns:
            Dict with: results, total_files, successful, failed,
                      total_latency_s, average_latency_s
        """
        return self._ext.batch_transcribe(
            audio_paths, model_size, language, n_threads, max_concurrent
        )

    def verify_ane(self) -> dict[str, Any]:
        """
        Verify ANE is being used for transcription.

        Returns:
            Dict with: ane_available, hardware_path, coreml_models, memory_info
        """
        return self._ext.verify_ane()

    def is_medium_available(self) -> bool:
        """Check if medium model is available (requires whisper_medium feature)."""
        return getattr(self._ext, "MEDIUM_AVAILABLE", False)


class _PythonWhisperDomain:
    """Python fallback for whisper transcription.

    Note: This provides a stub implementation. For actual transcription,
    use the Rust whisper module or the Python whispercpp package directly.
    """

    __slots__ = ()

    def is_available(self) -> bool:
        """Check if whisper is available (always False for Python fallback)."""
        return False

    def get_cache_dir(self) -> str:
        """Get the model cache directory path."""
        import os

        cache = os.path.expanduser("~/.cache/hledac/whisper_models")
        return cache

    def transcribe(
        self,
        audio_path: str,
        model_size: str = "tiny",
        language: str | None = None,
        n_threads: int = 4,
    ) -> dict[str, Any]:
        """
        Fallback: try Python whispercpp or raise ImportError.
        """
        # Try to use the existing whispercpp-based implementation
        try:
            from hledac.universal.brain.whisper_engine import WhisperEngine

            engine = WhisperEngine(model_size=model_size)
            result = engine.transcribe(audio_path, language=language)
            return {
                "text": result.get("text", ""),
                "language": result.get("language", language or "en"),
                "duration_s": result.get("duration_s", 0.0),
                "confidence": result.get("confidence", 0.0),
                "segments": result.get("segments", []),
                "coreml_used": False,
                "model_size": model_size,
                "latency_s": result.get("latency_s", 0.0),
            }
        except ImportError:
            raise NotImplementedError(
                "Rust whisper module not available. "
                "Install with: pip install whispercpp or build Rust extension with whisper feature"
    )

    def transcribe_with_timestamps(
        self,
        audio_path: str,
        model_size: str = "tiny",
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fallback with timestamps."""
        result = self.transcribe(audio_path, model_size, language)
        return result.get("segments", [])


# Module-level singleton for lazy access via rust.whisper
_rust_domain: _RustWhisperDomain | None = None
_python_domain: _PythonWhisperDomain | None = None


def _get_rust_domain() -> _RustWhisperDomain:
    """Get or create the Rust whisper domain."""
    global _rust_domain
    if _rust_domain is None:
        from ._prober import probe

        if probe.ext is not None and hasattr(probe.ext, "whisper"):
            _rust_domain = _RustWhisperDomain(probe.ext)
        else:
            _rust_domain = None  # type: ignore[assignment]
    return _rust_domain


def _get_python_domain() -> _PythonWhisperDomain:
    """Get or create the Python whisper domain."""
    global _python_domain
    if _python_domain is None:
        _python_domain = _PythonWhisperDomain()
    return _python_domain


def __getattr__(name: str) -> Any:
    """Lazy attribute access for domain switching.
    
    Handles attribute errors gracefully to avoid infinite recursion
    when the Rust module is not available.
    """
    # These are accessed from rust.whisper.*
    try:
        rust_domain = _get_rust_domain()
    except (ImportError, AttributeError):
        rust_domain = None
    
    if name == "is_available":
        if rust_domain:
            return rust_domain.is_available()
        return _get_python_domain().is_available()
    elif name == "get_available_models":
        if rust_domain:
            return rust_domain.get_available_models()
        return []
    elif name == "get_cache_dir":
        if rust_domain:
            return rust_domain.get_cache_dir()
        return _get_python_domain().get_cache_dir()
    elif name == "transcribe":
        if rust_domain:
            return rust_domain.transcribe
        return _get_python_domain().transcribe
    elif name == "transcribe_with_timestamps":
        if rust_domain:
            return rust_domain.transcribe_with_timestamps
        return _get_python_domain().transcribe_with_timestamps
    elif name == "batch_transcribe":
        if rust_domain:
            return rust_domain.batch_transcribe
        raise AttributeError(
            "batch_transcribe requires Rust whisper module. "
            "Build Rust extension with whisper feature."
        )
    elif name == "verify_ane":
        if rust_domain:
            return rust_domain.verify_ane
        raise AttributeError(
            "verify_ane requires Rust whisper module. "
            "Build Rust extension with whisper feature."
        )
    elif name == "is_medium_available":
        if rust_domain:
            return rust_domain.is_medium_available()
        return False
    elif name == "extract_voiceprint":
        if rust_domain:
            return rust_domain._ext.extract_voiceprint
        raise AttributeError(
            "extract_voiceprint requires Rust whisper module. "
            "Build Rust extension with whisper feature."
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
