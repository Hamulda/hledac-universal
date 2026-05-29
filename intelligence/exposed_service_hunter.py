"""
Exposed Service Hunter
======================

Discovers exposed services and misconfigurations for security research.
Self-hosted on M1 8GB - no external APIs required.

Features:
- S3 bucket enumeration using common naming patterns (40+ patterns)
- Exposed database detection: MongoDB, Redis, Elasticsearch, CouchDB
- GraphQL introspection discovery
- Certificate transparency logging queries (crt.sh)
- Docker API exposure detection
- Kubernetes API detection

M1 Optimized: Async I/O, connection pooling, minimal memory, no heavy ML models
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)


class ServiceType(Enum):
    """Types of exposed services."""
    S3_BUCKET = "s3"
    MONGODB = "mongodb"
    REDIS = "redis"
    ELASTICSEARCH = "elasticsearch"
    COUCHDB = "couchdb"
    GRAPHQL = "graphql"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    CERTIFICATE = "certificate"


class ExposureType(Enum):
    """Types of exposure."""
    OPEN = "open"
    MISCONFIGURED = "misconfigured"
    AUTH_BYPASS = "auth_bypass"
    PUBLIC = "public"
    LEAKED = "leaked"


class RiskLevel(Enum):
    """Risk levels for exposed services."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ExposedService:
    """Represents a discovered exposed service."""
    service_type: str
    host: str
    port: int
    exposure_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    risk_level: str = RiskLevel.MEDIUM.value
    discovered_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "service_type": self.service_type,
            "host": self.host,
            "port": self.port,
            "exposure_type": self.exposure_type,
            "metadata": self.metadata,
            "risk_level": self.risk_level,
            "discovered_at": self.discovered_at.isoformat()
        }


@dataclass
class S3Bucket:
    """S3 bucket information."""
    bucket_name: str
    region: str | None
    is_listable: bool
    has_files: bool
    file_count: int | None
    total_size: int | None
    permissions: list[str]


@dataclass
class CertificateInfo:
    """Certificate transparency information."""
    domain: str
    issuer: str
    not_before: datetime
    not_after: datetime
    san_domains: list[str]
    fingerprint: str


class S3BucketEnumerator:
    """
    S3 bucket enumeration using common naming patterns.

    Uses HTTP HEAD requests to check bucket existence and permissions.
    No AWS credentials required.
    """

    # Common S3 bucket naming patterns
    BUCKET_PATTERNS = [
        "{target}",
        "{target}-prod",
        "{target}-production",
        "{target}-dev",
        "{target}-development",
        "{target}-staging",
        "{target}-stage",
        "{target}-test",
        "{target}-testing",
        "{target}-qa",
        "{target}-uat",
        "{target}-demo",
        "{target}-backup",
        "{target}-backups",
        "{target}-archive",
        "{target}-archives",
        "{target}-logs",
        "{target}-data",
        "{target}-assets",
        "{target}-media",
        "{target}-files",
        "{target}-uploads",
        "{target}-downloads",
        "{target}-static",
        "{target}-content",
        "{target}-resources",
        "{target}-public",
        "{target}-private",
        "{target}-internal",
        "{target}-config",
        "{target}-configs",
        "{target}-secrets",
        "{target}-credentials",
        "{target}-db",
        "{target}-database",
        "{target}-app",
        "{target}-application",
        "{target}-web",
        "{target}-www",
        "{target}-api",
        "{target}-cdn",
        "{target}-images",
        "{target}-docs",
        "{target}-documents",
        "{target}-reports",
        "{target}-exports",
    ]

    S3_REGIONS = [
        "us-east-1", "us-east-2", "us-west-1", "us-west-2",
        "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1",
        "eu-north-1", "ap-southeast-1", "ap-southeast-2",
        "ap-northeast-1", "ap-northeast-2", "ap-south-1",
        "ca-central-1", "sa-east-1"
    ]

    def __init__(self, session: aiohttp.ClientSession | None = None):
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                connector=aiohttp.TCPConnector(limit=50, limit_per_host=10)
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def enumerate_buckets(
        self,
        target: str,
        max_concurrent: int = 20
    ) -> list[ExposedService]:
        """
        Enumerate S3 buckets using naming patterns.

        Args:
            target: Target domain or company name
            max_concurrent: Maximum concurrent requests

        Returns:
            List of exposed S3 buckets
        """
        findings = []
        target_clean = target.replace(".", "-").replace("_", "-").lower()

        # Generate bucket names from patterns
        bucket_names = set()
        for pattern in self.BUCKET_PATTERNS:
            bucket_name = pattern.format(target=target_clean)
            bucket_names.add(bucket_name)
            # Also try without hyphens
            bucket_names.add(bucket_name.replace("-", ""))
            # Also try with underscores
            bucket_names.add(bucket_name.replace("-", "_"))

        logger.info(f"Checking {len(bucket_names)} potential S3 buckets for {target}")

        semaphore = asyncio.Semaphore(max_concurrent)

        async def check_bucket(bucket_name: str) -> ExposedService | None:
            async with semaphore:
                try:
                    result = await self._check_bucket_exists(bucket_name)
                    if result:
                        logger.info(f"Found S3 bucket: {bucket_name}")
                        return result
                except Exception as e:
                    logger.debug(f"Error checking bucket {bucket_name}: {e}")
                return None

        # Check all buckets concurrently
        tasks = [check_bucket(name) for name in bucket_names]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if result:
                findings.append(result)

        return findings

    async def _check_bucket_exists(self, bucket_name: str) -> ExposedService | None:
        """Check if an S3 bucket exists and is accessible."""
        if not self.session:
            return None

        # Try multiple regions
        regions_to_try = [None] + self.S3_REGIONS[:5]  # Global + 5 regions

        for region in regions_to_try:
            try:
                if region:
                    url = f"https://s3.{region}.amazonaws.com/{bucket_name}"
                else:
                    url = f"https://{bucket_name}.s3.amazonaws.com"

                async with self.session.head(url, allow_redirects=True) as resp:
                    if resp.status == 200:
                        # Bucket exists and is listable
                        return ExposedService(
                            service_type=ServiceType.S3_BUCKET.value,
                            host=f"{bucket_name}.s3.amazonaws.com",
                            port=443,
                            exposure_type=ExposureType.OPEN.value,
                            risk_level=RiskLevel.CRITICAL.value,
                            metadata={
                                "bucket_name": bucket_name,
                                "region": region,
                                "listable": True,
                                "url": url
                            }
                        )
                    elif resp.status == 403:
                        # Bucket exists but is private
                        return ExposedService(
                            service_type=ServiceType.S3_BUCKET.value,
                            host=f"{bucket_name}.s3.amazonaws.com",
                            port=443,
                            exposure_type=ExposureType.PUBLIC.value,
                            risk_level=RiskLevel.LOW.value,
                            metadata={
                                "bucket_name": bucket_name,
                                "region": region,
                                "listable": False,
                                "exists": True,
                                "url": url
                            }
                        )
                    elif resp.status == 404:
                        # Bucket doesn't exist in this region
                        continue

            except TimeoutError:
                continue
            except Exception as e:
                logger.debug(f"Error checking bucket {bucket_name}: {e}")
                continue

        return None

    async def check_bucket_permissions(
        self,
        bucket_name: str
    ) -> dict[str, Any]:
        """Check specific permissions on an S3 bucket."""
        if not self.session:
            return {}

        permissions = {}
        checks = [
            ("list", f"https://{bucket_name}.s3.amazonaws.com/"),
            ("acl", f"https://{bucket_name}.s3.amazonaws.com/?acl"),
            ("policy", f"https://{bucket_name}.s3.amazonaws.com/?policy"),
            ("cors", f"https://{bucket_name}.s3.amazonaws.com/?cors"),
        ]

        for perm_name, url in checks:
            try:
                async with self.session.get(url, timeout=5) as resp:
                    permissions[perm_name] = {
                        "accessible": resp.status == 200,
                        "status": resp.status
                    }
            except Exception as e:
                permissions[perm_name] = {"accessible": False, "error": str(e)}

        return permissions


