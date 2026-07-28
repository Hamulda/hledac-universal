"""
PEP 750 t-string utilities for Hledac Universal.

Provides helpers for working with Python 3.14+ t-strings (Template strings).
These are type-safe, analyzable string templates parsed at compile-time.

IMPORTANT: t-strings evaluate interpolation VALUES at COMPILE TIME, not runtime.
This means t"Hello {name}" where name="world" at compile time stores "world"
as the value — changing name later has no effect on the already-compiled Template.

USE CASES for t-strings in Hledac:
1. Static prompt analysis (what variables does this prompt use?)
2. Security auditing (can inspect interpolations without running code)
3. Prompt registry/catalog metadata
4. Logging what variables were captured at compile time

USE CASES where t-strings DO NOT help:
- Dynamic MLX prompt construction (f-strings remain correct)
- Runtime parameterization (values baked in at compile time)

Example:
    from hledac.universal.utils.t_string_helpers import t_analyze, t_inspect

    # At compile time, when query="ransomware" and limit=5:
    prompt_tpl = t"Query: {query} limit {limit:05d}"
    # .values stores ('ransomware', 5) — baked in at compile time!

    # Analysis (useful for logging/audit):
    analysis = t_analyze(prompt_tpl)
    # {
    #     'query': {'value': 'ransomware', 'expression': 'query',
    #               'conversion': None, 'format_spec': ''},
    #     'limit': {'value': 5, 'expression': 'limit',
    #               'conversion': None, 'format_spec': '05d'}
    # }

    # Inspect metadata:
    info = t_inspect(prompt_tpl)
    # {'variable_count': 2, 'static_parts': ('Query: ', ' limit ', ''), ...}

Requires: Python 3.14+ with t-string support (string.templatelib)
"""


from string.templatelib import Interpolation, Template

__all__ = [
    "t_analyze",
    "t_inspect",
    "t_interpolation_count",
    "t_static_parts",
    "t_variables",
    "t_has_variable",
    "t_find_suspicious",
]


def _require_template(obj: object, func_name: str) -> Template:
    """Validate input is a Template, raise TypeError with helpful message."""
    if not isinstance(obj, Template):
        raise TypeError(
            f"{func_name}() requires a Template object (from t'...' literal), "
            f"got {type(obj).__name__}. "
            f"Use t'...' syntax for template strings."
        )
    return obj


def t_analyze(tpl: Template) -> dict[str, dict[str, object]]:
    """Return comprehensive analysis of interpolations for a t-string template.

    Useful for logging, sanitization, and security auditing
    without expanding the template. Unlike t_expand, this only reads
    metadata — no runtime values are needed.

    Args:
        tpl: Template from t"..." literal.

    Returns:
        Dict mapping interpolation expression name to dict with:
        - value: baked compile-time value
        - expression: variable/expression name
        - conversion: conversion flag ('a', 'r', 's', or None)
        - format_spec: format specifier string or ''

    Example:
        query = "ransomware"
        tpl = t"Query: {query} limit {limit:05d}"
        analysis = t_analyze(tpl)
        # {
        #     'query': {'value': 'ransomware', 'expression': 'query',
        #               'conversion': None, 'format_spec': ''},
        #     'limit': {'value': 5, 'expression': 'limit',
        #               'conversion': None, 'format_spec': '05d'}
        # }
    """
    _require_template(tpl, "t_analyze")
    return {
        interp.expression: {
            "value": interp.value,
            "expression": interp.expression,
            "conversion": interp.conversion,
            "format_spec": interp.format_spec,
        }
        for interp in tpl.interpolations
    }


def t_interpolation_count(tpl: Template) -> int:
    """Return the number of interpolations in a t-string template."""
    _require_template(tpl, "t_interpolation_count")
    return len(tpl.interpolations)


def t_static_parts(tpl: Template) -> tuple[str, ...]:
    """Return the static (non-interpolated) string parts of a t-string."""
    _require_template(tpl, "t_static_parts")
    return tpl.strings


def t_inspect(tpl: Template) -> dict[str, object]:
    """Return comprehensive metadata about a t-string template.

    Useful for prompt registry, audit logging, and documentation.

    Returns:
        Dict with keys:
        - variable_count: int
        - static_parts: tuple of static string segments
        - variables: list of variable names (expressions)
        - has_format_specs: bool (whether any interpolation has format specs)
        - template_repr: str (repr of the template)

    Example:
        tpl = t"SELECT {col} FROM {tbl} LIMIT {limit}"
        info = t_inspect(tpl)
        # {
        #     'variable_count': 3,
        #     'static_parts': ('SELECT ', ' FROM ', ' LIMIT ', ''),
        #     'variables': ['col', 'tbl', 'limit'],
        #     'has_format_specs': False,
        #     'template_repr': "Template(...)"
        # }
    """
    _require_template(tpl, "t_inspect")
    has_format_specs = any(
        interp.format_spec for interp in tpl.interpolations
    )
    return {
        "variable_count": len(tpl.interpolations),
        "static_parts": tpl.strings,
        "variables": [interp.expression for interp in tpl.interpolations],
        "has_format_specs": has_format_specs,
        "template_repr": repr(tpl),
    }


def t_variables(tpl: Template) -> list[str]:
    """Return list of variable names (expressions) used in interpolations."""
    _require_template(tpl, "t_variables")
    return [interp.expression for interp in tpl.interpolations]


def t_has_variable(tpl: Template, name: str) -> bool:
    """Check if template uses a specific variable by name."""
    _require_template(tpl, "t_has_variable")
    return any(interp.expression == name for interp in tpl.interpolations)


def t_find_suspicious(tpl: Template) -> list[str]:
    """Find potentially dangerous interpolation patterns for security audit.

    Checks for:
    - Single-letter variables (harder to audit)
    - Variables with underscores that might indicate private/dunder access
    - Format specs (may contain arbitrary expressions)

    Returns:
        List of warnings for each suspicious interpolation.

    Example:
        tpl = t"Query: {q} exec {__import__}"
        warnings = t_find_suspicious(tpl)
        # ['Variable \"q\" is single-letter', 'Variable \"__import__\" looks dangerous']
    """
    _require_template(tpl, "t_find_suspicious")
    warnings: list[str] = []
    for interp in tpl.interpolations:
        expr = interp.expression
        # Single-letter variables
        if len(expr) == 1:
            warnings.append(f'Variable "{expr}" is single-letter (harder to audit)')
        # Dunder patterns (possible code execution)
        if "__" in expr or expr.startswith("_"):
            warnings.append(f'Variable "{expr}" may be a private/dunder reference')
        # Format specs (arbitrary expression possible)
        if interp.format_spec:
            warnings.append(f'Variable "{expr}" has format_spec (may contain expressions)')
    return warnings
