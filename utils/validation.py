"""
Data validation utilities for the Hledac AI Research Platform.

This module provides robust data validation functions with comprehensive
error handling, type safety, and performance optimization for M1 systems.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Generic type variables for better type safety
T = TypeVar('T')
ValidationResult = dict[str, bool | str | list[str]]


class ValidationSeverity(Enum):
    """Enumeration of validation severity levels."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ValidationError:
    """Structured validation error information."""
    field: str
    message: str
    severity: ValidationSeverity
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'field': self.field,
            'message': self.message,
            'severity': self.severity.value,
            'timestamp': self.timestamp.isoformat()
        }


class DataValidator:
    """
    High-performance data validator with configurable rules and caching.

    Features:
    - Type-safe validation with comprehensive error reporting
    - Performance optimized for M1 systems with memoization
    - Extensible rule system with custom validators
    - Structured error reporting with severity levels
    - JSON schema compliance checking
    """

    def __init__(self, cache_size: int = 1000):
        """
        Initialize the validator with configurable cache size.

        Args:
            cache_size: Maximum number of validation results to cache
        """
        self._cache: dict[str, ValidationResult] = {}
        self._cache_size = cache_size
        self._custom_validators: dict[str, callable] = {}

        # Pre-compiled regex patterns for performance
        self._email_pattern = re.compile(
            r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        )
        self._url_pattern = re.compile(
            r'^https?://(?:[-\w.])+(?:[:\d]+)?(?:/(?:[\w/_.])*(?:\?(?:[\w&=%.])*)?(?:#(?:\w*))?)?$'
        )

    def validate_email(self, email: str, strict: bool = True) -> ValidationResult:
        """
        Validate email address with configurable strictness.

        Args:
            email: Email address to validate
            strict: Enable strict RFC compliance checking

        Returns:
            Validation result dictionary with success status and details
        """
        cache_key = f"email_{email}_{strict}"

        # Check cache first for performance
        if cache_key in self._cache:
                    return self._cache[cache_key]

        errors: list[ValidationError] = []

        # Basic structure validation
        if not email or not isinstance(email, str):
                errors.append(ValidationError(
                field="email",
                message="Email must be a non-empty string",
                severity=ValidationSeverity.ERROR,
                timestamp=datetime.now()
            ))
                return self._create_result(False, errors, cache_key)

        # Length validation
        if len(email) > 254:  # RFC 5321 limit
            errors.append(ValidationError(
                field="email",
                message="Email address exceeds maximum length of 254 characters",
                severity=ValidationSeverity.ERROR,
                timestamp=datetime.now()
            ))

        # Pattern validation
        if not self._email_pattern.match(email):
                errors.append(ValidationError(
                field="email",
                message="Email address format is invalid",
                severity=ValidationSeverity.ERROR,
                timestamp=datetime.now()
            ))

        # Additional strict validation
        if strict:
            # Check for consecutive dots
            if '..' in email:
                    errors.append(ValidationError(
                    field="email",
                    message="Email cannot contain consecutive dots",
                    severity=ValidationSeverity.WARNING,
                    timestamp=datetime.now()
                ))

            # Check domain validity
            domain = email.split('@')[-1] if '@' in email else ''
            if not domain or len(domain) < 4:
                    errors.append(ValidationError(
                    field="email",
                    message="Email domain is invalid or too short",
                    severity=ValidationSeverity.ERROR,
                    timestamp=datetime.now()
                ))

        success = len([e for e in errors if e.severity == ValidationSeverity.ERROR]) == 0
        return self._create_result(success, errors, cache_key)

    def validate_url(self, url: str, allowed_schemes: list[str] | None = None) -> ValidationResult:
        """
        Validate URL with configurable scheme restrictions.

        Args:
            url: URL to validate
            allowed_schemes: List of allowed URL schemes (default: ['http', 'https'])

        Returns:
            Validation result dictionary with success status and details
        """
        if allowed_schemes is None:
                allowed_schemes = ['http', 'https']

        cache_key = f"url_{url}_{hash(tuple(allowed_schemes))}"

        if cache_key in self._cache:
                    return self._cache[cache_key]

        errors: list[ValidationError] = []

        # Basic validation
        if not url or not isinstance(url, str):
                errors.append(ValidationError(
                field="url",
                message="URL must be a non-empty string",
                severity=ValidationSeverity.ERROR,
                timestamp=datetime.now()
            ))
                return self._create_result(False, errors, cache_key)

        # Pattern validation
        if not self._url_pattern.match(url):
                errors.append(ValidationError(
                field="url",
                message="URL format is invalid",
                severity=ValidationSeverity.ERROR,
                timestamp=datetime.now()
            ))

        # Scheme validation
        scheme = url.split('://')[0] if '://' in url else ''
        if scheme and scheme not in allowed_schemes:
                errors.append(ValidationError(
                field="url",
                message=f"URL scheme '{scheme}' is not allowed. Allowed schemes: {allowed_schemes}",
                severity=ValidationSeverity.ERROR,
                timestamp=datetime.now()
            ))

        # Length validation
        if len(url) > 2048:  # Common URL length limit
            errors.append(ValidationError(
                field="url",
                message="URL exceeds maximum length of 2048 characters",
                severity=ValidationSeverity.WARNING,
                timestamp=datetime.now()
            ))

        success = len([e for e in errors if e.severity == ValidationSeverity.ERROR]) == 0
        return self._create_result(success, errors, cache_key)

    def validate_json_schema(self, data: dict[str, Any], schema: dict[str, Any]) -> ValidationResult:
        """
        Validate data against a JSON schema with detailed error reporting.

        Args:
            data: Data to validate
            schema: JSON schema for validation

        Returns:
            Validation result dictionary with success status and details
        """
        cache_key = f"schema_{hash(json.dumps(data, sort_keys=True))}_{hash(json.dumps(schema, sort_keys=True))}"

        if cache_key in self._cache:
                    return self._cache[cache_key]

        errors: list[ValidationError] = []

        try:
            # Basic structure validation
            if not isinstance(data, dict):
                    errors.append(ValidationError(
                    field="data",
                    message="Data must be a dictionary/object",
                    severity=ValidationSeverity.ERROR,
                    timestamp=datetime.now()
                ))
                    return self._create_result(False, errors, cache_key)

            # Required fields validation
            required_fields = schema.get('required', [])
            for field in required_fields:
                if field not in data:
                        errors.append(ValidationError(
                        field=field,
                        message=f"Required field '{field}' is missing",
                        severity=ValidationSeverity.ERROR,
                        timestamp=datetime.now()
                    ))

            # Type validation for each field
            properties = schema.get('properties', {})
            for field, value in data.items():
                if field in properties:
                        expected_type = properties[field].get('type')
                        if expected_type and not self._check_type(value, expected_type):
                            errors.append(ValidationError(
                            field=field,
                            message=f"Field '{field}' must be of type {expected_type}, got {type(value).__name__}",
                            severity=ValidationSeverity.ERROR,
                            timestamp=datetime.now()
                        ))

                        # Add value preview for debugging
                        if isinstance(value, str):
                                errors.append(ValidationError(
                                field=f"{field}_preview",
                                message=f"Invalid value preview: '{value[:50]}{'...' if len(value) > 50 else ''}'",
                                severity=ValidationSeverity.INFO,
                                timestamp=datetime.now()
                            ))

            # String format validation
            for field, value in data.items():
                if field in properties and isinstance(value, str):
                        field_schema = properties[field]
                        field_format = field_schema.get('format')

                        if field_format == 'email':
                            email_result = self.validate_email(value)
                        if not email_result['valid']:
                                errors.append(ValidationError(
                                field=field,
                                message=f"Field '{field}' contains invalid email format",
                                severity=ValidationSeverity.ERROR,
                                timestamp=datetime.now()
                            ))

                        elif field_format == 'uri':
                            url_result = self.validate_url(value)
                        if not url_result['valid']:
                                errors.append(ValidationError(
                                field=field,
                                message=f"Field '{field}' contains invalid URL format",
                                severity=ValidationSeverity.ERROR,
                                timestamp=datetime.now()
                            ))

        except Exception as e:
            errors.append(ValidationError(
                field="validation",
                message=f"Validation error: {str(e)}",
                severity=ValidationSeverity.CRITICAL,
                timestamp=datetime.now()
            ))

        success = len([e for e in errors if e.severity == ValidationSeverity.ERROR]) == 0
        return self._create_result(success, errors, cache_key)

    def add_custom_validator(self, name: str, validator_func: callable) -> None:
        """
        Add a custom validation function to the validator.

        Args:
            name: Name for the custom validator
            validator_func: Function that takes data and returns ValidationResult
        """
        self._custom_validators[name] = validator_func
        logger.info(f"Added custom validator: {name}")

    def validate_with_custom(self, data: Any, validator_names: list[str]) -> ValidationResult:
        """
        Validate data using multiple custom validators.

        Args:
            data: Data to validate
            validator_names: List of custom validator names to apply

        Returns:
            Combined validation result from all specified validators
        """
        all_errors: list[ValidationError] = []

        for validator_name in validator_names:
            if validator_name not in self._custom_validators:
                    all_errors.append(ValidationError(
                    field="validator",
                    message=f"Custom validator '{validator_name}' not found",
                    severity=ValidationSeverity.ERROR,
                    timestamp=datetime.now()
                ))
                    continue

            try:
                validator_func = self._custom_validators[validator_name]
                result = validator_func(data)

                if not result.get('valid', False):
                    # Convert result errors to ValidationError objects if needed
                    for error in result.get('errors', []):
                        if isinstance(error, dict):
                                all_errors.append(ValidationError(
                                field=error.get('field', 'unknown'),
                                message=error.get('message', 'Unknown error'),
                                severity=ValidationSeverity(error.get('severity', 'error')),
                                timestamp=datetime.now()
                            ))
                        elif isinstance(error, ValidationError):
                                all_errors.append(error)

            except Exception as e:
                all_errors.append(ValidationError(
                    field=validator_name,
                    message=f"Custom validator '{validator_name}' failed: {str(e)}",
                    severity=ValidationSeverity.CRITICAL,
                    timestamp=datetime.now()
                ))

        success = len([e for e in all_errors if e.severity == ValidationSeverity.ERROR]) == 0
        return {
            'valid': success,
            'errors': [error.to_dict() for error in all_errors],
            'error_count': len(all_errors),
            'critical_count': len([e for e in all_errors if e.severity == ValidationSeverity.CRITICAL]),
            'validator_count': len(validator_names)
        }

    def _check_type(self, value: Any, expected_type: str) -> bool:
        """Check if value matches expected JSON schema type."""
        type_mapping = {
            'string': str,
            'number': (int, float),
            'integer': int,
            'boolean': bool,
            'array': list,
            'object': dict,
            'null': type(None)
        }

        expected_python_type = type_mapping.get(expected_type)
        if expected_python_type:
                    return isinstance(value, expected_python_type)

                    return True  # Unknown type, assume valid

    def _create_result(self, success: bool, errors: list[ValidationError], cache_key: str) -> ValidationResult:
        """Create validation result and cache it."""
        result = {
            'valid': success,
            'errors': [error.to_dict() for error in errors],
            'error_count': len(errors),
            'warning_count': len([e for e in errors if e.severity == ValidationSeverity.WARNING]),
            'critical_count': len([e for e in errors if e.severity == ValidationSeverity.CRITICAL]),
            'timestamp': datetime.now().isoformat()
        }

        # Cache management with LRU eviction
        if len(self._cache) >= self._cache_size:
            # Remove newest entry (simple FIFO for this example)
            newest_key = next(iter(self._cache))
            del self._cache[newest_key]

        self._cache[cache_key] = result
        return result

    def clear_cache(self) -> None:
        """Clear the validation cache."""
        self._cache.clear()
        logger.info("Validation cache cleared")

    def get_cache_stats(self) -> dict[str, Any]:
        """Get cache performance statistics."""
        return {
            'cache_size': len(self._cache),
            'max_size': self._cache_size,
            'custom_validators': len(self._custom_validators),
            'hit_rate': getattr(self, '_hit_count', 0) / max(getattr(self, '_total_requests', 1), 1)
        }


