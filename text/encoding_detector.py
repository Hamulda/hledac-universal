"""Base Encoding Detector for OSINT text analysis.

Detects Base64, Base32, Base85, Hex encoding in text with statistical
validation and nested encoding detection.
"""

from __future__ import annotations

import base64
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Regex patterns for encoding detection
BASE64_REGEX = r'[A-Za-z0-9+/]{20,}={0,2}'
BASE32_REGEX = r'[A-Z2-7]{20,}={0,6}'
BASE85_REGEX = r'<~[!-u]+~>'
HEX_REGEX = r'(?:[0-9a-fA-F]{2}){10,}'
URL_ENCODING_REGEX = r'(?:%[0-9A-Fa-f]{2})+'

MIN_ENTROPY = 2.5
MAX_ENTROPY = 7.5


@dataclass
class EncodingChain:
    """Represents a chain of nested encodings.

    Attributes:
        encodings: List of encoding types in order (e.g., ["base64", "hex"])
        final_content: Final decoded content
        depth: Depth of the encoding chain
    """
    encodings: list[str]
    final_content: str
    depth: int


@dataclass
class EncodingFinding:
    """Represents a detected encoding in text.

    Attributes:
        encoding_type: Type of encoding (base64, hex, etc.)
        position: Position in original text
        length: Length of the encoded string
        confidence: Confidence score (0.0-1.0)
        decoded_preview: Preview of decoded content
        nested_chain: Optional nested encoding chain
        original: Original encoded string
        is_printable: Whether decoded content is printable ASCII
        entropy: Shannon entropy of decoded content
    """
    encoding_type: str
    position: int
    length: int
    confidence: float
    decoded_preview: str
    original: str
    is_printable: bool = False
    entropy: float = 0.0
    nested_chain: EncodingChain | None = None


@dataclass
class EncodingConfig:
    """Configuration for encoding detection.

    Attributes:
        min_length: Minimum length to consider for encoding
        max_depth: Maximum depth for nested encoding detection
        detect_nested: Whether to detect nested encodings
        chunk_size: Chunk size for streaming file processing
        min_entropy: Minimum entropy threshold
        max_entropy: Maximum entropy threshold
    """
    min_length: int = 20
    max_depth: int = 5
    detect_nested: bool = True
    chunk_size: int = 1048576  # 1MB
    min_entropy: float = 2.5
    max_entropy: float = 7.5


