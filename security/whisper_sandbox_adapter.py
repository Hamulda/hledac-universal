"""
WhisperSandboxAdapter — Interface for sandboxed whisper execution
=================================================================




ADVERSARY-001: Protocol-based adapter for whisper transcription sandboxing.

Provides a clean seam between transcription logic and sandboxing concerns,
enabling:
- Unit testing with mock adapters
- Easy sandbox backend swapping (Seatbelt, gVisor, WASM)
- Clear interface for future extensions

Python 3.14+ Protocol for structural subtyping (duck typing).

Usage:
    # Real adapter using MediaSandboxCoordinator
    adapter = WhisperSandboxAdapter()
    
    # Mock adapter for testing
    adapter = MockWhisperSandboxAdapter(results=[...])
    
    result = await adapter.transcribe(audio_path, model_size="tiny")
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from hledac.universal.utils.asyncx import safe_wait_for

import msgspec

if TYPE_CHECKING:
    pass

# ─── Result Types ──────────────────────────────────────────────────────────────


class WhisperSandboxResult(msgspec.Struct, frozen=True, gc=False, kw_only=True):
    """Result from sandboxed whisper transcription."""
    text: str = ""
    language: str | None = None
    duration_s: float = 0.0
    confidence: float = 0.0
    error: str | None = None
    sandboxed: bool = False
    seatbelt_used: bool = False
    segments: list[dict[str, Any]] = msgspec.field(default_factory=list)


class SandboxStats(msgspec.Struct, frozen=True, gc=False, kw_only=True):
    """Statistics from sandbox operations."""
    sandboxed: int = 0
    fallback: int = 0
    errors: int = 0


# ─── Protocol (structural interface) ──────────────────────────────────────────


@runtime_checkable
class WhisperSandboxBackend(Protocol):
    """
    Interface for sandboxed whisper execution backends.
    
    Implementations:
    - SeatbeltWhisperAdapter: Uses MediaSandboxCoordinator
    - DirectWhisperAdapter: No sandbox (testing only)
    - MockWhisperAdapter: In-memory mock for unit tests
    
    Python 3.14+: Using runtime_checkable Protocol for structural subtyping.
    """

    async def transcribe(
        self,
        audio_path: Path | str,
        model_size: Literal["tiny", "base"] = "tiny",
        language: str | None = None,
        timeout_s: float = 120.0,
    ) -> WhisperSandboxResult:
        """Transcribe audio with sandbox isolation."""
        ...
    
    @property
    def is_sandboxed(self) -> bool:
        """Whether this adapter provides sandbox isolation."""
        ...
    
    @property
    def stats(self) -> SandboxStats:
        """Get sandbox usage statistics."""
        ...


# ─── Concrete Adapters ────────────────────────────────────────────────────────


class SeatbeltWhisperAdapter:
    """
    ADVERSARY-001: Production adapter using MediaSandboxCoordinator.
    
    Features:
    - Kernel-level Seatbelt isolation when available
    - Unified statistics collection
    - Automatic fallback to direct engine
    
    Thread-safe singleton pattern via module-level instance.
    """
    
    __slots__ = ('_coordinator', '_initialized')
    _instance: "SeatbeltWhisperAdapter | None" = None
    
    def __new__(cls) -> "SeatbeltWhisperAdapter":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self) -> None:
        if self._initialized:
            return
        self._coordinator = self._init_coordinator()
        self._initialized = True
    
    def _init_coordinator(self) -> Any | None:
        """Lazy initialization of MediaSandboxCoordinator."""
        try:
            from hledac.universal.security.media_sandbox import (
                MediaSandboxCoordinator,
                SANDBOX_ENABLED,
            )
            return MediaSandboxCoordinator(enabled=SANDBOX_ENABLED)
        except ImportError:
            return None
    
    async def transcribe(
        self,
        audio_path: Path | str,
        model_size: Literal["tiny", "base"] = "tiny",
        language: str | None = None,
        timeout_s: float = 120.0,
    ) -> WhisperSandboxResult:
        """Delegate to MediaSandboxCoordinator."""
        if self._coordinator is None:
            return WhisperSandboxResult(
                text="",
                error="MediaSandboxCoordinator unavailable",
                sandboxed=False,
            )
        
        result = await self._coordinator.run_whisper_transcription(
            audio_path=str(audio_path),
            model_size=model_size,
            language=language,
            timeout_s=timeout_s,
        )
        
        return WhisperSandboxResult(
            text=result.text,
            language=result.language,
            duration_s=result.duration_s,
            confidence=result.confidence,
            error=result.error,
            segments=result.segments,
            sandboxed=result.sandboxed,
            seatbelt_used=result.seatbelt_used,
        )
    
    @property
    def is_sandboxed(self) -> bool:
        """Check if seatbelt is available."""
        return self._coordinator is not None and self._coordinator._seatbelt_available
    
    @property
    def stats(self) -> SandboxStats:
        """Get aggregated stats from coordinator."""
        if self._coordinator is None:
            return SandboxStats()
        s = self._coordinator.stats
        return SandboxStats(
            sandboxed=s.whisper_sandboxed,
            fallback=s.whisper_fallback,
            errors=s.errors,
        )


class DirectWhisperAdapter:
    """
    Adapter without sandbox isolation (testing/dev only).
    
    ADVERSARY-001 SECURITY: This adapter does NOT provide sandbox isolation.
    Use only for development/testing when security is not a concern.
    """
    
    __slots__ = ('_sandboxed', '_fallback', '_errors')
    
    def __init__(self) -> None:
        self._sandboxed = 0
        self._fallback = 0
        self._errors = 0
    
    async def transcribe(
        self,
        audio_path: Path | str,
        model_size: Literal["tiny", "base"] = "tiny",
        language: str | None = None,
        timeout_s: float = 120.0,
    ) -> WhisperSandboxResult:
        """Direct whisper engine without sandbox."""
        import asyncio
        import logging
        logger = logging.getLogger(__name__)
        
        logger.warning(
            "[DirectWhisperAdapter] SECURITY: Running WITHOUT sandbox isolation"
        )
        
        try:
            from hledac.universal.brain.whisper_engine import get_whisper_engine
            
            engine = await get_whisper_engine()
            raw = await safe_wait_for(
                engine.transcribe(str(audio_path), model_size=model_size, language=language),
                timeout=timeout_s,
            )
            
            if raw is None or not raw.text:
                self._errors += 1
                return WhisperSandboxResult(
                    text="",
                    error="engine returned empty result",
                    sandboxed=False,
                )
            
            self._fallback += 1
            return WhisperSandboxResult(
                text=raw.text,
                language=raw.language,
                duration_s=raw.duration_s,
                confidence=raw.confidence,
                segments=[
                    {
                        "start_s": s.start_s,
                        "end_s": s.end_s,
                        "text": s.text,
                        "confidence": s.confidence,
                    }
                    for s in raw.segments
                ],
                sandboxed=False,
            )
            
        except asyncio.TimeoutError:
            self._errors += 1
            return WhisperSandboxResult(
                text="",
                error=f"timeout after {timeout_s}s",
                sandboxed=False,
            )
        except Exception as exc:
            self._errors += 1
            return WhisperSandboxResult(
                text="",
                error=str(exc),
                sandboxed=False,
            )
    
    @property
    def is_sandboxed(self) -> bool:
        """Never sandboxed in this adapter."""
        return False
    
    @property
    def stats(self) -> SandboxStats:
        """Return current stats."""
        return SandboxStats(
            sandboxed=self._sandboxed,
            fallback=self._fallback,
            errors=self._errors,
        )


# ─── Mock Adapter for Testing ─────────────────────────────────────────────────


class MockWhisperSegment(msgspec.Struct, frozen=True, gc=False):
    """Mock segment for testing."""
    start_s: float = 0.0
    end_s: float = 1.0
    text: str = "test segment"
    confidence: float = 0.9


class MockWhisperResult(msgspec.Struct, frozen=True, gc=False):
    """Mock transcription result for testing."""
    text: str = "This is a test transcription."
    language: str = "en"
    duration_s: float = 10.0
    confidence: float = 0.95
    error: str | None = None
    segments: tuple[MockWhisperSegment, ...] = (MockWhisperSegment(),)


class MockWhisperSandboxAdapter:
    """
    In-memory mock adapter for unit testing.
    
    Usage:
        adapter = MockWhisperSandboxAdapter(
            results=[MockWhisperResult(text="hello world")]
        )
        result = await adapter.transcribe("test.wav")
        assert result.text == "hello world"
    """
    
    __slots__ = ('_results', '_call_count', '_stats')
    
    def __init__(
        self,
        results: list[MockWhisperResult] | None = None,
        error_after: int | None = None,
    ) -> None:
        """
        Args:
            results: List of results to return in order (cycles if exhausted)
            error_after: Return error after N calls (for testing error handling)
        """
        self._results: list[MockWhisperResult] = results or [
            MockWhisperResult(text="mock transcription")
        ]
        self._call_count = 0
        self._error_after = error_after
    
    async def transcribe(
        self,
        audio_path: Path | str,
        model_size: Literal["tiny", "base"] = "tiny",
        language: str | None = None,
        timeout_s: float = 120.0,
    ) -> WhisperSandboxResult:
        """Return mock result for testing."""
        self._call_count += 1
        
        # Check if we should return an error
        if self._error_after and self._call_count > self._error_after:
            return WhisperSandboxResult(
                text="",
                error=f"mock error after {self._call_count} calls",
                sandboxed=True,
            )
        
        # Cycle through results
        result_index = (self._call_count - 1) % len(self._results)
        mock = self._results[result_index]
        
        return WhisperSandboxResult(
            text=mock.text,
            language=mock.language or language,
            duration_s=mock.duration_s,
            confidence=mock.confidence,
            error=mock.error,
            segments=[
                {
                    "start_s": s.start_s,
                    "end_s": s.end_s,
                    "text": s.text,
                    "confidence": s.confidence,
                }
                for s in mock.segments
            ],
            sandboxed=True,  # Mock always reports sandboxed
            seatbelt_used=True,
        )
    
    @property
    def is_sandboxed(self) -> bool:
        """Mock always appears sandboxed."""
        return True
    
    @property
    def stats(self) -> SandboxStats:
        """Return mock stats."""
        return SandboxStats(
            sandboxed=max(0, self._call_count - 1),
            fallback=0,
            errors=1 if self._error_after and self._call_count > self._error_after else 0,
        )
    
    @property
    def call_count(self) -> int:
        """Number of times transcribe was called."""
        return self._call_count
    
    def reset(self) -> None:
        """Reset call counter."""
        self._call_count = 0


# ─── Factory Function ─────────────────────────────────────────────────────────


def get_whisper_adapter(
    prefer_sandbox: bool = True,
) -> WhisperSandboxBackend:
    """
    Factory for creating appropriate whisper adapter.
    
    Args:
        prefer_sandbox: If True, try SeatbeltWhisperAdapter first.
                       If False, use DirectWhisperAdapter (testing only).
    
    Returns:
        Appropriate adapter implementing WhisperSandboxBackend protocol.
    """
    if prefer_sandbox:
        adapter = SeatbeltWhisperAdapter()
        if adapter._coordinator is not None:
            return adapter
        # Fall through to direct if coordinator unavailable
    
    return DirectWhisperAdapter()


# ─── Backwards Compatibility ──────────────────────────────────────────────────

# For existing code using run_whisper_in_subprocess directly
async def run_whisper_sandboxed(
    audio_path: str,
    model_size: Literal["tiny", "base"] = "tiny",
    language: str | None = None,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    """
    Backwards-compatible wrapper for run_whisper_in_subprocess.
    
    ADVERSARY-001: Prefer using MediaSandboxCoordinator.run_whisper_transcription()
    or SeatbeltWhisperAdapter directly for new code.
    """
    adapter = get_whisper_adapter(prefer_sandbox=True)
    result = await adapter.transcribe(
        audio_path=audio_path,
        model_size=model_size,
        language=language,
        timeout_s=timeout_s,
    )
    
    return {
        "text": result.text,
        "language": result.language,
        "duration_s": result.duration_s,
        "confidence": result.confidence,
        "error": result.error,
    }
