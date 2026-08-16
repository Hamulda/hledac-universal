"""
Universal Validation Coordinator
================================




Integrated validation coordination combining:
- Data validation (email, URL, JSON schema)
- Content cleaning (HTML to Markdown/JSON)
- Language detection
- Input sanitization

Unique Features Integrated:
1. Advanced data validation with caching
2. HTML content cleaning with MLX support
3. Multi-format output (Markdown, JSON, Text)
4. Validation severity levels
5. Custom validator support
"""
import logging
from dataclasses import field
from enum import Enum
from typing import Any

import msgspec
from compat.msgspec_gc_compat import Struct
from hledac.universal.compat.msgspec_gc_compat import Struct

from hledac.universal.utils.asyncx import parallel

from .base import UniversalCoordinator
from _core import aclose

logger = logging.getLogger(__name__)
try:
    from hledac.universal.utils.html_text_fast import html_to_text_fast
    HTML_TEXT_FAST_AVAILABLE = True
except ImportError:
    HTML_TEXT_FAST_AVAILABLE = False
    html_to_text_fast = None

def _format_markdown_lines(elems: list) -> str:
    """Format HTML elements as markdown lines."""
    lines_out: list[str] = []
    for elem in elems:
        text = elem.text(strip=True) if hasattr(elem, 'text') else elem.get_text(strip=True)
        if not text:
            continue
        tag = elem.tag if hasattr(elem, 'tag') else elem.name
        match tag:
            case 'h1':
                lines_out.append(f'# {text}')
            case 'h2':
                lines_out.append(f'## {text}')
            case 'h3':
                lines_out.append(f'### {text}')
            case 'li':
                lines_out.append(f'- {text}')
            case _:
                lines_out.append(text)
    return '\n\n'.join(lines_out)

def _extract_selectolax(html: str, output_format: str) -> dict[str, Any] | None:
    """Tier 1: selectolax extraction. Returns result dict or None on failure."""
    try:
        from selectolax.parser import HTMLParser as _SelectolaxParser
        tree = _SelectolaxParser(html)
        for tag in tree.css('script, style, nav, footer, header, aside'):
            tag.decompose()

        body = tree.body
        if body is None:
            return None

        if output_format == 'text':
            content = body.text(separator=' ', strip=True)
        elif output_format == 'markdown':
            content = _format_markdown_lines(tree.css('h1, h2, h3, p, li'))
        else:
            content = body.text(separator=' ', strip=True)

        return {
            'success': True,
            'content': content,
            'format': output_format,
            'metadata': {'method': 'selectolax'},
            'error': None,
        }
    except Exception:
        return None

def _extract_html_text_fast(html: str) -> dict[str, Any] | None:
    """Tier 2: html_text_fast extraction. Returns result dict or None on failure."""
    if not HTML_TEXT_FAST_AVAILABLE:
        return None
    try:
        content = html_to_text_fast(html)
        return {
            'success': True,
            'content': content,
            'format': 'text',
            'metadata': {'method': 'html_text_fast'},
            'error': None,
        }
    except Exception as e:
        logger.warning('html_text_fast failed, falling back to bs4: %s', e)
        return None

def _extract_selectolax(html: str, output_format: str) -> dict[str, Any] | None:
    """Tier 3: selectolax extraction (G1 FIX: replaces beautifulsoup4).

    Returns result dict or None on failure.
    """
    try:
        from selectolax.parser import HTMLParser as _Parser
        tree = _Parser(html)
        for tag in tree.css('script, style, nav, footer, header, aside'):
            tag.decompose()

        if output_format == 'text':
            body = tree.css_first('body')
            content = (body.text(separator=' ', strip=True) if body 
                      else tree.text(separator=' ', strip=True))
        elif output_format == 'markdown':
            # Extract headings and paragraphs for markdown
            elements = []
            for node in tree.css('h1, h2, h3, p, li'):
                text = node.text(strip=True)
                if text:
                    tag_name = node.tag
                    if tag_name == 'h1':
                        elements.append(f'# {text}')
                    elif tag_name == 'h2':
                        elements.append(f'## {text}')
                    elif tag_name == 'h3':
                        elements.append(f'### {text}')
                    elif tag_name == 'p':
                        elements.append(text)
                    elif tag_name == 'li':
                        elements.append(f'- {text}')
            content = '\n\n'.join(elements)
        else:
            body = tree.css_first('body')
            content = (body.text(separator=' ', strip=True) if body 
                      else tree.text(separator=' ', strip=True))

        return {
            'success': True,
            'content': content,
            'format': output_format,
            'metadata': {'method': 'selectolax'},
            'error': None,
        }
    except Exception:
        return None