class DatabasePortScanner:
    """
    Scanner for exposed database ports.

    Checks common database ports for open access.
    Uses lightweight TCP connection checks.
    """

    # Database port mappings
    DATABASE_PORTS = {
        27017: (ServiceType.MONGODB, "MongoDB"),
        27018: (ServiceType.MONGODB, "MongoDB Shard"),
        27019: (ServiceType.MONGODB, "MongoDB Config"),
        6379: (ServiceType.REDIS, "Redis"),
        6380: (ServiceType.REDIS, "Redis Alternate"),
        9200: (ServiceType.ELASTICSEARCH, "Elasticsearch"),
        9300: (ServiceType.ELASTICSEARCH, "Elasticsearch Transport"),
        5984: (ServiceType.COUCHDB, "CouchDB"),
        6984: (ServiceType.COUCHDB, "CouchDB SSL"),
        5432: ("postgresql", "PostgreSQL"),
        3306: ("mysql", "MySQL"),
        1433: ("mssql", "Microsoft SQL Server"),
        1521: ("oracle", "Oracle Database"),
        9042: ("cassandra", "Cassandra"),
        7474: ("neo4j", "Neo4j"),
        8529: ("arangodb", "ArangoDB"),
    }

    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout

    async def scan_hosts(
        self,
        hosts: list[str],
        ports: list[int] | None = None,
        max_concurrent: int = 50
    ) -> list[ExposedService]:
        """
        Scan hosts for exposed database ports.

        Args:
            hosts: List of hostnames or IPs to scan
            ports: Specific ports to check (default: all database ports)
            max_concurrent: Maximum concurrent connections

        Returns:
            List of exposed database services
        """
        findings = []
        ports_to_check = ports or list(self.DATABASE_PORTS.keys())

        logger.info(f"Scanning {len(hosts)} hosts on {len(ports_to_check)} ports")

        semaphore = asyncio.Semaphore(max_concurrent)

        async def check_port(host: str, port: int) -> ExposedService | None:
            async with semaphore:
                try:
                    result = await self._check_port(host, port)
                    if result:
                        logger.info(f"Found exposed database: {host}:{port}")
                        return result
                except Exception as e:
                    logger.debug(f"Error scanning {host}:{port}: {e}")
                return None

        # Create all scan tasks
        tasks = []
        for host in hosts:
            for port in ports_to_check:
                tasks.append(check_port(host, port))

        # Run scans concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if result:
                findings.append(result)

        return findings

    async def _check_port(self, host: str, port: int) -> ExposedService | None:
        """Check if a specific port is open and identify service."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self.timeout
            )

            # Try to grab banner
            banner = ""
            try:
                writer.write(b"\r\n")
                await writer.drain()
                banner = await asyncio.wait_for(reader.read(1024), timeout=2)
                banner = banner.decode("utf-8", errors="ignore").strip()
            except Exception:
                pass

            writer.close()
            await writer.wait_closed()

            # Determine service type
            service_info = self.DATABASE_PORTS.get(port, ("unknown", "Unknown"))
            service_type, service_name = service_info

            # Assess risk level
            risk_level = RiskLevel.CRITICAL.value if port in [27017, 6379, 9200, 5984] else RiskLevel.HIGH.value

            return ExposedService(
                service_type=service_type.value if isinstance(service_type, ServiceType) else service_type,
                host=host,
                port=port,
                exposure_type=ExposureType.OPEN.value,
                risk_level=risk_level,
                metadata={
                    "service_name": service_name,
                    "banner": banner[:200] if banner else None,
                    "protocol": "tcp"
                }
            )

        except TimeoutError:
            return None
        except ConnectionRefusedError:
            return None
        except Exception as e:
            logger.debug(f"Error checking {host}:{port}: {e}")
            return None

    async def test_mongodb_auth(self, host: str, port: int = 27017) -> dict[str, Any]:
        """Test MongoDB for authentication requirements."""
        result = {"auth_required": None, "version": None}

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self.timeout
            )

            # Send MongoDB isMaster command
            is_master_cmd = b'\x3d\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\xd4\x07\x00\x00'
            is_master_cmd += b'\x00\x00\x00\x00\x61\x64\x6d\x69\x6e\x2e\x24\x63\x6d\x64\x00\x00'
            is_master_cmd += b'\x00\x00\x00\xff\xff\xff\xff\x13\x00\x00\x00\x10\x69\x73\x4d\x61'
            is_master_cmd += b'\x73\x74\x65\x72\x00\x01\x00\x00\x00\x00'

            writer.write(is_master_cmd)
            await writer.drain()

            response = await asyncio.wait_for(reader.read(1024), timeout=5)
            writer.close()
            await writer.wait_closed()

            # Parse response for auth info
            if b"unauthorized" in response.lower() or b"auth" in response.lower():
                result["auth_required"] = True
            else:
                result["auth_required"] = False

            # Try to extract version
            version_match = re.search(rb'"version"\s*:\s*"([^"]+)"', response)
            if version_match:
                result["version"] = version_match.group(1).decode("utf-8", errors="ignore")

        except Exception as e:
            result["error"] = str(e)

        return result

    async def test_redis_auth(self, host: str, port: int = 6379) -> dict[str, Any]:
        """Test Redis for authentication requirements."""
        result = {"auth_required": None, "version": None}

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self.timeout
            )

            # Try INFO command
            writer.write(b"INFO\r\n")
            await writer.drain()

            response = await asyncio.wait_for(reader.read(2048), timeout=5)
            writer.close()
            await writer.wait_closed()

            response_str = response.decode("utf-8", errors="ignore")

            if "NOAUTH" in response_str or "authentication" in response_str.lower():
                result["auth_required"] = True
            elif "redis_version" in response_str:
                result["auth_required"] = False
                # Extract version
                version_match = re.search(r'redis_version:(\S+)', response_str)
                if version_match:
                    result["version"] = version_match.group(1)

        except Exception as e:
            result["error"] = str(e)

        return result


class GraphQLIntrospector:
    """
    GraphQL introspection discovery.

    Discovers GraphQL endpoints and extracts schema information.
    """

    # Common GraphQL endpoints
    COMMON_ENDPOINTS = [
        "/graphql",
        "/api/graphql",
        "/v1/graphql",
        "/v2/graphql",
        "/query",
        "/api",
        "/gql",
        "/graphql/v1",
        "/graphql/v2",
        "/api/v1/graphql",
        "/api/v2/graphql",
        "/explorer",
        "/playground",
        "/graphiql",
        "/altair",
    ]

    INTROSPECTION_QUERY = """
    query IntrospectionQuery {
      __schema {
        queryType { name }
        mutationType { name }
        subscriptionType { name }
        types {
          name
          kind
          description
          fields {
            name
            description
            type {
              name
              kind
            }
          }
        }
      }
    }
    """

    def __init__(self, session: aiohttp.ClientSession | None = None):
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def discover_endpoints(
        self,
        base_url: str,
        max_concurrent: int = 10
    ) -> list[ExposedService]:
        """
        Discover GraphQL endpoints on a target.

        Args:
            base_url: Base URL to scan
            max_concurrent: Maximum concurrent requests

        Returns:
            List of discovered GraphQL endpoints
        """
        findings = []
        base_url = base_url.rstrip("/")

        semaphore = asyncio.Semaphore(max_concurrent)

        async def check_endpoint(endpoint: str) -> ExposedService | None:
            async with semaphore:
                try:
                    result = await self._check_endpoint(f"{base_url}{endpoint}")
                    if result:
                        logger.info(f"Found GraphQL endpoint: {endpoint}")
                        return result
                except Exception as e:
                    logger.debug(f"Error checking {endpoint}: {e}")
                return None

        # Check all endpoints concurrently
        tasks = [check_endpoint(ep) for ep in self.COMMON_ENDPOINTS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if result:
                findings.append(result)

        return findings

    async def _check_endpoint(self, url: str) -> ExposedService | None:
        """Check if a URL is a GraphQL endpoint with introspection enabled."""
        if not self.session:
            return None

        try:
            # First, try a simple POST with introspection query
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json"
            }

            payload = {
                "query": self.INTROSPECTION_QUERY,
                "operationName": "IntrospectionQuery"
            }

            async with self.session.post(
                url,
                headers=headers,
                json=payload,
                timeout=10
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    if data.get("data", {}).get("__schema"):
                        schema = data["data"]["__schema"]

                        # Extract type counts
                        types = schema.get("types", [])
                        query_type = schema.get("queryType", {}).get("name")
                        mutation_type = schema.get("mutationType", {}).get("name")

                        return ExposedService(
                            service_type=ServiceType.GRAPHQL.value,
                            host=urlparse(url).netloc,
                            port=443 if url.startswith("https") else 80,
                            exposure_type=ExposureType.MISCONFIGURED.value,
                            risk_level=RiskLevel.HIGH.value,
                            metadata={
                                "endpoint": url,
                                "introspection_enabled": True,
                                "query_type": query_type,
                                "mutation_type": mutation_type,
                                "type_count": len(types),
                                "has_subscription": schema.get("subscriptionType") is not None
                            }
                        )

                # Check for GraphQL without introspection
                elif resp.status in [400, 401, 403]:
                    # Might be GraphQL but with introspection disabled
                    text = await resp.text()
                    if "introspection" in text.lower() or "__schema" in text.lower():
                        return ExposedService(
                            service_type=ServiceType.GRAPHQL.value,
                            host=urlparse(url).netloc,
                            port=443 if url.startswith("https") else 80,
                            exposure_type=ExposureType.PUBLIC.value,
                            risk_level=RiskLevel.MEDIUM.value,
                            metadata={
                                "endpoint": url,
                                "introspection_enabled": False,
                                "note": "GraphQL endpoint detected but introspection disabled"
                            }
                        )

        except aiohttp.ContentTypeError:
            # Not JSON response, probably not GraphQL
            pass
        except Exception as e:
            logger.debug(f"Error checking GraphQL endpoint {url}: {e}")

        return None

    async def introspect_endpoint(self, url: str) -> dict[str, Any] | None:
        """Perform full introspection on a GraphQL endpoint."""
        if not self.session:
            return None

        try:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json"
            }

            payload = {
                "query": self.INTROSPECTION_QUERY,
                "operationName": "IntrospectionQuery"
            }

            async with self.session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()

        except Exception as e:
            logger.error(f"Introspection failed for {url}: {e}")

        return None


class CertificateTransparency:
    """
    Certificate Transparency log queries via crt.sh.

    Queries the public crt.sh service for certificate information.
    No API key required.
    """

    CRTSH_API = "https://crt.sh/json"

    def __init__(self, session: aiohttp.ClientSession | None = None):
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def query_domain(
        self,
        domain: str,
        include_subdomains: bool = True
    ) -> list[str]:
        """
        Query certificate transparency logs for a domain.

        Args:
            domain: Domain to query
            include_subdomains: Include wildcard subdomains

        Returns:
            List of discovered subdomains
        """
        subdomains = set()

        if not self.session:
            return list(subdomains)

        try:
            # Query crt.sh
            params = {
                "q": domain,
                "output": "json"
            }

            if include_subdomains:
                params["q"] = f"%.{domain}"

            async with self.session.get(self.CRTSH_API, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    for entry in data:
                        # Extract name values
                        name_value = entry.get("name_value", "")
                        common_name = entry.get("common_name", "")

                        # Add all domains found
                        for name in [name_value, common_name]:
                            if name:
                                # Handle multiple domains (newline separated)
                                for subdomain in name.split("\n"):
                                    subdomain = subdomain.strip()
                                    if subdomain and domain in subdomain:
                                        subdomains.add(subdomain)

                    logger.info(f"Found {len(subdomains)} subdomains via CT logs for {domain}")

        except Exception as e:
            logger.error(f"CT log query failed for {domain}: {e}")

        return sorted(subdomains)

    async def get_certificate_details(
        self,
        domain: str
    ) -> list[CertificateInfo]:
        """Get detailed certificate information from CT logs."""
        certificates = []

        if not self.session:
            return certificates

        try:
            params = {
                "q": domain,
                "output": "json"
            }

            async with self.session.get(self.CRTSH_API, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    for entry in data:
                        try:
                            cert = CertificateInfo(
                                domain=entry.get("common_name", domain),
                                issuer=entry.get("issuer_name", "Unknown"),
                                not_before=datetime.strptime(
                                    entry.get("not_before", "1970-01-01"),
                                    "%Y-%m-%d"
                                ),
                                not_after=datetime.strptime(
                                    entry.get("not_after", "1970-01-01"),
                                    "%Y-%m-%d"
                                ),
                                san_domains=entry.get("name_value", "").split("\n"),
                                fingerprint=entry.get("cert_sha256", "")
                            )
                            certificates.append(cert)
                        except Exception as e:
                            logger.debug(f"Error parsing certificate entry: {e}")

        except Exception as e:
            logger.error(f"Certificate details query failed: {e}")

        return certificates


class ContainerAPIExplorer:
    """
    Docker and Kubernetes API explorer.

    Detects exposed container orchestration APIs.
    """

    DOCKER_PORTS = [2375, 2376, 2377]
    KUBERNETES_PORTS = [6443, 8080, 10250, 10255, 8443]

    DOCKER_ENDPOINTS = ["/version", "/info", "/containers/json", "/images/json"]
    K8S_ENDPOINTS = ["/api", "/api/v1", "/apis", "/version", "/healthz"]

    def __init__(self, session: aiohttp.ClientSession | None = None):
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def scan_docker_apis(
        self,
        hosts: list[str],
        max_concurrent: int = 20
    ) -> list[ExposedService]:
        """Scan for exposed Docker APIs."""
        findings = []

        semaphore = asyncio.Semaphore(max_concurrent)

        async def check_host(host: str, port: int) -> ExposedService | None:
            async with semaphore:
                try:
                    result = await self._check_docker_api(host, port)
                    if result:
                        logger.info(f"Found exposed Docker API: {host}:{port}")
                        return result
                except Exception as e:
                    logger.debug(f"Error checking Docker API {host}:{port}: {e}")
                return None

        tasks = []
        for host in hosts:
            for port in self.DOCKER_PORTS:
                tasks.append(check_host(host, port))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if result:
                findings.append(result)

        return findings

    async def _check_docker_api(self, host: str, port: int) -> ExposedService | None:
        """Check if a Docker API is exposed."""
        if not self.session:
            return None

        protocol = "https" if port == 2376 else "http"

        try:
            # Try the version endpoint
            url = f"{protocol}://{host}:{port}/version"

            async with self.session.get(url, timeout=5, ssl=False) as resp:
                if resp.status == 200:
                    try:
                        data = await resp.json()

                        if "Version" in data or "ApiVersion" in data:
                            return ExposedService(
                                service_type=ServiceType.DOCKER.value,
                                host=host,
                                port=port,
                                exposure_type=ExposureType.OPEN.value,
                                risk_level=RiskLevel.CRITICAL.value,
                                metadata={
                                    "version": data.get("Version"),
                                    "api_version": data.get("ApiVersion"),
                                    "platform": data.get("Platform", {}).get("Name"),
                                    "endpoint": url
                                }
                            )
                    except Exception:
                        # Not JSON, but endpoint responded
                        return ExposedService(
                            service_type=ServiceType.DOCKER.value,
                            host=host,
                            port=port,
                            exposure_type=ExposureType.OPEN.value,
                            risk_level=RiskLevel.CRITICAL.value,
                            metadata={
                                "endpoint": url,
                                "note": "Docker API responded but not JSON"
                            }
                        )

        except Exception as e:
            logger.debug(f"Docker API check failed for {host}:{port}: {e}")

        return None

    async def scan_kubernetes_apis(
        self,
        hosts: list[str],
        max_concurrent: int = 20
    ) -> list[ExposedService]:
        """Scan for exposed Kubernetes APIs."""
        findings = []

        semaphore = asyncio.Semaphore(max_concurrent)

        async def check_host(host: str, port: int) -> ExposedService | None:
            async with semaphore:
                try:
                    result = await self._check_kubernetes_api(host, port)
                    if result:
                        logger.info(f"Found exposed Kubernetes API: {host}:{port}")
                        return result
                except Exception as e:
                    logger.debug(f"Error checking K8s API {host}:{port}: {e}")
                return None

        tasks = []
        for host in hosts:
            for port in self.KUBERNETES_PORTS:
                tasks.append(check_host(host, port))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if result:
                findings.append(result)

        return findings

    async def _check_kubernetes_api(self, host: str, port: int) -> ExposedService | None:
        """Check if a Kubernetes API is exposed."""
        if not self.session:
            return None

        protocol = "https" if port in [6443, 8443] else "http"

        try:
            # Try the version endpoint
            url = f"{protocol}://{host}:{port}/version"

            async with self.session.get(url, timeout=5, ssl=False) as resp:
                if resp.status == 200:
                    try:
                        data = await resp.json()

                        if "gitVersion" in data or "major" in data:
                            return ExposedService(
                                service_type=ServiceType.KUBERNETES.value,
                                host=host,
                                port=port,
                                exposure_type=ExposureType.OPEN.value,
                                risk_level=RiskLevel.CRITICAL.value,
                                metadata={
                                    "version": data.get("gitVersion"),
                                    "major": data.get("major"),
                                    "minor": data.get("minor"),
                                    "platform": data.get("platform"),
                                    "endpoint": url
                                }
                            )
                    except Exception:
                        pass

                # Check if it's K8s but requires auth
                elif resp.status in [401, 403]:
                    text = await resp.text()
                    if "kubernetes" in text.lower() or "unauthorized" in text.lower():
                        return ExposedService(
                            service_type=ServiceType.KUBERNETES.value,
                            host=host,
                            port=port,
                            exposure_type=ExposureType.AUTH_BYPASS.value,
                            risk_level=RiskLevel.HIGH.value,
                            metadata={
                                "endpoint": url,
                                "auth_required": True,
                                "note": "Kubernetes API requires authentication"
                            }
                        )

        except Exception as e:
            logger.debug(f"K8s API check failed for {host}:{port}: {e}")

        return None


class ExposedServiceHunter:
    """
    Main exposed service hunter.

    Combines all exposed service discovery capabilities:
    - S3 bucket enumeration
    - Database port scanning
    - GraphQL introspection
    - Certificate transparency
    - Docker/Kubernetes API detection

    M1 Optimized: Async I/O, connection pooling, minimal memory

    Example:
        >>> hunter = ExposedServiceHunter()
        >>> results = await hunter.hunt("example.com")
        >>> print(f"Found {len(results['s3_buckets'])} S3 buckets")
    """

    def __init__(self):
        self.session: aiohttp.ClientSession | None = None
        self._s3_enumerator: S3BucketEnumerator | None = None
        self._db_scanner = DatabasePortScanner()
        self._graphql_introspector: GraphQLIntrospector | None = None
        self._ct_logs: CertificateTransparency | None = None
        self._container_explorer: ContainerAPIExplorer | None = None

    async def __aenter__(self):
        """Async context manager entry."""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            connector=aiohttp.TCPConnector(
                limit=100,
                limit_per_host=20,
                enable_cleanup_closed=True,
                force_close=True
            )
        )
        self._s3_enumerator = S3BucketEnumerator(self.session)
        self._graphql_introspector = GraphQLIntrospector(self.session)
        self._ct_logs = CertificateTransparency(self.session)
        self._container_explorer = ContainerAPIExplorer(self.session)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.session:
            await self.session.close()
            self.session = None

    async def enumerate_s3_buckets(self, target: str) -> list[ExposedService]:
        """
        Enumerate S3 buckets for a target.

        Args:
            target: Target domain or company name

        Returns:
            List of exposed S3 buckets
        """
        if not self._s3_enumerator:
            raise RuntimeError("Hunter not initialized. Use async context manager.")

        return await self._s3_enumerator.enumerate_buckets(target)

    async def scan_database_ports(self, hosts: list[str]) -> list[ExposedService]:
        """
        Scan hosts for exposed database ports.

        Args:
            hosts: List of hostnames or IPs

        Returns:
            List of exposed database services
        """
        return await self._db_scanner.scan_hosts(hosts)

    async def query_certificate_transparency(self, domain: str) -> list[str]:
        """
        Query certificate transparency logs.

        Args:
            domain: Domain to query

        Returns:
            List of discovered subdomains
        """
        if not self._ct_logs:
            raise RuntimeError("Hunter not initialized. Use async context manager.")

        return await self._ct_logs.query_domain(domain)

    async def check_graphql_introspection(self, endpoint: str) -> dict | None:
        """
        Check GraphQL endpoint for introspection.

        Args:
            endpoint: GraphQL endpoint URL

        Returns:
            Introspection result or None
        """
        if not self._graphql_introspector:
            raise RuntimeError("Hunter not initialized. Use async context manager.")

        result = await self._graphql_introspector._check_endpoint(endpoint)
        if result:
            return result.to_dict()
        return None

    async def discover_graphql_endpoints(self, base_url: str) -> list[ExposedService]:
        """
        Discover GraphQL endpoints on a target.

        Args:
            base_url: Base URL to scan

        Returns:
            List of discovered GraphQL endpoints
        """
        if not self._graphql_introspector:
            raise RuntimeError("Hunter not initialized. Use async context manager.")

        return await self._graphql_introspector.discover_endpoints(base_url)

    async def scan_container_apis(self, hosts: list[str]) -> list[ExposedService]:
        """
        Scan for exposed Docker and Kubernetes APIs.

        Args:
            hosts: List of hostnames or IPs

        Returns:
            List of exposed container APIs
        """
        if not self._container_explorer:
            raise RuntimeError("Hunter not initialized. Use async context manager.")

        findings = []

        # Scan Docker APIs
        docker_findings = await self._container_explorer.scan_docker_apis(hosts)
        findings.extend(docker_findings)

        # Scan Kubernetes APIs
        k8s_findings = await self._container_explorer.scan_kubernetes_apis(hosts)
        findings.extend(k8s_findings)

        return findings

    async def hunt(self, target: str) -> dict[str, list[ExposedService]]:
        """
        Perform comprehensive exposed service hunt.

        Args:
            target: Target domain or company name

        Returns:
            Dictionary with categorized findings
        """
        results = {
            "s3_buckets": [],
            "databases": [],
            "graphql": [],
            "certificates": [],
            "container_apis": [],
            "all": []
        }

        logger.info(f"Starting exposed service hunt for: {target}")

        # Extract domain from target
        domain = target.replace("https://", "").replace("http://", "").split("/")[0]

        # 1. Enumerate S3 buckets
        try:
            logger.info("Enumerating S3 buckets...")
            s3_findings = await self.enumerate_s3_buckets(target)
            results["s3_buckets"] = s3_findings
            results["all"].extend(s3_findings)
            logger.info(f"Found {len(s3_findings)} S3 buckets")
        except Exception as e:
            logger.error(f"S3 enumeration failed: {e}")

        # 2. Query certificate transparency for subdomains
        try:
            logger.info("Querying certificate transparency logs...")
            subdomains = await self.query_certificate_transparency(domain)
            results["certificates"] = [
                ExposedService(
                    service_type=ServiceType.CERTIFICATE.value,
                    host=subdomain,
                    port=443,
                    exposure_type=ExposureType.PUBLIC.value,
                    risk_level=RiskLevel.LOW.value,
                    metadata={"source": "certificate_transparency"}
                )
                for subdomain in subdomains
            ]
            results["all"].extend(results["certificates"])
            logger.info(f"Found {len(subdomains)} subdomains via CT logs")
        except Exception as e:
            logger.error(f"CT log query failed: {e}")

        # 3. Scan database ports on main domain and discovered subdomains
        try:
            logger.info("Scanning for exposed database ports...")
            hosts_to_scan = [domain] + [s.host for s in results["certificates"]][:10]
            db_findings = await self.scan_database_ports(hosts_to_scan)
            results["databases"] = db_findings
            results["all"].extend(db_findings)
            logger.info(f"Found {len(db_findings)} exposed databases")
        except Exception as e:
            logger.error(f"Database scan failed: {e}")

        # 4. Discover GraphQL endpoints
        try:
            logger.info("Discovering GraphQL endpoints...")
            base_url = f"https://{domain}"
            graphql_findings = await self.discover_graphql_endpoints(base_url)
            results["graphql"] = graphql_findings
            results["all"].extend(graphql_findings)
            logger.info(f"Found {len(graphql_findings)} GraphQL endpoints")
        except Exception as e:
            logger.error(f"GraphQL discovery failed: {e}")

        # 5. Scan for container APIs
        try:
            logger.info("Scanning for container APIs...")
            hosts_to_scan = [domain]
            container_findings = await self.scan_container_apis(hosts_to_scan)
            results["container_apis"] = container_findings
            results["all"].extend(container_findings)
            logger.info(f"Found {len(container_findings)} exposed container APIs")
        except Exception as e:
            logger.error(f"Container API scan failed: {e}")

        logger.info(f"Hunt complete. Total findings: {len(results['all'])}")

        return results

    def get_statistics(self) -> dict[str, Any]:
        """Get hunter statistics."""
        return {
            "session_active": self.session is not None,
            "components": {
                "s3_enumerator": self._s3_enumerator is not None,
                "db_scanner": True,
                "graphql_introspector": self._graphql_introspector is not None,
                "ct_logs": self._ct_logs is not None,
                "container_explorer": self._container_explorer is not None
            }
        }


class APICache:
    """
    Simple sqlite-based API cache with TTL.

    Used for rate-limited APIs like Shodan and Censys.
    """

    def __init__(self, cache_dir: str | None = None, ttl_seconds: int = 3600):
        """
        Initialize API cache.

        Args:
            cache_dir: Directory for cache DB (default: temp)
            ttl_seconds: Cache TTL in seconds (default: 1 hour)
        """
        import sqlite3
        from pathlib import Path

        self.ttl_seconds = ttl_seconds

        if cache_dir:
            cache_path = Path(cache_dir)
            cache_path.mkdir(parents=True, exist_ok=True)
            self._db_path = cache_path / "api_cache.db"
        else:
            import tempfile
            self._db_path = Path(tempfile.gettempdir()) / "hledac_api_cache.db"

        # Initialize DB
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS api_cache (
                key TEXT PRIMARY KEY,
                value TEXT,
                timestamp REAL
            )
        """)
        self._conn.commit()

    def get(self, key: str) -> str | None:
        """
        Get cached value if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value or None if expired/missing
        """
        import time

        cursor = self._conn.execute(
            "SELECT value, timestamp FROM api_cache WHERE key = ?",
            (key,)
        )
        row = cursor.fetchone()

        if row is None:
            return None

        value, timestamp = row
        if time.time() - timestamp > self.ttl_seconds:
            # Expired
            self._conn.execute("DELETE FROM api_cache WHERE key = ?", (key,))
            self._conn.commit()
            return None

        return value

    def set(self, key: str, value: str) -> None:
        """
        Set cached value with current timestamp.

        Args:
            key: Cache key
            value: Value to cache
        """
        import time

        self._conn.execute(
            "INSERT OR REPLACE INTO api_cache (key, value, timestamp) VALUES (?, ?, ?)",
            (key, value, time.time())
        )
        self._conn.commit()

    def clear(self) -> None:
        """Clear all cached entries."""
        self._conn.execute("DELETE FROM api_cache")
        self._conn.commit()

    def close(self) -> None:
        """Close database connection."""
        self._conn.close()


