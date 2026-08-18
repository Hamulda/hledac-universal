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
import asyncio
import logging
from dataclasses import field
from enum import Enum
from typing import Any

import msgspec
from compat.msgspec_gc_compat import Struct

from hledac.universal.utils.asyncx import parallel

from .base import UniversalCoordinator
from _core import aclose

logger = logging.getLogger(__name__)

# M1 8GB Safety: 5MB HTML input cap (matches Rust MAX_HTML_INPUT_SIZE)
_MAX_HTML_INPUT_SIZE: int = 5 * 1024 * 1024

# Rust html_parse availability (lol_html with 5MB cap, GIL release, ~5× faster)
# Architecture: via _core.rust_backend facade (R6 pattern)
_RUST_HTML_PARSE_AVAILABLE = False
_rust_html_parse = None  # Access via rust.raw.extract_html_text
try:
    from hledac.universal._core.rust_backend import rust

    # Check if extract_html_text is available in raw module
    if hasattr(rust, 'raw') and hasattr(rust.raw, 'extract_html_text'):
        _rust_html_parse = rust.raw
        _RUST_HTML_PARSE_AVAILABLE = True
    else:
        _rust_html_parse = None
except ImportError:
    _rust_html_parse = None  # type: ignore[assignment]

# html_text_fast availability
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


def _extract_html_parse(html: str, output_format: str) -> dict[str, Any] | None:
    """
    Tier 0: Rust lol_html extraction via html_parse module.

    Zero-allocation HTML→text via lol_html streaming parser.
    - 5MB input cap enforced (matches Rust MAX_HTML_INPUT_SIZE)
    - GIL release during parsing (rayon parallelism)
    - ~5× faster than selectolax for text extraction
    - Only supports 'text' output; 'markdown' falls through to selectolax

    Returns result dict or None on failure.
    """
    if not _RUST_HTML_PARSE_AVAILABLE or _rust_html_parse is None:
        return None

    # M1 8GB safety: enforce 5MB input cap before Rust call
    if len(html) > _MAX_HTML_INPUT_SIZE:
        html = html[:_MAX_HTML_INPUT_SIZE]

    # lol_html extracts plain text only; for 'markdown' format
    # we fall through to selectolax which provides better structure
    if output_format == 'markdown':
        return None

    try:
        # Use rust.raw.extract_html_text (R6 facade pattern)
        content = _rust_html_parse.extract_html_text(html)
        if not content:
            return None

        return {
            'success': True,
            'content': content,
            'format': 'text',
            'metadata': {
                'method': 'rust_html_parse',
                'input_size_bytes': len(html),
                'parser': 'lol_html',
            },
            'error': None,
        }
    except Exception as e:
        logger.debug('rust_html_parse extraction failed: %s', e)
        return None


def _extract_selectolax(html: str, output_format: str) -> dict[str, Any] | None:
    """
    Tier 2: selectolax extraction (Cython, M1-friendly).

    Supports 'text', 'markdown', and 'json' output formats.
    Returns result dict or None on failure.
    """
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
    """
    Tier 1: html_text_fast extraction (C extension, text-only).

    Fast text extraction via html_text_fast C extension.
    Only supports 'text' output; falls through to selectolax for other formats.
    Returns result dict or None on failure.
    """
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
        logger.warning('html_text_fast failed, falling back to selectolax: %s', e)
        return None


def _extract_regex_fallback(html: str, output_format: str) -> dict[str, Any]:
    """
    Tier 3: Regex fallback extraction (stdlib, always succeeds).

    Ultimate fallback when all other tiers fail.
    Uses stdlib re for maximum compatibility.
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

        Tier 0: Rust lol_html (5MB cap, GIL release, ~5× faster, text-only)
        Tier 1: html_text_fast (C extension, text-only, fast)
        Tier 2: selectolax (Cython, M1-friendly, supports text/markdown/json)
        Tier 3: regex fallback (stdlib, ultimate compatibility)
        """
        # Tier 0: Rust lol_html (zero-allocation text extraction)
        result = _extract_html_parse(html, output_format)
        if result is not None:
            return result

        # Tier 1: html_text_fast (text only)
        if output_format == 'text':
            result = _extract_html_text_fast(html)
            if result is not None:
                return result

        # Tier 2: selectolax (supports text, markdown, json)
        result = _extract_selectolax(html, output_format)
        if result is not None:
            return result

        # Tier 3: regex fallback (ultimate, always succeeds)
        return _extract_regex_fallback(html, output_format)

    async def batch_clean_html(self, html_list: list[str], output_format: str='markdown') -> list[dict[str, Any]]:
        """
        Clean multiple HTML documents with batch optimization.

        For 'text' output with Rust available:
        - Uses rust.raw.batch_extract_html_text (rayon parallel, GIL release)
        - Falls back to parallel() for 'markdown' or when Rust unavailable

        Args:
            html_list: List of HTML strings
            output_format: Output format for all

        Returns:
            List of cleaning results
        """
        # Optimized path for 'text' output with Rust batch extraction
        if output_format == 'text' and _RUST_HTML_PARSE_AVAILABLE and _rust_html_parse is not None:
            return await self._batch_clean_html_rust(html_list)

        # Fallback: parallel async processing
        return await parallel(
            [self.clean_html(h, output_format) for h in html_list],
            policy="log",
            concurrency=12,
            ctx="validation_coordinator.clean_html_batch",
        )

    async def _batch_clean_html_rust(self, html_list: list[str]) -> list[dict[str, Any]]:
        """
        Batch HTML→text via Rust rayon parallel processing.

        Uses rust.raw.batch_extract_html_text for GIL-free parallelism.
        One rayon call instead of N sequential calls — 3-5× speedup.
        """
        loop = asyncio.get_running_loop()
        try:
            texts: list[str] = await loop.run_in_executor(
                None,  # Use default executor (ThreadPool for rayon)
                lambda: _rust_html_parse.batch_extract_html_text(html_list),
            )
        except Exception as e:
            logger.warning('Rust batch_extract_html_text failed: %s, falling back to parallel', e)
            return await parallel(
                [self._simple_html_extract(h, 'text') for h in html_list],
                policy="log",
                concurrency=12,
                ctx="validation_coordinator.clean_html_batch_fallback",
            )

        return [
            {
                'success': bool(text),
                'content': text,
                'format': 'text',
                'metadata': {'method': 'rust_html_parse_batch'},
                'error': None,
            }
            for text in texts
        ]

    def get_validation_stats(self) -> dict[str, Any]:
        """Get validation statistics."""
        return {'validations_performed': self._validations_performed, 'cleanings_performed': self._cleanings_performed, 'validator_available': self._validator_available, 'cleaner_available': self._cleaner_available, 'custom_validators': len(self._custom_validators)}

    def _get_feature_list(self) -> list[str]:
        """Report available features."""
        features = ['Email validation (RFC 5321)', 'URL validation with scheme checking', 'JSON schema validation', 'Custom validator support', 'HTML to Markdown conversion', 'HTML to JSON extraction', 'Plain text extraction', 'MLX-powered cleaning (if available)', 'BeautifulSoup fallback', 'Batch processing support']
        return features
