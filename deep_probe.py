#!/usr/bin/env python3
"""
Deep Probe Scanner - Advanced Deep Crawling & Hidden Content Discovery
=======================================================================

Integrated from launch_shadow_walker.py - Shadow Walker Algorithm for deep research
and hidden endpoint discovery.

This module provides comprehensive deep crawling capabilities including:
- Shadow Walker algorithm for path prediction
- Dorking Engine for complex query generation
- Wayback Machine integration via CDX API
- Memory-optimized URL set management
- Tech stack signature detection

Categories: Deep Crawling & "Škvíry Internetu"
"""



import hashlib
import logging
import time
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

logger = logging.getLogger(__name__)

# M1 8GB: Hard bounds for memory safety
MAX_DISCOVERED_URLS: int = 100  # Max URLs to return from scan
MAX_BUCKET_RESULTS: int = 50  # Max S3 buckets to enumerate
MAX_IPFS_RESULTS: int = 20  # Max IPFS results
IPFS_TIMEOUT_S: float = 10.0  # Per-request timeout
SCAN_TIMEOUT_S: float = 30.0  # Total scan timeout

@dataclass
class DiscoveredEndpoint:
    """Represents a discovered endpoint with metadata."""
    url: str
    title: str | None = None
    confidence_score: float = 0.0
    discovery_method: str = "unknown"
    file_type: str | None = None
    path: str = ""
    source_url: str | None = None
    tech_stack: dict[str, Any] | None = None
    last_modified: str | None = None
    size_bytes: int | None = None

class MemoryOptimizedURLSet:
    """Memory-efficient URL set with bloom filter optimization."""

    def __init__(self, max_memory_mb: int = 50):
        self.max_memory_mb = max_memory_mb
        self.urls: set[str] = set()
        self._memory_usage = 0
        self._closed = False

    def add(self, url: str) -> bool:
        """Add URL if not already present."""
        if url in self.urls:
            return False

        # Estimate memory usage
        estimated_size = len(url.encode('utf-8')) + 64  # URL + metadata overhead
        if self._memory_usage + estimated_size > self.max_memory_mb * 1024 * 1024:
            logger.warning("Memory limit reached, cannot add more URLs")
            return False

        self.urls.add(url)
        self._memory_usage += estimated_size
        return True

    def __contains__(self, url: str) -> bool:
        return url in self.urls

    def __len__(self) -> int:
        return len(self.urls)

class DorkingEngine:
    """Advanced dorking engine for generating complex search queries."""

    def __init__(self):
        self.patterns = {
            'academic': [
                'site:{domain} filetype:pdf "research"',
                'site:{domain} filetype:pdf "study"',
                'site:{domain} filetype:pdf "analysis"',
                'site:{domain} inurl:research filetype:pdf',
                'site:{domain} inurl:publications filetype:pdf',
                # arXiv patterns
                'site:arxiv.org "{domain}"',
                'site:arxiv.org abs "{domain}"',
                'site:arxiv.org pdf "{domain}"',
                # CrossRef patterns
                'site:crossref.org "{domain}"',
                'site:doi.org "{domain}"',
                # Semantic Scholar patterns
                'site:semanticscholar.org "{domain}"',
                'site:semanticscholar.org/arxiv "{domain}"',
            ],
            'technical': [
                'site:{domain} filetype:pdf "specification"',
                'site:{domain} filetype:pdf "documentation"',
                'site:{domain} filetype:pdf "manual"',
                'site:{domain} inurl:docs filetype:pdf',
                'site:{domain} inurl:api filetype:pdf'
            ],
            'financial': [
                'site:{domain} filetype:pdf "report"',
                'site:{domain} filetype:pdf "annual"',
                'site:{domain} filetype:pdf "quarterly"',
                'site:{domain} inurl:investor filetype:pdf',
                'site:{domain} inurl:financial filetype:pdf'
            ],
            'government': [
                'site:{domain} filetype:pdf "classified"',
                'site:{domain} filetype:pdf "declassified"',
                'site:{domain} filetype:pdf "memo"',
                'site:{domain} inurl:foia filetype:pdf',
                'site:{domain} inurl:archives filetype:pdf'
            ]
        }

    def generate_complex_queries(self, topic: str, query_type: str = 'academic') -> list[str]:
        """Generate complex dorking queries for a topic."""
        if query_type not in self.patterns:
            query_type = 'academic'

        base_patterns = self.patterns[query_type]
        queries = []

        # Generate variations
        for pattern in base_patterns:
            # Add topic-specific variations
            queries.append(pattern.replace('{domain}', f'{topic}.edu'))
            queries.append(pattern.replace('{domain}', f'{topic}.gov'))
            queries.append(pattern.replace('{domain}', f'{topic}.org'))

            # Add filetype variations
            queries.append(pattern.replace('filetype:pdf', 'filetype:doc'))
            queries.append(pattern.replace('filetype:pdf', 'filetype:txt'))

        return list(set(queries))  # Remove duplicates

