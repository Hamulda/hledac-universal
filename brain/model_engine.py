"""
ModelEngine Protocol — Sprint F222
==================================

Canonical interface for all model engines in brain/.
Both Hermes3Engine and ModernBertModelAdapter satisfy this contract.

Rationale:
- Hermes3Engine generates text (ChatML, structured output, synthesis).
- ModernBertEngine does extractive summarization via embeddings — different contract.
- ModernBertModelAdapter bridges ModernBertEngine → ModelEngine so callers
  can swap engines without knowing which strategy is active.

Seam: model_manager._create_*_engine factories return ModelEngine.
Callers use the protocol, not concrete classes.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)


class ModelEngine:
    """
    Protocol contract for text-generation / summarization model engines.

    Implementors must provide:
    - load()         — async, idempotent, returns True when ready
    - unload()       — async, releases memory, clears Metal cache
    - generate()     — async, returns str text from prompt
    - generate_structured() — async, returns Pydantic model from prompt
    - get_current_model_name() — sync, returns model identifier or None

    Optional but supported:
    - generate_report()   — async, synthesizes OSINT report from findings
    - synthesize()        — async, general synthesis
    """

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def load(self) -> bool:
        """
        Load the model into memory.

        Idempotent: calling load() when already loaded returns True immediately.
        Fail-soft: returns False if no backend is available, never raises.
        """
        ...  # type: ignore

    async def unload(self) -> None:
        """
        Unload the model and reclaim memory.

        Must clear Metal cache via mx.eval([]) + mx.metal.clear_cache().
        Idempotent: calling unload() when not loaded is a no-op.
        """
        ...  # type: ignore

    # ── Core generation ────────────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
    ) -> str:
        """
        Generate text from a prompt.

        Args:
            prompt: User prompt text.
            temperature: Sampling temperature (0–1), None uses engine default.
            max_tokens: Max tokens to generate, None uses engine default.
            system_msg: Optional system message for ChatML-formatted engines.

        Returns:
            Generated text string.

        Raises:
            RuntimeError: if model not initialized before generate() call.
        """
        ...  # type: ignore

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
        max_retries: int = 2,
        priority: float = 1.0,
    ) -> T:
        """
        Generate a structured Pydantic response from a prompt.

        Args:
            prompt: User prompt text.
            response_model: Pydantic model class to deserialize into.
            temperature: Sampling temperature, None uses engine default.
            max_tokens: Max tokens, None uses engine default (typically 1024).
            system_msg: Optional system message.
            max_retries: Retry count on schema mismatch (outlines flush).
            priority: Batch queue priority (Hermes3Engine only, ignored elsewhere).

        Returns:
            Instance of response_model.
        """
        ...  # type: ignore

    # ── Identity ───────────────────────────────────────────────────────────────

    def get_current_model_name(self) -> str | None:
        """
        Return the currently loaded model identifier.

        Returns:
            Model name string, or None if no model is loaded.
        """
        ...  # type: ignore

    # ── Optional: synthesis / report ──────────────────────────────────────────

    async def generate_report(
        self,
        query: str,
        context: list[str],
    ) -> str:
        """
        Synthesize an OSINT report from findings/hypotheses.

        Optional — engines that do not support this may return "" or fall back
        to generate() with a constructed prompt.

        Args:
            query: Research query that drove the investigation.
            context: List of context strings (finding payloads, snippets, etc.).

        Returns:
            Markdown report string, or "" if synthesis not supported.
        """
        ...  # type: ignore

    async def synthesize(
        self,
        context: dict[str, Any],
    ) -> str:
        """
        General-purpose synthesis from structured context.

        Optional — general-purpose engines (e.g. Hermes3Engine) support this.
        Embedding-only engines (e.g. ModernBertEngine) may not implement it.

        Args:
            context: Structured context dict with keys like 'findings', 'hypotheses', etc.

        Returns:
            Synthesized text string, or "" if not supported.
        """
        ...  # type: ignore
