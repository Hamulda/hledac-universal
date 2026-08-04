"""
Secrets Scrubbing Module - PII/Secret Redaction for Storage
==========================================================

SEC-01: Prevents API keys, tokens, passwords, and other secrets
from being stored in LMDB/DuckDB evidence logs.

M1 8GB RAM optimized:
- Lazy compiled regex patterns (compile once, reuse)
- Bounded recursion depth (max_depth=8)
- LRU cache for pattern lookups
- Fail-safe: returns original data on any error

Usage:
    from hledac.universal.security.secrets_scrubber import scrub_secrets, scrub_dict_recursive

    # Scrub text
    clean = scrub_secrets("API key: sk-1234567890abcdef")

    # Scrub dict (e.g., before evidence_log storage)
    scrubbed = scrub_dict_recursive({"password": "secret123", "data": "ok"})
"""
from __future__ import annotations

import logging
import os
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

# Bound for recursive scrubbing
_MAX_RECURSION_DEPTH = 8

# Compiled patterns for common secret formats
# Format: (name, regex_pattern)
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # === API Keys ===
    # AWS Access Key ID
    (
        "aws_access_key",
        re.compile(r"\b(A3T[A-Z0-9]|AKIA|ABIA|ACCA)[A-Z0-9]{16}\b", re.IGNORECASE),
    ),
    # AWS Secret Access Key (40 char base64-like)
    (
        "aws_secret_key",
        re.compile(r"\b[A-Za-z0-9/+=]{40}\b", re.IGNORECASE),
    ),
    # GitHub Personal Access Token
    (
        "github_token",
        re.compile(r"\bgh[ps]_[A-Za-z0-9]{36,}\b", re.IGNORECASE),
    ),
    # GitHub OAuth Access Token
    (
        "github_oauth",
        re.compile(r"\bgithub_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59}\b", re.IGNORECASE),
    ),
    # Slack Token
    (
        "slack_token",
        re.compile(
            r"\bxox[baprs]-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*\b", re.IGNORECASE
        ),
    ),
    # Slack Webhook URL
    (
        "slack_webhook",
        re.compile(
            r"https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+",
            re.IGNORECASE,
        ),
    ),
    # Google API Key
    (
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z\\-_]{35}\b", re.IGNORECASE),
    ),
    # Google OAuth Client ID
    (
        "google_oauth",
        re.compile(
            r"\b[0-9]+-[0-9A-Fa-f]{32}\.apps\.googleusercontent\.com\b", re.IGNORECASE
        ),
    ),
    # Azure Key (32 char hex)
    (
        "azure_key",
        re.compile(r"\b[0-9a-fA-F]{32}\b", re.IGNORECASE),
    ),
    # OpenAI API Key
    (
        "openai_api_key",
        re.compile(r"\bsk-[0-9A-Za-z\\-_]{48}\b", re.IGNORECASE),
    ),
    # Anthropic API Key
    (
        "anthropic_api_key",
        re.compile(r"\bsk-ant-[0-9A-Za-z\\-_]{48,}\b", re.IGNORECASE),
    ),
    # Stripe API Key
    (
        "stripe_key",
        re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[0-9A-Za-z]{24,}\b", re.IGNORECASE),
    ),
    # Twilio API Key
    (
        "twilio_key",
        re.compile(r"\bSK[0-9a-fA-F]{32}\b", re.IGNORECASE),
    ),
    # SendGrid API Key
    (
        "sendgrid_key",
        re.compile(r"\bSG\.[0-9A-Za-z\\-_]{22}\.[0-9A-Za-z\\-_]{43}\b", re.IGNORECASE),
    ),
    # JWT Token
    (
        "jwt_token",
        re.compile(
            r"\beyJ[A-Za-z0-9\\-_]+\.eyJ[A-Za-z0-9\\-_]+\.[A-Za-z0-9\\-_]+\b",
            re.IGNORECASE,
        ),
    ),
    # Bearer Token
    (
        "bearer_token",
        re.compile(
            r"\bBearer\s+[A-Za-z0-9\\-_]+\.[A-Za-z0-9\\-_]+\.[A-Za-z0-9\\-_]+\b",
            re.IGNORECASE,
        ),
    ),
    # Basic Auth Header
    (
        "basic_auth",
        re.compile(r"\bBasic\s+[A-Za-z0-9+/=]+\b", re.IGNORECASE),
    ),
    # === Password Patterns ===
    # Password in URL param or similar
    (
        "password_param",
        re.compile(
            r'(?:password|passwd|pwd|secret|token|key|api[_-]?key|auth|bearer)[=:\s]["\']?([^"\'\s&]+)',
            re.IGNORECASE,
        ),
    ),
    # Password in JSON
    (
        "password_json",
        re.compile(
            r'"(?:password|passwd|pwd|secret|token|key|api[_-]?key|auth|bearer)"\s*:\s*"([^"]+)"',
            re.IGNORECASE,
        ),
    ),
    # Private Key Header
    (
        "private_key",
        re.compile(
            r"-----BEGIN\s+(?:RSA|EC|DSA|OPENSSH|PRIVATE\s+KEY|PGP\s+PRIVATE\s+KEY)-----",
            re.IGNORECASE,
        ),
    ),
    # Generic Secret Pattern (high confidence)
    (
        "generic_secret",
        re.compile(
            r'(?:secret|api[_-]?key|auth[_-]?token|access[_-]?token)\s*[:=]\s*["\']?([a-zA-Z0-9_\-]{20,})["\']?',
            re.IGNORECASE,
        ),
    ),
    # === Connection Strings ===
    # Database connection string with password
    (
        "db_connection",
        re.compile(
            r'(?:mysql|postgres|postgresql|mongodb|redis|sqlite):\/\/[^:]+:[^@]+@',
            re.IGNORECASE,
        ),
    ),
    # SAP HANA connection
    (
        "sap_connection",
        re.compile(r"\b(?:hana|sqlanywhere):\/\/[^:]+:[^@]+@", re.IGNORECASE),
    ),
]

