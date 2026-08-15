"""Error Classification — extracted from public_fetcher.py (ISSUE-014 REFACTOR).

Provides fetch error taxonomy and classification utilities.
Optimized for M1 8GB with O(log n) bisect-based prefix lookup.
"""
from __future__ import annotations

import bisect
import asyncio
from typing import Final
from _core import aclose


# --- Error taxonomy ---
# Exact match map: error prefix → classification
_ERROR_EXACT_MAP: dict[str, str] = {
    'circuit_breaker': 'circuit_breaker_blocked',
    'resource_governor': 'resource_governor_blocked',
    'fetch_text_none_or_empty': 'body_empty',
}

# Sorted prefix list for O(log n) lookup (longest first)
_ERROR_PREFIX_LIST: list[tuple[str, str]] = sorted([
    ('fetch_exception: ClientConnectorCertificateError', 'tls_error'),
    ('fetch_exception: ClientSSLError', 'tls_error'),
    ('fetch_exception: ClientProxyError', 'proxy_error'),
    ('fetch_exception: ClientConnectorError', 'connect_error'),
    ('fetch_exception: asyncio.TimeoutError', 'connect_timeout'),
    ('fetch_exception: TimeoutError', 'read_timeout'),
    ('fetch_timeout_after_', 'connect_timeout'),
    ('content_type_rejected:', 'content_type_rejected'),
], key=lambda x: len(x[0]), reverse=True)

_PREFIX_KEYS: list[str] = [p[0] for p in _ERROR_PREFIX_LIST]


def _lookup_prefix_fast(error_str: str) -> str | None:
    """O(log n) prefix lookup via bisect + early break on startswith."""
    if not error_str:
        return None
    min_len = len(_PREFIX_KEYS[-1]) if _PREFIX_KEYS else 0
    if len(error_str) < min_len:
        return None
    idx = bisect.bisect_right(_PREFIX_KEYS, error_str)
    for i in range(idx - 1, -1, -1):
        if error_str.startswith(_PREFIX_KEYS[i]):
            return _ERROR_PREFIX_LIST[i][1]
        if i + 1 < len(_PREFIX_KEYS) and len(_PREFIX_KEYS[i]) < len(_PREFIX_KEYS[i + 1]):
            break
    return None


# Full taxonomy for classify_fetch_error
_FETCH_ERROR_TAXONOMY: dict[str, str] = {
    'dns_error': 'dns_error',
    'connect_error': 'connect_error',
    'tls_error': 'tls_error',
    'timeout': 'read_timeout',
    'content_type_rejected:': 'content_type_rejected',
    'fetch_text_none_or_empty': 'body_empty',
    'fetch_timeout_after_': 'connect_timeout',
    'fetch_exception: asyncio.TimeoutError': 'connect_timeout',
    'fetch_exception: TimeoutError': 'read_timeout',
    'fetch_exception: ClientConnectorError': 'connect_error',
    'fetch_exception: ClientSSLError': 'tls_error',
    'fetch_exception: ClientProxyError': 'proxy_error',
    'fetch_exception: ClientConnectorCertificateError': 'tls_error',
    'circuit_breaker': 'circuit_breaker_blocked',
    'resource_governor': 'resource_governor_blocked',
}


def derive_failure_stage_and_network_kind(error: str | None) -> tuple[str | None, str | None]:
    """Parse error string to extract structured failure_stage and network_error_kind.

    Returns (failure_stage, network_error_kind).
    Both are None when error is None (success) or for URL-validation errors.

    failure_stage taxonomy:
      - validation  : URL was invalid before any network call
      - connection  : TCP/DNS/connection-level failure (body never reached)
      - tls          : TLS handshake failure
      - http         : HTTP-level failure (response received, non-2xx)
      - body         : headers OK but body read failed mid-stream
      - size         : body truncated due to size cap
      - tarpit       : HTML identified as tarpit/honeypot/link-labyrinth (ISSUE-014)

    network_error_kind (connection/tls only):
      - dns_error    : DNS resolution failure
      - connect_error: TCP connection refused/reset
      - tls_error    : TLS handshake/verification failure
      - timeout      : request timed out
    """
    if error is None:
        return (None, None)
    if error.startswith('url_'):
        return ('validation', None)
    if error == 'timeout':
        return ('connection', 'timeout')
    if error == 'size_cap_exceeded':
        return ('size', None)
    if error.startswith('tarpit_detected:'):
        return ('tarpit', None)
    if error.startswith('content_type_rejected:'):
        return ('http', None)
    if error.startswith('retryable:'):
        return ('http', None)
    if error.startswith('fetch_error;'):
        parts = error.split(';', 2)
        exc_type = parts[1] if len(parts) > 1 else ''
        if 'SSL' in exc_type or 'TLS' in exc_type or 'Certificate' in exc_type:
            return ('tls', 'tls_error')
        if 'DNS' in exc_type or 'Resolver' in exc_type:
            return ('connection', 'dns_error')
        if 'Connect' in exc_type or 'Connection' in exc_type or 'Network' in exc_type:
            return ('connection', 'connect_error')
        return ('connection', 'connect_error')
    return ('body', None)


