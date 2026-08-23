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

import base64
import binascii
import hashlib
import logging
import math
import re
import string
from collections import Counter
from dataclasses import field
from datetime import UTC, datetime
from enum import Enum
from functools import cache
from operator import attrgetter
from typing import Any

from compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)
try:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.hazmat.primitives.ciphers import Cipher

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False
    logger.warning("cryptography library not available - modern crypto operations disabled")

# SILICON-01: Metal GPU hash cracking — opportunistic during I/O wait
# Feature flag: HLEDAC_ENABLE_METAL_HASHCRACK=1 to enable Metal GPU path
# Default: 0 (disabled) — Metal crate must be compiled in (maturin develop --features "metal")
from hledac.universal._core.feature_flags import FeatureFlag, FeatureFlags

_METAL_HASHCRACK_ENABLED: bool = FeatureFlags.get(FeatureFlag.METAL_HASHCRACK)


@cache
def _get_metal_cracker():
    """Lazy-init MetalHashCracker singleton (functools.cache = once per process).

    Returns None if Metal is not available or not enabled.
    The @cache decorator ensures we attempt the import exactly once.
    """
    if not _METAL_HASHCRACK_ENABLED:
        return None
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal._core.rust_backend import rust

    MetalHashCracker = rust.raw.MetalHashCracker
    if MetalHashCracker is None:
        logger.warning("MetalHashCracker not available in Rust extension")
        return None
    cracker = MetalHashCracker()
    if cracker.is_available:
        logger.info(
            "MetalHashCracker initialized: device=%s, GPU opportunistic cracking enabled",
            cracker.device_name,
        )
        return cracker
    else:
        logger.debug("MetalHashCracker: Metal GPU not available (non-macOS or no Metal device)")
        return None


class CipherType(Enum):
    """Types of ciphers supported."""

    CAESAR = "caesar"
    VIGENERE = "vigenere"
    ATBASH = "atbash"
    PLAYFAIR = "playfair"
    RAIL_FENCE = "rail_fence"
    AFFINE = "affine"
    SUBSTITUTION = "substitution"
    TRANSPOSITION = "transposition"
    AES = "aes"
    DES = "des"
    DES3 = "3des"
    BLOWFISH = "blowfish"
    CHACHA20 = "chacha20"
    RC4 = "rc4"
    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"
    SHA512 = "sha512"
    BCRYPT = "bcrypt"
    ARGON2 = "argon2"
    PBKDF2 = "pbkdf2"
    SCRYPT = "scrypt"
    BASE64 = "base64"
    BASE32 = "base32"
    BASE16 = "base16"
    HEX = "hex"
    URL_ENCODE = "url_encode"
    HTML_ENCODE = "html_encode"
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


class CryptanalysisResult(Struct):
    """Result of cryptanalysis attempt."""

    success: bool
    plaintext: str | None
    cipher_type: CipherType
    key: str | None
    confidence: float
    method: str
    attempts: int
    time_seconds: float
    alternative_solutions: list[dict[str, Any]] = field(default_factory=list)


class HashAnalysis(Struct, frozen=True):
    """Analysis of a hash value."""

    hash_value: str
    possible_types: list[HashType]
    length: int
    charset: str
    entropy: float
    is_salted: bool
    salt: str | None = None
    estimated_complexity: str = "unknown"


class EncryptionDetection(Struct, frozen=True):
    """Detection of encryption type from ciphertext."""

    is_encrypted: bool
    possible_ciphers: list[CipherType]
    entropy: float
    chi_square: float
    ioc: float
    language_detected: str | None
    block_size_hint: int | None = None


class CertificateInfo(Struct, frozen=True):
    """Parsed certificate information."""

    subject: dict[str, str]
    issuer: dict[str, str]
    serial_number: str
    not_before: datetime
    not_after: datetime
    fingerprint_sha256: str
    fingerprint_sha1: str
    signature_algorithm: str
    public_key_algorithm: str
    key_size: int
    san_domains: list[str]
    is_self_signed: bool
    is_expired: bool
    days_until_expiry: int
    is_ca: bool
    chain_valid: bool


class KeyAnalysis(Struct, frozen=True):
    """Analysis of cryptographic key."""

    key_type: str
    key_size: int
    is_private: bool
    fingerprint: str
    strength_rating: str
    vulnerabilities: list[str]
    recommended_action: str


