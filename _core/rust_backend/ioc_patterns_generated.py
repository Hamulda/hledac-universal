# AUTO-GENERATED from rust_extensions/src/ioc_patterns.rs. DO NOT EDIT.
# Source of truth: rust_extensions/src/ioc_patterns.rs
#
# To regenerate after pattern changes:
# 1. Copy patterns from ioc_patterns.rs
# 2. Convert Rust raw strings to Python r"..." format
# 3. Update this file


import re
from typing import Final
from _core._util import aclose

# === IOC Patterns (must match rust_extensions/src/ioc_patterns.rs) ===

IPV4_PATTERN: Final[str] = r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
IPV6_PATTERN: Final[str] = r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
DOMAIN_PATTERN: Final[str] = r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b"
MD5_PATTERN: Final[str] = r"\b[a-fA-F0-9]{32}\b"
SHA1_PATTERN: Final[str] = r"\b[a-fA-F0-9]{40}\b"
SHA256_PATTERN: Final[str] = r"\b[a-fA-F0-9]{64}\b"
EMAIL_PATTERN: Final[str] = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
CVE_PATTERN: Final[str] = r"CVE-\d{4}-\d{4,}"  # No trailing \b — CVE has no word break after
URL_PATTERN: Final[str] = r"https?://[^\s<>\"']+"
HASH_PATTERN: Final[str] = r"\b[a-fA-F0-9]{32,64}\b"

# Encoding detection patterns
ENCODING_BASE32_PATTERN: Final[str] = r"^[A-Z2-7]+=*$"
ENCODING_BASE64_PATTERN: Final[str] = r"^[A-Za-z0-9+/]+=*$"
ENCODING_HEX_PATTERN: Final[str] = r"^[0-9a-fA-F]+$"
ENCODING_HIGH_ENTROPY_PATTERN: Final[str] = r"[a-z][A-Z]|[A-Z][a-z]|[a-zA-Z][0-9]|[0-9][a-zA-Z]"

# Pre-compiled regex for performance (Python fallback)
IPV4_RE = re.compile(IPV4_PATTERN)
IPV6_RE = re.compile(IPV6_PATTERN)
DOMAIN_RE = re.compile(DOMAIN_PATTERN)
MD5_RE = re.compile(MD5_PATTERN)
SHA1_RE = re.compile(SHA1_PATTERN)
SHA256_RE = re.compile(SHA256_PATTERN)
EMAIL_RE = re.compile(EMAIL_PATTERN)
CVE_RE = re.compile(CVE_PATTERN)
URL_RE = re.compile(URL_PATTERN)
HASH_RE = re.compile(HASH_PATTERN)

ENCODING_BASE32_RE = re.compile(ENCODING_BASE32_PATTERN)
ENCODING_BASE64_RE = re.compile(ENCODING_BASE64_PATTERN)
ENCODING_HEX_RE = re.compile(ENCODING_HEX_PATTERN)
ENCODING_HIGH_ENTROPY_RE = re.compile(ENCODING_HIGH_ENTROPY_PATTERN)