class TechStackSignature:
    """Tech stack signature detection for discovered endpoints."""

    def __init__(self):
        self.signatures = {
            'wordpress': ['wp-content', 'wp-admin', 'wp-json'],
            'drupal': ['node/', 'drupal.js', 'sites/default'],
            'joomla': ['administrator/', 'components/', 'modules/'],
            'django': ['admin/', 'static/admin', 'django'],
            'flask': ['static/', 'api/', 'swagger'],
            'express': ['api/', 'swagger', 'node_modules'],
            'rails': ['assets/', 'rails', 'application.js'],
            'laravel': ['vendor/', 'artisan', 'storage/'],
            'spring': ['actuator/', 'swagger-ui', 'WEB-INF'],
            'asp.net': ['WebResource.axd', 'ScriptResource.axd', 'App_Data']
        }

    def detect_stack(self, url: str, content: str | None = None) -> dict[str, Any] | None:
        """Detect technology stack from URL and content."""
        detected: dict[str, Any] = {
            'framework': None,
            'confidence': 0.0,
            'indicators': []
        }

        url_lower = url.lower()

        for framework, indicators in self.signatures.items():
            matches = 0
            found_indicators = []

            for indicator in indicators:
                if indicator.lower() in url_lower:
                    matches += 1
                    found_indicators.append(indicator)

            if content:
                for indicator in indicators:
                    if indicator.lower() in content.lower():
                        matches += 2  # Content matches weigh more
                        found_indicators.append(indicator)

            if matches > 0:
                confidence = min(matches / len(indicators), 1.0)
                if confidence > detected['confidence']:
                    detected['framework'] = framework
                    detected['confidence'] = confidence
                    detected['indicators'] = found_indicators
        return detected if detected['framework'] else None


# =============================================================================
# DeepProbeScanner — Wayback/CDX + path discovery scanner
# =============================================================================

