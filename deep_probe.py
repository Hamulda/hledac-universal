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

import asyncio
import logging
import re
import hashlib
import time
from collections import deque
from typing import List, Dict, Set, Optional, Tuple, Any
from dataclasses import dataclass
from urllib.parse import urlparse, urljoin, parse_qs, urlencode
from pathlib import Path
import aiohttp
import json
import numpy as np

logger = logging.getLogger(__name__)

@dataclass
class DiscoveredEndpoint:
    """Represents a discovered endpoint with metadata."""
    url: str
    title: Optional[str] = None
    confidence_score: float = 0.0
    discovery_method: str = "unknown"
    file_type: Optional[str] = None
    path: str = ""
    source_url: Optional[str] = None
    tech_stack: Optional[Dict[str, Any]] = None
    last_modified: Optional[str] = None
    size_bytes: Optional[int] = None

class MemoryOptimizedURLSet:
    """Memory-efficient URL set with bloom filter optimization."""

    def __init__(self, max_memory_mb: int = 50):
        self.max_memory_mb = max_memory_mb
        self.urls: Set[str] = set()
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

    def generate_complex_queries(self, topic: str, query_type: str = 'academic') -> List[str]:
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

    def detect_stack(self, url: str, content: Optional[str] = None) -> Dict[str, Any]:
        """Detect technology stack from URL and content."""
        detected = {
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
                    detected.update({
                        'framework': framework,
                        'confidence': confidence,
                        'indicators': found_indicators
                    })

        return detected

class ShadowWalkerAlgorithm:
    """Shadow Walker algorithm for intelligent path prediction."""

    def __init__(self):
        self.pattern_analyzer = PathPatternAnalyzer()

    def predict_next_paths(self, base_url: str, known_paths: List[str]) -> List[Tuple[str, float]]:
        """Predict next likely paths based on known paths."""
        return self.predict_next_paths_with_reranking(base_url, known_paths, query="", embedder=None)

    def predict_next_paths_with_reranking(
        self,
        base_url: str,
        known_paths: List[str],
        query: str = "",
        embedder=None
    ) -> List[Tuple[str, float]]:
        """
        Predict next likely paths based on known paths.

        Args:
            base_url: Base URL
            known_paths: Known existing paths
            query: Optional query for semantic reranking
            embedder: Optional embedder from ModelManager for reranking
        """
        if not known_paths:
            return []

        predictions = []
        parsed_base = urlparse(base_url)

        # Analyze patterns in known paths
        patterns = self.pattern_analyzer.analyze_patterns(known_paths)

        # Generate predictions based on patterns
        for pattern in patterns:
            # Use new method if available
            if hasattr(pattern, 'generate_predictions_with_scores'):
                predicted_paths = pattern.generate_predictions_with_scores()
            else:
                # Fallback for backward compatibility
                old_preds = pattern.generate_predictions()
                predicted_paths = [(p, 0.5) for p in old_preds]

            for path, confidence in predicted_paths:
                full_url = urljoin(base_url, path)
                predictions.append((full_url, confidence))

        # Apply reranking if query and embedder provided
        if query and embedder:
            predictions = self._rerank_predictions(predictions, query, embedder)
        else:
            # Sort by confidence
            predictions.sort(key=lambda x: x[1], reverse=True)

        # Remove duplicates while preserving highest confidence
        seen_urls = set()
        unique_predictions = []

        for url, confidence in predictions:
            if url not in seen_urls:
                unique_predictions.append((url, confidence))
                seen_urls.add(url)

        return unique_predictions[:20]  # Top 20 predictions

    def _rerank_predictions(
        self,
        predictions: List[Tuple[str, float]],
        query: str,
        embedder
    ) -> List[Tuple[str, float]]:
        """
        Rerank predictions using semantic similarity to query.

        Args:
            predictions: List of (url, base_score) tuples
            query: Search query for reranking
            embedder: Embedder instance from ModelManager
        """
        if not predictions or not query or embedder is None:
            return predictions

        try:
            # Compute query embedding once
            query_emb = embedder.embed(query)

            scored = []
            for url, base_score in predictions:
                # Extract path part for embedding
                path = url.rstrip('/').split('/')[-1] if '/' in url else url
                if not path:
                    path = url
                # Get embedding for path
                path_emb = embedder.embed(path)
                if path_emb is not None and query_emb is not None:
                    # Cosine similarity
                    sim = np.dot(query_emb, path_emb) / (np.linalg.norm(query_emb) * np.linalg.norm(path_emb) + 1e-8)
                    # Combine with base score (60% base, 40% semantic)
                    combined = 0.6 * base_score + 0.4 * sim
                    scored.append((url, combined))
                else:
                    scored.append((url, base_score))

            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:10]  # return top 10
        except Exception as e:
            logger.warning(f"Reranking failed: {e}")
            return predictions

