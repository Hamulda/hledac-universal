import hashlib
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

try:
    from fast_langdetect import detect as fast_detect
    FAST_LANGDETECT_AVAILABLE = True
except ImportError:
    FAST_LANGDETECT_AVAILABLE = False
    from ._warnings import warn_once_log

    warn_once_log("fast-langdetect-missing", "fast-langdetect not available, using fallback detection")


class LanguageDetector:
    """Fast Language Detection optimized for M1 Apple Silicon.
    
    Uses fast-langdetect (FTZ format) for ultra-fast language detection.
    Falls back to simple heuristic if fast-langdetect is not installed.
    """

    LANGUAGE_NAMES = {
        'en': 'English',
        'cs': 'Czech',
        'sk': 'Slovak',
        'de': 'German',
        'fr': 'French',
        'es': 'Spanish',
        'it': 'Italian',
        'pl': 'Polish',
        'ru': 'Russian',
        'zh': 'Chinese',
        'ja': 'Japanese',
        'ko': 'Korean',
        'ar': 'Arabic',
        'pt': 'Portuguese',
        'nl': 'Dutch',
        'sv': 'Swedish',
        'da': 'Danish',
        'no': 'Norwegian',
        'fi': 'Finnish',
        'hu': 'Hungarian',
        'tr': 'Turkish',
        'uk': 'Ukrainian',
        'bg': 'Bulgarian',
        'ro': 'Romanian',
        'el': 'Greek',
        'he': 'Hebrew',
        'th': 'Thai',
        'vi': 'Vietnamese',
        'id': 'Indonesian',
        'ms': 'Malay',
        'hi': 'Hindi',
        'bn': 'Bengali',
        'fa': 'Persian',
        'ur': 'Urdu',
        'ar': 'Arabic',
        'sw': 'Swahili',
    }

    def __init__(self, fallback_mode: bool = True):
        """Initialize language detector.
        
        Args:
            fallback_mode: If True, use simple fallback when fast-langdetect is not available
        """
        self.fallback_mode = fallback_mode
        self._char_ranges = self._build_char_ranges()

    def _build_char_ranges(self) -> Dict[str, tuple]:
        """Build character range mappings for fallback detection."""
        return {
            'zh': (0x4E00, 0x9FFF),
            'ja': (0x3040, 0x309F),
            'ko': (0xAC00, 0xD7AF),
            'ru': (0x0400, 0x04FF),
            'ar': (0x0600, 0x06FF),
            'el': (0x0370, 0x03FF),
            'th': (0x0E00, 0x0E7F),
            'he': (0x0590, 0x05FF),
        }

    def detect(self, text: str, min_length: int = 10) -> str:
        """Detect language of text.
        
        Args:
            text: Input text to analyze
            min_length: Minimum text length for detection (shorter texts return 'unknown')
            
        Returns:
            Language code (e.g., 'en', 'cs', 'zh')
            
        Example:
            >>> detector = LanguageDetector()
            >>> detector.detect("Ahoj světe")
            'cs'
            >>> detector.detect("Hello world")
            'en'
        """
        if not text or len(text.strip()) < min_length:
            return 'unknown'

        text = text.strip()

        if FAST_LANGDETECT_AVAILABLE:
            try:
                result = fast_detect(text)
                return result
            except Exception as e:
                logger.warning(f"fast-langdetect failed: {e}, using fallback")

        if self.fallback_mode:
            return self._fallback_detect(text)

        return 'unknown'

    def _fallback_detect(self, text: str) -> str:
        """Fallback detection using simple character analysis."""
        sample = text[:200]

        czech_chars = set('ěščřžýáíéďťňóůúĚŠČŘŽÝÁÍÉĎŤŇÓŮÚ')
        if czech_chars.intersection(sample):
            return 'cs'

        for lang, (start, end) in self._char_ranges.items():
            for char in sample:
                if start <= ord(char) <= end:
                    return lang

        common_czech_words = {'a', 'se', 'na', 'je', 'to', 'že', 's', 'v', 'o', 'z', 'do', 'ne', 'si', 'jako', 'ale', 'tak', 'jsem'}
        common_english_words = {'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have', 'i', 'it', 'for', 'not', 'on', 'with'}

        words = sample.lower().split()
        if not words:
            return 'unknown'

        czech_count = sum(1 for word in words if word in common_czech_words)
        english_count = sum(1 for word in words if word in common_english_words)

        if czech_count > english_count and czech_count > 0:
            return 'cs'
        if english_count > czech_count and english_count > 0:
            return 'en'

        return 'en'

    def is_supported(self, lang_code: str) -> bool:
        """Check if language code is supported.
        
        Args:
            lang_code: Language code to check
            
        Returns:
            True if language is supported
        """
        return lang_code in self.LANGUAGE_NAMES

    def get_language_name(self, lang_code: str) -> str:
        """Get human-readable language name.
        
        Args:
            lang_code: Language code (e.g., 'en', 'cs')
            
        Returns:
            Language name (e.g., 'English', 'Czech')
        """
        return self.LANGUAGE_NAMES.get(lang_code, lang_code)

    def batch_detect(self, texts: list, min_length: int = 10) -> list:
        """Detect languages for multiple texts.
        
        Args:
            texts: List of texts to analyze
            min_length: Minimum text length for detection
            
        Returns:
            List of language codes
        """
        return [self.detect(text, min_length) for text in texts]

    def filter_by_language(self, texts: list, allowed_langs: list) -> list:
        """Filter texts by allowed languages.
        
        Args:
            texts: List of (text, metadata) tuples or just texts
            allowed_langs: List of allowed language codes (e.g., ['en', 'cs'])
            
        Returns:
            Filtered list of texts
        """
        filtered = []
        for item in texts:
            if isinstance(item, tuple):
                text, metadata = item
            else:
                text, metadata = item, {}

            lang = self.detect(text)
            if lang in allowed_langs:
                if isinstance(item, tuple):
                    filtered.append((text, metadata))
                else:
                    filtered.append(text)

        return filtered


