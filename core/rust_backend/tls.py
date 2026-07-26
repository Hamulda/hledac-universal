# tls.py — TLS 1.3 JA4 fingerprinting domain
"""
TLS 1.3 fingerprinting via Rust rustls (JA4 algorithm).
Tier 1: Rust tls13 module (rustls-based, <1ms per fingerprint)
Tier 2: Python ssl analysis (fallback, less accurate)

JA4 = Salesforce TLS fingerprint: 13-char string derived from
TLS ClientHello during handshake (version, cipher suites, extensions, ALPN).

Integration:
    from core.rust_backend import rust
    result = rust.tls.connect_and_ja4("example.com", 443)
"""

from __future__ import annotations

import ssl
import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

# Availability flag — set once at module load
_TLS13_RUST_AVAILABLE = False

try:
    from hledac.universal.rust_extensions import tls13 as _tls13_rust

    # Check TLS13_AVAILABLE constant from Rust module
    try:
        _TLS13_RUST_AVAILABLE = getattr(_tls13_rust, "TLS13_AVAILABLE", False)
    except Exception:
        _TLS13_RUST_AVAILABLE = False
except ImportError:
    _tls13_rust = None  # type: ignore[assignment]


# =============================================================================
# TLS Domain
# =============================================================================


class _RustTlsDomain:
    """Rust-backed TLS 1.3 fingerprinting via rustls.

    JA4 algorithm: extracts TCP fingerprint from TLS ClientHello in <1ms.
    ECH (Encrypted Client Hello) detection included.
    """

    __slots__ = ("_ext",)

    def __init__(self, ext: object) -> None:
        self._ext = ext

    def ja4_from_client_hello(self, chello_hex: str) -> str:
        """Compute JA4 from raw ClientHello bytes (hex-encoded)."""
        return self._ext.ja4_from_client_hello(chello_hex)

    def ja4_from_client_hello_bytes(self, chello_bytes: bytes) -> str:
        """Compute JA4 from raw ClientHello bytes (binary)."""
        return self._ext.ja4_from_client_hello_bytes(chello_bytes)

    def connect_and_ja4(
        self,
        host: str,
        port: int = 443,
        sni: str | None = None,
        alpn: list[str] | None = None,
        timeout_ms: int = 5000,
    ) -> dict[str, Any]:
        """
        Connect to server and extract JA4 fingerprint + ECH detection.

        Returns dict with keys: ja4, ech_detected, tls_version, server_ciphers,
        server_extensions, alpn, cert_verified
        """
        return self._ext.connect_and_ja4(host, port, sni=sni, alpn=alpn, timeout_ms=timeout_ms)

    def batch_ja4(
        self,
        hosts: list[tuple[str, int]],
        snis: list[str] | None = None,
        alpn: list[str] | None = None,
        timeout_ms: int = 5000,
    ) -> list[dict[str, Any]]:
        """Batch JA4 for multiple hosts in parallel (max 8 concurrent)."""
        return self._ext.batch_ja4(hosts, snis=snis, alpn=alpn, timeout_ms=timeout_ms)


class _PythonTlsDomain:
    """Python TLS fingerprinting via stdlib ssl.

    Less accurate than Rust JA4 (doesn't parse ClientHello directly),
    but provides basic TLS version and cipher suite info.
    Used when Rust tls13 module unavailable.
    """

    __slots__ = ()

    async def connect_and_ja4(
        self,
        host: str,
        port: int = 443,
        sni: str | None = None,  # noqa: ARG002 — reserved for future SNI override
        alpn: list[str] | None = None,  # noqa: ARG002 — reserved for future ALPN override
        timeout_ms: int = 5000,
    ) -> dict[str, Any]:
        """Python fallback: basic TLS analysis via stdlib ssl."""
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            async with asyncio.timeout(timeout_ms / 1000):
                _reader, writer = await asyncio.open_connection(host, port, ssl=context)

            ssl_socket = writer.get_extra_info("ssl_object")
            if not ssl_socket:
                writer.close()
                await writer.wait_closed()
                return _make_error_result(host, port, "No SSL socket")

            # Get TLS version
            tls_version = getattr(ssl_socket, "version", lambda: "unknown")()
            if callable(tls_version):
                tls_version = "unknown"

            # Get cipher suite
            cipher = getattr(ssl_socket, "cipher", lambda: None)()
            if callable(cipher):
                cipher = None
            server_ciphers = [cipher[0]] if cipher else []

            # Get ALPN
            alpn_result = None
            if hasattr(ssl_socket, "selected_alpn_protocol"):
                try:
                    alpn_result = ssl_socket.selected_alpn_protocol()
                except Exception:
                    alpn_result = None

            writer.close()
            await writer.wait_closed()

            return {
                "host": host,
                "port": port,
                "ja4": "",  # Python ssl doesn't expose ClientHello for JA4
                "ech_detected": False,
                "tls_version": tls_version or "unknown",
                "server_ciphers": server_ciphers,
                "server_extensions": [],
                "alpn": alpn_result,
                "cert_verified": False,
                "error": "",
            }

        except asyncio.TimeoutError:
            return _make_error_result(host, port, "Timeout")
        except ConnectionRefusedError:
            return _make_error_result(host, port, "Connection refused")
        except Exception as e:
            return _make_error_result(host, port, str(e))

    async def ja4_from_client_hello(self, chello_hex: str) -> str:  # noqa: ARG002
        """Python cannot compute JA4 without ClientHello parsing."""
        raise NotImplementedError(
            "JA4 computation requires Rust tls13 module. "
            "Build with: maturin develop --features tls13"
        )

    async def ja4_from_client_hello_bytes(self, chello_bytes: bytes) -> str:  # noqa: ARG002
        """Python cannot compute JA4 without ClientHello parsing."""
        raise NotImplementedError(
            "JA4 computation requires Rust tls13 module. "
            "Build with: maturin develop --features tls13"
        )

    async def batch_ja4(
        self,
        hosts: list[tuple[str, int]],
        snis: list[str] | None = None,
        alpn: list[str] | None = None,
        timeout_ms: int = 5000,
    ) -> list[dict[str, Any]]:
        """Batch JA4 via asyncio.gather."""
        tasks = [
            self.connect_and_ja4(host, port, timeout_ms=timeout_ms)
            for host, port in hosts
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, dict)]


def _make_error_result(host: str, port: int, error: str) -> dict[str, Any]:
    """Create error result dict."""
    return {
        "host": host,
        "port": port,
        "ja4": "",
        "ech_detected": False,
        "tls_version": "",
        "server_ciphers": [],
        "server_extensions": [],
        "alpn": "",
        "cert_verified": False,
        "error": error,
    }


def get_tls_domain(ext: object | None) -> _RustTlsDomain | _PythonTlsDomain:
    """Factory: return Rust or Python TlsDomain based on ext availability."""
    if ext is not None and _TLS13_RUST_AVAILABLE:
        try:
            return _RustTlsDomain(ext)
        except Exception:
            pass
    return _PythonTlsDomain()