def _classify_by_status_code(status_code: int) -> str | None:
    """Classify HTTP status code into taxonomy string."""
    match status_code:
        case 403:
            return 'http_403'
        case 404:
            return 'http_404'
        case 429:
            return 'http_429'
        case s if 500 <= s < 600:
            return 'http_5xx'
    return None


def _classify_by_network_kind(network_kind: str) -> str | None:
    """Classify network error kind into taxonomy string."""
    match network_kind:
        case 'tls_error':
            return 'tls_error'
        case 'dns_error':
            return 'dns_error'
        case 'connect_error':
            return 'connect_error'
        case 'timeout':
            return 'read_timeout'
    return None


def _classify_by_failure_stage(failure_stage: str, error_str: str) -> str | None:
    """Classify failure stage into taxonomy string."""
    match failure_stage:
        case 'validation':
            return 'unknown_fetch_error'
        case 'tls':
            return 'tls_error'
        case 'http':
            if 'content_type_rejected' in error_str:
                return 'content_type_rejected'
            return 'unknown_fetch_error'
        case 'size':
            return 'max_bytes_exceeded'
        case 'tarpit':
            return 'tarpit_detected'
    return None


def _classify_error_prefixes(error_str: str) -> str | None:
    """Check circuit_breaker, resource_governor, and prefix map."""
    if 'circuit_breaker' in error_str:
        return 'circuit_breaker_blocked'
    if 'resource_governor' in error_str:
        return 'resource_governor_blocked'
    return _lookup_prefix_fast(error_str)


def _check_success_case(error_str: str, status_code: int, text: str | None) -> str | None:
    """Check if this is a success case (no error, 200, and non-empty body)."""
    if error_str or status_code != 200:
        return None
    if text and not text.strip():
        return 'body_empty'
    return 'none'


def _try_classification_chain(
    error_str: str, status_code: int,
    failure_stage: str, network_kind: str
) -> str | None:
    """Try all classification methods in order. Returns None if no match."""
    if status_result := _classify_by_status_code(status_code):
        return status_result
    if stage_result := _classify_by_failure_stage(failure_stage, error_str):
        return stage_result
    if network_result := _classify_by_network_kind(network_kind):
        return network_result
    if prefix_result := _classify_error_prefixes(error_str):
        return prefix_result
    return None


def _classify_result_object(result) -> str:
    """Classify a FetchResult object."""
    error_str = result.error or ''
    status_code = result.status_code or 0
    # Success case
    if success_result := _check_success_case(error_str, status_code, getattr(result, 'text', None)):
        return success_result
    # CancelledError must be re-raised
    if 'CancelledError' in error_str:
        raise asyncio.CancelledError('fetch cancelled')
    # Try classification chain
    failure_stage = getattr(result, 'failure_stage', '') or ''
    network_kind = getattr(result, 'network_error_kind', '') or ''
    if chain_result := _try_classification_chain(error_str, status_code, failure_stage, network_kind):
        return chain_result
    return 'unknown_fetch_error' if error_str else 'none'


def _classify_error_string(error_str: str) -> str:
    """Classify a raw error string."""
    if 'CancelledError' in error_str:
        raise asyncio.CancelledError('fetch cancelled')
    if not error_str:
        return 'none'
    if prefix_result := _classify_error_prefixes(error_str):
        return prefix_result
    return 'unknown_fetch_error'


def classify_fetch_error(result_or_error) -> str:
    """Classify a fetch outcome into a flat error type string for verdict telemetry.

    Takes a FetchResult (success or failure) or an error string.
    Returns one of the Sprint F206AC taxonomy strings:
      none | dns_error | connect_timeout | read_timeout | tls_error | proxy_error
      | http_403 | http_404 | http_429 | http_5xx | content_type_rejected
      | body_empty | max_bytes_exceeded | circuit_breaker_blocked
      | resource_governor_blocked | task_cancelled | tarpit_detected
      | unknown_fetch_error

    HARD RULE: CancelledError is re-raised, never classified and swallowed.
    """
    if hasattr(result_or_error, 'status_code'):
        return _classify_result_object(result_or_error)
    error_str = str(result_or_error) if result_or_error is not None else ''
    return _classify_error_string(error_str)


def derive_redirect_fields(url: str, final_url: str) -> tuple[bool, str | None]:
    """Return (redirected, redirect_target) based on URL comparison.

    downstream can use redirected=True as explicit signal instead of
    computing final_url != url themselves.
    """
    if final_url != url:
        return (True, final_url)
    return (False, None)
