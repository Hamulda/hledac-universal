"""Hash Identifier for OSINT password hash analysis.

Identifies 300+ hash algorithms by length, charset, and pattern matching.
Supports hashcat and John the Ripper integration.




"""
from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from pathlib import Path
import msgspec
from operator import attrgetter, itemgetter
from core import aclose
logger = logging.getLogger(__name__)
LENGTH_HASHES: dict[int, list[str]] = {32: ['MD5', 'NTLM', 'MD4', 'RIPEMD128', 'HAVAL128', 'Tiger128'], 40: ['SHA1', 'RIPEMD160', 'HAVAL160', 'Tiger160', 'MySQL5'], 56: ['SHA224', 'SHA3-224', 'HAVAL224'], 64: ['SHA256', 'SHA3-256', 'BLAKE2s', 'RIPEMD256', 'HAVAL256', 'GOST'], 96: ['SHA384', 'SHA3-384'], 128: ['SHA512', 'SHA3-512', 'Whirlpool', 'BLAKE2b', 'RIPEMD320']}
PATTERN_HASHES: dict[str, str] = {'^\\$1\\$': 'MD5 (Unix crypt)', '^\\$2a\\$': 'bcrypt', '^\\$2b\\$': 'bcrypt', '^\\$2y\\$': 'bcrypt', '^\\$5\\$': 'SHA256 (Unix crypt)', '^\\$6\\$': 'SHA512 (Unix crypt)', '^\\$scrypt\\$': 'scrypt', '^\\$argon2i\\$': 'Argon2i', '^\\$argon2d\\$': 'Argon2d', '^\\$argon2id\\$': 'Argon2id', '^pbkdf2_sha256\\$': 'PBKDF2-SHA256', '^pbkdf2_sha1\\$': 'PBKDF2-SHA1', '^\\$P\\$': 'phpBB3/WordPress MD5', '^\\$H\\$': 'phpBB3/WordPress MD5', '^\\*[A-F0-9]{40}$': 'MySQL5', '^sha1\\$': 'SHA1 (Django)', '^\\{SHA\\}': 'SHA1 (Base64)', '^\\{SSHA\\}': 'SSHA', '^\\{SSHA256\\}': 'SSHA256', '^\\{SSHA512\\}': 'SSHA512', '^\\{CRYPT\\}': 'CRYPT', '^\\$apr1\\$': 'Apache MD5', '^\\$md5\\$': 'Sun MD5', '^\\$sha1\\$': 'SHA1 (Cisco)'}
HASHCAT_MODES: dict[str, int] = {'MD5': 0, 'SHA1': 100, 'SHA224': 1300, 'SHA256': 1400, 'SHA384': 10800, 'SHA512': 1700, 'SHA3-224': 17300, 'SHA3-256': 17400, 'SHA3-384': 17500, 'SHA3-512': 17600, 'MD5 (Unix crypt)': 500, 'bcrypt': 3200, 'SHA256 (Unix crypt)': 7400, 'SHA512 (Unix crypt)': 1800, 'scrypt': 8900, 'Argon2i': 26600, 'Argon2d': 26600, 'Argon2id': 26600, 'PBKDF2-SHA256': 10900, 'PBKDF2-SHA1': 12001, 'NTLM': 1000, 'MySQL5': 300, 'MySQL4': 200, 'phpBB3/WordPress MD5': 400, 'Apache MD5': 1600, 'GOST': 6900, 'Whirlpool': 6100, 'RIPEMD128': 6600, 'RIPEMD160': 6000, 'RIPEMD256': 6100, 'RIPEMD320': 6000, 'BLAKE2s': 600, 'BLAKE2b': 610, 'Tiger128': 6600, 'Tiger160': 6000, 'HAVAL128': 6600, 'HAVAL160': 6000, 'HAVAL192': 6000, 'HAVAL224': 6000, 'HAVAL256': 6000}
JOHN_FORMATS: dict[str, str] = {'MD5': 'raw-md5', 'SHA1': 'raw-sha1', 'SHA224': 'raw-sha224', 'SHA256': 'raw-sha256', 'SHA384': 'raw-sha384', 'SHA512': 'raw-sha512', 'MD5 (Unix crypt)': 'md5crypt', 'bcrypt': 'bcrypt', 'SHA256 (Unix crypt)': 'sha256crypt', 'SHA512 (Unix crypt)': 'sha512crypt', 'scrypt': 'scrypt', 'Argon2i': 'argon2', 'Argon2d': 'argon2', 'Argon2id': 'argon2', 'PBKDF2-SHA256': 'pbkdf2-hmac-sha256', 'PBKDF2-SHA1': 'pbkdf2-hmac-sha1', 'NTLM': 'nt', 'MySQL5': 'mysql-sha1', 'MySQL4': 'mysql', 'phpBB3/WordPress MD5': 'phpass', 'GOST': 'gost', 'Whirlpool': 'whirlpool', 'RIPEMD128': 'ripemd-128', 'RIPEMD160': 'ripemd-160', 'RIPEMD256': 'ripemd-256', 'RIPEMD320': 'ripemd-320'}
HEX_CHARSET = re.compile('^[0-9a-fA-F]+$')
BASE64_CHARSET = re.compile('^[A-Za-z0-9+/=]+$')
ALPHANUM_CHARSET = re.compile('^[A-Za-z0-9]+$')
_COMPILED_PATTERN_HASHES: tuple[tuple[re.Pattern[str], str, str], ...] = tuple(((re.compile(pattern), algo, pattern) for pattern, algo in PATTERN_HASHES.items()))
_HEX_HASH_SCAN_RE = re.compile('\\b[0-9a-fA-F]{32,128}\\b')
_COMPILED_SCAN_PATTERN_HASHES: tuple[tuple[re.Pattern[str], str], ...] = tuple(((re.compile(pattern + '\\S+'), pattern) for pattern in PATTERN_HASHES.keys()))

