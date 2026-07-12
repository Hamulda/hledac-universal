"""
Deep Web Hints Extractor - analyzes HTML preview for forms, API candidates, and JS markers.
NOT a crawler - only analyzes already-fetched preview HTML.

This module extracts:
- Form definitions (action, method, input fields)
- API endpoint candidates (/api/, graphql, /v1/, etc.)
- JavaScript framework markers (__NEXT_DATA__, __NUXT__, etc.)

All operations are bounded to prevent memory issues.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
import msgspec
logger = logging.getLogger(__name__)
try:
    from bs4 import BeautifulSoup, Tag
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    BeautifulSoup = None
MAX_FORMS = 10
MAX_FIELDS_PER_FORM = 10
MAX_API_CANDIDATES = 20
MAX_HTML_PREVIEW_SIZE = 50 * 1024

@dataclass(slots=True)
class DeepWebHints:
    """Structured hints extracted from HTML preview."""
    url: str
    forms: list[dict] = field(default_factory=list)
    api_candidates: list[str] = field(default_factory=list)
    js_markers: dict = field(default_factory=dict)
    hints_hash: str = ''
    onion_links: list[str] = field(default_factory=list)
    bundle_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {'url': self.url, 'forms': self.forms, 'api_candidates': self.api_candidates, 'js_markers': self.js_markers, 'hints_hash': self.hints_hash, 'onion_links': self.onion_links, 'bundle_urls': self.bundle_urls}

class DeepWebHintsExtractor:
    """
    Extracts deep web hints from HTML preview.

    Extracts:
    - Forms: action, method, input fields (names, types, placeholder values)
    - API candidates: /api/, /graphql, /v1/, /rest/ patterns
    - JS markers: __NEXT_DATA__, __NUXT__, window.__INITIAL_STATE__

    All outputs are bounded to prevent memory issues.
    """
    API_PATTERNS = ['/api/', '/api/v\\d+', '/graphql', '/rest/', '/v\\d+/', '/wp-json/wp/', '\\.json$', '/endpoints?/', '/data\\.json']
    JS_MARKERS = {'__NEXT_DATA__': '__NEXT_DATA__\\s*=\\s*', '__NUXT__': '__NUXT__\\s*=\\s*', '__INITIAL_STATE__': 'window\\.__INITIAL_STATE__', 'data-hydration': 'data-hydration', 'nuxt': 'Nuxt\\s*\\(', 'next': 'next/', 'react': 'react[@/]', 'vue': 'Vue\\(', 'svelte': 'Svelte'}
    __slots__ = tuple(('_api_patterns', '_js_pattern', 'logger'))

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._api_patterns = [re.compile(p, re.IGNORECASE) for p in self.API_PATTERNS]
        self._js_pattern = '|'.join(self.JS_MARKERS.values())

    def extract(self, url: str, html_preview: str, base_url: str | None=None) -> DeepWebHints:
        """
        Extract deep web hints from HTML preview.

        Args:
            url: Source URL
            html_preview: HTML content (will be truncated to MAX_HTML_PREVIEW_SIZE)
            base_url: Base URL for resolving relative URLs

        Returns:
            DeepWebHints with bounded extracted data
        """
        hints = DeepWebHints(url=url)
        if not html_preview:
            return hints
        html_preview = html_preview[:MAX_HTML_PREVIEW_SIZE]
        if base_url is None:
            base_url = url
        try:
            if BS4_AVAILABLE:
                soup = BeautifulSoup(html_preview, 'html.parser')
                hints.forms = self._extract_forms(soup, base_url)
            else:
                hints.forms = self._extract_forms_fallback(html_preview, base_url)
            hints.api_candidates = self._extract_api_candidates(html_preview, base_url)
            hints.js_markers = self._extract_js_markers(html_preview)
            ONION_REGEX = re.compile('[a-z0-9]{16,56}\\.onion', re.IGNORECASE)
            hints.onion_links = ONION_REGEX.findall(html_preview)[:50]
            hints.bundle_urls = self._extract_js_bundle_urls(html_preview, base_url)
        except Exception as e:
            self.logger.warning(f'Deep web hints extraction failed: {e}')
        hints.hints_hash = self._compute_hints_hash(hints)
        return hints

    def _extract_forms(self, soup: BeautifulSoup, base_url: str) -> list[dict]:
        """Extract form information using BeautifulSoup."""
        forms = []
        for form in soup.find_all('form')[:MAX_FORMS]:
            form_info = {'action': '', 'method': 'GET', 'fields': []}
            action = form.get('action', '')
            if action:
                form_info['action'] = self._resolve_url(action, base_url)
            method = form.get('method', 'GET').upper()
            form_info['method'] = method if method in ('GET', 'POST', 'PUT', 'DELETE') else 'GET'
            inputs = form.find_all(['input', 'textarea', 'select'])
            for inp in inputs[:MAX_FIELDS_PER_FORM]:
                field_info = {}
                name = inp.get('name') or inp.get('id', '')
                if name:
                    field_info['name'] = name[:100]
                inp_type = inp.get('type', 'text').lower()
                field_info['type'] = inp_type
                placeholder = inp.get('placeholder', '')
                if placeholder:
                    field_info['placeholder'] = placeholder[:50]
                value = inp.get('value', '')
                if value and inp_type not in ('submit', 'hidden', 'checkbox', 'radio'):
                    field_info['value'] = value[:50]
                if field_info.get('name'):
                    form_info['fields'].append(field_info)
            forms.append(form_info)
        return forms

    def _extract_forms_fallback(self, html: str, base_url: str) -> list[dict]:
        """Extract forms without BeautifulSoup using regex."""
        forms = []
        form_pattern = re.compile('<form[^>]*>(.*?)</form>', re.DOTALL | re.IGNORECASE)
        form_matches = form_pattern.findall(html)[:MAX_FORMS]
        for form_html in form_matches:
            form_info = {'action': '', 'method': 'GET', 'fields': []}
            action_match = re.search('action=["\\\']([^"\\\']+)["\\\']', form_html)
            if action_match:
                form_info['action'] = self._resolve_url(action_match.group(1), base_url)
            method_match = re.search('method=["\\\']([^"\\\']+)["\\\']', form_html, re.IGNORECASE)
            if method_match:
                m = method_match.group(1).upper()
                form_info['method'] = m if m in ('GET', 'POST', 'PUT', 'DELETE') else 'GET'
            input_pattern = re.compile('<(?:input|textarea|select)[^>]*>', re.IGNORECASE)
            inputs = input_pattern.findall(form_html)[:MAX_FIELDS_PER_FORM]
            for inp in inputs:
                field_info = {}
                name_match = re.search('name=["\\\']([^"\\\']*)["\\\']', inp)
                if name_match and name_match.group(1):
                    field_info['name'] = name_match.group(1)[:100]
                type_match = re.search('type=["\\\']([^"\\\']*)["\\\']', inp, re.IGNORECASE)
                field_info['type'] = type_match.group(1).lower() if type_match else 'text'
                placeholder_match = re.search('placeholder=["\\\']([^"\\\']*)["\\\']', inp)
                if placeholder_match:
                    field_info['placeholder'] = placeholder_match.group(1)[:50]
                if field_info.get('name'):
                    form_info['fields'].append(field_info)
            forms.append(form_info)
        return forms

    def _extract_api_candidates(self, html: str, base_url: str) -> list[str]:
        """Extract API endpoint candidates from HTML."""
        candidates = set()
        for pattern in self._api_patterns:
            matches = pattern.findall(html)
            for match in matches:
                api_url = self._resolve_url(match.strip(), base_url)
                candidates.add(api_url)
                if len(candidates) >= MAX_API_CANDIDATES:
                    break
        script_patterns = ['fetch\\s*\\(\\s*[\'\\"]([^\'\\"]+)[\'\\"]', 'axios\\.\\w+\\s*\\(\\s*[\'\\"]([^\'\\"]+)[\'\\"]', '\\.get\\s*\\(\\s*[\'\\"]([^\'\\"]+)[\'\\"]', '\\.post\\s*\\(\\s*[\'\\"]([^\'\\"]+)[\'\\"]', 'window\\.fetch\\s*\\(\\s*[\'\\"]([^\'\\"]+)[\'\\"]']
        for pattern_str in script_patterns:
            pattern = re.compile(pattern_str, re.IGNORECASE)
            matches = pattern.findall(html)
            for match in matches:
                if '/api/' in match or '/graphql' in match or '/v' in match:
                    api_url = self._resolve_url(match.strip(), base_url)
                    candidates.add(api_url)
                    if len(candidates) >= MAX_API_CANDIDATES:
                        break
        return list(candidates)[:MAX_API_CANDIDATES]

    def _extract_js_markers(self, html: str) -> dict:
        """Extract JavaScript framework markers."""
        markers = {}
        for name, pattern in self.JS_MARKERS.items():
            try:
                if re.search(pattern, html, re.IGNORECASE):
                    markers[name] = True
            except re.error:
                pass
        if '__NEXT_DATA__' in html or 'next' in html.lower():
            markers['next_data'] = True
        if '__NUXT__' in html or 'nuxt' in html.lower():
            markers['nuxt'] = True
        return markers

    def _resolve_url(self, url: str, base_url: str) -> str:
        """Resolve relative URL against base URL."""
        if not url:
            return ''
        if url.startswith(('http://', 'https://', '//')):
            return url
        if url.startswith('/'):
            parsed = re.match('(https?://[^/]+)', base_url)
            if parsed:
                return parsed.group(1) + url
        from urllib.parse import urljoin
        return urljoin(base_url, url)

    def _extract_js_bundle_urls(self, html: str, base_url: str) -> list[str]:
        """Extract external .js bundle URLs from HTML (sync, no HTTP)."""
        from urllib.parse import urljoin
        pattern = re.compile('<script[^>]+src=["\\\']([^"\\\']*\\.js)["\\\']', re.IGNORECASE)
        urls = []
        for match in pattern.findall(html)[:10]:
            if match.startswith('/'):
                urls.append(urljoin(base_url, match))
            elif match.startswith(('http://', 'https://')):
                urls.append(match)
            else:
                urls.append(urljoin(base_url, match))
        return urls

    def _compute_hints_hash(self, hints: DeepWebHints) -> str:
        """Compute stable hash for hints."""
        stable = {'forms': [{'a': f.get('action', ''), 'm': f.get('method', ''), 'n': len(f.get('fields', []))} for f in hints.forms], 'api': sorted(hints.api_candidates)[:10], 'js': sorted(hints.js_markers.keys())}
        json_str = json.dumps(stable, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()[:16]

def extract_deepweb_hints(url: str, html_preview: str, base_url: str | None=None) -> DeepWebHints:
    """
    Convenience function to extract deep web hints.

    Args:
        url: Source URL
        html_preview: HTML content to analyze
        base_url: Base URL for resolving relative URLs

    Returns:
        DeepWebHints with bounded extracted data
    """
    extractor = DeepWebHintsExtractor()
    return extractor.extract(url, html_preview, base_url)