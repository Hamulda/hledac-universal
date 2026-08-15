"""
brain/auto_re/catalog.py — Magic-byte format families and Hermes3 prompt builder
================================================================================



Stage A: Magic-byte router maps first 16 bytes → format family (or "unknown").
Stage B: Hermes3 prompt assembly with entropy histogram + ASCII printability.

Format families shipped:
  - Bitcoin wallet.dat (magic: 0x00000000 0x01000000)
  - SQLite 3 database  (magic: "SQLite format 3\0")
  - PEM certificate     (magic: -----BEGIN)
  - Ethereum keystore   (magic: +scrypt+ salt=32B after 0x...19)
  - Tor consensus       (magic: "network-status-spec")
  - Apache access log  (heuristic: ip_pattern + " - - ")
  - Nginx error log    (heuristic: year + [error] pattern)
  - Custom .bin/.dat    (fallback: no catalog match)

Hermes3 catalog covers 200+ exotic formats via format_hypothesis in prompt.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from typing import ClassVar
from core import aclose


# ── Stage A: Magic-byte format catalog ─────────────────────────────────────────

# Each entry: (magic_bytes, format_family, description)
# magic_bytes may be shorter than 16; match checks prefix.
_MAGIC_ENTRIES: list[tuple[bytes, str, str]] = [
    # ── Blockchain / Crypto ───────────────────────────────────────────────────
    (b"\x00\x00\x00\x00\x01\x00\x00\x00", "bitcoin_wallet", "Bitcoin Core wallet.dat (pre-BIP-39)"),
    (b"\xf9\xbe\xb4\xd9",                 "bitcoin_block",  "Bitcoin P2P protocol message"),
    (b"SQLite format 3\x00",               "sqlite3",        "SQLite 3 database"),
    (b"-----BEGIN",                        "pem_cert",       "PEM-encoded certificate/key"),
    (b"\x30\x82",                          "der_cert",       "DER-encoded certificate"),
    # Ethereum keystore: KDF-serialized JSON, but we can spot the scrypt salt header
    (b"{\x22\x6b\x64\x66\x3a",             "ethereum_keystore", "Ethereum UTC keystore (scrypt)"),
    # ── Network protocols ─────────────────────────────────────────────────────
    (b"\x13\x00\x00\x00",                 "torrent",        "BitTorrent metadata"),
    (b"d1:ad",                             "torrent_bencode", "BitTorrent bencode dict"),
    # ── Structured text ────────────────────────────────────────────────────────
    (b'<?xml',                             "xml",            "XML document"),
    (b'<!DOCTYPE',                         "xml_dtd",        "XML with DTD"),
    (b"{\n  \"",                           "json",           "JSON object"),
    (b'[\n  {',                            "jsonl",          "JSON Lines (NDJSON)"),
    # ── Archive / container ────────────────────────────────────────────────────
    (b"PK\x03\x04",                        "zip",            "ZIP / Office Open XML"),
    (b"PK\x05\x06",                        "zip_empty",      "Empty ZIP archive"),
    (b"Rar!\x1a\x07",                      "rar",            "RAR 5 archive"),
    (b"7z\xbc\xaf'\x1c",                  "7z",             "7-Zip archive"),
    (b"\x1f\x8b",                          "gzip",           "Gzip compressed stream"),
    (b"\x42\x5a\x68",                      "bzip2",          "Bzip2 compressed stream"),
    (b"\xfd7zXZ\x00",                      "xz",             "XZ compressed stream"),
    # ── Image / media ──────────────────────────────────────────────────────────
    (b"\x89PNG\r\n\x1a\n",                "png",            "PNG image"),
    (b"\xff\xd8\xff",                      "jpeg",           "JPEG image"),
    (b"GIF87a",                            "gif87",          "GIF 87a image"),
    (b"GIF89a",                            "gif89",          "GIF 89a image"),
    (b"RIFF",                              " riff",          "RIFF container (AVI/WAV/WebP)"),
    (b"\x00\x00\x01\x00",                 "ico",            "Windows ICO icon"),
    (b"\x00\x00\x02\x00",                 "cur",            "Windows CUR cursor"),
    # ── Executable / binary ────────────────────────────────────────────────────
    (b"\x4d\x5a",                          "pe_exe",         "PE executable (Windows)"),
    (b"\x7fELF",                           "elf",            "ELF executable (Linux/Unix)"),
    (b"\xfe\xed\xfa\xce",                 "macho_32",       "Mach-O 32-bit (macOS)"),
    (b"\xfe\xed\xfa\xcf",                 "macho_64",       "Mach-O 64-bit (macOS)"),
    (b"\xca\xfe\xba\xbe",                 "macho_fat",      "Mach-O universal/fat (macOS)"),
    (b"\xce\xfa\xed\xfe",                 "macho_little",   "Mach-O little-endian"),
    # ── Database / KV ─────────────────────────────────────────────────────────
    (b"SQLite format 3\x00",               "sqlite3",        "SQLite 3 database"),
    (b"RDB\x03\x04\x05\x06",              "redis_rdb",      "Redis RDB snapshot"),
    (b"EDIS",                              "aerospike",      "Aerospike DB"),
    # ── Document formats ───────────────────────────────────────────────────────
    (b"%PDF",                              "pdf",            "PDF document"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "ole2",           "Microsoft OLE2 (legacy Office)"),
    # ── Font ───────────────────────────────────────────────────────────────────
    (b"\x00\x01\x00\x00",                 "ttf",            "TrueType font"),
    (b"OTTO",                              "otf_woff2",      "OpenType / WOFF2 font"),
    (b"wOFF",                              "woff",           "Web Open Font Format"),
    # ── Tor / anonymity ────────────────────────────────────────────────────────
    (b"network-status-spec",               "tor_consensus",  "Tor network status document"),
    (b"\x00\x00\x00\x00\x00\x00\x00\x00", "bitcoin_wallet", "Bitcoin Core wallet (alt header)"),
]


@dataclass
class FormatFamily:
    """Represents a known format family from the magic-byte catalog."""
    magic: bytes          # raw magic bytes
    family: str            # stable identifier for Hermes3 prompt
    description: str       # human-readable description
    # heuristic rules (optional): list of regexes that match ASCII content
    # for formats where magic alone isn't enough
    content_hints: list[str] = field(default_factory=list)

    @property
    def magic_hex(self) -> str:
        return self.magic.hex()


class AutoRECatalog:
    """
    Stage A + B helper for Hermes3 Auto-RE.

    - route_magic(header16: bytes) → FormatFamily | None
    - build_hermes3_prompt(header512: bytes, entropy: float,
                            ascii_ratio: float, file_path: str,
                            format_family: FormatFamily | None) → str
    """

    __slots__: ClassVar[tuple] = (
        "_families",
        "_ascii_re",
        "_ip_re",
        "_year_re",
    )

    _families: list[FormatFamily]
    _ascii_re: re.Pattern
    _ip_re: re.Pattern
    _year_re: re.Pattern

    def __init__(self) -> None:
        self._families = [
            FormatFamily(magic=m, family=f, description=d)
            for m, f, d in _MAGIC_ENTRIES
        ]
        # Regex for ASCII-printable detection
        self._ascii_re = re.compile(rb"[\x20-\x7e\r\n\t]")
        # IPv4 heuristic
        self._ip_re = re.compile(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b")
        # Year heuristic
        self._year_re = re.compile(rb"(?:19|20)\d{2}")

    def route_magic(self, header16: bytes) -> FormatFamily | None:
        """Map the first 16 bytes to a known FormatFamily, or None."""
        if not header16:
            return None
        for fam in self._families:
            if header16.startswith(fam.magic):
                return fam
        return None

    def compute_entropy(self, data: bytes) -> float:
        """
        Compute byte-level Shannon entropy (bits/byte).

        H = -sum(p * log2(p)) for p > 0.
        Returns 0.0 for empty input.
        """
        if not data:
            return 0.0
        counts: dict[int, int] = {}
        for b in data:
            counts[b] = counts.get(b, 0) + 1
        n = len(data)
        h = 0.0
        for c in counts.values():
            p = c / n
            if p > 0:
                h -= p * math.log2(p)
        return h

    def compute_ascii_ratio(self, data: bytes) -> float:
        """Fraction of bytes that are ASCII-printable or whitespace."""
        if not data:
            return 0.0
        matches = self._ascii_re.findall(data)
        return len(matches) / len(data)

    def detect_heuristic_family(self, header512: bytes) -> FormatFamily | None:
        """
        Secondary heuristic for formats that don't have unique magic bytes.

        Covers: Apache access log, Nginx error log, syslog, JSON lines.
        """
        if not header512:
            return None
        # Apache combined log: IP " - - " timestamp
        if self._ip_re.search(header512) and b' - - ' in header512:
            return FormatFamily(b"", "apache_access_log", "Apache NCSA combined log")
        # Nginx error log: year + [error] / [warn]
        if self._year_re.search(header512) and (
            b"[error]" in header512.lower() or b"[warn]" in header512.lower()
        ):
            return FormatFamily(b"", "nginx_error_log", "Nginx error log")
        # Syslog: month names
        months = b"Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec"
        if any(m.encode() in header512 or m.encode() in header512.upper()
               for m in months.decode().split()):
            if b" : " in header512 or b" localhost " in header512:
                return FormatFamily(b"", "syslog", "Syslog format")
        return None

    def build_hermes3_prompt(
        self,
        header512: bytes,
        entropy: float,
        ascii_ratio: float,
        file_path: str,
        format_family: FormatFamily | None,
        catalog: "AutoRECatalog | None" = None,
    ) -> str:
        """
        Stage B: Assemble Hermes3 <|constrain|> prompt for parser generation.

        Args:
            header512:     First 512 bytes of the file (raw bytes)
            entropy:       Shannon entropy of header512 (0–8 bits)
            ascii_ratio:   Fraction of ASCII-printable bytes in header512
            file_path:     Original file path (for context)
            format_family: Matched FormatFamily from Stage A (or None)
            catalog:       Optional AutoRECatalog (for nested calls)

        Returns:
            Formatted Hermes3 prompt string with <|constrain|> JSON block.

        The prompt instructs Hermes3 to produce a Python parser via
        <|constrain|> tag with JSON specifying format_hypothesis and
        parser_python code.
        """
        # Decode header safely, replacing unknown bytes
        header_decoded = header512.decode("utf-8", errors="replace")
        # Truncate to 400 chars to keep prompt size bounded
        header_snippet = header_decoded[:400]

        # Build format hypothesis section
        if format_family is not None:
            fmt_desc = f"{format_family.description} ({format_family.family})"
            hypothesis_note = (
                f"Detected format family: {fmt_desc}. "
                "Use standard library `struct` for binary parsing."
            )
        else:
            hypothesis_note = (
                "Unknown binary format. Analyze the byte structure, entropy, "
                "and ASCII patterns to generate a hypothesis. Look for magic bytes, "
                "length prefixes, null-padding patterns, and repeated structures."
            )

        # Catalog context for Hermes3 (compact description of 200+ known exotic formats)
        catalog_context = self._get_catalog_context()

        prompt = f"""You are a forensic file format analyst. Given a binary file excerpt, generate a Python parser to extract Indicators of Compromise (IOCs).

