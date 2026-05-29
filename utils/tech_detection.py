"""
Technology Stack Detection - Framework & CMS Identification
===========================================================

Integrated from hledac/scanners/deep_probe.py

Detects technology stacks from URLs and content signatures.
Useful for understanding the underlying technology of discovered endpoints.

M1-Optimized: Minimal dependencies, fast signature matching
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TechStackResult:
    """Result of technology stack detection."""
    framework: str | None
    confidence: float
    indicators: list[str]
    version: str | None = None
    additional_tech: list[str] = None

    def __post_init__(self):
        if self.additional_tech is None:
            self.additional_tech = []


class TechStackSignature:
    """
    Technology stack signature detection for discovered endpoints.

    Detects frameworks and CMS from URL patterns and content indicators.

    Supported frameworks:
    - CMS: WordPress, Drupal, Joomla
    - Python: Django, Flask
    - Node.js: Express
    - Ruby: Rails
    - PHP: Laravel
    - Java: Spring
    - .NET: ASP.NET

    Example:
        >>> detector = TechStackSignature()
        >>> result = detector.detect_stack('https://example.com/wp-content/themes/')
        >>> print(result.framework)
        'wordpress'
    """

    def __init__(self):
        # Main framework signatures
        self.signatures = {
            'wordpress': {
                'indicators': ['wp-content', 'wp-admin', 'wp-json', 'wp-includes'],
                'weight': 1.0,
            },
            'drupal': {
                'indicators': ['node/', 'drupal.js', 'sites/default', 'modules/'],
                'weight': 1.0,
            },
            'joomla': {
                'indicators': ['administrator/', 'components/', 'modules/', 'templates/'],
                'weight': 1.0,
            },
            'django': {
                'indicators': ['admin/', 'static/admin', 'django', '__debug__/'],
                'weight': 1.0,
            },
            'flask': {
                'indicators': ['static/', 'api/', 'swagger', 'flask'],
                'weight': 0.9,
            },
            'express': {
                'indicators': ['api/', 'swagger', 'node_modules', 'express'],
                'weight': 0.9,
            },
            'rails': {
                'indicators': ['assets/', 'rails', 'application.js', 'ruby'],
                'weight': 0.9,
            },
            'laravel': {
                'indicators': ['vendor/', 'artisan', 'storage/', 'laravel'],
                'weight': 0.9,
            },
            'spring': {
                'indicators': ['actuator/', 'swagger-ui', 'WEB-INF', 'spring'],
                'weight': 0.9,
            },
            'aspnet': {
                'indicators': ['WebResource.axd', 'ScriptResource.axd', 'App_Data', 'asp.net'],
                'weight': 0.9,
            },
            'nextjs': {
                'indicators': ['_next/', '__next', 'next.js', 'next/'],
                'weight': 0.9,
            },
            'react': {
                'indicators': ['react', 'reactjs', 'jsx', 'create-react-app'],
                'weight': 0.8,
            },
            'vue': {
                'indicators': ['vue', 'vuejs', 'vuetify', 'vue-router'],
                'weight': 0.8,
            },
            'angular': {
                'indicators': ['angular', 'ng-', '@angular'],
                'weight': 0.8,
            },
        }

        # Version detection patterns
        self.version_patterns = {
            'wordpress': [
                r'wp-includes/js/wp-emoji-release\.min\.js\?ver=([0-9.]+)',
                r'wp-content/themes/[^/]+/style\.css\?ver=([0-9.]+)',
            ],
            'drupal': [
                r'Drupal ([0-9.]+)',
                r'core/misc/drupal\.js\?v=([0-9.]+)',
            ],
            'django': [
                r'Django/([0-9.]+)',
            ],
            'rails': [
                r'Rails/([0-9.]+)',
                r'rails-([0-9.]+)',
            ],
        }

    def detect_stack(
        self,
        url: str,
        content: str | None = None,
        headers: dict[str, str] | None = None
    ) -> TechStackResult:
        """
        Detect technology stack from URL and content.

        Args:
            url: URL to analyze
            content: Optional page content
            headers: Optional HTTP response headers

        Returns:
            TechStackResult with detection info
        """
        url_lower = url.lower()
        detected_framework = None
        max_confidence = 0.0
        all_indicators = []
        version = None
        additional_tech = []

        for framework, config in self.signatures.items():
            matches = 0
            found_indicators = []

            # Check URL indicators
            for indicator in config['indicators']:
                if indicator.lower() in url_lower:
                    matches += 1
                    found_indicators.append(f"url:{indicator}")

            # Check content indicators (weight more)
            if content:
                content_lower = content.lower()
                for indicator in config['indicators']:
                    if indicator.lower() in content_lower:
                        matches += 2  # Content matches weigh more
                        found_indicators.append(f"content:{indicator}")

            # Check headers
            if headers:
                for header_name, header_value in headers.items():
                    header_str = f"{header_name}: {header_value}".lower()
                    for indicator in config['indicators']:
                        if indicator.lower() in header_str:
                            matches += 1
                            found_indicators.append(f"header:{indicator}")

            # Calculate confidence
            if matches > 0:
                base_confidence = matches / len(config['indicators'])
                confidence = min(base_confidence * config['weight'], 1.0)

                if confidence > max_confidence:
                    max_confidence = confidence
                    detected_framework = framework
                    all_indicators = list(set(found_indicators))

        # Detect version if possible
        if detected_framework and content:
            version = self._detect_version(detected_framework, content)

        # Detect additional technologies
        additional_tech = self._detect_additional_tech(url_lower, content)

        return TechStackResult(
            framework=detected_framework,
            confidence=max_confidence,
            indicators=all_indicators,
            version=version,
            additional_tech=additional_tech
        )

    def _detect_version(self, framework: str, content: str) -> str | None:
        """Detect framework version from content."""
        import re

        if framework not in self.version_patterns:
            return None

        for pattern in self.version_patterns[framework]:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return match.group(1)

        return None

    def _detect_additional_tech(
        self,
        url_lower: str,
        content: str | None
    ) -> list[str]:
        """Detect additional technologies."""
        additional = []

        # CDN detection
        cdns = {
            'cloudflare': ['cloudflare', 'cdnjs.cloudflare'],
            'aws': ['aws.amazon', 's3.amazonaws'],
            'google': ['googleapis', 'ajax.googleapis'],
            'jquery': ['jquery'],
            'bootstrap': ['bootstrap'],
        }

        content_lower = content.lower() if content else ''
        combined = url_lower + ' ' + content_lower

        for tech, indicators in cdns.items():
            for indicator in indicators:
                if indicator in combined:
                    additional.append(tech)
                    break

        return list(set(additional))

    def get_framework_info(self, framework: str) -> dict[str, Any]:
        """
        Get information about a framework.

        Args:
            framework: Framework name

        Returns:
            Dictionary with framework info
        """
        if framework not in self.signatures:
            return {}

        return {
            'name': framework,
            'indicators': self.signatures[framework]['indicators'],
            'weight': self.signatures[framework]['weight'],
        }

    def add_signature(
        self,
        framework: str,
        indicators: list[str],
        weight: float = 1.0
    ) -> None:
        """
        Add custom framework signature.

        Args:
            framework: Framework name
            indicators: List of URL/content indicators
            weight: Detection weight (0.0-1.0)
        """
        self.signatures[framework] = {
            'indicators': indicators,
            'weight': weight,
        }


# Convenience function
def detect_tech_stack(
    url: str,
    content: str | None = None,
    headers: dict[str, str] | None = None
) -> TechStackResult:
    """
    Quick technology stack detection.

    Args:
        url: URL to analyze
        content: Optional page content
        headers: Optional HTTP headers

    Returns:
        TechStackResult with detection info
    """
    detector = TechStackSignature()
    return detector.detect_stack(url, content, headers)


__all__ = [
    'TechStackResult',
    'TechStackSignature',
    'detect_tech_stack',
]
