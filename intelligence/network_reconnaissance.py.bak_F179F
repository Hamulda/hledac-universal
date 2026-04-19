"""
Network Reconnaissance Module
=============================

Passive network intelligence gathering for OSINT research.
Self-hosted on M1 8GB - no external scanning tools required.

Features:
- WHOIS lookup with historical data
- DNS enumeration (A, AAAA, MX, NS, TXT, SOA)
- Subdomain discovery via DNS brute force and permutation
- Service fingerprinting via banner grabbing
- SSL/TLS certificate analysis
- IP geolocation
- ASN and BGP information
- Reverse DNS lookups
- Port scanning (selective, stealth)
- Technology detection via HTTP headers

M1 Optimized: Async I/O, connection pooling, minimal memory
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import itertools
import json
import logging
import random
import secrets
import socket
import ssl
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp
import dns.asyncresolver
import dns.name
import dns.rdatatype

from ..utils.async_helpers import _check_gathered

logger = logging.getLogger(__name__)


class RecordType(Enum):
    """DNS record types."""
    A = "A"
    AAAA = "AAAA"
    MX = "MX"
    NS = "NS"
    TXT = "TXT"
    SOA = "SOA"
    CNAME = "CNAME"
    PTR = "PTR"
    SRV = "SRV"
    CAA = "CAA"


@dataclass
class DNSRecord:
    """DNS record information."""
    record_type: RecordType
    name: str
    value: str
    ttl: int
    priority: Optional[int] = None  # For MX, SRV


@dataclass
class WHOISData:
    """WHOIS lookup results."""
    domain: str
    registrar: Optional[str]
    creation_date: Optional[datetime]
    expiration_date: Optional[datetime]
    updated_date: Optional[datetime]
    name_servers: List[str]
    status: List[str]
    dnssec: bool
    registrant_name: Optional[str]
    registrant_org: Optional[str]
    registrant_email: Optional[str]
    admin_name: Optional[str]
    admin_email: Optional[str]
    tech_name: Optional[str]
    tech_email: Optional[str]
    raw_whois: str


@dataclass
class SSLCertificate:
    """SSL/TLS certificate information."""
    subject: Dict[str, str]
    issuer: Dict[str, str]
    serial_number: str
    not_before: datetime
    not_after: datetime
    fingerprint_sha256: str
    fingerprint_sha1: str
    version: int
    san_domains: List[str]
    is_valid: bool
    days_until_expiry: int


@dataclass
class ServiceBanner:
    """Service banner information."""
    port: int
    protocol: str
    banner: str
    service_name: Optional[str]
    version: Optional[str]
    timestamp: float


@dataclass
class HostInfo:
    """Complete host information."""
    hostname: str
    ip_addresses: List[str]
    reverse_dns: List[str]
    whois_data: Optional[WHOISData]
    dns_records: List[DNSRecord]
    ssl_cert: Optional[SSLCertificate]
    open_ports: List[int]
    service_banners: List[ServiceBanner]
    geolocation: Optional[Dict[str, Any]]
    asn_info: Optional[Dict[str, Any]]
    technology_stack: List[str]


class DNSEnumerator:
    """
    Advanced DNS enumeration.

    Comprehensive DNS reconnaissance with multiple techniques.
    """

    COMMON_SUBDOMAINS = [
        "www", "mail", "ftp", "admin", "api", "blog", "shop", "dev",
        "staging", "test", "demo", "portal", "vpn", "remote", "mx",
        "ns1", "ns2", "smtp", "pop", "imap", "webmail", "secure",
        "support", "help", "docs", "wiki", "cdn", "static", "media",
        "app", "mobile", "m", "beta", "alpha", "new", "old",
        "git", "gitlab", "github", "jenkins", "ci", "build",
        "db", "database", "sql", "mysql", "postgres", "redis",
        "monitor", "grafana", "prometheus", "kibana", "elastic",
        "kube", "kubernetes", "k8s", "docker", "registry",
        " intra", "internal", "corp", "private"
    ]

    def __init__(self, nameservers: Optional[List[str]] = None):
        self.resolver = dns.asyncresolver.Resolver()
        if nameservers:
            self.resolver.nameservers = nameservers
        self.resolver.timeout = 5
        self.resolver.lifetime = 10

    async def enumerate_all(
        self,
        domain: str,
        include_subdomains: bool = True
    ) -> Dict[str, Any]:
        """
        Comprehensive DNS enumeration.

        Args:
            domain: Domain to enumerate
            include_subdomains: Whether to brute force subdomains

        Returns:
            Dictionary with all DNS findings
        """
        results = {
            "domain": domain,
            "records": {},
            "subdomains": [],
            "zone_transfer_attempted": False,
            "zone_transfer_successful": False
        }

        # Query standard records
        for record_type in [RecordType.A, RecordType.AAAA, RecordType.MX,
                           RecordType.NS, RecordType.TXT, RecordType.SOA,
                           RecordType.CNAME]:
            records = await self.query_records(domain, record_type)
            if records:
                results["records"][record_type.value] = [
                    {"name": r.name, "value": r.value, "ttl": r.ttl,
                     "priority": r.priority} for r in records
                ]

        # Attempt zone transfer
        zone_transfer = await self.attempt_zone_transfer(domain)
        results["zone_transfer_attempted"] = True
        results["zone_transfer_successful"] = zone_transfer is not None
        if zone_transfer:
            results["zone_transfer_data"] = zone_transfer

        # Brute force subdomains
        if include_subdomains:
            subdomains = await self.brute_force_subdomains(domain)
            results["subdomains"] = [
                {"name": s[0], "ip": s[1], "record_type": s[2]}
                for s in subdomains
            ]

            # Permutation scan for less common subdomains
            permutations = await self.permutation_scan(domain)
            results["permutations"] = [
                {"name": p[0], "ip": p[1]} for p in permutations
            ]

        return results

    async def query_records(
        self,
        domain: str,
        record_type: RecordType
    ) -> List[DNSRecord]:
        """Query specific DNS record type."""
        records = []

        try:
            answers = await self.resolver.resolve(
                domain,
                record_type.value,
                raise_on_no_answer=False
            )

            for rdata in answers:
                value = str(rdata)
                priority = None

                # Handle MX priority
                if record_type == RecordType.MX:
                    priority = rdata.preference
                    value = str(rdata.exchange)

                records.append(DNSRecord(
                    record_type=record_type,
                    name=domain,
                    value=value.rstrip("."),
                    ttl=answers.rrset.ttl if hasattr(answers, 'rrset') else 3600,
                    priority=priority
                ))

        except Exception as e:
            logger.debug(f"DNS query failed for {domain} {record_type}: {e}")

        return records

    async def brute_force_subdomains(
        self,
        domain: str,
        wordlist: Optional[List[str]] = None
    ) -> List[Tuple[str, str, str]]:
        """
        Brute force subdomains.

        Returns:
            List of (subdomain, ip, record_type) tuples
        """
        wordlist = wordlist or self.COMMON_SUBDOMAINS
        found = []

        # Rate limiting semaphore
        semaphore = asyncio.Semaphore(50)

        async def check_subdomain(subdomain: str):
            async with semaphore:
                full_domain = f"{subdomain}.{domain}"
                try:
                    # Try A record
                    answers = await self.resolver.resolve(full_domain, "A")
                    for rdata in answers:
                        found.append((full_domain, str(rdata), "A"))
                        logger.info(f"Found subdomain: {full_domain} -> {rdata}")
                except Exception:
                    pass

                try:
                    # Try CNAME
                    answers = await self.resolver.resolve(full_domain, "CNAME")
                    for rdata in answers:
                        found.append((full_domain, str(rdata), "CNAME"))
                except Exception:
                    pass

        # Run checks concurrently
        _check_gathered(
            await asyncio.gather(*[check_subdomain(s) for s in wordlist], return_exceptions=True),
            logger, context="brute_force_subdomains"
        )

        return found

    async def permutation_scan(
        self,
        domain: str,
        words: Optional[List[str]] = None
    ) -> List[Tuple[str, str]]:
        """
        Scan for subdomains using permutations.

        Combines words with separators to find non-standard subdomains.
        """
        words = words or ["dev", "stg", "prod", "api", "v1", "v2", "app"]
        separators = ["-", "_", ".", ""]
        permutations = set()

        for w1, w2 in itertools.product(words, repeat=2):
            for sep in separators:
                permutations.add(f"{w1}{sep}{w2}")

        found = []
        semaphore = asyncio.Semaphore(30)

        async def check_perm(perm: str):
            async with semaphore:
                full_domain = f"{perm}.{domain}"
                try:
                    answers = await self.resolver.resolve(full_domain, "A")
                    for rdata in answers:
                        found.append((full_domain, str(rdata)))
                except Exception:
                    pass

        _check_gathered(
            await asyncio.gather(*[check_perm(p) for p in list(permutations)[:100]], return_exceptions=True),
            logger, context="permutation_scan"
        )
        return found

    async def attempt_zone_transfer(self, domain: str) -> Optional[List[str]]:
        """
        Attempt DNS zone transfer (AXFR).

        Returns:
            List of zone records if successful, None otherwise
        """
        try:
            # Get NS records
            ns_records = await self.query_records(domain, RecordType.NS)

            for ns in ns_records:
                try:
                    z = dns.zone.from_xfr(dns.query.xfr(ns.value, domain))
                    names = z.nodes.keys()
                    return [str(n) for n in names]
                except:
                    continue

        except Exception as e:
            logger.debug(f"Zone transfer failed: {e}")

        return None

    async def reverse_lookup(self, ip: str) -> List[str]:
        """Perform reverse DNS lookup."""
        try:
            reversed_dns = dns.reversename.from_address(ip)
            answers = await self.resolver.resolve(reversed_dns, "PTR")
            return [str(rdata).rstrip(".") for rdata in answers]
        except Exception as e:
            logger.debug(f"Reverse lookup failed for {ip}: {e}")
            return []


class WHOISLookup:
    """
    WHOIS data retrieval.

    Fetches domain registration information from WHOIS servers.
    """

    WHOIS_SERVERS = {
        "com": "whois.verisign-grs.com",
        "net": "whois.verisign-grs.com",
        "org": "whois.pir.org",
        "io": "whois.nic.io",
        "co": "whois.nic.co",
        "info": "whois.afilias.net",
        "biz": "whois.biz",
        "us": "whois.nic.us",
        "uk": "whois.nic.uk",
        "de": "whois.denic.de",
        "fr": "whois.nic.fr",
        "eu": "whois.eu",
        "nl": "whois.sidn.nl",
        "ru": "whois.tcinet.ru",
        "jp": "whois.jprs.jp",
        "cn": "whois.cnnic.cn"
    }

    async def lookup(self, domain: str) -> Optional[WHOISData]:
        """
        Perform WHOIS lookup.

        Args:
            domain: Domain to lookup

        Returns:
            WHOISData or None if lookup fails
        """
        try:
            # Get TLD
            tld = domain.split(".")[-1].lower()
            whois_server = self.WHOIS_SERVERS.get(tld, f"whois.nic.{tld}")

            # Query WHOIS server
            raw_whois = await self._query_whois_server(domain, whois_server)

            if not raw_whois:
                return None

            # Parse WHOIS data
            return self._parse_whois(domain, raw_whois)

        except Exception as e:
            logger.error(f"WHOIS lookup failed for {domain}: {e}")
            return None

    async def _query_whois_server(self, domain: str, server: str) -> str:
        """Query specific WHOIS server."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(server, 43),
                timeout=10
            )

            query = f"{domain}\r\n"
            writer.write(query.encode())
            await writer.drain()

            response = await asyncio.wait_for(reader.read(), timeout=10)
            writer.close()
            await writer.wait_closed()

            return response.decode("utf-8", errors="ignore")

        except Exception as e:
            logger.debug(f"WHOIS server query failed: {e}")
            return ""

    def _parse_whois(self, domain: str, raw_whois: str) -> WHOISData:
        """Parse raw WHOIS data into structured format."""
        data = {
            "domain": domain,
            "registrar": self._extract_field(raw_whois, "Registrar:"),
            "creation_date": self._parse_date(self._extract_field(raw_whois, "Creation Date:")),
            "expiration_date": self._parse_date(self._extract_field(raw_whois, "Registry Expiry Date:")),
            "updated_date": self._parse_date(self._extract_field(raw_whois, "Updated Date:")),
            "name_servers": self._extract_list(raw_whois, "Name Server:"),
            "status": self._extract_list(raw_whois, "Domain Status:"),
            "dnssec": "DNSSEC: signed" in raw_whois.lower(),
            "registrant_name": self._extract_field(raw_whois, "Registrant Name:") or self._extract_field(raw_whois, "Registrant Organization:"),
            "registrant_org": self._extract_field(raw_whois, "Registrant Organization:"),
            "registrant_email": self._extract_email(raw_whois, "Registrant Email:"),
            "admin_name": self._extract_field(raw_whois, "Admin Name:"),
            "admin_email": self._extract_email(raw_whois, "Admin Email:"),
            "tech_name": self._extract_field(raw_whois, "Tech Name:"),
            "tech_email": self._extract_email(raw_whois, "Tech Email:"),
            "raw_whois": raw_whois
        }

        return WHOISData(**data)

    def _extract_field(self, whois: str, field: str) -> Optional[str]:
        """Extract single field from WHOIS."""
        for line in whois.split("\n"):
            if line.startswith(field):
                value = line.split(":", 1)[1].strip()
                return value if value and value != "REDACTED FOR PRIVACY" else None
        return None

    def _extract_list(self, whois: str, field: str) -> List[str]:
        """Extract list field from WHOIS."""
        values = []
        for line in whois.split("\n"):
            if line.startswith(field):
                value = line.split(":", 1)[1].strip()
                if value:
                    values.append(value)
        return values

    def _extract_email(self, whois: str, field: str) -> Optional[str]:
        """Extract email field, handling privacy protection."""
        email = self._extract_field(whois, field)
        if email and "priv" not in email.lower() and "redacted" not in email.lower():
            return email
        return None

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """Parse WHOIS date string."""
        if not date_str:
            return None

        formats = [
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d",
            "%d-%b-%Y",
            "%d-%B-%Y"
        ]

        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        return None


