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

import asyncio
import json
import logging
import re
import weakref
from dataclasses import field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse

import httpx

from compat.msgspec_gc_compat import Struct
from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
from hledac.universal.utils._patterns import scan_parallel
from hledac.universal.utils.asyncx import parallel_ok, safe_wait_for

logger = logging.getLogger(__name__)


class ServiceType(Enum):
    """Types of exposed services."""

    S3_BUCKET = "s3"
    GCS_BUCKET = "gcs"
    AZURE_CONTAINER = "azure_blob"
    MONGODB = "mongodb"
    REDIS = "redis"
    ELASTICSEARCH = "elasticsearch"
    COUCHDB = "couchdb"
    GRAPHQL = "graphql"
    DOCKER = "docker"
    KUBERNETES = "kubernetes"
    CERTIFICATE = "certificate"
    SWAGGER = "swagger"
    DIRECTORY_LISTING = "directory_listing"
    RSYNC = "rsync"


class ExposureType(Enum):
    """Types of exposure."""

    OPEN = "open"
    MISCONFIGURED = "misconfigured"
    AUTH_BYPASS = "auth_bypass"
    PUBLIC = "public"
    LEAKED = "leaked"


class RiskLevel(Enum):
    """Risk levels for exposed services.

    Re-ordered to canonical order (LOW → CRITICAL) to match
    `project_types.RiskLevel`. Values are identical lowercase strings.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExposedService(Struct):
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
            "discovered_at": self.discovered_at.isoformat(),
        }


class S3Bucket(Struct, frozen=True):
    """S3 bucket information."""

    bucket_name: str
    region: str | None
    is_listable: bool
    has_files: bool
    file_count: int | None
    total_size: int | None
    permissions: list[str]


class CertificateInfo(Struct, frozen=True):
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
        "us-east-1",
        "us-east-2",
        "us-west-1",
        "us-west-2",
        "eu-west-1",
        "eu-west-2",
        "eu-west-3",
        "eu-central-1",
        "eu-north-1",
        "ap-southeast-1",
        "ap-southeast-2",
        "ap-northeast-1",
        "ap-northeast-2",
        "ap-south-1",
        "ca-central-1",
        "sa-east-1",
    ]
    __slots__ = ("_owned_session", "session")

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = await httpx.AsyncClient()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def enumerate_buckets(self, target: str, max_concurrent: int = 20) -> list[ExposedService]:
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
        bucket_names = set()
        for pattern in self.BUCKET_PATTERNS:
            bucket_name = pattern.format(target=target_clean)
            bucket_names.add(bucket_name)
            bucket_names.add(bucket_name.replace("-", ""))
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

        tasks = [check_bucket(name) for name in bucket_names]
        results = await parallel_ok(*tasks, label="exposed_service_hunter:241")
        for result in results:
            if result:
                findings.append(result)
        return findings

    async def _check_bucket_exists(self, bucket_name: str) -> ExposedService | None:
        """Check if an S3 bucket exists and is accessible.

        1.1/1.2/1.3 FIX: Uses GET ?list-type=2 (ListObjectsV2) to detect listability.
        HEAD request is insufficient because:
        - MinIO/self-hosted S3 may not return 200 on HEAD but allow listing
        - HEAD doesn't reveal actual read/list permissions accurately
        - Some S3-compatible services (MinIO, SeaweedFS) require explicit list ops

        Also checks MinIO-specific endpoint patterns.
        """
        if not self.session:
            return None
        regions_to_try = [None] + self.S3_REGIONS[:5]
        minio_patterns = [None, "localhost:9000", "minio.local:9000", "s3.local"]
        for region in regions_to_try:
            for endpoint in minio_patterns:
                try:
                    if region:
                        if endpoint:
                            url = f"https://{endpoint}/minio/{bucket_name}"
                        else:
                            url = f"https://s3.{region}.amazonaws.com/{bucket_name}"
                    elif endpoint and endpoint != "localhost:9000":
                        url = f"https://{endpoint}/{bucket_name}"
                    else:
                        url = f"https://{bucket_name}.s3.amazonaws.com"
                    list_url = f"{url}?list-type=2&max-keys=1"
                    async with self.session.get(list_url, follow_redirects=True, timeout=10) as resp:
                        if resp.status == 200:
                            text = resp.text or ""
                            is_listable = "<ListBucketResult" in text or "<ListAllMyBucketsResult" in text
                            has_objects = "<Contents" in text
                            has_prefixes = "<CommonPrefixes" in text
                            if "<ListAllMyBucketsResult" in text:
                                exposure = ExposureType.OPEN.value
                                risk = RiskLevel.CRITICAL.value
                            elif is_listable:
                                exposure = ExposureType.OPEN.value
                                risk = RiskLevel.HIGH.value
                            else:
                                exposure = ExposureType.PUBLIC.value
                                risk = RiskLevel.LOW.value
                            return ExposedService(
                                service_type=ServiceType.S3_BUCKET.value,
                                host=urlparse(url).netloc or f"{bucket_name}.s3.amazonaws.com",
                                port=443,
                                exposure_type=exposure,
                                risk_level=risk,
                                metadata={
                                    "bucket_name": bucket_name,
                                    "region": region,
                                    "listable": is_listable,
                                    "has_objects": has_objects,
                                    "has_prefixes": has_prefixes,
                                    "url": url,
                                    "is_minio": endpoint is not None,
                                },
                            )
                        elif resp.status == 403:
                            return ExposedService(
                                service_type=ServiceType.S3_BUCKET.value,
                                host=urlparse(url).netloc or f"{bucket_name}.s3.amazonaws.com",
                                port=443,
                                exposure_type=ExposureType.PUBLIC.value,
                                risk_level=RiskLevel.LOW.value,
                                metadata={
                                    "bucket_name": bucket_name,
                                    "region": region,
                                    "listable": False,
                                    "exists": True,
                                    "url": url,
                                    "is_minio": endpoint is not None,
                                },
                            )
                        elif resp.status == 404:
                            continue
                except TimeoutError:
                    continue
                except Exception as e:
                    logger.debug(f"Error checking bucket {bucket_name}: {e}")
                    continue
        return None

    async def check_bucket_permissions(self, bucket_name: str) -> dict[str, Any]:
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
                    permissions[perm_name] = {"accessible": resp.status == 200, "status": resp.status}
            except Exception as e:
                permissions[perm_name] = {"accessible": False, "error": str(e)}
        return permissions


class GCSBucketEnumerator:
    """
    Google Cloud Storage bucket enumeration using common naming patterns.

    Uses HTTP HEAD/GET requests to check bucket existence and permissions.
    No GCP credentials required.

    Endpoints:
    - https://storage.googleapis.com/{bucket}
    - https://{bucket}.storage.googleapis.com
    - https://{bucket}.storage.googleapis.com/?acl
    """

    BUCKET_SUFFIXES = (
        "",
        "-prod",
        "-dev",
        "-staging",
        "-backup",
        "-data",
        "-assets",
        "-media",
        "-static",
        "-files",
        "-documents",
        "-private",
        "-public",
        "-logs",
        "-config",
        "-database",
        "-storage",
        "-usercontent",
    )
    __slots__ = ("_owned_session", "session")

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = httpx.AsyncClient(timeout=httpx.Timeout(total=10))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def enumerate_buckets(
        self, target: str, san_names: list[str] | None = None, max_concurrent: int = 20
    ) -> list[ExposedService]:
        """
        Enumerate GCS buckets using naming patterns and optional SAN-derived names.

        Args:
            target: Target domain or company name
            san_names: Optional list of SAN names from CT logs for bucket name derivation
            max_concurrent: Maximum concurrent requests

        Returns:
            List of exposed GCS buckets
        """
        findings = []
        target_clean = target.replace(".", "-").replace("_", "-").lower()
        bucket_names: set[str] = set()
        for suffix in self.BUCKET_SUFFIXES:
            bucket_name = f"{target_clean}{suffix}"
            bucket_names.add(bucket_name)
            bucket_names.add(bucket_name.replace("-", ""))
            bucket_names.add(bucket_name.replace("-", "_"))
        if san_names:
            for san in san_names[:50]:
                san_clean = san.lower().strip().lstrip("*.")
                if san_clean and "." in san_clean:
                    parts = san_clean.split(".")
                    if len(parts) >= 2:
                        bucket_names.add(parts[0])
                        bucket_names.add(parts[0].replace(".", "-"))
        logger.info(f"Checking {len(bucket_names)} potential GCS buckets for {target}")
        semaphore = asyncio.Semaphore(max_concurrent)

        async def check_bucket(bucket_name: str) -> ExposedService | None:
            async with semaphore:
                try:
                    result = await self._check_bucket_exists(bucket_name)
                    if result:
                        logger.info(f"Found GCS bucket: {bucket_name}")
                        return result
                except Exception as e:
                    logger.debug(f"Error checking GCS bucket {bucket_name}: {e}")
                return None

        tasks = [check_bucket(name) for name in bucket_names]
        results = await parallel_ok(*tasks, label="exposed_service_hunter:gcs_enum")
        for result in results:
            if result:
                findings.append(result)
        return findings

    async def _check_bucket_exists(self, bucket_name: str) -> ExposedService | None:
        """Check if a GCS bucket exists and is accessible."""
        if not self.session:
            return None
        urls_to_try = [f"https://storage.googleapis.com/{bucket_name}", f"https://{bucket_name}.storage.googleapis.com"]
        for url in urls_to_try:
            try:
                async with self.session.head(url, follow_redirects=True, timeout=8) as resp:
                    status = resp.status_code
                    if status == 200:
                        return ExposedService(
                            service_type=ServiceType.GCS_BUCKET.value,
                            host=f"{bucket_name}.storage.googleapis.com",
                            port=443,
                            exposure_type=ExposureType.OPEN.value,
                            risk_level=RiskLevel.CRITICAL.value,
                            metadata={"bucket_name": bucket_name, "listable": True, "url": url, "provider": "gcs"},
                        )
                    elif status == 403:
                        return ExposedService(
                            service_type=ServiceType.GCS_BUCKET.value,
                            host=f"{bucket_name}.storage.googleapis.com",
                            port=443,
                            exposure_type=ExposureType.PUBLIC.value,
                            risk_level=RiskLevel.LOW.value,
                            metadata={
                                "bucket_name": bucket_name,
                                "listable": False,
                                "exists": True,
                                "url": url,
                                "provider": "gcs",
                            },
                        )
                    elif status == 404:
                        continue
            except TimeoutError:
                continue
            except Exception as e:
                logger.debug(f"Error checking GCS bucket {bucket_name}: {e}")
                continue
        return None

    async def check_bucket_permissions(self, bucket_name: str) -> dict[str, Any]:
        """Check specific permissions on a GCS bucket."""
        if not self.session:
            return {}
        permissions = {}
        base_url = f"https://storage.googleapis.com/{bucket_name}"
        checks = [("list", f"{base_url}/?prefix=&max-keys=1"), ("acl", f"{base_url}/?acl")]
        for perm_name, url in checks:
            try:
                async with self.session.get(url, timeout=5) as resp:
                    permissions[perm_name] = {"accessible": resp.status == 200, "status": resp.status}
            except Exception as e:
                permissions[perm_name] = {"accessible": False, "error": str(e)}
        return permissions


class AzureBlobEnumerator:
    """
    Azure Blob Storage container enumeration using common naming patterns.

    Uses HTTP HEAD/GET requests to check container existence and permissions.
    No Azure credentials required.

    Endpoints:
    - https://{account}.blob.core.windows.net/{container}
    - https://{account}.blob.core.windows.net/{container}?restype=container
    - https://{account}.blob.core.windows.net/{container}?comp=list
    """

    ACCOUNT_SUFFIXES = (
        "",
        "prod",
        "dev",
        "staging",
        "backup",
        "data",
        "assets",
        "media",
        "static",
        "files",
        "logs",
        "config",
    )
    CONTAINER_SUFFIXES = (
        "",
        "-prod",
        "-dev",
        "-staging",
        "-backup",
        "-data",
        "-assets",
        "-media",
        "-static",
        "-files",
        "-documents",
        "-private",
        "-public",
        "-logs",
        "-config",
        "-database",
        "-storage",
    )
    __slots__ = ("_owned_session", "session")

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = httpx.AsyncClient(timeout=httpx.Timeout(total=10))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def enumerate_containers(
        self, target: str, san_names: list[str] | None = None, max_concurrent: int = 20
    ) -> list[ExposedService]:
        """
        Enumerate Azure Blob containers using naming patterns.

        Args:
            target: Target domain or company name
            san_names: Optional list of SAN names from CT logs for name derivation
            max_concurrent: Maximum concurrent requests

        Returns:
            List of exposed Azure Blob containers
        """
        target_clean = target.replace(".", "").replace("-", "").replace("_", "").lower()
        account_names: set[str] = set()
        for suffix in self.ACCOUNT_SUFFIXES:
            account_name = f"{target_clean}{suffix}"[:24]
            if len(account_name) >= 3:
                account_names.add(account_name)
        container_names: set[str] = set()
        for suffix in self.CONTAINER_SUFFIXES:
            container_name = f"{target_clean}{suffix}"[:63]
            container_names.add(container_name)
            container_names.add(container_name.replace("-", ""))
        if san_names:
            for san in san_names[:30]:
                san_clean = san.lower().strip().lstrip("*.")
                if san_clean and "." in san_clean:
                    parts = san_clean.split(".")
                    if len(parts) >= 2:
                        account_names.add(parts[0][:24])
                        container_names.add(parts[0][:63])
        logger.info(f"Checking {len(account_names) * len(container_names)} potential Azure containers for {target}")
        limited_accounts = list(account_names)[:10]
        limited_containers = list(container_names)[:20]
        return await scan_parallel(
            check_args=[(a, c) for a in limited_accounts for c in limited_containers],
            checker=lambda a, c: self._check_container_exists(a, c),
            label="exposed_service_hunter:azure",
            logger=logger,
            log_success="Found Azure container: {0}/{1}",
            semaphore=asyncio.Semaphore(max_concurrent),
        )

    async def _check_container_exists(self, account_name: str, container_name: str) -> ExposedService | None:
        """Check if an Azure Blob container exists and is accessible."""
        if not self.session:
            return None
        host = f"{account_name}.blob.core.windows.net"
        url = f"https://{host}/{container_name}"
        try:
            async with self.session.get(f"{url}?restype=container", follow_redirects=True, timeout=8) as resp:
                status = resp.status_code
                if status == 200:
                    return ExposedService(
                        service_type=ServiceType.AZURE_CONTAINER.value,
                        host=host,
                        port=443,
                        exposure_type=ExposureType.OPEN.value,
                        risk_level=RiskLevel.CRITICAL.value,
                        metadata={
                            "account_name": account_name,
                            "container_name": container_name,
                            "listable": True,
                            "url": url,
                            "provider": "azure",
                        },
                    )
                elif status == 403:
                    return ExposedService(
                        service_type=ServiceType.AZURE_CONTAINER.value,
                        host=host,
                        port=443,
                        exposure_type=ExposureType.PUBLIC.value,
                        risk_level=RiskLevel.LOW.value,
                        metadata={
                            "account_name": account_name,
                            "container_name": container_name,
                            "listable": False,
                            "exists": True,
                            "url": url,
                            "provider": "azure",
                        },
                    )
        except TimeoutError:
            pass
        except Exception as e:
            logger.debug(f"Error checking Azure container {account_name}/{container_name}: {e}")
        return None

    async def check_container_permissions(self, account_name: str, container_name: str) -> dict[str, Any]:
        """Check specific permissions on an Azure Blob container."""
        if not self.session:
            return {}
        host = f"{account_name}.blob.core.windows.net"
        base_url = f"https://{host}/{container_name}"
        permissions = {}
        checks = [
            ("list", f"{base_url}?restype=container&comp=list"),
            ("acl", f"{base_url}?restype=container&comp=acl"),
        ]
        for perm_name, url in checks:
            try:
                async with self.session.get(url, timeout=5) as resp:
                    permissions[perm_name] = {"accessible": resp.status == 200, "status": resp.status}
            except Exception as e:
                permissions[perm_name] = {"accessible": False, "error": str(e)}
        return permissions


class RsyncScanner:
    """
    1.1/1.2/1.3 FIX: rsync/873 module enumeration.

    Rsync servers expose named modules via the rsync protocol on port 873.
    Each module maps to a filesystem path on the server. Modules can be
    publicly accessible without authentication.

    Enumeration approach:
    1. Connect to rsync daemon (no auth)
    2. Send list request to enumerate modules
    3. Attempt anonymous access to discovered modules

    Supports context manager protocol for proper connection cleanup.
    """

    RSYNC_PORT = 873
    RSYNC_TIMEOUT = 5.0
    _DEFAULT_MODULE_TIMEOUT = 3.0

    async def __aenter__(self) -> RsyncScanner:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit - no cleanup needed for raw sockets."""

    async def check_rsync(self, host: str) -> list[ExposedService]:
        """Check if rsync is exposed and enumerate modules."""
        findings = []
        try:
            modules = await self._enumerate_modules(host)
            if modules:
                for module in modules:
                    is_readable = await self._check_module_readable(host, module)
                    exposure = ExposureType.OPEN.value if is_readable else ExposureType.PUBLIC.value
                    risk = RiskLevel.HIGH.value if is_readable else RiskLevel.MEDIUM.value
                    findings.append(
                        ExposedService(
                            service_type=ServiceType.RSYNC.value,
                            host=host,
                            port=self.RSYNC_PORT,
                            exposure_type=exposure,
                            risk_level=risk,
                            metadata={"module": module, "is_readable": is_readable, "server_type": "rsync"},
                        )
                    )
                    if is_readable:
                        logger.info(f'[RSYNC] Module "{module}" readable on {host}')
        except Exception as e:
            logger.debug(f"Error checking rsync on {host}: {e}")
        return findings

    async def _enumerate_modules(self, host: str) -> list[str]:
        """Enumerate rsync modules by connecting to daemon."""
        try:
            reader, writer = await safe_wait_for(
                asyncio.open_connection(host, self.RSYNC_PORT), timeout=self.RSYNC_TIMEOUT
            )
            writer.write(b"@RSYNCD:30.0\n")
            await writer.drain()
            modules = []
            async with asyncio.timeout(self.RSYNC_TIMEOUT):
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    line_str = line.decode("utf-8", errors="ignore").strip()
                    if line_str == "@RSYNCD:EXIT":
                        break
                    if line_str and (not line_str.startswith("@")):
                        parts = line_str.split("\t")
                        modules.append(parts[0])
            writer.close()
            await writer.wait_closed()
            return modules
        except Exception:
            return []

    async def _check_module_readable(self, host: str, module: str) -> bool:
        """Check if a module is readable without authentication.

        Enumerates files in the module to verify actual read access.
        A module may not require auth but still deny file listing.
        """
        try:
            reader, writer = await safe_wait_for(
                asyncio.open_connection(host, self.RSYNC_PORT), timeout=self.RSYNC_TIMEOUT
            )
            writer.write(b"@RSYNCD:30.0\n")
            await writer.drain()
            async with asyncio.timeout(self._DEFAULT_MODULE_TIMEOUT):
                greeting = await reader.readline()
                greeting_str = greeting.decode("utf-8", errors="ignore").strip()
                if "@RSYNCD:ERR" in greeting_str or "@RSYNCD:CHKL" in greeting_str:
                    writer.close()
                    await writer.wait_closed()
                    return False
            writer.write(f"{module}\n".encode())
            await writer.drain()
            responses: list[bytes] = []
            async with asyncio.timeout(self._DEFAULT_MODULE_TIMEOUT):
                while True:
                    chunk = await reader.read(512)
                    if not chunk:
                        break
                    responses.append(chunk)
                    if b"@RSYNCD:EXIT" in chunk:
                        break
            writer.close()
            await writer.wait_closed()
            full_response = b"".join(responses).decode("utf-8", errors="ignore")
            if "@RSYNCD:ERR" in full_response or "@RSYNCD:CHKL" in full_response:
                return False
            has_file_entries = any(c in full_response for c in ("\t", "d", "f", "-", "l"))
            is_readable = "@RSYNCD:OK" in full_response or has_file_entries or len(full_response) > 50
            return is_readable
        except Exception:
            return False


