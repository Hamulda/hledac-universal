"""
Language detection module for cross-lingual embedding routing.

Supports:


- FastText-based detection (fast, accurate for 40+ languages)
- langdetect fallback (pure Python, no external model)
- Script-based detection for CJK, Cyrillic, Arabic
- Configurable confidence thresholds

Author: Hledac Team
Issue: [SWARM]-002
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)

# Script-based detection patterns for non-Latin scripts
_SCRIPT_PATTERNS = {
    'cyrillic': re.compile(r'[\u0400-\u04FF]'),      # Russian, Ukrainian, etc.
    'arabic': re.compile(r'[\u0600-\u06FF\u0750-\u077F]'),  # Arabic, Persian
    'cjk': re.compile(r'[\u4E00-\u9FFF\u3400-\u4DBF]'),    # Chinese, Japanese kanji
    'hangul': re.compile(r'[\uAC00-\uD7AF]'),       # Korean
    'thai': re.compile(r'[\u0E00-\u0E7F]'),         # Thai
    'devanagari': re.compile(r'[\u0900-\u097F]'),   # Hindi, Sanskrit
    'greek': re.compile(r'[\u0370-\u03FF]'),        # Greek
    'hebrew': re.compile(r'[\u0590-\u05FF]'),       # Hebrew
    'armenian': re.compile(r'[\u0530-\u058F]'),     # Armenian
    'georgian': re.compile(r'[\u10A0-\u10FF]'),     # Georgian
    'lao': re.compile(r'[\u0E80-\u0EDF]'),          # Lao
    'tibetan': re.compile(r'[\u0F00-\u0FFF]'),       # Tibetan
    'myanmar': re.compile(r'[\u1000-\u109F]'),       # Myanmar
    'khmer': re.compile(r'[\u1780-\u17FF]'),         # Khmer
}


class ScriptType(Enum):
    """Unicode script classification for threat intelligence sources."""
    LATIN = auto()
    CYRILLIC = auto()
    ARABIC = auto()
    CJK = auto()          # Chinese, Japanese kanji
    HANGUL = auto()       # Korean
    THAI = auto()
    DEVANAGARI = auto()   # Hindi, Sanskrit
    GREEK = auto()
    HEBREW = auto()
    ARMENIAN = auto()
    GEORGIAN = auto()
    MIXED = auto()
    UNKNOWN = auto()


@dataclass(frozen=True, slots=True)
class LanguageDetectionResult:
    """Result of language detection with metadata for embedding routing."""
    language: str                    # ISO 639-1 code (en, ru, zh, ar, etc.)
    script: ScriptType               # Detected script type
    confidence: float                # Confidence score 0.0-1.0
    is_english: bool                 # Quick check for English routing
    is_latin_script: bool            # Latin/Roman script languages
    requires_multilingual: bool       # True if non-English (needs BGE-M3)
    
    @classmethod
    def from_script_detection(
        cls,
        text: str,
        script: ScriptType,
        confidence: float = 0.9
    ) -> 'LanguageDetectionResult':
        """Create result from script-based detection (fast path)."""
        lang_map = {
            ScriptType.CYRILLIC: 'ru',
            ScriptType.ARABIC: 'ar',
            ScriptType.CJK: 'zh',
            ScriptType.HANGUL: 'ko',
            ScriptType.THAI: 'th',
            ScriptType.DEVANAGARI: 'hi',
            ScriptType.GREEK: 'el',
            ScriptType.HEBREW: 'he',
            ScriptType.ARMENIAN: 'hy',
            ScriptType.GEORGIAN: 'ka',
        }
        return cls(
            language=lang_map.get(script, 'unknown'),
            script=script,
            confidence=confidence,
            is_english=False,
            is_latin_script=script == ScriptType.LATIN,
            requires_multilingual=True
        )


class LangDetector:
    """
    Hybrid language detector combining:
    1. Script-based detection (fast, deterministic for non-Latin scripts)
    2. FastText (if available, 40+ languages, high accuracy)
    3. langdetect fallback (pure Python, 50+ languages)
    
    For threat intelligence:
    - Prioritizes script detection (Russian/Cyrillic forums are obvious non-English)
    - FastText provides accurate language ID for Latin-script languages
    - Confidence threshold: 0.7 for production, 0.5 for low-resource languages
    """
    
    # Supported languages for FastText
    FASTTEXT_SUPPORTED = {
        'en', 'ru', 'zh', 'ar', 'es', 'fr', 'de', 'ja', 'ko', 'pt',
        'it', 'nl', 'pl', 'uk', 'vi', 'th', 'hi', 'tr', 'cs', 'sv',
        'da', 'fi', 'no', 'id', 'ms', 'ro', 'el', 'he', 'hu', 'bg',
        'hr', 'sk', 'sl', 'sr', 'et', 'lt', 'lv', 'fa', 'ur', 'bn'
    }
    
    # Latin-script languages (vs Cyrillic, Arabic, CJK, etc.)
    LATIN_SCRIPT_LANGUAGES = {
        'en', 'es', 'fr', 'de', 'pt', 'it', 'nl', 'pl', 'vi', 'cs',
        'sv', 'da', 'fi', 'no', 'id', 'ms', 'ro', 'hr', 'sk', 'sl',
        'et', 'lt', 'lv', 'hu', 'tr', 'tl'  # Tagalog
    }
    
    def __init__(
        self,
        use_fasttext: bool = True,
        use_langdetect: bool = True,
        confidence_threshold: float = 0.7,
        min_text_length: int = 10
    ):
        """
        Initialize language detector.
        
        Args:
            use_fasttext: Use FastText for Latin-script languages (recommended).
            use_langdetect: Fallback to langdetect if FastText unavailable.
            confidence_threshold: Minimum confidence to accept detection (0.0-1.0).
            min_text_length: Minimum text length for reliable detection.
        """
        self._fasttext_model = None
        self._fasttext_available = False
        self._langdetect_available = False
        self._use_fasttext = use_fasttext
        self._use_langdetect = use_langdetect
        self._confidence_threshold = confidence_threshold
        self._min_text_length = min_text_length
        
        self._init_backends()
    
    def _init_backends(self) -> None:
        """Initialize detection backends with lazy loading."""
        # Try FastText first
        if self._use_fasttext:
            self._init_fasttext()
        
        # Always init langdetect as fallback
        if self._use_langdetect:
            self._init_langdetect()
    
    def _init_fasttext(self) -> None:
        """Lazy-load FastText language identification model."""
        try:
            import fasttext
            # Use lid.176.bin - compact 9MB model, 176 languages
            # For production, consider fasttext-modeling/lid-176ftz or similar
            model_path = self._get_fasttext_model_path()
            if model_path:
                self._fasttext_model = fasttext.load_model(model_path)
                self._fasttext_available = True
                logger.info('[LangDetector] FastText loaded successfully')
        except ImportError:
            logger.debug('[LangDetector] FastText not available (install: pip install fasttext)')
        except Exception as e:
            logger.warning(f'[LangDetector] FastText load failed: {e}')
    
    def _get_fasttext_model_path(self) -> Optional[str]:
        """Get path to FastText model, downloading if necessary."""
        import os
        from pathlib import Path
        
        # Default location: ~/.cache/hledac/fasttext/
        cache_dir = Path.home() / '.cache' / 'hledac' / 'fasttext'
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        model_file = cache_dir / 'lid.176.bin'
        
        # Check if model exists
        if model_file.exists():
            return str(model_file)
        
        # For now, require manual download or model path in config
        # Future: auto-download from HuggingFace
        logger.warning(
            f'[LangDetector] FastText model not found at {model_file}. '
            'Download from: https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin'
        )
        return None
    
    def _init_langdetect(self) -> None:
        """Initialize langdetect fallback."""
        try:
            from langdetect import DetectorFactory
            DetectorFactory.seed = 42  # Deterministic results
            self._langdetect_available = True
            logger.info('[LangDetector] langdetect initialized')
        except ImportError:
            logger.debug('[LangDetector] langdetect not available (install: pip install langdetect)')
    
    def detect(self, text: str) -> LanguageDetectionResult:
        """
        Detect language of input text.
        
        Detection order:
        1. Script-based detection (fastest, for non-Latin scripts)
        2. FastText (if available, for Latin-script languages)
        3. langdetect fallback (pure Python)
        
        Args:
            text: Input text to analyze.
            
        Returns:
            LanguageDetectionResult with language, script, confidence.
        """
        if not text or len(text.strip()) < self._min_text_length:
            # Short text: default to English (most common in mixed content)
            return LanguageDetectionResult(
                language='en',
                script=ScriptType.LATIN,
                confidence=0.5,
                is_english=True,
                is_latin_script=True,
                requires_multilingual=False
            )
        
        # Step 1: Script-based detection (deterministic, fast)
        script_result = self._detect_script(text)
        if script_result is not None:
            return script_result
        
        # Step 2: FastText for Latin-script languages
        if self._fasttext_available:
            fasttext_result = self._detect_fasttext(text)
            if fasttext_result is not None:
                return fasttext_result
        
        # Step 3: langdetect fallback
        if self._langdetect_available:
            langdetect_result = self._detect_langdetect(text)
            if langdetect_result is not None:
                return langdetect_result
        
        # Fallback: assume English (conservative for threat intelligence)
        logger.debug(f'[LangDetector] All backends failed, defaulting to English')
        return LanguageDetectionResult(
            language='en',
            script=ScriptType.LATIN,
            confidence=0.3,
            is_english=True,
            is_latin_script=True,
            requires_multilingual=False
        )
    
    def _detect_script(self, text: str) -> Optional[LanguageDetectionResult]:
        """
        Fast script-based language detection.
        
        Uses Unicode ranges to classify scripts without ML models.
        High confidence for non-Latin scripts (Russian forums, Arabic darknet, etc.)
        """
        script_matches: dict[str, int] = {}
        
        for script_name, pattern in _SCRIPT_PATTERNS.items():
            matches = len(pattern.findall(text))
            if matches > 0:
                script_matches[script_name] = matches
        
        if not script_matches:
            return None  # Latin script or no matches
        
        # Find dominant script
        dominant_script = max(script_matches, key=script_matches.get)
        total_chars = sum(script_matches.values())
        script_ratio = script_matches[dominant_script] / max(total_chars, 1)
        
        # High confidence if dominant script is clear (>70% of script chars)
        if script_ratio > 0.7:
            script_type = ScriptType[dominant_script.upper()]
            
            # Map script to language code (for common mappings)
            is_english = False  # English is always Latin
            is_latin_script = dominant_script == 'latin'
            requires_multilingual = not is_english
            
            return LanguageDetectionResult(
                language=self._script_to_language(dominant_script),
                script=script_type,
                confidence=min(script_ratio, 0.99),
                is_english=is_english,
                is_latin_script=is_latin_script,
                requires_multilingual=requires_multilingual
            )
        
        # Mixed script content
        return LanguageDetectionResult(
            language='mixed',
            script=ScriptType.MIXED,
            confidence=0.6,
            is_english=False,
            is_latin_script=False,
            requires_multilingual=True
        )
    
    def _script_to_language(self, script: str) -> str:
        """Map dominant script to most likely language."""
        script_to_lang = {
            'cyrillic': 'ru',      # Russian is most common in darknet
            'arabic': 'ar',
            'cjk': 'zh',
            'hangul': 'ko',
            'thai': 'th',
            'devanagari': 'hi',
            'greek': 'el',
            'hebrew': 'he',
            'armenian': 'hy',
            'georgian': 'ka',
        }
        return script_to_lang.get(script, 'unknown')
    
    def _detect_fasttext(self, text: str) -> Optional[LanguageDetectionResult]:
        """FastText-based language detection for Latin-script languages."""
        if self._fasttext_model is None:
            return None
        
        try:
            # FastText returns [(label, confidence), ...]
            predictions = self._fasttext_model.predict(text.replace('\n', ' '), k=1)
            if not predictions or not predictions[0]:
                return None
            
            label = predictions[0][0]  # '__label__en'
            lang = label.replace('__label__', '')
            confidence = float(predictions[1][0])
            
            # Check confidence threshold
            if confidence < self._confidence_threshold:
                return None
            
            is_english = lang == 'en'
            is_latin_script = lang in self.LATIN_SCRIPT_LANGUAGES
            
            return LanguageDetectionResult(
                language=lang,
                script=ScriptType.LATIN if is_latin_script else ScriptType.UNKNOWN,
                confidence=confidence,
                is_english=is_english,
                is_latin_script=is_latin_script,
                requires_multilingual=not is_english
            )
        except Exception as e:
            logger.debug(f'[LangDetector] FastText error: {e}')
            return None
    
    def _detect_langdetect(self, text: str) -> Optional[LanguageDetectionResult]:
        """langdetect fallback for language detection."""
        if not self._langdetect_available:
            return None
        
        try:
            from langdetect import detect
            
            lang = detect(text)
            confidence = 0.7  # langdetect doesn't provide confidence
            
            is_english = lang == 'en'
            is_latin_script = lang in self.LATIN_SCRIPT_LANGUAGES
            
            return LanguageDetectionResult(
                language=lang,
                script=ScriptType.LATIN if is_latin_script else ScriptType.UNKNOWN,
                confidence=confidence,
                is_english=is_english,
                is_latin_script=is_latin_script,
                requires_multilingual=not is_english
            )
        except Exception as e:
            logger.debug(f'[LangDetector] langdetect error: {e}')
            return None
    
    @property
    def is_loaded(self) -> bool:
        """Check if any backend is available."""
        return self._fasttext_available or self._langdetect_available
    
    def detect_batch(self, texts: list[str]) -> list[LanguageDetectionResult]:
        """Detect languages for a batch of texts."""
        return [self.detect(text) for text in texts]


# F320: Refactored to use centralized singleton pattern
# Previously: duplicate global + if None pattern in 5 files
from hledac.universal.utils._patterns import module_singleton_getter
from _core._util import aclose


def _make_lang_detector() -> LangDetector:
    """Factory for LangDetector singleton."""
    return LangDetector(
        use_fasttext=True,
        use_langdetect=True,
        confidence_threshold=0.7
    )


# Module-level singleton getter with thread-safe double-checked locking
_get_lang_detector = module_singleton_getter(
    singleton_name="_lang_detector_instance",
    factory=_make_lang_detector,
)


def get_lang_detector(
    use_fasttext: bool = True,
    use_langdetect: bool = True,
    confidence_threshold: float = 0.7
) -> LangDetector:
    """
    Get singleton language detector instance.

    Args:
        use_fasttext: Enable FastText backend.
        use_langdetect: Enable langdetect backend.
        confidence_threshold: Detection confidence threshold.

    Returns:
        Shared LangDetector instance.
    """
    return _get_lang_detector()


def detect_language(text: str) -> LanguageDetectionResult:
    """
    Convenience function for single text language detection.
    
    Args:
        text: Input text to analyze.
        
    Returns:
        LanguageDetectionResult with detected language info.
    """
    detector = get_lang_detector()
    return detector.detect(text)