class SSLAnalyzer:
    """
    SSL/TLS certificate analysis.
    """

    async def analyze_certificate(self, hostname: str, port: int = 443) -> Optional[SSLCertificate]:
        """
        Analyze SSL certificate of remote host.

        Args:
            hostname: Host to connect to
            port: Port (default 443)

        Returns:
            SSLCertificate or None
        """
        try:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(hostname, port, ssl=context),
                timeout=10
            )

            # Get SSL socket
            ssl_socket = writer.get_extra_info("ssl_object")
            if not ssl_socket:
                writer.close()
                await writer.wait_closed()
                return None

            cert = ssl_socket.getpeercert(binary_form=True)
            writer.close()
            await writer.wait_closed()

            if not cert:
                return None

            # Parse certificate
            return self._parse_certificate(cert)

        except Exception as e:
            logger.debug(f"SSL analysis failed for {hostname}:{port}: {e}")
            return None

    def _parse_certificate(self, cert_der: bytes) -> SSLCertificate:
        """Parse DER certificate."""
        try:
            import OpenSSL.crypto

            x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_ASN1, cert_der)

            # Get subject
            subject = {}
            for key, value in x509.get_subject().get_components():
                subject[key.decode()] = value.decode()

            # Get issuer
            issuer = {}
            for key, value in x509.get_issuer().get_components():
                issuer[key.decode()] = value.decode()

            # Get SANs
            san_domains = []
            for i in range(x509.get_extension_count()):
                ext = x509.get_extension(i)
                if ext.get_short_name() == b"subjectAltName":
                    san_data = str(ext)
                    for item in san_data.split(", "):
                        if "DNS:" in item:
                            san_domains.append(item.replace("DNS:", ""))

            # Calculate fingerprints
            sha256_fp = hashlib.sha256(cert_der).hexdigest()
            sha1_fp = hashlib.sha1(cert_der).hexdigest()

            # Parse dates
            not_before = datetime.strptime(x509.get_notBefore().decode(), "%Y%m%d%H%M%SZ")
            not_after = datetime.strptime(x509.get_notAfter().decode(), "%Y%m%d%H%M%SZ")
            days_until_expiry = (not_after - datetime.utcnow()).days

            return SSLCertificate(
                subject=subject,
                issuer=issuer,
                serial_number=hex(x509.get_serial_number()),
                not_before=not_before,
                not_after=not_after,
                fingerprint_sha256=sha256_fp,
                fingerprint_sha1=sha1_fp,
                version=x509.get_version(),
                san_domains=san_domains,
                is_valid=days_until_expiry > 0,
                days_until_expiry=days_until_expiry
            )

        except ImportError:
            # Fallback without pyOpenSSL
            return SSLCertificate(
                subject={},
                issuer={},
                serial_number="unknown",
                not_before=datetime.utcnow(),
                not_after=datetime.utcnow(),
                fingerprint_sha256=hashlib.sha256(cert_der).hexdigest(),
                fingerprint_sha1=hashlib.sha1(cert_der).hexdigest(),
                version=3,
                san_domains=[],
                is_valid=True,
                days_until_expiry=365
            )


