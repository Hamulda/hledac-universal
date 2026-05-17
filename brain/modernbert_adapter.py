"""
ModernBertModelAdapter — Sprint F222
=====================================

Bridges ModernBertEngine → ModelEngine protocol.

Usage:
    # Instead of using ModernBertEngine directly:
    engine = ModernBertModelAdapter()
    await engine.load()          # ✓ async load()
    text = await engine.generate("summarize: foo bar")  # raises — not supported
    summary = await engine.generate_structured(...)
    await engine.unload()        # ✓ async unload()
    name = engine.get_current_model_name()  # ✓ sync identity

    # Factory in model_manager (after refactor):
    engine = self._create_modernbert_engine()  # returns ModernBertModelAdapter

Design:
- ModernBertEngine does extractive summarization (no text generation).
- Adapter surfaces generate() as a warning + empty string (not a protocol violation
  — the protocol allows RuntimeError on unsupported ops, but fail-soft is kinder
  for code that tries generate() as a fallback after a Hermes3Engine swap).
- generate_report() delegates to summarize() with the query as a prefix.
- synthesize() likewise wraps summarize() into a simple "context: X" format.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, TypeVar

from pydantic import BaseModel

from .modernbert_engine import ModernBertEngine

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)


class ModernBertModelAdapter:
    """
    Adapter: ModernBertEngine → ModelEngine protocol.

    Wraps an extractive summarization engine (ModernBERT embeddings) and
    exposes it as a ModelEngine so callers can treat it as a drop-in engine.

    Limitations:
    - generate() returns "" (extractive engines don't generate text).
      Callers should check get_current_model_name() and fall back to Hermes
      if they need actual generation.
    - generate_structured() returns default-constructed response_model (empty shell).
      Prefer structured generation to Hermes3Engine for actual structured output.
    """

    def __init__(self, config: Optional[Any] = None):
        self._engine = ModernBertEngine(config=config)
        self._loaded = False
        self._model_name: str = "modernbert-embed-base"

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def load(self) -> bool:
        """Delegate to ModernBertEngine.load() — idempotent, fail-soft."""
        self._loaded = await self._engine.load()
        if self._loaded:
            logger.info("[ModernBertModelAdapter] Loaded ModernBertEngine")
        return self._loaded

    async def unload(self) -> None:
        """Delegate to ModernBertEngine.unload() — clears Metal cache."""
        await self._engine.unload()
        self._loaded = False
        logger.info("[ModernBertModelAdapter] Unloaded")

    # ── Core generation ────────────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_msg: Optional[str] = None,
    ) -> str:
        """
        Not supported on ModernBertModelAdapter.

        ModernBERT is an extractive embedder, not a text generator.
        Returns "" so callers can distinguish "empty result" from "error"
        without a RuntimeError — useful for mixed-engine fallback paths.
        """
        logger.debug(
            "[ModernBertModelAdapter] generate() called but ModernBERT is "
            "extractive-only; returning empty string. "
            "Use generate_report() or synthesize() for summarization."
        )
        return ""

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        system_msg: Optional[str] = None,
        max_retries: int = 2,
        priority: float = 1.0,
    ) -> T:
        """
        Not supported on ModernBertModelAdapter.

        ModernBERT cannot do grammar-constrained structured generation.
        Returns a default-constructed (all fields default) instance of response_model.
        Callers that need real structured output should use Hermes3Engine.
        """
        logger.debug(
            "[ModernBertModelAdapter] generate_structured() called but ModernBERT "
            "cannot do grammar-constrained generation. "
            "Returning default-constructed response_model. "
            "Use Hermes3Engine for actual structured output."
        )
        try:
            return response_model()
        except Exception:
            # Last resort — use model_construct to skip validation
            return response_model.model_construct()  # type: ignore

    # ── Identity ───────────────────────────────────────────────────────────────

    def get_current_model_name(self) -> Optional[str]:
        """Return model identifier (informational, not the same as Hermes model path)."""
        if self._loaded:
            return self._model_name
        return None

    # ── Summarization (primary ModernBERT capability) ─────────────────────────

    async def generate_report(
        self,
        query: str,
        context: List[str],
    ) -> str:
        """
        Synthesize report via extractive summarization.

        Wraps summarize() — prepends query as a directive to the first context item.
        ModernBERT selects the most central context items by embedding similarity.
        """
        if not self._loaded:
            await self.load()

        if not context:
            return ""

        # Prefix first item with query so embeddings include the query direction
        prefixed = [f"Research query: {query}\n\n{item}" for item in context]
        return await self._engine.summarize(prefixed)

    async def synthesize(self, context: dict[str, Any]) -> str:
        """
        General synthesis via extractive summarization.

        Serializes the context dict into a flat string and passes it to summarize().
        """
        if not self._loaded:
            await self.load()

        parts = []
        for key, value in context.items():
            parts.append(f"[{key}]: {value}")

        text = "\n".join(parts)
        return await self._engine.summarize([text])

    # ── passthrough helpers ───────────────────────────────────────────────────

    async def is_ready(self) -> bool:
        """True if the underlying engine is loaded."""
        return self._engine._loaded if hasattr(self._engine, '_loaded') else self._loaded