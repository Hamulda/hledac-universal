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
                'site:{domain} inurl:publications filetype:pdf'
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
        raise NotImplementedError

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
        to_visit = [(base_url, 0)]  # (url, depth)

        while to_visit and len(visited) < 1000:  # Safety limit
            current_url, depth = to_visit.pop(0)

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

# Export key classes for external use
__all__ = [
    'DeepProbeScanner',
    'ShadowWalkerAlgorithm',
    'DorkingEngine',
    'WaybackCDXClient',
    'DiscoveredEndpoint',
    'TechStackSignature',
    'scan_deep_web',
    'predict_hidden_paths'
]