def create_language_detector(fallback_mode: bool = True) -> LanguageDetector:
    """Factory function to create language detector.

    Args:
        fallback_mode: If True, use simple fallback when fast-langdetect is not available

    Returns:
        LanguageDetector instance
    """
    return LanguageDetector(fallback_mode=fallback_mode)


class FastLangDetector:
    """Fast language detection adapter with bounded output for evidence metadata.

    Returns a bounded dict with lang code, confidence bucket, and hash.
    Used for metadata deduplication and cross-language comparison control.
    """

    MAX_CHARS = 4000  # Hard cap on input text length
    MIN_LENGTH = 10   # Minimum text length for detection

    def __init__(self):
        """Initialize the fast language detector."""
        self._detector = LanguageDetector(fallback_mode=True)

    def detect(self, text: str, *, max_chars: int = 4000) -> Dict[str, any]:
        """
        Detect language with bounded output.

        Args:
            text: Input text to analyze
            max_chars: Maximum characters to process (default 4000)

        Returns:
            Bounded dict:
                { "lang": "en", "confidence": 0.9, "conf_bucket": "high|med|low",
                  "lang_hash": "a1b2c3d4" }
        """
        # Truncate input
        if not text:
            return self._default_result()

        truncated = text[:max_chars].strip()
        if len(truncated) < self.MIN_LENGTH:
            return self._default_result()

        # Detect language
        lang_code = self._detector.detect(truncated)

        # Determine confidence bucket (using simple heuristic since fast-langdetect
        # returns just the language code, not probability)
        confidence = 0.5  # Default confidence
        if FAST_LANGDETECT_AVAILABLE:
            # fast-langdetect might provide confidence in different format
            # For now use length-based confidence heuristic
            if len(truncated) >= 200:
                confidence = 0.8
            elif len(truncated) >= 100:
                confidence = 0.7
            else:
                confidence = 0.5
        else:
            # Fallback is less reliable
            confidence = 0.4

        # Determine bucket
        if confidence >= 0.7:
            conf_bucket = "high"
        elif confidence >= 0.4:
            conf_bucket = "med"
        else:
            conf_bucket = "low"

        # Compute hash for deduplication (only lang + bucket, not full text)
        lang_hash_input = f"{lang_code}:{conf_bucket}"
        lang_hash = hashlib.sha256(lang_hash_input.encode()).hexdigest()[:8]

        return {
            "lang": lang_code if lang_code != 'unknown' else 'und',
            "confidence": confidence,
            "conf_bucket": conf_bucket,
            "lang_hash": lang_hash,
        }

    def _default_result(self) -> Dict[str, any]:
        """Return default result for insufficient text or error."""
        return {
            "lang": "und",
            "confidence": 0.0,
            "conf_bucket": "low",
            "lang_hash": "00000000",
        }

    def is_cross_language_comparable(self, result1: Dict[str, any], result2: Dict[str, any]) -> bool:
        """
        Determine if two language detection results are comparable.

        Returns True if:
        - Both are "und" (undetermined)
        - Both have low confidence
        - Both have same language code
        """
        # Both undetermined - compare
        if result1["lang"] == "und" and result2["lang"] == "und":
            return True

        # Both low confidence - compare
        if result1["conf_bucket"] == "low" and result2["conf_bucket"] == "low":
            return True

        # Same language - compare
        if result1["lang"] == result2["lang"]:
            return True

        return False