async def search_shodan(
    query: str,
    api_key: str | None = None
) -> list[dict[str, Any]]:
    """
    Search Shodan using free API (no key or community key).

    Args:
        query: Search query (e.g., "apache", "nginx", "product:cisco")
        api_key: Shodan API key (default: SHODAN_API_KEY env var)

    Returns:
        List of dicts with structure:
        [{'ip': str, 'port': int, 'service': str, 'banner': str}]

    Anti-patterns:
      - Rate limited (uses APICache with 1-hour TTL)
      - No API key hardcoded (uses .env)
    """
    import os

    results: list[dict[str, Any]] = []

    # Get API key from env if not provided
    if not api_key:
        api_key = os.environ.get("SHODAN_API_KEY", "")

    # Check cache first
    cache = APICache(ttl_seconds=3600)
    cache_key = f"shodan:{query}:{api_key}"
    cached = cache.get(cache_key)

    if cached:
        try:
            results = json.loads(cached)
            logger.info(f"Shodan cache hit for query: {query}")
            cache.close()
            return results
        except json.JSONDecodeError:
            pass

    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession() as session:
            # Shodan API endpoint (free tier)
            base_url = "https://api.shodan.io/shodan/host/search"

            params = {
                "key": api_key if api_key else "free",
                "query": query,
                "minify": True,
            }

            async with session.get(base_url, params=params, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    matches = data.get("matches", [])
                    for match in matches[:50]:  # Limit results
                        try:
                            result = {
                                "ip": match.get("ip_str", ""),
                                "port": match.get("port", 0),
                                "service": match.get("product", match.get("proto", "unknown")),
                                "banner": match.get("data", "")[:500],  # Truncate banners
                                "org": match.get("org", ""),
                                "asn": match.get("asn", ""),
                                "transport": match.get("transport", ""),
                                "timestamp": match.get("timestamp", ""),
                            }
                            results.append(result)

                        except Exception as e:
                            logger.debug(f"Error parsing Shodan match: {e}")
                            continue

                    # Cache results
                    cache.set(cache_key, json.dumps(results))

                elif resp.status == 429:
                    logger.warning("Shodan rate limited")
                else:
                    logger.debug(f"Shodan API returned status {resp.status}")

    except Exception as e:
        logger.debug(f"Shodan search failed for '{query}': {e}")

    cache.close()
    logger.info(f"search_shodan('{query}'): {len(results)} results")
    return results


async def search_censys(
    query: str,
    api_id: str | None = None,
    api_secret: str | None = None
) -> list[dict[str, Any]]:
    """
    Search Censys using free API (Censys data API).

    Args:
        query: Search query (e.g., "services.tls.certificates.leaf_data.subject.common_name: example.com")
        api_id: Censys API ID (default: CENSYS_API_ID env var)
        api_secret: Censys API Secret (default: CENSYS_API_SECRET env var)

    Returns:
        List of dicts with structure:
        [{'ip': str, 'port': int, 'service': str, 'banner': str}]

    Anti-patterns:
      - Rate limited (uses APICache with 1-hour TTL)
      - No API credentials hardcoded (uses .env)
    """
    import base64
    import os

    results: list[dict[str, Any]] = []

    # Get credentials from env if not provided
    if not api_id:
        api_id = os.environ.get("CENSYS_API_ID", "")
    if not api_secret:
        api_secret = os.environ.get("CENSYS_API_SECRET", "")

    # Check cache first
    cache = APICache(ttl_seconds=3600)
    cache_key = f"censys:{query}"
    cached = cache.get(cache_key)

    if cached:
        try:
            results = json.loads(cached)
            logger.info(f"Censys cache hit for query: {query}")
            cache.close()
            return results
        except json.JSONDecodeError:
            pass

    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession() as session:
            # Censys Search API v2
            base_url = "https://search.censys.io/api/v1/search"

            # Build auth header if credentials provided
            headers = {"Accept": "application/json"}
            if api_id and api_secret:
                auth_str = f"{api_id}:{api_secret}"
                auth_bytes = base64.b64encode(auth_str.encode()).decode()
                headers["Authorization"] = f"Basic {auth_bytes}"

            params = {
                "q": query,
                "max_records": 50,
            }

            async with session.get(base_url, params=params, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    results_list = data.get("results", [])
                    for result in results_list[:50]:  # Limit results
                        try:
                            # Extract IP and services info
                            ip = result.get("ip", "")
                            services = result.get("services", [])

                            for svc in services:
                                result_entry = {
                                    "ip": ip,
                                    "port": svc.get("port", 0),
                                    "service": svc.get("service", "unknown"),
                                    "banner": svc.get("banner", "")[:500],
                                    "transport": svc.get("transport", ""),
                                }
                                results.append(result_entry)

                        except Exception as e:
                            logger.debug(f"Error parsing Censys result: {e}")
                            continue

                    # Cache results
                    cache.set(cache_key, json.dumps(results))

                elif resp.status == 429:
                    logger.warning("Censys rate limited")
                elif resp.status == 401:
                    logger.warning("Censys auth failed")
                else:
                    logger.debug(f"Censys API returned status {resp.status}")

    except Exception as e:
        logger.debug(f"Censys search failed for '{query}': {e}")

    cache.close()
    logger.info(f"search_censys('{query}'): {len(results)} results")
    return results


# Convenience functions
async def quick_hunt(target: str) -> dict[str, list[ExposedService]]:
    """Quick exposed service hunt."""
    async with ExposedServiceHunter() as hunter:
        return await hunter.hunt(target)


async def check_s3_bucket(bucket_name: str) -> ExposedService | None:
    """Check if a specific S3 bucket exists and is exposed."""
    async with S3BucketEnumerator() as enumerator:
        results = await enumerator.enumerate_buckets(bucket_name)
        return results[0] if results else None


async def scan_graphql_endpoint(url: str) -> dict | None:
    """Scan a specific GraphQL endpoint."""
    async with GraphQLIntrospector() as introspector:
        result = await introspector._check_endpoint(url)
        return result.to_dict() if result else None


# Export
__all__ = [
    # Main class
    "ExposedServiceHunter",
    # Component classes
    "S3BucketEnumerator",
    "DatabasePortScanner",
    "GraphQLIntrospector",
    "CertificateTransparency",
    "ContainerAPIExplorer",
    # Data classes
    "ExposedService",
    "S3Bucket",
    "CertificateInfo",
    # Enums
    "ServiceType",
    "ExposureType",
    "RiskLevel",
    # Convenience functions
    "quick_hunt",
    "check_s3_bucket",
    "scan_graphql_endpoint",
    # Phase 15 additions
    "search_shodan",
    "search_censys",
    "APICache",
]
