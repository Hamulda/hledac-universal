"""
Unicode Attack Surface Analyzer

High-speed Unicode attack analyzer detecting:
- Zero-width characters (invisible text attacks)
- Homoglyph substitution (confusable character attacks)
- Bidirectional text attacks (RLO/LRO/PDF spoofing)
- Normalization anomalies (NFD vs NFC attacks)

Target: 100+ MB/s text processing speed
"""

import asyncio
import hashlib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any, FrozenSet
import logging
import sys

logger = logging.getLogger(__name__)


@dataclass
class UnicodeConfig:
    """Configuration for Unicode attack analysis."""
    detect_zero_width: bool = True
    detect_homoglyphs: bool = True
    detect_bidi_attacks: bool = True
    detect_normalization: bool = True
    chunk_size: int = 1048576  # 1MB chunks
    max_file_size: int = 1073741824  # 1GB
    include_context: bool = True
    context_window: int = 20


@dataclass
class ZeroWidthFinding:
    """Finding for zero-width character detection."""
    position: int
    char_code: str
    char_name: str
    context: Optional[str] = None


@dataclass
class HomoglyphFinding:
    """Finding for homoglyph/confusable character detection."""
    position: int
    char: str
    canonical_form: str
    confusable_with: List[str]
    char_code: str = ""


@dataclass
class BidiFinding:
    """Finding for bidirectional text attack detection."""
    position: int
    char_code: str
    attack_type: str
    description: str
    context: Optional[str] = None


@dataclass
class NormalizationFinding:
    """Finding for Unicode normalization anomaly detection."""
    position: int
    original: str
    normalized: str
    anomaly_type: str
    char_code: str = ""


