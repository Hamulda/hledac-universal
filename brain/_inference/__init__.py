"""
_inference — Generation Module
==============================

PEP 698: Extracted from DeepHermes3Engine generation methods.
PEP 544: Protocol for structural subtyping of LLM engines.

Architecture:
- stream_handler.py: Token streaming abstraction
- generate.py: GenerationFacade (MLX token generation)
- _protocols.py: LLMEngine Protocol (PEP 544)

NOTE: This is NOT brain.inference_engine (abductive reasoning / evidence chaining).
Independence: brain._inference is MLX-generate-only; brain.inference_engine is symbolic.

SRP Separation (ARCH-SRP-001):
- LLMEngine Protocol: pouze inference contract (generate, generate_stream, generate_structured)
- PromptBuilder Protocol: pouze prompt composition (v brain/_prompts.py)
- GenerationFacade: concrete MLX implementation
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator, Protocol, TypeVar

from hledac.universal.brain._inference.stream_handler import StreamHandler
from hledac.universal.brain._inference.generate import GenerationFacade

if TYPE_CHECKING:
    from typing import Any

T = TypeVar("T")


__all__ = [
    # Concrete implementations
    "StreamHandler",
    "GenerationFacade",
    # SRP Protocols (PEP 544)
    "LLMEngine",
]


class LLMEngine(Protocol):
    """
    Protocol for LLM inference engines (PEP 544 structural subtyping).

    SEPARATION OF CONCERNS (ARCH-SRP-001):
    - This protocol defines ONLY inference operations
    - Prompt composition is delegated to PromptBuilder protocol
    - No prompt formatting, templating, or history management here

    This protocol is satisfied by:
    - GenerationFacade (MLX implementation)
    - DeepHermes3Adapter (composition adapter)
    - Any external LLM backend (OpenAI, Anthropic, etc.)

    Usage:
        class MyService:
            def __init__(self, llm: LLMEngine, prompt_builder: PromptBuilder):
                self._llm = llm
                self._prompt_builder = prompt_builder

            async def think(self, query: str) -> str:
                # Build prompt externally
                prompt = self._prompt_builder.format_chatml(
                    system_msg="You are helpful",
                    user_msg=query
                )
                # Delegate inference to engine
                result = await self._llm.generate(prompt)
                return result.text

    """

    async def generate(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
    ) -> str:
        """
        Generate text from pre-formatted prompt.

        Args:
            prompt: Pre-formatted prompt (prompt composition done externally)
            temperature: Sampling temperature (None = model default)
            max_tokens: Maximum tokens to generate (None = model default)
            system_msg: Optional system message override

        Returns:
            Generated text string
        """
        ...

    async def generate_stream(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        system_msg: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """
        Stream generated tokens from pre-formatted prompt.

        Args:
            prompt: Pre-formatted prompt
            max_tokens: Maximum tokens to generate
            system_msg: Optional system message override
            temperature: Sampling temperature

        Yields:
            Individual generated tokens
        """
        ...

    async def generate_structured(
        self,
        prompt: str,
        response_model: type[T],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        system_msg: str | None = None,
        priority: float = 1.0,
    ) -> T:
        """
        Generate and parse structured response.

        Args:
            prompt: Pre-formatted prompt
            response_model: msgspec.Struct type to parse into
            temperature: Sampling temperature
            max_tokens: Maximum tokens
            system_msg: Optional system message
            priority: Batch scheduling priority

        Returns:
            Parsed response as response_model instance
        """
        ...