class DatabasePortScanner:
    """
    Scanner for exposed database ports.

    Checks common database ports for open access.
    Uses lightweight TCP connection checks.

    HEIST-03: After detecting an unauthenticated database, triggers
    native wire-protocol extraction via Rust dumpers (MongoDB, Redis,
    Elasticsearch). Results are stored in ExposedService.metadata['extraction'].
    Extraction is fire-and-forget — failures are logged but don't block scanning.
    """

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
    _EXTRACTABLE_PORTS: dict[int, str] = {
        27017: "mongodb",
        27018: "mongodb",
        27027019: "mongodb",
        6379: "redis",
        6380: "redis",
        9200: "elasticsearch",
    }
    __slots__ = ("timeout",)

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout

    async def scan_hosts(
        self, hosts: list[str], ports: list[int] | None = None, max_concurrent: int = 50
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
        ports_to_check = ports or list(self.DATABASE_PORTS.keys())
        logger.info(f"Scanning {len(hosts)} hosts on {len(ports_to_check)} ports")
        return await scan_parallel(
            check_args=[(h, p) for h in hosts for p in ports_to_check],
            checker=lambda h, p: self._check_port(h, p),
            label="exposed_service_hunter:db_scan",
            logger=logger,
            log_success="Found exposed database: {host}:{port}",
        )

    async def _check_port(self, host: str, port: int) -> ExposedService | None:
        """Check if a specific port is open and identify service."""
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_connection(host, port)
            banner = ""
            try:
                writer.write(b"\r\n")
                await writer.drain()
                async with asyncio.timeout(2):
                    banner = await reader.read(1024)
                banner = banner.decode("utf-8", errors="ignore").strip()
            except Exception:
                pass
            writer.close()
            await writer.wait_closed()
            service_info = self.DATABASE_PORTS.get(port, ("unknown", "Unknown"))
            service_type, service_name = service_info
            risk_level = RiskLevel.CRITICAL.value if port in [27017, 6379, 9200, 5984] else RiskLevel.HIGH.value
            result = ExposedService(
                service_type=service_type.value if isinstance(service_type, ServiceType) else service_type,
                host=host,
                port=port,
                exposure_type=ExposureType.OPEN.value,
                risk_level=risk_level,
                metadata={"service_name": service_name, "banner": banner[:200] if banner else None, "protocol": "tcp"},
            )
            if port in self._EXTRACTABLE_PORTS:
                try:
                    extraction_data = await self._extract_database_data(host, port)
                    if extraction_data:
                        result.metadata["extraction_data"] = extraction_data
                        logger.debug(f"[HEIST-03] extraction result attached for {host}:{port}")
                except Exception as e:
                    logger.debug(f"[HEIST-03] extraction await failed for {host}:{port}: {e}")
            return result
        except TimeoutError:
            return None
        except ConnectionRefusedError:
            return None
        except Exception as e:
            logger.debug(f"Error checking {host}:{port}: {e}")
            return None

    async def _extract_database_data(self, host: str, port: int) -> dict[str, Any] | None:
        """
        HEIST-03/HEIST-08: Extract data from an unauthenticated database.

        Delegates to network/native_extraction.extract_from_exposed() which
        uses Rust wire-protocol dumpers for MongoDB/Redis and pure Python
        HTTP for Elasticsearch.

        Fire-and-forget — logged on success, warning on failure. Never raises.
        """
        db_type = self._EXTRACTABLE_PORTS.get(port)
        if not db_type:
            return None
        try:
            from hledac.universal.network.native_extraction import extract_from_exposed

            result = await extract_from_exposed(host, port, db_type)
            if result is None:
                return None
            logger.info(
                f"[HEIST-03] {db_type} extraction {host}:{port} — success={result.success}, databases={result.databases}, keys={result.key_count}, indices={result.indices}"
            )
            return {
                "db_type": db_type,
                "success": result.success,
                "error": result.error,
                "databases": result.databases,
                "collections": dict(result.collections) if result.collections else None,
                "sample_documents": result.sample_documents,
                "keys": result.keys,
                "key_count": result.key_count,
                "indices": result.indices,
                "es_documents": result.es_documents,
                "auth_required": result.auth_required,
                "banner": result.banner,
            }
        except ImportError:
            logger.debug(
                f"[HEIST-03] native_extraction not available for {host}:{port} ({db_type}) — extraction skipped"
            )
        except Exception as e:
            logger.warning(f"[HEIST-03] Extraction failed for {host}:{port} ({db_type}): {e}")
        return None

    async def test_mongodb_auth(self, host: str, port: int = 27017) -> dict[str, Any]:
        """Test MongoDB for authentication requirements. HEIST-03: triggers extraction on no-auth."""
        result: dict[str, Any] = {"auth_required": None, "version": None}
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_connection(host, port)
            is_master_cmd = b"=\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\xd4\x07\x00\x00"
            is_master_cmd += b"\x00\x00\x00\x00admin.$cmd\x00\x00"
            is_master_cmd += b"\x00\x00\x00\xff\xff\xff\xff\x13\x00\x00\x00\x10isMa"
            is_master_cmd += b"ster\x00\x01\x00\x00\x00\x00"
            writer.write(is_master_cmd)
            await writer.drain()
            async with asyncio.timeout(5):
                response = await reader.read(1024)
            writer.close()
            await writer.wait_closed()
            if b"unauthorized" in response.lower() or b"auth" in response.lower():
                result["auth_required"] = True
            else:
                result["auth_required"] = False
            version_match = re.search(b'"version"\\s*:\\s*"([^"]+)"', response)
            if version_match:
                result["version"] = version_match.group(1).decode("utf-8", errors="ignore")
            if result["auth_required"] is False:
                logger.info(f"[HEIST-03] MongoDB no-auth detected at {host}:{port} — extracting...")
                extraction = await self._extract_database_data(host, port)
                if extraction:
                    result["extraction"] = extraction
        except Exception as e:
            result["error"] = str(e)
        return result

    async def test_redis_auth(self, host: str, port: int = 6379) -> dict[str, Any]:
        """Test Redis for authentication requirements. HEIST-03: triggers extraction on no-auth."""
        result: dict[str, Any] = {"auth_required": None, "version": None}
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_connection(host, port)
            writer.write(b"INFO\r\n")
            await writer.drain()
            async with asyncio.timeout(5):
                response = await reader.read(2048)
            writer.close()
            await writer.wait_closed()
            response_str = response.decode("utf-8", errors="ignore")
            if "NOAUTH" in response_str or "authentication" in response_str.lower():
                result["auth_required"] = True
            elif "redis_version" in response_str:
                result["auth_required"] = False
                version_match = re.search("redis_version:(\\S+)", response_str)
                if version_match:
                    result["version"] = version_match.group(1)
            if result["auth_required"] is False:
                logger.info(f"[HEIST-03] Redis no-auth detected at {host}:{port} — extracting...")
                extraction = await self._extract_database_data(host, port)
                if extraction:
                    result["extraction"] = extraction
        except Exception as e:
            result["error"] = str(e)
        return result

    async def test_elasticsearch_auth(self, host: str, port: int = 9200) -> dict[str, Any]:
        """Test Elasticsearch for authentication requirements."""
        result: dict[str, Any] = {"auth_required": None, "version": None}
        try:
            import httpx

            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout)) as client:
                resp = await client.get(f"http://{host}:{port}/")
            if resp.status_code == 200:
                result["auth_required"] = False
                try:
                    body = resp.json()
                    result["version"] = body.get("version", {}).get("number")
                    result["cluster_name"] = body.get("cluster_name")
                except Exception:
                    pass
            elif resp.status_code == 401:
                result["auth_required"] = True
        except Exception as e:
            result["error"] = str(e)
        return result

    async def _try_extract_mongodb(self, host: str, port: int = 27017, limit: int = 500) -> list[dict[str, Any]]:
        """HEIST-03: Extract data from unauthenticated MongoDB via Rust native_db.

        Called after test_mongodb_auth() returns auth_required=False.
        Falls back gracefully if Rust native_db feature not compiled.
        """
        from hledac.universal._core.rust_backend import rust

        MongoDumper = rust.raw.MongoDumper
        if MongoDumper is None:
            logger.debug(f"[HEIST-03] Rust native_db not available — skipping MongoDB extraction for {host}:{port}")
            return []
        try:
            dumper = MongoDumper()
            entries = await asyncio.to_thread(dumper.dump_all, host, port, limit, self.timeout)
            results: list[dict[str, Any]] = []
            for entry in entries:
                entry_dict = {
                    "database": entry.database,
                    "collection": entry.collection,
                    "document_count": entry.document_count,
                    "documents_json": entry.documents_json,
                    "error": entry.error,
                }
                results.append(entry_dict)
                if entry.error:
                    logger.warning(
                        f"[HEIST-03] MongoDB extraction error {host}:{port}/{entry.database}/{entry.collection}: {entry.error}"
                    )
                elif entry.collection and entry.document_count:
                    logger.info(
                        f"[HEIST-03] MongoDB extracted {entry.document_count} docs from {host}:{port}/{entry.database}/{entry.collection}"
                    )
            return results
        except Exception as e:
            logger.warning(f"[HEIST-03] MongoDB extraction failed {host}:{port}: {e}")
            return []

    async def _try_extract_redis(self, host: str, port: int = 6379, max_keys: int = 500) -> list[dict[str, Any]]:
        """HEIST-03: Extract data from unauthenticated Redis via Rust native_db.

        Called after test_redis_auth() returns auth_required=False.
        Falls back gracefully if Rust native_db feature not compiled.
        """
        from hledac.universal._core.rust_backend import rust

        RedisDumper = rust.raw.RedisDumper
        if RedisDumper is None:
            logger.debug(f"[HEIST-03] Rust native_db not available — skipping Redis extraction for {host}:{port}")
            return []
        try:
            dumper = RedisDumper()
            entries = await asyncio.to_thread(dumper.dump_all, host, port, max_keys, self.timeout)
            results: list[dict[str, Any]] = []
            for entry in entries:
                entry_dict = {
                    "key": entry.key,
                    "key_type": entry.key_type,
                    "value": entry.value,
                    "ttl": entry.ttl,
                    "error": entry.error,
                }
                results.append(entry_dict)
                if entry.error:
                    logger.warning(f"[HEIST-03] Redis extraction error {host}:{port}/{entry.key}: {entry.error}")
            if results and (not results[0].get("error")):
                logger.info(f"[HEIST-03] Redis extracted {len(results)} keys from {host}:{port}")
            return results
        except Exception as e:
            logger.warning(f"[HEIST-03] Redis extraction failed {host}:{port}: {e}")
            return []

    async def _try_extract_elasticsearch(self, host: str, port: int = 9200, limit: int = 100) -> list[dict[str, Any]]:
        """HEIST-03: Extract data from unauthenticated Elasticsearch via Rust
        native_db.

        Called after test_elasticsearch_auth() returns auth_required=False.
        Falls back to httpx-based extraction if Rust native_db not compiled.
        """
        from hledac.universal._core.rust_backend import rust

        ElasticsearchDumper = rust.raw.ElasticsearchDumper
        if ElasticsearchDumper is not None:
            try:
                dumper = ElasticsearchDumper()
                entries = await asyncio.to_thread(dumper.dump_all, host, port, limit, self.timeout)
                results: list[dict[str, Any]] = []
                for entry in entries:
                    entry_dict = {
                        "index": entry.index,
                        "document_count": entry.document_count,
                        "documents_json": entry.documents_json,
                        "error": entry.error,
                    }
                    results.append(entry_dict)
                    if entry.error:
                        logger.warning(f"[HEIST-03] ES extraction error {host}:{port}/{entry.index}: {entry.error}")
                    elif entry.document_count:
                        logger.info(
                            f"[HEIST-03] ES extracted {entry.document_count} docs from {host}:{port}/{entry.index}"
                        )
                return results
            except Exception as e:
                logger.debug(f"[HEIST-03] Rust ES extraction failed, httpx fallback: {e}")
        else:
            logger.debug(
                f"[HEIST-03] Rust native_db not available — falling back to httpx ES extraction for {host}:{port}"
            )
        try:
            import json

            import httpx

            results: list[dict[str, Any]] = []
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout)) as client:
                cat_resp = await client.get(f"http://{host}:{port}/_cat/indices?format=json")
                if cat_resp.status_code != 200:
                    return []
                indices_data = cat_resp.json()
                for idx_entry in indices_data:
                    index_name = idx_entry.get("index", "")
                    if not index_name or index_name.startswith("."):
                        continue
                    try:
                        search_resp = await client.post(
                            f"http://{host}:{port}/{index_name}/_search",
                            json={"query": {"match_all": {}}, "size": limit, "_source": True},
                        )
                        if search_resp.status_code == 200:
                            body = search_resp.json()
                            hits = body.get("hits", {}).get("hits", [])
                            docs = [json.dumps(h.get("_source", {})) for h in hits]
                            results.append(
                                {
                                    "index": index_name,
                                    "document_count": len(docs),
                                    "documents_json": docs,
                                    "error": None,
                                }
                            )
                            logger.info(
                                f"[HEIST-03] ES (httpx) extracted {len(docs)} docs from {host}:{port}/{index_name}"
                            )
                    except Exception as e:
                        results.append(
                            {"index": index_name, "document_count": None, "documents_json": None, "error": str(e)}
                        )
            return results
        except Exception as e:
            logger.warning(f"[HEIST-03] ES extraction failed {host}:{port}: {e}")
            return []

    async def scan_and_extract(
        self,
        hosts: list[str],
        extract_data: bool = True,
        mongo_limit: int = 500,
        redis_max_keys: int = 500,
        es_limit: int = 100,
    ) -> list[ExposedService]:
        """HEIST-03: Scan for exposed databases AND extract data.

        Extends scan_hosts() with native wire-protocol data extraction
        for MongoDB, Redis, and Elasticsearch instances found without auth.

        Args:
            hosts: Hostnames or IPs to scan.
            extract_data: If True, attempt data extraction on open instances.
            mongo_limit: Max documents per MongoDB collection.
            redis_max_keys: Max keys to extract from Redis.
            es_limit: Max documents per Elasticsearch index.

        Returns:
            List of ExposedService objects with extraction metadata populated.
        """
        findings = await self.scan_hosts(hosts)
        if not extract_data:
            return findings
        for finding in findings:
            service_type = finding.service_type
            host = finding.host
            port = finding.port
            try:
                if service_type in ("mongodb", ServiceType.MONGODB.value):
                    auth = await self.test_mongodb_auth(host, port)
                    finding.metadata["mongo_auth"] = auth
                    if auth.get("auth_required") is False:
                        logger.info(f"[HEIST-03] Open MongoDB at {host}:{port} — extracting data...")
                        extracted = await self._try_extract_mongodb(host, port, mongo_limit)
                        finding.metadata["extracted_data"] = extracted
                        finding.metadata["extraction_method"] = "rust_native_db"
                elif service_type in ("redis", ServiceType.REDIS.value):
                    auth = await self.test_redis_auth(host, port)
                    finding.metadata["redis_auth"] = auth
                    if auth.get("auth_required") is False:
                        logger.info(f"[HEIST-03] Open Redis at {host}:{port} — extracting data...")
                        extracted = await self._try_extract_redis(host, port, redis_max_keys)
                        finding.metadata["extracted_data"] = extracted
                        finding.metadata["extraction_method"] = "rust_native_db"
                elif service_type in ("elasticsearch", ServiceType.ELASTICSEARCH.value):
                    auth = await self.test_elasticsearch_auth(host, port)
                    finding.metadata["es_auth"] = auth
                    if auth.get("auth_required") is False:
                        logger.info(f"[HEIST-03] Open Elasticsearch at {host}:{port} — extracting data...")
                        extracted = await self._try_extract_elasticsearch(host, port, es_limit)
                        finding.metadata["extracted_data"] = extracted
                        finding.metadata["extraction_method"] = (
                            "rust_native_db" if "rust_native_db" not in str(extracted) else "httpx_fallback"
                        )
            except Exception as e:
                logger.warning(f"[HEIST-03] Extraction orchestration failed {host}:{port}: {e}")
                finding.metadata["extraction_error"] = str(e)
        return findings