def _extract_regex_fallback(html: str, output_format: str) -> dict[str, Any]:
    """Tier 4: Regex fallback extraction. Always succeeds.

    G1 FIX: This is now the final fallback instead of beautifulsoup4.
    """
    import re
    # Remove unwanted tags
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<header[^>]*>.*?</header>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<aside[^>]*>.*?</aside>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return {
        'success': True,
        'content': text,
        'format': output_format,
        'metadata': {'method': 'regex_fallback'},
        'error': None,
    }

class ValidationSeverity(Enum):
    """Validation severity levels."""
    INFO = 'info'
    WARNING = 'warning'
    ERROR = 'error'
    CRITICAL = 'critical'

class OutputFormat(Enum):
    """Content cleaning output formats."""
    MARKDOWN = 'markdown'
    JSON = 'json'
    TEXT = 'text'

class ValidationResult(Struct):
    """Result of validation operation."""
    valid: bool
    field: str
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    severity: ValidationSeverity = ValidationSeverity.INFO

class CleaningResult(Struct, frozen=True):
    """Result of content cleaning."""
    success: bool
    content: str
    format: OutputFormat
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

class UniversalValidationCoordinator(UniversalCoordinator):
    """
    Universal coordinator for validation and content cleaning.

    Integrates validation backends:
    1. DataValidator - Email, URL, JSON schema validation
    2. ContentCleaner - HTML to Markdown/JSON cleaning
    3. LanguageDetector - Text language detection

    Routing Strategy:
    - 'validate'/'check' → DataValidator
    - 'clean'/'convert'/'extract' → ContentCleaner
    - 'language'/'detect_lang' → LanguageDetector
    """
    __slots__ = ('_cleaner_available', '_cleanings_performed', '_content_cleaner', '_custom_validators', '_data_validator', '_validations_performed', '_validator_available')

    def __init__(self, max_concurrent: int=10) -> None:
        super().__init__(name='universal_validation_coordinator', max_concurrent=max_concurrent, memory_aware=True)
        self._data_validator: Any | None = None
        self._content_cleaner: Any | None = None
        self._validator_available = False
        self._cleaner_available = False
        self._validations_performed = 0
        self._cleanings_performed = 0
        self._custom_validators: dict[str, Any] = {}

    async def _do_initialize(self) -> bool:
        """Initialize validation subsystems."""
        initialized_any = False
        try:
            from hledac.universal.tools.preserved_logic.engine_core.data_validator import DataValidator
            self._data_validator = DataValidator()
            self._validator_available = True
            initialized_any = True
            logger.info('ValidationCoordinator: DataValidator initialized')
        except ImportError:
            logger.warning('ValidationCoordinator: DataValidator not available')
        except Exception as e:
            logger.warning(f'ValidationCoordinator: DataValidator init failed: {e}')
        try:
            from hledac.universal.tools.preserved_logic.content_cleaner import ContentCleaner
            self._content_cleaner = ContentCleaner()
            self._cleaner_available = True
            initialized_any = True
            logger.info('ValidationCoordinator: ContentCleaner initialized')
        except ImportError:
            logger.warning('ValidationCoordinator: ContentCleaner not available')
        except Exception as e:
            logger.warning(f'ValidationCoordinator: ContentCleaner init failed: {e}')
        return initialized_any

    async def validate_email(self, email: str, strict: bool=True) -> dict[str, Any]:
        """
        Validate email address with comprehensive checks.

        Integrated from: tools/preserved_logic/engine_core/data_validator.py

        Features:
        - RFC 5321 compliance checking
        - Pattern validation with regex
        - Domain validity verification
        - Consecutive dots detection
        - Length validation (254 char limit)

        Args:
            email: Email address to validate
            strict: Enable strict RFC compliance

        Returns:
            Validation result with details
        """
        if not self._validator_available:
            return {'valid': False, 'error': 'DataValidator not available'}
        try:
            result = self._data_validator.validate_email(email, strict=strict)
            self._validations_performed += 1
            return {'valid': result.get('valid', False), 'email': email, 'strict_mode': strict, 'error_count': result.get('error_count', 0), 'warning_count': result.get('warning_count', 0), 'errors': result.get('errors', [])}
        except Exception as e:
            logger.error(f'Email validation failed: {e}')
            return {'valid': False, 'error': str(e), 'email': email}

    async def validate_url(self, url: str, allowed_schemes: list[str] | None=None) -> dict[str, Any]:
        """
        Validate URL with scheme restrictions.

        Features:
        - Pattern validation
        - Scheme restriction checking
        - Length validation (2048 char limit)

        Args:
            url: URL to validate
            allowed_schemes: List of allowed schemes (default: ['http', 'https'])

        Returns:
            Validation result
        """
        if not self._validator_available:
            return {'valid': False, 'error': 'DataValidator not available'}
        try:
            result = self._data_validator.validate_url(url, allowed_schemes)
            self._validations_performed += 1
            return {'valid': result.get('valid', False), 'url': url, 'allowed_schemes': allowed_schemes or ['http', 'https'], 'error_count': result.get('error_count', 0), 'errors': result.get('errors', [])}
        except Exception as e:
            logger.error(f'URL validation failed: {e}')
            return {'valid': False, 'error': str(e), 'url': url}

    async def validate_json_schema(self, data: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        """
        Validate data against JSON schema.

        Features:
        - Required field validation
        - Type checking
        - String format validation (email, URI)
        - Nested object validation

        Args:
            data: Data to validate
            schema: JSON schema

        Returns:
            Validation result with detailed errors
        """
        if not self._validator_available:
            return {'valid': False, 'error': 'DataValidator not available'}
        try:
            result = self._data_validator.validate_json_schema(data, schema)
            self._validations_performed += 1
            return {'valid': result.get('valid', False), 'error_count': result.get('error_count', 0), 'critical_count': result.get('critical_count', 0), 'warning_count': result.get('warning_count', 0), 'errors': result.get('errors', []), 'timestamp': result.get('timestamp')}
        except Exception as e:
            logger.error(f'JSON schema validation failed: {e}')
            return {'valid': False, 'error': str(e)}

    async def add_custom_validator(self, name: str, validator_func: Any) -> dict[str, Any]:
        """
        Add custom validation function.

        Args:
            name: Validator name
            validator_func: Validation function

        Returns:
            Add result
        """
        if not self._validator_available:
            return {'success': False, 'error': 'DataValidator not available'}
        try:
            self._data_validator.add_custom_validator(name, validator_func)
            self._custom_validators[name] = validator_func
            return {'success': True, 'validator_name': name, 'total_validators': len(self._custom_validators)}
        except Exception as e:
            logger.error(f'Failed to add custom validator: {e}')
            return {'success': False, 'error': str(e)}

    async def clean_html(self, html: str, output_format: str='markdown', use_mlx: bool=True) -> dict[str, Any]:
        """
        Clean HTML and convert to specified format.

        Integrated from: tools/preserved_logic/content_cleaner.py

        Features:
        - HTML to Markdown conversion
        - HTML to JSON structured extraction
        - Plain text extraction
        - BeautifulSoup-based cleaning
        - Removes scripts, styles, nav, footer

        Args:
            html: Raw HTML content
            output_format: 'markdown', 'json', or 'text'
            use_mlx: Try MLX model first (if available)

        Returns:
            Cleaning result with converted content
        """
        if not self._cleaner_available:
            return await self._simple_html_extract(html, output_format)
        try:
            from hledac.universal.tools.preserved_logic.content_cleaner import OutputFormat
            fmt = OutputFormat(output_format.lower())
            result = self._content_cleaner.clean_html(html, fmt)
            self._cleanings_performed += 1
            return {'success': result.success, 'content': result.content, 'format': output_format, 'metadata': result.metadata or {}, 'error': result.error}
        except Exception as e:
            logger.error(f'HTML cleaning failed: {e}')
            return await self._simple_html_extract(html, output_format)

    async def _simple_html_extract(self, html: str, output_format: str) -> dict[str, Any]:
        """
        Simple HTML extraction fallback with tier-based approach.

        Tier 1: selectolax (fastest, Rust C backend)
        Tier 2: html_text_fast for 'text' output
        Tier 3: bs4 html.parser fallback
        Tier 4: regex ultimate fallback
        """
        # Tier 1: selectolax
        result = _extract_selectolax(html, output_format)
        if result is not None:
            return result

        # Tier 2: html_text_fast (text only)
        if output_format == 'text':
            result = _extract_html_text_fast(html)
            if result is not None:
                return result

        # Tier 3: selectolax (G1 FIX: replaces beautifulsoup4)
        result = _extract_selectolax(html, output_format)
        if result is not None:
            return result

        # Tier 4: regex fallback
        return _extract_regex_fallback(html, output_format)

    async def batch_clean_html(self, html_list: list[str], output_format: str='markdown') -> list[dict[str, Any]]:
        """
        Clean multiple HTML documents.

        Args:
            html_list: List of HTML strings
            output_format: Output format for all

        Returns:
            List of cleaning results
        """
        return await parallel(
            [self.clean_html(h, output_format) for h in html_list],
            policy="log",
            concurrency=12,
            ctx="validation_coordinator.clean_html_batch",
    )

    def get_validation_stats(self) -> dict[str, Any]:
        """Get validation statistics."""
        return {'validations_performed': self._validations_performed, 'cleanings_performed': self._cleanings_performed, 'validator_available': self._validator_available, 'cleaner_available': self._cleaner_available, 'custom_validators': len(self._custom_validators)}

    def _get_feature_list(self) -> list[str]:
        """Report available features."""
        features = ['Email validation (RFC 5321)', 'URL validation with scheme checking', 'JSON schema validation', 'Custom validator support', 'HTML to Markdown conversion', 'HTML to JSON extraction', 'Plain text extraction', 'MLX-powered cleaning (if available)', 'BeautifulSoup fallback', 'Batch processing support']
        return features