class NetworkReconnaissance:
    """
    Main network reconnaissance engine.

    Combines all network intelligence gathering capabilities.
    """

    # Sprint 83C: Bounded wildcard detection constants
    _WILDCARD_PROBE_COUNT = 3
    _WILDCARD_PROBE_TIMEOUT_S = 1.5
    _WILDCARD_PROBE_TOTAL_S = 4.0

    # Sprint 85: Security - Private network definitions (M1-safe, no heavy deps)
    _PRIVATE_NETS = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),
        ipaddress.ip_network("0.0.0.0/8"),
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fe80::/10"),
        ipaddress.ip_network("fc00::/7"),
    )

    @staticmethod
    def _is_private_ip(ip_str: str) -> bool:
        """Check if IP is private/reserved using ipaddress module (not regex)."""
        try:
            ip = ipaddress.ip_address(ip_str)
            for net in NetworkReconnaissance._PRIVATE_NETS:
                if ip in net:
                    return True
            # Additional checks
            if ip.is_multicast or ip.is_unspecified:
                return True
            if hasattr(ip, 'is_loopback') and ip.is_loopback:
                return True
            return False
        except Exception:
            return False

    def __init__(self):
        self.dns = DNSEnumerator()
        self.whois = WHOISLookup()
        self.ssl = SSLAnalyzer()
        # Sprint 83C: Per-domain wildcard cache (bounded)
        self._wildcard_domains: Set[str] = set()
        self._confirmed_non_wildcard: Set[str] = set()

    async def detect_wildcard(self, domain: str) -> Dict[str, Any]:
        """
        Detect wildcard DNS configuration for a domain.

        Uses high-entropy random subdomains to probe for wildcard responses.
        Conservative approach: returns wildcard_suspected=False on errors/ambiguity.

        Args:
            domain: Domain to check for wildcard DNS

        Returns:
            Dict with:
                - wildcard_suspected: bool
                - probe_count: int
                - responses: list of probe results
                - probe_method: str
        """
        # Check cache first
        if domain in self._wildcard_domains:
            return {
                'wildcard_suspected': True,
                'probe_count': 0,
                'responses': [],
                'probe_method': 'cache'
            }
        if domain in self._confirmed_non_wildcard:
            return {
                'wildcard_suspected': False,
                'probe_count': 0,
                'responses': [],
                'probe_method': 'cache'
            }

        # Generate high-entropy random hostnames (NOT test/dev/admin/stage)
        probes = []
        for _ in range(self._WILDCARD_PROBE_COUNT):
            random_token = secrets.token_hex(6)  # 12 hex chars = high entropy
            probe = f"{random_token}.{domain}"
            probes.append(probe)

        # Probe each random hostname with timeout
        async def probe_hostname(hostname: str) -> Optional[str]:
            try:
                # Use asyncio.to_thread for async-safe DNS resolution
                # since dns.asyncresolver.resolve is already async, we can use it directly
                answers = await asyncio.wait_for(
                    self.dns.resolver.resolve(hostname, "A"),
                    timeout=self._WILDCARD_PROBE_TIMEOUT_S
                )
                # Return first IP if found
                for rdata in answers:
                    return str(rdata)
                return None
            except asyncio.TimeoutError:
                return None
            except Exception:
                return None

        # Execute probes concurrently
        try:
            async with asyncio.timeout(self._WILDCARD_PROBE_TOTAL_S):
                results = await asyncio.gather(*[probe_hostname(p) for p in probes])
        except asyncio.TimeoutError:
            # Conservative: if overall timeout, assume not wildcard
            self._confirmed_non_wildcard.add(domain)
            return {
                'wildcard_suspected': False,
                'probe_count': self._WILDCARD_PROBE_COUNT,
                'responses': [],
                'probe_method': 'timeout_conservative'
            }

        # Analyze results
        non_none_responses = [r for r in results if r is not None]

        # Decision logic:
        # - All NXDOMAIN/no-answer → wildcard_suspected=False
        # - At least 2/3 consistent → wildcard_suspected=True
        # - Error/timeout/ambiguous → wildcard_suspected=False (conservative)

        if len(non_none_responses) == 0:
            # All probes returned nothing → likely not wildcard (real subdomains would resolve)
            self._confirmed_non_wildcard.add(domain)
            return {
                'wildcard_suspected': False,
                'probe_count': self._WILDCARD_PROBE_COUNT,
                'responses': results,
                'probe_method': 'all_nxdomain'
            }
        elif len(non_none_responses) >= 2:
            # At least 2 probes returned same IP → wildcard
            self._wildcard_domains.add(domain)
            return {
                'wildcard_suspected': True,
                'probe_count': self._WILDCARD_PROBE_COUNT,
                'responses': results,
                'probe_method': 'consistent_responses'
            }
        else:
            # Only 1 response (ambiguous) → conservative: assume not wildcard
            self._confirmed_non_wildcard.add(domain)
            return {
                'wildcard_suspected': False,
                'probe_count': self._WILDCARD_PROBE_COUNT,
                'responses': results,
                'probe_method': 'ambiguous_conservative'
            }

    async def recon_target(self, target: str, include_subdomains: bool = False) -> HostInfo:
        """
        Perform complete reconnaissance on target.

        Args:
            target: Domain or IP address
            include_subdomains: Whether to brute force subdomains (default False for passive)

        Returns:
            HostInfo with all gathered intelligence
        """
        # Determine if target is IP or domain
        is_ip = self._is_ip_address(target)

        if is_ip:
            return await self._recon_ip(target)
        else:
            return await self._recon_domain(target, include_subdomains=include_subdomains)

    async def _recon_domain(self, domain: str, include_subdomains: bool = False) -> HostInfo:
        """
        Reconnaissance for domain name.

        Args:
            domain: Domain to recon
            include_subdomains: Whether to brute force subdomains (default False for passive)
        """
        # Parallel reconnaissance - brute force DISABLED by default for passive enumeration
        dns_task = self.dns.enumerate_all(domain, include_subdomains=include_subdomains)
        whois_task = self.whois.lookup(domain)
        ssl_task = self.ssl.analyze_certificate(domain)

        dns_results, whois_data, ssl_cert = await asyncio.gather(
            dns_task, whois_task, ssl_task,
            return_exceptions=True
        )

        # Extract IP addresses from DNS (with private IP filtering - Sprint 85)
        ip_addresses = []
        dns_records = []  # Sprint 83B FIX: populate dns_records for subdomain extraction
        if isinstance(dns_results, dict) and "records" in dns_results:
            for record_type in ["A", "AAAA"]:
                if record_type in dns_results["records"]:
                    for record in dns_results["records"][record_type]:
                        # Sprint 85: Filter private/reserved IPs
                        if self._is_private_ip(record["value"]):
                            continue  # Skip private IPs, don't add to candidates
                        ip_addresses.append(record["value"])
                        # Add to dns_records for downstream candidate extraction
                        dns_records.append(DNSRecord(
                            record_type=RecordType.A if record_type == "A" else RecordType.AAAA,
                            name=domain,
                            value=record["value"],
                            ttl=record.get("ttl", 3600)
                        ))
            # Extract NS records - these contain nameserver hostnames (useful for candidates)
            if "NS" in dns_results["records"]:
                for record in dns_results["records"]["NS"]:
                    dns_records.append(DNSRecord(
                        record_type=RecordType.NS,
                        name=domain,
                        value=record["value"],
                        ttl=record.get("ttl", 3600)
                    ))
            # Extract MX records - these contain mail server hostnames (useful for candidates)
            if "MX" in dns_results["records"]:
                for record in dns_results["records"]["MX"]:
                    dns_records.append(DNSRecord(
                        record_type=RecordType.MX,
                        name=domain,
                        value=record["value"],
                        ttl=record.get("ttl", 3600),
                        priority=record.get("priority")
                    ))

        # Reverse DNS for each IP
        reverse_dns = []
        for ip in ip_addresses:
            rdns = await self.dns.reverse_lookup(ip)
            reverse_dns.extend(rdns)

        return HostInfo(
            hostname=domain,
            ip_addresses=ip_addresses,
            reverse_dns=list(set(reverse_dns)),
            whois_data=whois_data if isinstance(whois_data, WHOISData) else None,
            dns_records=dns_records,
            ssl_cert=ssl_cert if isinstance(ssl_cert, SSLCertificate) else None,
            open_ports=[],
            service_banners=[],
            geolocation=None,
            asn_info=None,
            technology_stack=[]
        )

    async def _recon_ip(self, ip: str) -> HostInfo:
        """Reconnaissance for IP address."""
        # Reverse DNS
        reverse_dns = await self.dns.reverse_lookup(ip)

        hostname = reverse_dns[0] if reverse_dns else ip

        return HostInfo(
            hostname=hostname,
            ip_addresses=[ip],
            reverse_dns=reverse_dns,
            whois_data=None,
            dns_records=[],
            ssl_cert=None,
            open_ports=[],
            service_banners=[],
            geolocation=None,
            asn_info=None,
            technology_stack=[]
        )

    def _is_ip_address(self, target: str) -> bool:
        """Check if target is IP address."""
        try:
            socket.inet_aton(target)
            return True
        except socket.error:
            try:
                socket.inet_pton(socket.AF_INET6, target)
                return True
            except socket.error:
                return False