class GraphQLIntrospector:
    """
    GraphQL introspection discovery.

    Discovers GraphQL endpoints and extracts schema information.
    """

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
    INTROSPECTION_QUERY = "\n    query IntrospectionQuery {\n      __schema {\n        queryType { name }\n        mutationType { name }\n        subscriptionType { name }\n        types {\n          name\n          kind\n          description\n          fields {\n            name\n            description\n            type {\n              name\n              kind\n            }\n          }\n        }\n      }\n    }\n    "
    __slots__ = ("_owned_session", "session")

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = httpx.AsyncClient(timeout=httpx.Timeout(total=10))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def discover_endpoints(self, base_url: str, max_concurrent: int = 10) -> list[ExposedService]:
        """
        Discover GraphQL endpoints on a target.

        Args:
            base_url: Base URL to scan
            max_concurrent: Maximum concurrent requests

        Returns:
            List of discovered GraphQL endpoints
        """
        base_url = base_url.rstrip("/")

        async def check_with_base(endpoint: str) -> ExposedService | None:
            return await self._check_endpoint(f"{base_url}{endpoint}")

        return await scan_parallel(
            check_args=[(ep,) for ep in self.COMMON_ENDPOINTS],
            checker=check_with_base,
            label="exposed_service_hunter:graphql",
            logger=logger,
            log_success="Found GraphQL endpoint: {0}",
        )

    async def _check_endpoint(self, url: str) -> ExposedService | None:
        """Check if a URL is a GraphQL endpoint with introspection enabled."""
        if not self.session:
            return None
        try:
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            payload = {"query": self.INTROSPECTION_QUERY, "operationName": "IntrospectionQuery"}
            async with self.session.post(url, headers=headers, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("data", {}).get("__schema"):
                        schema = data["data"]["__schema"]
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
                                "has_subscription": schema.get("subscriptionType") is not None,
                            },
                        )
                elif resp.status in [400, 401, 403]:
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
                                "note": "GraphQL endpoint detected but introspection disabled",
                            },
                        )
        except httpx.HTTPError:
            pass
        except Exception as e:
            logger.debug(f"Error checking GraphQL endpoint {url}: {e}")
        return None

    async def introspect_endpoint(self, url: str) -> dict[str, Any] | None:
        """Perform full introspection on a GraphQL endpoint."""
        if not self.session:
            return None
        try:
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            payload = {"query": self.INTROSPECTION_QUERY, "operationName": "IntrospectionQuery"}
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
    __slots__ = ("_owned_session", "session")

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = httpx.AsyncClient(timeout=httpx.Timeout(total=30))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def query_domain(self, domain: str, include_subdomains: bool = True) -> list[str]:
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
            params = {"q": domain, "output": "json"}
            if include_subdomains:
                params["q"] = f"%.{domain}"
            async with self.session.get(self.CRTSH_API, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for entry in data:
                        name_value = entry.get("name_value", "")
                        common_name = entry.get("common_name", "")
                        for name in [name_value, common_name]:
                            if name:
                                for subdomain in name.split("\n"):
                                    subdomain = subdomain.strip()
                                    if subdomain and domain in subdomain:
                                        subdomains.add(subdomain)
                    logger.info(f"Found {len(subdomains)} subdomains via CT logs for {domain}")
        except Exception as e:
            logger.error(f"CT log query failed for {domain}: {e}")
        return sorted(subdomains)

    async def get_certificate_details(self, domain: str) -> list[CertificateInfo]:
        """Get detailed certificate information from CT logs."""
        certificates = []
        if not self.session:
            return certificates
        try:
            params = {"q": domain, "output": "json"}
            async with self.session.get(self.CRTSH_API, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for entry in data:
                        try:
                            cert = CertificateInfo(
                                domain=entry.get("common_name", domain),
                                issuer=entry.get("issuer_name", "Unknown"),
                                not_before=datetime.strptime(entry.get("not_before", "1970-01-01"), "%Y-%m-%d"),
                                not_after=datetime.strptime(entry.get("not_after", "1970-01-01"), "%Y-%m-%d"),
                                san_domains=entry.get("name_value", "").split("\n"),
                                fingerprint=entry.get("cert_sha256", ""),
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
    __slots__ = ("_owned_session", "session")

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = httpx.AsyncClient(timeout=httpx.Timeout(total=10))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def scan_docker_apis(self, hosts: list[str], max_concurrent: int = 20) -> list[ExposedService]:
        """Scan for exposed Docker APIs."""
        return await scan_parallel(
            check_args=[(h, p) for h in hosts for p in self.DOCKER_PORTS],
            checker=lambda h, p: self._check_docker_api(h, p),
            label="exposed_service_hunter:docker",
            logger=logger,
            log_success="Found exposed Docker API: {host}:{port}",
        )

    async def _check_docker_api(self, host: str, port: int) -> ExposedService | None:
        """Check if a Docker API is exposed."""
        if not self.session:
            return None
        protocol = "https" if port == 2376 else "http"
        try:
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
                                    "endpoint": url,
                                },
                            )
                    except Exception:
                        return ExposedService(
                            service_type=ServiceType.DOCKER.value,
                            host=host,
                            port=port,
                            exposure_type=ExposureType.OPEN.value,
                            risk_level=RiskLevel.CRITICAL.value,
                            metadata={"endpoint": url, "note": "Docker API responded but not JSON"},
                        )
        except Exception as e:
            logger.debug(f"Docker API check failed for {host}:{port}: {e}")
        return None

    async def scan_kubernetes_apis(self, hosts: list[str], max_concurrent: int = 20) -> list[ExposedService]:
        """Scan for exposed Kubernetes APIs."""
        return await scan_parallel(
            check_args=[(h, p) for h in hosts for p in self.KUBERNETES_PORTS],
            checker=lambda h, p: self._check_kubernetes_api(h, p),
            label="exposed_service_hunter:k8s",
            logger=logger,
            log_success="Found exposed Kubernetes API: {host}:{port}",
        )

    async def _check_kubernetes_api(self, host: str, port: int) -> ExposedService | None:
        """Check if a Kubernetes API is exposed."""
        if not self.session:
            return None
        protocol = "https" if port in [6443, 8443] else "http"
        try:
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
                                    "endpoint": url,
                                },
                            )
                    except Exception:
                        pass
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
                                "note": "Kubernetes API requires authentication",
                            },
                        )
        except Exception as e:
            logger.debug(f"K8s API check failed for {host}:{port}: {e}")
        return None


