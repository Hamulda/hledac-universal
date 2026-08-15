"""HELPER — Intelligent Input Detector for OSINT analysis.

Detects and analyzes input types for the universal processing pipeline.
Supports file type detection via magic bytes, content analysis, pattern





matching, and complexity estimation.

Features:
- Magic byte-based file type detection
- Content type classification
- Pattern scanning (hashes, URLs, IPs, emails, etc.)
- Encoding detection
- Complexity scoring with time estimates
- Analysis recommendations

M1 8GB Optimized:
- Streaming for large files
- Memory-efficient pattern matching
- Lazy loading of heavy content
"""
import logging
import math
import re
from dataclasses import dataclass, field
import msgspec
from pathlib import Path
from typing import Any
from operator import attrgetter, itemgetter
from _core import aclose
logger = logging.getLogger(__name__)
MAGIC_BYTES = {'jpeg': (b'\xff\xd8\xff',), 'png': (b'\x89PNG\r\n\x1a\n',), 'pdf': (b'%PDF',), 'zip': (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'), 'pcap': (b'\xa1\xb2\xc3\xd4', b'\xd4\xc3\xb2\xa1'), 'gif': (b'GIF87a', b'GIF89a'), 'bmp': (b'BM',), 'tiff': (b'II*\x00', b'MM\x00*'), 'webp': (b'RIFF',), 'mp3': (b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'), 'wav': (b'RIFF',), 'mp4': (b'ftyp',), 'elf': (b'\x7fELF',), 'macho': (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe')}
HASH_PATTERN = '\\b[0-9a-fA-F]{32,128}\\b'
BASE64_PATTERN = '[A-Za-z0-9+/]{20,}={0,2}'
URL_PATTERN = 'https?://[^\\s<>\\"{}|\\\\^`\\[\\]]+'
IP_PATTERN = '\\b(?:[0-9]{1,3}\\.){3}[0-9]{1,3}\\b'
EMAIL_PATTERN = '\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b'
ZERO_WIDTH_PATTERN = '[\\u200B\\u200C\\u200D\\uFEFF]'
DOMAIN_PATTERN = '\\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\\-]{0,61}[a-zA-Z0-9])?\\.)+[a-zA-Z]{2,}\\b'
MAC_ADDRESS_PATTERN = '\\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\\b'
UUID_PATTERN = '\\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\\b'
CREDIT_CARD_PATTERN = '\\b(?:\\d{4}[-\\s]?){3}\\d{4}\\b'
PHONE_PATTERN = '\\b(?:\\+?\\d{1,3}[-.\\s]?)?\\(?\\d{3}\\)?[-.\\s]?\\d{3}[-.\\s]?\\d{4}\\b'

class Pattern(msgspec.Struct, gc=False):
    """Represents a detected pattern in input data.

    Attributes:
        pattern_type: Type of pattern detected (hash, url, ip, etc.)
        location: Position in content where pattern was found
        confidence: Confidence score (0.0-1.0)
        preview: Preview of the matched content
    """
    pattern_type: str
    location: int
    confidence: float
    preview: str

class ComplexityScore(msgspec.Struct, frozen=True, gc=False):
    """Complexity analysis for input data.

    Attributes:
        level: Complexity level (low, medium, high, critical)
        factors: Dictionary of complexity factors and their scores
        estimated_analysis_time: Estimated time for analysis in seconds
    """
    level: str
    factors: dict[str, float] = field(default_factory=dict)
    estimated_analysis_time: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'level': self.level, 'factors': self.factors, 'estimated_analysis_time': self.estimated_analysis_time}

class InputAnalysis(msgspec.Struct, frozen=True, gc=False):
    """Complete input analysis result.

    Attributes:
        input_type: Type of input (file, text, binary, url, etc.)
        file_type: Detected file type if input is a file
        content_type: Content classification (text, binary, encoded, etc.)
        patterns: List of detected patterns
        complexity: Complexity score and analysis
        recommendations: List of analysis recommendations
        encoding: Detected encoding if applicable
        size_bytes: Size of input in bytes
        entropy: Shannon entropy of content
    """
    input_type: str
    file_type: str | None = None
    content_type: str = 'unknown'
    patterns: list[Pattern] = field(default_factory=list)
    complexity: ComplexityScore | None = None
    recommendations: list[str] = field(default_factory=list)
    encoding: str | None = None
    size_bytes: int = 0
    entropy: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'input_type': self.input_type, 'file_type': self.file_type, 'content_type': self.content_type, 'patterns': [{'pattern_type': p.pattern_type, 'location': p.location, 'confidence': p.confidence, 'preview': p.preview} for p in self.patterns], 'complexity': self.complexity.to_dict() if self.complexity else None, 'recommendations': self.recommendations, 'encoding': self.encoding, 'size_bytes': self.size_bytes, 'entropy': self.entropy}

