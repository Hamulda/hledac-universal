"""
brain/brain_coordinator.py — Brain Composition Layer
=================================================


ARCH-SRP-001: Clean SRP separation for brain module.

Composition layer that orchestrates:
- LLMEngine: pure inference (no prompt composition)
- PromptBuilder: pure prompt composition (no inference)
- HypothesisEngine: pivot planning (optional)

This coordinator provides a clean entry point that separates concerns:
- Prompt building is handled by PromptBuilder
- Inference is delegated to LLMEngine
- Memory management is delegated to ModelManager

Usage:
    from hledac.universal.brain import BrainCoordinator, PromptBuilder, LLMEngine

    coordinator = BrainCoordinator(
        llm_engine=GenerationFacade(...),
        prompt_builder=ChatMLPromptFormatter(),
    )
    result = await coordinator.think("What is the latest on X?")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from collections.abc import AsyncIterator
from _core import aclose

if TYPE_CHECKING:
    from collections.abc import Sequence

    from hledac.universal.brain._inference import GenerationFacade
    from hledac.universal.brain._prompts import ChatMLMessage, ChatMLPromptFormatter, PromptRole, PromptTemplate

logger = logging.getLogger(__name__)


class BrainCoordinator:
    """
    Central coordinator for brain module — clean composition layer.

    ARCH-SRP-001: This class solves the God Object problem by ensuring:
    1. LLMEngine only does inference (no prompt composition)
    2. PromptBuilder only does prompt composition (no inference)
    3. This coordinator orchestrates without doing either directly

    M1 8GB: Lazy initialization of components. Nothing is loaded until first use.
    """

    __slots__ = (
        '_hypothesis',
        '_hypothesis_engine',
        '_llm',
        '_llm_engine',
        '_prompt',
        '_prompt_builder',
    )

    def __init__(
        self,
        llm_engine: GenerationFacade,
        prompt_builder: ChatMLPromptFormatter,
        hypothesis_engine: Any | None = None,
    ) -> None:
        """
        Initialize coordinator with injected dependencies.

        Args:
            llm_engine: LLM inference engine (GenerationFacade, DeepHermes3Engine, etc.)
            prompt_builder: Prompt composition (ChatMLPromptFormatter, etc.)
            hypothesis_engine: Optional hypothesis planning engine
        """
        self._llm = llm_engine
        self._prompt = prompt_builder
        self._hypothesis = hypothesis_engine

    async def think(
        self,
        query: str,
        *,
        system_msg: str | None = None,
        context: list[str] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """
        Main entry point for LLM-powered reasoning.

        Combines prompt building and inference in correct order.

        Args:
            query: User query
            system_msg: Optional system message (uses default if None)
            context: Optional context strings for few-shot
            temperature: Optional temperature override
            max_tokens: Optional max tokens override

        Returns:
            Generated text response
        """
        # Build prompt using PromptBuilder (composition responsibility)
        system = system_msg or self._default_system()
        history = self._context_to_history(context) if context else None

        formatted = self._prompt.format_chatml(
            system_msg=system,
            user_msg=query,
            history=history,
    )

        # Delegate to LLMEngine (inference responsibility)
        gen_result = await self._llm.generate(
            formatted,
            temperature=temperature,
            max_tokens=max_tokens,
    )

        # Extract text from GenerateResult
        text = gen_result.text if hasattr(gen_result, 'text') else gen_result

        # Extract thinking if present
        if hasattr(self._prompt, 'extract_thinking'):
            extracted = self._prompt.extract_thinking(text)
            return extracted.get('answer', text)

        return text

    async def think_stream(
        self,
        query: str,
        *,
        system_msg: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 512,
    ) -> AsyncIterator[str]:
        """
        Streaming variant of think().

        Args:
            query: User query
            system_msg: Optional system message
            temperature: Optional temperature override
            max_tokens: Max tokens to generate

        Yields:
            Individual tokens as they are generated
        """
        system = system_msg or self._default_system()

        formatted = self._prompt.format_chatml(
            system_msg=system,
            user_msg=query,
            history=None,
    )

        async for token in self._llm.generate_stream(
            formatted,
            temperature=temperature if temperature is not None else 0.7,
            max_tokens=max_tokens,
        ):
            yield token

    async def analyze(
        self,
        query: str,
        evidence: list[str],
        *,
        template: PromptTemplate | None = None,
    ) -> str:
        """
        Evidence-based analysis using OSINT prompt templates.

        Args:
            query: Analysis query
            evidence: List of evidence strings
            template: Optional custom prompt template

        Returns:
            Structured analysis response
        """
        from hledac.universal.brain._prompts import ANALYSIS_PROMPT

        # Format evidence into prompt
        evidence_text = "\n".join(f"- {e}" for e in evidence)

        template = template or ANALYSIS_PROMPT
        formatted = template.format(
            query=query,
            evidence=evidence_text,
    )

        # Use thinking mode for complex analysis
        gen_result = await self._llm.generate(
            formatted,
            temperature=template.temperature,
            max_tokens=template.max_tokens,
    )

        return gen_result.text if hasattr(gen_result, 'text') else gen_result

    async def synthesize(
        self,
        query: str,
        expert_outputs: list[dict[str, Any]],
    ) -> str:
        """
        Synthesize multiple expert outputs into coherent response.

        Args:
            query: Original query
            expert_outputs: List of {expert, score, output} dicts

        Returns:
            Synthesized response
        """
        from hledac.universal.brain._prompts import EVIDENCE_SYNTHESIS_PROMPT

        # Format expert outputs
        blocks = [f"Original Query: {query}\n\nExpert Analyses:"]
        for i, output in enumerate(expert_outputs, 1):
            blocks.append(
                f"## Expert {i}: {output['expert'].upper()} "
                f"(confidence: {output['score']:.2f})\n{output['output']}"
    )
        blocks.append("\nSynthesize a comprehensive answer combining these perspectives.")

        synthesis_input = "\n".join(blocks)

        # Generate synthesis
        gen_result = await self._llm.generate(
            synthesis_input,
            temperature=EVIDENCE_SYNTHESIS_PROMPT.temperature,
            max_tokens=EVIDENCE_SYNTHESIS_PROMPT.max_tokens,
    )

        return gen_result.text if hasattr(gen_result, 'text') else gen_result

    def _default_system(self) -> str:
        """Default system message for OSINT research."""
        return (
            "You are a thorough OSINT research assistant. "
            "Analyze the provided information and extract actionable intelligence. "
            "Always cite your sources. When uncertain, explicitly state confidence levels."
    )

    def _context_to_history(
        self,
        context: list[str],
    ) -> list[dict[str, str]]:
        """Convert context strings to ChatML history format."""
        history = []
        for i, ctx in enumerate(context):
            if i % 2 == 0:
                history.append({"role": "user", "content": ctx})
            else:
                history.append({"role": "assistant", "content": ctx})
        return history