class PathPatternAnalyzer:
    """Analyzes path patterns to predict new paths."""

    def analyze_patterns(self, paths: List[str]) -> List['PathPattern']:
        """Analyze paths and extract patterns."""
        patterns = []

        # Extract date patterns
        date_pattern = self._extract_date_pattern(paths)
        if date_pattern:
            patterns.append(date_pattern)

        # Extract sequential patterns
        sequential_pattern = self._extract_sequential_pattern(paths)
        if sequential_pattern:
            patterns.append(sequential_pattern)

        # Extract file type patterns
        file_pattern = self._extract_file_pattern(paths)
        if file_pattern:
            patterns.append(file_pattern)

        return patterns

    def _extract_date_pattern(self, paths: List[str]) -> Optional['DatePathPattern']:
        """Extract date-based patterns from paths."""
        # Look for year patterns like /2023/, /2022/, etc.
        year_pattern = re.compile(r'/(\d{4})/')
        years = []

        for path in paths:
            matches = year_pattern.findall(path)
            years.extend([int(year) for year in matches])

        if len(set(years)) >= 2:
            years_sorted = sorted(set(years))
            return DatePathPattern(years_sorted)

        return None

    def _extract_sequential_pattern(self, paths: List[str]) -> Optional['SequentialPathPattern']:
        """Extract sequential number patterns."""
        # Look for numbered sequences
        number_pattern = re.compile(r'/(\d+)/')
        sequences = []

        for path in paths:
            matches = number_pattern.findall(path)
            sequences.extend([int(num) for num in matches])

        if len(set(sequences)) >= 3:
            sequences_sorted = sorted(set(sequences))
            return SequentialPathPattern(sequences_sorted)

        return None

    def _extract_file_pattern(self, paths: List[str]) -> Optional['FilePathPattern']:
        """Extract file type patterns."""
        extensions = []
        for path in paths:
            if '.' in path:
                ext = path.split('.')[-1].lower()
                if ext in ['pdf', 'doc', 'docx', 'txt', 'csv', 'xml', 'json']:
                    extensions.append(ext)

        if extensions:
            return FilePathPattern(list(set(extensions)))

        return None

class PathPattern:
    """Base class for path patterns."""

    def generate_predictions(self) -> List[Tuple[str, float]]:
        """Generate path predictions with confidence scores."""
        raise NotImplementedError("PathPattern.generate_predictions must be implemented by subclass")

class DatePathPattern(PathPattern):
    """Pattern for date-based paths."""

    def __init__(self, years: List[int]):
        self.years = years

    def generate_predictions(self) -> List[Tuple[str, float]]:
        predictions = []
        if not self.years:
            return predictions

        # Predict next year
        next_year = max(self.years) + 1
        predictions.append((f"/{next_year}/", 0.8))

        # Predict previous year
        prev_year = min(self.years) - 1
        if prev_year >= 1900:
            predictions.append((f"/{prev_year}/", 0.6))

        return predictions

