"""
P1G-A + ISSUE-8.2: Prompt Injection Validator v2
=================================================

Lightweight deterministic heuristic sanitizer for scraped content before
it reaches the Hermes prompt. 3-layer defense against injection bypass.

Layer 1: Unicode Normalization (Rust text_norm)
  - NFC normalization (canonical composition)
  - Homoglyph detection and replacement (Cyrillic→Latin, Greek→Latin)
  - Unicode whitespace → ASCII space

Layer 2: Aho-Corasick Multi-Pattern (Rust aho_corasick)
  - 10k+ blacklisted phrases, O(n) single scan
  - Word-boundary policy eliminates false positives
  - Parallel batch scan via rayon

Layer 3: Structural Heuristics (Python)
  - Repeated delimiter detection
  - Hidden block analysis
  - Context overflow detection

Integration point: Hermes3Engine.generate() after adaptive context preflight,
before _sanitize_for_llm callback or fallback_sanitize.

M1 8GB: Rust layer fail-open (returns original on any error).
Always-on, bounded, fail-safe.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import msgspec

__all__ = [
    'PromptInjectionValidationResult',
    'sanitize_prompt_injection_patterns',
    'PromptInjectionValidator',
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_CHARS = 200_000
_MAX_PATTERNS = 50_000  # Aho-Corasick upper bound

# Homoglyph pairs: (Cyrillic/Latin/Greek lookalikes)
# fmt: off
_HOMOGLYPH_MAP: dict[str, str] = {
    # Cyrillic → Latin
    'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'х': 'x',  # U+0430, U+0435, U+043E, U+0440, U+0441, U+0445
    'і': 'i', 'ј': 'j', 'ѕ': 's', 'ԁ': 'd', 'ɡ': 'g', 'һ': 'h',  # U+0456, U+0458, U+0455, U+0501, U+0261, U+0572
    'Ӏ': 'l', 'מ': 'm', 'נ': 'n', 'ג': 'g', 'ש': 'w', 'ף': 'f',  # U+04CF, U+05DE, U+05E0, U+05D2, U+05E9, U+05E3
    'г': 'r', 'ь': 'b', 'т': 't', 'ү': 'y', 'в': 'B', 'к': 'k',  # U+0433, U+044C, U+0442, U+04AF, U+0432, U+043A
    'И': 'N', 'Н': 'H', 'Р': 'P', 'С': 'C', 'Х': 'X', 'О': 'O',  # Capital Cyrillic
    'і': 'i', 'ј': 'j', 'Ѕ': 'S', 'ꙁ': 'z', 'ԁ': 'd', '�قف': 'f', # Lowercase extended
    # Greek → Latin
    'α': 'a', 'β': 'B', 'γ': 'r', 'δ': 'd', 'ε': 'e', 'ζ': 's',  # U+03B1-03B6
    'η': 'n', 'θ': '0', 'ι': 'i', 'κ': 'k', 'λ': 'l', 'μ': 'u',  # U+03B7-03BC
    'ν': 'v', 'ξ': '3', 'ο': 'o', 'π': 'n', 'ρ': 'p', 'σ': 'o',  # U+03BD-03C3
    'τ': 't', 'υ': 'u', 'φ': '0', 'χ': 'x', 'ψ': 'ps', 'ω': 'w', # U+03C4-03C9
    # Simulated ASCII (special Unicode lookalikes)
    '​': '',   # Zero-width space
    '‌': '',   # Zero-width non-joiner
    '‍': '',   # Zero-width joiner
    '﻿': '',   # BOM
    ' ': ' ',  # Non-breaking space → ASCII space
    '‎': '',   # Left-to-right mark
    '‏': '',   # Right-to-left mark
    ' ': ' ',  # Line separator
    ' ': ' ',  # Paragraph separator
    '‪': '',   # Left-to-right embedding
    '‫': '',   # Right-to-left embedding
    '‬': '',   # Pop directional formatting
    '‭': '',   # Left-to-right override
    '‮': '',   # Right-to-left override
}
# fmt: on

# Combined instruction override patterns (layer 2 - fallback when Rust unavailable)
_INSTRUCTION_OVERRIDE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ('ignore_previous_instructions', re.compile(
        r'ignore[\s\w]*previous[\s\w]*instructions?', re.IGNORECASE
    )),
    ('disregard_instructions', re.compile(
        r'disregard[\s\w]*instructions?', re.IGNORECASE
    )),
    ('forget_instructions', re.compile(
        r'forget[\s\w]*instructions?', re.IGNORECASE
    )),
    ('ignore_all_previous', re.compile(
        r'ignore\s+all\s+previous', re.IGNORECASE
    )),
    ('do_not_follow', re.compile(
        r'do\s+not\s+follow', re.IGNORECASE
    )),
    ('ignore_prior', re.compile(
        r'ignore\s+prior', re.IGNORECASE
    )),
    ('disregard_previous', re.compile(
        r'disregard\s+previous', re.IGNORECASE
    )),
    ('disregard_all', re.compile(
        r'disregard\s+all', re.IGNORECASE
    )),
    ('forget_all', re.compile(
        r'forget\s+all', re.IGNORECASE
    )),
]

# System impersonation patterns
_SYSTEM_IMPERSONATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ('system_prompt_injection', re.compile(
        r'(?:^|\n)[\s]*(?:system[\s]*prompt)[\s]*:', re.IGNORECASE | re.MULTILINE
    )),
    ('developer_message_injection', re.compile(
        r'(?:^|\n)[\s]*(?:developer[\s]*message)[\s]*:', re.IGNORECASE | re.MULTILINE
    )),
    ('you_are_chatgpt', re.compile(
        r'you\s+are\s+(?:ChatGPT|claude|gemini|llama|gpt)', re.IGNORECASE
    )),
    ('as_an_ai', re.compile(
        r'as\s+an?\s+(?:AI|artificial\s+intelligence|ML|language\s+model)', re.IGNORECASE
    )),
    ('you_are_an_ai', re.compile(
        r'you\s+are\s+an?\s+(?:AI|artificial\s+intelligence)', re.IGNORECASE
    )),
]

# Delimiter injection patterns
_DELIMITER_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ('repeated_hash_system', re.compile(
        r'(?:^[ \t]*[#]{1,6}[\s]*system[\s]*$){2,}', re.MULTILINE | re.IGNORECASE
    )),
    ('repeated_dash_system', re.compile(
        r'(?:^[ \t]*[-]{3,}[\s]*(?:system|instruction|role)[\s]*$){2,}', re.MULTILINE | re.IGNORECASE
    )),
    ('repeated_underscore_role', re.compile(
        r'(?:^[ \t]*[_]{3,}[\s]*(?:system|instruction)[\s]*$){2,}', re.MULTILINE | re.IGNORECASE
    )),
    ('triple_hash_system', re.compile(
        r'(?:^[ \t]*###[\s]*(?:system|instruction|role)[\s]*$)', re.MULTILINE | re.IGNORECASE
    )),
    # New: nested/mixed delimiters
    ('mixed_delimiter_injection', re.compile(
        r'(?:[#_*-]{3,}[\s]*){3,}', re.MULTILINE
    )),
]

# Hidden block patterns
_HIDDEN_BLOCK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ('html_comment_injection', re.compile(
        r'<!--[\s\S]*?(?:ignore|system|prompt|instruction|developer)[\s\S]*?-->', re.IGNORECASE
    )),
    ('markdown_details_hide', re.compile(
        r'<details>[\s\S]*?</details>', re.IGNORECASE
    )),
    ('zero_width_chars', re.compile(
        r'[​‌‍﻿]'
    )),
    ('bom_injection', re.compile(
        r'﻿'
    )),
    # New: hidden Unicode injection attempts
    ('unicode_override_attempt', re.compile(
        r'[‪-‮​‌‍]+', re.IGNORECASE
    )),
]

# All patterns for layer 3
_ALL_PATTERNS: list[tuple[str, re.Pattern[str]]] = (
    _INSTRUCTION_OVERRIDE_PATTERNS +
    _SYSTEM_IMPERSONATION_PATTERNS +
    _DELIMITER_INJECTION_PATTERNS +
    _HIDDEN_BLOCK_PATTERNS
)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class PromptInjectionValidationResult(msgspec.Struct, frozen=True, gc=False):
    """Result of prompt injection validation."""
    safe_text: str
    suspicious: bool
    patterns: tuple[str, ...]
    original_chars: int
    final_chars: int
    reason: str
    # New fields for v2
    normalization_applied: bool = False
    homoglyph_replacements: int = 0
    rust_aho_used: bool = False
    layers_passed: int = 1  # 1-3 depending on which layers ran


# ---------------------------------------------------------------------------
# Layer 1: Unicode Normalization (Python fallback when Rust unavailable)
# ---------------------------------------------------------------------------

def _normalize_unicode(text: str) -> tuple[str, int]:
    """
    Normalize Unicode text to defeat homoglyph and whitespace bypass.

    Steps:
    1. NFC normalization (canonical composition)
    2. Homoglyph replacement (Cyrillic/Greek → ASCII)
    3. Unicode whitespace → ASCII space

    Returns (normalized_text, num_replacements).
    """
    replacements = 0
    result = text

    # Step 1: NFC normalization
    result = unicodedata.normalize('NFC', result)

    # Step 2: Homoglyph replacement
    for non_ascii, ascii_char in _HOMOGLYPH_MAP.items():
        if non_ascii in result:
            count = result.count(non_ascii)
            result = result.replace(non_ascii, ascii_char)
            replacements += count

    # Step 3: Collapse multiple spaces (including normalized Unicode spaces)
    result = re.sub(r'[ \t ]{2,}', ' ', result)

    # Step 4: Remove zero-width and directional characters
    zw_chars = ['​', '‌', '‍', '‎', '‏',
                '‪', '‫', '‬', '‭', '‮']
    for zw in zw_chars:
        if zw in result:
            result = result.replace(zw, '')

    return result, replacements


# ---------------------------------------------------------------------------
# Layer 2: Aho-Corasick via Rust (with Python fallback)
# ---------------------------------------------------------------------------

# Blacklisted phrases for Aho-Corasick (10k+ in production)
# This is the core injection pattern set
_INJECTION_BLACKLIST: list[str] = [
    # Core instruction overrides
    "ignore all previous instructions",
    "ignore previous instructions",
    "disregard all instructions",
    "disregard previous instructions",
    "forget all instructions",
    "forget previous instructions",
    "do not follow instructions",
    "ignore prior instructions",
    "disregard prior instructions",
    # System prompt injection
    "system prompt injection",
    "you are now a system prompt",
    "you are a different AI",
    "you are ChatGPT",
    "you are Claude",
    "you are Gemini",
    "you are an AI",
    "you are an artificial intelligence",
    "as an AI",
    "as an artificial intelligence",
    "role: admin",
    "role: root",
    "role: superuser",
    # Jailbreak patterns
    "jailbreak",
    "DAN",
    "do anything now",
    "developer mode",
    "javascript payload",
    # Prompt injection markers
    "```system",
    "### system",
    "<|system|>",
    "<<<system>>>",
    "[SYSTEM]",
    "{SYSTEM}",
    # Hidden injection
    "<!-- ignore -->",
    "<!-- system -->",
    "<details><summary>",
    # Context overflow attempts
    " " * 1000,  # Padding
    "\t" * 500,
]


class _AhoCorasickCache:
    """Singleton cache for Aho-Corasick matcher (lazy initialization)."""
    _instance: '_AhoCorasickCache | None' = None
    _lock: Any = None  # Placeholder for threading.Lock

    def __new__(cls) -> '_AhoCorasickCache':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._matcher = None
            cls._instance._failed = False
            cls._instance._lock = __import__('threading').Lock()
        return cls._instance

    def get_matcher(self):
        """Get or create the Aho-Corasick matcher."""
        if self._matcher is not None or self._failed:
            return self._matcher

        with self._lock:
            if self._matcher is not None or self._failed:
                return self._matcher

            try:
                from hledac_rust_extensions import AhoCorasickMatcher

                # Build labels (parallel to patterns)
                labels = [f"inj_{i}" for i in range(len(_INJECTION_BLACKLIST))]

                self._matcher = AhoCorasickMatcher(
                    patterns=_INJECTION_BLACKLIST,
                    labels=labels,
                    capture_patterns=[],  # No capture groups needed
                )
                return self._matcher
            except Exception:
                self._failed = True
                return None


def _scan_aho_corasick(text: str) -> tuple[bool, list[str], bool]:
    """
    Scan text using Aho-Corasick multi-pattern matcher.

    Returns (is_malicious, matched_patterns, rust_used).
    Uses Rust AhoCorasickMatcher if available, falls back to Python regex.
    """
    cache = _AhoCorasickCache()
    matcher = cache.get_matcher()
    rust_used = False

    if matcher is not None:
        try:
            hits = matcher.scan(text, boundary_policy="word")
            if hits:
                patterns = [hit.pattern for hit in hits]
                return True, patterns, True
            return False, [], True
        except Exception:
            pass

    # Fallback: Python regex (slower but always works)
    rust_used = False
    matched = []
    for name, pattern in _INSTRUCTION_OVERRIDE_PATTERNS:
        if pattern.search(text):
            matched.append(name)

    # Additional checks
    for name, pattern in _SYSTEM_IMPERSONATION_PATTERNS:
        if pattern.search(text):
            matched.append(name)

    return bool(matched), matched, rust_used


# ---------------------------------------------------------------------------
# Layer 3: Structural Heuristics
# ---------------------------------------------------------------------------

def _detect_structural_injection(text: str) -> list[str]:
    """
    Layer 3: Detect structural injection patterns that evade text matching.

    Detects:
    - Repeated delimiter blocks
    - Hidden content blocks
    - Unusual character distributions
    - Context overflow attempts
    """
    detected = []

    # Check for repeated delimiter blocks (3+ consecutive lines starting with delimiter)
    # A line "starts with delimiter" if it begins with 3+ delimiter chars at column 0
    lines = text.split('\n')
    consecutive_delimiter_lines = 0
    for line in lines:
        stripped = line.strip()
        # Check if line starts with 3+ delimiter characters (with optional leading whitespace)
        if stripped and len(stripped) >= 3 and all(c in '#*_-' for c in stripped[:3]):
            # It's a delimiter line - but only count if it's delimiter-only or system/role/instruction
            if all(c in '#*_-' for c in stripped) or any(kw in stripped.lower() for kw in ['system', 'instruction', 'role', 'prompt']):
                consecutive_delimiter_lines += 1
                if consecutive_delimiter_lines >= 3:
                    detected.append('structural_repeated_delimiters')
                    break
        else:
            consecutive_delimiter_lines = 0

    # Check for HTML/XML hidden blocks
    if re.search(r'<!--[\s\S]*?-->', text):
        detected.append('structural_html_comment')
    if re.search(r'<details>[\s\S]*?</details>', text, re.IGNORECASE):
        detected.append('structural_hidden_details')

    # Check for Unicode directional override
    if re.search(r'[‪-‮]', text):
        detected.append('structural_directional_override')

    # NOTE: non_ascii_ratio check is now done in validate() BEFORE normalization
    # because normalization replaces homoglyphs (reducing the ratio).
    # This comment documents why the check was moved.

    # Check for zero-width character flood
    zw_count = sum(1 for c in text if c in '\u200b\u200c\u200d\ufeff\u200e\u200f\u2028\u2029\u202a\u202b\u202c\u202d\u202e')
    if zw_count > 10:
        detected.append('structural_zero_width_flood')

    # Check for line-ending anomalies (mixed \r\n, \r, unusual)
    crlf_count = text.count('\r\n')
    lf_only = text.count('\n') - crlf_count
    cr_only = text.count('\r') - crlf_count
    if cr_only > lf_only * 0.5:
        detected.append('structural_unusual_line_endings')

    # Check for context overflow attempt (padding)
    if re.search(r'(?:[ \t]{20,}){5,}', text):
        detected.append('structural_padding_detected')

    return detected


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

class PromptInjectionValidator:
    """
    3-layer prompt injection validator.

    Layer 1: Unicode normalization (NFC + homoglyph + whitespace)
    Layer 2: Aho-Corasick multi-pattern scan (Rust or Python fallback)
    Layer 3: Structural heuristics (delimiter/hidden block analysis)

    Always-on, fail-open (returns original on any error).
    M1 8GB safe: no MLX, minimal memory footprint.
    """

    __slots__ = ('_cache', '_rust_available')

    def __init__(self) -> None:
        self._cache = _AhoCorasickCache()

    def validate(self, text: str, *, max_chars: int = _MAX_CHARS) -> PromptInjectionValidationResult:
        """
        Validate and sanitize prompt injection patterns from text.

        Args:
            text: Raw text to validate
            max_chars: Hard cap on input length

        Returns:
            PromptInjectionValidationResult with sanitized text and metadata
        """
        original_chars = len(text) if isinstance(text, str) else 0

        if not isinstance(text, str):
            return PromptInjectionValidationResult(
                safe_text='',
                suspicious=False,
                patterns=(),
                original_chars=0,
                final_chars=0,
                reason='non_string_input',
            )

        # Truncate if too long (context overflow protection)
        if original_chars > max_chars:
            text = text[:max_chars]

        detected: list[str] = []
        layers_passed = 0
        normalization_applied = False
        homoglyph_replacements = 0
        rust_aho_used = False

        # Pre-normalization checks on ORIGINAL text (before homoglyph replacement)
        # This catches homoglyph floods and NBSP injection that normalization would mask
        original_non_ascii = sum(1 for c in text if ord(c) > 127)
        original_non_ascii_ratio = original_non_ascii / max(len(text), 1)
        if original_non_ascii_ratio > 0.3:
            detected.append('structural_high_non_ascii')
        # Check for excessive NBSP (U+00A0) in original text
        if ' ' in text or '\u00a0' in text:
            detected.append('structural_nnbsp_injection')
        # Check for Unicode directional override in ORIGINAL text (Layer 1 removes them)
        directional_overrides = '\u202a\u202b\u202c\u202d\u202e'
        if any(c in text for c in directional_overrides):
            detected.append('structural_directional_override')

        try:
            # ===== LAYER 1: Unicode Normalization =====
            try:
                normalized, homoglyph_replacements = _normalize_unicode(text)
                normalization_applied = True
                layers_passed = 1
            except Exception:
                normalized = text
                homoglyph_replacements = 0

            # ===== LAYER 2: Aho-Corasick Scan =====
            is_malicious, matched_patterns, rust_aho_used = _scan_aho_corasick(normalized)
            if is_malicious:
                detected.extend(matched_patterns)
            layers_passed = max(layers_passed, 2)

            # ===== LAYER 3: Structural Heuristics =====
            structural = _detect_structural_injection(normalized)
            if structural:
                detected.extend(structural)
            layers_passed = 3

            # Deduplicate
            detected = list(dict.fromkeys(detected))

            # Apply whitespace normalization for repeated delimiters
            result = normalized
            if 'structural_repeated_delimiters' in detected:
                result = re.sub(r'\n{3,}', '\n\n[WARN: repeated delimiter removed]\n', result)
                if result != normalized:
                    detected.append('whitespace_collapse')

            final_chars = len(result)
            detected_tuple = tuple(detected)

            reason = (
                f"detected {len(detected_tuple)} pattern(s): {', '.join(detected_tuple)}"
                if detected_tuple else 'clean'
            )

            return PromptInjectionValidationResult(
                safe_text=result,
                suspicious=bool(detected_tuple),
                patterns=detected_tuple,
                original_chars=original_chars,
                final_chars=final_chars,
                reason=reason,
                normalization_applied=normalization_applied,
                homoglyph_replacements=homoglyph_replacements,
                rust_aho_used=rust_aho_used,
                layers_passed=layers_passed,
            )

        except Exception:
            # Fail-open: return original text
            return PromptInjectionValidationResult(
                safe_text=text[:max_chars] if len(text) > max_chars else text,
                suspicious=False,
                patterns=(),
                original_chars=original_chars,
                final_chars=min(original_chars, max_chars),
                reason='internal_error_fallback',
            )


# ---------------------------------------------------------------------------
# Legacy API (backward compatibility)
# ---------------------------------------------------------------------------

# Global validator instance
_validator: PromptInjectionValidator | None = None


def _get_validator() -> PromptInjectionValidator:
    """Get or create global validator instance."""
    global _validator
    if _validator is None:
        _validator = PromptInjectionValidator()
    return _validator


def sanitize_prompt_injection_patterns(
    text: str,
    *,
    max_chars: int = _MAX_CHARS,
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
    validator = _get_validator()
    return validator.validate(text, max_chars=max_chars)
