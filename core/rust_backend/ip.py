# ip.py — IP parsing and classification domain

from typing import TYPE_CHECKING, Any
from core._util import aclose


if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _RustIpDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def parse_ip_fast(self, ip_str: str) -> tuple[int, int] | None:
        return self._ext.parse_ip_fast(ip_str)

    def is_private_ip(self, ip_str: str) -> bool:
        return self._ext.is_private_ip(ip_str)

    def is_public_ip(self, ip_str: str) -> bool:
        return self._ext.is_public_ip(ip_str)

    def batch_ip_classify(self, ips: list[str]) -> list[tuple[str, int]]:
        return self._ext.batch_ip_classify(ips)

    def cidr_contains(self, cidr: str, ip: str) -> bool:
        return self._ext.cidr_contains(cidr, ip)


class _PythonIpDomain:
    """Pure-Python IP parsing and classification fallback."""

    __slots__ = ()

    @staticmethod
    def parse_ip_fast(ip_str: str) -> tuple[int, int] | None:
        return _python_parse_ip_fast(ip_str)

    @staticmethod
    def is_private_ip(ip_str: str) -> bool:
        return _python_is_private_ip(ip_str)

    @staticmethod
    def is_public_ip(ip_str: str) -> bool:
        return _python_is_public_ip(ip_str)

    @staticmethod
    def batch_ip_classify(ips: list[str]) -> list[tuple[str, int]]:
        return _python_batch_ip_classify(ips)

    @staticmethod
    def cidr_contains(cidr: str, ip: str) -> bool:
        return _python_cidr_contains(cidr, ip)


# ------------------------------------------------------------------
# Pure-Python IP helpers (moved from top of rust_backend.py)
# ------------------------------------------------------------------


def _python_parse_ip_fast(ip_str: str) -> tuple[int, int] | None:
    """Parse IPv4 address into (int_repr, version). Returns None on failure."""
    try:
        parts = ip_str.strip().split(".")
        if len(parts) != 4:
            return None
        octets = [int(p) for p in parts]
        if not all(0 <= o <= 255 for o in octets):
            return None
        addr_int = (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]
        return (addr_int, 4)
    except Exception:
        return None


def _python_is_private_ip(ip_str: str) -> bool:
    """Check if IP is private (RFC 1918, loopback, link-local)."""
    try:
        result = _python_parse_ip_fast(ip_str)
        if result is None:
            return False
        addr_int, _ = result
        # 10.0.0.0/8
        if 0x0A000000 <= addr_int <= 0x0AFFFFFF:
            return True
        # 172.16.0.0/12
        if 0xAC100000 <= addr_int <= 0xAC1FFFFF:
            return True
        # 192.168.0.0/16
        if 0xC0A80000 <= addr_int <= 0xC0A8FFFF:
            return True
        # 127.0.0.0/8 (loopback)
        if 0x7F000000 <= addr_int <= 0x7FFFFFFF:
            return True
        # 169.254.0.0/16 (link-local)
        if 0xA9FE0000 <= addr_int <= 0xA9FEFFFF:
            return True
        return False
    except Exception:
        return False


def _python_is_public_ip(ip_str: str) -> bool:
    """Check if IP is public (not private)."""
    return not _python_is_private_ip(ip_str)


def _python_batch_ip_classify(ips: list[str]) -> list[tuple[str, int]]:
    """Classify batch of IPs: (ip_str, is_private)."""
    return [(ip_, 1 if _python_is_private_ip(ip_) else 0) for ip_ in ips]


def _python_cidr_contains(cidr: str, ip: str) -> bool:
    """Check if IP is within CIDR block."""
    try:
        parts = cidr.split("/")
        if len(parts) != 2:
            return False
        network_str, prefix_str = parts
        prefix = int(prefix_str)
        if not (0 <= prefix <= 32):
            return False
        network_result = _python_parse_ip_fast(network_str.strip())
        ip_result = _python_parse_ip_fast(ip.strip())
        if network_result is None or ip_result is None:
            return False
        network_int, _ = network_result
        ip_int, _ = ip_result
        mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
        return (network_int & mask) == (ip_int & mask)
    except Exception:
        return False


def get_domain(ext: object | None) -> _RustIpDomain | _PythonIpDomain:
    if ext is not None:
        return _RustIpDomain(ext)
    return _PythonIpDomain()