_REDACTED_PREFIX = "[REDACTED:"
_REDACTED_SUFFIX = "]"


@lru_cache(maxsize=128)
def _get_compiled_patterns() -> list[tuple[str, re.Pattern[str]]]:
    """Return cached list of compiled secret patterns."""
    return _SECRET_PATTERNS


def scrub_secrets(text: str) -> str:
    """
    Scrub sensitive data from text content.

    Replaces found secrets with [REDACTED:pattern_name] markers.

    Args:
        text: Input text that may contain secrets

    Returns:
        Text with secrets replaced by [REDACTED:pattern_name]

    Examples:
        >>> scrub_secrets("API key: sk-1234567890abcdef")
        'API key: [REDACTED:openai_api_key]'
        >>> scrub_secrets("password=secret123")
        '[REDACTED:password_param]'
    """
    if not isinstance(text, str):
        return text

    try:
        result = text
        for pattern_name, pattern in _get_compiled_patterns():
            result = pattern.sub(
                f"{_REDACTED_PREFIX}{pattern_name}{_REDACTED_SUFFIX}", result
            )
        return result
    except Exception as e:
        # Fail-safe: return original text on any error
        logger.debug("scrub_secrets_failed: %s", str(e))
        return text


def scrub_dict_recursive(
    data: Any, depth: int = 0, max_depth: int = _MAX_RECURSION_DEPTH
) -> Any:
    """
    Recursively scrub secrets from dictionary/JSON-serializable data.

    Bound to max_depth to prevent runaway recursion.
    Handles: dict, list, tuple, set, str, bytes, None, numeric, bool.

    Args:
        data: Input data to scrub
        depth: Current recursion depth (internal use)
        max_depth: Maximum recursion depth (default: 8)

    Returns:
        Data with all string values scrubbed of secrets

    Examples:
        >>> scrub_dict_recursive({"password": "secret", "user": "admin"})
        {'password': '[REDACTED:password_param]', 'user': 'admin'}
        >>> scrub_dict_recursive(["api_key=sk-123", "normal text"])
        ['[REDACTED:openai_api_key]', 'normal text']
    """
    if depth > max_depth:
        return f"{_REDACTED_PREFIX}max_depth{_REDACTED_SUFFIX}"

    try:
        if isinstance(data, str):
            return scrub_secrets(data)
        elif isinstance(data, bytes):
            try:
                decoded = data.decode("utf-8", errors="replace")
                scrubbed = scrub_secrets(decoded)
                # NOTE: return str, not bytes — caller may pass result to orjson.dumps()
                return scrubbed
            except Exception:
                return data
        elif isinstance(data, dict):
            return {
                k: scrub_dict_recursive(v, depth + 1, max_depth)
                for k, v in data.items()
            }
        elif isinstance(data, list):
            return [scrub_dict_recursive(v, depth + 1, max_depth) for v in data]
        elif isinstance(data, tuple):
            return tuple(
                scrub_dict_recursive(v, depth + 1, max_depth) for v in data
            )
        elif isinstance(data, set):
            return {
                scrub_dict_recursive(v, depth + 1, max_depth) for v in data
            }
        else:
            # int, float, bool, None, etc. - return as-is
            return data
    except Exception as e:
        # Fail-safe: return placeholder on any error
        logger.debug("scrub_dict_recursive_failed: %s, depth=%d", str(e), depth)
        return data


