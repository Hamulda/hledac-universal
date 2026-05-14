"""Hash Identifier for OSINT password hash analysis.

Identifies 300+ hash algorithms by length, charset, and pattern matching.
Supports hashcat and John the Ripper integration.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Tuple, Set

logger = logging.getLogger(__name__)

# Length-based hash identification
LENGTH_HASHES: Dict[int, List[str]] = {
    32: ['MD5', 'NTLM', 'MD4', 'RIPEMD128', 'HAVAL128', 'Tiger128'],
    40: ['SHA1', 'RIPEMD160', 'HAVAL160', 'Tiger160', 'MySQL5'],
    56: ['SHA224', 'SHA3-224', 'HAVAL224'],
    64: ['SHA256', 'SHA3-256', 'BLAKE2s', 'RIPEMD256', 'HAVAL256', 'GOST'],
    96: ['SHA384', 'SHA3-384'],
    128: ['SHA512', 'SHA3-512', 'Whirlpool', 'BLAKE2b', 'RIPEMD320'],
}

# Pattern-based hash identification (regex -> algorithm)
PATTERN_HASHES: Dict[str, str] = {
    r'^\$1\$': 'MD5 (Unix crypt)',
    r'^\$2a\$': 'bcrypt',
    r'^\$2b\$': 'bcrypt',
    r'^\$2y\$': 'bcrypt',
    r'^\$5\$': 'SHA256 (Unix crypt)',
    r'^\$6\$': 'SHA512 (Unix crypt)',
    r'^\$scrypt\$': 'scrypt',
    r'^\$argon2i\$': 'Argon2i',
    r'^\$argon2d\$': 'Argon2d',
    r'^\$argon2id\$': 'Argon2id',
    r'^pbkdf2_sha256\$': 'PBKDF2-SHA256',
    r'^pbkdf2_sha1\$': 'PBKDF2-SHA1',
    r'^\$P\$': 'phpBB3/WordPress MD5',
    r'^\$H\$': 'phpBB3/WordPress MD5',
    r'^\*[A-F0-9]{40}$': 'MySQL5',
    r'^sha1\$': 'SHA1 (Django)',
    r'^\{SHA\}': 'SHA1 (Base64)',
    r'^\{SSHA\}': 'SSHA',
    r'^\{SSHA256\}': 'SSHA256',
    r'^\{SSHA512\}': 'SSHA512',
    r'^\{CRYPT\}': 'CRYPT',
    r'^\$apr1\$': 'Apache MD5',
    r'^\$md5\$': 'Sun MD5',
    r'^\$sha1\$': 'SHA1 (Cisco)',
}

# Hashcat mode mapping
HASHCAT_MODES: Dict[str, int] = {
    'MD5': 0,
    'SHA1': 100,
    'SHA224': 1300,
    'SHA256': 1400,
    'SHA384': 10800,
    'SHA512': 1700,
    'SHA3-224': 17300,
    'SHA3-256': 17400,
    'SHA3-384': 17500,
    'SHA3-512': 17600,
    'MD5 (Unix crypt)': 500,
    'bcrypt': 3200,
    'SHA256 (Unix crypt)': 7400,
    'SHA512 (Unix crypt)': 1800,
    'scrypt': 8900,
    'Argon2i': 26600,
    'Argon2d': 26600,
    'Argon2id': 26600,
    'PBKDF2-SHA256': 10900,
    'PBKDF2-SHA1': 12001,
    'NTLM': 1000,
    'MySQL5': 300,
    'MySQL4': 200,
    'phpBB3/WordPress MD5': 400,
    'Apache MD5': 1600,
    'GOST': 6900,
    'Whirlpool': 6100,
    'RIPEMD128': 6600,
    'RIPEMD160': 6000,
    'RIPEMD256': 6100,
    'RIPEMD320': 6000,
    'BLAKE2s': 600,
    'BLAKE2b': 610,
    'Tiger128': 6600,
    'Tiger160': 6000,
    'HAVAL128': 6600,
    'HAVAL160': 6000,
    'HAVAL192': 6000,
    'HAVAL224': 6000,
    'HAVAL256': 6000,
}

# John the Ripper format mapping
JOHN_FORMATS: Dict[str, str] = {
    'MD5': 'raw-md5',
    'SHA1': 'raw-sha1',
    'SHA224': 'raw-sha224',
    'SHA256': 'raw-sha256',
    'SHA384': 'raw-sha384',
    'SHA512': 'raw-sha512',
    'MD5 (Unix crypt)': 'md5crypt',
    'bcrypt': 'bcrypt',
    'SHA256 (Unix crypt)': 'sha256crypt',
    'SHA512 (Unix crypt)': 'sha512crypt',
    'scrypt': 'scrypt',
    'Argon2i': 'argon2',
    'Argon2d': 'argon2',
    'Argon2id': 'argon2',
    'PBKDF2-SHA256': 'pbkdf2-hmac-sha256',
    'PBKDF2-SHA1': 'pbkdf2-hmac-sha1',
    'NTLM': 'nt',
    'MySQL5': 'mysql-sha1',
    'MySQL4': 'mysql',
    'phpBB3/WordPress MD5': 'phpass',
    'GOST': 'gost',
    'Whirlpool': 'whirlpool',
    'RIPEMD128': 'ripemd-128',
    'RIPEMD160': 'ripemd-160',
    'RIPEMD256': 'ripemd-256',
    'RIPEMD320': 'ripemd-320',
}

# Charset patterns
HEX_CHARSET = re.compile(r'^[0-9a-fA-F]+$')
BASE64_CHARSET = re.compile(r'^[A-Za-z0-9+/=]+$')
ALPHANUM_CHARSET = re.compile(r'^[A-Za-z0-9]+$')

# Pre-compiled pattern hashes: (compiled_regex, algorithm, original_pattern)
_COMPILED_PATTERN_HASHES: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(
    (re.compile(pattern), algo, pattern)
    for pattern, algo in PATTERN_HASHES.items()
)

# Pre-compiled hex hash scan pattern (shared across identify_in_file)
_HEX_HASH_SCAN_RE = re.compile(r'\b[0-9a-fA-F]{32,128}\b')

# Pre-compiled scan patterns for identify_in_file: (compiled_regex, original_pattern)
_COMPILED_SCAN_PATTERN_HASHES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern + r'\S+'), pattern)
    for pattern in PATTERN_HASHES.keys()
)


@dataclass
class HashMatch:
    """Represents a hash algorithm match.

    Attributes:
        algorithm: Name of the hash algorithm
        confidence: Confidence score (0.0-1.0)
        length: Length of the hash string
        charset: Character set used (hex, base64, etc.)
        pattern: Pattern that matched (if any)
        hashcat_mode: Hashcat mode number (if available)
        john_format: John the Ripper format (if available)
    """
    algorithm: str
    confidence: float
    length: int
    charset: str
    pattern: Optional[str]
    hashcat_mode: Optional[int]
    john_format: Optional[str]


@dataclass
class HashFinding:
    """Represents a hash found in text.

    Attributes:
        position: Position in the text
        hash_string: The hash string found
        matches: List of possible algorithm matches
        context: Context around the hash (20 chars before/after)
    """
    position: int
    hash_string: str
    matches: List[HashMatch]
    context: str


@dataclass
class HashConfig:
    """Configuration for hash identification.

    Attributes:
        min_confidence: Minimum confidence threshold
        top_k_results: Number of top results to return
        detect_salted: Whether to detect salted hashes
        batch_size: Batch size for processing
    """
    min_confidence: float = 0.3
    top_k_results: int = 3
    detect_salted: bool = True
    batch_size: int = 1000


class HashIdentifier:
    """Identifies hash algorithms from hash strings.

    Supports 300+ hash algorithms with pattern, length, and charset matching.
    Integrates with hashcat and John the Ripper.

    Example:
        identifier = HashIdentifier()
        matches = await identifier.identify("5d41402abc4b2a76b9719d911017c592")
        for match in matches:
            print(f"{match.algorithm}: {match.confidence}")
    """

    def __init__(self, config: Optional[HashConfig] = None):
        """Initialize the hash identifier.

        Args:
            config: Optional configuration object
        """
        self.config = config or HashConfig()
        self._stats: Dict[str, int] = {
            'hashes_processed': 0,
            'hashes_identified': 0,
            'pattern_matches': 0,
            'length_matches': 0,
            'charset_matches': 0,
        }

    def _detect_charset(self, hash_string: str) -> str:
        """Detect the character set of a hash string.

        Args:
            hash_string: Hash string to analyze

        Returns:
            Character set type (hex, base64, alphanumeric, mixed)
        """
        if HEX_CHARSET.match(hash_string):
            return 'hex'
        elif BASE64_CHARSET.match(hash_string):
            return 'base64'
        elif ALPHANUM_CHARSET.match(hash_string):
            return 'alphanumeric'
        else:
            return 'mixed'

    def _match_by_pattern(self, hash_string: str) -> List[Tuple[str, str]]:
        """Match hash by pattern (e.g., $1$, $2a$).

        Args:
            hash_string: Hash string

        Returns:
            List of (algorithm, pattern) tuples
        """
        matches = []
        for compiled_re, algo, original_pattern in _COMPILED_PATTERN_HASHES:
            if compiled_re.match(hash_string):
                matches.append((algo, original_pattern))
                self._stats['pattern_matches'] += 1
        return matches

    def _match_by_length(self, hash_string: str) -> List[str]:
        """Match hash by length.

        Args:
            hash_string: Hash string

        Returns:
            List of matching algorithms
        """
        length = len(hash_string)
        matches = LENGTH_HASHES.get(length, [])
        if matches:
            self._stats['length_matches'] += len(matches)
        return matches

    def _match_by_charset(self, hash_string: str) -> List[str]:
        """Match hash by charset.

        Args:
            hash_string: Hash string

        Returns:
            List of matching algorithms
        """
        charset = self._detect_charset(hash_string)
        matches = []

        if charset == 'hex':
            # Most hashes use hex
            matches.extend(['MD5', 'SHA1', 'SHA256', 'SHA512', 'NTLM', 'MySQL5'])
        elif charset == 'base64':
            # bcrypt, scrypt, PBKDF2
            matches.extend(['bcrypt', 'scrypt', 'PBKDF2-SHA256', 'SSHA'])

        if matches:
            self._stats['charset_matches'] += 1

        return matches

    def _extract_salt(self, hash_string: str) -> Tuple[str, Optional[str]]:
        """Extract salt from hash:salt or salt:hash format.

        Args:
            hash_string: Hash string potentially containing salt

        Returns:
            Tuple of (hash_part, salt_part)
        """
        if not self.config.detect_salted:
            return hash_string, None

        # Check for hash:salt format
        if ':' in hash_string:
            parts = hash_string.rsplit(':', 1)
            if len(parts) == 2:
                # Assume first part is hash if it looks like one
                if len(parts[0]) > len(parts[1]):
                    return parts[0], parts[1]
                else:
                    return parts[1], parts[0]

        return hash_string, None

    def _get_hashcat_mode(self, algorithm: str) -> Optional[int]:
        """Get hashcat mode for algorithm.

        Args:
            algorithm: Algorithm name

        Returns:
            Hashcat mode number or None
        """
        return HASHCAT_MODES.get(algorithm)

    def _get_john_format(self, algorithm: str) -> Optional[str]:
        """Get John the Ripper format for algorithm.

        Args:
            algorithm: Algorithm name

        Returns:
            John format string or None
        """
        return JOHN_FORMATS.get(algorithm)

    async def identify(self, hash_string: str) -> List[HashMatch]:
        """Identify hash algorithm from hash string.

        Args:
            hash_string: Hash string to identify

        Returns:
            List of probable hash algorithms with confidence scores
        """
        hash_string = hash_string.strip()
        self._stats['hashes_processed'] += 1

        if not hash_string:
            return []

        # Extract salt if present
        hash_part, salt = self._extract_salt(hash_string)

        matches: List[HashMatch] = []
        seen_algorithms: Set[str] = set()

        # Pattern-based matching (highest priority)
        pattern_matches = self._match_by_pattern(hash_part)
        for algo, pattern in pattern_matches:
            if algo not in seen_algorithms:
                seen_algorithms.add(algo)
                matches.append(HashMatch(
                    algorithm=algo,
                    confidence=0.9,
                    length=len(hash_part),
                    charset=self._detect_charset(hash_part),
                    pattern=pattern,
                    hashcat_mode=self._get_hashcat_mode(algo),
                    john_format=self._get_john_format(algo)
                ))

        # Length-based matching
        length_matches = self._match_by_length(hash_part)
        for algo in length_matches:
            if algo not in seen_algorithms:
                seen_algorithms.add(algo)
                matches.append(HashMatch(
                    algorithm=algo,
                    confidence=0.6,
                    length=len(hash_part),
                    charset=self._detect_charset(hash_part),
                    pattern=None,
                    hashcat_mode=self._get_hashcat_mode(algo),
                    john_format=self._get_john_format(algo)
                ))

        # Charset-based matching (lowest priority)
        charset_matches = self._match_by_charset(hash_part)
        for algo in charset_matches:
            if algo not in seen_algorithms:
                seen_algorithms.add(algo)
                matches.append(HashMatch(
                    algorithm=algo,
                    confidence=0.3,
                    length=len(hash_part),
                    charset=self._detect_charset(hash_part),
                    pattern=None,
                    hashcat_mode=self._get_hashcat_mode(algo),
                    john_format=self._get_john_format(algo)
                ))

        # Filter by confidence
        matches = [m for m in matches if m.confidence >= self.config.min_confidence]

        # Sort by confidence and return top K
        matches.sort(key=lambda m: m.confidence, reverse=True)
        result = matches[:self.config.top_k_results]

        if result:
            self._stats['hashes_identified'] += 1

        return result

    async def identify_batch(
        self,
        hashes: List[str]
    ) -> Dict[str, List[HashMatch]]:
        """Identify multiple hashes in batch.

        Args:
            hashes: List of hash strings

        Returns:
            Dictionary mapping hash strings to matches
        """
        results: Dict[str, List[HashMatch]] = {}

        for i in range(0, len(hashes), self.config.batch_size):
            batch = hashes[i:i + self.config.batch_size]
            for hash_string in batch:
                matches = await self.identify(hash_string)
                results[hash_string] = matches

        return results

    async def identify_in_file(self, file_path: str) -> List[HashFinding]:
        """Scan file for hash patterns.

        Args:
            file_path: Path to file to scan

        Returns:
            List of hash findings
        """
        findings: List[HashFinding] = []

        path = Path(file_path)
        if not path.exists():
            logger.error(f"File not found: {file_path}")
            return findings

        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            # Find potential hashes (hex strings of specific lengths)
            for match in _HEX_HASH_SCAN_RE.finditer(content):
                hash_string = match.group(0)
                matches = await self.identify(hash_string)

                if matches:
                    # Get context (20 chars before/after)
                    start = max(0, match.start() - 20)
                    end = min(len(content), match.end() + 20)
                    context = content[start:end]

                    findings.append(HashFinding(
                        position=match.start(),
                        hash_string=hash_string,
                        matches=matches,
                        context=context
                    ))

            # Also look for pattern-based hashes (bcrypt, etc.)
            for compiled_re, _ in _COMPILED_SCAN_PATTERN_HASHES:
                for match in compiled_re.finditer(content):
                    hash_string = match.group(0)
                    matches = await self.identify(hash_string)

                    if matches:
                        start = max(0, match.start() - 20)
                        end = min(len(content), match.end() + 20)
                        context = content[start:end]

                        findings.append(HashFinding(
                            position=match.start(),
                            hash_string=hash_string,
                            matches=matches,
                            context=context
                        ))

        except Exception as e:
            logger.error(f"Error processing file {file_path}: {e}")

        return findings

    def get_stats(self) -> Dict[str, int]:
        """Get identification statistics.

        Returns:
            Dictionary of statistics
        """
        return self._stats.copy()

    def reset_stats(self) -> None:
        """Reset statistics."""
        for key in self._stats:
            self._stats[key] = 0


# Factory function
def create_hash_identifier(config: Optional[HashConfig] = None) -> HashIdentifier:
    """Create a configured HashIdentifier instance.

    Args:
        config: Optional configuration

    Returns:
        Configured HashIdentifier instance
    """
    return HashIdentifier(config)


# Convenience function
async def identify_hash(hash_string: str, config: Optional[HashConfig] = None):
    """Convenience function to identify a hash."""
    identifier = create_hash_identifier(config)
    return await identifier.identify(hash_string)