class SwaggerEnumerator:
    """
    Swagger/OpenAPI specification discovery and parsing.

    Probes 17 common paths for Swagger/OpenAPI JSON/YAML specs and extracts
    all documented endpoint URLs, parameters, and authentication schemes.

    Uses HEAD-first strategy to minimize bandwidth: HEAD to check existence,
    GET only when HEAD returns 200/403 (spec present but access-restricted).

    M1 Optimized: Minimal YAML parsing - extracts only endpoint paths and
    auth schemes, not full spec tree. Uses iterparse-style streaming for JSON.
    """

    SWAGGER_PATHS: tuple[str, ...] = (
        "/swagger.json",
        "/openapi.json",
        "/api-docs",
        "/api-docs.json",
        "/swagger/v1/swagger.json",
        "/api/v1/docs",
        "/api/v2/docs",
        "/openapi.yaml",
        "/swagger.yaml",
        "/v2/api-docs",
        "/v3/api-docs",
        "/api/swagger.json",
        "/api/openapi.json",
        "/docs/swagger.json",
        "/api/v1/swagger.json",
        "/api/v2/openapi.json",
        "/api/schema.json",
    )
    __slots__ = ("_owned_session", "session")

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        self.session = session
        self._owned_session = session is None

    async def __aenter__(self):
        if self._owned_session:
            self.session = httpx.AsyncClient(timeout=httpx.Timeout(total=10))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def discover_endpoints(self, base_url: str, max_concurrent: int = 10) -> list[ExposedService]:
        """
        Discover Swagger/OpenAPI specification files on a target.

        Strategy: HEAD-first to confirm existence (saves bandwidth), then
        GET to parse content. Falls back to GET if HEAD returns 405 (Not Allowed).

        Args:
            base_url: Base URL to scan (e.g., 'https://example.com')
            max_concurrent: Maximum concurrent requests

        Returns:
            List of ExposedService findings with extracted endpoint metadata
        """
        base_url = base_url.rstrip("/")

        async def check_with_base(path: str) -> ExposedService | None:
            return await self._check_swagger_path(f"{base_url}{path}")

        return await scan_parallel(
            check_args=[(p,) for p in self.SWAGGER_PATHS],
            checker=check_with_base,
            label="exposed_service_hunter:swagger",
            logger=logger,
            log_success="Found Swagger/OpenAPI spec: {0}",
        )

    async def _check_swagger_path(self, url: str) -> ExposedService | None:
        """Check if a URL serves a valid Swagger/OpenAPI specification."""
        if not self.session:
            return None
        try:
            async with self.session.head(url, follow_redirects=True, timeout=8) as head_resp:
                if head_resp.status_code == 200:
                    content_type = head_resp.headers.get("content-type", "").lower()
                    if any(ct in content_type for ct in ("json", "yaml", "x-yaml", "octet-stream")):
                        return await self._fetch_and_parse_spec(url)
                elif head_resp.status_code == 405:
                    return await self._fetch_and_parse_spec(url)
                elif head_resp.status_code in (401, 403):
                    host = urlparse(url).netloc
                    return ExposedService(
                        service_type=ServiceType.SWAGGER.value,
                        host=host,
                        port=443 if url.startswith("https") else 80,
                        exposure_type=ExposureType.PUBLIC.value,
                        risk_level=RiskLevel.MEDIUM.value,
                        metadata={
                            "endpoint": url,
                            "accessible": False,
                            "status": head_resp.status_code,
                            "note": "Swagger/OpenAPI spec detected but access-restricted",
                        },
                    )
        except httpx.HTTPError:
            pass
        except Exception as e:
            logger.debug(f"Error checking Swagger path {url}: {e}")
        return None

    async def _fetch_and_parse_spec(self, url: str) -> ExposedService | None:
        """Fetch and parse a Swagger/OpenAPI specification file."""
        if not self.session:
            return None
        try:
            async with self.session.get(url, follow_redirects=True, timeout=10) as resp:
                if resp.status_code != 200:
                    return None
                data = self._parse_spec_content(url, resp)
                if data is None:
                    return None
                return self._build_exposed_service(url, data)
        except httpx.HTTPError:
            pass
        except Exception as e:
            logger.debug(f"Error fetching Swagger spec {url}: {e}")
        return None

    def _parse_spec_content(self, url: str, resp: httpx.Response) -> dict | None:
        """Parse spec content based on content type."""
        content_type = resp.headers.get("content-type", "").lower()
        text = resp.text
        if not text or len(text) < 20:
            return None
        if "json" in content_type or url.endswith(".json"):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return self._parse_yaml_minimal(text)
        elif "yaml" in content_type or url.endswith((".yaml", ".yml")):
            return self._parse_yaml_minimal(text)
        else:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return self._parse_yaml_minimal(text)

    def _extract_spec_fields(self, data: dict) -> tuple[list[str], list[str], str, str]:
        """Extract endpoints, auth schemes, version, and title from spec data."""
        spec_version = data.get("swagger") or data.get("openapi", "unknown")
        title = "unknown"
        info = data.get("info", {})
        if isinstance(info, dict):
            title = info.get("title", "unknown")
        endpoints = []
        paths = data.get("paths", {})
        if isinstance(paths, dict):
            endpoints = list(paths.keys())[:50]
        auth_schemes = self._extract_auth_schemes(data)
        return (endpoints, auth_schemes, spec_version, title)

    def _extract_auth_schemes(self, data: dict) -> list[str]:
        """Extract authentication schemes from spec data."""
        auth_schemes: list[str] = []
        components = data.get("components", {})
        security_defs = data.get("securityDefinitions", {})
        security = components.get("securitySchemes", security_defs)
        if isinstance(security, dict):
            for scheme_name, scheme_def in security.items():
                if isinstance(scheme_def, dict):
                    auth_type = scheme_def.get("type", scheme_def.get("in", ""))
                    auth_schemes.append(f"{scheme_name}:{auth_type}")
        global_security = data.get("security", [])
        if isinstance(global_security, list):
            for sec_req in global_security[:5]:
                if isinstance(sec_req, dict):
                    auth_schemes.extend(sec_req.keys())
        return auth_schemes

    def _build_exposed_service(self, url: str, data: dict) -> ExposedService:
        """Build ExposedService from parsed spec data."""
        endpoints, auth_schemes, spec_version, title = self._extract_spec_fields(data)
        host = urlparse(url).netloc
        port = 443 if url.startswith("https") else 80
        base_path = "/".join(url.split("/")[:3])
        risk = RiskLevel.HIGH.value
        if auth_schemes:
            risk = (
                RiskLevel.CRITICAL.value
                if any(s and "api" in str(s).lower() or "bearer" in str(s).lower() for s in auth_schemes)
                else RiskLevel.HIGH.value
            )
        return ExposedService(
            service_type=ServiceType.SWAGGER.value,
            host=host,
            port=port,
            exposure_type=ExposureType.MISCONFIGURED.value,
            risk_level=risk,
            metadata={
                "endpoint": url,
                "spec_version": spec_version,
                "title": title,
                "endpoint_count": len(endpoints),
                "sample_endpoints": endpoints[:20],
                "auth_schemes": auth_schemes[:10],
                "base_path": base_path,
            },
        )

    @staticmethod
    def _parse_yaml_minimal(text: str) -> dict | None:
        """
        Minimal YAML parsing for spec extraction.

        Avoids full YAML parsing overhead — regex-based extraction of
        paths, security schemes, and info fields is ~20x faster on M1.
        Falls back to PyYAML only if regex extraction fails.
        """
        result: dict[str, Any] = {}
        paths_match = re.search("^paths:\\s*\\n((?:  \\S.*\\n)*)", text, re.MULTILINE)
        if paths_match:
            paths_block = paths_match.group(1)
            path_keys = re.findall("^  (/[^:]+):", paths_block, re.MULTILINE)
            if path_keys:
                result["paths"] = {k: {} for k in path_keys}
        version_match = re.search("^(?:swagger|openapi):\\s*[\"\\']?([\\d.]+)", text, re.MULTILINE)
        if version_match:
            result["openapi"] = version_match.group(1)
            result["swagger"] = version_match.group(1)
        info_match = re.search("^info:\\s*\\n(?:^\\s{2}title:\\s*['\\\"]?([^\\n'\\\"]+))", text, re.MULTILINE)
        if info_match:
            result["info"] = {"title": info_match.group(1).strip()}
        sec_schemes: dict[str, dict[str, str]] = {}
        sec_block = re.search(
            "(?:securitySchemes|securityDefinitions):\\s*\\n((?:  \\S[^\\n]*\\n(?:    \\S[^\\n]*\\n)*)*)",
            text,
            re.MULTILINE,
        )
        if sec_block:
            scheme_names = re.findall("^  (\\S+):", sec_block.group(1), re.MULTILINE)
            for name in scheme_names[:10]:
                type_match = re.search(
                    f"^  {re.escape(name)}:\\s*\\n    type:\\s*(\\S+)", sec_block.group(1), re.MULTILINE
                )
                if type_match:
                    sec_schemes[name] = {"type": type_match.group(1)}
            if sec_schemes:
                result["securitySchemes"] = sec_schemes
        if not result.get("paths"):
            try:
                import yaml

                data = yaml.safe_load(text)
                if isinstance(data, dict):
                    result = {
                        k: v
                        for k, v in data.items()
                        if k in ("paths", "info", "openapi", "swagger", "security", "components", "securityDefinitions")
                    }
            except Exception:
                pass
        return result if result else None

    async def parse_spec_endpoints(self, url: str) -> dict[str, Any]:
        """
        Full endpoint extraction from a Swagger/OpenAPI spec.

        Returns all documented endpoints with HTTP methods, parameters,
        and authentication requirements.

        Args:
            url: Full URL to the Swagger/OpenAPI spec

        Returns:
            Dict with 'endpoints', 'auth_schemes', 'version', 'title'
        """
        result: dict[str, Any] = {
            "endpoints": [],
            "auth_schemes": [],
            "version": "unknown",
            "title": "unknown",
            "base_url": "/".join(url.split("/")[:3]),
        }
        if not self.session:
            return result
        try:
            async with self.session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return result
                text = resp.text
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = self._parse_yaml_minimal(text)
                if isinstance(data, dict):
                    result["version"] = data.get("swagger") or data.get("openapi", "unknown")
                    info = data.get("info", {})
                    if isinstance(info, dict):
                        result["title"] = info.get("title", "unknown")
                    paths = data.get("paths", {})
                    if isinstance(paths, dict):
                        for path, methods in paths.items():
                            if isinstance(methods, dict):
                                http_methods = [
                                    m.upper()
                                    for m in methods
                                    if m.lower() in ("get", "post", "put", "delete", "patch", "options", "head")
                                ]
                                for method in http_methods:
                                    result["endpoints"].append({"path": path, "method": method})
        except Exception as e:
            logger.debug(f"Error parsing spec endpoints {url}: {e}")
        return result


