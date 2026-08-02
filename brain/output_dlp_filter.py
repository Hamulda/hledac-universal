"""
OutputDLPFilter — Centralized Data Loss Prevention filter for report output.

SOVEREIGN-010: Consolidates decentralized _mask_secret() functions from:
  - recon/github_secret_scanner.py:26
  - recon/open_source_collectors.py:141
  - recon/pastebin_monitor.py:39

Provides comprehensive secret detection and masking for:
  - API keys (OpenAI sk_*, GitHub ghp_*, AWS AKIA*, etc.)
  - System paths (~/.hledac/*, internal directories)
  - Internal/private IP addresses
  - Generic secrets (tokens, passwords, credentials)

Always-on filter — no environment variable toggle.
M1 8GB optimized: uses compiled regex patterns and __slots__ for minimal memory footprint.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ['OutputDLPFilter', 'mask_secret', 'DLP_STATS']

# ─── Constants ─────────────────────────────────────────────────────────────────
_SECRET_REDACT_LEN = 4
_MASK_CHAR = '*'

# ─── Compiled Regex Patterns ───────────────────────────────────────────────────
# API Keys & Tokens
_RE_OPENAI_KEY = re.compile(r'\bsk-(?:proj-|ant-)?[a-zA-Z0-9\-_]{20,}\b')
_RE_ANTHROPIC_KEY = re.compile(r'\bsk-ant-[a-zA-Z0-9\-_]{95}\b')
_RE_OPENAI_PROJ_KEY = re.compile(r'\bsk-proj-[a-zA-Z0-9\-_]{100}\b')
_RE_GITHUB_TOKEN = re.compile(r'\bghp_[a-zA-Z0-9]{36}\b')
_RE_GITHUB_FINE = re.compile(r'\bgithub_pat_[a-zA-Z0-9]{22,}\b')
_RE_AWS_ACCESS_KEY = re.compile(r'\bAKIA[0-9A-Z]{16}\b')
_RE_AWS_SECRET_KEY = re.compile(
    r'\b(?:aws)?_?secret_?access?_?key\s*[=:]\s*[\'"]?([A-Za-z0-9/+=]{40})[\'"]?',
    re.IGNORECASE
)
_RE_STRIPE_KEY = re.compile(r'\bsk_live_[0-9a-zA-Z]{24}\b')
_RE_SLACK_TOKEN = re.compile(
    r'\bxox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[A-Za-z0-9]{24,32}\b'
)
_RE_HUGGINGFACE_TOKEN = re.compile(r'\bhf_[a-zA-Z0-9]{34}\b')
_RE_DOPPLER_SECRET = re.compile(r'\bdp\.pt\.[a-zA-Z0-9]{43}\b')
_RE_INFISICAL_TOKEN = re.compile(r'\binf-[a-zA-Z0-9]{43}\b')
_RE_VERCEL_TOKEN = re.compile(r'\b[a-zA-Z0-9]{24,}\b')
_RE_SUPABASE_KEY = re.compile(
    r'\beyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\b'
)
_RE_GOOGLE_API_KEY = re.compile(r'\bAIza[0-9A-Za-z\-_]{35}\b')

# Bearer tokens & generic auth
_RE_BEARER_TOKEN = re.compile(r'\bBearer\s+[A-Za-z0-9_\.\-]{20,}\b', re.IGNORECASE)
_RE_GENERIC_TOKEN = re.compile(
    r'\b(?:token|key|secret|password|passwd|pwd|auth|credential)[\'"]?[:=]?\s*[\'"]?([A-Za-z0-9_\-]{16,64})[\'"]?\b',
    re.IGNORECASE
)
_RE_GENERIC_API_KEY = re.compile(
    r'\b(?:api[_-]?key|apikey|api_secret)\s*[=:]\s*[\'"]?([A-Za-z0-9_\-]{20,64})[\'"]?',
    re.IGNORECASE
)

# Private keys
_RE_PRIVATE_KEY = re.compile(
    r'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----',
    re.IGNORECASE
)

# System paths (internal)
_RE_HLEDAC_PATH = re.compile(r'~/.hledac/[^\s\'"]+|/Users/[^/]+/.hledac/[^\s\'"]+')
_RE_INTERNAL_PATH = re.compile(r'/Users/[a-zA-Z0-9_]+/[^\s\'"]{10,}')

# Network
_RE_IPV4 = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b'
)
_RE_IPV6 = re.compile(r'\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b')
_RE_PRIVATE_IPV4 = re.compile(
    r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b'
)

# Email (for optional masking)
_RE_EMAIL = re.compile(r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b')

# ─── Pattern Registry ──────────────────────────────────────────────────────────
_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    # (name, pattern, replacement)
    ('openai_key', _RE_OPENAI_KEY, 'sk-****'),
    ('anthropic_key', _RE_ANTHROPIC_KEY, 'sk-ant-****'),
    ('openai_proj_key', _RE_OPENAI_PROJ_KEY, 'sk-proj-****'),
    ('github_token', _RE_GITHUB_TOKEN, 'ghp_****'),
    ('github_fine_grained', _RE_GITHUB_FINE, 'github_pat_****'),
    ('aws_access_key', _RE_AWS_ACCESS_KEY, 'AKIA****'),
    ('stripe_key', _RE_STRIPE_KEY, 'sk_live_****'),
    ('slack_token', _RE_SLACK_TOKEN, 'xox*-****'),
    ('huggingface_token', _RE_HUGGINGFACE_TOKEN, 'hf_****'),
    ('doppler_secret', _RE_DOPPLER_SECRET, 'dp.pt.****'),
    ('infisical_token', _RE_INFISICAL_TOKEN, 'inf-****'),
    ('supabase_key', _RE_SUPABASE_KEY, 'eyJ****'),
    ('google_api_key', _RE_GOOGLE_API_KEY, 'AIza****'),
    ('bearer_token', _RE_BEARER_TOKEN, 'Bearer ****'),
    ('private_key', _RE_PRIVATE_KEY, '-----BEGIN PRIVATE KEY-----'),
    ('hledac_path', _RE_HLEDAC_PATH, '~/.hledac/[REDACTED]'),
    ('internal_path', _RE_INTERNAL_PATH, '[INTERNAL_PATH]'),
    ('private_ipv4', _RE_PRIVATE_IPV4, '[PRIVATE_IP]'),
]

# Group capture patterns (need special handling)
_GROUP_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ('aws_secret_key', _RE_AWS_SECRET_KEY, '[AWS_SECRET]'),
    ('generic_token', _RE_GENERIC_TOKEN, '[TOKEN]'),
    ('generic_api_key', _RE_GENERIC_API_KEY, '[API_KEY]'),
]


# ─── Statistics ────────────────────────────────────────────────────────────────
@dataclass
class DLPStats:
    """Statistics for DLP filtering operations."""
    total_redactions: int = 0
    redactions_by_type: dict[str, int] = None

    def __post_init__(self) -> None:
        if self.redactions_by_type is None:
            self.redactions_by_type = {}

    def record(self, pattern_name: str, count: int = 1) -> None:
        self.total_redactions += count
        self.redactions_by_type[pattern_name] = self.redactions_by_type.get(pattern_name, 0) + count


# Global stats instance
DLP_STATS = DLPStats()


# ─── Core Functions ────────────────────────────────────────────────────────────
def mask_secret(value: str, redact_len: int = _SECRET_REDACT_LEN) -> str:
    """
    Mask a secret value by replacing the last N characters with asterisks.

    This is the consolidated replacement for the 3 local _mask_secret() functions.

    Args:
        value: The secret string to mask
        redact_len: Number of characters to mask (default: 4)

    Returns:
        Masked string with last N characters replaced by asterisks

    Examples:
        >>> mask_secret("sk-abc123def456")
        'sk-abc123def****'
        >>> mask_secret("short")
        '*****'
    """
    if len(value) <= redact_len:
        return _MASK_CHAR * len(value)
    return value[:-redact_len] + _MASK_CHAR * redact_len


class OutputDLPFilter:
    """
    Centralized DLP filter for sanitizing report output.

    Scans text content for secrets, API keys, internal paths, and private IPs,
    replacing them with safe placeholders.

    Usage:
        filter = OutputDLPFilter()
        safe_text = filter.sanitize(report_content)

    Attributes:
        mask_emails: Whether to mask email addresses (default: False)
        mask_all_ips: Whether to mask all IP addresses, not just private (default: False)
        mask_internal_paths: Whether to mask internal file paths (default: True)
    """

    __slots__ = ('mask_emails', 'mask_all_ips', 'mask_internal_paths', '_stats')

    def __init__(
        self,
        *,
        mask_emails: bool = False,
        mask_all_ips: bool = False,
        mask_internal_paths: bool = True,
    ) -> None:
        self.mask_emails = mask_emails
        self.mask_all_ips = mask_all_ips
        self.mask_internal_paths = mask_internal_paths
        self._stats = DLPStats()

    @property
    def stats(self) -> DLPStats:
        """Get filtering statistics."""
        return self._stats

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self._stats = DLPStats()

    def sanitize(self, text: str) -> str:
        """
        Sanitize text by masking all detected secrets and sensitive data.

        This is the main entry point for DLP filtering. Applies all configured
        patterns in order of specificity (most specific first).

        Args:
            text: The text content to sanitize

        Returns:
            Sanitized text with secrets replaced by safe placeholders
        """
        if not text:
            return text

        result = text

        # Phase 1: Apply simple replacement patterns
        for name, pattern, replacement in _PATTERNS:
            # Skip internal path patterns if disabled
            if not self.mask_internal_paths and name in ('hledac_path', 'internal_path'):
                continue

            matches = pattern.findall(result)
            if matches:
                result = pattern.sub(replacement, result)
                self._stats.record(name, len(matches) if isinstance(matches, list) else 1)

        # Phase 2: Apply group capture patterns (need special handling)
        for name, pattern, replacement in _GROUP_PATTERNS:
            matches = pattern.findall(result)
            if matches:
                result = pattern.sub(replacement, result)
                self._stats.record(name, len(matches) if isinstance(matches, list) else 1)

        # Phase 3: Optional email masking
        if self.mask_emails:
            emails = _RE_EMAIL.findall(result)
            if emails:
                result = _RE_EMAIL.sub('[EMAIL]', result)
                self._stats.record('email', len(emails))

        # Phase 4: Optional full IP masking
        if self.mask_all_ips:
            ipv4s = _RE_IPV4.findall(result)
            if ipv4s:
                result = _RE_IPV4.sub('[IP]', result)
                self._stats.record('ipv4', len(ipv4s))

            ipv6s = _RE_IPV6.findall(result)
            if ipv6s:
                result = _RE_IPV6.sub('[IPV6]', result)
                self._stats.record('ipv6', len(ipv6s))

        return result

    def sanitize_dict(self, data: dict[str, Any], *, recursive: bool = True) -> dict[str, Any]:
        """
        Sanitize all string values in a dictionary.

        Args:
            data: Dictionary to sanitize
            recursive: Whether to recursively sanitize nested dicts/lists

        Returns:
            New dictionary with sanitized values
        """
        result: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.sanitize(value)
            elif recursive and isinstance(value, dict):
                result[key] = self.sanitize_dict(value, recursive=True)
            elif recursive and isinstance(value, list):
                result[key] = self._sanitize_list(value)
            else:
                result[key] = value
        return result

    def _sanitize_list(self, items: list[Any]) -> list[Any]:
        """Sanitize all string values in a list."""
        result: list[Any] = []
        for item in items:
            if isinstance(item, str):
                result.append(self.sanitize(item))
            elif isinstance(item, dict):
                result.append(self.sanitize_dict(item, recursive=True))
            elif isinstance(item, list):
                result.append(self._sanitize_list(item))
            else:
                result.append(item)
        return result

    def scan(self, text: str) -> list[dict[str, Any]]:
        """
        Scan text for secrets without modifying it.

        Returns a list of findings with pattern name, match text, and position.

        Args:
            text: Text to scan

        Returns:
            List of finding dicts with keys: pattern, match, start, end
        """
        findings: list[dict[str, Any]] = []

        for name, pattern, _ in _PATTERNS:
            if not self.mask_internal_paths and name in ('hledac_path', 'internal_path'):
                continue

            for match in pattern.finditer(text):
                findings.append({
                    'pattern': name,
                    'match': match.group(),
                    'start': match.start(),
                    'end': match.end(),
                })

        for name, pattern, _ in _GROUP_PATTERNS:
            for match in pattern.finditer(text):
                findings.append({
                    'pattern': name,
                    'match': match.group(),
                    'start': match.start(),
                    'end': match.end(),
                })

        if self.mask_emails:
            for match in _RE_EMAIL.finditer(text):
                findings.append({
                    'pattern': 'email',
                    'match': match.group(),
                    'start': match.start(),
                    'end': match.end(),
                })

        return findings


# ─── Module-level convenience function ─────────────────────────────────────────
_default_filter: OutputDLPFilter | None = None


def get_dlp_filter() -> OutputDLPFilter:
    """Get the singleton DLP filter instance."""
    global _default_filter
    if _default_filter is None:
        _default_filter = OutputDLPFilter()
    return _default_filter


def sanitize_text(text: str) -> str:
    """
    Convenience function to sanitize text using the default DLP filter.

    Args:
        text: Text to sanitize

    Returns:
        Sanitized text
    """
    return get_dlp_filter().sanitize(text)
