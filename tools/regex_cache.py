"""
Regex cache with LRU for compiled patterns.
Sprint 79a: Avoid recompiling regex patterns in hot paths.
"""

import re
from functools import lru_cache
from re import Pattern


@lru_cache(maxsize=100)
def get_compiled_pattern(pattern: str, flags: int = 0) -> Pattern:
    """
    Get compiled regex pattern with LRU caching.

    Args:
        pattern: Regular expression pattern
        flags: Optional re flags (e.g., re.IGNORECASE, re.DOTALL)

    Returns:
        Compiled regex Pattern object
    """
    return re.compile(pattern, flags)


# Common patterns pre-compiled for hot paths
_IP_PATTERN = get_compiled_pattern(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')
_URL_PATTERN = get_compiled_pattern(
    r'https?://[^\s<>"{}|\\^`\[\]]+',
    re.IGNORECASE
)
_EMAIL_PATTERN = get_compiled_pattern(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
_DOMAIN_PATTERN = get_compiled_pattern(r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b')


def check_ip(text: str) -> bool:
    """Check if text contains an IP address."""
    return _IP_PATTERN.search(text) is not None


def check_url(text: str) -> bool:
    """Check if text contains a URL."""
    return _URL_PATTERN.search(text) is not None


def check_email(text: str) -> bool:
    """Check if text contains an email address."""
    return _EMAIL_PATTERN.search(text) is not None


def check_domain(text: str) -> bool:
    """Check if text contains a domain name."""
    return _DOMAIN_PATTERN.search(text) is not None


def extract_ips(text: str) -> list:
    """Extract all IP addresses from text."""
    return _IP_PATTERN.findall(text)


def extract_urls(text: str) -> list:
    """Extract all URLs from text."""
    return _URL_PATTERN.findall(text)


def extract_emails(text: str) -> list:
    """Extract all email addresses from text."""
    return _EMAIL_PATTERN.findall(text)


def extract_domains(text: str) -> list:
    """Extract all domain names from text."""
    return _DOMAIN_PATTERN.findall(text)