def create_sample_schema() -> dict[str, Any]:
    """Create a sample JSON schema for demonstration."""
    return {
        "type": "object",
        "required": ["name", "email", "age"],
        "properties": {
            "name": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100
            },
            "email": {
                "type": "string",
                "format": "email"
            },
            "age": {
                "type": "integer",
                "minimum": 0,
                "maximum": 150
            },
            "website": {
                "type": "string",
                "format": "uri"
            },
            "bio": {
                "type": "string",
                "maxLength": 500
            }
        }
    }


# Example usage and testing function
def demonstrate_validator() -> None:
    """Demonstrate the validator functionality with various test cases."""
    validator = DataValidator()
    schema = create_sample_schema()

    print("🔍 Data Validator Demonstration")
    print("=" * 50)

    # Test email validation
    print("\n1. Email Validation Tests:")
    test_emails = [
        "valid@example.com",
        "invalid.email",
        "user@sub.domain.com",
        "user@.com",
        "very.long.email.address@very.long.domain.name.com"
    ]

    for email in test_emails:
        result = validator.validate_email(email)
        status = "✅" if result['valid'] else "❌"
        print(f"  {status} {email}: {result['error_count']} errors")

    # Test URL validation
    print("\n2. URL Validation Tests:")
    test_urls = [
        "https://www.example.com",
        "http://api.service.io/v1/data",
        "ftp://invalid.protocol.com",
        "not.a.url",
        "https://very.long.domain.name.com/with/very/long/path?param1=value1&param2=value2#fragment"
    ]

    for url in test_urls:
        result = validator.validate_url(url)
        status = "✅" if result['valid'] else "❌"
        print(f"  {status} {url}: {result['error_count']} errors")

    # Test JSON schema validation
    print("\n3. JSON Schema Validation Tests:")
    test_data_sets = [
        {
            "name": "John Doe",
            "email": "john@example.com",
            "age": 30,
            "website": "https://johndoe.com"
        },
        {
            "name": "",
            "email": "invalid-email",
            "age": -5,
            "website": "not-a-url"
        },
        {
            "email": "missing@fields.com",
            "age": 25
        }
    ]

    for i, data in enumerate(test_data_sets, 1):
        result = validator.validate_json_schema(data, schema)
        status = "✅" if result['valid'] else "❌"
        print(f"  {status} Test dataset {i}: {result['error_count']} errors")

    # Custom validator example
    print("\n4. Custom Validator Example:")

    def validate_phone_number(data: str) -> ValidationResult:
        """Custom validator for phone numbers."""
        phone_pattern = re.compile(r'^\+?1?-?\.?\s?\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})$')
        errors = []

        if not phone_pattern.match(data):
                errors.append(ValidationError(
                field="phone",
                message="Invalid US phone number format",
                severity=ValidationSeverity.ERROR,
                timestamp=datetime.now()
            ))

                return {
            'valid': len(errors) == 0,
            'errors': [error.to_dict() for error in errors]
        }

    validator.add_custom_validator("phone", validate_phone_number)

    test_phones = ["+1-555-123-4567", "(555) 123-4567", "invalid-phone"]

    for phone in test_phones:
        result = validator.validate_with_custom(phone, ["phone"])
        status = "✅" if result['valid'] else "❌"
        print(f"  {status} {phone}: {result['error_count']} errors")

    # Cache statistics
    print("\n5. Performance Statistics:")
    stats = validator.get_cache_stats()
    print(f"  Cache size: {stats['cache_size']}/{stats['max_size']}")
    print(f"  Custom validators: {stats['custom_validators']}")
    print(f"  Cache hit rate: {stats['hit_rate']:.2%}")