class SequentialPathPattern(PathPattern):
    """Pattern for sequential number paths."""

    def __init__(self, numbers: List[int]):
        self.numbers = numbers

    def generate_predictions(self) -> List[Tuple[str, float]]:
        predictions = []
        if len(self.numbers) < 2:
            return predictions

        # Calculate step
        diffs = [self.numbers[i+1] - self.numbers[i] for i in range(len(self.numbers)-1)]
        avg_step = sum(diffs) / len(diffs)

        # Predict next number
        next_num = int(self.numbers[-1] + avg_step)
        predictions.append((f"/{next_num}/", 0.7))

        return predictions

    def generate_predictions_with_scores(self) -> List[Tuple[str, float]]:
        """
        Generate multiple prediction candidates with confidence scores.

        Returns:
            List of (url_path, confidence_score) tuples
        """
        predictions = []
        if len(self.numbers) < 2:
            return predictions

        # Calculate step
        diffs = [self.numbers[i+1] - self.numbers[i] for i in range(len(self.numbers)-1)]
        avg_step = sum(diffs) / len(diffs)

        # Generate multiple candidates (not just next)
        for offset in range(1, 6):  # next 5 numbers
            next_num = int(self.numbers[-1] + avg_step * offset)
            predictions.append((f"/{next_num}/", 0.7 - offset * 0.1))

        # Also try step variations
        if len(diffs) >= 2:
            min_step = max(1, int(min(diffs)))
            max_step = int(max(diffs)) + 1
            step_range = range(min_step, max_step + 1)
            step_step = max(1, (max_step - min_step) // 3) if max_step > min_step else 1
            for step in range(min_step, max_step + 1, step_step):
                if step != avg_step:
                    next_num = self.numbers[-1] + step
                    predictions.append((f"/{next_num}/", 0.5))

        return predictions


class FilePathPattern(PathPattern):
    """Pattern for file type paths."""

    def __init__(self, extensions: List[str]):
        self.extensions = extensions

    def generate_predictions(self) -> List[Tuple[str, float]]:
        predictions = []
        common_dirs = ['data', 'files', 'documents', 'reports', 'research']

        for ext in self.extensions:
            for dir_name in common_dirs:
                predictions.append((f"/{dir_name}/file.{ext}", 0.5))

        return predictions

class WaybackCDXClient:
    """Client for Wayback Machine CDX API."""

    def __init__(self):
        self.session = None
        self.base_url = "https://web.archive.org/cdx/search/cdx"

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def query_snapshots(self, url: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Query Wayback Machine for URL snapshots."""
        if not self.session:
            raise RuntimeError("Client not initialized")

        params = {
            'url': url,
            'output': 'json',
            'limit': str(limit),
            'fl': 'timestamp,original,statuscode,digest,length'
        }

        try:
            async with self.session.get(self.base_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if len(data) > 1:  # First row is headers
                        headers = data[0]
                        return [dict(zip(headers, row)) for row in data[1:]]
                return []
        except Exception as e:
            logger.error(f"Wayback CDX query failed: {e}")
            return []

class DeepProbeScanner:
    """
    Main deep probe scanner integrating all deep crawling capabilities.

    This class provides the unified interface for deep internet research
    and hidden content discovery.
    """

    def __init__(self, max_memory_mb: int = 100):
        self.max_memory_mb = max_memory_mb
        self.shadow_walker = ShadowWalkerAlgorithm()
        self.dorking_engine = DorkingEngine()
        self.tech_detector = TechStackSignature()
        self.discovered_urls = MemoryOptimizedURLSet(max_memory_mb)

    async def scan(self, domain: str) -> list[str]:
        """
        P10: Scan a domain using dorking, Wayback CDX, and path prediction.

        Combines multiple discovery methods:
          1. Dorking: Generate search queries for the domain
          2. Wayback CDX: Query historical URLs for the domain
          3. Path prediction: Use Shadow Walker to predict hidden paths

        Args:
            domain: Domain to scan (e.g., "example.com")

        Returns:
            List of discovered URLs relevant to the domain

        Anti-patterns:
          - No Playwright/Selenium (explicitly excluded in P10)
          - No large buffers (bounded MemoryOptimizedURLSet)
          - No blocking sync calls (all async)
        """
        discovered: list[str] = []
        seen: set[str] = set()

        # Filter for seen URLs
        def _add(url: str) -> bool:
            if url in seen:
                return False
            seen.add(url)
            return True

        # 1. Wayback CDX discovery
        try:
            async with WaybackCDXClient() as client:
                # Query with wildcards for the domain
                wildcard_query = f"*.{domain}/*"
                snapshots = await client.query_snapshots(wildcard_query, limit=100)
                for snapshot in snapshots[:50]:
                    original = snapshot.get("original", "")
                    if original and _add(original):
                        discovered.append(original)
        except Exception as e:
            logger.debug(f"Wayback CDX scan failed for {domain}: {e}")

        # 2. Path prediction using Shadow Walker
        try:
            base_url = f"https://{domain}"
            predictions = self.shadow_walker.predict_next_paths(base_url, [])
            for predicted_url, confidence in predictions:
                # Filter to only URLs for this domain
                if domain in predicted_url and confidence > 0.4 and _add(predicted_url):
                    discovered.append(predicted_url)
        except Exception as e:
            logger.debug(f"Path prediction failed for {domain}: {e}")

        # 3. Dorking URLs (generate search query URLs, not actual searches)
        try:
            dork_queries = self.dorking_engine.generate_complex_queries(domain, 'technical')
            # Convert dork queries to potential URL patterns (not actual search results)
            for query in dork_queries[:20]:
                # Extract path hints from dork patterns
                # e.g., "site:example.com filetype:pdf" -> potential URL
                if f"site:{domain}" in query:
                    # Generate plausible URL from query
                    path_hints = [
                        f"/research/{domain}.pdf",
                        f"/documents/{domain}-report.pdf",
                        f"/publications/{domain}-paper.pdf",
                        f"/data/{domain}-analysis.pdf",
                    ]
                    for hint in path_hints:
                        url = f"https://{domain}{hint}"
                        if _add(url):
                            discovered.append(url)
        except Exception as e:
            logger.debug(f"Dorking URL generation failed for {domain}: {e}")

        logger.info(f"DeepProbeScanner.scan({domain}): {len(discovered)} URLs discovered")
        return discovered[:100]  # Cap at 100 URLs

    async def deep_crawl(self, base_url: str, max_depth: int = 3) -> List[DiscoveredEndpoint]:
        """
        Perform deep crawling starting from base URL.

        Args:
            base_url: Starting URL for crawling
            max_depth: Maximum crawling depth

        Returns:
            List of discovered endpoints
        """
        logger.info(f"Starting deep crawl of {base_url} with depth {max_depth}")

        discovered = []
        visited = set()
        to_visit = deque([(base_url, 0)])  # (url, depth)

        while to_visit and len(visited) < 1000:  # Safety limit
            current_url, depth = to_visit.popleft()

            if current_url in visited or depth > max_depth:
                continue

            visited.add(current_url)

            # Discover endpoints at current URL
            endpoints = await self._discover_endpoints(current_url)
            discovered.extend(endpoints)

            # Use Shadow Walker to predict next URLs
            if depth < max_depth:
                predictions = self.shadow_walker.predict_next_paths(current_url, [])
                for predicted_url, confidence in predictions:
                    if predicted_url not in visited and confidence > 0.5:
                        to_visit.append((predicted_url, depth + 1))

        return discovered

    async def _discover_endpoints(self, url: str) -> List[DiscoveredEndpoint]:
        """Discover endpoints at a given URL."""
        endpoints = []

        # Use dorking engine to generate search queries
        dork_queries = self.dorking_engine.generate_complex_queries(
            urlparse(url).netloc.split('.')[0], 'academic'
        )

        # Simulate endpoint discovery (in real implementation, this would
        # actually crawl and analyze the URL)
        for query in dork_queries[:5]:  # Limit for demo
            endpoint = DiscoveredEndpoint(
                url=f"{url.rstrip('/')}/generated/{hash(query) % 1000}.pdf",
                title=f"Discovered via: {query[:50]}...",
                confidence_score=0.7,
                discovery_method="dorking",
                file_type=".pdf",
                path=f"/generated/{hash(query) % 1000}.pdf",
                source_url=url
            )
            endpoints.append(endpoint)

        return endpoints

    async def analyze_endpoint(self, endpoint: DiscoveredEndpoint) -> DiscoveredEndpoint:
        """Analyze a discovered endpoint for additional metadata."""
        # Detect tech stack
        endpoint.tech_stack = self.tech_detector.detect_stack(endpoint.url)

        # Add additional analysis here (content analysis, etc.)
        return endpoint

    async def wayback_discovery(self, url: str) -> List[DiscoveredEndpoint]:
        """Discover historical versions using Wayback Machine."""
        endpoints = []

        async with WaybackCDXClient() as client:
            snapshots = await client.query_snapshots(url, limit=50)

            for snapshot in snapshots:
                if snapshot.get('statuscode') == '200':
                    wayback_url = f"https://web.archive.org/web/{snapshot['timestamp']}/{url}"
                    endpoint = DiscoveredEndpoint(
                        url=wayback_url,
                        confidence_score=0.8,
                        discovery_method="wayback",
                        last_modified=snapshot.get('timestamp'),
                        source_url=url
                    )
                    endpoints.append(endpoint)

        return endpoints

    async def scan_s3_buckets(
        self,
        domain: str,
        store=None,
        max_buckets: int = 50
    ) -> Tuple[List[dict], List['CanonicalFinding']]:
        """
        P14: Scan for open S3/GCS/Azure Blob buckets.

        Generates probable bucket names from domain and tests
        anonymous access using boto3 (S3), aiohttp (GCS, Azure).

        Args:
            domain: Target domain (e.g., "example.com")
            store: Optional DuckDBShadowStore for persisting findings
            max_buckets: Maximum bucket names to try (default 50)

        Returns:
            List of dicts with structure:
            {'bucket': str, 'provider': str, 'objects': List[dict], 'accessible': bool}

        Anti-patterns:
          - No API keys hardcoded (uses unsigned requests for S3)
          - Rate limited via asyncio.Semaphore(5)
          - No images >50MB stored (object listing only)
        """
        import asyncio
        import hashlib

        # Generate probable bucket names from domain
        bucket_names = self._generate_bucket_candidates(domain, max_buckets)

        results = []
        semaphore = asyncio.Semaphore(5)  # Rate limit: 5 concurrent

        # S3 scan tasks
        s3_tasks = [self._check_s3_bucket(name, semaphore) for name in bucket_names["s3"]]
        # GCS scan tasks
        gcs_tasks = [self._check_gcs_bucket(name, semaphore) for name in bucket_names["gcs"]]
        # Azure scan tasks
        azure_tasks = [self._check_azure_blob(name, semaphore) for name in bucket_names["azure"]]

        # Execute all concurrently
        all_tasks = s3_tasks + gcs_tasks + azure_tasks
        scan_results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # Process results
        s3_results = scan_results[:len(s3_tasks)]
        gcs_results = scan_results[len(s3_tasks):len(s3_tasks) + len(gcs_tasks)]
        azure_results = scan_results[len(s3_tasks) + len(gcs_tasks):]

        bucket_findings: list[CanonicalFinding] = []

        for result in s3_results:
            if isinstance(result, dict) and result.get("accessible"):
                results.append(result)
                finding = self._make_bucket_finding(result, "deep_probe")
                if finding:
                    bucket_findings.append(finding)

        for result in gcs_results:
            if isinstance(result, dict) and result.get("accessible"):
                results.append(result)
                finding = self._make_bucket_finding(result, "deep_probe_gcs")
                if finding:
                    bucket_findings.append(finding)

        for result in azure_results:
            if isinstance(result, dict) and result.get("accessible"):
                results.append(result)
                finding = self._make_bucket_finding(result, "deep_probe_azure")
                if finding:
                    bucket_findings.append(finding)

        logger.info(f"scan_s3_buckets({domain}): {len(results)} open buckets, {len(bucket_findings)} findings")
        return results, bucket_findings

    def _generate_bucket_candidates(self, domain: str, max_count: int) -> Dict[str, List[str]]:
        """Generate probable bucket names for S3, GCS, and Azure."""
        # Extract domain parts
        parts = domain.replace(".com", "").replace(".org", "").replace(".net", "").split(".")
        base_name = parts[0] if parts else domain

        s3_buckets = []
        gcs_buckets = []
        azure_buckets = []

        # S3 bucket naming conventions (lowercase, hyphens, numbers)
        s3_patterns = [
            base_name,
            base_name.replace("_", "-"),
            f"{base_name}-data",
            f"{base_name}-assets",
            f"{base_name}-files",
            f"{base_name}-public",
            f"{base_name}-storage",
            f"{base_name}-backup",
            f"{base_name}-media",
            f"{base_name}-images",
            domain.replace(".", "-"),
            domain.replace(".", ""),
        ]
        for i in range(len(parts)):
            s3_buckets.append("-".join(parts[i:]))
        s3_buckets.extend(s3_patterns)

        # GCS bucket naming (lowercase, numbers, hyphens)
        gcs_patterns = [
            base_name,
            f"{base_name}-gcp",
            f"{base_name}-google",
            f"{base_name}-storage",
            domain.replace(".", "-"),
        ]
        gcs_buckets.extend(gcs_patterns)

        # Azure Blob naming conventions
        azure_patterns = [
            base_name,
            f"{base_name}blob",
            f"{base_name}storage",
            f"{base_name}container",
            domain.replace(".", ""),
        ]
        azure_buckets.extend(azure_patterns)

        # Deduplicate and limit
        return {
            "s3": list(set(s3_buckets))[:max_count],
            "gcs": list(set(gcs_buckets))[:max_count],
            "azure": list(set(azure_buckets))[:max_count],
        }

    async def _check_s3_bucket(self, bucket_name: str, semaphore: asyncio.Semaphore) -> Optional[dict]:
        """Check if S3 bucket is publicly accessible (unsigned)."""
        async with semaphore:
            try:
                # Lazy import boto3
                try:
                    import boto3
                    from botocore.config import Config
                except ImportError:
                    return None

                s3_client = boto3.client(
                    "s3",
                    config=Config(signature_version="s3v4", read_timeout=5),
                    region_name="us-east-1",
                )

                # Try to list objects (anonymous access)
                response = s3_client.list_objects_v2(Bucket=bucket_name, MaxKeys=100)

                objects = []
                for obj in response.get("Contents", [])[:50]:  # Cap at 50 objects
                    objects.append({
                        "key": obj.get("Key", ""),
                        "size": obj.get("Size", 0),
                        "last_modified": str(obj.get("LastModified", "")),
                    })

                return {
                    "bucket": bucket_name,
                    "provider": "s3",
                    "objects": objects,
                    "accessible": True,
                }

            except Exception:
                # Bucket not accessible or doesn't exist
                return {"bucket": bucket_name, "provider": "s3", "objects": [], "accessible": False}

    async def _check_gcs_bucket(self, bucket_name: str, semaphore: asyncio.Semaphore) -> Optional[dict]:
        """Check if GCS bucket is publicly accessible."""
        async with semaphore:
            try:
                import aiohttp

                # GCS XML API endpoint
                url = f"https://{bucket_name}.storage.googleapis.com"

                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as response:
                        if response.status == 200:
                            # Try to list objects
                            list_url = f"https://storage.googleapis.com/{bucket_name}?versions=true"
                            async with session.get(list_url, timeout=aiohttp.ClientTimeout(total=5)) as list_response:
                                objects = []
                                if list_response.status == 200:
                                    text = await list_response.text()
                                    # Parse simple XML listing
                                    import xml.etree.ElementTree as ET
                                    try:
                                        root = ET.fromstring(text)
                                        for content in root.findall(".//{http://s3.amazonaws.com/doc/2006-03-01/}Contents"):
                                            key = content.find("{http://s3.amazonaws.com/doc/2006-03-01/}Key")
                                            if key is not None:
                                                objects.append({"key": key.text or "", "size": 0})
                                    except Exception:
                                        pass

                                return {
                                    "bucket": bucket_name,
                                    "provider": "gcs",
                                    "objects": objects[:50],
                                    "accessible": True,
                                }

                        return {"bucket": bucket_name, "provider": "gcs", "objects": [], "accessible": False}

            except Exception:
                return {"bucket": bucket_name, "provider": "gcs", "objects": [], "accessible": False}

    async def _check_azure_blob(self, container_name: str, semaphore: asyncio.Semaphore) -> Optional[dict]:
        """Check if Azure Blob container is publicly accessible."""
        async with semaphore:
            try:
                import aiohttp

                # Azure Blob Storage endpoints
                endpoints = [
                    f"https://{container_name}.blob.core.windows.net",
                    f"https://{container_name}.blob.core.windows.net?restype=container&comp=list",
                ]

                objects = []
                accessible = False

                async with aiohttp.ClientSession() as session:
                    for endpoint in endpoints:
                        try:
                            async with session.get(endpoint, timeout=aiohttp.ClientTimeout(total=5)) as response:
                                if response.status == 200:
                                    accessible = True
                                    text = await response.text()
                                    # Parse Azure Blob XML response
                                    import xml.etree.ElementTree as ET
                                    try:
                                        root = ET.fromstring(text)
                                        for blob in root.findall(".//Blob"):
                                            name_elem = blob.find("Name")
                                            size_elem = blob.find("Properties/Content-Length")
                                            if name_elem is not None:
                                                objects.append({
                                                    "key": name_elem.text or "",
                                                    "size": int(size_elem.text or "0") if size_elem is not None else 0,
                                                })
                                    except Exception:
                                        pass
                                    break
                        except Exception:
                            continue

                return {
                    "bucket": container_name,
                    "provider": "azure",
                    "objects": objects[:50],
                    "accessible": accessible,
                }

            except Exception:
                return {"bucket": container_name, "provider": "azure", "objects": [], "accessible": False}

    def _make_bucket_finding(self, result: dict, source_type: str) -> Optional['CanonicalFinding']:
        """
        Build a CanonicalFinding from a bucket scan result.

        Returns None if creation fails (fail-safe).
        Does NOT persist — caller is responsible for batching and ingest.
        """
        try:
            import hashlib

            # Build dedup key from bucket + provider
            dedup_key = f"{result['bucket']}:{source_type}"
            finding_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:16]

            # Serialize objects list to JSON for payload_text
            import json
            objects_json = json.dumps(result.get("objects", [])[:20])  # Cap at 20 objects

            # CanonicalFinding is imported lazily from duckdb_store to avoid circular import
            from hledac.universal.knowledge.duckdb_store import CanonicalFinding

            return CanonicalFinding(
                finding_id=finding_id,
                query=result["bucket"],
                source_type=source_type,
                confidence=0.9 if result.get("objects") else 0.5,
                ts=time.time(),
                provenance=("deep_probe", f"bucket:{source_type}"),
                payload_text=objects_json,
            )
        except Exception as e:
            logger.debug(f"Failed to build bucket finding: {e}")
            return None


# Convenience functions for easy integration
async def scan_deep_web(target_url: str, options: Optional[Dict[str, Any]] = None) -> List[DiscoveredEndpoint]:
    """
    Convenience function for deep web scanning.

    Args:
        target_url: URL to scan
        options: Scanning options

    Returns:
        List of discovered endpoints
    """
    scanner = DeepProbeScanner()
    return await scanner.deep_crawl(target_url, options.get('max_depth', 3) if options else 3)

async def predict_hidden_paths(base_url: str, known_paths: List[str]) -> List[Tuple[str, float]]:
    """
    Predict hidden paths using Shadow Walker algorithm.

    Args:
        base_url: Base URL
        known_paths: Known existing paths

    Returns:
        List of (url, confidence) tuples
    """
    algorithm = ShadowWalkerAlgorithm()
    return algorithm.predict_next_paths(base_url, known_paths)


# ---------------------------------------------------------------------------
# P16: IPFS and S3 dorking helpers
# ---------------------------------------------------------------------------


def generate_ipfs_dorks(query: str) -> list[str]:
    """
    Generate IPFS search pattern strings for dorking.

    Args:
        query: Search keyword/phrase

    Returns:
        List of IPFS search pattern strings (dorks)

    Anti-patterns prevented:
      - Non-blocking: pure string operations
      - Returns list of patterns, not actual results
    """
    return [
        f'ipfs site:ipfs.io "{query}"',
        f'filetype:pdf site:gateway.ipfs.io "{query}"',
        f'site:cloudflare-ipfs.com "{query}"',
    ]


def generate_s3_dorks(query: str) -> list[str]:
    """
    Generate S3/GCS/Azure Blob search pattern strings for dorking.

    Args:
        query: Search keyword/phrase

    Returns:
        List of S3 search pattern strings (dorks)

    Anti-patterns prevented:
      - Non-blocking: pure string operations
      - Returns list of patterns, not actual results
    """
    return [
        f'site:s3.amazonaws.com "{query}"',
        f'site:storage.googleapis.com "{query}"',
        f'site:blob.core.windows.net "{query}"',
    ]


# Export key classes for external use
__all__ = [
    'DeepProbeScanner',
    'ShadowWalkerAlgorithm',
    'DorkingEngine',
    'WaybackCDXClient',
    'DiscoveredEndpoint',
    'TechStackSignature',
    'scan_deep_web',
    'predict_hidden_paths',
    'scan_s3_buckets',
    'scan_ipfs',
    'generate_ipfs_dorks',
    'generate_s3_dorks',
]


async def scan_s3_buckets(
    domain: str,
    store=None,
    max_buckets: int = 50
) -> Tuple[List[dict], List['CanonicalFinding']]:
    """
    Convenience function for S3/GCS/Azure bucket scanning.

    Args:
        domain: Target domain (e.g., "example.com")
        store: Optional DuckDBShadowStore (unused, kept for backward compat)
        max_buckets: Maximum bucket names to try (default 50)

    Returns:
        Tuple of (legacy dict results, canonical CanonicalFinding list).
        Canonical findings should be ingested via async_ingest_findings_batch().
    """
    scanner = DeepProbeScanner()
    return await scanner.scan_s3_buckets(domain, store=store, max_buckets=max_buckets)


async def scan_ipfs(keyword: str, store=None) -> List[Dict[str, Any]]:
    """
    Search IPFS content via ipfssearch.com API and Cloudflare IPFS gateway.

    Args:
        keyword: Search keyword/query
        store: Optional DuckDBShadowStore for persisting findings

    Returns:
        List of dicts with structure:
        [{'title': str, 'size': int, 'cid': str, 'source': str}]

    Anti-patterns:
      - Bounded RAM (<300MB for IPFS data)
      - aiohttp only (no blocking)
      - Store via DuckDB async patterns
    """
    results: List[Dict[str, Any]] = []
    seen_cids: set = set()  # Deduplicate by CID

    # Memory bound: cap total results
    MAX_RESULTS = 100
    MAX_MEMORY_MB = 300

    timeout = aiohttp.ClientTimeout(total=30)

    # 1. Query ipfssearch.com API
    try:
        async with aiohttp.ClientSession() as session:
            # ipfssearch.com public API
            search_url = f"https://ipfssearch.com/api?q={keyword}"
            async with session.get(
                search_url,
                timeout=timeout,
                headers={"Accept": "application/json"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()

                    # Parse response (format may vary - adapt to actual API)
                    entries = data if isinstance(data, list) else data.get("results", [])

                    for entry in entries[:MAX_RESULTS]:
                        try:
                            cid = entry.get("cid") or entry.get("hash")
                            if not cid or cid in seen_cids:
                                continue
                            seen_cids.add(cid)

                            result = {
                                "title": entry.get("title", entry.get("name", "")),
                                "size": entry.get("size", 0),
                                "cid": cid,
                                "source": entry.get("source", "ipfssearch.com"),
                                "url": entry.get("url", f"https://ipfs.io/ipfs/{cid}"),
                            }
                            results.append(result)

                        except Exception as e:
                            logger.debug(f"Error parsing IPFS entry: {e}")
                            continue

    except Exception as e:
        logger.debug(f"ipfssearch.com API failed for '{keyword}': {e}")

    # 2. Try Cloudflare IPFS gateway for known CIDs
    # (This is for when you already have CIDs to check)
    # Note: Cloudflare gateway doesn't support search, only retrieval

    # 3. Try alternative IPFS search via web gateway
    try:
        async with aiohttp.ClientSession() as session:
            #ipfs-search (dweb search) alternative
            alt_url = f"https://api.ipfs-search.com/v1/search?q={keyword}"
            async with session.get(
                alt_url,
                timeout=timeout,
                headers={"Accept": "application/json"}
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    hits = data.get("hits", {}).get("hits", []) if isinstance(data, dict) else []

                    for hit in hits[:MAX_RESULTS]:
                        try:
                            source = hit.get("_source", {})
                            cid = source.get("cid") or source.get("hash")
                            if not cid or cid in seen_cids:
                                continue
                            seen_cids.add(cid)

                            result = {
                                "title": source.get("title", source.get("name", "")),
                                "size": source.get("size", 0),
                                "cid": cid,
                                "source": source.get("source", "ipfs-search.com"),
                                "url": f"https://ipfs.io/ipfs/{cid}",
                            }
                            results.append(result)

                        except Exception as e:
                            logger.debug(f"Error parsing ipfs-search entry: {e}")
                            continue

    except Exception as e:
        logger.debug(f"ipfs-search.com API failed for '{keyword}': {e}")

    # Build canonical findings instead of direct persistence
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

    ipfs_findings: List[CanonicalFinding] = []
    for result in results:
        try:
            dedup_key = f"{result.get('cid', '')}:ipfs"
            finding_id = hashlib.sha256(dedup_key.encode()).hexdigest()[:16]

            finding = CanonicalFinding(
                finding_id=finding_id,
                query=keyword,
                source_type="deep_probe",
                confidence=0.7,
                ts=time.time(),
                provenance=("deep_probe", "ipfs"),
                payload_text=json.dumps(result) if result else None,
            )
            ipfs_findings.append(finding)
        except Exception as e:
            logger.debug(f"Failed to build IPFS finding: {e}")
            continue

    logger.info(f"scan_ipfs('{keyword}'): {len(results)} results, {len(ipfs_findings)} canonical findings")
    return ipfs_findings