class GitExposer:
    """
    1.7 FIX: Git repository forensics via direct .git file access.

    The previous implementation relied solely on "Index of" directory listing
    signatures, which misses many exposed git repos. This class performs
    direct forensics:

    1. Fetches .git/HEAD to verify it's a git repo (contains refs/heads/ refs)
    2. Fetches .git/config for repo metadata (user emails, remote URLs)
    3. Fetches .git/packed-refs for branch/tag enumeration
    4. Detects packfiles for forensics (objects/pack/*.pack)
    5. Analyzes packfile headers to determine exposure severity

    Packfile forensics:
    - Packfiles contain compressed git objects
    - Can reveal commit history, file contents, secrets
    - Even without listing, packfile access is a finding
    """

    GIT_PATHS = (".git/HEAD", ".git/config", ".git/packed-refs", ".git/objects/pack/", ".git/refs/heads/", ".git/index")
    PACKFILE_MAGIC = b"PACK"
    PACKFILE_MIN_HEADER = 12
    _RE_GIT_HEAD = re.compile("^ref:\\s*refs/heads/(\\S+)$", re.MULTILINE)
    _RE_GIT_COMMIT = re.compile("\\b[0-9a-f]{40}\\b")
    _RE_GIT_EMAIL = re.compile("[\\w.+-]+@[\\w.-]+\\.[a-zA-Z]{2,}")
    _RE_GIT_EMAIL_QUOTED = re.compile('"([\\w.+-]+@[\\w.-]+\\.[a-zA-Z]{2,})"')
    __slots__ = ("session",)

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        """Initialize GitExposer with optional session."""
        self.session: httpx.AsyncClient | None = session

    async def _analyze_packfiles(self, base: str) -> dict[str, Any]:
        """Analyze packfiles to determine exposure severity.

        Packfile header forensics:
        - PACK magic bytes indicate valid packfile
        - Version 2 is most common, version 3 supported
        - Object count reveals repository size/complexity

        Returns dict with packfile analysis results.
        """
        packfile_info: dict[str, Any] = {"detected": False, "count": 0, "total_objects": 0, "versions": set()}
        if not self.session:
            return packfile_info
        base = base.rstrip("/")
        pack_url = f"{base}/.git/objects/pack/"
        try:
            async with self.session.get(pack_url, timeout=10, follow_redirects=True) as resp:
                if resp.status != 200:
                    return packfile_info
                text = resp.text or ""
                pack_files = re.findall("pack-([a-f0-9]{40})\\.pack", text, re.IGNORECASE)
                packfile_info["count"] = len(pack_files)
                packfile_info["detected"] = len(pack_files) > 0
                if pack_files:
                    sample_pack = pack_files[0]
                    header_info = await self._fetch_packfile_header(base, sample_pack)
                    if header_info:
                        packfile_info.update(header_info)
        except Exception:
            pass
        return packfile_info

    async def _fetch_packfile_header(self, base: str, pack_hash: str) -> dict[str, Any]:
        """Fetch and analyze packfile header to get object count and version.

        Git packfile format:
        - 4 bytes: magic "PACK"
        - 4 bytes: version (2 or 3)
        - 4 bytes: number of objects (big-endian)
        """
        info: dict[str, Any] = {"total_objects": 0, "versions": set()}
        if not self.session:
            return info
        pack_url = f"{base}/.git/objects/pack/pack-{pack_hash}.pack"
        try:
            async with self.session.get(
                pack_url, timeout=10, follow_redirects=True, headers={"Range": "bytes=0-15"}
            ) as resp:
                if resp.status not in (200, 206):
                    return info
                content = resp.content
                if len(content) < 12:
                    return info
                if content[:4] == b"PACK":
                    info["versions"].add(int.from_bytes(content[4:8], "big"))
                    info["total_objects"] = int.from_bytes(content[8:12], "big")
                    if info["total_objects"] > 1000000:
                        info["total_objects"] = 1000000
        except Exception:
            pass
        info["versions"] = list(info["versions"])
        return info

    async def check_git_exposure(self, base_url: str) -> ExposedService | None:
        """Check if a URL exposes a git repository."""
        if not self.session:
            return None
        base = base_url.rstrip("/")
        git_info: dict[str, Any] = {}
        found_files: list[str] = []
        for path in self.GIT_PATHS:
            try:
                url = f"{base}/.git/{path.lstrip('.git/')}"
                async with self.session.get(url, timeout=10, follow_redirects=True) as resp:
                    if resp.status == 200:
                        found_files.append(path)
                        content = resp.text[:4096] if resp.text else ""
                        git_info[path] = content[:500]
            except Exception:
                pass
        if not found_files:
            return None
        is_git_repo = False
        branch = None
        if ".git/HEAD" in git_info:
            head_content = git_info.get(".git/HEAD", "")
            match = self._RE_GIT_HEAD.match(head_content)
            if match:
                is_git_repo = True
                branch = match.group(1)
            elif self._RE_GIT_COMMIT.search(head_content):
                is_git_repo = True
        if not is_git_repo:
            return None
        emails = []
        if ".git/config" in git_info:
            config_text = git_info.get(".git/config", "")
            emails.extend(self._RE_GIT_EMAIL.findall(config_text)[:3])
            emails.extend(self._RE_GIT_EMAIL_QUOTED.findall(config_text)[:3])
            emails = list(dict.fromkeys(emails))[:5]
        has_packfiles = ".git/objects/pack/" in found_files
        packfile_count = len([f for f in found_files if "pack" in f])
        packfile_info = await self._analyze_packfiles(base) if has_packfiles else {}
        risk = RiskLevel.HIGH.value
        if emails or has_packfiles:
            risk = RiskLevel.CRITICAL.value
        elif len(found_files) >= 3:
            risk = RiskLevel.HIGH.value
        if packfile_info.get("detected") and packfile_info.get("total_objects", 0) > 1000:
            risk = RiskLevel.CRITICAL.value
        host = urlparse(base).netloc
        port = 443 if base.startswith("https") else 80
        return ExposedService(
            service_type="git_repo",
            host=host,
            port=port,
            exposure_type=ExposureType.LEAKED.value,
            risk_level=risk,
            metadata={
                "url": base,
                "files_exposed": found_files,
                "branch": branch,
                "emails_found": emails,
                "has_packfiles": has_packfiles,
                "packfile_count": packfile_count,
                "packfile_analysis": packfile_info,
                "forensics": {
                    "git_head": git_info.get(".git/HEAD", "")[:200],
                    "git_config": git_info.get(".git/config", "")[:500],
                    "packed_refs": git_info.get(".git/packed-refs", "")[:500]
                    if ".git/packed-refs" in git_info
                    else None,
                },
            },
        )