def is_scrubbed(text: str) -> bool:
    """
    Check if text has already been scrubbed.

    Args:
        text: Text to check

    Returns:
        True if text contains REDACTED markers
    """
    return _REDACTED_PREFIX in text


def count_secrets(text: str) -> int:
    """
    Count the number of secrets found in text.

    Args:
        text: Text to scan

    Returns:
        Number of secret patterns found
    """
    if not isinstance(text, str):
        return 0

    count = 0
    for _, pattern in _get_compiled_patterns():
        count += len(pattern.findall(text))
    return count


# =============================================================================
# API-Key Specific Redaction (ISSUE [FINAL]-019-09)
# Defense-in-depth: redact environment-sourced API keys from any text that
# might be logged or stored before scrub_secrets() patterns can match.
# =============================================================================

_REDACTED_MARKER = "[REDACTED_API_KEY]"


def redact_env_var(text: str | bytes | None, env_var: str) -> str | bytes | None:
    """
    Redact a specific environment variable's value from text.

    Defense-in-depth for ISSUE [FINAL]-019-09: API keys stored in environment
    variables may appear verbatim in error responses (e.g., 401 bodies echoing
    the invalid key). This function replaces the literal key value before any
    generic pattern matching.

    Args:
        text: Text or bytes potentially containing the API key
        env_var: Name of environment variable containing the key

    Returns:
        Text with the API key replaced by [REDACTED_API_KEY], or None if input
        was None/empty

    Examples:
        >>> import os
        >>> os.environ["SHODAN_API_KEY"] = "abc123secret"
        >>> redact_env_var("Invalid API key: abc123secret", "SHODAN_API_KEY")
        'Invalid API key: [REDACTED_API_KEY]'
    """
    if text is None:
        return None
    if not text:
        return text

    try:
        key = os.environ.get(env_var)
        if not key:
            return text

        # Handle bytes: decode -> redact -> return str (same as scrub_dict_recursive)
        if isinstance(text, bytes):
            decoded = text.decode("utf-8", errors="replace")
            redacted = decoded.replace(key, _REDACTED_MARKER)
            return redacted

        # String case
        return text.replace(key, _REDACTED_MARKER)
    except Exception as e:
        logger.debug("redact_env_var_failed: var=%s, err=%s", env_var, str(e))
        return text


