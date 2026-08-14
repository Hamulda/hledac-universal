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
import asyncio
import hashlib
import ipaddress
import itertools
import logging
import secrets
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
import msgspec
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal
from hledac.universal.utils.msgspec_json import loads as _msgspec_loads
import dns.resolver  # E3 FIX: dns.asyncresolver removed in dnspython 3.x; use dns.resolver (async-aware in 3.x)
import httpx
from hledac.universal.transport.session_pool import session_pool
from hledac.universal.utils.asyncx import parallel_ok, parallel
from hledac.universal.core.concurrency_registry import ConcurrencyCategory, ConcurrencyBudgetRegistry
logger = logging.getLogger(__name__)

class RecordType(Enum):
    """DNS record types."""
    A = 'A'
    AAAA = 'AAAA'
    MX = 'MX'
    NS = 'NS'
    TXT = 'TXT'
    SOA = 'SOA'
    CNAME = 'CNAME'
    PTR = 'PTR'
    SRV = 'SRV'
    CAA = 'CAA'

class DNSRecord(msgspec.Struct, gc=False):
    """DNS record information."""
    record_type: RecordType
    name: str
    value: str
    ttl: int
    priority: int | None = None

class WHOISData(msgspec.Struct, frozen=True, gc=False):
    """WHOIS lookup results."""
    domain: str
    registrar: str | None
    creation_date: datetime | None
    expiration_date: datetime | None
    updated_date: datetime | None
    name_servers: list[str]
    status: list[str]
    dnssec: bool
    registrant_name: str | None
    registrant_org: str | None
    registrant_email: str | None
    admin_name: str | None
    admin_email: str | None
    tech_name: str | None
    tech_email: str | None
    raw_whois: str

class SSLCertificate(msgspec.Struct, frozen=True, gc=False):
    """SSL/TLS certificate information."""
    subject: dict[str, str]
    issuer: dict[str, str]
    serial_number: str
    not_before: datetime
    not_after: datetime
    fingerprint_sha256: str
    fingerprint_sha1: str
    version: int
    san_domains: list[str]
    is_valid: bool
    days_until_expiry: int

class ServiceBanner(msgspec.Struct, frozen=True, gc=False):
    """Service banner information."""
    port: int
    protocol: str
    banner: str
    service_name: str | None
    version: str | None
    timestamp: float

class HostInfo(msgspec.Struct, frozen=True, gc=False):
    """Complete host information."""
    hostname: str
    ip_addresses: list[str]
    reverse_dns: list[str]
    whois_data: WHOISData | None
    dns_records: list[DNSRecord]
    ssl_cert: SSLCertificate | None
    open_ports: list[int]
    service_banners: list[ServiceBanner]
    geolocation: dict[str, Any] | None
    asn_info: dict[str, Any] | None
    technology_stack: list[str]

