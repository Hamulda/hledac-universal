"""
core/ioc_patterns.py — Centralizované IOC regex patterns
=========================================================

Single source of truth pro Python IOC regex. Rust strana žije v
rust_extensions/src/ioc_patterns.rs — obě verze musí zůstat synchronní.

Vzor: Jeden regex string, dvě implementace (Rust = primární pro high-performance,
Python = fallback pro integrační body).

Pattern design rules:
- Hash patterns (MD5/SHA1/SHA256) používají \b word boundaries
- RegexSet-kompatibilní patterns (bez \b) jsou označeny has_boundary=False
- Všechny patterns jsou kompilovány jednou na úrovni modulu

GHOST_INVARIANTS:
- fail-safe: any error → returns []
- bounded: žádné limitované kolekce (readonly data)
- no blocking: pure Python re, no I/O
- always-on: žádný feature flag

Usage:
    from hledac.universal._core.ioc_patterns import (
        IPV4_RE, IPV6_RE, DOMAIN_RE,
        MD5_RE, SHA1_RE, SHA256_RE,
        EMAIL_RE, CVE_RE, URL_RE,
        HASH_RE,  # kombinovaný MD5|SHA1|SHA256
    )
"""

import re
from typing import Final
from _core._util import aclose

__all__ = [
    "IPV4_RE",
    "IPV6_RE",
    "DOMAIN_RE",
    "MD5_RE",
    "SHA1_RE",
    "SHA256_RE",
    "EMAIL_RE",
    "CVE_RE",
    "URL_RE",
    "HASH_RE",  # kombinovaný
]

# =============================================================================
# Single source of truth — synchronní s rust_extensions/src/ioc_patterns.rs
# =============================================================================

# IPv4:全班 octet matching, bez word boundary (Prefix match nutný pro RegexSet)
IPV4_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
    )

# IPv6: full form only (:: compression nepokryta, dostatečná pro IoC)
IPV6_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}",
    re.IGNORECASE,
    )

# Domain: word boundary + multi-label, lowercase normalization required
DOMAIN_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b",
    re.IGNORECASE,
    )

# MD5: 32 hex digits, word boundary nutný (prefix match)
MD5_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[a-fA-F0-9]{32}\b",
    re.IGNORECASE,
    )

# SHA1: 40 hex digits, word boundary nutný
SHA1_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[a-fA-F0-9]{40}\b",
    re.IGNORECASE,
    )

# SHA256: 64 hex digits, word boundary nutný
SHA256_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[a-fA-F0-9]{64}\b",
    re.IGNORECASE,
    )

# Email: standard format, word boundary
EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    )

# CVE: bez word boundary (prefix pattern, CVE-2023-12345 form)
CVE_RE: Final[re.Pattern[str]] = re.compile(
    r"CVE-\d{4}-\d{4,}"
    )

# URL: http(s) scheme, without word boundary (inline match)
URL_RE: Final[re.Pattern[str]] = re.compile(
    r"https?://[^\s<>\"']+",
    re.IGNORECASE,
    )

# HASH_RE: kombinovaný MD5|SHA1|SHA256 pro workflow_orchestrator kompatibilitu
HASH_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:[a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64})\b",
    re.IGNORECASE,
    )