@dataclass
class UnicodeAnalysisResult:
    """Complete result of Unicode attack analysis."""
    zero_width_findings: List[ZeroWidthFinding] = field(default_factory=list)
    homoglyph_findings: List[HomoglyphFinding] = field(default_factory=list)
    bidi_findings: List[BidiFinding] = field(default_factory=list)
    normalization_findings: List[NormalizationFinding] = field(default_factory=list)
    risk_score: float = 0.0
    total_chars: int = 0
    processed_bytes: int = 0
    processing_time_ms: float = 0.0

    def has_findings(self) -> bool:
        """Check if any findings were detected."""
        return bool(
            self.zero_width_findings
            or self.homoglyph_findings
            or self.bidi_findings
            or self.normalization_findings
        )

    def get_finding_count(self) -> int:
        """Get total number of findings."""
        return (
            len(self.zero_width_findings)
            + len(self.homoglyph_findings)
            + len(self.bidi_findings)
            + len(self.normalization_findings)
        )

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of analysis results."""
        return {
            "risk_score": self.risk_score,
            "total_findings": self.get_finding_count(),
            "zero_width_count": len(self.zero_width_findings),
            "homoglyph_count": len(self.homoglyph_findings),
            "bidi_count": len(self.bidi_findings),
            "normalization_count": len(self.normalization_findings),
            "total_chars": self.total_chars,
            "processed_bytes": self.processed_bytes,
            "processing_time_ms": self.processing_time_ms,
        }


class UnicodeAttackAnalyzer:
    """
    High-speed Unicode attack surface analyzer.

    Detects various Unicode-based attacks including zero-width characters,
    homoglyph substitution, bidirectional text attacks, and normalization anomalies.
    Optimized for 100+ MB/s processing speed.
    """

    # Pre-computed frozensets for O(1) lookup
    ZERO_WIDTH_CHARS: FrozenSet[int] = frozenset({
        0x200B,  # ZERO WIDTH SPACE
        0x200C,  # ZERO WIDTH NON-JOINER
        0x200D,  # ZERO WIDTH JOINER
        0x200E,  # LEFT-TO-RIGHT MARK
        0x200F,  # RIGHT-TO-LEFT MARK
        0xFEFF,  # ZERO WIDTH NO-BREAK SPACE (BOM)
    })

    # Bidi character info as tuples for fast lookup
    BIDI_CHARS: Dict[int, Tuple[str, str]] = {
        0x202A: ("LRE", "Left-to-Right Embedding"),
        0x202B: ("RLE", "Right-to-Left Embedding"),
        0x202C: ("PDF", "Pop Directional Formatting"),
        0x202D: ("LRO", "Left-to-Right Override"),
        0x202E: ("RLO", "Right-to-Left Override"),
        0x2066: ("LRI", "Left-to-Right Isolate"),
        0x2067: ("RLI", "Right-to-Left Isolate"),
        0x2068: ("FSI", "First Strong Isolate"),
        0x2069: ("PDI", "Pop Directional Isolate"),
    }

    HIGH_RISK_BIDI: FrozenSet[int] = frozenset({0x202E, 0x202D, 0x202C})
    BIDI_OPENING: FrozenSet[int] = frozenset({0x202A, 0x202B, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068})
    BIDI_CLOSING: FrozenSet[int] = frozenset({0x202C, 0x2069})

    def __init__(self, config: Optional[UnicodeConfig] = None):
        """Initialize the Unicode attack analyzer."""
        self.config = config or UnicodeConfig()
        self._confusable_set: FrozenSet[str] = frozenset()
        self._canonical_map: Dict[str, str] = {}
        self._confusable_map: Dict[str, List[str]] = {}
        self._initialized: bool = False
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Initialize the analyzer by loading confusable mappings."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            try:
                self._load_confusable_mappings()
                self._initialized = True
                logger.info("UnicodeAttackAnalyzer initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize UnicodeAttackAnalyzer: {e}")
                raise

    def _load_confusable_mappings(self) -> None:
        """Load confusable character mappings - optimized version."""
        # Build comprehensive confusable character map
        # Latin confusables mapping: canonical -> list of confusables
        raw_map: Dict[str, List[str]] = {
            'a': ['а', 'à', 'á', 'â', 'ã', 'ä', 'å', 'ā', 'ă', 'ą', 'ạ', 'ả', 'ấ', 'ầ', 'ẩ', 'ẫ', 'ậ'],
            'b': ['ь', 'Ь', 'в', 'В', 'Ƅ', 'ɓ', 'ḃ', 'ḅ', 'ḇ'],
            'c': ['с', 'С', '¢', '©', 'ç', 'ć', 'ĉ', 'ċ', 'č', 'ƈ', 'ḉ'],
            'd': ['ԁ', 'ḋ', 'ḍ', 'ḏ', 'ḑ', 'ḓ', 'ď', 'đ', 'ɗ', 'ɖ'],
            'e': ['е', 'Е', 'è', 'é', 'ê', 'ë', 'ē', 'ĕ', 'ė', 'ę', 'ě', 'ẹ', 'ẻ', 'ẽ', 'ế', 'ề', 'ể', 'ễ', 'ệ', 'ə'],
            'f': ['ғ', 'ḟ', 'ƒ', 'ſ', 'ϝ'],
            'g': ['ġ', 'ģ', 'ǵ', 'ɡ', 'ɢ', 'ḡ', 'ց'],
            'h': ['һ', 'Н', 'ĥ', 'ħ', 'ɦ', 'ḣ', 'ḥ', 'ḧ', 'ḩ', 'ḫ', 'ẖ'],
            'i': ['і', 'Í', 'ì', 'í', 'î', 'ï', 'ĩ', 'ī', 'ĭ', 'į', 'ǐ', 'ị', 'ỉ', 'ɨ', 'ɩ', 'ı'],
            'j': ['ј', 'ĵ', 'ǰ', 'ɉ', 'Ϳ'],
            'k': ['ķ', 'ĸ', 'ǩ', 'ḱ', 'ḳ', 'ḵ', 'к', 'Κ', 'κ'],
            'l': ['ӏ', 'ĺ', 'ļ', 'ľ', 'ŀ', 'ł', 'ḷ', 'ḹ', 'ḻ', 'ḽ', 'ℓ', 'ⅼ'],
            'm': ['м', 'ḿ', 'ṁ', 'ṃ', 'ṃ', 'ⅿ'],
            'n': ['ո', 'ñ', 'ń', 'ņ', 'ň', 'ŉ', 'ṅ', 'ṇ', 'ṉ', 'ṋ', 'ɲ', 'ƞ'],
            'o': ['о', 'О', 'ò', 'ó', 'ô', 'õ', 'ö', 'ø', 'ō', 'ŏ', 'ő', 'ơ', 'ọ', 'ỏ', 'ố', 'ồ', 'ổ', 'ỗ', 'ộ', 'ớ', 'ờ', 'ở', 'ỡ', 'ợ', 'ο', 'σ', 'օ'],
            'p': ['р', 'ṕ', 'ṗ', 'ρ', 'ϱ', 'Þ', 'þ'],
            'q': ['ԛ', 'ɋ', 'ʠ'],
            'r': ['г', 'ŕ', 'ŗ', 'ř', 'ṙ', 'ṛ', 'ṝ', 'ṟ', 'ɍ', 'ɼ', 'г', 'Γ'],
            's': ['ѕ', 'ś', 'ŝ', 'ş', 'š', 'ș', 'ṡ', 'ṣ', 'ṥ', 'ṧ', 'ṩ', 'ʂ', 'ƨ', 'ş', '$', '§'],
            't': ['т', 'ţ', 'ť', 'ŧ', 'ṫ', 'ṭ', 'ṯ', 'ṱ', 'ẗ', 'Ț', 'ț', 'τ'],
            'u': ['υ', 'ù', 'ú', 'û', 'ü', 'ũ', 'ū', 'ŭ', 'ů', 'ű', 'ų', 'ư', 'ụ', 'ủ', 'ứ', 'ừ', 'ử', 'ữ', 'ự', 'μ'],
            'v': ['ν', 'ṽ', 'ṿ', 'ν', 'ѵ', 'ⅴ'],
            'w': ['ω', 'ŵ', 'ẁ', 'ẃ', 'ẅ', 'ẇ', 'ẉ', 'ẘ', 'ω', 'ώ', 'ѡ', 'ա'],
            'x': ['х', 'ẋ', 'ẍ', '×', 'χ', 'ҳ', 'ⅹ'],
            'y': ['у', 'ý', 'ÿ', 'ŷ', 'ẏ', 'ẙ', 'ỳ', 'ỵ', 'ỷ', 'ỹ', 'ƴ', 'ɏ', 'γ', 'у'],
            'z': ['з', 'ź', 'ż', 'ž', 'ẑ', 'ẓ', 'ẕ', 'ʐ', 'ƶ', 'ζ'],
            'A': ['А', 'À', 'Á', 'Â', 'Ã', 'Ä', 'Å', 'Ā', 'Ă', 'Ą', 'Ǎ', 'Ǟ', 'Ǡ', 'Ȁ', 'Ȃ', 'Ȧ', 'Ⱥ', 'Α', 'ᾼ'],
            'B': ['В', 'Ḃ', 'Ḅ', 'Ḇ', 'Β', 'ß'],
            'C': ['С', 'Ç', 'Ć', 'Ĉ', 'Ċ', 'Č', 'Ƈ', 'Ȼ', 'С', 'Ϲ'],
            'D': ['Ď', 'Ḋ', 'Ḍ', 'Ḏ', 'Ḑ', 'Ḓ', 'Đ', 'Ɗ', 'Ǆ', 'ǅ', 'ǲ'],
            'E': ['Е', 'È', 'É', 'Ê', 'Ë', 'Ē', 'Ĕ', 'Ė', 'Ę', 'Ě', 'Ȅ', 'Ȇ', 'Ȩ', 'Ε', 'Ё'],
            'F': ['Ḟ', 'Ƒ', 'Ϝ'],
            'G': ['Ĝ', 'Ğ', 'Ġ', 'Ģ', 'Ǧ', 'Ǵ', 'Ġ', 'Ԍ'],
            'H': ['Н', 'Ĥ', 'Ħ', 'Ȟ', 'Ḣ', 'Ḥ', 'Ḧ', 'Ḩ', 'Ḫ', 'ῌ', 'Η'],
            'I': ['І', 'Ì', 'Í', 'Î', 'Ï', 'Ĩ', 'Ī', 'Ĭ', 'Į', 'İ', 'Ǐ', 'Ȉ', 'Ȋ', 'Ι', 'Ί', 'Ὶ'],
            'J': ['Ј', 'Ĵ', 'ǰ', 'Ϳ'],
            'K': ['Ķ', 'ĸ', 'Ǩ', 'Ḱ', 'Ḳ', 'Ḵ', 'Κ', 'К', 'Ḱ'],
            'L': ['Ĺ', 'Ļ', 'Ľ', 'Ŀ', 'Ł', 'Ḷ', 'Ḹ', 'Ḻ', 'Ḽ', 'Ƚ', 'Λ', 'Ⅼ'],
            'M': ['М', 'Ḿ', 'Ṁ', 'Ṃ', 'Μ', 'Ⅿ'],
            'N': ['Ń', 'Ņ', 'Ň', 'Ŋ', 'Ǹ', 'Ṅ', 'Ṇ', 'Ṉ', 'Ṋ', 'Ñ', 'Ν'],
            'O': ['О', 'Ò', 'Ó', 'Ô', 'Õ', 'Ö', 'Ø', 'Ō', 'Ŏ', 'Ő', 'Ơ', 'Ǒ', 'Ǫ', 'Ǭ', 'Ȍ', 'Ȏ', 'Ȫ', 'Ȭ', 'Ȯ', 'Ȱ', 'Ṍ', 'Ṏ', 'Ṑ', 'Ṓ', 'Ọ', 'Ỏ', 'Ố', 'Ồ', 'Ổ', 'Ỗ', 'Ộ', 'Ớ', 'Ờ', 'Ở', 'Ỡ', 'Ợ', 'Θ', 'Ο', 'Ό', 'Ὸ'],
            'P': ['Р', 'Ṕ', 'Ṗ', 'Ρ', 'Ῥ', 'Þ'],
            'Q': ['Ԛ'],
            'R': ['Ŕ', 'Ŗ', 'Ř', 'Ȑ', 'Ȓ', 'Ṙ', 'Ṛ', 'Ṝ', 'Ṟ', 'Ṟ', 'Я', 'Г', 'Ρ'],
            'S': ['Ѕ', 'Ś', 'Ŝ', 'Ş', 'Š', 'Ș', 'Ṡ', 'Ṣ', 'Ṥ', 'Ṧ', 'Ṩ', 'Ş', '§', '$'],
            'T': ['Т', 'Ţ', 'Ť', 'Ŧ', 'Ț', 'Ṫ', 'Ṭ', 'Ṯ', 'Ṱ', 'Τ', 'Т'],
            'U': ['υ', 'Ù', 'Ú', 'Û', 'Ü', 'Ũ', 'Ū', 'Ŭ', 'Ů', 'Ű', 'Ų', 'Ư', 'Ǔ', 'Ǖ', 'Ǘ', 'Ǚ', 'Ǜ', 'Ȕ', 'Ȗ', 'Ṳ', 'Ṵ', 'Ṷ', 'Ṹ', 'Ṻ', 'Ụ', 'Ủ', 'Ứ', 'Ừ', 'Ử', 'Ữ', 'Ự', 'Ʊ', 'Ս'],
            'V': ['Ṽ', 'Ṿ', 'ν', 'Ѵ', 'Ѷ', 'Ⅴ'],
            'W': ['Ŵ', 'Ẁ', 'Ẃ', 'Ẅ', 'Ẇ', 'Ẉ', 'Ш', 'Щ', 'Ѡ', 'Ꮤ'],
            'X': ['Х', 'Ẋ', 'Ẍ', 'Χ', 'Χ', 'Ⅹ'],
            'Y': ['Ү', 'Ý', 'Ŷ', 'Ÿ', 'Ȳ', 'Ẏ', 'Ỳ', 'Ỵ', 'Ỷ', 'Ỹ', 'Υ', 'Ύ', 'Ῠ', 'Ῡ', 'Ὺ', 'Ϋ'],
            'Z': ['Ζ', 'Ź', 'Ż', 'Ž', 'Ẑ', 'Ẓ', 'Ẕ', 'Ȥ', 'Ζ'],
            '0': ['O', 'Ο', 'О', 'Օ', 'ⵔ', '〇', '𝟎', '𝟘', '𝟢', '𝟬', '𝟶'],
            '1': ['l', 'I', 'і', '|', 'ℓ', 'ⅼ', '𝟙', '𝟣', '𝟭', '𝟷'],
            '2': ['ƻ', 'ᒿ', '𝟐', '𝟚', '𝟤', '𝟮', '𝟸'],
            '3': ['Ʒ', 'З', 'Ӡ', '𝟑', '𝟛', '𝟥', '𝟯', '𝟹'],
            '4': ['Ꮞ', '𝟒', '𝟜', '𝟦', '𝟰', '𝟺'],
            '5': ['Ƽ', '𝟓', '𝟝', '𝟧', '𝟱', '𝟻'],
            '6': ['б', 'Ꮾ', '𝟔', '𝟞', '𝟨', '𝟲', '𝟼'],
            '7': ['𝟕', '𝟟', '𝟩', '𝟳', '𝟽'],
            '8': ['ȣ', 'Ȣ', '৪', '੪', '𝟖', '𝟠', '𝟪', '𝟴', '𝟾'],
            '9': ['৭', '੧', '୨', '𝟗', '𝟡', '𝟫', '𝟵', '𝟿'],
            '-': ['−', '–', '—', '‐', '‑', '‒', '―', '─', '━', '┄', '┅', '┈', '┉'],
            '.': ['․', '܁', '‥', '…', '∙', '⋅', '·', '٠', '۰', '।', '।'],
            ',': ['‚', '،', '⸲', '⸲', '٫'],
            ';': [';', '؛'],
            ':': ['։', '፡', '᛬', '∶', 'ː', '˸'],
            '!': ['ǃ', '¡', '！'],
            '?': ['¿', '？'],
            '"': ['"', '"', '"', '"', '"', '"', '"', '"', '″', '〃', 'ˮ', '״'],
            "'": ['`', '´', '‘', '’', '‚', '‛', '′', 'ʹ', 'ˈ', 'ʼ', '՚', '׳'],
            '/': ['∕', '⁄', '／', '⧸', '╱', '⟋', 'Ⳇ'],
            '\\': ['∖', '＼', '⧵', '╲', '⟍', '⧹'],
            '(': ['❨', '❪', '（', '⁽', '₍', '⸨', '❲', '〔'],
            ')': ['❩', '❫', '）', '⁾', '₎', '⸩', '❳', '〕'],
            '[': ['［', '❲', '⁽', '₍'],
            ']': ['］', '❳', '⁾', '₎'],
            '{': ['｛', '❴', '𝄔'],
            '}': ['｝', '❵', '𝄕'],
            '<': ['‹', '«', '⟨', '〈', '＜', '≺', '⋖', '⋜'],
            '>': ['›', '»', '⟩', '〉', '＞', '≻', '⋗', '⋝'],
            '=': ['＝', '═', '≡', '≣', '≗', '≘', '≙', '≚', '≛', '≜', '≝', '≞', '≟'],
            '+': ['＋', '₊', '⁺', '✚', '✛', '✜', '✝', '†', '✞', '✟', '➕'],
            '*': ['∗', '＊', '⋆', '★', '☆', '✡', '✦', '✧', '✩', '✪', '✫', '✬', '✭', '✮', '✯', '✰'],
            '#': ['＃', '№', '⋕'],
            '@': ['＠', 'ⓐ'],
            '$': ['＄', '€', '£', '¥', '₹', '₽', '₩', '₪', '₫', '₴', '₦', '₲', '₱', '₡', '₣', '₤', '₥', '₧', '₨'],
            '%': ['％', '٪', '⁒', '℅', '‰', '‱'],
            '&': ['＆', '⅋', 'ꝸ', '꜕'],
            '^': ['＾', 'ˆ', '̂', '̂', '˄', 'ˆ', '̂'],
            '_': ['＿', '̲', '̲', '̲'],
            '|': ['｜', '∣', 'ǀ', 'ǀ', '│', '┃', '┆', '┇', '┊', '┋', '╎', '╏', '║'],
            '~': ['～', '˜', '̃', '̰', '̴', '∼', '≈', '≋', '≃', '⋍'],
        }

        # Build optimized lookup structures
        self._confusable_map = raw_map

        # Build reverse mapping: confusable -> canonical
        canonical_map: Dict[str, str] = {}
        confusable_chars: Set[str] = set()

        for canonical, confusables in raw_map.items():
            for confusable in confusables:
                confusable_chars.add(confusable)
                if confusable not in canonical_map:
                    canonical_map[confusable] = canonical

        self._canonical_map = canonical_map
        self._confusable_set = frozenset(confusable_chars)

        logger.debug(f"Loaded {len(raw_map)} confusable mappings, {len(confusable_chars)} confusable chars")

    def _get_context(self, text: str, position: int, window: int = 20) -> str:
        """Extract context around a position in text."""
        start = max(0, position - window)
        end = min(len(text), position + window + 1)
        context = text[start:end]
        # Replace newlines and control chars for readability
        context = ''.join(c if c.isprintable() or c.isspace() else f'\\u{ord(c):04X}' for c in context)
        return context.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')

    def _detect_zero_width(self, text: str, offset: int = 0) -> List[ZeroWidthFinding]:
        """Detect zero-width characters in text - optimized version."""
        findings = []
        zw_chars = self.ZERO_WIDTH_CHARS
        include_context = self.config.include_context
        get_context = self._get_context

        for i, char in enumerate(text):
            code_point = ord(char)
            if code_point in zw_chars:
                context = get_context(text, i) if include_context else None
                findings.append(ZeroWidthFinding(
                    position=offset + i,
                    char_code=f"U+{code_point:04X}",
                    char_name=unicodedata.name(char, "UNKNOWN"),
                    context=context
                ))

        return findings

    def _detect_homoglyphs(self, text: str, offset: int = 0) -> List[HomoglyphFinding]:
        """Detect homoglyph/confusable characters in text - optimized version."""
        findings = []
        confusable_set = self._confusable_set
        canonical_map = self._canonical_map
        confusable_map = self._confusable_map

        for i, char in enumerate(text):
            if char in confusable_set:
                canonical = canonical_map.get(char)
                if canonical:
                    confusables = confusable_map.get(canonical, [])
                    findings.append(HomoglyphFinding(
                        position=offset + i,
                        char=char,
                        canonical_form=canonical,
                        confusable_with=confusables,
                        char_code=f"U+{ord(char):04X}"
                    ))

        return findings

    def _detect_bidi_attacks(self, text: str, offset: int = 0) -> List[BidiFinding]:
        """Detect bidirectional text attacks in text - optimized version."""
        findings = []
        bidi_chars = self.BIDI_CHARS
        high_risk = self.HIGH_RISK_BIDI
        opening = self.BIDI_OPENING
        closing = self.BIDI_CLOSING
        include_context = self.config.include_context
        get_context = self._get_context
        bidi_stack = []

        for i, char in enumerate(text):
            code_point = ord(char)

            if code_point in bidi_chars:
                char_code, description = bidi_chars[code_point]

                # Track bidi control characters
                if code_point in opening:
                    bidi_stack.append((code_point, i))
                elif code_point in closing:
                    if bidi_stack:
                        bidi_stack.pop()

                # Determine attack type
                if code_point in high_risk:
                    if code_point == 0x202E:
                        attack_type = "RLO_ATTACK"
                    elif code_point == 0x202D:
                        attack_type = "LRO_ATTACK"
                    else:
                        attack_type = "PDF_TERMINATOR"
                else:
                    attack_type = "BIDI_CONTROL"

                context = get_context(text, i) if include_context else None
                findings.append(BidiFinding(
                    position=offset + i,
                    char_code=f"U+{code_point:04X} ({char_code})",
                    attack_type=attack_type,
                    description=description,
                    context=context
                ))

        # Check for unclosed bidi sequences
        for code_point, pos in bidi_stack:
            char_code, description = bidi_chars[code_point]
            findings.append(BidiFinding(
                position=offset + pos,
                char_code=f"U+{code_point:04X} ({char_code})",
                attack_type="UNCLOSED_BIDI_SEQUENCE",
                description=f"Unclosed: {description}",
                context=None
            ))

        return findings

    def _detect_normalization_anomalies(self, text: str, offset: int = 0) -> List[NormalizationFinding]:
        """Detect Unicode normalization anomalies in text - optimized version."""
        findings = []

        for i, char in enumerate(text):
            # Quick check: does character have decomposition?
            decomp = unicodedata.decomposition(char)
            if not decomp:
                continue

            # Check for characters that change under normalization
            nfc_form = unicodedata.normalize('NFC', char)
            nfd_form = unicodedata.normalize('NFD', char)

            anomaly_type = None
            normalized = char

            if char != nfc_form:
                anomaly_type = "NOT_NFC"
                normalized = nfc_form
            elif char != nfd_form:
                # Check for mixed normalization
                if i > 0 and unicodedata.combining(char) > 0:
                    prev = text[i - 1]
                    if unicodedata.combining(prev) == 0:
                        anomaly_type = "MIXED_NORMALIZATION"
                        normalized = nfd_form

            if anomaly_type:
                findings.append(NormalizationFinding(
                    position=offset + i,
                    original=char,
                    normalized=normalized,
                    anomaly_type=anomaly_type,
                    char_code=f"U+{ord(char):04X}"
                ))

        return findings

    def analyze_text(self, text: str) -> UnicodeAnalysisResult:
        """
        Analyze text for Unicode attacks.

        Args:
            text: The text to analyze

        Returns:
            UnicodeAnalysisResult with all findings
        """
        import time

        if not self._initialized:
            raise RuntimeError("Analyzer not initialized. Call initialize() first.")

        start_time = time.perf_counter()

        result = UnicodeAnalysisResult()
        result.total_chars = len(text)
        result.processed_bytes = len(text.encode('utf-8'))

        # Run all detection methods
        if self.config.detect_zero_width:
            result.zero_width_findings = self._detect_zero_width(text)

        if self.config.detect_homoglyphs:
            result.homoglyph_findings = self._detect_homoglyphs(text)

        if self.config.detect_bidi_attacks:
            result.bidi_findings = self._detect_bidi_attacks(text)

        if self.config.detect_normalization:
            result.normalization_findings = self._detect_normalization_anomalies(text)

        # Calculate risk score
        result.risk_score = self._calculate_risk_score(result)

        end_time = time.perf_counter()
        result.processing_time_ms = (end_time - start_time) * 1000

        return result

    async def analyze_file(self, file_path: Path) -> UnicodeAnalysisResult:
        """
        Stream-analyze a file for Unicode attacks.

        Args:
            file_path: Path to the file to analyze

        Returns:
            UnicodeAnalysisResult with all findings
        """
        import time

        if not self._initialized:
            raise RuntimeError("Analyzer not initialized. Call initialize() first.")

        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = file_path.stat().st_size

        if file_size > self.config.max_file_size:
            raise ValueError(f"File too large: {file_size} bytes (max: {self.config.max_file_size})")

        start_time = time.perf_counter()

        result = UnicodeAnalysisResult()
        result.processed_bytes = file_size

        offset = 0
        char_count = 0
        chunk_size = self.config.chunk_size

        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break

                    char_count += len(chunk)

                    # Analyze chunk
                    if self.config.detect_zero_width:
                        result.zero_width_findings.extend(self._detect_zero_width(chunk, offset))

                    if self.config.detect_homoglyphs:
                        result.homoglyph_findings.extend(self._detect_homoglyphs(chunk, offset))

                    if self.config.detect_bidi_attacks:
                        result.bidi_findings.extend(self._detect_bidi_attacks(chunk, offset))

                    if self.config.detect_normalization:
                        result.normalization_findings.extend(self._detect_normalization_anomalies(chunk, offset))

                    offset += len(chunk)

                    # Yield control periodically for async
                    if offset % (chunk_size * 10) == 0:
                        await asyncio.sleep(0)

        except UnicodeDecodeError:
            # Try with different encoding
            try:
                with open(file_path, 'r', encoding='utf-16', errors='replace') as f:
                    content = f.read()
                    result = self.analyze_text(content)
                    result.processed_bytes = file_size
                    return result
            except Exception as e:
                logger.error(f"Failed to decode file {file_path}: {e}")
                raise

        result.total_chars = char_count
        result.risk_score = self._calculate_risk_score(result)

        end_time = time.perf_counter()
        result.processing_time_ms = (end_time - start_time) * 1000

        return result

    def _calculate_risk_score(self, result: UnicodeAnalysisResult) -> float:
        """
        Calculate overall risk score based on findings.

        Returns:
            Risk score from 0.0 (no risk) to 100.0 (critical)
        """
        score = 0.0

        # Zero-width characters (high risk - invisible text attacks)
        zw_count = len(result.zero_width_findings)
        if zw_count > 0:
            score += min(30.0, zw_count * 5.0)

        # Homoglyph attacks (medium-high risk - spoofing)
        hg_count = len(result.homoglyph_findings)
        if hg_count > 0:
            score += min(25.0, hg_count * 2.5)

        # Bidi attacks (critical risk - code spoofing)
        bidi_count = len(result.bidi_findings)
        high_risk_bidi = sum(
            1 for f in result.bidi_findings
            if f.attack_type in ("RLO_ATTACK", "LRO_ATTACK")
        )
        if bidi_count > 0:
            score += min(40.0, bidi_count * 8.0)
        if high_risk_bidi > 0:
            score += min(25.0, high_risk_bidi * 12.5)

        # Normalization anomalies (medium risk - spoofing)
        norm_count = len(result.normalization_findings)
        if norm_count > 0:
            score += min(15.0, norm_count * 1.5)

        return min(100.0, score)

    def compute_skeleton_hash(self, text: str) -> str:
        """
        Compute UTS #39 skeleton hash for confusables detection.

        Applies:
        - NFD normalization
        - Basic confusable mapping (using loaded mappings if available)
        - Re-NFD normalization
        - Returns sha256(skeleton)[:16]

        This is used for:
        - Spoof network clustering (same skeleton = possible confusables)
        - Internal signal only (skeleton text is NOT stored)

        Args:
            text: Input text (typically hostname or URL segment)

        Returns:
            16-char hex digest of skeleton hash
        """
        if not text:
            return ""

        # Step 1: NFD normalization
        normalized = unicodedata.normalize('NFD', text)

        # Step 2: Map confusables to canonical forms (if mappings loaded)
        skeleton_chars = []
        for char in normalized:
            # Check if this character is a known confusable
            canonical = self._canonical_map.get(char)
            if canonical:
                skeleton_chars.append(canonical)
            else:
                skeleton_chars.append(char)

        skeleton = ''.join(skeleton_chars)

        # Step 3: Re-NFD normalize
        skeleton = unicodedata.normalize('NFD', skeleton)

        # Step 4: Compute hash (store only digest, not skeleton)
        result_digest = hashlib.sha256(skeleton.encode('utf-8')).hexdigest()[:16]

        return result_digest

    def detect_mixed_script(self, text: str) -> bool:
        """
        Detect mixed-script usage in text (potential spoofing indicator).

        Args:
            text: Input text to check

        Returns:
            True if mixed scripts detected
        """
        if not text:
            return False

        scripts = set()
        for char in text:
            if char.isascii():
                scripts.add('LATIN')
            else:
                try:
                    script = unicodedata.name(char, '').split()[0]
                    if script:
                        scripts.add(script)
                except ValueError:
                    pass

        # Mixed script = more than 2 scripts (LATIN + 1 other = OK for domain names)
        return len(scripts) > 2

    async def cleanup(self) -> None:
        """Clean up resources and free memory."""
        async with self._lock:
            self._confusable_map.clear()
            self._canonical_map.clear()
            self._confusable_set = frozenset()
            self._initialized = False
            logger.info("UnicodeAttackAnalyzer cleaned up")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if self._initialized:
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    # Already in async context - fire-and-forget cleanup
                    asyncio.create_task(self.cleanup())
                else:
                    # M1-SAFE: Loop exists but not running - use run_until_complete on
                    # the existing loop from this worker thread. This avoids creating
                    # a nested event loop with asyncio.run() which crashes Metal on M1.
                    # F206AE: Fixed - use run_in_executor with default executor instead
                    # of creating a new ThreadPoolExecutor that is never shut down.
                    loop.run_in_executor(None, lambda: loop.run_until_complete(self.cleanup()))
            except RuntimeError:
                # No running loop - asyncio.run() directly is safe (no existing loop).
                # F196A: Do NOT wrap in ThreadPoolExecutor.submit() - that creates
                # a nested event loop which crashes Metal on Apple Silicon M1.
                try:
                    asyncio.run(self.cleanup())
                except Exception as e:
                    logger.warning(f"[F206AC] unicode_analyzer cleanup failed: {e}")


def create_unicode_analyzer(config: Optional[UnicodeConfig] = None) -> Optional[UnicodeAttackAnalyzer]:
    """
    Factory function to create a Unicode attack analyzer.

    Args:
        config: Optional configuration for the analyzer

    Returns:
        UnicodeAttackAnalyzer instance or None if creation fails
    """
    try:
        return UnicodeAttackAnalyzer(config or UnicodeConfig())
    except Exception as e:
        logger.error(f"Failed to create UnicodeAttackAnalyzer: {e}")
        return None


# Convenience async factory
async def create_and_initialize_unicode_analyzer(
    config: Optional[UnicodeConfig] = None
) -> Optional[UnicodeAttackAnalyzer]:
    """
    Factory function to create and initialize a Unicode attack analyzer.

    Args:
        config: Optional configuration for the analyzer

    Returns:
        Initialized UnicodeAttackAnalyzer instance or None if creation fails
    """
    analyzer = create_unicode_analyzer(config)
    if analyzer:
        await analyzer.initialize()
    return analyzer
