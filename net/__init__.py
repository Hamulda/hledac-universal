"""
Network Utilities — IP, ASN, GeoIP Parsing
=========================================

Sprint 8.7: Naming overlap resolution

PURPOSE:
  One-stop shop for network-layer utilities previously scattered across
  network/ and intelligence/. Max 500 lines.

CONTENTS:
  - IP parsing and classification (IPv4/IPv6)
  - ASN lookup helpers (no external deps)
  - GeoIP country code from IP (struct-based, no DB required)
  - Network range utilities (CIDR parsing, overlap detection)
  - IP reputation scoring (basic heuristic)

INVARIANTS:
  [N-1] No network I/O in this module
  [N-2] No external dependencies (pure Python)
  [N-3] All functions are sync (network I/O belongs in transport/ or intelligence/)
"""
from __future__ import annotations


import ipaddress
import re
import struct
from dataclasses import dataclass
from collections.abc import Iterator


# --- IP Utilities ---

def is_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
        return True
    except ValueError:
        return False


def is_ipv6(value: str) -> bool:
    try:
        ipaddress.IPv6Address(value)
        return True
    except ValueError:
        return False


def is_ip(value: str) -> bool:
    return is_ipv4(value) or is_ipv6(value)


def classify_ip(value: str) -> str:
    """Return 'ipv4', 'ipv6', or 'invalid'."""
    if is_ipv4(value):
        return "ipv4"
    if is_ipv6(value):
        return "ipv6"
    return "invalid"


def is_private_ip(value: str) -> bool:
    """True if IP is in private/reserved range."""
    try:
        ip = ipaddress.ip_address(value)
        return ip.is_private or ip.is_reserved or ip.is_loopback
    except ValueError:
        return False


def is_bogon(value: str) -> bool:
    """True if IP is bogon (should not appear in public internet)."""
    try:
        ip = ipaddress.ip_address(value)
        return (
            ip.is_loopback or
            ip.is_private or
            ip.is_reserved or
            ip.is_multicast or
            str(ip).startswith("0.") or
            str(ip).startswith("127.") or
            str(ip).startswith("255.255.255.255")
        )
    except ValueError:
        return False


def ip_to_int(value: str) -> int | None:
    try:
        return int(ipaddress.ip_address(value))
    except ValueError:
        return None


def int_to_ipv4(n: int) -> str:
    return str(ipaddress.IPv4Address(n))


def ip_sort_key(value: str) -> tuple[int, int]:
    """Sortable key for IPs — sorts IPv4 before IPv6."""
    try:
        ip = ipaddress.ip_address(value)
        version = 0 if ip.version == 4 else 1
        return (version, int(ip))
    except ValueError:
        return (2, 0)


# --- CIDR Utilities ---

def parse_cidr(cidr: str) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    try:
        return ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return None


def is_ip_in_cidr(ip: str, cidr: str) -> bool:
    """True if IP is within the CIDR range."""
    try:
        network = parse_cidr(cidr)
        if network is None:
            return False
        return ipaddress.ip_address(ip) in network
    except ValueError:
        return False


def cidr_overlap(cidr1: str, cidr2: str) -> bool:
    """True if two CIDR ranges overlap."""
    n1 = parse_cidr(cidr1)
    n2 = parse_cidr(cidr2)
    if n1 is None or n2 is None:
        return False
    return n1.overlaps(n2)


def cidr_subnets(cidr: str, prefix: int) -> Iterator[str]:
    """Yield sub-CIDRs of a given prefix length."""
    network = parse_cidr(cidr)
    if network is None:
        return
    for sub in network.subnets(new_prefix=prefix):
        yield str(sub)


def cidr_count(cidr: str) -> int | None:
    """Number of IP addresses in CIDR."""
    network = parse_cidr(cidr)
    if network is None:
        return None
    return network.num_addresses


# --- ASN Utilities ---

_ASN_RE = re.compile(r"^(?:AS)?(\d+)$", re.IGNORECASE)


def parse_asn(value: str) -> int | None:
    """Extract ASN number from string like 'AS15169' or '15169'."""
    m = _ASN_RE.match(value.strip())
    if m:
        num = int(m.group(1))
        if 0 <= num <= 2**32 - 1:
            return num
    return None


def format_asn(asn: int) -> str:
    return f"AS{asn}"


def is_valid_asn(value: str) -> bool:
    return parse_asn(value) is not None


# --- GeoIP (struct-based, no external DB) ---