class BaseEncodingDetector:
    """Detects various base encodings in text.

    Supports Base64, Base32, Base85, Hexadecimal, and URL encoding.
    Includes statistical validation and nested encoding detection.

    Example:
        detector = BaseEncodingDetector()
        text = "Here is encoded data: SGVsbG8gV29ybGQh"
        findings = await detector.detect_text(text)
        for finding in findings:
            print(f"Found {finding.encoding_type} at {finding.position}")
    """

    def __init__(self, config: EncodingConfig | None = None):
        """Initialize the encoding detector.

        Args:
            config: Optional configuration object
        """
        self.config = config or EncodingConfig()
        self._stats: dict[str, int] = {
            'base64_found': 0,
            'base32_found': 0,
            'base85_found': 0,
            'hex_found': 0,
            'url_found': 0,
            'nested_found': 0,
        }

    def _calculate_entropy(self, data: bytes) -> float:
        """Calculate Shannon entropy of data.

        Args:
            data: Binary data to analyze

        Returns:
            Entropy in bits per byte
        """
        if not data:
            return 0.0

        entropy = 0.0
        for x in range(256):
            p_x = float(data.count(x)) / len(data)
            if p_x > 0:
                entropy += -p_x * math.log(p_x, 2)

        return entropy

    def _is_printable(self, data: bytes) -> bool:
        """Check if data is printable ASCII.

        Args:
            data: Binary data to check

        Returns:
            True if all bytes are printable ASCII
        """
        return all(32 <= b <= 126 or b in (9, 10, 13) for b in data)

    def _get_preview(self, data: bytes, max_length: int = 100) -> str:
        """Get a preview of decoded content.

        Args:
            data: Binary data
            max_length: Maximum preview length

        Returns:
            String preview of content
        """
        try:
            text = data.decode('utf-8', errors='ignore')
            if len(text) > max_length:
                text = text[:max_length] + "..."
            return text
        except Exception:
            return f"<binary data: {len(data)} bytes>"

    async def detect_text(self, text: str) -> list[EncodingFinding]:
        """Detect encodings in text.

        Args:
            text: Input text to scan

        Returns:
            List of encoding findings
        """
        findings: list[EncodingFinding] = []

        # Detect various encodings
        findings.extend(self._detect_base64(text))
        findings.extend(self._detect_base32(text))
        findings.extend(self._detect_base85(text))
        findings.extend(self._detect_hex(text))
        findings.extend(self._detect_url_encoding(text))

        # Sort by position
        findings.sort(key=lambda x: x.position)

        # Detect nested encodings if enabled
        if self.config.detect_nested:
            for finding in findings:
                nested = await self._analyze_nested(finding)
                if nested:
                    finding.nested_chain = nested
                    self._stats['nested_found'] += 1

        return findings

    async def detect_file(self, file_path: str) -> list[EncodingFinding]:
        """Stream-process large file for encoding detection.

        Args:
            file_path: Path to text file

        Returns:
            List of encoding findings
        """
        findings: list[EncodingFinding] = []
        chunk_size = self.config.chunk_size
        overlap = 1000  # Overlap to catch encodings across boundaries

        path = Path(file_path)
        if not path.exists():
            logger.error(f"File not found: {file_path}")
            return findings

        try:
            with open(path, encoding='utf-8', errors='ignore') as f:
                previous_chunk = ""
                offset = 0

                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break

                    # Combine with previous overlap
                    combined = previous_chunk + chunk

                    # Detect encodings in combined chunk
                    chunk_findings = await self.detect_text(combined)

                    # Filter out duplicates from overlap region and adjust positions
                    for finding in chunk_findings:
                        if finding.position >= len(previous_chunk):
                            finding.position += offset
                            findings.append(finding)

                    # Save overlap for next iteration
                    previous_chunk = chunk[-overlap:] if len(chunk) >= overlap else chunk
                    offset += len(chunk)

        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")

        return findings

    def _detect_base64(self, text: str) -> list[EncodingFinding]:
        """Detect Base64 encoded strings.

        Args:
            text: Input text to scan

        Returns:
            List of Base64 findings
        """
        findings = []

        for match in re.finditer(BASE64_REGEX, text):
            candidate = match.group(0)

            # Length validation
            if len(candidate) < self.config.min_length:
                continue

            # Must be multiple of 4 (with padding)
            if len(candidate) % 4 != 0:
                continue

            # Try to decode
            try:
                # Validate charset
                if not re.match(r'^[A-Za-z0-9+/]+={0,2}$', candidate):
                    continue

                decoded = base64.b64decode(candidate, validate=True)

                if not decoded:
                    continue

                # Entropy check
                entropy = self._calculate_entropy(decoded)
                if not (self.config.min_entropy <= entropy <= self.config.max_entropy):
                    continue

                # Confidence scoring
                confidence = 0.7
                is_printable = self._is_printable(decoded)
                if is_printable:
                    confidence += 0.2
                if len(candidate) >= 40:
                    confidence += 0.1

                findings.append(EncodingFinding(
                    encoding_type="base64",
                    position=match.start(),
                    length=len(candidate),
                    confidence=min(confidence, 1.0),
                    decoded_preview=self._get_preview(decoded),
                    original=candidate,
                    is_printable=is_printable,
                    entropy=entropy
                ))
                self._stats['base64_found'] += 1

            except Exception:
                continue

        return findings

    def _detect_base32(self, text: str) -> list[EncodingFinding]:
        """Detect Base32 encoded strings.

        Args:
            text: Input text to scan

        Returns:
            List of Base32 findings
        """
        findings = []

        for match in re.finditer(BASE32_REGEX, text):
            candidate = match.group(0)

            if len(candidate) < self.config.min_length:
                continue

            # Must be multiple of 8
            if len(candidate) % 8 != 0:
                continue

            try:
                decoded = base64.b32decode(candidate)

                if not decoded:
                    continue

                entropy = self._calculate_entropy(decoded)
                if not (self.config.min_entropy <= entropy <= self.config.max_entropy):
                    continue

                confidence = 0.8  # Higher confidence - less common
                is_printable = self._is_printable(decoded)

                findings.append(EncodingFinding(
                    encoding_type="base32",
                    position=match.start(),
                    length=len(candidate),
                    confidence=confidence,
                    decoded_preview=self._get_preview(decoded),
                    original=candidate,
                    is_printable=is_printable,
                    entropy=entropy
                ))
                self._stats['base32_found'] += 1

            except Exception:
                continue

        return findings

    def _detect_base85(self, text: str) -> list[EncodingFinding]:
        """Detect Base85/Ascii85 encoded strings.

        Args:
            text: Input text to scan

        Returns:
            List of Base85 findings
        """
        findings = []

        for match in re.finditer(BASE85_REGEX, text):
            candidate = match.group(0)

            # Extract content between <~ and ~>
            content = candidate[2:-2]

            if len(content) < self.config.min_length:
                continue

            try:
                decoded = base64.b85decode(content)

                if not decoded:
                    continue

                entropy = self._calculate_entropy(decoded)
                if entropy < self.config.min_entropy:
                    continue

                findings.append(EncodingFinding(
                    encoding_type="base85",
                    position=match.start(),
                    length=len(candidate),
                    confidence=0.9,  # High confidence due to format markers
                    decoded_preview=self._get_preview(decoded),
                    original=candidate,
                    is_printable=self._is_printable(decoded),
                    entropy=entropy
                ))
                self._stats['base85_found'] += 1

            except Exception:
                continue

        return findings

    def _detect_hex(self, text: str) -> list[EncodingFinding]:
        """Detect hexadecimal encoded strings.

        Args:
            text: Input text to scan

        Returns:
            List of hex findings
        """
        findings = []

        for match in re.finditer(HEX_REGEX, text):
            candidate = match.group(0)

            if len(candidate) < self.config.min_length:
                continue

            # Must be even length
            if len(candidate) % 2 != 0:
                continue

            try:
                decoded = bytes.fromhex(candidate)

                if not decoded:
                    continue

                entropy = self._calculate_entropy(decoded)
                if not (self.config.min_entropy <= entropy <= self.config.max_entropy):
                    continue

                confidence = 0.6
                is_printable = self._is_printable(decoded)
                if is_printable:
                    confidence += 0.2

                findings.append(EncodingFinding(
                    encoding_type="hex",
                    position=match.start(),
                    length=len(candidate),
                    confidence=confidence,
                    decoded_preview=self._get_preview(decoded),
                    original=candidate,
                    is_printable=is_printable,
                    entropy=entropy
                ))
                self._stats['hex_found'] += 1

            except Exception:
                continue

        return findings

    def _detect_url_encoding(self, text: str) -> list[EncodingFinding]:
        """Detect URL/percent-encoded strings.

        Args:
            text: Input text to scan

        Returns:
            List of URL encoding findings
        """
        findings = []

        for match in re.finditer(URL_ENCODING_REGEX, text):
            candidate = match.group(0)

            if len(candidate) < 6:  # Minimum %XX%XX
                continue

            try:
                # Unquote the URL-encoded string
                decoded = bytes.fromhex(candidate.replace('%', ''))

                if not decoded:
                    continue

                is_printable = self._is_printable(decoded)
                if not is_printable:
                    continue

                findings.append(EncodingFinding(
                    encoding_type="url_encoded",
                    position=match.start(),
                    length=len(candidate),
                    confidence=0.75,
                    decoded_preview=self._get_preview(decoded),
                    original=candidate,
                    is_printable=True,
                    entropy=self._calculate_entropy(decoded)
                ))
                self._stats['url_found'] += 1

            except Exception:
                continue

        return findings

    async def _analyze_nested(self, finding: EncodingFinding) -> EncodingChain | None:
        """Analyze finding for nested encodings.

        Args:
            finding: The encoding finding to analyze

        Returns:
            Optional encoding chain if nested encodings found
        """
        if finding.nested_chain and finding.nested_chain.depth >= self.config.max_depth:
            return None

        # Get decoded content
        try:
            if finding.encoding_type == "base64":
                decoded = base64.b64decode(finding.original)
            elif finding.encoding_type == "base32":
                decoded = base64.b32decode(finding.original)
            elif finding.encoding_type == "base85":
                decoded = base64.b85decode(finding.original[2:-2])
            elif finding.encoding_type == "hex":
                decoded = bytes.fromhex(finding.original)
            else:
                return None

            # Try to decode as text
            try:
                decoded_str = decoded.decode('utf-8', errors='ignore')
            except Exception:
                return None

            # Look for more encodings in decoded content
            nested_findings = await self.detect_text(decoded_str)

            if nested_findings:
                # Build encoding chain
                chain = EncodingChain(
                    encodings=[finding.encoding_type],
                    final_content=decoded_str,
                    depth=1
                )

                # Add nested encodings
                for nf in nested_findings[:3]:  # Limit to first 3
                    chain.encodings.append(nf.encoding_type)
                    try:
                        if nf.encoding_type == "base64":
                            chain.final_content = base64.b64decode(nf.original).decode('utf-8', errors='ignore')
                        elif nf.encoding_type == "hex":
                            chain.final_content = bytes.fromhex(nf.original).decode('utf-8', errors='ignore')
                    except Exception:
                        pass

                chain.depth = len(chain.encodings)
                return chain

        except Exception:
            pass

        return None

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


# Factory function
def create_encoding_detector(config: EncodingConfig | None = None) -> BaseEncodingDetector:
    """Create a configured BaseEncodingDetector instance.

    Args:
        config: Optional configuration

    Returns:
        Configured BaseEncodingDetector instance
    """
    return BaseEncodingDetector(config)


# Convenience function
async def detect_encodings(text: str, config: EncodingConfig | None = None) -> list[EncodingFinding]:
    """Convenience function to detect encodings in text.

    Args:
        text: Input text to scan
        config: Optional configuration

    Returns:
        List of encoding findings
    """
    detector = create_encoding_detector(config)
    return await detector.detect_text(text)
