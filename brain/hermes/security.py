"""
brain/hermes/security.py — Prompt Security & Validation
===================================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- Prompt injection detection
- LLM input sanitization
- Security validation

M1 8GB: Fast pattern matching, fail-soft operation.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Injection patterns compiled once
_INJECTION_PATTERNS = [
    re.compile(
        r"ignore\s+(?:all\s+)?previous\s+(?:instructions?|commands?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:system|prompt)\s*:\s*you\s+are\s+(?:now\s+)?a",
        re.IGNORECASE,
    ),
    re.compile(r"#{3,}\s*system\s*[:\s]", re.IGNORECASE),
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"\bROLE\s*:\s*(?:admin|root|superuser)", re.IGNORECASE),
    re.compile(r"(?:jailbreak|DAN|do\s+anything\s+now)", re.IGNORECASE),
    re.compile(r"```\s*system", re.IGNORECASE),
]


def detect_prompt_injection(prompt: str) -> tuple[bool, list[str]]:
    """
    GAP-5: Detect prompt injection patterns in user-controlled input.
    
    Fail-soft: returns (False, []) on any error.
    
    Args:
        prompt: User input prompt
        
    Returns:
        Tuple of (is_injection, matched_pattern_descriptions)
    """
    try:
        matched = [p.pattern for p in _INJECTION_PATTERNS if p.search(prompt)]
        return (bool(matched), matched)
    except Exception:
        return (False, [])


def sanitize_for_llm_fallback(text: str, max_length: int = 8192) -> str:
    """
    Standalone stub when security.pii_gate unavailable.
    
    Args:
        text: Text to sanitize
        max_length: Maximum output length
        
    Returns:
        Sanitized text
    """
    if not text:
        return ""
    
    # Simple sanitization: truncate and strip
    sanitized = text[:max_length]
    
    # Remove common injection markers
    sanitized = re.sub(r"<\|system\|>", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"```system\s*", "```", sanitized, flags=re.IGNORECASE)
    
    return sanitized.strip()


def get_sanitize_function(
    custom_fn: Callable[[str], str] | None = None,
) -> Callable[[str], str]:
    """
    Get appropriate sanitization function.
    
    Args:
        custom_fn: Optional custom sanitization function
        
    Returns:
        Sanitization function
    """
    if custom_fn is not None:
        return custom_fn
    
    # Try to load from pii_gate
    try:
        from brain.security.pii_gate import fallback_sanitize
        return fallback_sanitize
    except ImportError:
        pass
    
    return sanitize_for_llm_fallback


def validate_prompt_security(prompt: str) -> str:
    """
    Validate and sanitize prompt for security.
    
    Args:
        prompt: Raw prompt
        
    Returns:
        Validated prompt (may be modified)
        
    Raises:
        ValueError: If prompt is blocked
    """
    # Check for empty prompt
    if not prompt or not prompt.strip():
        raise ValueError("Empty prompt not allowed")
    
    # Check for injection
    is_injection, matches = detect_prompt_injection(prompt)
    if is_injection:
        logger.warning(f"[SECURITY] Prompt injection detected: {matches}")
        raise ValueError(f"Prompt injection detected: {matches[0][:50] if matches else 'unknown'}")
    
    # Truncate to reasonable length
    max_prompt = 32768  # 32K tokens approximate
    if len(prompt) > max_prompt:
        prompt = prompt[:max_prompt]
        logger.debug(f"[SECURITY] Prompt truncated to {max_prompt} chars")
    
    return prompt


def check_gap5_injection(prompt: str) -> None:
    """
    GAP-5: Check and raise on prompt injection.
    
    Args:
        prompt: Prompt to check
        
    Raises:
        ValueError: If injection detected
    """
    is_injection, matches = detect_prompt_injection(prompt)
    if is_injection:
        raise ValueError(f"GAP-5: Prompt injection detected")


class PromptSecurityValidator:
    """
    Stateful prompt security validator.
    
    Provides configurable security checks.
    """
    
    __slots__ = (
        "_max_length",
        "_allow_empty",
        "_block_patterns",
        "_sanitize_fn",
    )
    
    def __init__(
        self,
        max_length: int = 32768,
        allow_empty: bool = False,
        block_patterns: list[re.Pattern] | None = None,
        sanitize_fn: Callable[[str], str] | None = None,
    ):
        self._max_length = max_length
        self._allow_empty = allow_empty
        self._block_patterns = block_patterns or []
        self._sanitize_fn = sanitize_fn or sanitize_for_llm_fallback
    
    def validate(self, prompt: str) -> str:
        """
        Validate and optionally sanitize prompt.
        
        Args:
            prompt: Raw prompt
            
        Returns:
            Validated (and optionally sanitized) prompt
            
        Raises:
            ValueError: If validation fails
        """
        # Empty check
        if not prompt:
            if not self._allow_empty:
                raise ValueError("Empty prompt not allowed")
            return prompt
        
        # Length check
        if len(prompt) > self._max_length:
            prompt = prompt[:self._max_length]
        
        # Pattern check
        for pattern in self._block_patterns:
            if pattern.search(prompt):
                raise ValueError(f"Prompt blocked by pattern: {pattern.pattern[:30]}")
        
        # Injection check
        is_injection, matches = detect_prompt_injection(prompt)
        if is_injection:
            raise ValueError(f"Prompt injection detected: {matches[0][:50] if matches else 'unknown'}")
        
        # Sanitize
        return self._sanitize_fn(prompt)
    
    @property
    def max_length(self) -> int:
        return self._max_length


# Thinking extraction - use placeholder names to avoid parsing issues
_THINK_START = "THON"
_THINK_END = "HTOX"

def extract_thinking(response: str) -> dict[str, str]:
    """
    Extract thinking content from response.
    
    Looks for think tags in the response.
    
    Args:
        response: Model response text
        
    Returns:
        Dict with 'thinking' and 'answer' keys
    """
    # Use simple string matching
    start_marker = _THINK_START
    end_marker = _THINK_END
    
    start_idx = response.find(start_marker)
    end_idx = response.find(end_marker)
    
    if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
        start_content = start_idx + len(start_marker)
        thinking = response[start_content:end_idx].strip()
        answer = response[end_idx + len(end_marker):].strip()
    else:
        thinking = ""
        answer = response.strip()
    
    return {"thinking": thinking, "answer": answer}