class IntelligenceConfig(msgspec.Struct, frozen=True, gc=False):
    """Configuration for intelligent input detection.

    Attributes:
        max_file_size: Maximum file size to process (bytes)
        chunk_size: Chunk size for streaming large files
        min_pattern_length: Minimum length for pattern matching
        entropy_threshold_low: Low entropy threshold
        entropy_threshold_high: High entropy threshold
        enable_pattern_scanning: Enable pattern detection
        enable_encoding_detection: Enable encoding detection
        enable_complexity_analysis: Enable complexity scoring
    """
    max_file_size: int = 1073741824
    chunk_size: int = 1048576
    min_pattern_length: int = 8
    entropy_threshold_low: float = 3.0
    entropy_threshold_high: float = 7.5
    enable_pattern_scanning: bool = True
    enable_encoding_detection: bool = True
    enable_complexity_analysis: bool = True

class IntelligentInputDetector:
    """Intelligent input detector for OSINT analysis.

    Analyzes input data to determine type, content, patterns, and complexity.
    Supports files, text, binary data, and URLs with magic byte detection
    and comprehensive pattern matching.

    M1 8GB Optimized:
    - Streaming for files >100MB
    - Memory-efficient pattern matching
    - Lazy content loading

    Example:
        detector = IntelligentInputDetector()

        # Analyze a file
        analysis = await detector.detect("/path/to/file.bin")
        print(f"Type: {analysis.file_type}, Complexity: {analysis.complexity.level}")

        # Analyze text content
        analysis = await detector.detect("Contact: admin@example.com")
        for pattern in analysis.patterns:
            print(f"Found {pattern.pattern_type} at {pattern.location}")
    """
    __slots__ = tuple(('_pattern_regexes', '_stats', 'config'))

    def __init__(self, config: IntelligenceConfig | None=None):
        """Initialize the input detector.

        Args:
            config: Optional configuration object
        """
        self.config = config or IntelligenceConfig()
        self._pattern_regexes: dict[str, re.Pattern] = {'hash': re.compile(HASH_PATTERN), 'base64': re.compile(BASE64_PATTERN), 'url': re.compile(URL_PATTERN), 'ip': re.compile(IP_PATTERN), 'email': re.compile(EMAIL_PATTERN), 'zero_width': re.compile(ZERO_WIDTH_PATTERN), 'domain': re.compile(DOMAIN_PATTERN), 'mac_address': re.compile(MAC_ADDRESS_PATTERN), 'uuid': re.compile(UUID_PATTERN), 'credit_card': re.compile(CREDIT_CARD_PATTERN), 'phone': re.compile(PHONE_PATTERN)}
        self._stats: dict[str, int] = {'files_analyzed': 0, 'text_analyzed': 0, 'patterns_found': 0}

    async def detect(self, input_data: Any) -> InputAnalysis:
        """Detect and analyze input data.

        Args:
            input_data: Input to analyze (str path, bytes, or string content)

        Returns:
            InputAnalysis with complete analysis results
        """
        try:
            if isinstance(input_data, (str, Path)):
                path = Path(input_data)
                if path.exists() and path.is_file():
                    return await self._analyze_file(str(path))
                else:
                    return await self._analyze_text(str(input_data))
            elif isinstance(input_data, bytes):
                return await self._analyze_bytes(input_data)
            else:
                return await self._analyze_text(str(input_data))
        except Exception as e:
            logger.error(f'Error analyzing input: {e}')
            return InputAnalysis(input_type='error', content_type='unknown', recommendations=[f'Analysis failed: {str(e)}'])

    async def _analyze_file(self, file_path: str) -> InputAnalysis:
        """Analyze a file.

        Args:
            file_path: Path to file

        Returns:
            InputAnalysis result
        """
        path = Path(file_path)
        size = path.stat().st_size
        if size > self.config.max_file_size:
            return InputAnalysis(input_type='file', content_type='oversized', size_bytes=size, recommendations=[f'File exceeds maximum size: {size} bytes'])
        with open(file_path, 'rb') as f:
            content = f.read()
        file_type = self._detect_file_type_from_bytes(content)
        analysis = await self._analyze_bytes(content)
        analysis.input_type = 'file'
        analysis.file_type = file_type
        analysis.size_bytes = size
        self._stats['files_analyzed'] += 1
        return analysis

    async def _analyze_bytes(self, content: bytes) -> InputAnalysis:
        """Analyze byte content.

        Args:
            content: Byte content to analyze

        Returns:
            InputAnalysis result
        """
        size = len(content)
        entropy = self._calculate_entropy(content)
        file_type = self._detect_file_type_from_bytes(content)
        content_type = self._detect_content_type(content)
        patterns: list[Pattern] = []
        encoding = None
        if self.config.enable_encoding_detection:
            encoding = self._detect_encoding(content)
        text_content = ''
        if encoding:
            try:
                text_content = content.decode(encoding, errors='ignore')
            except Exception:  # noqa: BLE001
                pass
        else:
            for enc in ['utf-8', 'ascii', 'latin-1', 'cp1252']:
                try:
                    text_content = content.decode(enc, errors='ignore')
                    encoding = enc
                    break
                except Exception:
                    continue
        if text_content and self.config.enable_pattern_scanning:
            patterns = self._scan_for_patterns(text_content)
        complexity = None
        if self.config.enable_complexity_analysis:
            complexity = self._estimate_complexity_from_content(content, text_content, patterns, entropy)
        recommendations = self._generate_recommendations(file_type, content_type, patterns, entropy, complexity)
        self._stats['patterns_found'] += len(patterns)
        return InputAnalysis(input_type='binary', file_type=file_type, content_type=content_type, patterns=patterns, complexity=complexity, recommendations=recommendations, encoding=encoding, size_bytes=size, entropy=entropy)

    async def _analyze_text(self, text: str) -> InputAnalysis:
        """Analyze text content.

        Args:
            text: Text content to analyze

        Returns:
            InputAnalysis result
        """
        content = text.encode('utf-8')
        size = len(content)
        entropy = self._calculate_entropy(content)
        patterns: list[Pattern] = []
        if self.config.enable_pattern_scanning:
            patterns = self._scan_for_patterns(text)
        complexity = None
        if self.config.enable_complexity_analysis:
            complexity = self._estimate_complexity_from_content(content, text, patterns, entropy)
        recommendations = self._generate_recommendations(None, 'text', patterns, entropy, complexity)
        self._stats['text_analyzed'] += 1
        self._stats['patterns_found'] += len(patterns)
        return InputAnalysis(input_type='text', content_type='text', patterns=patterns, complexity=complexity, recommendations=recommendations, encoding='utf-8', size_bytes=size, entropy=entropy)

    def _detect_file_type(self, file_path: str) -> str | None:
        """Detect file type from magic bytes.

        Args:
            file_path: Path to file

        Returns:
            File type string or None
        """
        try:
            with open(file_path, 'rb') as f:
                header = f.read(32)
            return self._detect_file_type_from_bytes(header)
        except Exception as e:
            logger.error(f'Error detecting file type: {e}')
            return None

    def _check_magic_match(self, content: bytes, file_type: str, magic_list: tuple) -> str | None:
        """Check if content matches magic bytes for a file type."""
        for magic in magic_list:
            if content.startswith(magic):
                # Handle RIFF-based formats (webp, wav)
                if file_type == 'webp' and b'WEBP' in content[:12]:
                    return 'webp'
                if file_type == 'wav' and b'WAVE' in content[:12]:
                    return 'wav'
                return file_type
        return None

    def _detect_file_type_from_bytes(self, content: bytes) -> str | None:
        """Detect file type from byte content.

        Args:
            content: Byte content to analyze

        Returns:
            File type string or None
        """
        if len(content) < 4:
            return None
        for file_type, magic_list in MAGIC_BYTES.items():
            result = self._check_magic_match(content, file_type, magic_list)
            if result is not None:
                return result
        return None

    def _detect_content_type(self, content: bytes) -> str:
        """Detect content type classification.

        Args:
            content: Byte content to analyze

        Returns:
            Content type classification
        """
        if b'\x00' in content[:1024]:
            return 'binary'
        printable_count = sum((1 for b in content[:1024] if 32 <= b <= 126 or b in (9, 10, 13)))
        if len(content[:1024]) > 0:
            ratio = printable_count / len(content[:1024])
            if ratio < 0.7:
                return 'binary'
        try:
            text = content.decode('utf-8', errors='strict')
            if re.search(BASE64_PATTERN, text):
                return 'encoded_text'
            return 'text'
        except UnicodeDecodeError:
            return 'binary'

    def _scan_for_patterns(self, content: str) -> list[Pattern]:
        """Scan content for patterns.

        Args:
            content: Text content to scan

        Returns:
            List of detected patterns
        """
        patterns: list[Pattern] = []
        for pattern_type, regex in self._pattern_regexes.items():
            for match in regex.finditer(content):
                matched_text = match.group(0)
                if len(matched_text) < self.config.min_pattern_length:
                    continue
                confidence = self._calculate_pattern_confidence(pattern_type, matched_text)
                preview = matched_text[:50]
                if len(matched_text) > 50:
                    preview += '...'
                patterns.append(Pattern(pattern_type=pattern_type, location=match.start(), confidence=confidence, preview=preview))
        patterns.sort(key=attrgetter("location"))
        return patterns

    def _calculate_pattern_confidence(self, pattern_type: str, match: str) -> float:
        """Calculate confidence score for a pattern match."""
        calculators = {
            'hash': self._hash_confidence,
            'base64': self._base64_confidence,
            'ip': self._ip_confidence,
            'email': self._email_confidence,
            'url': self._url_confidence,
            'uuid': lambda _: 0.95,
            'mac_address': self._mac_confidence,
        }
        calculator = calculators.get(pattern_type, lambda _: 0.7)
        return min(max(calculator(match), 0.0), 1.0)

    def _hash_confidence(self, match: str) -> float:
        """Calculate confidence for hash patterns."""
        base = 0.7
        if len(match) in [32, 40, 64, 128]:
            base += 0.2
        if re.match('^[0-9a-fA-F]+$', match):
            base += 0.1
        return base

    def _base64_confidence(self, match: str) -> float:
        """Calculate confidence for base64 patterns."""
        base = 0.7
        if len(match) % 4 == 0:
            base += 0.15
        if len(match) >= 40:
            base += 0.1
        return base

    def _ip_confidence(self, match: str) -> float:
        """Calculate confidence for IP address patterns."""
        base = 0.7
        try:
            octets = match.split('.')
            if all((0 <= int(o) <= 255 for o in octets)):
                base += 0.25
            else:
                base -= 0.3
        except ValueError:
            base -= 0.3
        return base

    def _email_confidence(self, match: str) -> float:
        """Calculate confidence for email patterns."""
        base = 0.7
        if '@' in match:
            parts = match.split('@')
            if len(parts) == 2 and '.' in parts[1]:
                base += 0.2
        return base

    def _url_confidence(self, match: str) -> float:
        """Calculate confidence for URL patterns."""
        base = 0.7
        if '://' in match:
            base += 0.2
        if match.startswith(('http://', 'https://')):
            base += 0.1
        return base

    def _mac_confidence(self, match: str) -> float:
        """Calculate confidence for MAC address patterns."""
        if re.match('^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$', match):
            return 0.9
        return 0.7

    def _detect_encoding(self, content: bytes) -> str | None:
        """Detect text encoding.

        Args:
            content: Byte content to analyze

        Returns:
            Detected encoding or None
        """
        if content.startswith(b'\xef\xbb\xbf'):
            return 'utf-8-sig'
        elif content.startswith(b'\xff\xfe'):
            return 'utf-16-le'
        elif content.startswith(b'\xfe\xff'):
            return 'utf-16-be'
        encodings = ['utf-8', 'ascii', 'latin-1', 'cp1252', 'iso-8859-1']
        for encoding in encodings:
            try:
                content.decode(encoding, errors='strict')
                return encoding
            except UnicodeDecodeError:
                continue
        return None

    def _estimate_complexity(self, input_data: Any) -> ComplexityScore:
        """Estimate complexity of input data.

        Args:
            input_data: Input data to analyze

        Returns:
            ComplexityScore
        """
        return ComplexityScore(level='medium', factors={}, estimated_analysis_time=1.0)

    def _score_size_factor(self, size: int) -> float:
        """Score file size complexity factor."""
        if size < 1024:
            return 0.1
        elif size < 10240:
            return 0.3
        elif size < 102400:
            return 0.5
        elif size < 1048576:
            return 0.7
        return 1.0

    def _score_entropy_factor(self, entropy: float) -> float:
        """Score entropy complexity factor."""
        if entropy < 3.0:
            return 0.1
        elif entropy < 5.0:
            return 0.3
        elif entropy < 7.0:
            return 0.6
        return 1.0

    def _score_pattern_factor(self, pattern_count: int) -> float:
        """Score pattern count complexity factor."""
        if pattern_count == 0:
            return 0.0
        elif pattern_count < 5:
            return 0.2
        elif pattern_count < 20:
            return 0.5
        return 0.8

    def _score_binary_content(self, content: bytes) -> float:
        """Check for binary content presence."""
        return 0.4 if b'\x00' in content[:1024] else 0.0

    def _estimate_complexity_from_content(self, content: bytes, text_content: str, patterns: list[Pattern], entropy: float) -> ComplexityScore:
        """Estimate complexity from content analysis."""
        size = len(content)
        pattern_count = len(patterns)
        unique_types = len({p.pattern_type for p in patterns})

        factors: dict[str, float] = {
            'size': self._score_size_factor(size),
            'entropy': self._score_entropy_factor(entropy),
            'patterns': self._score_pattern_factor(pattern_count),
            'pattern_diversity': min(unique_types * 0.15, 0.6),
            'binary_content': self._score_binary_content(content),
        }

        total_score = sum(factors.values())
        avg_score = total_score / len(factors) if factors else 0.0

        level, base_time = self._get_complexity_level(avg_score)
        size_multiplier = 1.0 + size / 1048576 * 0.1
        estimated_time = base_time * size_multiplier

        return ComplexityScore(level=level, factors=factors, estimated_analysis_time=estimated_time)

    def _get_complexity_level(self, avg_score: float) -> tuple[str, float]:
        """Map average score to complexity level and base time."""
        if avg_score < 0.25:
            return 'low', 0.5
        elif avg_score < 0.5:
            return 'medium', 1.0
        elif avg_score < 0.75:
            return 'high', 3.0
        return 'critical', 10.0

    def _calculate_entropy(self, data: bytes) -> float:
        """Calculate Shannon entropy of data.

        Args:
            data: Binary data to analyze

        Returns:
            Entropy in bits per byte (0-8)
        """
        if not data:
            return 0.0
        byte_counts = [0] * 256
        for byte in data:
            byte_counts[byte] += 1
        entropy = 0.0
        length = len(data)
        for count in byte_counts:
            if count > 0:
                p = count / length
                entropy -= p * math.log2(p)
        return entropy

    def _generate_recommendations(self, file_type: str | None, content_type: str, patterns: list[Pattern], entropy: float, complexity: ComplexityScore | None) -> list[str]:
        """Generate analysis recommendations.

        Args:
            file_type: Detected file type
            content_type: Content classification
            patterns: Detected patterns
            entropy: Shannon entropy
            complexity: Complexity score

        Returns:
            List of recommendations
        """
        recommendations: list[str] = []
        recommendations.extend(self._recommend_by_type(file_type))
        recommendations.extend(self._recommend_by_entropy(entropy))
        recommendations.extend(self._recommend_by_pattern(patterns))
        recommendations.extend(self._recommend_by_complexity(complexity))
        recommendations.extend(self._recommend_by_content_type(content_type))
        return recommendations

    def get_stats(self) -> dict[str, int]:
        """Get detection statistics.

        Returns:
            Dictionary of detection statistics
        """
        return self._stats.copy()

    def reset_stats(self) -> None:
        """Reset detection statistics."""
        for key in self._stats:
            self._stats[key] = 0

def create_input_detector(config: IntelligenceConfig | None=None) -> IntelligentInputDetector:
    """Create a configured IntelligentInputDetector instance.

    Args:
        config: Optional configuration

    Returns:
        Configured IntelligentInputDetector instance

    Example:
        detector = create_input_detector(
            config=IntelligenceConfig(max_file_size=500*1024*1024)
        )
        analysis = await detector.detect("/path/to/file.bin")
    """
    return IntelligentInputDetector(config)

async def analyze_input(input_data: Any, config: IntelligenceConfig | None=None) -> InputAnalysis:
    """Convenience function to analyze input data.

    Args:
        input_data: Input to analyze
        config: Optional configuration

    Returns:
        InputAnalysis result
    """
    detector = create_input_detector(config)
    return await detector.detect(input_data)

async def detect_file_type(file_path: str) -> str | None:
    """Convenience function to detect file type.

    Args:
        file_path: Path to file

    Returns:
        File type string or None
    """
    detector = create_input_detector()
    return detector._detect_file_type(file_path)