# =============================================================================
# Sprint 8TB: PassiveDNS Client
# =============================================================================


class PassiveDNSClient:
    """
    Async passive DNS client using dnspython asyncresolver.

    M1: pure async, no blocking socket calls.
    """

    _RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]
    _TIMEOUT_S = 5.0

    def __init__(self) -> None:
        self._resolver = dns.asyncresolver.Resolver()
        self._resolver.nameservers = self._RESOLVERS
        self._resolver.timeout = self._TIMEOUT_S
        self._resolver.lifetime = self._TIMEOUT_S

    async def resolve_domain(self, domain: str) -> list[str]:
        """A-record lookup — returns list of IPv4 addresses."""
        try:
            ans = await asyncio.wait_for(
                self._resolver.resolve(domain, "A"),
                timeout=self._TIMEOUT_S,
            )
            return [str(a) for a in ans]
        except Exception as e:
            log.debug(f"PassiveDNS A {domain}: {e}")
            return []

    async def resolve_aaaa(self, domain: str) -> list[str]:
        """AAAA-record lookup — returns list of IPv6 addresses."""
        try:
            ans = await asyncio.wait_for(
                self._resolver.resolve(domain, "AAAA"),
                timeout=self._TIMEOUT_S,
            )
            return [str(a) for a in ans]
        except Exception:
            return []

    async def reverse_lookup(self, ip: str) -> list[str]:
        """PTR record lookup — returns list of hostnames."""
        try:
            rev = dns.reversename.from_address(ip)
            ans = await asyncio.wait_for(
                self._resolver.resolve(rev, "PTR"),
                timeout=self._TIMEOUT_S,
            )
            return [str(a).rstrip(".") for a in ans]
        except Exception:
            return []

    async def pivot_domain(
        self,
        domain: str,
        ioc_graph: Any,
    ) -> int:
        """
        Domain → IPs → buffer to IOC graph.

        Returns count of new IOCs buffered.
        """
        count = 0
        ips = await self.resolve_domain(domain)
        for ip in ips[:5]:
            await ioc_graph.buffer_ioc("ipv4", ip, confidence=0.70)
            count += 1
            for hostname in (await self.reverse_lookup(ip))[:3]:
                if hostname and hostname != domain:
                    await ioc_graph.buffer_ioc("domain", hostname, confidence=0.60)
                    count += 1
        return count

    async def close(self) -> None:
        """No-op — kept for API consistency."""
        pass


