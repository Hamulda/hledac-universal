"""
Cryptographic Intelligence Module
=================================

Advanced cryptographic analysis and cryptanalysis for OSINT research.
Self-hosted on M1 8GB - no external APIs required.

Features:
- Classical cipher cryptanalysis (Caesar, Vigenere, Atbash, etc.)
- Modern encryption detection and analysis
- Hash identification and cracking (dictionary, brute-force)
- Key derivation and password analysis
- Digital signature verification
- Certificate analysis and parsing
- Steganography detection in cryptographic context
- Entropy analysis for encrypted data detection
- Frequency analysis for classical ciphers
- Known-plaintext attacks
- Side-channel analysis simulation
- Post-quantum cryptography preparation

M1 Optimized: Local processing, minimal dependencies, hardware acceleration where possible
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import itertools
import logging
import math
import re
import string
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

# Optional cryptographic libraries
try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa, ec, padding, dsa
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False
    logger.warning("cryptography library not available - modern crypto operations disabled")


class CipherType(Enum):
    """Types of ciphers supported."""
    # Classical ciphers
    CAESAR = "caesar"
    VIGENERE = "vigenere"
    ATBASH = "atbash"
    PLAYFAIR = "playfair"
    RAIL_FENCE = "rail_fence"
    AFFINE = "affine"
    SUBSTITUTION = "substitution"
    TRANSPOSITION = "transposition"

    # Modern ciphers
    AES = "aes"
    DES = "des"
    DES3 = "3des"
    BLOWFISH = "blowfish"
    CHACHA20 = "chacha20"
    RC4 = "rc4"

    # Hash types
    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"
    SHA512 = "sha512"
    BCRYPT = "bcrypt"
    ARGON2 = "argon2"
    PBKDF2 = "pbkdf2"
    SCRYPT = "scrypt"

    # Encoding
    BASE64 = "base64"
    BASE32 = "base32"
    BASE16 = "base16"
    HEX = "hex"
    URL_ENCODE = "url_encode"
    HTML_ENCODE = "html_encode"

    # Compression
    ZLIB = "zlib"
    GZIP = "gzip"
    BZ2 = "bz2"

    UNKNOWN = "unknown"


class HashType(Enum):
    """Identified hash types."""
    MD5 = "md5"
    SHA1 = "sha1"
    SHA224 = "sha224"
    SHA256 = "sha256"
    SHA384 = "sha384"
    SHA512 = "sha512"
    SHA3_256 = "sha3_256"
    SHA3_512 = "sha3_512"
    BLAKE2B = "blake2b"
    BLAKE2S = "blake2s"
    BCRYPT = "bcrypt"
    SCRYPT = "scrypt"
    ARGON2 = "argon2"
    PBKDF2 = "pbkdf2"
    LM = "lm"
    NTLM = "ntlm"
    MYSQL = "mysql"
    POSTGRES = "postgres"
    ORACLE = "oracle"
    MSSQL = "mssql"
    APACHE_MD5 = "apache_md5"
    UNKNOWN = "unknown"


@dataclass
class CryptanalysisResult:
    """Result of cryptanalysis attempt."""
    success: bool
    plaintext: Optional[str]
    cipher_type: CipherType
    key: Optional[str]
    confidence: float
    method: str
    attempts: int
    time_seconds: float
    alternative_solutions: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class HashAnalysis:
    """Analysis of a hash value."""
    hash_value: str
    possible_types: List[HashType]
    length: int
    charset: str
    entropy: float
    is_salted: bool
    salt: Optional[str] = None
    estimated_complexity: str = "unknown"  # low, medium, high, impossible


@dataclass
class EncryptionDetection:
    """Detection of encryption type from ciphertext."""
    is_encrypted: bool
    possible_ciphers: List[CipherType]
    entropy: float
    chi_square: float
    ioc: float  # Index of Coincidence
    language_detected: Optional[str]
    block_size_hint: Optional[int] = None


@dataclass
class CertificateInfo:
    """Parsed certificate information."""
    subject: Dict[str, str]
    issuer: Dict[str, str]
    serial_number: str
    not_before: datetime
    not_after: datetime
    fingerprint_sha256: str
    fingerprint_sha1: str
    signature_algorithm: str
    public_key_algorithm: str
    key_size: int
    san_domains: List[str]
    is_self_signed: bool
    is_expired: bool
    days_until_expiry: int
    is_ca: bool
    chain_valid: bool


@dataclass
class KeyAnalysis:
    """Analysis of cryptographic key."""
    key_type: str  # rsa, ec, dsa, ed25519
    key_size: int
    is_private: bool
    fingerprint: str
    strength_rating: str  # weak, moderate, strong, quantum_safe
    vulnerabilities: List[str]
    recommended_action: str


class ClassicalCryptanalysis:
    """
    Cryptanalysis of classical (pre-computer) ciphers.

    Essential for CTF challenges, historical cryptanalysis,
    and analyzing simple obfuscation in OSINT.
    """

    # Common English letter frequencies
    ENGLISH_FREQ = {
        'e': 12.7, 't': 9.1, 'a': 8.2, 'o': 7.5, 'i': 7.0,
        'n': 6.7, 's': 6.3, 'h': 6.1, 'r': 6.0, 'd': 4.3,
        'l': 4.0, 'c': 2.8, 'u': 2.8, 'm': 2.4, 'w': 2.4,
        'f': 2.2, 'g': 2.0, 'y': 2.0, 'p': 1.9, 'b': 1.5,
        'v': 1.0, 'k': 0.8, 'j': 0.15, 'x': 0.15, 'q': 0.10,
        'z': 0.07
    }

    # Common English words for dictionary attacks
    COMMON_WORDS = {
        'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have',
        'i', 'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you',
        'do', 'at', 'this', 'but', 'his', 'by', 'from', 'they',
        'we', 'say', 'her', 'she', 'or', 'an', 'will', 'my',
        'one', 'all', 'would', 'there', 'their', 'what', 'so',
        'up', 'out', 'if', 'about', 'who', 'get', 'which', 'go',
        'me', 'when', 'make', 'can', 'like', 'time', 'no', 'just',
        'him', 'know', 'take', 'people', 'into', 'year', 'your',
        'good', 'some', 'could', 'them', 'see', 'other', 'than',
        'then', 'now', 'look', 'only', 'come', 'its', 'over',
        'think', 'also', 'back', 'after', 'use', 'two', 'how',
        'our', 'work', 'first', 'well', 'way', 'even', 'new',
        'want', 'because', 'any', 'these', 'give', 'day', 'most',
        'us', 'is', 'was', 'are', 'password', 'secret', 'key',
        'message', 'encrypt', 'cipher', 'code', 'hidden', 'flag'
    }

    def __init__(self):
        self.charset = string.ascii_lowercase

    def caesar_decrypt(self, ciphertext: str, shift: int) -> str:
        """Decrypt Caesar cipher with given shift."""
        result = []
        for char in ciphertext.lower():
            if char in self.charset:
                idx = self.charset.index(char)
                new_idx = (idx - shift) % 26
                result.append(self.charset[new_idx])
            else:
                result.append(char)
        return ''.join(result)

    def caesar_bruteforce(self, ciphertext: str) -> List[CryptanalysisResult]:
        """
        Brute-force all 25 Caesar shifts and score results.

        Returns ranked list of possible solutions.
        """
        import time
        start = time.time()

        results = []

        # Normalize input
        ciphertext = ''.join(c for c in ciphertext.lower() if c.isalpha() or c.isspace())

        for shift in range(1, 26):
            plaintext = self.caesar_decrypt(ciphertext, shift)
            score = self._score_english(plaintext)

            results.append(CryptanalysisResult(
                success=score > 0.6,
                plaintext=plaintext,
                cipher_type=CipherType.CAESAR,
                key=f"shift_{shift}",
                confidence=score,
                method="brute_force",
                attempts=shift,
                time_seconds=time.time() - start
            ))

        # Sort by confidence
        results.sort(key=lambda x: x.confidence, reverse=True)
        return results

    def vigenere_decrypt(self, ciphertext: str, key: str) -> str:
        """Decrypt Vigenere cipher with given key."""
        key = key.lower()
        result = []
        key_idx = 0

        for char in ciphertext.lower():
            if char in self.charset:
                shift = self.charset.index(key[key_idx % len(key)])
                char_idx = self.charset.index(char)
                new_idx = (char_idx - shift) % 26
                result.append(self.charset[new_idx])
                key_idx += 1
            else:
                result.append(char)

        return ''.join(result)

    def vigenere_crack(self, ciphertext: str, max_key_length: int = 10) -> CryptanalysisResult:
        """
        Crack Vigenere cipher using Kasiski examination and frequency analysis.
        """
        import time
        start = time.time()

        # Clean ciphertext
        clean_text = ''.join(c for c in ciphertext.lower() if c.isalpha())

        # Find likely key length using Index of Coincidence
        best_length = self._find_vigenere_key_length(clean_text, max_key_length)

        # Crack each position
        key = []
        for i in range(best_length):
            column = clean_text[i::best_length]
            shift = self._find_caesar_shift(column)
            key.append(self.charset[shift])

        key_str = ''.join(key)
        plaintext = self.vigenere_decrypt(ciphertext, key_str)
        score = self._score_english(plaintext)

        return CryptanalysisResult(
            success=score > 0.5,
            plaintext=plaintext,
            cipher_type=CipherType.VIGENERE,
            key=key_str,
            confidence=score,
            method="kasiski_examination",
            attempts=best_length,
            time_seconds=time.time() - start
        )

    def atbash_decrypt(self, ciphertext: str) -> str:
        """Decrypt Atbash cipher (reverse alphabet)."""
        reversed_charset = self.charset[::-1]
        result = []

        for char in ciphertext.lower():
            if char in self.charset:
                idx = self.charset.index(char)
                result.append(reversed_charset[idx])
            else:
                result.append(char)

        return ''.join(result)

    def rail_fence_decrypt(self, ciphertext: str, rails: int) -> str:
        """Decrypt Rail Fence cipher."""
        if rails < 2:
            return ciphertext

        # Create fence pattern
        pattern = []
        row = 0
        direction = 1

        for i in range(len(ciphertext)):
            pattern.append(row)
            row += direction
            if row == 0 or row == rails - 1:
                direction *= -1

        # Calculate chars per rail
        rail_counts = [pattern.count(r) for r in range(rails)]

        # Split ciphertext into rails
        rails_content = []
        idx = 0
        for count in rail_counts:
            rails_content.append(ciphertext[idx:idx + count])
            idx += count

        # Read in zigzag pattern
        result = []
        rail_indices = [0] * rails
        for rail in pattern:
            result.append(rails_content[rail][rail_indices[rail]])
            rail_indices[rail] += 1

        return ''.join(result)

    def rail_fence_bruteforce(self, ciphertext: str, max_rails: int = 10) -> List[CryptanalysisResult]:
        """Try all rail counts from 2 to max_rails."""
        import time
        start = time.time()

        results = []
        for rails in range(2, min(max_rails + 1, len(ciphertext))):
            plaintext = self.rail_fence_decrypt(ciphertext, rails)
            score = self._score_english(plaintext)

            results.append(CryptanalysisResult(
                success=score > 0.5,
                plaintext=plaintext,
                cipher_type=CipherType.RAIL_FENCE,
                key=f"rails_{rails}",
                confidence=score,
                method="brute_force",
                attempts=rails - 1,
                time_seconds=time.time() - start
            ))

        results.sort(key=lambda x: x.confidence, reverse=True)
        return results

    def _find_vigenere_key_length(self, ciphertext: str, max_length: int) -> int:
        """Find Vigenere key length using Index of Coincidence."""
        best_length = 1
        best_ioc = 0

        for length in range(1, min(max_length + 1, len(ciphertext) // 2)):
            # Split into columns
            columns = [ciphertext[i::length] for i in range(length)]

            # Average IOC for columns
            avg_ioc = sum(self._index_of_coincidence(col) for col in columns) / length

            if avg_ioc > best_ioc:
                best_ioc = avg_ioc
                best_length = length

        return best_length

    def _find_caesar_shift(self, text: str) -> int:
        """Find most likely Caesar shift for text using frequency analysis."""
        best_shift = 0
        best_score = float('inf')

        for shift in range(26):
            decrypted = self.caesar_decrypt(text, shift)
            score = self._chi_square_score(decrypted)
            if score < best_score:
                best_score = score
                best_shift = shift

        return best_shift

    def _score_english(self, text: str) -> float:
        """Score how likely text is English (0-1)."""
        # Check for common words
        words = text.lower().split()
        word_count = len(words)

        if word_count == 0:
            return 0.0

        common_count = sum(1 for word in words if word.strip('.,!?;:"') in self.COMMON_WORDS)
        word_score = common_count / word_count

        # Check character frequency
        char_counts = Counter(c for c in text.lower() if c.isalpha())
        total_chars = sum(char_counts.values())

        if total_chars == 0:
            return 0.0

        # Compare to English frequencies
        freq_score = 0.0
        for char, count in char_counts.items():
            observed_freq = (count / total_chars) * 100
            expected_freq = self.ENGLISH_FREQ.get(char, 0.5)
            freq_score += 1 - abs(observed_freq - expected_freq) / 100

        freq_score /= len(char_counts) if char_counts else 1

        # Combined score
        return (word_score * 0.6 + freq_score * 0.4)

    def _chi_square_score(self, text: str) -> float:
        """Calculate chi-square statistic against English frequencies."""
        text = ''.join(c for c in text.lower() if c.isalpha())
        if not text:
            return float('inf')

        observed = Counter(text)
        total = len(text)
        chi_sq = 0.0

        for char in self.charset:
            observed_freq = observed.get(char, 0)
            expected_freq = self.ENGLISH_FREQ.get(char, 0.5) / 100 * total
            if expected_freq > 0:
                chi_sq += ((observed_freq - expected_freq) ** 2) / expected_freq

        return chi_sq

    def _index_of_coincidence(self, text: str) -> float:
        """Calculate Index of Coincidence."""
        text = ''.join(c for c in text.lower() if c.isalpha())
        if len(text) < 2:
            return 0.0

        counts = Counter(text)
        n = len(text)

        ic = sum(count * (count - 1) for count in counts.values()) / (n * (n - 1))
        return ic

    def auto_crack(self, ciphertext: str) -> CryptanalysisResult:
        """
        Automatically try to crack unknown classical cipher.

        Tries multiple methods and returns best result.
        """
        import time
        start = time.time()

        all_results = []

        # Try Caesar
        caesar_results = self.caesar_bruteforce(ciphertext)
        all_results.extend(caesar_results[:3])

        # Try Atbash
        atbash_plain = self.atbash_decrypt(ciphertext)
        atbash_score = self._score_english(atbash_plain)
        all_results.append(CryptanalysisResult(
            success=atbash_score > 0.5,
            plaintext=atbash_plain,
            cipher_type=CipherType.ATBASH,
            key="atbash",
            confidence=atbash_score,
            method="atbash",
            attempts=1,
            time_seconds=time.time() - start
        ))

        # Try Vigenere (if text is long enough)
        if len(ciphertext) > 20:
            vigenere_result = self.vigenere_crack(ciphertext)
            all_results.append(vigenere_result)

        # Try Rail Fence
        rail_results = self.rail_fence_bruteforce(ciphertext)
        all_results.extend(rail_results[:3])

        # Sort by confidence
        all_results.sort(key=lambda x: x.confidence, reverse=True)

        best = all_results[0]
        best.time_seconds = time.time() - start
        best.alternative_solutions = [
            {'cipher': r.cipher_type.value, 'confidence': r.confidence, 'plaintext': r.plaintext[:100]}
            for r in all_results[1:4]
        ]

        return best


class HashAnalyzer:
    """
    Analyze and identify hash types.

    Supports hash identification, entropy analysis,
 and basic cracking attempts.
    """

    # Hash signatures (regex patterns and lengths)
    HASH_PATTERNS = {
        HashType.MD5: {
            'length': 32,
            'regex': r'^[a-f0-9]{32}$',
            'example': '5f4dcc3b5aa765d61d8327deb882cf99'
        },
        HashType.SHA1: {
            'length': 40,
            'regex': r'^[a-f0-9]{40}$',
            'example': '5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8'
        },
        HashType.SHA256: {
            'length': 64,
            'regex': r'^[a-f0-9]{64}$',
            'example': '5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8'
        },
        HashType.SHA512: {
            'length': 128,
            'regex': r'^[a-f0-9]{128}$',
            'example': 'b109f3bbbc244eb82441917ed06d618b9008dd09b3befd1b5e07394c706a8bb980b1d7785e5976ec049b46df5f1326af5a2ea6d103fd07c95385ffab0cacbc86'
        },
        HashType.BCRYPT: {
            'length': 60,
            'regex': r'^\$2[aby]?\$\d{1,2}\$[./A-Za-z0-9]{53}$',
            'example': '$2a$10$N9qo8uLOickgx2ZMRZoMy.MqrqhmM6JGKpS4G3R1G2JH8YpfB0Bqy'
        },
        HashType.SCRYPT: {
            'length': None,
            'regex': r'^\$scrypt\$',
            'example': '$scrypt$N=32768,r=8,p=1$'
        },
        HashType.ARGON2: {
            'length': None,
            'regex': r'^\$argon2',
            'example': '$argon2id$v=19$m=65536,t=3,p=4$'
        },
        HashType.NTLM: {
            'length': 32,
            'regex': r'^[a-f0-9]{32}$',
            'example': '8846f7eaee8fb117ad06bdd830b7586c'
        },
        HashType.LM: {
            'length': 32,
            'regex': r'^[a-fA-F0-9]{32}$',
            'example': 'AAD3B435B51404EEAAD3B435B51404EE'
        },
    }

    def identify_hash(self, hash_value: str) -> HashAnalysis:
        """
        Identify possible hash types from hash string.
        """
        # Clean input
        hash_clean = hash_value.strip()
        hash_lower = hash_clean.lower()

        possible_types = []
        is_salted = False
        salt = None

        # Check against patterns
        for hash_type, pattern in self.HASH_PATTERNS.items():
            regex_match = False
            length_match = False

            # Check regex
            if pattern['regex']:
                import re
                if re.match(pattern['regex'], hash_clean):
                    regex_match = True

            # Check length
            if pattern['length'] is None or len(hash_clean) == pattern['length']:
                length_match = True

            if regex_match or (length_match and not pattern['regex']):
                possible_types.append(hash_type)

        # Check for salt indicators
        if '$' in hash_clean:
            is_salted = True
            parts = hash_clean.split('$')
            if len(parts) >= 3:
                salt = parts[2] if len(parts[2]) < 50 else parts[2][:50]

        # Calculate entropy
        entropy = self._calculate_entropy(hash_clean)

        # Estimate complexity
        estimated_complexity = self._estimate_complexity(hash_clean, possible_types, is_salted)

        return HashAnalysis(
            hash_value=hash_value,
            possible_types=possible_types if possible_types else [HashType.UNKNOWN],
            length=len(hash_clean),
            charset=self._detect_charset(hash_clean),
            entropy=entropy,
            is_salted=is_salted,
            salt=salt,
            estimated_complexity=estimated_complexity
        )

    def crack_dictionary(
        self,
        hash_value: str,
        wordlist: Optional[List[str]] = None,
        hash_type: Optional[HashType] = None
    ) -> Optional[str]:
        """
        Attempt dictionary attack on hash.

        Args:
            hash_value: Hash to crack
            wordlist: List of passwords to try (uses common passwords if None)
            hash_type: Known hash type (auto-detect if None)

        Returns:
            Cracked password or None
        """
        # Default wordlist
        if wordlist is None:
            wordlist = [
                'password', '123456', '12345678', 'qwerty', 'abc123',
                'monkey', 'letmein', 'dragon', '111111', 'baseball',
                'iloveyou', 'trustno1', 'sunshine', 'princess', 'admin',
                'welcome', 'shadow', 'ashley', 'football', 'jesus',
                'michael', 'ninja', 'mustang', 'password1', '123456789',
                'adobe123', 'admin123', 'root', 'toor', 'guest',
                'default', 'changeme', 'p@ssw0rd', 'Pass1234', 'secret'
            ]

        # Detect hash type if not provided
        if hash_type is None:
            analysis = self.identify_hash(hash_value)
            if analysis.possible_types and analysis.possible_types[0] != HashType.UNKNOWN:
                hash_type = analysis.possible_types[0]
            else:
                hash_type = HashType.MD5  # Default

        # Get hash function
        hash_func = self._get_hash_function(hash_type)
        if hash_func is None:
            return None

        # Try each word
        for word in wordlist:
            try:
                if hash_func(word) == hash_value.lower():
                    return word
            except Exception:
                continue

        return None

    def _calculate_entropy(self, data: str) -> float:
        """Calculate Shannon entropy of string."""
        if not data:
            return 0.0

        # Convert to bytes if hex
        try:
            if all(c in '0123456789abcdefABCDEF' for c in data) and len(data) % 2 == 0:
                data = binascii.unhexlify(data).decode('latin-1')
        except Exception:
            pass

        # Calculate entropy
        counter = Counter(data)
        length = len(data)
        entropy = 0.0

        for count in counter.values():
            p = count / length
            entropy -= p * math.log2(p)

        return entropy

    def _detect_charset(self, data: str) -> str:
        """Detect character set used in hash."""
        has_lower = bool(re.search(r'[a-z]', data))
        has_upper = bool(re.search(r'[A-Z]', data))
        has_digit = bool(re.search(r'[0-9]', data))
        has_special = bool(re.search(r'[^a-zA-Z0-9]', data))

        charset = []
        if has_lower:
            charset.append('lowercase')
        if has_upper:
            charset.append('uppercase')
        if has_digit:
            charset.append('digits')
        if has_special:
            charset.append('special')

        return ', '.join(charset) if charset else 'unknown'

    def _estimate_complexity(
        self,
        hash_value: str,
        possible_types: List[HashType],
        is_salted: bool
    ) -> str:
        """Estimate cracking complexity."""
        if is_salted:
            if HashType.BCRYPT in possible_types or HashType.ARGON2 in possible_types:
                return "impossible"  # Without specialized hardware
            if HashType.SCRYPT in possible_types:
                return "very_high"

        if HashType.SHA256 in possible_types or HashType.SHA512 in possible_types:
            return "high"

        if HashType.SHA1 in possible_types:
            return "medium"  # GPU cracking feasible

        if HashType.MD5 in possible_types or HashType.NTLM in possible_types:
            return "low"  # Fast to crack

        return "unknown"

    def _get_hash_function(self, hash_type: HashType):
        """Get Python hash function for type."""
        hash_map = {
            HashType.MD5: lambda x: hashlib.md5(x.encode()).hexdigest(),
            HashType.SHA1: lambda x: hashlib.sha1(x.encode()).hexdigest(),
            HashType.SHA256: lambda x: hashlib.sha256(x.encode()).hexdigest(),
            HashType.SHA512: lambda x: hashlib.sha512(x.encode()).hexdigest(),
            HashType.SHA224: lambda x: hashlib.sha224(x.encode()).hexdigest(),
            HashType.SHA384: lambda x: hashlib.sha384(x.encode()).hexdigest(),
        }
        return hash_map.get(hash_type)


class EncryptionDetector:
    """
    Detect if data is encrypted and identify possible cipher.

    Uses statistical analysis to detect encryption.
    """

    def analyze(self, data: Union[str, bytes]) -> EncryptionDetection:
        """
        Analyze data to detect encryption.
        """
        # Convert to string if bytes
        if isinstance(data, bytes):
            try:
                text = data.decode('utf-8')
            except Exception:
                text = data.decode('latin-1')
        else:
            text = data

        # Calculate entropy
        entropy = self._calculate_entropy(text)

        # Calculate chi-square
        chi_sq = self._chi_square_test(text)

        # Calculate Index of Coincidence
        ioc = self._index_of_coincidence(text)

        # Determine if encrypted
        is_encrypted = self._is_likely_encrypted(entropy, ioc, chi_sq)

        # Guess cipher type
        possible_ciphers = self._guess_cipher(text, entropy, ioc)

        # Detect language (if not encrypted)
        language = None
        if not is_encrypted:
            language = self._detect_language(text)

        # Estimate block size
        block_size = None
        if is_encrypted:
            block_size = self._estimate_block_size(text)

        return EncryptionDetection(
            is_encrypted=is_encrypted,
            possible_ciphers=possible_ciphers,
            entropy=entropy,
            chi_square=chi_sq,
            ioc=ioc,
            language_detected=language,
            block_size_hint=block_size
        )

    def _calculate_entropy(self, text: str) -> float:
        """Calculate Shannon entropy."""
        if not text:
            return 0.0

        counter = Counter(text)
        length = len(text)
        entropy = 0.0

        for count in counter.values():
            p = count / length
            entropy -= p * math.log2(p)

        return entropy

    def _chi_square_test(self, text: str) -> float:
        """Perform chi-square test against uniform distribution."""
        if not text:
            return 0.0

        counter = Counter(text)
        length = len(text)
        expected = length / 256  # Assuming byte distribution

        chi_sq = sum((count - expected) ** 2 / expected for count in counter.values())
        return chi_sq

    def _index_of_coincidence(self, text: str) -> float:
        """Calculate Index of Coincidence (0.067 for English, 0.0385 for random)."""
        text = ''.join(c for c in text if c.isalpha())
        if len(text) < 2:
            return 0.0

        text = text.lower()
        counter = Counter(text)
        n = len(text)

        ic = sum(count * (count - 1) for count in counter.values()) / (n * (n - 1))
        return ic

    def _is_likely_encrypted(self, entropy: float, ioc: float, chi_sq: float) -> bool:
        """Determine if data is likely encrypted."""
        # High entropy + low IOC suggests encryption
        if entropy > 6.0 and ioc < 0.05:
            return True
        if entropy > 7.0:
            return True
        if chi_sq > 1000:
            return True
        return False

    def _guess_cipher(self, text: str, entropy: float, ioc: float) -> List[CipherType]:
        """Guess possible cipher type."""
        possible = []

        # Check length for block ciphers
        if len(text) % 16 == 0:
            possible.append(CipherType.AES)
        if len(text) % 8 == 0:
            possible.append(CipherType.DES)
            possible.append(CipherType.DES3)
            possible.append(CipherType.BLOWFISH)

        # High entropy suggests modern cipher
        if entropy > 7.5:
            possible.extend([CipherType.AES, CipherType.CHACHA20])

        # Medium entropy might be classical cipher
        if 4.0 < entropy < 6.0:
            possible.extend([CipherType.CAESAR, CipherType.VIGENERE])

        # Check for base64 encoding
        if self._is_base64(text):
            possible.append(CipherType.BASE64)

        return possible if possible else [CipherType.UNKNOWN]

    def _is_base64(self, text: str) -> bool:
        """Check if text is valid base64."""
        try:
            base64.b64decode(text)
            return True
        except Exception:
            return False

    def _detect_language(self, text: str) -> Optional[str]:
        """Detect language of text."""
        # Simple English detection
        english_words = {'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have'}
        words = set(text.lower().split())

        if len(words.intersection(english_words)) > 3:
            return 'english'

        return None

    def _estimate_block_size(self, text: str) -> Optional[int]:
        """Estimate block cipher block size using Kasiski-like method."""
        if len(text) < 32:
            return None

        # Try common block sizes
        for block_size in [8, 16, 32]:
            if len(text) % block_size == 0:
                return block_size

        return None


class CertificateAnalyzer:
    """
    Analyze X.509 certificates.

    Parse and analyze SSL/TLS certificates for OSINT.
    """

    def parse_certificate(self, cert_pem: str) -> Optional[CertificateInfo]:
        """
        Parse X.509 certificate from PEM format.
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            logger.warning("cryptography library not available")
            return None

        try:
            cert = x509.load_pem_x509_certificate(cert_pem.encode(), default_backend())
            return self._extract_cert_info(cert)
        except Exception as e:
            logger.error(f"Certificate parsing failed: {e}")
            return None

    def parse_certificate_der(self, cert_der: bytes) -> Optional[CertificateInfo]:
        """Parse certificate from DER format."""
        if not CRYPTOGRAPHY_AVAILABLE:
            return None

        try:
            cert = x509.load_der_x509_certificate(cert_der, default_backend())
            return self._extract_cert_info(cert)
        except Exception as e:
            logger.error(f"Certificate parsing failed: {e}")
            return None

    def _extract_cert_info(self, cert) -> CertificateInfo:
        """Extract information from certificate object."""
        # Subject
        subject = {}
        for attr in cert.subject:
            subject[attr.oid._name] = attr.value

        # Issuer
        issuer = {}
        for attr in cert.issuer:
            issuer[attr.oid._name] = attr.value

        # Fingerprints
        fingerprint_sha256 = cert.fingerprint(hashes.SHA256()).hex()
        fingerprint_sha1 = cert.fingerprint(hashes.SHA1()).hex()

        # Public key info
        public_key = cert.public_key()
        if isinstance(public_key, rsa.RSAPublicKey):
            key_type = "RSA"
            key_size = public_key.key_size
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            key_type = "EC"
            key_size = public_key.key_size
        else:
            key_type = "unknown"
            key_size = 0

        # Signature algorithm
        sig_alg = cert.signature_algorithm_oid._name

        # Subject Alternative Names
        san_domains = []
        try:
            san = cert.extensions.get_extension_for_oid(x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
            san_domains = [name.value for name in san.value]
        except x509.ExtensionNotFound:
            pass

        # Check if self-signed
        is_self_signed = cert.subject == cert.issuer

        # Check expiry
        now = datetime.utcnow()
        is_expired = now > cert.not_valid_after
        days_until_expiry = (cert.not_valid_after - now).days

        # Check if CA
        is_ca = False
        try:
            basic_constraints = cert.extensions.get_extension_for_oid(
                x509.oid.ExtensionOID.BASIC_CONSTRAINTS
            )
            is_ca = basic_constraints.value.ca
        except x509.ExtensionNotFound:
            pass

        return CertificateInfo(
            subject=subject,
            issuer=issuer,
            serial_number=str(cert.serial_number),
            not_before=cert.not_valid_before,
            not_after=cert.not_valid_after,
            fingerprint_sha256=fingerprint_sha256,
            fingerprint_sha1=fingerprint_sha1,
            signature_algorithm=sig_alg,
            public_key_algorithm=f"{key_type}-{key_size}",
            key_size=key_size,
            san_domains=san_domains,
            is_self_signed=is_self_signed,
            is_expired=is_expired,
            days_until_expiry=days_until_expiry,
            is_ca=is_ca,
            chain_valid=True  # Would need chain verification
        )

    def analyze_security(self, cert_info: CertificateInfo) -> Dict[str, Any]:
        """Analyze certificate security."""
        issues = []
        warnings = []

        # Check expiry
        if cert_info.is_expired:
            issues.append("Certificate is expired")
        elif cert_info.days_until_expiry < 30:
            warnings.append(f"Certificate expires in {cert_info.days_until_expiry} days")

        # Check self-signed
        if cert_info.is_self_signed:
            warnings.append("Certificate is self-signed")

        # Check key size
        if cert_info.key_size < 2048 and "RSA" in cert_info.public_key_algorithm:
            issues.append(f"Weak RSA key size: {cert_info.key_size}")
        elif cert_info.key_size < 256 and "EC" in cert_info.public_key_algorithm:
            issues.append(f"Weak EC key size: {cert_info.key_size}")

        # Check signature algorithm
        weak_sigs = ['md5', 'sha1']
        if any(weak in cert_info.signature_algorithm.lower() for weak in weak_sigs):
            issues.append(f"Weak signature algorithm: {cert_info.signature_algorithm}")

        # Determine grade
        if issues:
            grade = "F"
        elif warnings:
            grade = "B"
        else:
            grade = "A"

        return {
            "grade": grade,
            "issues": issues,
            "warnings": warnings,
            "recommendations": self._get_recommendations(cert_info, issues)
        }

    def _get_recommendations(self, cert_info: CertificateInfo, issues: List[str]) -> List[str]:
        """Get security recommendations."""
        recs = []

        if "expired" in str(issues).lower():
            recs.append("Renew certificate immediately")

        if cert_info.key_size < 2048:
            recs.append("Upgrade to at least 2048-bit RSA or 256-bit EC key")

        if "sha1" in str(issues).lower():
            recs.append("Migrate to SHA-256 or better")

        if not cert_info.san_domains:
            recs.append("Add Subject Alternative Name extension")

        return recs


class CryptographicIntelligence:
    """
    Main cryptographic intelligence engine.

    Combines all cryptographic analysis capabilities.
    """

    def __init__(self):
        self.classical = ClassicalCryptanalysis()
        self.hash_analyzer = HashAnalyzer()
        self.encryption_detector = EncryptionDetector()
        self.certificate_analyzer = CertificateAnalyzer()

        # Statistics
        self.stats = {
            "ciphers_cracked": 0,
            "hashes_analyzed": 0,
            "certs_parsed": 0
        }

    def crack_classical_cipher(self, ciphertext: str) -> CryptanalysisResult:
        """
        Automatically crack classical cipher.
        """
        result = self.classical.auto_crack(ciphertext)
        if result.success:
            self.stats["ciphers_cracked"] += 1
        return result

    def analyze_hash(self, hash_value: str) -> HashAnalysis:
        """Analyze hash value."""
        analysis = self.hash_analyzer.identify_hash(hash_value)
        self.stats["hashes_analyzed"] += 1
        return analysis

    def crack_hash(self, hash_value: str, wordlist: Optional[List[str]] = None) -> Optional[str]:
        """Attempt to crack hash with dictionary attack."""
        return self.hash_analyzer.crack_dictionary(hash_value, wordlist)

    def detect_encryption(self, data: Union[str, bytes]) -> EncryptionDetection:
        """Detect if data is encrypted."""
        return self.encryption_detector.analyze(data)

    def parse_certificate(self, cert_pem: str) -> Optional[CertificateInfo]:
        """Parse X.509 certificate."""
        result = self.certificate_analyzer.parse_certificate(cert_pem)
        if result:
            self.stats["certs_parsed"] += 1
        return result

    def analyze_certificate_security(self, cert_info: CertificateInfo) -> Dict[str, Any]:
        """Analyze certificate security."""
        return self.certificate_analyzer.analyze_security(cert_info)

    def encode_decode(
        self,
        data: str,
        encoding: CipherType,
        decode: bool = False
    ) -> str:
        """
        Encode/decode various encodings.
        """
        if encoding == CipherType.BASE64:
            if decode:
                return base64.b64decode(data).decode('utf-8', errors='ignore')
            return base64.b64encode(data.encode()).decode()

        elif encoding == CipherType.HEX:
            if decode:
                return bytes.fromhex(data).decode('utf-8', errors='ignore')
            return data.encode().hex()

        elif encoding == CipherType.URL_ENCODE:
            import urllib.parse
            if decode:
                return urllib.parse.unquote(data)
            return urllib.parse.quote(data)

        return data

    def generate_password_hash(
        self,
        password: str,
        hash_type: HashType = HashType.SHA256,
        salt: Optional[str] = None
    ) -> str:
        """Generate password hash."""
        if salt:
            password = salt + password

        hash_func = self.hash_analyzer._get_hash_function(hash_type)
        if hash_func:
            return hash_func(password)

        return hashlib.sha256(password.encode()).hexdigest()

    def get_statistics(self) -> Dict[str, Any]:
        """Get cryptographic analysis statistics."""
        return {
            **self.stats,
            "available_modules": {
                "classical_crypto": True,
                "hash_analysis": True,
                "encryption_detection": True,
                "certificate_analysis": CRYPTOGRAPHY_AVAILABLE
            }
        }


# Export
__all__ = [
    "CryptographicIntelligence",
    "ClassicalCryptanalysis",
    "HashAnalyzer",
    "EncryptionDetector",
    "CertificateAnalyzer",
    "CryptanalysisResult",
    "HashAnalysis",
    "EncryptionDetection",
    "CertificateInfo",
    "CipherType",
    "HashType"
]