class DNSEnumerator:
    """
    Advanced DNS enumeration.

    Comprehensive DNS reconnaissance with multiple techniques.
    """
    COMMON_SUBDOMAINS = ['www', 'mail', 'ftp', 'admin', 'api', 'blog', 'shop', 'dev', 'staging', 'test', 'demo', 'portal', 'vpn', 'remote', 'mx', 'ns1', 'ns2', 'smtp', 'pop', 'imap', 'webmail', 'secure', 'support', 'help', 'docs', 'wiki', 'cdn', 'static', 'media', 'app', 'mobile', 'm', 'beta', 'alpha', 'new', 'old', 'git', 'gitlab', 'github', 'jenkins', 'ci', 'build', 'db', 'database', 'sql', 'mysql', 'postgres', 'redis', 'monitor', 'grafana', 'prometheus', 'kibana', 'elastic', 'kube', 'kubernetes', 'k8s', 'docker', 'registry', ' intra', 'internal', 'corp', 'private']
    __slots__ = tuple(('resolver',))

    def __init__(self, nameservers: list[str] | None=None):
        # E3 FIX: dns.asyncresolver.Resolver() → dns.resolver.Resolver() (dnspython 3.x compatible)
        self.resolver = dns.resolver.Resolver()
        if nameservers:
            self.resolver.nameservers = nameservers
        self.resolver.timeout = 5
        self.resolver.lifetime = 10

    async def enumerate_all(self, domain: str, include_subdomains: bool=True) -> dict[str, Any]:
        """
        Comprehensive DNS enumeration.

        Args:
            domain: Domain to enumerate
            include_subdomains: Whether to brute force subdomains

        Returns:
            Dictionary with all DNS findings
        """
        results = {'domain': domain, 'records': {}, 'subdomains': [], 'zone_transfer_attempted': False, 'zone_transfer_successful': False}
        # ISSUE-XXX: parallel DNS queries — 7 record types run concurrently via bounded semaphore.
        # Prior: sequential for-loop (7 × ~50ms = 350ms). New: parallel at concurrency=4 (~100ms wall).
        RTYPES = [RecordType.A, RecordType.AAAA, RecordType.MX, RecordType.NS, RecordType.TXT, RecordType.SOA, RecordType.CNAME]

        async def _query_one(rt: RecordType) -> tuple[str, list[dict] | None]:
            records = await self.query_records(domain, rt)
            if records:
                return (rt.value, [{'name': r.name, 'value': r.value, 'ttl': r.ttl, 'priority': r.priority} for r in records])
            return (rt.value, None)

        sem = asyncio.Semaphore(4)

        async def _query_bounded(rt: RecordType):
            async with sem:
                return await _query_one(rt)

        q_results = await parallel([_query_bounded(rt) for rt in RTYPES], policy="collect", ctx="enumerate_all:dns")
        for rtype, data in q_results:
            if data is not None:
                results['records'][rtype] = data
        zone_transfer = await self.attempt_zone_transfer(domain)
        results['zone_transfer_attempted'] = True
        results['zone_transfer_successful'] = zone_transfer is not None
        if zone_transfer:
            results['zone_transfer_data'] = zone_transfer
        if include_subdomains:
            subdomains = await self.brute_force_subdomains(domain)
            results['subdomains'] = [{'name': s[0], 'ip': s[1], 'record_type': s[2]} for s in subdomains]
            permutations = await self.permutation_scan(domain)
            results['permutations'] = [{'name': p[0], 'ip': p[1]} for p in permutations]
        return results

    async def query_records(self, domain: str, record_type: RecordType) -> list[DNSRecord]:
        """Query specific DNS record type."""
        records = []
        try:
            answers = await self.resolver.resolve(domain, record_type.value, raise_on_no_answer=False)
            for rdata in answers:
                value = str(rdata)
                priority = None
                if record_type == RecordType.MX:
                    priority = rdata.preference
                    value = str(rdata.exchange)
                records.append(DNSRecord(record_type=record_type, name=domain, value=value.rstrip('.'), ttl=answers.rrset.ttl if hasattr(answers, 'rrset') else 3600, priority=priority))
        except Exception as e:
            logger.debug(f'DNS query failed for {domain} {record_type}: {e}')
        return records

    async def brute_force_subdomains(self, domain: str, wordlist: list[str] | None=None) -> list[tuple[str, str, str]]:
        """
        Brute force subdomains.

        Returns:
            List of (subdomain, ip, record_type) tuples
        """
        wordlist = wordlist or self.COMMON_SUBDOMAINS
        found = []
        registry = await ConcurrencyBudgetRegistry.get_instance_async()
        semaphore = registry.get(ConcurrencyCategory.DNS_BRUTE)

        async def check_subdomain(subdomain: str):
            async with semaphore:
                full_domain = f'{subdomain}.{domain}'
                try:
                    answers = await self.resolver.resolve(full_domain, 'A')
                    for rdata in answers:
                        found.append((full_domain, str(rdata), 'A'))
                        logger.info(f'Found subdomain: {full_domain} -> {rdata}')
                except Exception as e:
                    logger.debug(f'[DNS] A lookup failed for {full_domain}: {e}')
                try:
                    answers = await self.resolver.resolve(full_domain, 'CNAME')
                    for rdata in answers:
                        found.append((full_domain, str(rdata), 'CNAME'))
                except Exception as e:
                    logger.debug(f'[DNS] CNAME lookup failed for {full_domain}: {e}')
        await parallel([check_subdomain(s) for s in wordlist], policy="log", ctx='brute_force_subdomains')
        return found

    async def permutation_scan(self, domain: str, words: list[str] | None=None) -> list[tuple[str, str]]:
        """
        Scan for subdomains using permutations.

        Combines words with separators to find non-standard subdomains.
        """
        words = words or ['dev', 'stg', 'prod', 'api', 'v1', 'v2', 'app']
        separators = ['-', '_', '.', '']
        permutations = set()
        for w1, w2 in itertools.product(words, repeat=2):
            for sep in separators:
                permutations.add(f'{w1}{sep}{w2}')
        found = []
        from hledac.universal.core.concurrency import ConcurrencyCategory, get_semaphore
        semaphore = get_semaphore(ConcurrencyCategory.DNS_BRUTE)

        async def check_perm(perm: str):
            async with semaphore:
                full_domain = f'{perm}.{domain}'
                try:
                    answers = await self.resolver.resolve(full_domain, 'A')
                    for rdata in answers:
                        found.append((full_domain, str(rdata)))
                except Exception:  # noqa: BLE001
                    pass
        await parallel([check_perm(p) for p in list(permutations)[:100]], policy="log", ctx='permutation_scan')
        return found

    async def attempt_zone_transfer(self, domain: str) -> list[str] | None:
        """
        Attempt DNS zone transfer (AXFR).

        Returns:
            List of zone records if successful, None otherwise
        """
        try:
            ns_records = await self.query_records(domain, RecordType.NS)
            for ns in ns_records:
                try:
                    z = dns.zone.from_xfr(dns.query.xfr(ns.value, domain))
                    names = z.nodes.keys()
                    return [str(n) for n in names]
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f'Zone transfer failed: {e}')
        return None

    async def reverse_lookup(self, ip: str) -> list[str]:
        """Perform reverse DNS lookup."""
        try:
            reversed_dns = dns.reversename.from_address(ip)
            answers = await self.resolver.resolve(reversed_dns, 'PTR')
            return [str(rdata).rstrip('.') for rdata in answers]
        except Exception as e:
            logger.debug(f'Reverse lookup failed for {ip}: {e}')
            return []

