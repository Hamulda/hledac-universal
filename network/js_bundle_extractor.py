from __future__ import annotations

"""JS Bundle AST extractor – finds API endpoints in external JS files."""
import logging
import re

logger = logging.getLogger(__name__)


class _JSBundleExtractor:
    """Extract API endpoints from JavaScript bundle content."""

    # Common API call patterns
    FETCH_PATTERN = re.compile(r'fetch\(["\']([^"\']+)["\']', re.IGNORECASE)
    XHR_PATTERN = re.compile(r'XMLHttpRequest\([^)]*\)[^;]*\.open\(["\'][^"\']*["\'],\s*["\']([^"\']+)["\']', re.IGNORECASE)
    AXIOS_PATTERN = re.compile(r'axios\.(?:get|post|put|delete)\(["\']([^"\']+)["\']', re.IGNORECASE)
    AXIOS_INSTANCE_PATTERN = re.compile(r'\.(?:get|post|put|delete)\(["\']([^"\']+)["\']', re.IGNORECASE)

    # Relative URL patterns
    RELATIVE_PATTERN = re.compile(r'["\'](/[^"\']*)["\']')

    def extract_from_js(self, js_content: str, base_url: str = "") -> list[str]:
        """
        Extract API endpoints from JS content.
        Returns list of unique, normalized endpoints.
        """
        endpoints: set[str] = set()

        # Fetch calls
        for match in self.FETCH_PATTERN.findall(js_content):
            self._add_endpoint(endpoints, match, base_url)

        # XHR open calls
        for match in self.XHR_PATTERN.findall(js_content):
            self._add_endpoint(endpoints, match, base_url)

        # Axios calls
        for match in self.AXIOS_PATTERN.findall(js_content):
            self._add_endpoint(endpoints, match, base_url)

        # Axios instance calls (variable.method)
        for match in self.AXIOS_INSTANCE_PATTERN.findall(js_content):
            self._add_endpoint(endpoints, match, base_url)

        # Relative paths that look like API endpoints
        for match in self.RELATIVE_PATTERN.findall(js_content):
            if '/api/' in match or '/v1/' in match or '/graphql' in match:
                self._add_endpoint(endpoints, match, base_url)

        # Return bounded list
        return list(endpoints)[:50]

    def _add_endpoint(self, endpoints: set[str], path: str, base_url: str) -> None:
        """Normalize and add endpoint if it looks plausible."""
        path = path.strip()
        if not path or len(path) < 3:
            return

        # Skip obvious false positives
        if path.startswith(('javascript:', 'data:', 'blob:')):
            return
        if path in ('/', '#', 'about:blank'):
            return

        # Convert relative to absolute if base_url provided
        if base_url and path.startswith('/'):
            from urllib.parse import urljoin
            full = urljoin(base_url, path)
            endpoints.add(full)
        else:
            endpoints.add(path)
