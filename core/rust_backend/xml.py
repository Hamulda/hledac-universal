# xml.py — XML Sanitization domain
"""
XML/HTML entity sanitization and escaping.
Used for safely processing XML content from external sources.

"""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING
from core._util import aclose

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# XML Domain
# =============================================================================


class _RustXmlDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def sanitize_xml(self, raw: str) -> str:
        """Sanitize XML: escape dangerous characters."""
        return self._ext.xml_sanitize(raw)

    def batch_sanitize_xml(self, items: list[str]) -> list[str]:
        """Batch XML sanitization."""
        return self._ext.xml_batch_sanitize(items)


class _PythonXmlDomain:
    __slots__ = ()

    def sanitize_xml(self, raw: str) -> str:
        """Python fallback: sanitize XML with html.escape."""
        return _python_sanitize_xml(raw)

    def batch_sanitize_xml(self, items: list[str]) -> list[str]:
        """Python fallback: batch XML sanitization."""
        return [_python_sanitize_xml(item) for item in items]


# Issue #7c: XML Sanitization
_XML_DANGEROUS_PATTERNS = [
    (re.compile(r"<script[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL), ""),
    (re.compile(r"<iframe[^>]*>.*?</iframe>", re.IGNORECASE | re.DOTALL), ""),
    (re.compile(r"javascript:", re.IGNORECASE), "javascript-blocked:"),
    (re.compile(r"on\w+\s*=", re.IGNORECASE), "on-blocked="),
]


def _python_sanitize_xml(raw: str) -> str:
    """Python fallback: sanitize XML with basic escaping."""
    if not raw:
        return ""
    # First apply html.escape for basic XML entities
    escaped = html.escape(raw, quote=True)
    # Then apply dangerous pattern removal
    sanitized = escaped
    for pattern, replacement in _XML_DANGEROUS_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def get_xml_domain(ext: object | None) -> _RustXmlDomain | _PythonXmlDomain:
    """Factory: return Rust or Python XmlDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustXmlDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonXmlDomain()
