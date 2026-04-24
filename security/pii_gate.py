"""
SecurityGate - PII Detection and Sanitization
=============================================
Memory-efficient PII detection using regex patterns.
Optimized for M1 8GB RAM - no large ML models.

EARLY PRIVACY GATE AUTHORITY (this module):
- PII detection via regex patterns (email, phone, SSN, etc.)
- Text sanitization with optional masking
- Risk scoring based on PII density
- Always-on fallback sanitizer for fail-safe operation

THIS MODULE IS NOT AUTHORITY FOR:
- Vault/export operations (see vault_manager.py)
- Steganography detection (see stego_detector.py)
- Content blocking/rejection (early gate = detection only)
- Runtime budget/memory management
- Media processing or augmentation

Note: Piiranha MLX model was removed (deprecated).
Uses regex patterns for fast, lightweight PII detection.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class PIICategory(Enum):
    """Categories of PII (Personal Identifiable Information)"""
    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    IP_ADDRESS = "ip_address"
    URL = "url"
    USERNAME = "username"
    DATE = "date"
    PASSPORT = "passport"
    DRIVER_LICENSE = "driver_license"
    ADDRESS = "address"


@dataclass
class PIIMatch:
    """A single PII match found in text"""
    text: str
    category: PIICategory
    start: int
    end: int
    confidence: float
    method: str  # "regex"


@dataclass
class SanitizationResult:
    """Result of sanitization operation"""
    sanitized_text: str
    pii_found: List[PIIMatch]
    pii_count: int
    success: bool
    error: Optional[str] = None
    risk_level: str = "low"
    risk_score: int = 0

    def __post_init__(self):
        if self.pii_found is None:
            self.pii_found = []


class SecurityGate:
    """
    Early privacy gate for PII detection and sanitization.

    ROLE (authority):
        - sanitize(): detect PII and optionally mask with mask_char
        - analyze_risk(): compute risk score based on PII density
        - fallback_sanitize(): always-on fail-safe redaction

    NOT AUTHORITY (non-authority):
        - NO ML models / Piiranha / transformers / torch
        - NO vault/export/encryption
        - NO content blocking or rejection
        - NO runtime memory/budget management
        - NO steganography or media processing

    Lightweight regex-based, bounded scanning (MAX_FALLBACK_LENGTH=10000).
    Optimized for M1 8GB RAM.
    """

    def __init__(
        self,
        threshold: float = 0.85,
        mask_char: str = "*"
    ):
        """
        Initialize SecurityGate.

        Args:
            threshold: Confidence threshold for PII detection (unused, kept for compatibility)
            mask_char: Character to use for masking PII
        """
        self.threshold = threshold
        self.mask_char = mask_char

        self._regex_patterns = self._compile_regex_patterns()

        logger.info("SecurityGate initialized (regex-based)")

    def _compile_regex_patterns(self) -> Dict[PIICategory, re.Pattern]:
        """Compile regex patterns for common PII"""
        patterns = {
            PIICategory.EMAIL: re.compile(
                r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
                re.IGNORECASE
            ),
            PIICategory.PHONE: re.compile(
                r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
                re.IGNORECASE
            ),
            PIICategory.SSN: re.compile(
                r'\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b'
            ),
            PIICategory.CREDIT_CARD: re.compile(
                r'\b(?:\d{4}[-.\s]?){3}\d{4}\b'
            ),
            PIICategory.IP_ADDRESS: re.compile(
                r'\b(?:\d{1,3}\.){3}\d{1,3}\b'
            ),
            PIICategory.URL: re.compile(
                r'https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+/[\w .-]*/?'
            ),
            PIICategory.DATE: re.compile(
                r'\b(?:\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})\b'
            ),
            PIICategory.PASSPORT: re.compile(
                r'\b[A-Z]{2}\d{7,9}\b'
            ),
            PIICategory.DRIVER_LICENSE: re.compile(
                r'\b[A-Z]{1}\d{7,12}\b'
            )
        }

        return patterns

    def sanitize(
        self,
        text: str,
        mask_pii: bool = True,
        return_matches: bool = True
    ) -> SanitizationResult:
        """
        Sanitize text by detecting and optionally masking PII.

        Args:
            text: Input text to sanitize
            mask_pii: Whether to mask PII with asterisks
            return_matches: Return detailed PII matches

        Returns:
            SanitizationResult with sanitized text and PII info
        """
        try:
            if not isinstance(text, str):
                # Explicit coercion to str for non-string inputs (int, float, list, etc.)
                # Ensures sanitized_text contract is always str
                return SanitizationResult(
                    sanitized_text="",
                    pii_found=[],
                    pii_count=0,
                    success=True
                )

            logger.info("[SECURITY] Scanning content for PII...")

            pii_matches: List[PIIMatch] = []

            # Use regex detection
            regex_matches = self._detect_with_regex(text)
            pii_matches.extend(regex_matches)

            unique_matches = self._deduplicate_matches(pii_matches)

            # Calculate risk
            risk_score = len(unique_matches) * 5
            risk_level = "high" if risk_score > 20 else "medium" if risk_score > 5 else "low"

            sanitized_text = text
            if mask_pii and unique_matches:
                sanitized_text = self._mask_pii(text, unique_matches)
                logger.info(f"[SECURITY] Masked {len(unique_matches)} PII items")

            return SanitizationResult(
                sanitized_text=sanitized_text,
                pii_found=unique_matches if return_matches else [],
                pii_count=len(unique_matches),
                success=True,
                risk_level=risk_level,
                risk_score=risk_score
            )

        except Exception as e:
            logger.error(f"Sanitization failed: {e}")
            return SanitizationResult(
                sanitized_text=text,
                pii_found=[],
                pii_count=0,
                success=False,
                error=str(e)
            )

    def _detect_with_regex(self, text: str) -> List[PIIMatch]:
        """Detect PII using regex patterns"""
        matches = []

        for category, pattern in self._regex_patterns.items():
            for match in pattern.finditer(text):
                pii_match = PIIMatch(
                    text=match.group(),
                    category=category,
                    start=match.start(),
                    end=match.end(),
                    confidence=0.8,
                    method="regex"
                )
                matches.append(pii_match)

        logger.debug(f"Regex detected {len(matches)} PII entities")
        return matches

    def _deduplicate_matches(self, matches: List[PIIMatch]) -> List[PIIMatch]:
        """Remove duplicate PII matches, preferring higher confidence"""
        # Sort by start position and confidence
        sorted_matches = sorted(
            matches,
            key=lambda m: (m.start, -m.confidence)
        )

        unique: List[PIIMatch] = []
        for match in sorted_matches:
            # Check for overlap with existing matches
            is_overlapping = any(
                self._overlaps(match, existing)
                for existing in unique
            )

            if not is_overlapping:
                unique.append(match)

        return unique

    def _overlaps(self, m1: PIIMatch, m2: PIIMatch) -> bool:
        """Check if two matches overlap"""
        return not (m1.end <= m2.start or m2.end <= m1.start)

    def _mask_pii(self, text: str, matches: List[PIIMatch]) -> str:
        """Mask PII in text"""
        # Sort by position in reverse order to preserve indices
        sorted_matches = sorted(matches, key=lambda m: m.start, reverse=True)

        # O(n) approach: collect segments and join once instead of O(n²) string concat
        segments = []
        last_pos = len(text)
        for match in sorted_matches:
            segments.append(text[match.end:last_pos])
            segments.append(self.mask_char * len(match.text))
            last_pos = match.start
        segments.append(text[:last_pos])
        return ''.join(reversed(segments))

    def analyze_risk(self, text: str) -> Dict[str, Any]:
        """
        Analyze PII risk in text.

        Returns:
            Risk analysis including level, score, and breakdown
        """
        # Fail-soft: coerce non-string to empty string to avoid TypeError
        if not isinstance(text, str):
            return {
                "risk_level": "low",
                "risk_score": 0,
                "detection_count": 0,
                "by_category": {},
                "method": "regex"
            }
        matches = self._detect_with_regex(text)

        # Deduplicate matches for consistent risk scoring with sanitize()
        unique_matches = self._deduplicate_matches(matches)

        # Count by category
        by_category = {}
        for match in unique_matches:
            cat = match.category.value
            by_category[cat] = by_category.get(cat, 0) + 1

        risk_score = len(unique_matches) * 5
        risk_level = "high" if risk_score > 20 else "medium" if risk_score > 5 else "low"

        return {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "detection_count": len(unique_matches),
            "by_category": by_category,
            "method": "regex"
        }

    def unload(self) -> None:
        """Unload resources (no-op for regex-based detection)"""
        pass

    def get_stats(self) -> Dict[str, Any]:
        """Get security gate statistics"""
        return {
            "threshold": self.threshold,
            "regex_patterns": len(self._regex_patterns),
            "method": "regex"
        }


# Lazy singleton for quick_sanitize
_DEFAULT_GATE: Optional["SecurityGate"] = None


# Convenience functions
def create_security_gate(
    threshold: float = 0.85,
    mask_char: str = "*"
) -> SecurityGate:
    """
    Create a SecurityGate instance.

    Args:
        threshold: Confidence threshold for PII detection
        mask_char: Character to use for masking PII

    Returns:
        Configured SecurityGate instance
    """
    return SecurityGate(
        threshold=threshold,
        mask_char=mask_char
    )


def quick_sanitize(text: str, mask_char: str = "*") -> str:
    """
    Quick sanitize function for one-off operations.

    Args:
        text: Text to sanitize
        mask_char: Character to use for masking

    Returns:
        Sanitized text
    """
    global _DEFAULT_GATE
    try:
        # Recreate gate if mask_char differs from singleton's mask_char
        # This ensures deterministic behavior wrt mask_char parameter
        if _DEFAULT_GATE is None or _DEFAULT_GATE.mask_char != mask_char:
            _DEFAULT_GATE = create_security_gate(mask_char=mask_char)
        result = _DEFAULT_GATE.sanitize(text, mask_pii=True, return_matches=False)
        return result.sanitized_text
    except Exception:
        # Fail-safe: fall back to regex-based sanitizer
        return fallback_sanitize(text)


# =============================================================================
# FALLBACK PII MASKER - Always-on mandatory PII masking
# Used when main SecurityGate is unavailable
# =============================================================================

# Compiled regex patterns for fallback masking (high-confidence categories only)
# US-centric patterns
_FALLBACK_PATTERNS = {
    "EMAIL": re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', re.IGNORECASE),
    "PHONE": re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),
    "SSN": re.compile(r'\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b'),
    "CREDIT_CARD": re.compile(r'\b(?:\d{4}[-.\s]?){3}\d{4}\b'),
    "IP_ADDRESS": re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),
    "DRIVER_LICENSE": re.compile(r'\b[A-Z]{1}\d{7,12}\b'),
    # PASSPORT must be last - it overlaps with country codes like DE, FR, GB etc.
    "PASSPORT": re.compile(r'\b[A-Z]{2}\d{7,9}\b'),
}

# International PII patterns (conservative - avoid over-masking)
# These are added to the fallback sanitizer to extend beyond US-centric patterns
# Order matters: more specific patterns first (IBAN before VAT)
_INTERNATIONAL_PATTERNS = {
    # IBAN - International Bank Account Number (most specific first)
    # Format: 2-letter country code + 2 check digits + up to 30 alphanumeric
    # Total length: 15-34 characters (conservative range)
    # Example: DE89 3704 0044 0532 0130 00
    "IBAN": re.compile(
        r'\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b'
    ),
    # EU VAT Number - very conservative pattern
    # Format: 2-letter country code + 2-12 digits (no letters)
    # Example: DE123456789, FR12345678901
    # Must be followed by word boundary or end
    "EU_VAT": re.compile(
        r'\b(?:AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK)\d{4,12}\b',
        re.IGNORECASE
    ),
    # E.164 International Phone Format - more comprehensive than US-only
    # Matches: +[country code][number] with optional separators
    # Format: +XX XXX XXX XXXX or +XX-XXX-XXX-XXXX etc.
    "E164_PHONE": re.compile(
        r'\+(?:\d{1,3}[-.\s]?)?(?:\d{1,4}[-.\s]?){1,4}\d{1,4}'
    ),
    # UK National Insurance Number (NINO) - very specific pattern
    # Format: 2 letters + 6 digits + 1 letter (A, B, C, D)
    # Example: AB 12 34 56 C
    "UK_NINO": re.compile(
        r'\b[A-Z]{2}[-.\s]?\d{6}[-.\s]?[A-D]\b',
        re.IGNORECASE
    ),
    # Czech/Slovak Rodné číslo (Birth Number) - VERY conservative
    # Format: YYMMDD/XXXX or YYMMDDXXXX (10 digits total)
    # Requires slash separator or exact format to avoid over-masking
    # Must be at least 10 digits, optionally with slash
    "CZ_RODNE_CISLO": re.compile(
        r'\b\d{6}[/\s]\d{3,4}\b'  # Very conservative: requires separator
    ),
}

# Token replacements (stable, human-readable)
_PII_TOKENS = {
    "EMAIL": "[REDACTED:EMAIL]",
    "PHONE": "[REDACTED:PHONE]",
    "SSN": "[REDACTED:SSN]",
    "CREDIT_CARD": "[REDACTED:CREDIT_CARD]",
    "IP_ADDRESS": "[REDACTED:IP]",
    "PASSPORT": "[REDACTED:PASSPORT]",
    "DRIVER_LICENSE": "[REDACTED:DL]",
    # International tokens
    "E164_PHONE": "[REDACTED:INTL_PHONE]",
    "UK_NINO": "[REDACTED:NINO]",
    "EU_VAT": "[REDACTED:VAT]",
    "IBAN": "[REDACTED:IBAN]",
    "CZ_RODNE_CISLO": "[REDACTED:RC]",
}

# Max text length for fallback (bounded runtime)
# Must be >= MAX_SANITIZE_LENGTH (8192) to ensure PII at end of long strings is caught
# CRITICAL: 10KB limit prevents catastrophic regex backtracking (EMAIL pattern is O(n²))
MAX_FALLBACK_LENGTH = 10000


def fallback_sanitize(text: str, max_length: int = MAX_FALLBACK_LENGTH) -> str:
    """
    Fallback PII sanitizer using regex patterns.
    ALWAYS runs when main SecurityGate is unavailable.

    This is a mandatory safety net - never returns raw text with PII.

    Args:
        text: Input text to sanitize
        max_length: Maximum text length to process

    Returns:
        Sanitized text with PII replaced by tokens
    """
    if not isinstance(text, str):
        return ""

    # Bound input length for runtime safety (prevent catastrophic regex backtracking)
    # MUST be done before finditer() calls
    text = text[:max_length]

    result = text
    # Process patterns in reverse order (by position) to preserve indices
    # Build list of (start, end, replacement) tuples first
    replacements = []

    # Explicit priority order: international patterns first (more specific)
    # Then US patterns, but PASSPORT must be last due to overlap with country codes
    priority_order = [
        # International (most specific first)
        "IBAN", "EU_VAT", "E164_PHONE", "UK_NINO", "CZ_RODNE_CISLO",
        # US patterns (PASSPORT must be last)
        "EMAIL", "PHONE", "SSN", "CREDIT_CARD", "IP_ADDRESS", "DRIVER_LICENSE", "PASSPORT"
    ]

    # Create priority lookup: lower number = higher priority
    priority_lookup = {cat: idx for idx, cat in enumerate(priority_order)}

    # Build ordered patterns dict while preserving priority
    ordered_patterns = {}
    for cat in priority_order:
        if cat in _INTERNATIONAL_PATTERNS:
            ordered_patterns[cat] = _INTERNATIONAL_PATTERNS[cat]
        elif cat in _FALLBACK_PATTERNS:
            ordered_patterns[cat] = _FALLBACK_PATTERNS[cat]

    for category, pattern in ordered_patterns.items():
        for match in pattern.finditer(result):
            # Include priority in the tuple for sorting
            replacements.append((match.start(), match.end(), _PII_TOKENS[category], priority_lookup.get(category, 999)))

    # Sort by position descending, then by priority ascending (lower = higher priority)
    replacements.sort(key=lambda x: (-x[0], x[3]))

    # Deduplicate: keep only highest priority (lowest number) for each unique position
    # FIX: When higher-priority match overlaps with lower-priority one, REMOVE the lower-priority
    non_overlapping = []
    for start, end, replacement, priority in replacements:
        to_remove = []
        should_skip = False

        for existing_start, existing_end, _, existing_priority in non_overlapping:
            # Check for overlap
            overlaps = not (end <= existing_start or existing_end <= start)

            if overlaps and priority > existing_priority:
                # This match has lower priority and overlaps with higher-priority existing
                should_skip = True
                break
            elif overlaps and priority < existing_priority:
                # This match has higher priority - mark existing lower-priority for removal
                to_remove.append((existing_start, existing_end))

        if should_skip:
            continue

        # Remove lower-priority overlapping matches that this higher-priority one supersedes
        non_overlapping = [(s, e, r, p) for s, e, r, p in non_overlapping
                           if (s, e) not in to_remove]

        non_overlapping.append((start, end, replacement, priority))

    # Apply replacements from HIGHEST start position to LOWEST (reverse order)
    # This ensures earlier replacements don't shift indices of later ones
    non_overlapping.sort(key=lambda x: -x[0])
    # O(n) approach: collect segments and join once instead of O(n²) string concat
    segments = []
    last_pos = len(result)
    for start, end, replacement, priority in non_overlapping:
        segments.append(result[end:last_pos])
        segments.append(replacement)
        last_pos = start
    segments.append(result[:last_pos])
    return ''.join(reversed(segments))


def is_fallback_available() -> bool:
    """Check if fallback sanitizer is available (always True)."""
    return True