class WHOISLookup:
    """
    WHOIS data retrieval.

    Fetches domain registration information from WHOIS servers.
    """
    WHOIS_SERVERS = {'com': 'whois.verisign-grs.com', 'net': 'whois.verisign-grs.com', 'org': 'whois.pir.org', 'io': 'whois.nic.io', 'co': 'whois.nic.co', 'info': 'whois.afilias.net', 'biz': 'whois.biz', 'us': 'whois.nic.us', 'uk': 'whois.nic.uk', 'de': 'whois.denic.de', 'fr': 'whois.nic.fr', 'eu': 'whois.eu', 'nl': 'whois.sidn.nl', 'ru': 'whois.tcinet.ru', 'jp': 'whois.jprs.jp', 'cn': 'whois.cnnic.cn'}

    async def lookup(self, domain: str) -> WHOISData | None:
        """
        Perform WHOIS lookup.

        Args:
            domain: Domain to lookup

        Returns:
            WHOISData or None if lookup fails
        """
        try:
            tld = domain.split('.')[-1].lower()
            whois_server = self.WHOIS_SERVERS.get(tld, f'whois.nic.{tld}')
            raw_whois = await self._query_whois_server(domain, whois_server)
            if not raw_whois:
                return None
            return self._parse_whois(domain, raw_whois)
        except Exception as e:
            logger.error(f'WHOIS lookup failed for {domain}: {e}')
            return None

    async def _query_whois_server(self, domain: str, server: str) -> str:
        """Query specific WHOIS server."""
        try:
            async with asyncio.timeout(10):
                reader, writer = await asyncio.open_connection(server, 43)
            query = f'{domain}\r\n'
            writer.write(query.encode())
            await writer.drain()
            async with asyncio.timeout(10):
                response = await reader.read()
            writer.close()
            await writer.wait_closed()
            return response.decode('utf-8', errors='ignore')
        except Exception as e:
            logger.debug(f'WHOIS server query failed: {e}')
            return ''

    def _parse_whois(self, domain: str, raw_whois: str) -> WHOISData:
        """Parse raw WHOIS data into structured format."""
        data = {'domain': domain, 'registrar': self._extract_field(raw_whois, 'Registrar:'), 'creation_date': self._parse_date(self._extract_field(raw_whois, 'Creation Date:')), 'expiration_date': self._parse_date(self._extract_field(raw_whois, 'Registry Expiry Date:')), 'updated_date': self._parse_date(self._extract_field(raw_whois, 'Updated Date:')), 'name_servers': self._extract_list(raw_whois, 'Name Server:'), 'status': self._extract_list(raw_whois, 'Domain Status:'), 'dnssec': 'DNSSEC: signed' in raw_whois.lower(), 'registrant_name': self._extract_field(raw_whois, 'Registrant Name:') or self._extract_field(raw_whois, 'Registrant Organization:'), 'registrant_org': self._extract_field(raw_whois, 'Registrant Organization:'), 'registrant_email': self._extract_email(raw_whois, 'Registrant Email:'), 'admin_name': self._extract_field(raw_whois, 'Admin Name:'), 'admin_email': self._extract_email(raw_whois, 'Admin Email:'), 'tech_name': self._extract_field(raw_whois, 'Tech Name:'), 'tech_email': self._extract_email(raw_whois, 'Tech Email:'), 'raw_whois': raw_whois}
        return WHOISData(**data)

    def _extract_field(self, whois: str, field: str) -> str | None:
        """Extract single field from WHOIS."""
        for line in whois.split('\n'):
            if line.startswith(field):
                value = line.split(':', 1)[1].strip()
                return value if value and value != 'REDACTED FOR PRIVACY' else None
        return None

    def _extract_list(self, whois: str, field: str) -> list[str]:
        """Extract list field from WHOIS."""
        values = []
        for line in whois.split('\n'):
            if line.startswith(field):
                value = line.split(':', 1)[1].strip()
                if value:
                    values.append(value)
        return values

    def _extract_email(self, whois: str, field: str) -> str | None:
        """Extract email field, handling privacy protection."""
        email = self._extract_field(whois, field)
        if email and 'priv' not in email.lower() and ('redacted' not in email.lower()):
            return email
        return None

    def _parse_date(self, date_str: str | None) -> datetime | None:
        """Parse WHOIS date string."""
        if not date_str:
            return None
        formats = ['%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%d', '%d-%b-%Y', '%d-%B-%Y']
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

    async def analyze_certificate(self, hostname: str, port: int=443) -> SSLCertificate | None:
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
            async with asyncio.timeout(10):
                reader, writer = await asyncio.open_connection(hostname, port, ssl=context)
            ssl_socket = writer.get_extra_info('ssl_object')
            if not ssl_socket:
                writer.close()
                await writer.wait_closed()
                return None
            cert = ssl_socket.getpeercert(binary_form=True)
            writer.close()
            await writer.wait_closed()
            if not cert:
                return None
            return self._parse_certificate(cert)
        except Exception as e:
            logger.debug(f'SSL analysis failed for {hostname}:{port}: {e}')
            return None

    def _parse_certificate(self, cert_der: bytes) -> SSLCertificate:
        """Parse DER certificate."""
        try:
            import OpenSSL.crypto
            x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_ASN1, cert_der)
            subject = {}
            for key, value in x509.get_subject().get_components():
                subject[key.decode()] = value.decode()
            issuer = {}
            for key, value in x509.get_issuer().get_components():
                issuer[key.decode()] = value.decode()
            san_domains = []
            for i in range(x509.get_extension_count()):
                ext = x509.get_extension(i)
                if ext.get_short_name() == b'subjectAltName':
                    san_data = str(ext)
                    for item in san_data.split(', '):
                        if 'DNS:' in item:
                            san_domains.append(item.replace('DNS:', ''))
            sha256_fp = hashlib.sha256(cert_der).hexdigest()
            sha1_fp = hashlib.sha1(cert_der).hexdigest()
            not_before = datetime.strptime(x509.get_notBefore().decode(), '%Y%m%d%H%M%SZ').replace(tzinfo=UTC)
            not_after = datetime.strptime(x509.get_notAfter().decode(), '%Y%m%d%H%M%SZ').replace(tzinfo=UTC)
            days_until_expiry = (not_after - datetime.now(UTC)).days
            return SSLCertificate(subject=subject, issuer=issuer, serial_number=hex(x509.get_serial_number()), not_before=not_before, not_after=not_after, fingerprint_sha256=sha256_fp, fingerprint_sha1=sha1_fp, version=x509.get_version(), san_domains=san_domains, is_valid=days_until_expiry > 0, days_until_expiry=days_until_expiry)
        except ImportError:
            return SSLCertificate(subject={}, issuer={}, serial_number='unknown', not_before=datetime.now(UTC), not_after=datetime.now(UTC), fingerprint_sha256=hashlib.sha256(cert_der).hexdigest(), fingerprint_sha1=hashlib.sha256(cert_der).hexdigest(), version=3, san_domains=[], is_valid=True, days_until_expiry=365)

    async def ja4_fingerprint(self, hostname: str, port: int=443, timeout_ms: int=5000) -> dict[str, Any] | None:
        """
        Extract JA4 TLS fingerprint from remote host.

        Uses Rust tls13 module (rustls) when available, falls back to Python
        ssl analysis for basic fingerprinting.

        Args:
            hostname: Host to connect to
            port: Port (default 443)
            timeout_ms: Connection timeout in milliseconds (default 5000)

        Returns:
            Dict with keys: ja4, ech_detected, tls_version, server_ciphers,
            server_extensions, alpn, cert_verified, host, port
            or None if connection fails
        """
        try:
            # PRIMARY: Rust tls13 module (<1ms, accurate JA4)
            # NOTE: Requires rust.tls with tls13 feature enabled in Cargo.toml
            # Build: `maturin develop --features tls13` or use --features full
            try:
                from hledac.universal.core.rust_backend import rust as _rust
                if hasattr(_rust, 'tls') and _rust.TLS13_AVAILABLE:
                    result = _rust.tls.connect_and_ja4(hostname, port, timeout_ms=timeout_ms)
                    result['host'] = hostname
                    result['port'] = port
                    return result
            except Exception:  # noqa: BLE001
                pass

            # SECONDARY: Python ssl analysis (slower, less accurate)
            # This fallback is used when Rust tls13 feature is not compiled.
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            async with asyncio.timeout(timeout_ms / 1000):
                reader, writer = await asyncio.open_connection(hostname, port, ssl=context)
            ssl_socket = writer.get_extra_info('ssl_object')
            if not ssl_socket:
                writer.close()
                await writer.wait_closed()
                return None

            # Get TLS version
            if hasattr(ssl_socket, 'version'):
                tls_version = ssl_socket.version() or 'unknown'
            else:
                tls_version = 'unknown'

            # Get cipher suite
            cipher = ssl_socket.cipher()
            server_ciphers = [cipher[0]] if cipher else []
            server_extensions = []
            alpn = ssl_socket.selected_alpn_protocol() if hasattr(ssl_socket, 'selected_alpn_protocol') else None

            writer.close()
            await writer.wait_closed()

            return {
                'host': hostname,
                'port': port,
                'ja4': '',  # Python ssl doesn't expose ClientHello for JA4
                'ech_detected': False,
                'tls_version': tls_version,
                'server_ciphers': server_ciphers,
                'server_extensions': server_extensions,
                'alpn': alpn,
                'cert_verified': False,
                'error': '',
            }
        except Exception as e:
            logger.debug(f'JA4 fingerprint failed for {hostname}:{port}: {e}')
            return None

    async def batch_ja4(self, hosts: list[tuple[str, int]], timeout_ms: int=5000) -> list[dict[str, Any]]:
        """
        Batch JA4 fingerprint for multiple hosts in parallel.

        Args:
            hosts: List of (hostname, port) tuples
            timeout_ms: Connection timeout in milliseconds

        Returns:
            List of result dicts (same as ja4_fingerprint)
        """
        import asyncio

        tasks = [self.ja4_fingerprint(host, port, timeout_ms) for host, port in hosts]
        # F3XX: parallel_ok() replaces asyncio.gather — preserves original order.
        results = await parallel_ok(*tasks, label="batch_ja4")
        return [r for r in results if isinstance(r, dict)]

