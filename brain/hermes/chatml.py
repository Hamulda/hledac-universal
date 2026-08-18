"""
brain/hermes/chatml.py — ChatML Formatting
==========================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- ChatML prompt formatting with system/user/assistant roles
- History-aware conversation formatting
- Token measurement and preparation

M1 8GB: CPU-bound, runs in prep executor.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from mlx_lm import TokenizerWrapper as MLXTokenizer

logger = logging.getLogger(__name__)


def format_chatml(
    system_msg: str,
    user_msg: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    """
    Format message into ChatML format.

    Args:
        system_msg: System message
        user_msg: User message
        history: Conversation history

    Returns:
        Formatted ChatML prompt string
    """
    parts = []
    parts.append(f"<|im_start|>system\n{system_msg}<|im_end|>")
    
    if history:
        for entry in history:
            role = entry.get("role", "user")
            content = entry.get("content", "")
            parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
    
    parts.append(f"<|im_start|>user\n{user_msg}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    
    return "\n".join(parts)


def format_chatml_with_tools(
    system_msg: str,
    user_msg: str,
    history: list[dict[str, str]] | None = None,
    tools: list[dict] | None = None,
) -> str:
    """
    Format message into ChatML format with tool definitions.

    Args:
        system_msg: System message
        user_msg: User message
        history: Conversation history
        tools: List of tool definitions

    Returns:
        Formatted ChatML prompt string with tools
    """
    parts = []
    
    # System with optional tools
    system_parts = [system_msg]
    if tools:
        import json
        tools_str = json.dumps(tools, indent=2)
        system_parts.append(f"\n\nYou have access to the following tools:\n{tools_str}")
    
    parts.append(f"<|im_start|>system\n{''.join(system_parts)}<|im_end|>")
    
    if history:
        for entry in history:
            role = entry.get("role", "user")
            content = entry.get("content", "")
            parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
    
    parts.append(f"<|im_start|>user\n{user_msg}<|im_end|>")
    parts.append("<|im_start|>assistant\n")
    
    return "\n".join(parts)


def measure_tokens(text: str, tokenizer: MLXTokenizer) -> int:
    """
    Measure token count for a text string.

    Args:
        text: Text to measure
        tokenizer: MLX tokenizer

    Returns:
        Token count
    """
    try:
        return len(tokenizer.encode(text))
    except Exception:
        # Fallback: rough estimation
        return len(text) // 4


def truncate_to_token_limit(
    text: str,
    tokenizer: MLXTokenizer,
    max_tokens: int,
) -> str:
    """
    Truncate text to fit within token limit.

    Args:
        text: Text to truncate
        tokenizer: MLX tokenizer
        max_tokens: Maximum tokens allowed

    Returns:
        Truncated text
    """
    if not text:
        return text
    
    tokens = tokenizer.encode(text)
    if len(tokens) <= max_tokens:
        return text
    
    truncated_tokens = tokens[:max_tokens]
    return tokenizer.decode(truncated_tokens)


class ChatMLFormatter:
    """
    Stateful ChatML formatter with caching support.
    
    Can be used for batch formatting with consistent system prompts.
    """
    
    __slots__ = (
        "_system_msg",
        "_tokenizer",
        "_max_context_tokens",
        "_sanitize_fn",
    )
    
    def __init__(
        self,
        system_msg: str,
        tokenizer: MLXTokenizer,
        max_context_tokens: int = 8192,
        sanitize_fn: Callable[[str], str] | None = None,
    ):
        self._system_msg = system_msg
        self._tokenizer = tokenizer
        self._max_context_tokens = max_context_tokens
        self._sanitize_fn = sanitize_fn
    
    def format(
        self,
        user_msg: str,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[str, list[int]]:
        """
        Format and return (prompt, token_ids).
        
        Args:
            user_msg: User message
            history: Optional conversation history
            
        Returns:
            Tuple of (formatted_prompt, token_ids)
        """
        # Apply sanitization if configured
        if self._sanitize_fn:
            user_msg = self._sanitize_fn(user_msg)
        
        prompt = format_chatml(self._system_msg, user_msg, history)
        tokens = self._tokenizer.encode(prompt)
        
        # Truncate if needed
        if len(tokens) > self._max_context_tokens:
            tokens = tokens[: self._max_context_tokens]
            prompt = self._tokenizer.decode(tokens)
        
        return prompt, tokens
    
    @property
    def system_msg(self) -> str:
        return self._system_msg
    
    @property
    def max_context_tokens(self) -> int:
        return self._max_context_tokens
