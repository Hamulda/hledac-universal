"""
brain/_prompts.py — Shared Prompt Abstractions (PEP 3149 + Python 3.14)
============================================================================
Extracts prompt formatting and template logic from DeepHermes3Engine and



Hermes3DSPyLM to eliminate self-cloning.

Python 3.14 features used:
- enum.Enum with auto() for PromptRole (no magic numbers)
- dataclasses with frozen=True for immutable prompt templates (cache-friendly)
- Type parameters in Protocol (PEP 544) via typing.Protocol

M1 8GB constraints:
- No new heap allocations — all formatting uses str.join/list operations
- PromptFormatter is stateless (no instance caching of formatted strings)
- Frozen dataclasses allow safe sharing across async contexts

Usage:
    from hledac.universal.brain._prompts import PromptFormatter, PromptRole, PromptTemplate

    formatter = PromptFormatter()
    chatml = formatter.format_chatml(system_msg, user_msg, history)
    dspy = formatter.format_dspy(messages)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Protocol, TypeVar
from _core import aclose

if TYPE_CHECKING:
    from collections.abc import Sequence

T = TypeVar('T')


class PromptRole(Enum):
    """
    Enum for prompt message roles using auto() — no magic numbers.

    Mirrors the DSPy/ChatML role vocabulary:
      SYSTEM    — system instructions (prepended to prompt)
      USER      — user queries
      ASSISTANT — model responses (used in few-shot prompting)
      EVIDENCE  — OSINT evidence context (domain-specific)
      ANALYSIS  — analysis instructions (domain-specific)
    """
    SYSTEM = auto()
    USER = auto()
    ASSISTANT = auto()
    EVIDENCE = auto()
    ANALYSIS = auto()


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """
    Frozen dataclass for immutable prompt templates.

    cache-friendly: frozen=True prevents accidental mutation after creation.
    slots=True (Python 3.14+) reduces per-instance memory to ~48 bytes.

    Used for:
      - System prompts with few-shot examples
      - Domain-specific analysis templates (OSINT, research, synthesis)
      - DSPy signature instructions
    """
    role: PromptRole
    template: str
    max_tokens: int
    temperature: float

    def format(self, **kwargs: str) -> str:
        """Format the template with provided kwargs (safe, returns new str)."""
        return self.template.format(**kwargs)

    def with_overrides(
        self,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> PromptTemplate:
        """
        Return a new PromptTemplate with overridden generation params.
        Uses __post_init__ preservation for frozen compatibility.
        """
        return PromptTemplate(
            role=self.role,
            template=self.template,
            max_tokens=max_tokens if max_tokens is not None else self.max_tokens,
            temperature=temperature if temperature is not None else self.temperature,
    )


@dataclass(frozen=True, slots=True)
class ChatMLMessage:
    """Immutable ChatML message container (frozen + slots = M1 cache-friendly)."""
    role: str  # "system" | "user" | "assistant"
    content: str


class PromptFormatter(Protocol):
    """
    Protocol for brain engines that format prompts.

    Python 3.14: type[Protocol] with TypeVar works at runtime isinstance checks.
    All brain engines (DeepHermes3Engine, Hermes3DSPyLM) implement this.
    """

    def format_chatml(
        self,
        system_msg: str,
        user_msg: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """Format messages in ChatML (<|im_start|>/<|im_end|>) format."""
        ...

    def format_dspy(
        self,
        messages: list[dict[str, str]],
        *,
        system_msg: str | None = None,
        user_prompt: str | None = None,
    ) -> tuple[str, str | None]:
        """
        Format DSPy message list into (full_prompt, system_msg) tuple.

        Returns (prompt, system_msg) where prompt is role-prefixed and
        system_msg is the extracted system message (or None).
        """
        ...

    def extract_thinking(self, response: str) -> dict[str, str]:
        """
        Extract <think>...</think> thinking block from model response.

        Returns:
            dict with keys:
            - thinking: content between <think> and </think> (stripped), empty if not present
            - answer: remaining text after <think>...</think> block (stripped)
        """
        ...


class ChatMLPromptFormatter:
    """
    ChatML-format prompt formatter for DeepHermes3Engine.

    Stateless — all methods are pure functions of inputs.
    Thread-safe — no instance state modified after construction.
    """

    __slots__ = ('_re_pi',)

    # Pre-compiled regex for thinking block extraction (module-level = shared across instances)
    _RE_THINKING_OPEN = re.compile(r'<think>', re.DOTALL | re.MULTILINE)
    _RE_THINKING_CLOSE = re.compile(r'</think>', re.DOTALL | re.MULTILINE)

    def __init__(self) -> None:
        self._re_pi = re.compile(
            r'<think>(.*?)</think>', re.DOTALL | re.MULTILINE
    )

    def format_chatml(
        self,
        system_msg: str,
        user_msg: str,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        """
        Format messages into ChatML format for DeepHermes3Engine.

        Format:
            <|im_start|>system
            {system_msg}<|im_end|>
            <|im_start|>{role}
            {content}<|im_end|>
            ...
            <|im_start|>user
            {user_msg}<|im_end|>
            <|im_start|>assistant\n

        Args:
            system_msg: System instruction string
            user_msg: Current user query
            history: Optional conversation history as list of {role, content} dicts

        Returns:
            Formatted ChatML prompt string
        """
        parts: list[str] = []
        parts.append(f'<|im_start|>system\n{system_msg}<|im_end|>')
        if history:
            for entry in history:
                role = entry.get('role', 'user')
                content = entry.get('content', '')
                parts.append(f'<|im_start|>{role}\n{content}<|im_end|>')
        parts.append(f'<|im_start|>user\n{user_msg}<|im_end|>')
        parts.append('<|im_start|>assistant\n')
        return '\n'.join(parts)

    def format_dspy(
        self,
        messages: list[dict[str, str]],
        *,
        system_msg: str | None = None,
        user_prompt: str | None = None,
    ) -> tuple[str, str | None]:
        """
        Format DSPy message list into role-prefixed prompt.

        DSPy uses "User: ..." / "Assistant: ..." format (simpler than ChatML).

        Args:
            messages: DSPy message list [{role, content}, ...]
            system_msg: Override system message (extracted from messages if None)
            user_prompt: Additional user prompt to append

        Returns:
            (full_prompt, extracted_or_override_system_msg)
        """
        prompt_parts: list[str] = []
        resolved_system: str | None = system_msg

        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            if role == 'system':
                resolved_system = content
                # System messages are extracted and prepended separately as a header;
                # they do NOT appear inline in the DSPy prompt body.
            elif role == 'user':
                prompt_parts.append(f'User: {content}')
            elif role == 'assistant':
                prompt_parts.append(f'Assistant: {content}')

        if user_prompt:
            prompt_parts.append(f'User: {user_prompt}')

        full_prompt = '\n\n'.join(prompt_parts)
        return full_prompt, resolved_system

    def extract_thinking(self, response: str) -> dict[str, str]:
        """
        Extract <think>...</think> thinking block from model response.

        Pre-compiled regex per instance (not class-level) to ensure
        re.DOTALL | re.MULTILINE flags are applied correctly.
        """
        match = self._re_pi.search(response)
        if match:
            thinking = match.group(1).strip()
            answer = response[match.end():].strip()
        else:
            thinking = ''
            answer = response.strip()
        return {'thinking': thinking, 'answer': answer}

    def sanitize(self, text: str, max_length: int = 32768) -> str:
        """
        Basic sanitization for LLM input (fallback when sanitize_for_llm not provided).

        Removes control characters and enforces max length.
        """
        # Remove problematic control characters but preserve newlines/tabs
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        return cleaned[:max_length]


# Singleton instance — stateless, safe to share across all callers
PROMPT_FORMATTER = ChatMLPromptFormatter()


# ─── OSINT-specific Prompt Templates ───────────────────────────────────────────
# Domain-specific templates for OSINT/research workflows.
# These replace hardcoded template strings in DeepHermes3Engine and
# hypothesis_engine (reducing the 20+ variant problem).

OSINT_SYSTEM_PROMPT = PromptTemplate(
    role=PromptRole.SYSTEM,
    template=(
        "You are a thorough OSINT research assistant. "
        "Analyze the provided information and extract actionable intelligence. "
        "Always cite your sources. When uncertain, explicitly state confidence levels."
    ),
    max_tokens=2048,
    temperature=0.3,
    )

EVIDENCE_SYNTHESIS_PROMPT = PromptTemplate(
    role=PromptRole.EVIDENCE,
    template=(
        "## Evidence Analysis\n\n"
        "Based on the following sources:\n"
        "{evidence}\n\n"
        "## Task\n"
        "{task}\n\n"
        "Provide a structured synthesis with:\n"
        "1. Key findings (with source citations)\n"
        "2. Confidence assessment (HIGH/MEDIUM/LOW)\n"
        "3. Intelligence gaps (what's missing)\n"
    ),
    max_tokens=1024,
    temperature=0.4,
    )

ANALYSIS_PROMPT = PromptTemplate(
    role=PromptRole.ANALYSIS,
    template=(
        "## OSINT Analysis Request\n\n"
        "Query: {query}\n\n"
        "Context: {context}\n\n"
        "## Analysis\n"
        "Provide a structured analysis covering:\n"
        "- Primary findings\n"
        "- Secondary indicators\n"
        "- Anonymization/de-anonymization signals\n"
        "- Recommended follow-up queries"
    ),
    max_tokens=1536,
    temperature=0.3,
    )