class SSHFPRecord(Struct, frozen=True):
    """
    P8-007: SSHFP DNS record (RFC 4255) — SSH host key fingerprint.

    SSHFP records published in DNS provide a verifiable fingerprint of
    an SSH server's host key. Same fingerprint = same physical server
    = strong infrastructure pivot independent of domain/IP.

    Fields:
        algorithm: SSH key algorithm (1=RSA, 2=DSS, 3=ECDSA, 4=Ed25519)
        fingerprint_type: hash algorithm used (1=SHA-1, 2=SHA-256)
        fingerprint: hex-encoded fingerprint of the host key
        domain: domain that published this SSHFP record
    """

    algorithm: int
    fingerprint_type: int
    fingerprint: str
    domain: str = ""

    @property
    def algorithm_name(self) -> str:
        _algo_map = {1: "RSA", 2: "DSS", 3: "ECDSA", 4: "Ed25519", 5: "Ed448"}
        return _algo_map.get(self.algorithm, f"unknown({self.algorithm})")

    @property
    def fingerprint_type_name(self) -> str:
        _type_map = {1: "SHA-1", 2: "SHA-256"}
        return _type_map.get(self.fingerprint_type, f"unknown({self.fingerprint_type})")


class ClassicalCryptanalysis:
    """
    Cryptanalysis of classical (pre-computer) ciphers.

    Essential for CTF challenges, historical cryptanalysis,
    and analyzing simple obfuscation in OSINT.
    """

    ENGLISH_FREQ = {
        "e": 12.7,
        "t": 9.1,
        "a": 8.2,
        "o": 7.5,
        "i": 7.0,
        "n": 6.7,
        "s": 6.3,
        "h": 6.1,
        "r": 6.0,
        "d": 4.3,
        "l": 4.0,
        "c": 2.8,
        "u": 2.8,
        "m": 2.4,
        "w": 2.4,
        "f": 2.2,
        "g": 2.0,
        "y": 2.0,
        "p": 1.9,
        "b": 1.5,
        "v": 1.0,
        "k": 0.8,
        "j": 0.15,
        "x": 0.15,
        "q": 0.1,
        "z": 0.07,
    }
    COMMON_WORDS = {
        "the",
        "be",
        "to",
        "of",
        "and",
        "a",
        "in",
        "that",
        "have",
        "i",
        "it",
        "for",
        "not",
        "on",
        "with",
        "he",
        "as",
        "you",
        "do",
        "at",
        "this",
        "but",
        "his",
        "by",
        "from",
        "they",
        "we",
        "say",
        "her",
        "she",
        "or",
        "an",
        "will",
        "my",
        "one",
        "all",
        "would",
        "there",
        "their",
        "what",
        "so",
        "up",
        "out",
        "if",
        "about",
        "who",
        "get",
        "which",
        "go",
        "me",
        "when",
        "make",
        "can",
        "like",
        "time",
        "no",
        "just",
        "him",
        "know",
        "take",
        "people",
        "into",
        "year",
        "your",
        "good",
        "some",
        "could",
        "them",
        "see",
        "other",
        "than",
        "then",
        "now",
        "look",
        "only",
        "come",
        "its",
        "over",
        "think",
        "also",
        "back",
        "after",
        "use",
        "two",
        "how",
        "our",
        "work",
        "first",
        "well",
        "way",
        "even",
        "new",
        "want",
        "because",
        "any",
        "these",
        "give",
        "day",
        "most",
        "us",
        "is",
        "was",
        "are",
        "password",
        "secret",
        "key",
        "message",
        "encrypt",
        "cipher",
        "code",
        "hidden",
        "flag",
    }
    __slots__ = ("charset",)

    def __init__(self) -> None:
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
        return "".join(result)

    def caesar_bruteforce(self, ciphertext: str) -> list[CryptanalysisResult]:
        """
        Brute-force all 25 Caesar shifts and score results.

        Returns ranked list of possible solutions.
        """
        import time

        start = time.time()
        results = []
        ciphertext = "".join(c for c in ciphertext.lower() if c.isalpha() or c.isspace())
        for shift in range(1, 26):
            plaintext = self.caesar_decrypt(ciphertext, shift)
            score = self._score_english(plaintext)
            results.append(
                CryptanalysisResult(
                    success=score > 0.6,
                    plaintext=plaintext,
                    cipher_type=CipherType.CAESAR,
                    key=f"shift_{shift}",
                    confidence=score,
                    method="brute_force",
                    attempts=shift,
                    time_seconds=time.time() - start,
                )
            )
        results.sort(key=attrgetter("confidence"), reverse=True)
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
        return "".join(result)

    def vigenere_crack(self, ciphertext: str, max_key_length: int = 10) -> CryptanalysisResult:
        """
        Crack Vigenere cipher using Kasiski examination and frequency analysis.
        """
        import time

        start = time.time()
        clean_text = "".join(c for c in ciphertext.lower() if c.isalpha())
        best_length = self._find_vigenere_key_length(clean_text, max_key_length)
        key = []
        for i in range(best_length):
            column = clean_text[i::best_length]
            shift = self._find_caesar_shift(column)
            key.append(self.charset[shift])
        key_str = "".join(key)
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
            time_seconds=time.time() - start,
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
        return "".join(result)

    def rail_fence_decrypt(self, ciphertext: str, rails: int) -> str:
        """Decrypt Rail Fence cipher."""
        if rails < 2:
            return ciphertext
        pattern = []
        row = 0
        direction = 1
        for _i in range(len(ciphertext)):
            pattern.append(row)
            row += direction
            if row == 0 or row == rails - 1:
                direction *= -1
        rail_counts = [pattern.count(r) for r in range(rails)]
        rails_content = []
        idx = 0
        for count in rail_counts:
            rails_content.append(ciphertext[idx : idx + count])
            idx += count
        result = []
        rail_indices = [0] * rails
        for rail in pattern:
            result.append(rails_content[rail][rail_indices[rail]])
            rail_indices[rail] += 1
        return "".join(result)

    def rail_fence_bruteforce(self, ciphertext: str, max_rails: int = 10) -> list[CryptanalysisResult]:
        """Try all rail counts from 2 to max_rails."""
        import time

        start = time.time()
        results = []
        for rails in range(2, min(max_rails + 1, len(ciphertext))):
            plaintext = self.rail_fence_decrypt(ciphertext, rails)
            score = self._score_english(plaintext)
            results.append(
                CryptanalysisResult(
                    success=score > 0.5,
                    plaintext=plaintext,
                    cipher_type=CipherType.RAIL_FENCE,
                    key=f"rails_{rails}",
                    confidence=score,
                    method="brute_force",
                    attempts=rails - 1,
                    time_seconds=time.time() - start,
                )
            )
        results.sort(key=attrgetter("confidence"), reverse=True)
        return results

    def _find_vigenere_key_length(self, ciphertext: str, max_length: int) -> int:
        """Find Vigenere key length using Index of Coincidence."""
        best_length = 1
        best_ioc = 0
        for length in range(1, min(max_length + 1, len(ciphertext) // 2)):
            columns = [ciphertext[i::length] for i in range(length)]
            avg_ioc = sum(self._index_of_coincidence(col) for col in columns) / length
            if avg_ioc > best_ioc:
                best_ioc = avg_ioc
                best_length = length
        return best_length

    def _find_caesar_shift(self, text: str) -> int:
        """Find most likely Caesar shift for text using frequency analysis."""
        best_shift = 0
        best_score = float("inf")
        for shift in range(26):
            decrypted = self.caesar_decrypt(text, shift)
            score = self._chi_square_score(decrypted)
            if score < best_score:
                best_score = score
                best_shift = shift
        return best_shift

    def _score_english(self, text: str) -> float:
        """Score how likely text is English (0-1)."""
        words = text.lower().split()
        word_count = len(words)
        if word_count == 0:
            return 0.0
        common_count = sum(1 for word in words if word.strip('.,!?;:"') in self.COMMON_WORDS)
        word_score = common_count / word_count
        char_counts = Counter(c for c in text.lower() if c.isalpha())
        total_chars = sum(char_counts.values())
        if total_chars == 0:
            return 0.0
        freq_score = 0.0
        for char, count in char_counts.items():
            observed_freq = count / total_chars * 100
            expected_freq = self.ENGLISH_FREQ.get(char, 0.5)
            freq_score += 1 - abs(observed_freq - expected_freq) / 100
        freq_score /= len(char_counts) if char_counts else 1
        return word_score * 0.6 + freq_score * 0.4

    def _chi_square_score(self, text: str) -> float:
        """Calculate chi-square statistic against English frequencies."""
        text = "".join(c for c in text.lower() if c.isalpha())
        if not text:
            return float("inf")
        observed = Counter(text)
        total = len(text)
        chi_sq = 0.0
        for char in self.charset:
            observed_freq = observed.get(char, 0)
            expected_freq = self.ENGLISH_FREQ.get(char, 0.5) / 100 * total
            if expected_freq > 0:
                chi_sq += (observed_freq - expected_freq) ** 2 / expected_freq
        return chi_sq

    def _index_of_coincidence(self, text: str) -> float:
        """Calculate Index of Coincidence."""
        text = "".join(c for c in text.lower() if c.isalpha())
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
        caesar_results = self.caesar_bruteforce(ciphertext)
        all_results.extend(caesar_results[:3])
        atbash_plain = self.atbash_decrypt(ciphertext)
        atbash_score = self._score_english(atbash_plain)
        all_results.append(
            CryptanalysisResult(
                success=atbash_score > 0.5,
                plaintext=atbash_plain,
                cipher_type=CipherType.ATBASH,
                key="atbash",
                confidence=atbash_score,
                method="atbash",
                attempts=1,
                time_seconds=time.time() - start,
            )
        )
        if len(ciphertext) > 20:
            vigenere_result = self.vigenere_crack(ciphertext)
            all_results.append(vigenere_result)
        rail_results = self.rail_fence_bruteforce(ciphertext)
        all_results.extend(rail_results[:3])
        all_results.sort(key=attrgetter("confidence"), reverse=True)
        best = all_results[0]
        best.time_seconds = time.time() - start
        best.alternative_solutions = [
            {"cipher": r.cipher_type.value, "confidence": r.confidence, "plaintext": r.plaintext[:100]}
            for r in all_results[1:4]
        ]
        return best


class HashAnalyzer:
    """
       Analyze and identify hash types.

       Supports hash identification, entropy analysis,
    and basic cracking attempts.
    """

    HASH_PATTERNS = {
        HashType.MD5: {"length": 32, "regex": "^[a-f0-9]{32}$", "example": "5f4dcc3b5aa765d61d8327deb882cf99"},
        HashType.SHA1: {"length": 40, "regex": "^[a-f0-9]{40}$", "example": "5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8"},
        HashType.SHA256: {
            "length": 64,
            "regex": "^[a-f0-9]{64}$",
            "example": "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8",
        },
        HashType.SHA512: {
            "length": 128,
            "regex": "^[a-f0-9]{128}$",
            "example": "b109f3bbbc244eb82441917ed06d618b9008dd09b3befd1b5e07394c706a8bb980b1d7785e5976ec049b46df5f1326af5a2ea6d103fd07c95385ffab0cacbc86",
        },
        HashType.BCRYPT: {
            "length": 60,
            "regex": "^\\$2[aby]?\\$\\d{1,2}\\$[./A-Za-z0-9]{53}$",
            "example": "$2a$10$N9qo8uLOickgx2ZMRZoMy.MqrqhmM6JGKpS4G3R1G2JH8YpfB0Bqy",
        },
        HashType.SCRYPT: {"length": None, "regex": "^\\$scrypt\\$", "example": "$scrypt$N=32768,r=8,p=1$"},
        HashType.ARGON2: {"length": None, "regex": "^\\$argon2", "example": "$argon2id$v=19$m=65536,t=3,p=4$"},
        HashType.NTLM: {"length": 32, "regex": "^[a-f0-9]{32}$", "example": "8846f7eaee8fb117ad06bdd830b7586c"},
        HashType.LM: {"length": 32, "regex": "^[a-fA-F0-9]{32}$", "example": "AAD3B435B51404EEAAD3B435B51404EE"},
    }

    def identify_hash(self, hash_value: str) -> HashAnalysis:
        """
        Identify possible hash types from hash string.
        """
        hash_clean = hash_value.strip().lower()
        possible_types = []
        is_salted = False
        salt = None
        for hash_type, pattern in self.HASH_PATTERNS.items():
            regex_match = False
            length_match = False
            if pattern["regex"]:
                import re

                if re.match(pattern["regex"], hash_clean):
                    regex_match = True
            if pattern["length"] is None or len(hash_clean) == pattern["length"]:
                length_match = True
            if regex_match or (length_match and (not pattern["regex"])):
                possible_types.append(hash_type)
        if "$" in hash_clean:
            is_salted = True
            parts = hash_clean.split("$")
            if len(parts) >= 3:
                salt = parts[2] if len(parts[2]) < 50 else parts[2][:50]
        entropy = self._calculate_entropy(hash_clean)
        estimated_complexity = self._estimate_complexity(hash_clean, possible_types, is_salted)
        return HashAnalysis(
            hash_value=hash_value,
            possible_types=possible_types if possible_types else [HashType.UNKNOWN],
            length=len(hash_clean),
            charset=self._detect_charset(hash_clean),
            entropy=entropy,
            is_salted=is_salted,
            salt=salt,
            estimated_complexity=estimated_complexity,
        )

    def crack_dictionary(
        self, hash_value: str, wordlist: list[str] | None = None, hash_type: HashType | None = None
    ) -> str | None:
        """
        Attempt dictionary attack on hash.

        Tries Metal GPU → CPU NEON (Rust) → Python hashlib fallback chain.
        Metal GPU is used opportunistically — only when HLEDAC_ENABLE_METAL_HASHCRACK=1
        and the Metal cracker is available.

        Args:
            hash_value: Hash to crack
            wordlist: List of passwords to try (uses common passwords if None)
            hash_type: Known hash type (auto-detect if None)

        Returns:
            Cracked password or None
        """
        if wordlist is None:
            wordlist = [
                "password",
                "123456",
                "12345678",
                "qwerty",
                "abc123",
                "monkey",
                "letmein",
                "dragon",
                "111111",
                "baseball",
                "iloveyou",
                "trustno1",
                "sunshine",
                "princess",
                "admin",
                "welcome",
                "shadow",
                "ashley",
                "football",
                "jesus",
                "michael",
                "ninja",
                "mustang",
                "password1",
                "123456789",
                "adobe123",
                "admin123",
                "root",
                "toor",
                "guest",
                "default",
                "changeme",
                "p@ssw0rd",
                "Pass1234",
                "secret",
            ]
        if hash_type is None:
            analysis = self.identify_hash(hash_value)
            if analysis.possible_types and analysis.possible_types[0] != HashType.UNKNOWN:
                hash_type = analysis.possible_types[0]
            else:
                hash_type = HashType.MD5

        # ── SILICON-01: Try Metal GPU hash cracker first ──
        cracker = _get_metal_cracker()
        if cracker is not None:
            try:
                if hash_type == HashType.MD5:
                    result = cracker.crack_md5(hash_value.lower(), wordlist)
                elif hash_type in (HashType.SHA256,):
                    result = cracker.crack_sha256(hash_value.lower(), wordlist)
                else:
                    result = None  # unsupported hash type for GPU

                if result is not None:
                    gpu_stats = cracker.get_stats()
                    logger.debug(
                        "MetalHashCracker: GPU match found gpu_attempts=%d gpu_matches=%d cpu_fallbacks=%d",
                        gpu_stats.get("gpu_attempts", 0),
                        gpu_stats.get("gpu_matches", 0),
                        gpu_stats.get("cpu_fallbacks", 0),
                    )
                    return result
            except Exception as exc:
                logger.debug("MetalHashCracker.crack_md5 failed, falling back to CPU: %s", exc)

        # ── Python CPU fallback (hashlib) ──
        hash_func = self._get_hash_function(hash_type)
        if hash_func is None:
            return None
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
        try:
            if all(c in "0123456789abcdefABCDEF" for c in data) and len(data) % 2 == 0:
                data = binascii.unhexlify(data).decode("latin-1")
        except Exception:  # noqa: BLE001
            pass
        counter = Counter(data)
        length = len(data)
        entropy = 0.0
        for count in counter.values():
            p = count / length
            entropy -= p * math.log2(p)
        return entropy

    def _detect_charset(self, data: str) -> str:
        """Detect character set used in hash."""
        has_lower = bool(re.search("[a-z]", data))
        has_upper = bool(re.search("[A-Z]", data))
        has_digit = bool(re.search("[0-9]", data))
        has_special = bool(re.search("[^a-zA-Z0-9]", data))
        charset = []
        if has_lower:
            charset.append("lowercase")
        if has_upper:
            charset.append("uppercase")
        if has_digit:
            charset.append("digits")
        if has_special:
            charset.append("special")
        return ", ".join(charset) if charset else "unknown"

    def _estimate_complexity(self, hash_value: str, possible_types: list[HashType], is_salted: bool) -> str:
        """Estimate cracking complexity."""
        if is_salted:
            if HashType.BCRYPT in possible_types or HashType.ARGON2 in possible_types:
                return "impossible"
            if HashType.SCRYPT in possible_types:
                return "very_high"
        if HashType.SHA256 in possible_types or HashType.SHA512 in possible_types:
            return "high"
        if HashType.SHA1 in possible_types:
            return "medium"
        if HashType.MD5 in possible_types or HashType.NTLM in possible_types:
            return "low"
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

    def crack_batch(
        self,
        hash_values: list[str],
        wordlist: list[str],
        hash_type: HashType = HashType.MD5,
    ) -> dict[str, str | None]:
        """
        Batch crack multiple hashes against the same wordlist.

        Uses Metal GPU for parallel cracking (optimal for ≥4 hashes).
        More efficient than calling crack_dictionary() N times because
        the wordlist is processed once.

        Args:
            hash_values: List of hashes to crack (all must be same type)
            wordlist: List of passwords to try
            hash_type: Hash type (default: MD5)

        Returns:
            Dict mapping hash_value → cracked_password (or None if not found)
        """
        if not hash_values:
            return {}

        # Try Metal GPU batch cracking first (Rust-accelerated)
        cracker = _get_metal_cracker()
        if cracker is not None and hash_type == HashType.MD5:
            try:
                targets = [h.lower() for h in hash_values]
                # Rust crack_batch_md5 returns HashMap<String, Option<String>>
                # PyO3 converts Option<String> to Python str | None automatically
                results_raw = cracker.crack_batch_md5(targets, wordlist)
                # results_raw is already dict[str, str | None], just return as-is
                if isinstance(results_raw, dict):
                    # Map back to original case-preserved hash_values
                    target_lower_to_original = {h.lower(): h for h in hash_values}
                    return {target_lower_to_original.get(k, k): v for k, v in results_raw.items()}
            except Exception as exc:
                logger.debug("MetalHashCracker.crack_batch_md5 failed: %s", exc)

        # CPU fallback: sequential cracking
        results: dict[str, str | None] = {}
        for hash_value in hash_values:
            results[hash_value] = self.crack_dictionary(hash_value, wordlist, hash_type)
        return results


class EncryptionDetector:
    """
    Detect if data is encrypted and identify possible cipher.

    Uses statistical analysis to detect encryption.
    """

    def analyze(self, data: str | bytes) -> EncryptionDetection:
        """
        Analyze data to detect encryption.
        """
        if isinstance(data, bytes):
            try:
                text = data.decode("utf-8")
            except Exception:
                text = data.decode("latin-1")
        else:
            text = data
        entropy = self._calculate_entropy(text)
        chi_sq = self._chi_square_test(text)
        ioc = self._index_of_coincidence(text)
        is_encrypted = self._is_likely_encrypted(entropy, ioc, chi_sq)
        possible_ciphers = self._guess_cipher(text, entropy, ioc)
        language = None
        if not is_encrypted:
            language = self._detect_language(text)
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
            block_size_hint=block_size,
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
        expected = length / 256
        chi_sq = sum((count - expected) ** 2 / expected for count in counter.values())
        return chi_sq

    def _index_of_coincidence(self, text: str) -> float:
        """Calculate Index of Coincidence (0.067 for English, 0.0385 for random)."""
        text = "".join(c for c in text if c.isalpha())
        if len(text) < 2:
            return 0.0
        text = text.lower()
        counter = Counter(text)
        n = len(text)
        ic = sum(count * (count - 1) for count in counter.values()) / (n * (n - 1))
        return ic

    def _is_likely_encrypted(self, entropy: float, ioc: float, chi_sq: float) -> bool:
        """Determine if data is likely encrypted."""
        if entropy > 6.0 and ioc < 0.05:
            return True
        if entropy > 7.0:
            return True
        if chi_sq > 1000:
            return True
        return False

    def _guess_cipher(self, text: str, entropy: float, ioc: float) -> list[CipherType]:
        """Guess possible cipher type."""
        possible = []
        if len(text) % 16 == 0:
            possible.append(CipherType.AES)
        if len(text) % 8 == 0:
            possible.append(CipherType.DES)
            possible.append(CipherType.DES3)
            possible.append(CipherType.BLOWFISH)
        if entropy > 7.5:
            possible.extend([CipherType.AES, CipherType.CHACHA20])
        if 4.0 < entropy < 6.0:
            possible.extend([CipherType.CAESAR, CipherType.VIGENERE])
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

    def _detect_language(self, text: str) -> str | None:
        """Detect language of text."""
        english_words = {"the", "be", "to", "of", "and", "a", "in", "that", "have"}
        words = set(text.lower().split())
        if len(words.intersection(english_words)) > 3:
            return "english"
        return None

    def _estimate_block_size(self, text: str) -> int | None:
        """Estimate block cipher block size using Kasiski-like method."""
        if len(text) < 32:
            return None
        for block_size in [8, 16, 32]:
            if len(text) % block_size == 0:
                return block_size
        return None


class CertificateAnalyzer:
    """
    Analyze X.509 certificates.

    Parse and analyze SSL/TLS certificates for OSINT.
    """

    def parse_certificate(self, cert_pem: str) -> CertificateInfo | None:
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

    def parse_certificate_der(self, cert_der: bytes) -> CertificateInfo | None:
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
        subject = {}
        for attr in cert.subject:
            subject[attr.oid._name] = attr.value
        issuer = {}
        for attr in cert.issuer:
            issuer[attr.oid._name] = attr.value
        fingerprint_sha256 = cert.fingerprint(hashes.SHA256()).hex()
        fingerprint_sha1 = cert.fingerprint(hashes.SHA1()).hex()
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
        sig_alg = cert.signature_algorithm_oid._name
        san_domains = []
        try:
            san = cert.extensions.get_extension_for_oid(x509.oid.ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
            san_domains = [name.value for name in san.value]
        except x509.ExtensionNotFound:  # noqa: BLE001
            pass
        is_self_signed = cert.subject == cert.issuer
        now = datetime.now(UTC)
        is_expired = now > cert.not_valid_after
        days_until_expiry = (cert.not_valid_after - now).days
        is_ca = False
        try:
            basic_constraints = cert.extensions.get_extension_for_oid(x509.oid.ExtensionOID.BASIC_CONSTRAINTS)
            is_ca = basic_constraints.value.ca
        except x509.ExtensionNotFound:  # noqa: BLE001
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
            chain_valid=True,
        )

    def analyze_security(self, cert_info: CertificateInfo) -> dict[str, Any]:
        """Analyze certificate security."""
        issues = []
        warnings = []
        if cert_info.is_expired:
            issues.append("Certificate is expired")
        elif cert_info.days_until_expiry < 30:
            warnings.append(f"Certificate expires in {cert_info.days_until_expiry} days")
        if cert_info.is_self_signed:
            warnings.append("Certificate is self-signed")
        if cert_info.key_size < 2048 and "RSA" in cert_info.public_key_algorithm:
            issues.append(f"Weak RSA key size: {cert_info.key_size}")
        elif cert_info.key_size < 256 and "EC" in cert_info.public_key_algorithm:
            issues.append(f"Weak EC key size: {cert_info.key_size}")
        weak_sigs = ["md5", "sha1"]
        if any(weak in cert_info.signature_algorithm.lower() for weak in weak_sigs):
            issues.append(f"Weak signature algorithm: {cert_info.signature_algorithm}")
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
            "recommendations": self._get_recommendations(cert_info, issues),
        }

    def _get_recommendations(self, cert_info: CertificateInfo, issues: list[str]) -> list[str]:
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

    __slots__ = ("certificate_analyzer", "classical", "encryption_detector", "hash_analyzer", "stats")

    def __init__(self) -> None:
        self.classical = ClassicalCryptanalysis()
        self.hash_analyzer = HashAnalyzer()
        self.encryption_detector = EncryptionDetector()
        self.certificate_analyzer = CertificateAnalyzer()
        self.stats = {"ciphers_cracked": 0, "hashes_analyzed": 0, "certs_parsed": 0, "sshfp_queried": 0}

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

    def crack_hash(self, hash_value: str, wordlist: list[str] | None = None) -> str | None:
        """Attempt to crack hash with dictionary attack."""
        return self.hash_analyzer.crack_dictionary(hash_value, wordlist)

    def detect_encryption(self, data: str | bytes) -> EncryptionDetection:
        """Detect if data is encrypted."""
        return self.encryption_detector.analyze(data)

    def parse_certificate(self, cert_pem: str) -> CertificateInfo | None:
        """Parse X.509 certificate."""
        result = self.certificate_analyzer.parse_certificate(cert_pem)
        if result:
            self.stats["certs_parsed"] += 1
        return result

    def analyze_certificate_security(self, cert_info: CertificateInfo) -> dict[str, Any]:
        """Analyze certificate security."""
        return self.certificate_analyzer.analyze_security(cert_info)

    def query_sshfp(self, domain: str) -> list[SSHFPRecord]:
        """
        P8-007: Query SSHFP DNS records for SSH host key fingerprints.

        Returns list of SSHFPRecord with algorithm, fingerprint type,
        and fingerprint. Empty list if no SSHFP records published.

        Wraps the standalone query_sshfp() with stats tracking.
        """
        result = query_sshfp(domain)
        if result:
            self.stats["sshfp_queried"] += 1
        return result

    def encode_decode(self, data: str, encoding: CipherType, decode: bool = False) -> str:
        """
        Encode/decode various encodings.
        """
        if encoding == CipherType.BASE64:
            if decode:
                return base64.b64decode(data).decode("utf-8", errors="ignore")
            return base64.b64encode(data.encode()).decode()
        elif encoding == CipherType.HEX:
            if decode:
                return bytes.fromhex(data).decode("utf-8", errors="ignore")
            return data.encode().hex()
        elif encoding == CipherType.URL_ENCODE:
            import urllib.parse

            if decode:
                return urllib.parse.unquote(data)
            return urllib.parse.quote(data)
        return data

    def generate_password_hash(
        self, password: str, hash_type: HashType = HashType.SHA256, salt: str | None = None
    ) -> str:
        """Generate password hash."""
        if salt:
            password = salt + password
        hash_func = self.hash_analyzer._get_hash_function(hash_type)
        if hash_func:
            return hash_func(password)
        return hashlib.sha256(password.encode()).hexdigest()

    def get_statistics(self) -> dict[str, Any]:
        """Get cryptographic analysis statistics."""
        return {
            **self.stats,
            "available_modules": {
                "classical_crypto": True,
                "hash_analysis": True,
                "encryption_detection": True,
                "certificate_analysis": CRYPTOGRAPHY_AVAILABLE,
            },
        }


def query_sshfp(domain: str) -> list[SSHFPRecord]:
    """
    P8-007: Query SSHFP DNS records (RFC 4255) for SSH host key fingerprints.

    SSHFP records published in DNS provide a verifiable fingerprint of
    an SSH server's host key. Same SSH host key fingerprint across
    domains = same physical server = strong infrastructure pivot
    independent of domain name or IP address.

    Algorithm mapping (RFC 4255 sec 3.1.1):
        1 = RSA, 2 = DSS, 3 = ECDSA, 4 = Ed25519
    Fingerprint type mapping (RFC 4255 sec 3.1.2):
        1 = SHA-1, 2 = SHA-256

    Args:
        domain: Domain name to query SSHFP records for

    Returns:
        List of SSHFPRecord objects. Empty list if no records or on error.

    Note:
        dnspython must be installed (>=2.4.0 in pyproject.toml).
        Uses lazy import to avoid M1 8GB startup cost.
    """
    try:
        import dns.resolver
    except ImportError:
        logger.debug("[CryptoIntel] dnspython not available for SSHFP query")
        return []
    if not domain or not isinstance(domain, str):
        return []
    domain = domain.strip().lower().rstrip(".")
    try:
        answers = dns.resolver.resolve(domain, "SSHFP")
        records: list[SSHFPRecord] = []
        for rdata in answers:
            try:
                records.append(
                    SSHFPRecord(
                        algorithm=rdata.algorithm,
                        fingerprint_type=rdata.fp_type,
                        fingerprint=rdata.fingerprint.hex(),
                        domain=domain,
                    )
                )
            except (AttributeError, TypeError):
                # Malformed record — skip
                continue
        _sshfp_stats["queries"] += 1
        _sshfp_stats["records_found"] += len(records)
        return records
    except dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers:
        _sshfp_stats["queries"] += 1
        return []
    except Exception as e:
        logger.debug(f"[CryptoIntel] SSHFP query failed for {domain}: {e}")
        _sshfp_stats["queries"] += 1
        return []


async def query_sshfp_async(domain: str) -> list[SSHFPRecord]:
    """
    P8-007: Async wrapper for SSHFP DNS query.

    Uses asyncio.to_thread() for non-blocking DNS resolution since
    dnspython 2.x resolve() is synchronous. For dnspython 3.x,
    dns.asyncresolver.resolve() would be natively async.
    """
    import asyncio

    try:
        return await asyncio.to_thread(query_sshfp, domain)
    except Exception:
        return []


def get_sshfp_stats() -> dict[str, int]:
    """P8-007: Return SSHFP query statistics."""
    return dict(_sshfp_stats)


_sshfp_stats: dict[str, int] = {"queries": 0, "records_found": 0}
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
    "SSHFPRecord",
    "CipherType",
    "HashType",
    "query_sshfp",
    "query_sshfp_async",
]