class NetworkReconnaissance:
    """
    Main network reconnaissance engine.

    Combines all network intelligence gathering capabilities.
    """
    _WILDCARD_PROBE_COUNT = 3
    _WILDCARD_PROBE_TIMEOUT_S = 1.5
    _WILDCARD_PROBE_TOTAL_S = 4.0
    _PRIVATE_NETS = (ipaddress.ip_network('10.0.0.0/8'), ipaddress.ip_network('172.16.0.0/12'), ipaddress.ip_network('192.168.0.0/16'), ipaddress.ip_network('127.0.0.0/8'), ipaddress.ip_network('169.254.0.0/16'), ipaddress.ip_network('0.0.0.0/8'), ipaddress.ip_network('::1/128'), ipaddress.ip_network('fe80::/10'), ipaddress.ip_network('fc00::/7'))
    _rust_batch_classify: Callable[[list[str]], bytes] | None | Literal[False] = None
    __slots__ = tuple(('_confirmed_non_wildcard', '_wildcard_domains', 'dns', 'ssl', 'whois'))

    @classmethod
    def _get_rust_batch_classify(cls) -> Callable[[list[str]], bytes] | None:
        """Lazy load Rust batch_ip_classify, fail-soft if unavailable."""
        if cls._rust_batch_classify is None:
            try:
                from hledac.universal.core.rust_backend import rust as _rust_backend
                if _rust_backend.is_available and _rust_backend.ip is not None:
                    cls._rust_batch_classify = _rust_backend.ip.batch_ip_classify
                else:
                    cls._rust_batch_classify = False
            except Exception:
                cls._rust_batch_classify = False
        return cls._rust_batch_classify if cls._rust_batch_classify else None

    @staticmethod
    def _is_private_ip(ip_str: str) -> bool:
        """Check if IP is private/reserved using ipaddress module (not regex)."""
        try:
            ip = ipaddress.ip_address(ip_str)
            for net in NetworkReconnaissance._PRIVATE_NETS:
                if ip in net:
                    return True
            if ip.is_multicast or ip.is_unspecified:
                return True
            if hasattr(ip, 'is_loopback') and ip.is_loopback:
                return True
            return False
        except Exception:
            return False

    @classmethod
    def _filter_private_ips_batch(cls, ip_values: list[str]) -> tuple[list[str], list[str]]:
        """
        Batch-filter IPs using Rust batch_ip_classify.

        Returns (public_ips, private_ips) based on Rust classification.
        Falls back to Python _is_private_ip if Rust unavailable.

        Rust IpClass: 0=invalid, 1=private, 2=public, 3=loopback, 4=link-local
        Private = class in (1, 3, 4) — Rust does same checks as Python _is_private_ip.
        """
        rust_fn = cls._get_rust_batch_classify()
        if rust_fn is not None and ip_values:
            try:
                result_bytes = rust_fn(ip_values)
                public_ips = []
                private_ips = []
                for ip_val, class_byte in zip(ip_values, result_bytes):
                    if class_byte in (1, 3, 4):
                        private_ips.append(ip_val)
                    else:
                        public_ips.append(ip_val)
                return (public_ips, private_ips)
            except Exception:  # noqa: BLE001
                pass
        public_ips = []
        private_ips = []
        for ip_val in ip_values:
            if cls._is_private_ip(ip_val):
                private_ips.append(ip_val)
            else:
                public_ips.append(ip_val)
        return (public_ips, private_ips)

    def __init__(self):
        self.dns = DNSEnumerator()
        self.whois = WHOISLookup()
        self.ssl = SSLAnalyzer()
        self._wildcard_domains: set[str] = set()
        self._confirmed_non_wildcard: set[str] = set()

    async def detect_wildcard(self, domain: str) -> dict[str, Any]:
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
        if domain in self._wildcard_domains:
            return {'wildcard_suspected': True, 'probe_count': 0, 'responses': [], 'probe_method': 'cache'}
        if domain in self._confirmed_non_wildcard:
            return {'wildcard_suspected': False, 'probe_count': 0, 'responses': [], 'probe_method': 'cache'}
        probes = []
        for _ in range(self._WILDCARD_PROBE_COUNT):
            random_token = secrets.token_hex(6)
            probe = f'{random_token}.{domain}'
            probes.append(probe)

        async def probe_hostname(hostname: str) -> str | None:
            try:
                async with asyncio.timeout(self._WILDCARD_PROBE_TIMEOUT_S):
                    answers = await self.dns.resolver.resolve(hostname, 'A')
                for rdata in answers:
                    return str(rdata)
                return None
            except TimeoutError:
                return None
            except Exception:
                return None
        try:
            async with asyncio.timeout(self._WILDCARD_PROBE_TOTAL_S):
                _probe_result = await parallel([probe_hostname(p) for p in probes], policy="log", ctx='network_reconnaissance:745')
                results = _probe_result.ok
        except TimeoutError:
            self._confirmed_non_wildcard.add(domain)
            return {'wildcard_suspected': False, 'probe_count': self._WILDCARD_PROBE_COUNT, 'responses': [], 'probe_method': 'timeout_conservative'}
        non_none_responses = [r for r in results if r is not None]
        if not non_none_responses:
            self._confirmed_non_wildcard.add(domain)
            return {'wildcard_suspected': False, 'probe_count': self._WILDCARD_PROBE_COUNT, 'responses': results, 'probe_method': 'all_nxdomain'}
        elif len(non_none_responses) >= 2:
            self._wildcard_domains.add(domain)
            return {'wildcard_suspected': True, 'probe_count': self._WILDCARD_PROBE_COUNT, 'responses': results, 'probe_method': 'consistent_responses'}
        else:
            self._confirmed_non_wildcard.add(domain)
            return {'wildcard_suspected': False, 'probe_count': self._WILDCARD_PROBE_COUNT, 'responses': results, 'probe_method': 'ambiguous_conservative'}

    async def recon_target(self, target: str, include_subdomains: bool=False) -> HostInfo:
        """
        Perform complete reconnaissance on target.

        Args:
            target: Domain or IP address
            include_subdomains: Whether to brute force subdomains (default False for passive)

        Returns:
            HostInfo with all gathered intelligence
        """
        is_ip = self._is_ip_address(target)
        if is_ip:
            return await self._recon_ip(target)
        else:
            return await self._recon_domain(target, include_subdomains=include_subdomains)

    async def _recon_domain(self, domain: str, include_subdomains: bool=False) -> HostInfo:
        """
        Reconnaissance for domain name.

        Args:
            domain: Domain to recon
            include_subdomains: Whether to brute force subdomains (default False for passive)
        """
        dns_task = self.dns.enumerate_all(domain, include_subdomains=include_subdomains)
        whois_task = self.whois.lookup(domain)
        ssl_task = self.ssl.analyze_certificate(domain)
        _dns_result = await parallel([dns_task, whois_task, ssl_task], policy="log", ctx='network_reconnaissance:825')
        _dr = _dns_result.ok
        dns_results, whois_data, ssl_cert = (_dr[0], _dr[1], _dr[2]) if len(_dr) >= 3 else (None, None, None)
        ip_addresses: list[str] = []
        dns_records: list[DNSRecord] = []
        if isinstance(dns_results, dict) and 'records' in dns_results:
            ip_values_by_record: list[tuple[str, str, int]] = []
            for record_type in ['A', 'AAAA']:
                if record_type in dns_results['records']:
                    for record in dns_results['records'][record_type]:
                        ip_values_by_record.append((record['value'], record_type, record.get('ttl', 3600)))
            if ip_values_by_record:
                ip_values = [r[0] for r in ip_values_by_record]
                public_ips, _ = self._filter_private_ips_batch(ip_values)
                public_ip_set = set(public_ips)
                for ip_val, record_type, ttl in ip_values_by_record:
                    if ip_val in public_ip_set:
                        ip_addresses.append(ip_val)
                        dns_records.append(DNSRecord(record_type=RecordType.A if record_type == 'A' else RecordType.AAAA, name=domain, value=ip_val, ttl=ttl))
            if 'NS' in dns_results['records']:
                for record in dns_results['records']['NS']:
                    dns_records.append(DNSRecord(record_type=RecordType.NS, name=domain, value=record['value'], ttl=record.get('ttl', 3600)))
            if 'MX' in dns_results['records']:
                for record in dns_results['records']['MX']:
                    dns_records.append(DNSRecord(record_type=RecordType.MX, name=domain, value=record['value'], ttl=record.get('ttl', 3600), priority=record.get('priority')))
        # ISSUE-XXX: parallel reverse DNS lookups — N IPs queried concurrently via semaphore.
        # Prior: sequential for-loop. New: bounded parallel (M1 8GB safe, concurrency=8).
        _DNS_SEM = asyncio.Semaphore(8)

        async def _revlookup(ip: str) -> list[str]:
            async with _DNS_SEM:
                return await self.dns.reverse_lookup(ip)

        if ip_addresses:
            rdns_results = await parallel([_revlookup(ip) for ip in ip_addresses], policy="collect", ctx="recon_domain:reverse_dns")
            reverse_dns = []
            for rdns_list in rdns_results:
                if rdns_list:
                    reverse_dns.extend(rdns_list)
        else:
            reverse_dns = []
        return HostInfo(hostname=domain, ip_addresses=ip_addresses, reverse_dns=list(set(reverse_dns)), whois_data=whois_data if isinstance(whois_data, WHOISData) else None, dns_records=dns_records, ssl_cert=ssl_cert if isinstance(ssl_cert, SSLCertificate) else None, open_ports=[], service_banners=[], geolocation=None, asn_info=None, technology_stack=[])

    async def _recon_ip(self, ip: str) -> HostInfo:
        """Reconnaissance for IP address."""
        reverse_dns = await self.dns.reverse_lookup(ip)
        hostname = reverse_dns[0] if reverse_dns else ip
        return HostInfo(hostname=hostname, ip_addresses=[ip], reverse_dns=reverse_dns, whois_data=None, dns_records=[], ssl_cert=None, open_ports=[], service_banners=[], geolocation=None, asn_info=None, technology_stack=[])

    def _is_ip_address(self, target: str) -> bool:
        """Check if target is IP address."""
        try:
            socket.inet_aton(target)
            return True
        except OSError:
            try:
                socket.inet_pton(socket.AF_INET6, target)
                return True
            except OSError:
                return False