class DirectoryListingDetector:
    """
    Directory listing detection for exposed web servers.

    Detects Apache/nginx/IIS directory listings that expose internal files:
    - Backup files (.bak, .backup, .old, .orig)
    - Configuration files (.env, .config, .ini, .yaml)
    - Log files (.log, access_log, error_log)
    - SQL dumps (.sql, .sql.gz)
    - Archive/archive files (.zip, .tar.gz)

    1.7 FIX: Now includes dedicated GitExposer for reliable git repo detection.

    Detection signatures:
    - "Index of /" in page title or body
    - "<title>Directory listing for" in HTML
    - Apache-style: "<h1>Index of" + table with Name/Last modified/Size columns
    - nginx-style: "<html><head><title>Index of"
    - IIS-style: "[To Parent Directory]" link pattern

    M1 Optimized: Regex-first detection (no DOM parsing), bounded response
    body scan (first 8KB only), async semaphore-gated concurrency.
    """

    DIR_LIST_PATHS: tuple[str, ...] = (
        "/",
        "/backup/",
        "/backups/",
        "/archive/",
        "/archives/",
        "/logs/",
        "/log/",
        "/tmp/",
        "/temp/",
        "/data/",
        "/dump/",
        "/dumps/",
        "/export/",
        "/exports/",
        "/db/",
        "/database/",
        "/config/",
        "/conf/",
        "/.git/",
        "/.svn/",
        "/.hg/",
        "/wp-content/uploads/",
        "/uploads/",
        "/files/",
        "/assets/",
    )
    DIR_LISTING_PATTERNS: tuple[re.Pattern, ...] = (
        re.compile("<title>\\s*Index\\s+of\\s+/", re.IGNORECASE),
        re.compile("<title>\\s*Directory\\s+listing\\s+for\\s+/", re.IGNORECASE),
        re.compile("<h1>\\s*Index\\s+of\\s+/", re.IGNORECASE),
        re.compile("\\[To\\s+Parent\\s+Directory\\]", re.IGNORECASE),
        re.compile('<a\\s+href="[^"]*">\\s*Parent\\s+Directory\\s*</a>', re.IGNORECASE),
        re.compile(
            "<th[^>]*>\\s*Name\\s*</th>.*<th[^>]*>\\s*(?:Last\\s+modified|Date)\\s*</th>", re.IGNORECASE | re.DOTALL
        ),
        re.compile("<th[^>]*>\\s*Size\\s*</th>", re.IGNORECASE),
    )
    SENSITIVE_EXTENSIONS: tuple[str, ...] = (
        ".bak",
        ".backup",
        ".old",
        ".orig",
        ".swp",
        ".swo",
        ".env",
        ".config",
        ".ini",
        ".cfg",
        ".conf",
        ".yaml",
        ".yml",
        ".log",
        ".sql",
        ".sql.gz",
        ".dump",
        ".pgsql",
        ".zip",
        ".tar",
        ".tar.gz",
        ".tgz",
        ".7z",
        ".rar",
        ".pem",
        ".key",
        ".crt",
        ".cer",
        ".p12",
        ".pfx",
        ".htpasswd",
        ".htaccess",
        ".passwd",
        ".shadow",
        ".DS_Store",
        ".gitignore",
        ".dockerignore",
    )
    __slots__ = ("_owned_session", "session", "_git_exposer")

    def __init__(self, session: httpx.AsyncClient | None = None) -> None:
        self.session = session
        self._owned_session = session is None
        self._git_exposer = GitExposer()

    async def __aenter__(self):
        if self._owned_session:
            self.session = httpx.AsyncClient(timeout=httpx.Timeout(total=15))
        self._git_exposer.session = self.session
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._owned_session and self.session:
            await self.session.close()
            self.session = None

    async def scan_urls(self, urls: list[str], max_concurrent: int = 10) -> list[ExposedService]:
        """
        Scan a list of URLs for directory listing exposure.

        For each URL, checks common sub-paths for directory listing indicators.
        Uses bounded response reading (first 8KB) to avoid downloading large listings.

        1.7 FIX: Now also performs dedicated git forensics via GitExposer,
        checking .git/HEAD, .git/config, .git/packed-refs, and packfiles.

        Args:
            urls: List of base URLs to scan (e.g., ['https://example.com'])
            max_concurrent: Maximum concurrent requests

        Returns:
            List of ExposedService findings for directory listings
        """
        findings: list[ExposedService] = []
        semaphore = get_semaphore(ConcurrencyCategory.SCRAPE_GENERAL)

        async def _check_url_combo(base_url: str, subpath: str) -> ExposedService | None:
            async with semaphore:
                try:
                    full_url = f"{base_url.rstrip('/')}{subpath}"
                    result = await self._check_directory_listing(full_url)
                    if result:
                        logger.info(f"Found directory listing: {full_url}")
                        return result
                except Exception as e:
                    logger.debug(f"Error checking directory {base_url}{subpath}: {e}")
                return None

        tasks: list = []
        for base_url in urls:
            for subpath in self.DIR_LIST_PATHS:
                tasks.append(_check_url_combo(base_url, subpath))
        results = await parallel_ok(*tasks, label="exposed_service_hunter:dir_list_scan")
        for result in results:
            if result:
                findings.append(result)
        for base_url in urls:
            try:
                git_result = await self._git_exposer.check_git_exposure(base_url)
                if git_result:
                    logger.info(f"[1.7] Git repo exposed: {base_url}")
                    findings.append(git_result)
            except Exception as e:
                logger.debug(f"Error checking git exposure for {base_url}: {e}")
        return findings

    async def scan_host(self, base_url: str, max_concurrent: int = 10) -> list[ExposedService]:
        """Scan a single host for directory listings."""
        return await self.scan_urls([base_url], max_concurrent)

    async def _check_directory_listing(self, url: str) -> ExposedService | None:
        """Check if a URL exposes directory listing with sensitive files."""
        if not self.session:
            return None
        try:
            headers = {"Range": "bytes=0-8191"}
            async with self.session.get(url, headers=headers, follow_redirects=True, timeout=10) as resp:
                if resp.status_code not in (200, 206, 301, 302):
                    return None
                text = (resp.text or "")[:8192]
                if not text or len(text) < 100:
                    return None
                is_dir_listing = False
                matched_patterns: list[str] = []
                for pattern in self.DIR_LISTING_PATTERNS:
                    if pattern.search(text):
                        is_dir_listing = True
                        matched_patterns.append(pattern.pattern[:60])
                        break
                if not is_dir_listing:
                    return None
                sensitive_files: list[str] = []
                total_files = 0
                file_links = re.findall('<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>', text, re.IGNORECASE)
                for href, _display in file_links:
                    if href in ("/", "..", "../", "./"):
                        continue
                    total_files += 1
                    href_lower = href.lower()
                    for ext in self.SENSITIVE_EXTENSIONS:
                        if href_lower.endswith(ext):
                            sensitive_files.append(href)
                            break
                risk = RiskLevel.MEDIUM.value
                if any(f.endswith((".pem", ".key", ".crt", ".env", ".htpasswd")) for f in sensitive_files):
                    risk = RiskLevel.CRITICAL.value
                elif any(f.endswith((".sql", ".dump", ".backup", ".bak")) for f in sensitive_files):
                    risk = RiskLevel.HIGH.value
                elif sensitive_files:
                    risk = RiskLevel.MEDIUM.value
                host = urlparse(url).netloc
                port = 443 if url.startswith("https") else 80
                return ExposedService(
                    service_type=ServiceType.DIRECTORY_LISTING.value,
                    host=host,
                    port=port,
                    exposure_type=ExposureType.MISCONFIGURED.value,
                    risk_level=risk,
                    metadata={
                        "url": url,
                        "total_files_visible": total_files,
                        "sensitive_files_count": len(sensitive_files),
                        "sensitive_files": sensitive_files[:20],
                        "match_pattern": matched_patterns[0] if matched_patterns else None,
                        "server": resp.headers.get("server", "unknown"),
                    },
                )
        except httpx.HTTPError:
            pass
        except Exception as e:
            logger.debug(f"Error checking directory listing {url}: {e}")
        return None