# Heuristic: First byte of IPv4 encodes rough regional data
# This is NOT accurate GeoIP — use intelligence/geoip_lane.py for real data.
# This module provides FAST rough classification for routing decisions.

_COUNTRY_HEURISTIC: dict[int, str] = {
    1: "US",   # ARIN
    2: "EU",   # RIPE (approximate)
    5: "EU",   # RIPE
    8: "US",   # ARIN
    12: "US",  # ARIN
    14: "AP",  # APNIC
    23: "US",  # ARIN
    24: "US",  # ARIN
    32: "US",  # ARIN
    34: "US",  # ARIN
    35: "US",  # ARIN
    37: "EU",  # RIPE
    38: "US",  # ARIN
    40: "US",  # ARIN
    44: "US",  # ARIN
    45: "US",  # ARIN
    47: "US",  # ARIN
    48: "US",  # ARIN
    50: "US",  # ARIN
    52: "US",  # ARIN
    54: "US",  # ARIN
    63: "US",  # ARIN
    64: "US",  # ARIN
    65: "US",  # ARIN
    66: "US",  # ARIN
    67: "US",  # ARIN
    68: "US",  # ARIN
    69: "US",  # ARIN
    70: "US",  # ARIN
    71: "US",  # ARIN
    72: "US",  # ARIN
    73: "US",  # ARIN
    74: "US",  # ARIN
    75: "US",  # ARIN
    76: "US",  # ARIN
    96: "US",  # ARIN
    97: "US",  # ARIN
    98: "US",  # ARIN
    99: "US",  # ARIN
}


def country_from_ip_heuristic(ip: str) -> str:
    """
    Fast country heuristic from first byte of IPv4 address.

    WARNING: This is a rough heuristic, NOT accurate GeoIP.
    Uses IANA allocation blocks to guess regional registry.

    For production use: intelligence/geoip_lane.py with MaxMind DB.
    """
    if not is_ipv4(ip):
        return "XX"  # Unknown
    first_byte = int(ip.split(".")[0])
    return _COUNTRY_HEURISTIC.get(first_byte, "XX")


# --- Domain Utilities ---

_DOMAIN_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$"
)


def is_domain(value: str) -> bool:
    return bool(_DOMAIN_RE.match(value.strip().lower()))


def domain_depth(domain: str) -> int:
    return max(domain.count(".") - 1, 0)


def parent_domain(domain: str) -> str | None:
    parts = domain.strip().lower().split(".")
    if len(parts) < 3:
        return None
    return ".".join(parts[1:])


def extract_domain_from_url(url: str) -> str | None:
    """Extract domain from URL like https://example.com/path."""
    m = re.match(r"^https?://([^/]+)", url.strip())
    if m:
        return m.group(1).split(":")[0].lower()
    return None


# --- Port Utilities ---

_PORT_KNOWN = {
    20: "FTP-DATA", 21: "FTP", 22: "SSH", 23: "TELNET", 25: "SMTP",
    53: "DNS", 80: "HTTP", 110: "POP3", 119: "NNTP", 123: "NTP",
    143: "IMAP", 161: "SNMP", 194: "IRC", 443: "HTTPS", 465: "SMTPS",
    587: "SMTP-SUB", 993: "IMAPS", 995: "POP3S",
    3306: "MYSQL", 5432: "POSTGRES", 6379: "REDIS", 27017: "MONGODB",
}


def port_name(port: int) -> str:
    return _PORT_KNOWN.get(port, f"PORT-{port}")


def is_port_known(port: int) -> bool:
    return port in _PORT_KNOWN


# --- Protocol Detection ---

_PROTO_RE = re.compile(r"\b(TCP|UDP|SCTP|DCCP)\b", re.IGNORECASE)


def parse_protocol(proto: str) -> str | None:
    m = _PROTO_RE.match(proto.strip())
    return m.group(1).upper() if m else None


# --- Exported API ---

__all__ = [
    # IP
    "is_ipv4", "is_ipv6", "is_ip", "classify_ip",
    "is_private_ip", "is_bogon", "ip_to_int", "int_to_ipv4", "ip_sort_key",
    # CIDR
    "parse_cidr", "is_ip_in_cidr", "cidr_overlap", "cidr_subnets", "cidr_count",
    # ASN
    "parse_asn", "format_asn", "is_valid_asn",
    # GeoIP
    "country_from_ip_heuristic",
    # Domain
    "is_domain", "domain_depth", "parent_domain", "extract_domain_from_url",
    # Port
    "port_name", "is_port_known",
    # Protocol
    "parse_protocol",
]
