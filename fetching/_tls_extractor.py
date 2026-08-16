"""TLS Metadata Extraction — extracted from public_fetcher.py (ISSUE-014 REFACTOR).

Provides TLS certificate extraction and parsing utilities.
Optimized for M1 8GB with Rust acceleration for SAN/issuer processing.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from _core import aclose

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def extract_tls_metadata_from_response(resp) -> dict:
    """
    Extract TLS certificate metadata and Server header from an HTTP response.
    For aiohttp response: resp is aiohttp.ClientResponse
    For httpx response: resp is httpx.Response

    Architecture (Issue B5 / Issue-9):
        - Python pre-fetches raw SSL object via short-circuit getattr chain
        - Python parses dict form of getpeercert() -> san_entries + issuer_org
        - Rust does SAN cap (20) + issuer cap (200) + SHA-256 in a single call
        - Server header: plain Python (no Rust needed)
    Memory bounds: all collections are bounded, fail-safe throughout.
    """
    from hledac.universal._core.rust_backend import rust as _rust_backend

    result: dict = {
        'tls_cert_san': (),
        'tls_cert_issuer': None,
        'tls_cert_sha256': None,
        'server_header': None
    }

    try:
        server = resp.headers.get('Server') or resp.headers.get('server')
        if server:
            result['server_header'] = server[:200]
    except Exception:  # noqa: BLE001 — best-effort
        pass

    # --- SSL object extraction via short-circuit getattr chain ---
    ssl_obj = getattr(resp, 'connection', None) or getattr(resp, '_ssl', None)
    if ssl_obj is None and hasattr(resp, 'transport'):
        try:
            ssl_obj = resp.transport.get_extra_info('ssl_object')
        except Exception:  # noqa: BLE001 — best-effort
            pass
    if ssl_obj is None:
        return result

    # --- Certificate extraction (dict form + DER bytes) — independent try/except ---
    cert_dict: dict | None = None
    try:
        cert_dict = ssl_obj.getpeercert()
    except Exception:  # noqa: BLE001 — best-effort
        pass
    der_bytes: bytes | None = None
    try:
        der_bytes = ssl_obj.getpeercert(binary_form=True)
    except Exception:  # noqa: BLE001 — best-effort
        pass

    # --- Parse cert_dict → san_entries + issuer_org (Python-side cap prevents OOM from malicious certs) ---
    issuer_org: str | None = None
    san_entries: list[tuple[int, str]] = []
    if cert_dict:
        san_list = cert_dict.get('subjectAltName', [])
        for typ, val in san_list:
            if not isinstance(val, (str, bytes)):
                continue
            if len(san_entries) >= 100:   # cap before Rust call — malicious certs can have 10k+ SANs
                break
            san_entries.append((typ, val) if isinstance(val, str) else (typ, val.decode('utf-8', errors='replace')))
        subject = cert_dict.get('subject', ())
        for rdn in subject:
            for k, v in rdn:
                if k == 'organizationName':
                    issuer_org = v if isinstance(v, str) else str(v) if isinstance(v, bytes) else str(v)
                    break
            if issuer_org:
                break

    # --- Rust: SAN cap (20) + issuer cap (200) + SHA-256 in a single call ---
    try:
        sans, issuer, sha256 = _rust_backend.tls.extract_tls_metadata(san_entries, issuer_org, der_bytes)
        result['tls_cert_san'] = tuple(sans)
        result['tls_cert_issuer'] = issuer
        result['tls_cert_sha256'] = sha256
    except Exception:  # noqa: BLE001 — best-effort
        logger.debug("Rust TLS metadata extraction failed, TLS cert data unavailable", exc_info=True)

    return result


def extract_server_header(resp) -> str | None:
    """Extract Server header from HTTP response."""
    try:
        server = resp.headers.get('Server') or resp.headers.get('server')
        if server:
            return server[:200]
    except Exception:  # noqa: BLE001 — best-effort
        pass
    return None
