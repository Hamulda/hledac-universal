"""
IOC Literal Patterns — Shared across Rust backend scanners.

This module contains the canonical list of IOC literal patterns used by:
- _core.rust_backend.ioc_stream (singleton scanner)
- _core.rust_backend.ioc_stream_scan (streaming scanner)

Patterns are organized by category for documentation purposes but
the list itself is flat for performance (avoiding dict lookups).

Note: These are Aho-Corasick LITERALS, not regex. For regex-based
extraction, see runtime/nonfeed_seed_extractor.py.
"""
from __future__ import annotations

# Canonical IOC literal patterns (Aho-Corasick literals)
IOC_LITERALS: list[str] = [
    # IPv4 patterns
    "127.0.0.1",
    "0.0.0.0",
    "255.255.255.255",
    "192.168.",
    "10.0.",
    "172.16.",
    # Domains
    "pastebin.com",
    "github.com",
    "raw.githubusercontent",
    "mega.nz",
    "mediafire.com",
    "dropbox.com",
    # Hashes (partial, caught by length validation)
    "da39a3ee",
    "e3b0c44",
    "58845d3a",
    # Emails
    "@gmail.com",
    "@yahoo.com",
    "@hotmail.com",
    # CVEs
    "CVE-",
    "CVE-202",
    "CVE-201",
    # TLDs
    ".ru",
    ".cn",
    ".tk",
    ".ml",
    ".ga",
    ".cf",
    ".gq",
    # Protocols
    "http://",
    "https://",
    "ftp://",
    "sftp://",
    ".onion",
    "ssh://",
    "telnet://",
    "rdp://",
]

__all__ = ["IOC_LITERALS"]