class ExposedServiceHunter:
    """
    Main exposed service hunter.

    Combines all exposed service discovery capabilities:
    - S3 bucket enumeration
    - GCS bucket enumeration
    - Azure Blob container enumeration
    - Database port scanning
    - GraphQL introspection
    - Certificate transparency
    - Docker/Kubernetes API detection
    - Swagger/OpenAPI spec discovery
    - Directory listing detection

    M1 Optimized: Async I/O, connection pooling, minimal memory

    Example:
        >>> hunter = ExposedServiceHunter()
        >>> results = await hunter.hunt("example.com")
        >>> print(f"Found {len(results['s3_buckets'])} S3 buckets")
    """

    __slots__ = (
        "_container_explorer",
        "_ct_logs",
        "_db_scanner",
        "_dir_listing_detector",
        "_graphql_introspector",
        "_s3_enumerator",
        "_swagger_enumerator",
        "session",
    )

    def __init__(self) -> None:
        self.session: httpx.AsyncClient | None = None
        self._s3_enumerator: S3BucketEnumerator | None = None
        self._db_scanner = DatabasePortScanner()
        self._graphql_introspector: GraphQLIntrospector | None = None
        self._ct_logs: CertificateTransparency | None = None
        self._container_explorer: ContainerAPIExplorer | None = None
        self._swagger_enumerator: SwaggerEnumerator | None = None
        self._dir_listing_detector: DirectoryListingDetector | None = None

    async def __aenter__(self):
        """Async context manager entry."""
        self.session = httpx.AsyncClient(
            timeout=httpx.Timeout(total=30), limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
        self._s3_enumerator = S3BucketEnumerator(self.session)
        self._graphql_introspector = GraphQLIntrospector(self.session)
        self._ct_logs = CertificateTransparency(self.session)
        self._container_explorer = ContainerAPIExplorer(self.session)
        self._swagger_enumerator = SwaggerEnumerator(self.session)
        self._dir_listing_detector = DirectoryListingDetector(self.session)
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
        docker_findings = await self._container_explorer.scan_docker_apis(hosts)
        findings.extend(docker_findings)
        k8s_findings = await self._container_explorer.scan_kubernetes_apis(hosts)
        findings.extend(k8s_findings)
        return findings

    async def discover_swagger_endpoints(self, base_url: str) -> list[ExposedService]:
        """
        Discover Swagger/OpenAPI specification endpoints on a target.

        Args:
            base_url: Base URL to scan

        Returns:
            List of discovered Swagger/OpenAPI specs
        """
        if not self._swagger_enumerator:
            raise RuntimeError("Hunter not initialized. Use async context manager.")
        return await self._swagger_enumerator.discover_endpoints(base_url)

    async def detect_directory_listings(self, base_url: str) -> list[ExposedService]:
        """
        Detect exposed directory listings on a target.

        Args:
            base_url: Base URL to scan

        Returns:
            List of exposed directory listings
        """
        if not self._dir_listing_detector:
            raise RuntimeError("Hunter not initialized. Use async context manager.")
        return await self._dir_listing_detector.scan_host(base_url)

    async def hunt(self, target: str) -> dict[str, list[ExposedService]]:
        """
        Perform comprehensive exposed service hunt.

        Args:
            target: Target domain or company name

        Returns:
            Dictionary with categorized findings
        """
        results: dict[str, list[ExposedService]] = {
            "s3_buckets": [],
            "databases": [],
            "graphql": [],
            "certificates": [],
            "container_apis": [],
            "swagger": [],
            "directory_listings": [],
            "all": [],
        }
        logger.info(f"Starting exposed service hunt for: {target}")
        domain = target.replace("https://", "").replace("http://", "").split("/")[0]
        try:
            logger.info("Enumerating S3 buckets...")
            s3_findings = await self.enumerate_s3_buckets(target)
            results["s3_buckets"] = s3_findings
            results["all"].extend(s3_findings)
            logger.info(f"Found {len(s3_findings)} S3 buckets")
        except Exception as e:
            logger.error(f"S3 enumeration failed: {e}")
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
                    metadata={"source": "certificate_transparency"},
                )
                for subdomain in subdomains
            ]
            results["all"].extend(results["certificates"])
            logger.info(f"Found {len(subdomains)} subdomains via CT logs")
        except Exception as e:
            logger.error(f"CT log query failed: {e}")
        try:
            logger.info("Scanning for exposed database ports...")
            hosts_to_scan = [domain] + [s.host for s in results["certificates"]][:10]
            db_findings = await self.scan_database_ports(hosts_to_scan)
            results["databases"] = db_findings
            results["all"].extend(db_findings)
            logger.info(f"Found {len(db_findings)} exposed databases")
        except Exception as e:
            logger.error(f"Database scan failed: {e}")
        try:
            logger.info("Discovering GraphQL endpoints...")
            base_url = f"https://{domain}"
            graphql_findings = await self.discover_graphql_endpoints(base_url)
            results["graphql"] = graphql_findings
            results["all"].extend(graphql_findings)
            logger.info(f"Found {len(graphql_findings)} GraphQL endpoints")
        except Exception as e:
            logger.error(f"GraphQL discovery failed: {e}")
        try:
            logger.info("Scanning for container APIs...")
            hosts_to_scan = [domain]
            container_findings = await self.scan_container_apis(hosts_to_scan)
            results["container_apis"] = container_findings
            results["all"].extend(container_findings)
            logger.info(f"Found {len(container_findings)} exposed container APIs")
        except Exception as e:
            logger.error(f"Container API scan failed: {e}")
        try:
            logger.info("Discovering Swagger/OpenAPI specs...")
            base_url = f"https://{domain}"
            swagger_findings = await self.discover_swagger_endpoints(base_url)
            results["swagger"] = swagger_findings
            results["all"].extend(swagger_findings)
            logger.info(f"Found {len(swagger_findings)} Swagger/OpenAPI specs")
            if base_url.startswith("https://"):
                http_url = base_url.replace("https://", "http://")
                http_swagger = await self.discover_swagger_endpoints(http_url)
                results["swagger"].extend(http_swagger)
                results["all"].extend(http_swagger)
        except Exception as e:
            logger.error(f"Swagger discovery failed: {e}")
        try:
            logger.info("Detecting directory listings...")
            dir_findings = await self.detect_directory_listings(f"https://{domain}")
            results["directory_listings"] = dir_findings
            results["all"].extend(dir_findings)
            logger.info(f"Found {len(dir_findings)} directory listings")
            http_dir_findings = await self.detect_directory_listings(f"http://{domain}")
            results["directory_listings"].extend(http_dir_findings)
            results["all"].extend(http_dir_findings)
        except Exception as e:
            logger.error(f"Directory listing detection failed: {e}")
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
                "container_explorer": self._container_explorer is not None,
                "swagger_enumerator": self._swagger_enumerator is not None,
                "dir_listing_detector": self._dir_listing_detector is not None,
            },
        }


class APICache:
    """
    Simple sqlite-based API cache with TTL.

    Used for rate-limited APIs like Shodan and Censys.
    """

    __slots__ = ("_conn", "_db_path", "ttl_seconds", "_finalizer")

    def __init__(self, cache_dir: str | None = None, ttl_seconds: int = 3600) -> None:
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
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute(
            "\n            CREATE TABLE IF NOT EXISTS api_cache (\n                key TEXT PRIMARY KEY,\n                value TEXT,\n                timestamp REAL\n            )\n        "
        )
        self._conn.commit()
        # F264: weakref.finalize for deterministic cleanup (Python 3.14+ compatible)
        self._finalizer = weakref.finalize(self, _api_cache_cleanup, self._conn)

    def get(self, key: str) -> str | None:
        """
        Get cached value if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value or None if expired/missing
        """
        import time

        cursor = self._conn.execute("SELECT value, timestamp FROM api_cache WHERE key = ?", (key,))
        row = cursor.fetchone()
        if row is None:
            return None
        value, timestamp = row
        if time.time() - timestamp > self.ttl_seconds:
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
            "INSERT OR REPLACE INTO api_cache (key, value, timestamp) VALUES (?, ?, ?)", (key, value, time.time())
        )
        self._conn.commit()

    def clear(self) -> None:
        """Clear all cached entries."""
        self._conn.execute("DELETE FROM api_cache")
        self._conn.commit()

    def close(self) -> None:
        """Close database connection."""
        self._conn.close()

    def __enter__(self) -> APICache:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __del__(self) -> None:
        """
        F264: Fallback cleanup — weakref.finalize is primary, __del__ is last resort.

        Called only if:
        - Finalizer wasn't triggered (interpreter shutdown order)
        - Object was resurrected and then deleted
        """
        if hasattr(self, "_finalizer") and self._finalizer.detach():
            self.close()


