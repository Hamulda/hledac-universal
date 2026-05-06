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

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from .base import (
    UniversalCoordinator,
    OperationType,
    DecisionResponse,
    OperationResult,
    MemoryPressureLevel
)

logger = logging.getLogger(__name__)

# F214OPT-A: selectolax-first HTML→text
try:
    from hledac.universal.utils.html_text_fast import html_to_text_fast

    HTML_TEXT_FAST_AVAILABLE = True
except ImportError:
    HTML_TEXT_FAST_AVAILABLE = False
    html_to_text_fast = None  # type: ignore[assignment]


class ValidationSeverity(Enum):
    """Validation severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class OutputFormat(Enum):
    """Content cleaning output formats."""
    MARKDOWN = "markdown"
    JSON = "json"
    TEXT = "text"


@dataclass
class ValidationResult:
    """Result of validation operation."""
    valid: bool
    field: str
    errors: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    severity: ValidationSeverity = ValidationSeverity.INFO


@dataclass
class CleaningResult:
    """Result of content cleaning."""
    success: bool
    content: str
    format: OutputFormat
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


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

    def __init__(self, max_concurrent: int = 10):
        super().__init__(
            name="universal_validation_coordinator",
            max_concurrent=max_concurrent,
            memory_aware=True
        )
        
        # Validation subsystems (lazy initialization)
        self._data_validator: Optional[Any] = None
        self._content_cleaner: Optional[Any] = None
        
        # Availability flags
        self._validator_available = False
        self._cleaner_available = False
        
        # Statistics
        self._validations_performed = 0
        self._cleanings_performed = 0
        self._custom_validators: Dict[str, Any] = {}

    # ========================================================================
    # Initialization
    # ========================================================================

    async def _do_initialize(self) -> bool:
        """Initialize validation subsystems."""
        initialized_any = False
        
        # Try DataValidator
        try:
            from hledac.tools.preserved_logic.engine_core.data_validator import DataValidator
            self._data_validator = DataValidator()
            self._validator_available = True
            initialized_any = True
            logger.info("ValidationCoordinator: DataValidator initialized")
        except ImportError:
            logger.warning("ValidationCoordinator: DataValidator not available")
        except Exception as e:
            logger.warning(f"ValidationCoordinator: DataValidator init failed: {e}")
        
        # Try ContentCleaner
        try:
            from hledac.tools.preserved_logic.content_cleaner import ContentCleaner
            self._content_cleaner = ContentCleaner()
            self._cleaner_available = True
            initialized_any = True
            logger.info("ValidationCoordinator: ContentCleaner initialized")
        except ImportError:
            logger.warning("ValidationCoordinator: ContentCleaner not available")
        except Exception as e:
            logger.warning(f"ValidationCoordinator: ContentCleaner init failed: {e}")
        
        return initialized_any

    # ========================================================================
    # Data Validation Operations
    # ========================================================================

    async def validate_email(
        self,
        email: str,
        strict: bool = True
    ) -> Dict[str, Any]:
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
            
            return {
                'valid': result.get('valid', False),
                'email': email,
                'strict_mode': strict,
                'error_count': result.get('error_count', 0),
                'warning_count': result.get('warning_count', 0),
                'errors': result.get('errors', [])
            }
            
        except Exception as e:
            logger.error(f"Email validation failed: {e}")
            return {'valid': False, 'error': str(e), 'email': email}

    async def validate_url(
        self,
        url: str,
        allowed_schemes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
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
            
            return {
                'valid': result.get('valid', False),
                'url': url,
                'allowed_schemes': allowed_schemes or ['http', 'https'],
                'error_count': result.get('error_count', 0),
                'errors': result.get('errors', [])
            }
            
        except Exception as e:
            logger.error(f"URL validation failed: {e}")
            return {'valid': False, 'error': str(e), 'url': url}

    async def validate_json_schema(
        self,
        data: Dict[str, Any],
        schema: Dict[str, Any]
    ) -> Dict[str, Any]:
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
            
            return {
                'valid': result.get('valid', False),
                'error_count': result.get('error_count', 0),
                'critical_count': result.get('critical_count', 0),
                'warning_count': result.get('warning_count', 0),
                'errors': result.get('errors', []),
                'timestamp': result.get('timestamp')
            }
            
        except Exception as e:
            logger.error(f"JSON schema validation failed: {e}")
            return {'valid': False, 'error': str(e)}

    async def add_custom_validator(
        self,
        name: str,
        validator_func: Any
    ) -> Dict[str, Any]:
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
            
            return {
                'success': True,
                'validator_name': name,
                'total_validators': len(self._custom_validators)
            }
            
        except Exception as e:
            logger.error(f"Failed to add custom validator: {e}")
            return {'success': False, 'error': str(e)}

    # ========================================================================
    # Content Cleaning Operations
    # ========================================================================

    async def clean_html(
        self,
        html: str,
        output_format: str = "markdown",
        use_mlx: bool = True
    ) -> Dict[str, Any]:
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
            # Fallback to simple extraction
            return await self._simple_html_extract(html, output_format)
        
        try:
            from hledac.tools.preserved_logic.content_cleaner import OutputFormat
            
            fmt = OutputFormat(output_format.lower())
            result = self._content_cleaner.clean_html(html, fmt)
            self._cleanings_performed += 1
            
            return {
                'success': result.success,
                'content': result.content,
                'format': output_format,
                'metadata': result.metadata or {},
                'error': result.error
            }
            
        except Exception as e:
            logger.error(f"HTML cleaning failed: {e}")
            return await self._simple_html_extract(html, output_format)

    async def _simple_html_extract(
        self,
        html: str,
        output_format: str
    ) -> Dict[str, Any]:
        """
        Simple HTML extraction fallback.

        F214OPT-A notes:
        - 'text' output: uses html_to_text_fast (selectolax-first, faster)
        - 'markdown' output: keeps BeautifulSoup for DOM-specific element traversal
          (heading/list structure) — selectolax text() doesn't preserve structure
        """
        # F214OPT-A: selectolax-first for plain text
        if output_format == 'text' and HTML_TEXT_FAST_AVAILABLE:
            try:
                content = html_to_text_fast(html)  # type: ignore[operator]
                return {
                    'success': True,
                    'content': content,
                    'format': output_format,
                    'metadata': {'method': 'html_text_fast'},
                    'error': None
                }
            except Exception as e:
                logger.warning("html_to_text_fast failed, falling back to BeautifulSoup: %s", e)
                # fall through to BeautifulSoup path

        try:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, 'html.parser')

            # Remove unwanted elements
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                tag.decompose()

            if output_format == 'text':
                content = soup.get_text(separator=' ', strip=True)
            elif output_format == 'markdown':
                # Simple markdown conversion
                lines_out = []
                for elem in soup.find_all(['h1', 'h2', 'h3', 'p', 'li']):
                    text = elem.get_text(strip=True)
                    if elem.name == 'h1':
                        lines_out.append(f'# {text}')
                    elif elem.name == 'h2':
                        lines_out.append(f'## {text}')
                    elif elem.name == 'h3':
                        lines_out.append(f'### {text}')
                    elif elem.name == 'li':
                        lines_out.append(f'- {text}')
                    else:
                        lines_out.append(text)
                content = '\n\n'.join(lines_out)
            else:
                content = soup.get_text(separator=' ', strip=True)

            return {
                'success': True,
                'content': content,
                'format': output_format,
                'metadata': {'method': 'beautifulsoup_fallback'},
                'error': None
            }

        except ImportError:
            # Ultimate fallback
            import re
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()

            return {
                'success': True,
                'content': text,
                'format': output_format,
                'metadata': {'method': 'regex_fallback'},
                'error': None
            }

    async def batch_clean_html(
        self,
        html_list: List[str],
        output_format: str = "markdown"
    ) -> List[Dict[str, Any]]:
        """
        Clean multiple HTML documents.
        
        Args:
            html_list: List of HTML strings
            output_format: Output format for all
            
        Returns:
            List of cleaning results
        """
        results = []
        for html in html_list:
            result = await self.clean_html(html, output_format)
            results.append(result)
        return results

    # ========================================================================
    # Statistics
    # ========================================================================

    def get_validation_stats(self) -> Dict[str, Any]:
        """Get validation statistics."""
        return {
            'validations_performed': self._validations_performed,
            'cleanings_performed': self._cleanings_performed,
            'validator_available': self._validator_available,
            'cleaner_available': self._cleaner_available,
            'custom_validators': len(self._custom_validators)
        }

    def _get_feature_list(self) -> List[str]:
        """Report available features."""
        features = [
            "Email validation (RFC 5321)",
            "URL validation with scheme checking",
            "JSON schema validation",
            "Custom validator support",
            "HTML to Markdown conversion",
            "HTML to JSON extraction",
            "Plain text extraction",
            "MLX-powered cleaning (if available)",
            "BeautifulSoup fallback",
            "Batch processing support"
        ]
        return features
