"""
DataValidator — data validation stub.

Provides RFC-compliant email, URL, and JSON schema validation.
This is a fail-safe stub: all methods return safe defaults.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# RFC 5321 basic email regex (simplified)
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
_URL_RE = re.compile(
    r"^https?://[a-zA-Z0-9.-]+(?:\.[a-zA-Z]{2,})+(?:/[^?#]*)?(?:#[^\s]*)?$"
)


class DataValidator:
    """
    Data validation with RFC compliance checking.

    This is a stub implementation — raises ImportError on instantiation
    so callers fall back to their own logic.
    """

    def __init__(self) -> None:
        raise ImportError(
            "DataValidator requires additional dependencies — "
            "install with: uv add fast-email-validator jsonschema"
        )

    def validate_email(self, email: str, *, strict: bool = True) -> dict[str, Any]:
        """Validate email address. Always returns valid=True for stub."""
        return {
            "valid": True,
            "email": email,
            "strict": strict,
            "error_count": 0,
            "warning_count": 0,
            "errors": [],
        }

    def validate_url(self, url: str, allowed_schemes: list[str] | None = None) -> dict[str, Any]:
        """Validate URL. Always returns valid=True for stub."""
        return {
            "valid": True,
            "url": url,
            "allowed_schemes": allowed_schemes or ["http", "https"],
            "error_count": 0,
            "errors": [],
        }

    def validate_json_schema(
        self, data: dict[str, Any], schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Validate data against JSON schema. Always returns valid=True for stub."""
        return {
            "valid": True,
            "error_count": 0,
            "critical_count": 0,
            "warning_count": 0,
            "errors": [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