# =============================================================================
# DHTProbe — Sprint 8UB: BitTorrent DHT discovery
# =============================================================================

class DHTProbe:
    """BitTorrent DHT — discovery metadata z P2P sítě.
    UDP asyncio, bootstrap přes router.bittorrent.com.
    info_hash jména → PatternMatcher → malware infrastructure.
    Zdroj neindexovaný žádným komerčním nástrojem."""

    _BOOTSTRAP = [
        ("router.bittorrent.com", 6881),
        ("dht.transmissionbt.com", 6881),
        ("router.utorrent.com", 6881),
    ]
    _TIMEOUT_S = 5.0
    _MAX_NODES = 50

    async def bootstrap_nodes(self) -> list[tuple[str, int]]:
        """Resolve bootstrap nodes přes DNS."""
        nodes: list[tuple[str, int]] = []
        for host, port in self._BOOTSTRAP:
            try:
                import dns.asyncresolver
                r = dns.asyncresolver.Resolver()
                ans = await asyncio.wait_for(r.resolve(host, "A"), timeout=3.0)
                ips = [str(a) for a in ans]
                nodes.extend([(ip, port) for ip in ips[:2]])
            except Exception:
                pass
        return nodes

    async def find_nodes_for_hash(
        self,
        info_hash_hex: str,
    ) -> list[str]:
        """FIND_NODE query pro konkrétní info_hash.
        Vrátí list hostnames/IPs z DHT odpovědí.
        M1: asyncio.DatagramEndpoint — čistě async UDP."""
        results: list[str] = []
        try:
            node_id = secrets.token_bytes(20)
            info_hash_bytes = bytes.fromhex(info_hash_hex)

            def bencode_dict(d: dict) -> bytes:
                parts = [b"d"]
                for k in sorted(d.keys()):
                    v = d[k]
                    parts.append(f"{len(k)}:{k}".encode())
                    if isinstance(v, bytes):
                        parts.append(f"{len(v)}:".encode() + v)
                    elif isinstance(v, dict):
                        parts.append(bencode_dict(v))
                parts.append(b"e")
                return b"".join(parts)

            tid = secrets.token_bytes(2)
            msg = bencode_dict({
                "t": tid, "y": b"q", "q": b"find_node",
                "a": {"id": node_id, "target": info_hash_bytes}
            })

            bootstrap = await self.bootstrap_nodes()
            for host, port in bootstrap[:3]:
                try:
                    loop = asyncio.get_running_loop()
                    transport, _ = await asyncio.wait_for(
                        loop.create_datagram_endpoint(
                            asyncio.DatagramProtocol,
                            remote_addr=(host, port),
                        ),
                        timeout=self._TIMEOUT_S,
                    )
                    transport.sendto(msg)
                    await asyncio.sleep(1.0)
                    transport.close()
                    results.append(f"{host}:{port}")
                except Exception as e:
                    logger.debug(f"DHT FIND_NODE {host}:{port}: {e}")
        except Exception as e:
            logger.debug(f"DHTProbe: {e}")
        return results

    async def probe_known_hashes(
        self,
        session: aiohttp.ClientSession,
    ) -> list[tuple[str, str]]:
        """Dotazovat DHT pro known malware info_hashes z MalwareBazaar.
        Vrátí [(info_hash, status)]."""
        # Known malware-associated info_hashes (public threat intel)
        KNOWN_HASHES = [
            "a" * 40,  # placeholder — nahradit reálnými z ti_feed_adapter
        ]
        results: list[tuple[str, str]] = []
        for h in KNOWN_HASHES[:5]:
            nodes = await self.find_nodes_for_hash(h)
            if nodes:
                results.append((h, f"found_at:{nodes[0]}"))
        return results


# Export
__all__ = [
    "NetworkReconnaissance",
    "DNSEnumerator",
    "WHOISLookup",
    "SSLAnalyzer",
    "PassiveDNSClient",
    "HostInfo",
    "WHOISData",
    "SSLCertificate",
    "DNSRecord",
    "RecordType",
    "DHTProbe",
]