def _api_cache_cleanup(conn: Any) -> None:
    """
    Module-level cleanup function for weakref.finalize.

    F264: Close sqlite connection when APICache is garbage collected.
    Called automatically by weakref.finalize when the object is GC'd.
    """
    try:
        if conn is not None:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


async def search_shodan(query: str, api_key: str | None = None) -> list[dict[str, Any]]:
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
    if not api_key:
        api_key = os.environ.get("SHODAN_API_KEY", "")
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
    timeout = httpx.Timeout(total=30)
    try:
        _sess = await httpx.AsyncClient()
        async with _sess as session:
            base_url = "https://api.shodan.io/shodan/host/search"
            params = {"key": api_key if api_key else "free", "query": query, "minify": True}
            async with session.get(base_url, params=params, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    matches = data.get("matches", [])
                    for match in matches[:50]:
                        try:
                            result = {
                                "ip": match.get("ip_str", ""),
                                "port": match.get("port", 0),
                                "service": match.get("product", match.get("proto", "unknown")),
                                "banner": match.get("data", "")[:500],
                                "org": match.get("org", ""),
                                "asn": match.get("asn", ""),
                                "transport": match.get("transport", ""),
                                "timestamp": match.get("timestamp", ""),
                            }
                            results.append(result)
                        except Exception as e:
                            logger.debug(f"Error parsing Shodan match: {e}")
                            continue
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


async def search_censys(query: str, api_id: str | None = None, api_secret: str | None = None) -> list[dict[str, Any]]:
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
    if not api_id:
        api_id = os.environ.get("CENSYS_API_ID", "")
    if not api_secret:
        api_secret = os.environ.get("CENSYS_API_SECRET", "")
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
    timeout = httpx.Timeout(total=30)
    try:
        _sess = await httpx.AsyncClient()
        async with _sess as session:
            base_url = "https://search.censys.io/api/v1/search"
            headers = {"Accept": "application/json"}
            if api_id and api_secret:
                auth_str = f"{api_id}:{api_secret}"
                auth_bytes = base64.b64encode(auth_str.encode()).decode()
                headers["Authorization"] = f"Basic {auth_bytes}"
            params = {"q": query, "max_records": 50}
            async with session.get(base_url, params=params, headers=headers, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results_list = data.get("results", [])
                    for result in results_list[:50]:
                        try:
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


class BannerParser:
    """
    Parse service banners to extract technology and version information.

    Maps raw banner strings to structured (technology, version) tuples
    for CVE correlation via CveCorrelationMatrix.

    SUPPORTED BANNERS:
        - HTTP Server headers (nginx, Apache, IIS)
        - SSH banners (OpenSSH)
        - SMTP banners (Postfix, Exim, Sendmail)
        - FTP banners (vsftpd, ProFTPD)
        - Database connection strings (MySQL, PostgreSQL, Redis)
        - Docker/Kubernetes API responses
        - TLS certificate Subject/CN fields
    """

    _BANNER_PATTERNS: list[tuple[str, str, re.Pattern]] = [
        ("nginx", "nginx(?:/|\\\\s)([\\d.]+)", None),
        ("Apache", "Apache/([\\d.]+)", None),
        ("Apache", "Apache-Coyote/([\\d.]+)", None),
        ("microsoft-iis", "Microsoft-IIS/([\\d.]+)", None),
        ("lighttpd", "lighttpd/([\\d.]+)", None),
        ("OpenBSD httpd", "Server: OpenBSD httpd", None),
        ("OpenSSH", "SSH-[\\d.]+-OpenSSH_([\\d.]+)", None),
        ("OpenSSH", "OpenSSH_([\\d.]+)", None),
        ("Postfix", "Postfix", None),
        ("Exim", "Exim ([\\d.]+)", None),
        ("Sendmail", "Sendmail", None),
        ("Dovecot", "Dovecot ([\\d.]+)", None),
        ("MySQL", "mysql[\\s]+([\\d.]+)", None),
        ("MySQL", "MySQL Community Server ([\\d.]+)", None),
        ("PostgreSQL", "PostgreSQL ([\\d.]+)", None),
        ("PostgreSQL", "pg[\\s]+([\\d.]+)", None),
        ("Redis", "Redis ([\\d.]+)", None),
        ("MongoDB", "MongoDB ([\\d.]+)", None),
        ("Elasticsearch", "elasticsearch/([\\d.]+)", None),
        ("Docker", "Docker", None),
        ("Kubernetes", "kubernetes", None),
        ("PHP", "PHP/([\\d.]+)", None),
        ("Node.js", "Node\\.js", None),
        ("Django", "Django", None),
        ("Flask", "Flask", None),
        ("vsftpd", "vsftpd ([\\d.]+)", None),
        ("ProFTPD", "ProFTPD ([\\d.]+)", None),
        ("OpenVPN", "OpenVPN", None),
    ]

    @classmethod
    def _init_patterns(cls) -> None:
        """Lazily initialize compiled regex patterns."""
        if cls._BANNER_PATTERNS[0][2] is None:
            cls._BANNER_PATTERNS = [
                (tech, ver, re.compile(pat, re.IGNORECASE)) for tech, ver, _ in cls._BANNER_PATTERNS
            ]

    @classmethod
    def parse_banner(cls, banner: str) -> list[tuple[str, str | None]]:
        """
        Parse a service banner to extract technology and version.

        Args:
            banner: Raw banner string from service detection

        Returns:
            List of (technology, version) tuples. Version may be None.
        """
        cls._init_patterns()
        results: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        for tech, _ver_pat, pattern in cls._BANNER_PATTERNS:
            match = pattern.search(banner)
            if match:
                key = f"{tech}:{(match.group(1) if match.lastindex else '')}"
                if key in seen:
                    continue
                seen.add(key)
                version = match.group(1) if match.lastindex else None
                results.append((tech, version))
                if tech.lower() not in [r[0].lower() for r in results]:
                    results.append((tech, None))
        return results

    @classmethod
    def parse_http_headers(cls, headers: dict[str, str]) -> list[tuple[str, str | None]]:
        """
        Parse HTTP response headers for technology information.

        Headers checked:
            - Server: nginx, Apache, IIS, etc.
            - X-Powered-By: PHP, ASP.NET, etc.
            - X-AspNet-Version: .NET versions
        """
        results: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        server = headers.get("server", "")
        if server:
            for tech, ver, pattern in cls._BANNER_PATTERNS:
                if pattern.search(server):
                    key = f"{tech}:{ver}"
                    if key in seen:
                        continue
                    seen.add(key)
                    version = pattern.search(server)
                    ver = version.group(1) if version and version.lastindex else None
                    results.append((tech, ver))
        powered_by = headers.get("x-powered-by", "")
        if powered_by:
            if "PHP" in powered_by:
                match = re.search("PHP[/\\s]([\\d.]+)", powered_by, re.IGNORECASE)
                results.append(("PHP", match.group(1) if match else None))
            elif "ASP.NET" in powered_by:
                match = re.search("ASP\\.NET[\\s]([\\d.]+)", powered_by, re.IGNORECASE)
                results.append(("ASP.NET", match.group(1) if match else None))
        asp_ver = headers.get("x-aspnet-version") or headers.get("x-aspnetmvc-version")
        if asp_ver:
            results.append(("ASP.NET", asp_ver))
        return results


def correlate_banner_cves(banner: str) -> list[dict[str, Any]]:
    """
    Correlate CVEs from a banner string using CveCorrelationMatrix.

    ISSUE [ULTIMATE]-004: Zero-network CVE lookup via local DuckDB matrix.

    Args:
        banner: Service banner string

    Returns:
        List of CVE match dicts with keys: cve_id, cvss_score, cwe_id, description
    """
    try:
        from hledac.universal.knowledge.duckdb_cve_matrix import get_cve_matrix

        matrix = get_cve_matrix()
    except ImportError:
        return []
    results: list[dict[str, Any]] = []
    parsed = BannerParser.parse_banner(banner)
    for tech, version in parsed:
        try:
            matches = matrix.match(tech, version)
            for match in matches[:5]:
                results.append(
                    {
                        "technology": tech,
                        "version": version,
                        "cve_id": match.cve_id,
                        "cvss_score": match.cvss_score,
                        "cwe_id": match.cwe_id,
                        "description": match.description_snippet[:200],
                    }
                )
        except Exception:
            continue
    return results


async def banner_grabber(host: str, port: int, timeout: float = 5.0) -> str | None:
    """
    Grab service banner via TCP connection.

    Attempts multiple protocols based on port:
        - 80/443: HTTP request
        - 22: SSH banner
        - 25/587: SMTP banner
        - 21: FTP banner
        - 3306/5432/6379/27017: Database handshake

    Args:
        host: Target host
        port: Target port
        timeout: Connection timeout in seconds

    Returns:
        Raw banner string or None
    """
    if port in (80, 8080, 443, 8443):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                protocol = "https" if port in (443, 8443) else "http"
                resp = await client.get(f"{protocol}://{host}:{port}/", timeout=timeout)
                headers = dict(resp.headers)
                banner_parts = [f"HTTP/{resp.http_version} {resp.status_code}"]
                if "server" in headers:
                    banner_parts.append(f"Server: {headers['server']}")
                if "x-powered-by" in headers:
                    banner_parts.append(f"X-Powered-By: {headers['x-powered-by']}")
                return "\n".join(banner_parts)
        except Exception:
            pass
    else:
        try:
            reader, writer = await safe_wait_for(asyncio.open_connection(host, port), timeout=timeout)
            try:
                if port == 22:
                    pass
                elif port == 21:
                    writer.write(b"QUIT\r\n")
                    await writer.drain()
                banner = await safe_wait_for(reader.read(1024), timeout=timeout)
                return banner.decode("utf-8", errors="ignore").strip()
            finally:
                writer.close()
                await writer.wait_closed()
        except Exception:
            pass
    return None


__all__ = [
    "ExposedServiceHunter",
    "S3BucketEnumerator",
    "DatabasePortScanner",
    "GraphQLIntrospector",
    "CertificateTransparency",
    "ContainerAPIExplorer",
    "SwaggerEnumerator",
    "DirectoryListingDetector",
    "ExposedService",
    "S3Bucket",
    "CertificateInfo",
    "ServiceType",
    "ExposureType",
    "RiskLevel",
    "quick_hunt",
    "check_s3_bucket",
    "scan_graphql_endpoint",
    "search_shodan",
    "search_censys",
    "APICache",
    "BannerParser",
    "correlate_banner_cves",
    "banner_grabber",
]
