"""
TextAnalyzerFacade — bounded hook for existing text analyzers.

Scope: Sprint F205G — conservative wiring of dormant text analyzers
into pattern matching seams. No network, no model, no new dependencies.

GHOST_INVARIANTS:
- MAX_TEXT_ANALYZER_BYTES = 4096 — input truncation bound
- MAX_TEXT_ANALYZER_HINTS = 10 — max derived hints per text
- fail-soft: analyzer unavailability does NOT block pipeline
- no external calls (network, filesystem beyond read-only)
- additive only: hints are supplementary signal, NOT filtering
- no changes to CanonicalFinding tuple contract

Analyzers wired (up to 3):
- UnicodeAttackAnalyzer: zero-width, homoglyph, bidi, normalization
- BaseEncodingDetector: base64/base32/hex detection
- HashIdentifier: hash algorithm identification
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# Bounds
MAX_TEXT_ANALYZER_BYTES: int = 4096
MAX_TEXT_ANALYZER_HINTS: int = 10


@dataclass
class TextAnalyzerHint:
    """Single derived hint from text analysis."""
    hint_type: str       # "unicode" | "encoding" | "hash"
    label: str           # "zero_width" | "base64" | "md5" | etc.
    confidence: float    # 0.0–1.0
    detail: str         # human-readable detail


@dataclass
class TextAnalyzerResult:
    """Aggregated result from all text analyzers."""
    hints: List[TextAnalyzerHint] = field(default_factory=list)
    analyzer_errors: int = 0  # count of failed analyzers (fail-soft)

    @property
    def has_findings(self) -> bool:
        return len(self.hints) > 0


def _truncate_text(text: str) -> str:
    """Truncate text to MAX_TEXT_ANALYZER_BYTES."""
    if len(text) <= MAX_TEXT_ANALYZER_BYTES:
        return text
    return text[:MAX_TEXT_ANALYZER_BYTES]


class TextAnalyzerFacade:
    """
    Bounded facade for text analysis analyzers.

    Wires up to 3 analyzers (unicode, encoding, hash) with:
    - MAX_TEXT_ANALYZER_BYTES input truncation
    - MAX_TEXT_ANALYZER_HINTS output bound
    - fail-soft: any analyzer failure is skipped, not propagated
    """

    def __init__(self) -> None:
        self._initialized = False
        self._unicode_analyzer = None
        self._encoding_detector = None
        self._hash_identifier = None

    def _lazy_init(self) -> None:
        """Lazy initialization of analyzers — fail-soft on import error."""
        if self._initialized:
            return

        # Unicode analyzer
        try:
            from hledac.universal.text import UNICODE_ANALYZER_AVAILABLE

            if UNICODE_ANALYZER_AVAILABLE:
                from hledac.universal.text.unicode_analyzer import (
                    UnicodeAttackAnalyzer,
                    UnicodeConfig,
                )
                cfg = UnicodeConfig(
                    detect_zero_width=True,
                    detect_homoglyphs=True,
                    detect_bidi_attacks=True,
                    detect_normalization=False,  # skip normalization (expensive)
                    include_context=False,        # faster, we only need presence
                    chunk_size=65536,
                )
                self._unicode_analyzer = UnicodeAttackAnalyzer(cfg)
                # Load confusable mappings synchronously — M1-safe (no asyncio.run)
                self._unicode_analyzer._load_confusable_mappings()
                self._unicode_analyzer._initialized = True
        except Exception as e:
            logger.debug(f"[TextAnalyzerFacade] Unicode analyzer unavailable: {e}")
            self._unicode_analyzer = None

        # Encoding detector
        try:
            from hledac.universal.text import ENCODING_DETECTOR_AVAILABLE
            if ENCODING_DETECTOR_AVAILABLE:
                from hledac.universal.text.encoding_detector import BaseEncodingDetector
                self._encoding_detector = BaseEncodingDetector()
        except Exception as e:
            logger.debug(f"[TextAnalyzerFacade] Encoding detector unavailable: {e}")
            self._encoding_detector = None

        # Hash identifier
        try:
            from hledac.universal.text import HASH_IDENTIFIER_AVAILABLE
            if HASH_IDENTIFIER_AVAILABLE:
                from hledac.universal.text.hash_identifier import HashIdentifier
                self._hash_identifier = HashIdentifier()
        except Exception as e:
            logger.debug(f"[TextAnalyzerFacade] Hash identifier unavailable: {e}")
            self._hash_identifier = None

        self._initialized = True

    def analyze(self, text: str) -> TextAnalyzerResult:
        """
        Analyze text with all available analyzers.

        Fail-soft: any analyzer error is logged and skipped.
        Output is bounded to MAX_TEXT_ANALYZER_HINTS.

        Args:
            text: Input text (truncated to MAX_TEXT_ANALYZER_BYTES)

        Returns:
            TextAnalyzerResult with derived hints
        """
        self._lazy_init()

        truncated = _truncate_text(text)
        result = TextAnalyzerResult()
        seen_labels: set[str] = set()

        # Unicode analysis
        if self._unicode_analyzer is not None:
            try:
                unicode_result = self._unicode_analyzer.analyze_text(truncated)
                if unicode_result.has_findings():
                    # Zero-width findings
                    for wf in unicode_result.zero_width_findings[:3]:
                        if len(result.hints) >= MAX_TEXT_ANALYZER_HINTS:
                            break
                        if wf.char_name not in seen_labels:
                            seen_labels.add(wf.char_name)
                            result.hints.append(TextAnalyzerHint(
                                hint_type="unicode",
                                label="zero_width",
                                confidence=0.8,
                                detail=f"{wf.char_code} {wf.char_name}",
                            ))
                    # Bidi findings (high risk)
                    for bf in unicode_result.bidi_findings[:2]:
                        if len(result.hints) >= MAX_TEXT_ANALYZER_HINTS:
                            break
                        if bf.attack_type not in seen_labels:
                            seen_labels.add(bf.attack_type)
                            result.hints.append(TextAnalyzerHint(
                                hint_type="unicode",
                                label=f"bidi_{bf.attack_type.lower()}",
                                confidence=0.9,
                                detail=bf.description,
                            ))
            except Exception as e:
                logger.debug(f"[TextAnalyzerFacade] Unicode analysis failed: {e}")
                result.analyzer_errors += 1

        # Encoding detection (synchronous detect_text)
        if self._encoding_detector is not None:
            try:
                # Run synchronously — detect_text is sync but we call it directly
                # since analyze() is synchronous and we're in thread pool or sync ctx
                encoding_findings = self._encoding_detector._detect_base64(truncated)
                encoding_findings.extend(self._encoding_detector._detect_hex(truncated))
                for ef in encoding_findings[:3]:
                    if len(result.hints) >= MAX_TEXT_ANALYZER_HINTS:
                        break
                    key = ef.encoding_type
                    if key not in seen_labels:
                        seen_labels.add(key)
                        result.hints.append(TextAnalyzerHint(
                            hint_type="encoding",
                            label=ef.encoding_type,
                            confidence=ef.confidence,
                            detail=f"len={ef.length} entropy={ef.entropy:.1f}",
                        ))
            except Exception as e:
                logger.debug(f"[TextAnalyzerFacade] Encoding detection failed: {e}")
                result.analyzer_errors += 1

        # Hash identification
        if self._hash_identifier is not None:
            try:
                # Extract potential hash strings (hex patterns 32-128 chars)
                import re
                hex_pattern = re.compile(r'\b[a-f0-9]{32,128}\b', re.IGNORECASE)
                for m in hex_pattern.finditer(truncated):
                    if len(result.hints) >= MAX_TEXT_ANALYZER_HINTS:
                        break
                    hash_str = m.group()
                    # Sync identify — call directly since we're in sync context
                    matches = self._hash_identifier._match_by_length(hash_str)
                    if matches:
                        algo = matches[0] if matches else "unknown"
                        result.hints.append(TextAnalyzerHint(
                            hint_type="hash",
                            label=algo.lower().replace(" ", "_"),
                            confidence=0.6,
                            detail=f"{algo} ({len(hash_str)} chars)",
                        ))
            except Exception as e:
                logger.debug(f"[TextAnalyzerFacade] Hash identification failed: {e}")
                result.analyzer_errors += 1

        return result


# Singleton instance
_facade: Optional[TextAnalyzerFacade] = None


def get_text_analyzer_facade() -> TextAnalyzerFacade:
    """Return the singleton TextAnalyzerFacade instance."""
    global _facade
    if _facade is None:
        _facade = TextAnalyzerFacade()
    return _facade


def analyze_text(text: str) -> TextAnalyzerResult:
    """
    Convenience function — analyze text with all available text analyzers.

    Fail-soft: returns empty TextAnalyzerResult if no analyzers are available.
    """
    try:
        facade = get_text_analyzer_facade()
        return facade.analyze(text)
    except Exception as e:
        logger.debug(f"[TextAnalyzerFacade] analyze_text failed: {e}")
        return TextAnalyzerResult()