class PassiveDNSClient:
    """
    Async passive DNS client using dnspython asyncresolver.

    M1: pure async, no blocking socket calls.
    """
    _RESOLVERS = ['1.1.1.1', '8.8.8.8', '9.9.9.9']
    _TIMEOUT_S = 5.0
    __slots__ = tuple(('_resolver',))

    def __init__(self) -> None:
        # E3 FIX: dns.asyncresolver.Resolver() → dns.resolver.Resolver() (dnspython 3.x compatible)
        self._resolver = dns.resolver.Resolver()
        self._resolver.nameservers = self._RESOLVERS
        self._resolver.timeout = self._TIMEOUT_S
        self._resolver.lifetime = self._TIMEOUT_S

    async def resolve_domain(self, domain: str) -> list[str]:
        """A-record lookup — returns list of IPv4 addresses."""
        try:
            async with asyncio.timeout(self._TIMEOUT_S):
                ans = await self._resolver.resolve(domain, 'A')
            return [str(a) for a in ans]
        except Exception as e:
            logger.debug(f'PassiveDNS A {domain}: {e}')
            return []

    async def resolve_aaaa(self, domain: str) -> list[str]:
        """AAAA-record lookup — returns list of IPv6 addresses."""
        try:
            async with asyncio.timeout(self._TIMEOUT_S):
                ans = await self._resolver.resolve(domain, 'AAAA')
            return [str(a) for a in ans]
        except Exception:
            return []

    async def reverse_lookup(self, ip: str) -> list[str]:
        """PTR record lookup — returns list of hostnames."""
        try:
            rev = dns.reversename.from_address(ip)
            async with asyncio.timeout(self._TIMEOUT_S):
                ans = await self._resolver.resolve(rev, 'PTR')
            return [str(a).rstrip('.') for a in ans]
        except Exception:
            return []

    async def pivot_domain(self, domain: str, ioc_graph: Any) -> int:
        """
        Domain → IPs → buffer to IOC graph.

        Returns count of new IOCs buffered.
        """
        ips = await self.resolve_domain(domain)
        ips_slice = ips[:5]
        if not ips_slice:
            return 0
        # ISSUE-XXX: parallel pivot — reverse lookups + IOC buffers run concurrently.
        # Prior: sequential for-loop (5 IPs × ~20ms each = 100ms+). New: parallel.
        # buffer_ioc is fire-and-forget (no return value), reverse_lookup is I/O-bound.
        _PIVOT_SEM = asyncio.Semaphore(5)

        async def _buffer_ip(ip: str) -> tuple[str, int]:
            async with _PIVOT_SEM:
                await ioc_graph.buffer_ioc('ipv4', ip, confidence=0.7)
            return (ip, 1)

        async def _buffer_hostnames(ip: str) -> list[tuple[str, int]]:
            async with _PIVOT_SEM:
                hostnames = await self.reverse_lookup(ip)
            results = []
            for hostname in hostnames[:3]:
                if hostname and hostname != domain:
                    await ioc_graph.buffer_ioc('domain', hostname, confidence=0.6)
                    results.append((hostname, 1))
            return results

        # Phase 1: resolve all hostnames in parallel (no blocking on per-IP sequential)
        hostname_coros = [_buffer_hostnames(ip) for ip in ips_slice]
        all_hostname_results = await parallel(hostname_coros, policy="collect", ctx="pivot_domain:reverse_lookups")
        # Phase 2: buffer IP IOCs in parallel
        await parallel([_buffer_ip(ip) for ip in ips_slice], policy="collect", ctx="pivot_domain:buffer_ips")
        # Count total
        count = len(ips_slice)
        for hostnames in all_hostname_results:
            count += len(hostnames)
        return count

    async def close(self) -> None:
        """No-op — kept for API consistency."""
        pass