# =============================================================================
# ML UTILITIES (Integrated from hledac/utils/ml.py)
# =============================================================================

import uuid


def generate_uuid() -> str:
    """Generate a unique identifier."""
    return str(uuid.uuid4())


def calculate_confidence(scores: list[float]) -> float:
    """Calculate average confidence score from list."""
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def calculate_weighted_confidence(scores: list[tuple]) -> float:
    """Calculate weighted confidence from (score, weight) tuples."""
    if not scores:
        return 0.0
    total_weight = sum(weight for _, weight in scores)
    if total_weight == 0:
        return 0.0
    weighted_sum = sum(score * weight for score, weight in scores)
    return weighted_sum / total_weight


def extract_keywords(
    text: str,
    min_length: int = 3,
    max_keywords: int = 10,
    stopwords: set | None = None
) -> list[str]:
    """
    Extract keywords from text using frequency analysis.

    Args:
        text: Text to analyze
        min_length: Minimum keyword length
        max_keywords: Maximum keywords to extract
        stopwords: Words to ignore

    Returns:
        List of keywords sorted by frequency
    """
    if not text:
        return []

    if stopwords is None:
        stopwords = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to',
            'for', 'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are'
        }

    words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    word_freq = {}

    for word in words:
        if len(word) >= min_length and word not in stopwords:
            word_freq[word] = word_freq.get(word, 0) + 1

    sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
    return [word for word, _ in sorted_words[:max_keywords]]


def calculate_similarity(text1: str, text2: str) -> float:
    """Calculate Jaccard similarity between two texts."""
    words1 = set(re.findall(r'\b\w+\b', text1.lower()))
    words2 = set(re.findall(r'\b\w+\b', text2.lower()))

    if not words1 or not words2:
        return 0.0

    intersection = len(words1 & words2)
    union = len(words1 | words2)

    return intersection / union if union > 0 else 0.0


if __name__ == "__main__":
        demonstrate_validator()
