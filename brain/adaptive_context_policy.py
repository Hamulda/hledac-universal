"""
Sprint F219A + F285: Adaptive Context Policy for DeepHermes on M1 8GB.

Provides runtime preflight guardrails to estimate whether the prompt/context
is safe for generation, and truncate/summarize evidence safely when memory
pressure is elevated.

F285 Enhancement: Dual-source memory probing —
1. Primary: M1ResourceGovernor (UmaStatus state + available GiB) — canonical path
2. Fallback: psutil.virtual_memory().available — fail-open when Governor unavailable

Governor integration aligns context budget modes with the established UMA state
ladder (soft_warn → warn → critical → emergency), ensuring consistent memory
pressure response across all advisory layers.

This module is stdlib-first with optional psutil support.
"""



from dataclasses import dataclass

# ─── UMA State → Context Budget Mode Mapping ─────────────────────────────────
#
# Aligned with M1ResourceGovernor UmaStatus states:
#   ok (5.0 GiB)         → normal (8192 tokens)
#   soft_warn (5.5 GiB)  → normal (8192 tokens) — proactive headroom
#   warn (6.0 GiB)        → reduced (4096 tokens)
#   critical (6.7 GiB)   → minimal (2048 tokens)
#   emergency (7.0 GiB)  → reject (0 tokens)
#
# Available memory thresholds (MiB free = 8192 - system_used_gib * 1024):
#   5.0 GiB used → ~3072 MiB free  → normal
#   5.5 GiB used → ~2560 MiB free  → reduced
#   6.0 GiB used → ~2048 MiB free  → minimal
#   6.7 GiB used → ~1331 MiB free  → minimal
#   7.0 GiB used → ~1024 MiB free  → reject
#
# Token budgets are applied to max_context_tokens_estimate; actual inference
# uses model-level kv_bits and max_kv_size overrides from deephermes3_engine.

_MEMORY_THRESHOLD_REDUCED = 2048  # MiB free — warn (6.0 GiB used)
_MEMORY_THRESHOLD_MINIMAL = 1332  # MiB free — critical (6.7 GiB used)
_MEMORY_THRESHOLD_REJECT = 1024   # MiB free — emergency (7.0 GiB used)


@dataclass(frozen=True)
class ContextBudgetDecision:
    """Result of a context budget decision."""

    mode: str  # normal | reduced | minimal | reject
    max_prompt_chars: int
    max_context_tokens_estimate: int
    reason: str
    memory_available_mb: float | None
    uma_state: str | None  # governor uma state when available
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


def _get_governor_uma_state() -> tuple[str | None, float | None]:
    """
    Probe M1ResourceGovernor for current UmaStatus.

    Returns:
        Tuple of (uma_state string, available_MiB float).
        (None, None) when Governor unavailable or sample fails.
    """
    try:
        from hledac.universal.core.protocols import get_governor

        gov = get_governor()
        snap = gov.snapshot()
        # GovernorSnapshot has system_used_gib + free_uma_gib
        free_miB = getattr(snap, "free_uma_gib", None)
        if free_miB is not None:
            free_miB = free_miB * 1024  # GiB → MiB
        uma_state = getattr(snap, "uma_state", None)
        return uma_state, free_miB
    except Exception:
        return None, None


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


def decide_context_budget(
    prompt: str,
    *,
    requested_context_window: int = 8192,
    available_memory_mb: float | None = None,
    uma_state: str | None = None,
) -> ContextBudgetDecision:
    """
    Decide how to budget the context window based on memory availability.

    F285: Dual-source probing — Governor (uma_state + free MiB) primary,
    psutil fallback. When Governor is available its state takes precedence
    over raw MiB values to ensure alignment with the UMA state ladder.

    Args:
        prompt: The input prompt string.
        requested_context_window: The context window size requested by the caller.
        available_memory_mb: Current available physical memory in MB.
            If None, Governor or psutil is used to determine it.
        uma_state: Governor UmaStatus state string (ok/soft_warn/warn/
            critical/emergency). When provided alongside available_memory_mb,
            the state overrides the MiB-based mode decision for alignment
            with the Governor's hysteresis ladder.

    Budget policy for M1 8GB (aligned with M1ResourceGovernor):

    normal:   uma_state in (None, ok, soft_warn) OR available >= 2048 MiB
    reduced:  uma_state == warn OR 1332 <= available < 2048 MiB
    minimal:  uma_state == critical OR 1024 <= available < 1332 MiB
    reject:   uma_state == emergency OR available < 1024 MiB
    """
    original_chars = len(prompt)
    effective_state = uma_state
    effective_miB = available_memory_mb

    # F285: Primary — try Governor
    if effective_state is None and effective_miB is None:
        effective_state, effective_miB = _get_governor_uma_state()

    # Fallback: psutil only when Governor unavailable
    if effective_miB is None:
        effective_miB = get_available_memory_mb()

    # ── Mode determination via Governor state ladder ─────────────────────────
    if effective_state in ("emergency",):
        mode = "reject"
        max_context_tokens = 0
        reason = f"uma_emergency_free={effective_miB:.0f}mb" if effective_miB else "uma_emergency"
        max_prompt_chars = 0
        final_chars = 0
        truncated = False
    elif effective_state == "critical" or (
        effective_miB is not None and effective_miB < _MEMORY_THRESHOLD_MINIMAL
    ):
        mode = "minimal"
        max_context_tokens = min(requested_context_window, 2048)
        reason = (
            f"uma_critical_free={effective_miB:.0f}mb"
            if effective_miB
            else "uma_critical"
        )
        max_prompt_chars = max_context_tokens * 4
        final_chars = min(original_chars, max_prompt_chars)
        truncated = original_chars > max_prompt_chars
    elif effective_state == "warn" or (
        effective_miB is not None and effective_miB < _MEMORY_THRESHOLD_REDUCED
    ):
        mode = "reduced"
        max_context_tokens = min(requested_context_window, 4096)
        reason = (
            f"uma_warn_free={effective_miB:.0f}mb"
            if effective_miB
            else "uma_warn"
        )
        max_prompt_chars = max_context_tokens * 4
        final_chars = min(original_chars, max_prompt_chars)
        truncated = original_chars > max_prompt_chars
    else:
        mode = "normal"
        max_context_tokens = min(requested_context_window, 8192)
        if effective_state is not None:
            reason = f"uma_{effective_state}_free={effective_miB:.0f}mb" if effective_miB else f"uma_{effective_state}"
        elif effective_miB is None:
            reason = "psutil_unavailable"
        else:
            reason = f"normal_free={effective_miB:.0f}mb"
        max_prompt_chars = max_context_tokens * 4
        final_chars = min(original_chars, max_prompt_chars)
        truncated = False

    return ContextBudgetDecision(
        mode=mode,
        max_prompt_chars=max_prompt_chars,
        max_context_tokens_estimate=max_context_tokens,
        reason=reason,
        memory_available_mb=effective_miB,
        uma_state=effective_state,
        original_chars=original_chars,
        final_chars=final_chars,
        truncated=truncated,
    )


def apply_context_budget(prompt: str, decision: ContextBudgetDecision) -> str:  # noqa: E501
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