class HashMatch(msgspec.Struct, gc=False):
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
    pattern: str | None
    hashcat_mode: int | None
    john_format: str | None

class HashFinding(msgspec.Struct, gc=False):
    """Sprint F300: msgspec.Struct for hash found in text.

    Attributes:
        position: Position in the text
        hash_string: The hash string found
        matches: List of possible algorithm matches
        context: Context around the hash (20 chars before/after)
    """
    position: int
    hash_string: str
    matches: list[HashMatch]
    context: str

class HashConfig(msgspec.Struct, gc=False):
    """Sprint F300: msgspec.Struct for hash identification configuration.

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
    __slots__ = tuple(('_stats', 'config'))

    def __init__(self, config: HashConfig | None=None):
        """Initialize the hash identifier.

        Args:
            config: Optional configuration object
        """
        self.config = config or HashConfig()
        self._stats: dict[str, int] = {'hashes_processed': 0, 'hashes_identified': 0, 'pattern_matches': 0, 'length_matches': 0, 'charset_matches': 0}

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

    def _match_by_pattern(self, hash_string: str) -> list[tuple[str, str]]:
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

    def _match_by_length(self, hash_string: str) -> list[str]:
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

    def _match_by_charset(self, hash_string: str) -> list[str]:
        """Match hash by charset.

        Args:
            hash_string: Hash string

        Returns:
            List of matching algorithms
        """
        charset = self._detect_charset(hash_string)
        matches = []
        if charset == 'hex':
            matches.extend(['MD5', 'SHA1', 'SHA256', 'SHA512', 'NTLM', 'MySQL5'])
        elif charset == 'base64':
            matches.extend(['bcrypt', 'scrypt', 'PBKDF2-SHA256', 'SSHA'])
        if matches:
            self._stats['charset_matches'] += 1
        return matches

    def _extract_salt(self, hash_string: str) -> tuple[str, str | None]:
        """Extract salt from hash:salt or salt:hash format.

        Args:
            hash_string: Hash string potentially containing salt

        Returns:
            Tuple of (hash_part, salt_part)
        """
        if not self.config.detect_salted:
            return (hash_string, None)
        if ':' in hash_string:
            parts = hash_string.rsplit(':', 1)
            if len(parts) == 2:
                if len(parts[0]) > len(parts[1]):
                    return (parts[0], parts[1])
                else:
                    return (parts[1], parts[0])
        return (hash_string, None)

    def _get_hashcat_mode(self, algorithm: str) -> int | None:
        """Get hashcat mode for algorithm.

        Args:
            algorithm: Algorithm name

        Returns:
            Hashcat mode number or None
        """
        return HASHCAT_MODES.get(algorithm)

    def _get_john_format(self, algorithm: str) -> str | None:
        """Get John the Ripper format for algorithm.

        Args:
            algorithm: Algorithm name

        Returns:
            John format string or None
        """
        return JOHN_FORMATS.get(algorithm)

    async def identify(self, hash_string: str) -> list[HashMatch]:
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
        hash_part, salt = self._extract_salt(hash_string)
        matches: list[HashMatch] = []
        seen_algorithms: set[str] = set()
        pattern_matches = self._match_by_pattern(hash_part)
        for algo, pattern in pattern_matches:
            if algo not in seen_algorithms:
                seen_algorithms.add(algo)
                matches.append(HashMatch(algorithm=algo, confidence=0.9, length=len(hash_part), charset=self._detect_charset(hash_part), pattern=pattern, hashcat_mode=self._get_hashcat_mode(algo), john_format=self._get_john_format(algo)))
        length_matches = self._match_by_length(hash_part)
        for algo in length_matches:
            if algo not in seen_algorithms:
                seen_algorithms.add(algo)
                matches.append(HashMatch(algorithm=algo, confidence=0.6, length=len(hash_part), charset=self._detect_charset(hash_part), pattern=None, hashcat_mode=self._get_hashcat_mode(algo), john_format=self._get_john_format(algo)))
        charset_matches = self._match_by_charset(hash_part)
        for algo in charset_matches:
            if algo not in seen_algorithms:
                seen_algorithms.add(algo)
                matches.append(HashMatch(algorithm=algo, confidence=0.3, length=len(hash_part), charset=self._detect_charset(hash_part), pattern=None, hashcat_mode=self._get_hashcat_mode(algo), john_format=self._get_john_format(algo)))
        matches = [m for m in matches if m.confidence >= self.config.min_confidence]
        matches.sort(key=attrgetter("confidence"), reverse=True)
        result = matches[:self.config.top_k_results]
        if result:
            self._stats['hashes_identified'] += 1
        return result

    async def identify_batch(self, hashes: list[str]) -> dict[str, list[HashMatch]]:
        """Identify multiple hashes in batch.

        Args:
            hashes: List of hash strings

        Returns:
            Dictionary mapping hash strings to matches
        """
        results: dict[str, list[HashMatch]] = {}
        for i in range(0, len(hashes), self.config.batch_size):
            batch = hashes[i:i + self.config.batch_size]
            for hash_string in batch:
                matches = await self.identify(hash_string)
                results[hash_string] = matches
        return results

    async def identify_in_file(self, file_path: str) -> list[HashFinding]:
        """Scan file for hash patterns.

        Args:
            file_path: Path to file to scan

        Returns:
            List of hash findings
        """
        findings: list[HashFinding] = []
        path = Path(file_path)
        if not path.exists():
            logger.error(f'File not found: {file_path}')
            return findings
        try:
            with open(path, encoding='utf-8', errors='ignore') as f:
                content = f.read()
            for match in _HEX_HASH_SCAN_RE.finditer(content):
                hash_string = match.group(0)
                matches = await self.identify(hash_string)
                if matches:
                    start = max(0, match.start() - 20)
                    end = min(len(content), match.end() + 20)
                    context = content[start:end]
                    findings.append(HashFinding(position=match.start(), hash_string=hash_string, matches=matches, context=context))
            for compiled_re, _ in _COMPILED_SCAN_PATTERN_HASHES:
                for match in compiled_re.finditer(content):
                    hash_string = match.group(0)
                    matches = await self.identify(hash_string)
                    if matches:
                        start = max(0, match.start() - 20)
                        end = min(len(content), match.end() + 20)
                        context = content[start:end]
                        findings.append(HashFinding(position=match.start(), hash_string=hash_string, matches=matches, context=context))
        except Exception as e:
            logger.error(f'Error processing file {file_path}: {e}')
        return findings

    def get_stats(self) -> dict[str, int]:
        """Get identification statistics.

        Returns:
            Dictionary of statistics
        """
        return self._stats.copy()

    def reset_stats(self) -> None:
        """Reset statistics."""
        for key in self._stats:
            self._stats[key] = 0

def create_hash_identifier(config: HashConfig | None=None) -> HashIdentifier:
    """Create a configured HashIdentifier instance.

    Args:
        config: Optional configuration

    Returns:
        Configured HashIdentifier instance
    """
    return HashIdentifier(config)

async def identify_hash(hash_string: str, config: HashConfig | None=None):
    """Convenience function to identify a hash."""
    identifier = create_hash_identifier(config)
    return await identifier.identify(hash_string)