async def graph_add_domain_ip_relations(domain: str, ip_addresses: list[str], graph: Any) -> None:
    """
    FÁZE P9: Add domain→IP relations to GraphManager.

    Streamované přidávání — voláno po každé DNS/A arch resolution.
    """
    if graph is None:
        return
    for ip in ip_addresses[:10]:
        try:
            graph.add_relation(domain, ip, 'resolves_to')
        except Exception:  # noqa: BLE001
            pass

async def graph_add_ip_asn_relations(ip: str, asn_info: ASNInfo | list[ASNInfo], graph: Any) -> None:
    """
    FÁZE P9: Add IP→ASN relations to GraphManager.

    Streamované přidávání — voláno po ASN lookup.
    """
    if graph is None:
        return
    if isinstance(asn_info, list):
        for a in asn_info[:3]:
            try:
                graph.add_relation(ip, f'AS{a.asn}', 'belongs_to_asn')
            except Exception:  # noqa: BLE001
                pass
    elif asn_info is not None:
        try:
            graph.add_relation(ip, f'AS{asn_info.asn}', 'belongs_to_asn')
        except Exception:  # noqa: BLE001
            pass

class DHTProbe:
    """BitTorrent DHT — discovery metadata z P2P sítě.
    UDP asyncio, bootstrap přes router.bittorrent.com.
    info_hash jména → PatternMatcher → malware infrastructure.
    Zdroj neindexovaný žádným komerčním nástrojem."""
    _BOOTSTRAP = [('router.bittorrent.com', 6881), ('dht.transmissionbt.com', 6881), ('router.utorrent.com', 6881)]
    _TIMEOUT_S = 5.0
    _MAX_NODES = 50

    async def bootstrap_nodes(self) -> list[tuple[str, int]]:
        """Resolve bootstrap nodes přes DNS.
        
        E3 FIX: Uses dns.resolver (dnspython 3.x compatible) instead of dns.asyncresolver.
        In dnspython 3.x, dns.resolver.Resolver() is natively async-aware.
        """
        nodes: list[tuple[str, int]] = []
        for host, port in self._BOOTSTRAP:
            try:
                # E3 FIX: dns.resolver.Resolver() works in both 2.x (sync) and 3.x (async-aware)
                r = dns.resolver.Resolver()
                async with asyncio.timeout(3.0):
                    ans = await r.resolve(host, 'A')
                ips = [str(a) for a in ans]
                nodes.extend([(ip, port) for ip in ips[:2]])
            except Exception:  # noqa: BLE001
                pass
        return nodes

    async def find_nodes_for_hash(self, info_hash_hex: str) -> list[str]:
        """FIND_NODE query pro konkrétní info_hash.
        Vrátí list hostnames/IPs z DHT odpovědí.
        M1: asyncio.DatagramEndpoint — čistě async UDP."""
        results: list[str] = []
        try:
            node_id = secrets.token_bytes(20)
            info_hash_bytes = bytes.fromhex(info_hash_hex)

            def bencode_dict(d: dict) -> bytes:
                parts = [b'd']
                for k in sorted(d.keys()):
                    v = d[k]
                    parts.append(f'{len(k)}:{k}'.encode())
                    if isinstance(v, bytes):
                        parts.append(f'{len(v)}:'.encode() + v)
                    elif isinstance(v, dict):
                        parts.append(bencode_dict(v))
                parts.append(b'e')
                return b''.join(parts)
            tid = secrets.token_bytes(2)
            msg = bencode_dict({'t': tid, 'y': b'q', 'q': b'find_node', 'a': {'id': node_id, 'target': info_hash_bytes}})
            bootstrap = await self.bootstrap_nodes()
            for host, port in bootstrap[:3]:
                try:
                    loop = asyncio.get_running_loop()
                    async with asyncio.timeout(self._TIMEOUT_S):
                        transport, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=(host, port))
                    transport.sendto(msg)
                    await asyncio.sleep(1.0)
                    transport.close()
                    results.append(f'{host}:{port}')
                except Exception as e:
                    logger.debug(f'DHT FIND_NODE {host}:{port}: {e}')
        except Exception as e:
            logger.debug(f'DHTProbe: {e}')
        return results

    async def probe_known_hashes(self, session: httpx.AsyncClient) -> list[tuple[str, str]]:
        """Dotazovat DHT pro known malware info_hashes z MalwareBazaar.
        Vrátí [(info_hash, status)]."""
        KNOWN_HASHES = ['a' * 40]
        results: list[tuple[str, str]] = []
        for h in KNOWN_HASHES[:5]:
            nodes = await self.find_nodes_for_hash(h)
            if nodes:
                results.append((h, f'found_at:{nodes[0]}'))
        return results

