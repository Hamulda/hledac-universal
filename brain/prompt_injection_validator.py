"""
P1G-A: Prompt Injection Validator v1
====================================

Lightweight deterministic heuristic sanitizer for scraped content before
it reaches the Hermes prompt. Bounded, fail-open, no new dependencies.

Detects and neutralizes:
- Instruction override patterns ("ignore previous instructions", "system prompt")
- Model impersonation ("you are ChatGPT")
- Delimiter injection ("### system", "--- ---")
- Hidden markdown/HTML instruction blocks
- Zero-width and extreme control characters

Integration point: Hermes3Engine.generate() after adaptive context preflight,
before _sanitize_for_llm callback or fallback_sanitize.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "PromptInjectionValidationResult",
    "sanitize_prompt_injection_patterns",
]

# ----------------------------------------------------------------------
# Result type
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class PromptInjectionValidationResult:
    safe_text: str
    suspicious: bool
    patterns: tuple[str, ...]
    original_chars: int
    final_chars: int
    reason: str


# ----------------------------------------------------------------------
# Bounded pattern definitions
# All patterns use re.IGNORECASE | re.MULTILINE where needed.
# Compiled once at module load (not per call) — safe for unbounded re use.
# ----------------------------------------------------------------------

# 1. Instruction override phrases (case-insensitive substrings)
_INSTRUCTION_OVERRIDE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_previous_instructions", re.compile(
        r"ignore[\s\w]*previous[\s\w]*instructions?", re.IGNORECASE
    )),
    ("disregard_instructions", re.compile(
        r"disregard[\s\w]*instructions?", re.IGNORECASE
    )),
    ("forget_instructions", re.compile(
        r"forget[\s\w]*instructions?", re.IGNORECASE
    )),
    ("ignore_all_previous", re.compile(
        r"ignore\s+all\s+previous", re.IGNORECASE
    )),
    ("do_not_follow", re.compile(
        r"do\s+not\s+follow", re.IGNORECASE
    )),
    ("ignore_prior", re.compile(
        r"ignore\s+prior", re.IGNORECASE
    )),
]

# 2. System prompt / developer message impersonation
_SYSTEM_IMPERSONATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("system_prompt_injection", re.compile(
        r"(?:^|\n)[\s]*(?:system[\s]*prompt)[\s]*:", re.IGNORECASE | re.MULTILINE
    )),
    ("developer_message_injection", re.compile(
        r"(?:^|\n)[\s]*(?:developer[\s]*message)[\s]*:", re.IGNORECASE | re.MULTILINE
    )),
    ("you_are_chatgpt", re.compile(
        r"you\s+are\s+(?:ChatGPT|claude|gemini|llama|gpt)", re.IGNORECASE
    )),
    ("as_an_ai", re.compile(
        r"as\s+an?\s+(?:AI|artificial\s+intelligence|ML|language\s+model)", re.IGNORECASE
    )),
    ("you_are_an_ai", re.compile(
        r"you\s+are\s+an?\s+(?:AI|artificial\s+intelligence)", re.IGNORECASE
    )),
]

# 3. Delimiter injection (repeated structural markers)
_DELIMITER_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("repeated_hash_system", re.compile(
        r"(?:^[ \t]*[#]{1,6}[\s]*system[\s]*$){2,}",
        re.MULTILINE | re.IGNORECASE
    )),
    ("repeated_dash_system", re.compile(
        r"(?:^[ \t]*[-]{3,}[\s]*(?:system|instruction|role)[\s]*$){2,}",
        re.MULTILINE | re.IGNORECASE
    )),
    ("repeated_underscore_role", re.compile(
        r"(?:^[ \t]*[_]{3,}[\s]*(?:system|instruction)[\s]*$){2,}",
        re.MULTILINE | re.IGNORECASE
    )),
    ("triple_hash_system", re.compile(
        r"(?:^[ \t]*###[\s]*(?:system|instruction|role)[\s]*$)",
        re.MULTILINE | re.IGNORECASE
    )),
]

# 4. Markdown/HTML hidden instruction blocks
_HIDDEN_BLOCK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # HTML comment hiding content from LLM view but visible to scraper parsers
    ("html_comment_injection", re.compile(
        r"<!--[\s\S]*?(?:ignore|system|prompt|instruction|developer)[\s\S]*?-->",
        re.IGNORECASE
    )),
    # Markdown details/summary hide blocks
    ("markdown_details_hide", re.compile(
        r"<details>[\s\S]*?</details>",
        re.IGNORECASE
    )),
    # Zero-width space in domain names (homoglyph attack)
    ("zero_width_chars", re.compile(
        # Zero-width space, zero-width joiner, zero-width non-joiner, word joiner
        r"[​‌‍﻿]"
    )),
    # BOM at unexpected positions (can be used to hide patterns from naive string matching)
    ("bom_injection", re.compile(
        r"﻿"
    )),
]

# 5. Control characters (extreme range — printable ASCII only)
_CONTROL_CHAR_PATTERN: re.Pattern[str] = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]"
)

# Compile all pattern groups for iteration
_ALL_PATTERNS: list[tuple[str, re.Pattern[str]]] = (
    _INSTRUCTION_OVERRIDE_PATTERNS
    + _SYSTEM_IMPERSONATION_PATTERNS
    + _DELIMITER_INJECTION_PATTERNS
    + _HIDDEN_BLOCK_PATTERNS
)

# ----------------------------------------------------------------------
# Sanitization
# ----------------------------------------------------------------------

def sanitize_prompt_injection_patterns(
    text: str,
    *,
    max_chars: int = 200_000,
) -> PromptInjectionValidationResult:
    """
    Scan and sanitize prompt injection patterns from scraped content.

    Fail-open: on any internal error, returns the original text (truncated)
    as a non-suspicious result.

    Args:
        text: Raw scraped content to sanitize.
        max_chars: Hard cap on input length before pattern scanning.

    Returns:
        PromptInjectionValidationResult with sanitized text and metadata.
    """
    original_chars = len(text) if isinstance(text, str) else 0

    # Fail-open: treat non-string as safe empty
    if not isinstance(text, str):
        return PromptInjectionValidationResult(
            safe_text="",
            suspicious=False,
            patterns=(),
            original_chars=0,
            final_chars=0,
            reason="non_string_input",
        )

    # Hard cap — truncate before scanning to bound memory
    if original_chars > max_chars:
        text = text[:max_chars]

    detected: list[str] = []
    result = text

    try:
        # Phase 1: Zero-width / BOM characters (simple replace — no regex needed)
        zw_removed = 0
        for zw_char in ["​", "‌", "‍", "﻿"]:
            count = result.count(zw_char)
            if count:
                zw_removed += count
                result = result.replace(zw_char, "")

        if zw_removed:
            detected.append("zero_width_chars")

        # Phase 2: Control characters outside printable ASCII + valid Unicode categories
        # Replace with space to preserve word boundary approx
        ctrl_removed = len(_CONTROL_CHAR_PATTERN.findall(result))
        if ctrl_removed:
            detected.append("control_chars")
            result = _CONTROL_CHAR_PATTERN.sub(" ", result)

        # Phase 3: Pattern-based injection detection (mark detected, do NOT remove)
        for name, pattern in _ALL_PATTERNS:
            if pattern.search(result):
                if name not in detected:
                    detected.append(name)

        # Phase 4: Collapse excessive whitespace that could hide delimiters
        # Only collapse if delimiter injection was detected — conservative
        if "repeated_hash_system" in detected or "repeated_dash_system" in detected:
            # Replace 2+ consecutive newlines with double newline + warning marker
            result = re.sub(r"\n{3,}", "\n\n[WARN: repeated delimiter removed]\n", result)
            if result != text:
                detected.append("whitespace_collapse")

    except Exception:
        # Fail-open: return original text (within max_chars) as safe
        return PromptInjectionValidationResult(
            safe_text=text[:max_chars] if len(text) > max_chars else text,
            suspicious=False,
            patterns=(),
            original_chars=original_chars,
            final_chars=min(original_chars, max_chars),
            reason="internal_error_fallback",
        )

    final_chars = len(result)
    detected_tuple = tuple(detected)

    return PromptInjectionValidationResult(
        safe_text=result,
        suspicious=len(detected_tuple) > 0,
        patterns=detected_tuple,
        original_chars=original_chars,
        final_chars=final_chars,
        reason=(
            f"detected {len(detected_tuple)} pattern(s): {', '.join(detected_tuple)}"
            if detected_tuple else "clean"
        ),
    )
