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
import re
from dataclasses import dataclass
import msgspec
__all__ = ['PromptInjectionValidationResult', 'sanitize_prompt_injection_patterns']

class PromptInjectionValidationResult(msgspec.Struct, frozen=True):
    safe_text: str
    suspicious: bool
    patterns: tuple[str, ...]
    original_chars: int
    final_chars: int
    reason: str
_INSTRUCTION_OVERRIDE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [('ignore_previous_instructions', re.compile('ignore[\\s\\w]*previous[\\s\\w]*instructions?', re.IGNORECASE)), ('disregard_instructions', re.compile('disregard[\\s\\w]*instructions?', re.IGNORECASE)), ('forget_instructions', re.compile('forget[\\s\\w]*instructions?', re.IGNORECASE)), ('ignore_all_previous', re.compile('ignore\\s+all\\s+previous', re.IGNORECASE)), ('do_not_follow', re.compile('do\\s+not\\s+follow', re.IGNORECASE)), ('ignore_prior', re.compile('ignore\\s+prior', re.IGNORECASE))]
_SYSTEM_IMPERSONATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [('system_prompt_injection', re.compile('(?:^|\\n)[\\s]*(?:system[\\s]*prompt)[\\s]*:', re.IGNORECASE | re.MULTILINE)), ('developer_message_injection', re.compile('(?:^|\\n)[\\s]*(?:developer[\\s]*message)[\\s]*:', re.IGNORECASE | re.MULTILINE)), ('you_are_chatgpt', re.compile('you\\s+are\\s+(?:ChatGPT|claude|gemini|llama|gpt)', re.IGNORECASE)), ('as_an_ai', re.compile('as\\s+an?\\s+(?:AI|artificial\\s+intelligence|ML|language\\s+model)', re.IGNORECASE)), ('you_are_an_ai', re.compile('you\\s+are\\s+an?\\s+(?:AI|artificial\\s+intelligence)', re.IGNORECASE))]
_DELIMITER_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [('repeated_hash_system', re.compile('(?:^[ \\t]*[#]{1,6}[\\s]*system[\\s]*$){2,}', re.MULTILINE | re.IGNORECASE)), ('repeated_dash_system', re.compile('(?:^[ \\t]*[-]{3,}[\\s]*(?:system|instruction|role)[\\s]*$){2,}', re.MULTILINE | re.IGNORECASE)), ('repeated_underscore_role', re.compile('(?:^[ \\t]*[_]{3,}[\\s]*(?:system|instruction)[\\s]*$){2,}', re.MULTILINE | re.IGNORECASE)), ('triple_hash_system', re.compile('(?:^[ \\t]*###[\\s]*(?:system|instruction|role)[\\s]*$)', re.MULTILINE | re.IGNORECASE))]
_HIDDEN_BLOCK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [('html_comment_injection', re.compile('<!--[\\s\\S]*?(?:ignore|system|prompt|instruction|developer)[\\s\\S]*?-->', re.IGNORECASE)), ('markdown_details_hide', re.compile('<details>[\\s\\S]*?</details>', re.IGNORECASE)), ('zero_width_chars', re.compile('[\u200b\u200c\u200d\ufeff]')), ('bom_injection', re.compile('\ufeff'))]
_CONTROL_CHAR_PATTERN: re.Pattern[str] = re.compile('[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f]')
_ALL_PATTERNS: list[tuple[str, re.Pattern[str]]] = _INSTRUCTION_OVERRIDE_PATTERNS + _SYSTEM_IMPERSONATION_PATTERNS + _DELIMITER_INJECTION_PATTERNS + _HIDDEN_BLOCK_PATTERNS

def sanitize_prompt_injection_patterns(text: str, *, max_chars: int=200000) -> PromptInjectionValidationResult:
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
    if not isinstance(text, str):
        return PromptInjectionValidationResult(safe_text='', suspicious=False, patterns=(), original_chars=0, final_chars=0, reason='non_string_input')
    if original_chars > max_chars:
        text = text[:max_chars]
    detected: list[str] = []
    result = text
    try:
        zw_removed = 0
        for zw_char in ['\u200b', '\u200c', '\u200d', '\ufeff']:
            count = result.count(zw_char)
            if count:
                zw_removed += count
                result = result.replace(zw_char, '')
        if zw_removed:
            detected.append('zero_width_chars')
        ctrl_removed = len(_CONTROL_CHAR_PATTERN.findall(result))
        if ctrl_removed:
            detected.append('control_chars')
            result = _CONTROL_CHAR_PATTERN.sub(' ', result)
        for name, pattern in _ALL_PATTERNS:
            if pattern.search(result):
                if name not in detected:
                    detected.append(name)
        if 'repeated_hash_system' in detected or 'repeated_dash_system' in detected:
            result = re.sub('\\n{3,}', '\n\n[WARN: repeated delimiter removed]\n', result)
            if result != text:
                detected.append('whitespace_collapse')
    except Exception:
        return PromptInjectionValidationResult(safe_text=text[:max_chars] if len(text) > max_chars else text, suspicious=False, patterns=(), original_chars=original_chars, final_chars=min(original_chars, max_chars), reason='internal_error_fallback')
    final_chars = len(result)
    detected_tuple = tuple(detected)
    return PromptInjectionValidationResult(safe_text=result, suspicious=detected_tuple, patterns=detected_tuple, original_chars=original_chars, final_chars=final_chars, reason=f"detected {len(detected_tuple)} pattern(s): {', '.join(detected_tuple)}" if detected_tuple else 'clean')