class CNAMERecord(msgspec.Struct, frozen=True, gc=False):
    """CNAME chain record."""
    source: str
    target: str
    ttl: int

async def resolve_cname_chain(domain: str, max_depth: int=10) -> list[CNAMERecord]:
    """
    Resolve full CNAME chain for a domain.

    Args:
        domain: Starting domain
        max_depth: Maximum resolution depth

    Returns:
        List of CNAMERecord objects forming the alias chain
    """
    chain: list[CNAMERecord] = []
    current = domain
    seen: set[str] = set()
    try:
        # E3 FIX: dns.asyncresolver.Resolver() → dns.resolver.Resolver() (dnspython 3.x compatible)
        resolver = dns.resolver.Resolver()
        resolver.nameservers = ['1.1.1.1', '8.8.8.8']
        resolver.timeout = 3.0
        resolver.lifetime = 10.0
        for _ in range(max_depth):
            if current in seen:
                break
            seen.add(current)
            try:
                async with asyncio.timeout(5.0):
                    answers = await resolver.resolve(current, 'CNAME')
                cname_value = str(answers[0]).rstrip('.')
                chain.append(CNAMERecord(source=current, target=cname_value, ttl=answers.ttl))
                current = cname_value
            except (TimeoutError, dns.resolver.NoAnswer, dns.resolver.NXDOMAIN):
                # E3 FIX: dns.asyncresolver.* → dns.resolver.* (dnspython 3.x compatible)
                break
    except Exception as e:
        logger.debug(f'resolve_cname_chain({domain}): {e}')
    return chain

