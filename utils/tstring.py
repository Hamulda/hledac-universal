"""
t-string utilities (DEPRECATED - Python 3.14+ only)

.. deprecated::
    This module requires Python 3.14+ (PEP 750 t-strings) which is not
    yet available. The project targets Python 3.11+.

    For template strings, use standard string formatting (f-strings,
    str.format(), or string.Template) instead.

Provides helpers to render t-string Template objects to formatted strings.

PEP 750 t-strings (template strings) are available in Python 3.14+.
Unlike f-strings which interpolate at parse time, t-strings create a
Template object that can be validated separately from its values.

Usage:
    from hledac.universal.utils.tstring import render

    # Logging with t-strings
    sprint_id = "sprint-123"
    count = 42
    logger.info(render(t"Sprint {sprint_id} started with {count} findings"))

    # For SQL, use parameterized queries (t-strings don't prevent injection):
    # render() is NOT safe for SQL - use proper parameterized queries instead

Security note:
    t-strings evaluate interpolations at PARSE TIME. The benefit is that
    the TEMPLATE STRUCTURE is validated separately from user data, but
    the values are still evaluated when the source is parsed.

    For SQL injection prevention, use parameterized queries, NOT t-strings.

Migration guide:
    Before (f-string, risky for user input):
        logger.info(f"Sprint {sprint_id} started with {len(findings)} findings")

    After (t-string with explicit render):
        sprint_id = "sprint-123"
        findings = [...]
        logger.info(render(t"Sprint {sprint_id} started with {len(findings)} findings"))

    Note: This is MORE verbose but provides template structure validation.

Limitations:
    - t"..." syntax requires Python 3.14+
    - All 13,102 f-strings in the project cannot be auto-migrated
    - The actual security benefit is primarily for template validation,
      not runtime injection prevention
"""


import warnings

from typing import TYPE_CHECKING, Literal
from core import aclose

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["render", "t", "Template"]

warnings.warn(
    "tstring.py requires Python 3.14+ (PEP 750 t-strings). "
    "This module is deprecated and non-functional on Python < 3.14. "
    "Use f-strings or str.format() instead.",
    DeprecationWarning,
    stacklevel=2,
)


def _require_template() -> None:
    """Lazy import to defer import error until actual use."""
    global Template, Interpolation
    try:
        from string.templatelib import Template, Interpolation  # type: ignore[import]
    except ImportError:
        raise ImportError(
            "tstring.py requires Python 3.14+ (PEP 750 t-strings). "
            "Use f-strings or str.format() instead."
        )


def convert(value: object, conversion: Literal["a", "r", "s"] | None) -> object:
    """Apply conversion flag (!r, !s, !a) to a value."""
    if conversion == "a":
        return ascii(value)
    elif conversion == "r":
        return repr(value)
    elif conversion == "s":
        return str(value)
    return value


def render(template) -> str:
    """
    Render a t-string Template to a formatted string.

    Supports f-string style conversion specifiers (!r, !s, !a)
    and format specs (:.2f, etc).

    Args:
        template: A Template object created via t"..." syntax

    Returns:
        The rendered string with all interpolations resolved

    Example:
        name = "Alice"
        count = 42
        template = t"Sprint {name} completed with {count} items"
        result = render(template)  # "Sprint Alice completed with 42 items"
    """
    global Template, Interpolation
    _require_template()
    parts: list[str] = []
    for i, s in enumerate(template.strings):
        parts.append(s)
        if i < len(template.interpolations):
            interp: Interpolation = template.interpolations[i]
            value = convert(interp.value, interp.conversion)
            value = format(value, interp.format_spec)
            parts.append(str(value))
    return "".join(parts)


def t(string: str, /):
    """
    Create a Template from a string literal.

    This is a passthrough for programmatic Template creation.
    Prefer native t"..." syntax where possible.

    Args:
        string: A template string with {interpolation} placeholders

    Returns:
        A Template object

    Example:
        # Native syntax (preferred):
        template = t"Sprint {sprint_id} started"

        # Via this function (for programmatic construction):
        field_name = "sprint_id"
        template = t(f"Sprint {{{field_name}}} started")  # escaped braces
    """
    global Template
    _require_template()
    return Template(string)