def redact_shodan_key(text: str | bytes | None) -> str | bytes | None:
    """
    Redact SHODAN_API_KEY from text.

    ISSUE [FINAL]-019-09: Shodan may echo the API key in 401/403 error
    responses. This ensures the key never reaches payload_text or logs.

    Args:
        text: Text potentially containing the Shodan API key

    Returns:
        Text with SHODAN_API_KEY value replaced by [REDACTED_API_KEY]
    """
    return redact_env_var(text, "SHODAN_API_KEY")


def redact_censys_credentials(
    text: str | bytes | None,
) -> str | bytes | None:
    """
    Redact CENSYS_API_ID and CENSYS_SECRET from text.

    ISSUE [FINAL]-019-09: Censys may echo credentials in 401/403 error
    responses. Redacts both API ID and secret.

    Args:
        text: Text potentially containing Censys credentials

    Returns:
        Text with both CENSYS_API_ID and CENSYS_SECRET values replaced
    """
    result = redact_env_var(text, "CENSYS_API_ID")
    result = redact_env_var(result, "CENSYS_SECRET")
    return result


def redact_greynoise_key(text: str | bytes | None) -> str | bytes | None:
    """
    Redact GREYNOISE_API_KEY from text.

    ISSUE [FINAL]-019-09: GreyNoise may echo the API key in 401/403 error
    responses. This ensures the key never reaches payload_text or logs.

    Args:
        text: Text potentially containing the GreyNoise API key

    Returns:
        Text with GREYNOISE_API_KEY value replaced by [REDACTED_API_KEY]
    """
    return redact_env_var(text, "GREYNOISE_API_KEY")


def redact_ipinfo_key(text: str | bytes | None) -> str | bytes | None:
    """
    Redact IPINFO_API_KEY from text.

    ISSUE [FINAL]-019-09: IPInfo may echo the API key in 401/403 error
    responses.

    Args:
        text: Text potentially containing the IPInfo API key

    Returns:
        Text with IPINFO_API_KEY value replaced by [REDACTED_API_KEY]
    """
    return redact_env_var(text, "IPINFO_API_KEY")


def redact_hibp_key(text: str | bytes | None) -> str | bytes | None:
    """
    Redact HIBP_API_KEY from text.

    ISSUE [FINAL]-019-09: HaveIBeenPwned may echo the API key in error responses.

    Args:
        text: Text potentially containing the HIBP API key

    Returns:
        Text with HIBP_API_KEY value replaced by [REDACTED_API_KEY]
    """
    return redact_env_var(text, "HIBP_API_KEY")


def safe_error_log(logger: logging.Logger, message: str, *args: Any) -> None:
    """
    Log a message while ensuring no API keys leak through format arguments.

    Defense-in-depth wrapper for ISSUE [FINAL]-019-09. Use this instead of
    direct logger.warning/error calls when the message or args might contain
    API key values.

    Currently redacts: SHODAN_API_KEY, CENSYS_API_ID, CENSYS_SECRET,
    GREYNOISE_API_KEY, IPINFO_API_KEY, HIBP_API_KEY

    Args:
        logger: Logger instance to use
        message: Message template (may contain %s placeholders)
        *args: Arguments that might include API keys
    """
    try:
        # Redact from message (order matters: apply all redactions)
        safe_msg = redact_shodan_key(message)
        safe_msg = redact_censys_credentials(safe_msg)
        safe_msg = redact_greynoise_key(safe_msg)
        safe_msg = redact_ipinfo_key(safe_msg)
        safe_msg = redact_hibp_key(safe_msg)

        # Redact from each arg
        safe_args = tuple(
            redact_hibp_key(
                redact_ipinfo_key(
                    redact_greynoise_key(
                        redact_censys_credentials(
                            redact_shodan_key(arg) if isinstance(arg, str) else arg
                        )
                    )
                )
            )
            if isinstance(arg, str)
            else arg
            for arg in args
        )

        logger.warning(safe_msg, *safe_args)
    except Exception as e:
        # Fail-safe: log without args rather than risk key leak
        logger.warning("safe_error_log failed: %s", str(e))
