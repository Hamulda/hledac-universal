"""
Sprint F219A: Adaptive Context Policy for DeepHermes on M1 8GB.

Provides runtime preflight guardrails to estimate whether the prompt/context
is safe for generation, and truncate/summarize evidence safely when memory
pressure is elevated.

This module is stdlib-first with optional psutil support.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextBudgetDecision:
    """Result of a context budget decision."""

    mode: str  # normal | reduced | minimal | reject
    max_prompt_chars: int
    max_context_tokens_estimate: int
    reason: str
    memory_available_mb: float | None
    original_chars: int
    final_chars: int
    truncated: bool


def estimate_tokens(text: str) -> int:
    """
    Simple conservative token estimate.

    Uses max(1, len(text) // 4) as a rough character-to-token ratio
    for English text. This is conservative (overestimates tokens for
    short prompts, underestimates for very dense technical content).
    """
    return max(1, len(text) // 4)


def get_available_memory_mb() -> float | None:
    """
    Get available physical memory in MB.

    Returns:
        Available memory in MB, or None if psutil is unavailable.

    Note:
        Does not add psutil as a required dependency — returns None
        gracefully if the import fails.
    """
    try:
        import psutil

        return psutil.virtual_memory().available / (1024 ** 2)
    except Exception:
        return None


# Budget thresholds for M1 8GB (in MB of available memory)
_MEMORY_THRESHOLD_REDUCED = 2500
_MEMORY_THRESHOLD_MINIMAL = 1500
_MEMORY_THRESHOLD_REJECT = 800


def decide_context_budget(
    prompt: str,
    *,
    requested_context_window: int = 8192,
    available_memory_mb: float | None = None,
) -> ContextBudgetDecision:
    """
    Decide how to budget the context window based on memory availability.

    Args:
        prompt: The input prompt string.
        requested_context_window: The context window size requested by the caller.
        available_memory_mb: Current available physical memory in MB.
            If None, psutil is used to determine it. If psutil is unavailable,
            defaults to 'normal' mode.

    Budget policy for M1 8GB:

    normal:
        available_memory_mb is None (psutil unavailable) or >= 2500 MB
        max_context_tokens_estimate = min(requested_context_window, 8192)

    reduced:
        1500 <= available_memory_mb < 2500
        max_context_tokens_estimate = min(requested_context_window, 4096)

    minimal:
        800 <= available_memory_mb < 1500
        max_context_tokens_estimate = min(requested_context_window, 2048)

    reject:
        available_memory_mb < 800
        reason = memory_critical
    """
    original_chars = len(prompt)

    # Fetch memory if not provided
    if available_memory_mb is None:
        available_memory_mb = get_available_memory_mb()

    # Determine mode and budget
    if available_memory_mb is not None and available_memory_mb < _MEMORY_THRESHOLD_REJECT:
        mode = "reject"
        max_context_tokens = 0
        reason = "memory_critical"
        max_prompt_chars = 0
        final_chars = 0
        truncated = False
    elif available_memory_mb is not None and available_memory_mb < _MEMORY_THRESHOLD_MINIMAL:
        mode = "minimal"
        max_context_tokens = min(requested_context_window, 2048)
        reason = f"minimal_memory_available={available_memory_mb:.0f}mb"
        max_prompt_chars = max_context_tokens * 4
        final_chars = min(original_chars, max_prompt_chars)
        truncated = original_chars > max_prompt_chars
    elif available_memory_mb is not None and available_memory_mb < _MEMORY_THRESHOLD_REDUCED:
        mode = "reduced"
        max_context_tokens = min(requested_context_window, 4096)
        reason = f"reduced_memory_available={available_memory_mb:.0f}mb"
        max_prompt_chars = max_context_tokens * 4
        final_chars = min(original_chars, max_prompt_chars)
        truncated = original_chars > max_prompt_chars
    else:
        mode = "normal"
        max_context_tokens = min(requested_context_window, 8192)
        if available_memory_mb is None:
            reason = "psutil_unavailable"
        else:
            reason = f"normal_memory_available={available_memory_mb:.0f}mb"
        max_prompt_chars = max_context_tokens * 4
        final_chars = min(original_chars, max_prompt_chars)
        truncated = False

    return ContextBudgetDecision(
        mode=mode,
        max_prompt_chars=max_prompt_chars,
        max_context_tokens_estimate=max_context_tokens,
        reason=reason,
        memory_available_mb=available_memory_mb,
        original_chars=original_chars,
        final_chars=final_chars,
        truncated=truncated,
    )


def apply_context_budget(prompt: str, decision: ContextBudgetDecision) -> str:
    """
    Apply a context budget decision to a prompt.

    Truncation strategy (when truncation is needed):
    - Preserve beginning: system prompt / task instructions
    - Preserve ending: most recent user question or final instruction
    - Trim middle: evidence / context / history

    If the prompt is short enough to fit within max_prompt_chars,
    it is returned unchanged.

    Args:
        prompt: The original prompt string.
        decision: The budget decision from decide_context_budget().

    Returns:
        The truncated prompt (or original if no truncation needed).
    """
    if not decision.truncated:
        return prompt

    max_chars = decision.max_prompt_chars
    if len(prompt) <= max_chars:
        return prompt

    # Strategy: preserve beginning + ending, trim middle
    # Keep first 40% and last 40%, trim middle 20%
    keep_front = int(max_chars * 0.4)
    keep_back = int(max_chars * 0.4)

    # If prompt is very short, just truncate
    if len(prompt) <= keep_front + keep_back:
        return prompt[:max_chars]

    front = prompt[:keep_front]
    back = prompt[-keep_back:] if keep_back > 0 else ""

    # Simple ellipsis marker for truncation
    result = front + "\n\n[... context truncated due to memory pressure ...]\n\n" + back
    return result


def truncate_prompt_simple(
    prompt: str,
    max_chars: int,
    preserve_end_fraction: float = 0.4,
) -> str:
    """
    Truncate prompt preserving beginning and recent end.

    This is a simpler version of apply_context_budget for when
    the caller only needs basic truncation.

    Args:
        prompt: The original prompt.
        max_chars: Maximum characters allowed.
        preserve_end_fraction: Fraction of max_chars to preserve at end.
            Default 0.4 (40% at end, 60% at beginning).

    Returns:
        Truncated prompt with ellipsis marker.
    """
    if len(prompt) <= max_chars:
        return prompt

    keep_front = int(max_chars * (1.0 - preserve_end_fraction))
    keep_back = int(max_chars * preserve_end_fraction)

    front = prompt[:keep_front]
    back = prompt[-keep_back:]

    return front + "\n\n[... truncated ...]\n\n" + back
