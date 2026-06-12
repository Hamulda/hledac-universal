#!/usr/bin/env python3
"""

from __future__ import annotations
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

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

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