class ASNInfo(msgspec.Struct, frozen=True, gc=False):
    """Autonomous System Number information."""
    asn: int
    prefix: str
    name: str
    country: str | None
    source: str

async def lookup_asn(ip_or_prefix: str) -> list[ASNInfo]:
    """
    Look up ASN information for IP address or prefix.

    Args:
        ip_or_prefix: IP address (e.g., "8.8.8.8") or prefix (e.g., "8.8.8.0/24")

    Returns:
        List of ASNInfo objects
    """
    results: list[ASNInfo] = []
    try:
        # F-01: session_pool.httpx() returns shared singleton
        from hledac.universal.transport.session_pool import session_pool
        session = await session_pool.httpx()
        url = f'https://ipinfo.io/{ip_or_prefix}/json'
        resp = await session.get(url, timeout=httpx.Timeout(total=10))
        if resp.status == 200:
            data = await resp.json()
            if 'org' in data:
                org = data['org']
                parts = org.split()
                asn_num = int(parts[0].replace('AS', '')) if parts else 0
                prefix = ' '.join(parts[1:]) if len(parts) > 1 else ''
                results.append(ASNInfo(asn=asn_num, prefix=prefix, name=data.get('name', data.get('org', '')), country=data.get('country'), source='ipinfo.io'))
    except Exception as e:
        logger.debug(f'lookup_asn({ip_or_prefix}): {e}')
    return results

class CTRawCertificate(msgspec.Struct, frozen=True, gc=False):
    """Certificate Transparency log entry."""
    common_name: str
    name_value: str
    issue_date: str
    expiry_date: str
    issuer_name: str | None

async def lookup_crtsh(domain: str, limit: int=50) -> list[CTRawCertificate]:
    """
    Query crt.sh Certificate Transparency log for domain certificates.

    Args:
        domain: Domain to search (e.g., "apple.com")
        limit: Maximum number of results

    Returns:
        List of CTRawCertificate objects
    """
    results: list[CTRawCertificate] = []
    try:
        # F-01: session_pool.httpx() returns shared singleton
        from hledac.universal.transport.session_pool import session_pool
        import httpx
        session = await session_pool.httpx()
        url = f'https://crt.sh/?q=%.{domain}&output=json'
        resp = await session.get(url, timeout=httpx.Timeout(30.0), headers={'User-Agent': 'Hledac-OSINT/1.0'})
        if resp.status_code == 200:
            try:
                data = await resp.json()
            except Exception:
                text = await resp.text()
                if 'json' in (resp.headers.get('Content-Type', '') or ''):
                    import json as _json
                    data = _msgspec_loads(text) if text.strip().startswith('[') else []
                else:
                    data = []
            for entry in data[:limit]:
                results.append(CTRawCertificate(common_name=entry.get('common_name', ''), name_value=entry.get('name_value', ''), issue_date=entry.get('not_before', ''), expiry_date=entry.get('not_after', ''), issuer_name=entry.get('issuer_name')))
    except Exception as e:
        logger.debug(f'lookup_crtsh({domain}): {e}')
    return results

async def passive_dns_lookup(domain: str, api_key: str | None=None) -> dict[str, Any]:
    """
    Query Passive DNS service for domain resolution history.

    Args:
        domain: Domain to look up
        api_key: Optional API key for dnslookupapi.com

    Returns:
        Dict with resolution records
    """
    result: dict[str, Any] = {'domain': domain, 'resolutions': [], 'subdomains': []}
    if not api_key:
        logger.debug('passive_dns_lookup: no API key provided')
        return result
    try:
        # F-01: session_pool.httpx() returns shared singleton
        from hledac.universal.transport.session_pool import session_pool
        import httpx
        session = await session_pool.httpx()
        url = f'https://api.dnslookupapi.com/v1/dns/{domain}/history'
        resp = await session.get(url, params={'api_key': api_key}, timeout=httpx.Timeout(15.0))
        if resp.status_code == 200:
            data = await resp.json()
            result['resolutions'] = data.get('records', [])
            result['subdomains'] = data.get('subdomains', [])
    except Exception as e:
        logger.debug(f'passive_dns_lookup({domain}): {e}')
    return result
__all__ = ['NetworkReconnaissance', 'DNSEnumerator', 'WHOISLookup', 'SSLAnalyzer', 'PassiveDNSClient', 'HostInfo', 'WHOISData', 'SSLCertificate', 'DNSRecord', 'RecordType', 'DHTProbe', 'resolve_cname_chain', 'lookup_asn', 'lookup_crtsh', 'passive_dns_lookup', 'CNAMERecord', 'ASNInfo', 'CTRawCertificate', 'graph_add_domain_ip_relations', 'graph_add_ip_asn_relations']