class DeepProbeScanner:
    """
    M1 8GB-safe deep probe scanner using Wayback Machine CDX API.

    Bounded: MAX_DISCOVERED_URLS=100 per scan, SCAN_TIMEOUT_S=30s.
    No GPU, no MLX, no heavy dependencies — pure asyncio + curl_cffi.
    """

    def __init__(self, max_memory_mb: int = 100) -> None:
        self.max_memory_mb = max_memory_mb
        self._url_set: list[str] = []

    async def scan(self, domain: str) -> list[str]:
        """
        Query Wayback Machine CDX API for discovered URLs under domain.

        Returns up to MAX_DISCOVERED_URLS URLs.
        """
        if not domain:
            return []
        try:
            import aiohttp
            cdx_url = f"https://web.archive.org/cdx/search/cdx?url={domain}/*&output=json&limit={MAX_DISCOVERED_URLS}&fl=original"
            timeout = aiohttp.ClientTimeout(total=SCAN_TIMEOUT_S)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(cdx_url) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    urls = []
                    for row in data[1:]:  # Skip header row
                        if isinstance(row, list) and row:
                            urls.append(row[0])
                    return urls[:MAX_DISCOVERED_URLS]
        except Exception:
            return []

    async def scan_s3_buckets(
        self, domain: str, store: Any = None, max_buckets: int = MAX_BUCKET_RESULTS
    ) -> tuple[list[dict], list[CanonicalFinding]]:
        """
        Scan for open S3 buckets using common naming patterns.

        Returns (raw_results, CanonicalFinding list).
        M1 8GB bounded: max_buckets limit.
        """
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        if not domain:
            return [], []
        findings: list[CanonicalFinding] = []
        raw_results: list[dict] = []
        # Common bucket naming patterns
        patterns = [domain, domain.replace(".", "-"), domain.replace(".", "_")]
        checked: set[str] = set()
        for pattern in patterns:
            for suffix in ["-www", "-assets", "-media", "-static", "-data", "-backup"]:
                bucket_name = f"{pattern}{suffix}"
                if bucket_name in checked:
                    continue
                checked.add(bucket_name)
                if len(checked) >= max_buckets:
                    break
                # Probe bucket exists via HEAD request (fast)
                try:
                    import aiohttp
                    url = f"https://{bucket_name}.s3.amazonaws.com"
                    timeout = aiohttp.ClientTimeout(total=5.0)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.head(url, allow_redirects=True) as resp:
                            if resp.status == 200:
                                raw_results.append({"bucket": bucket_name, "url": url, "status": 200})
                                fid = hashlib.sha256(bucket_name.encode()).hexdigest()[:16]
                                findings.append(CanonicalFinding(
                                    finding_id=fid,
                                    query=domain,
                                    source_type="deep_probe",
                                    confidence=0.5,
                                    ts=time.time(),
                                    provenance=("deep_probe", "s3", bucket_name),
                                    payload_text=f"Open S3 bucket: {bucket_name}",
                                ))
                except Exception:  # noqa: BLE001
                    pass
            if len(checked) >= max_buckets:
                break
        return raw_results, findings

    def _make_bucket_finding(
        self, result: dict, source_type: str = "deep_probe"
    ) -> CanonicalFinding | None:
        """
        Build a CanonicalFinding from a bucket scan result dict.

        Args:
            result: Dict with keys: bucket, provider, objects, accessible.
            source_type: Source type tag for the finding.

        Returns:
            CanonicalFinding or None if result is empty/invalid.
        """
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        bucket = result.get("bucket")
        if not bucket:
            return None
        objects = result.get("objects", [])
        accessible = result.get("accessible", False)
        confidence = 0.9 if (objects and accessible) else 0.5 if accessible else 0.3
        fid = hashlib.sha256(bucket.encode()).hexdigest()[:16]
        return CanonicalFinding(
            finding_id=fid,
            query=bucket,
            source_type=source_type,
            confidence=confidence,
            ts=time.time(),
            provenance=(source_type, "s3", bucket),
            payload_text=f"Open S3 bucket: {bucket} ({len(objects)} objects)",
        )


# =============================================================================
# scan_s3_buckets — standalone wrapper for test compatibility (F197A)
# =============================================================================
async def scan_s3_buckets(
    domain: str,
    store: Any = None,
    max_buckets: int = MAX_BUCKET_RESULTS,
) -> tuple[list[dict], list]:
    """
    Standalone wrapper — creates a DeepProbeScanner instance and delegates
    to its scan_s3_buckets() method. Exists for test compatibility
    (tests/probe_f197a imports this as a standalone function).

    Returns (raw_results, CanonicalFinding list).
    """
    scanner = DeepProbeScanner()
    return await scanner.scan_s3_buckets(domain, store=store, max_buckets=max_buckets)


# =============================================================================
# scan_ipfs — IPFS content discovery via public gateways
# =============================================================================

async def scan_ipfs(
    query: str,
    store: Any = None,
    max_results: int = MAX_IPFS_RESULTS,
) -> list[CanonicalFinding]:
    """
    Search IPFS DHT/pinning services for content matching query.

    M1 8GB bounded: max_results limit, IPFS_TIMEOUT_S per request.
    Returns list of CanonicalFinding.
    """
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    if not query:
        return []
    findings: list[CanonicalFinding] = []
    # IPFS content addressing via CID v1
    import re
    cid_pattern = re.compile(r'\b(Qm[1-9A-HJ-NP-Za-km-z]{44,})\b')
    cids = cid_pattern.findall(query)
    for cid in cids[:max_results]:
        for gateway in ["https://cloudflare-ipfs.com/ipfs/", "https://ipfs.io/ipfs/"]:
            url = f"{gateway}{cid}"
            try:
                import aiohttp
                timeout = aiohttp.ClientTimeout(total=IPFS_TIMEOUT_S)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.head(url, allow_redirects=True) as resp:
                        if resp.status == 200:
                            fid = hashlib.sha256(cid.encode()).hexdigest()[:16]
                            findings.append(CanonicalFinding(
                                finding_id=fid,
                                query=query,
                                source_type="deep_probe",
                                confidence=0.7,
                                ts=time.time(),
                                provenance=("deep_probe", "ipfs", cid),
                                payload_text=f"IPFS content: {url}",
                            ))
                            break  # One success per CID is enough
            except Exception:  # noqa: BLE001
                pass
        if len(findings) >= max_results:
            break
    return findings