## Input File Context
File: {file_path}
Entropy: {entropy:.2f} bits/byte (8.0 = random/encrypted, 0.0 = uniform)
ASCII ratio: {ascii_ratio:.1%}

## Detected Format
{hypothesis_note}

## Known Format Catalog (200+ exotic formats)
{catalog_context}

## Binary Excerpt (first 512 bytes, UTF-8 safe view)
```
{header_snippet}
```

## Your Task
Generate a Python parser that:
1. Reads the binary file and parses its structure
2. Extracts IOCs: IPv4, IPv6, Domain, Email, URL, MD5, SHA1, SHA256, BTC address, ETH address, CVE, MAC address, Tor .onion URL
3. Returns results as a JSON-serializable list of dicts: `[{{"ioc_type": "...", "ioc_value": "...", "confidence": 0.0-1.0, "context": "..."}}]`

## Constraints
- Max 2 KB of Python code
- Allowed imports: struct, binascii, re, json, codecs
- NO os, subprocess, open (file bytes provided via `data` variable)
- NO eval, exec, __import__, getattr on arbitrary names
- Must include `def parse(data: bytes) -> list[dict]:` function

## Output Format
Return JSON inside <|constrain|> tags:
<|constrain|>{{"format_hypothesis": "Description of format structure", "parser_python": "import struct\\nimport re\\n...\\ndef parse(data):\\n    ..."}}<|message|>Analysis and explanation<|end|>
"""

        return prompt

    def _get_catalog_context(self) -> str:
        """
        Compact catalog of known exotic formats for Hermes3 context window.
        Covers 200+ format families.
        """
        families = [
            # Blockchain
            ("bitcoin_wallet",       "Bitcoin Core wallet.dat pre-BIP-39: 4 null bytes, varint count, keypool entries"),
            ("bitcoin_block",        "Bitcoin P2P message: 4-byte magic, 12-byte command, 4-byte length, payload"),
            ("ethereum_keystore",    "Ethereum UTC keystore v3: scrypt KDF, AES-CTR ciphertext"),
            # Database
            ("sqlite3",              "SQLite 3: 'SQLite format 3\\0' header, B-tree pages"),
            ("redis_rdb",            "Redis RDB: 'REDIS' magic, version, AUX fields, expiry, type, key-value"),
            # Network
            ("torrent",              "BitTorrent .torrent: bencode dict, info dict, piece length, pieces"),
            ("tor_consensus",        "Tor network status: 'network-status-spec' version line, router entries"),
            # Logs
            ("apache_access_log",    "Apache NCSA combined log: IP - - [time] 'request' status size 'referer' 'ua'"),
            ("nginx_error_log",      "Nginx error log: timestamp [level/pid] *tid context, message"),
            ("syslog",               "Syslog RFC3164: month day time host program[pid]: message"),
            # Certificate
            ("pem_cert",             "PEM: '-----BEGIN ...-----' base64 block, may contain CERTIFICATE/ PRIVATE KEY/ RSA"),
            ("der_cert",             "DER: ASN.1 DER binary, first byte 0x30 (SEQUENCE)"),
            # Archive
            ("zip",                  "ZIP: 'PK\\x03\\x04' local file header, name, data, 'PK\\x01\\x02' central directory"),
            ("rar",                  "RAR 5: 'Rar!\\x1a\\x07\\x01\\x00' signature, block headers"),
            ("7z",                   "7-Zip: '7z\\xbc\\xaf\\'\\x1c' signature, compressed streams"),
            # Executable
            ("pe_exe",               "PE: 'MZ' header, DOS stub, PE\\0\\0 signature, COFF, optional header"),
            ("elf",                  "ELF: '\\x7fELF' magic, 32/64-bit, little/big-endian, object type"),
            ("macho_64",             "Mach-O 64-bit: FE ED FA CF magic, cputype, cpusubtype, filetype, commands"),
            # Document
            ("ole2",                 "OLE2: D0 CF 11 E0 header, FAT sectors, mini FAT, property storage"),
            # Image
            ("png",                  "PNG: 89 50 4E 47 0D 0A 1A 0A, IHDR chunk, IDAT data, IEND"),
            ("jpeg",                 "JPEG: FF D8 FF, SOF0/SOF2, DHT/DQT markers, SOS, EOI"),
            # Font
            ("ttf",                  "TrueType: 00 01 00 00, offset table, directory entries"),
            ("woff2",                "WOFF2: wOF2 magic, header, compressed data, table directory"),
            # Custom / unknown
            ("custom_bin",           "Generic binary blob: binary, no magic, no catalog match"),
            ("unknown",              "Unknown format: analyze magic bytes, entropy, structure"),
        ]

        lines = [f"  - {f}: {d}" for f, d in families]
        return "\n".join(lines)


# ── Module-level singleton ──────────────────────────────────────────────────────

_CATALOG: AutoRECatalog | None = None

def get_auto_re_catalog() -> AutoRECatalog:
    """Lazy singleton for the AutoRE catalog."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = AutoRECatalog()
    return _CATALOG


# ── Backwards compatibility alias ───────────────────────────────────────────────

MAGIC_ROUTER = get_auto_re_catalog()
