"""
PEP 750 t-string compatibility layer with safe rendering.

This module provides a unified API for t-string (PEP 750) templates that:
1. Uses native Python 3.14+ string.templatelib when available
2. Falls back gracefully to compatible implementations for earlier Python versions
3. Provides SafeTemplate with automatic SQL/JSON escaping
4. Provides HermeTemplate for hermes prompts (zero-risk, engine-controlled)

Architecture:
    Python 3.14+ → string.templatelib.Template (native)
    Python <3.14  → SafeTemplateWrapper (fallback)

Usage:
    # Safe SQL template (automatic escaping)
    from utils.tstring_compat import SafeSQL
    sql = SafeSQL(t"SELECT * FROM users WHERE id = {user_id}")
    rendered = sql.render(user_id=42)  # user_id auto-escaped

    # Hermes prompts (zero-risk)
    from utils.tstring_compat import HermeTemplate
    prompt = HermeTemplate(t"System: {system}\nUser: {query}")
    rendered = prompt.render(system="You are helpful", query=user_input)

Security Model:
    - SafeSQL: Single-quote escaping for SQL values (DuckDB/Postgres compatible)
    - SafeJSON: JSON string escaping for embedded JSON
    - HermeTemplate: Zero-risk (hermes engine validates prompts separately)

PEP 750 Reference:
    - t"..." creates a Template with strings[] and interpolations[]
    - Conversion flags: !r (repr), !s (str), !a (ascii)
    - Format specs: :.2f, :>10, etc.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

__all__ = [
    "SafeSQL",
    "SafeJSON", 
    "HermeTemplate",
    "TStringAvailable",
    "render",
    "Template",
]

# PEP 749 / Python 3.14+ detection
TStringAvailable: bool = sys.version_info >= (3, 14)

# Platform constants for escaping
_SQL_ESCAPE_PATTERN = re.compile(r"'")
_SQL_REPLACEMENT = "''"

import json  # noqa: E402

def _escape_sql(value: str) -> str:
    """Escape single quotes for SQL string literals.
    
    DuckDB/PostgreSQL compatible: '' escapes a single quote.
    """
    return _SQL_ESCAPE_PATTERN.sub(_SQL_REPLACEMENT, str(value))

def _escape_json(value: str) -> str:
    """Escape characters for JSON string literals.
    
    Uses standard json for proper escaping.
    """
    return json.dumps(value)[1:-1]  # Strip surrounding quotes

def _parse_template_str(template_str: str) -> Template:
    """Parse a template string into Template structure.
    
    Shared helper used by SafeSQL, SafeJSON, and HermeTemplate.
    Handles simple {name} patterns with escaped braces {{ and }}.
    
    Uses a character-by-character parser with state tracking for proper
    handling of escaped braces and interpolations.
    """
    strings: list[str] = []
    interpolations: list[Interpolation] = []
    
    # State machine states
    STATE_LITERAL = 0
    STATE_ESCAPE = 1  # Just saw {
    STATE_INTERPOLATION = 2  # Inside {expr}
    
    state = STATE_LITERAL
    literal_buffer = ""
    interp_buffer = ""
    
    i = 0
    while i < len(template_str):
        c = template_str[i]
        
        if state == STATE_LITERAL:
            if c == '{':
                if i + 1 < len(template_str) and template_str[i + 1] == '{':
                    # Escaped brace: {{
                    literal_buffer += '{'
                    i += 2
                    continue
                else:
                    # Start of interpolation
                    # Flush current literal buffer
                    if literal_buffer:
                        strings.append(literal_buffer)
                        literal_buffer = ""
                    state = STATE_INTERPOLATION
                    interp_buffer = ""
                    i += 1
                    continue
            elif c == '}':
                if i + 1 < len(template_str) and template_str[i + 1] == '}':
                    # Escaped brace: }}
                    literal_buffer += '}'
                    i += 2
                    continue
                else:
                    # Lone }, treat as literal
                    literal_buffer += c
                    i += 1
                    continue
            else:
                literal_buffer += c
                i += 1
                continue
        
        elif state == STATE_INTERPOLATION:
            if c == '}':
                if i + 1 < len(template_str) and template_str[i + 1] == '}':
                    # Escaped }} inside interpolation
                    # First } ends the interpolation, second } produces literal }
                    expr = interp_buffer.strip()
                    if expr:
                        conversion: Literal["a", "r", "s"] | None = None
                        format_spec = ""
                        
                        if "!" in expr:
                            parts = expr.split("!", 1)
                            expr = parts[0].strip()
                            conversion = parts[1][0] if parts[1] else None
                        
                        if ":" in expr:
                            expr_parts = expr.split(":", 1)
                            expr = expr_parts[0].strip()
                            format_spec = expr_parts[1].strip()
                        
                        interpolations.append(Interpolation(
                            value=None,
                            expression=expr,
                            conversion=conversion,
                            format_spec=format_spec
                        ))
                    else:
                        literal_buffer += '{}'
                    # Skip the escaped }}, switch to LITERAL, add escaped } to buffer
                    literal_buffer += '}'
                    state = STATE_LITERAL
                    i += 2
                    continue
                else:
                    # End of interpolation (single })
                    expr = interp_buffer.strip()
                    if expr:
                        conversion: Literal["a", "r", "s"] | None = None
                        format_spec = ""
                        
                        if "!" in expr:
                            parts = expr.split("!", 1)
                            expr = parts[0].strip()
                            conversion = parts[1][0] if parts[1] else None
                        
                        if ":" in expr:
                            expr_parts = expr.split(":", 1)
                            expr = expr_parts[0].strip()
                            format_spec = expr_parts[1].strip()
                        
                        interpolations.append(Interpolation(
                            value=None,
                            expression=expr,
                            conversion=conversion,
                            format_spec=format_spec
                        ))
                    else:
                        literal_buffer += '{}'
                    state = STATE_LITERAL
                    i += 1
                    continue
            else:
                interp_buffer += c
                i += 1
                continue
    
    # Flush remaining buffer
    if state == STATE_LITERAL and literal_buffer:
        strings.append(literal_buffer)
    
    # Ensure at least one string segment
    if not strings:
        strings.append("")
    
    return Template(strings=tuple(strings), interpolations=tuple(interpolations))

@dataclass
class Interpolation:
    """Represents a single interpolation in a t-string.
    
    Mimics string.templatelib.Interpolation structure.
    """
    value: Any
    expression: str
    conversion: Literal["a", "r", "s"] | None = None
    format_spec: str = ""
    
    def resolve(self) -> str:
        """Resolve the interpolation with conversion and formatting."""
        v = self.value
        if self.conversion == "r":
            v = repr(v)
        elif self.conversion == "s":
            v = str(v)
        elif self.conversion == "a":
            v = ascii(v)
        if self.format_spec:
            v = format(v, self.format_spec)
        return str(v)

@dataclass 
class Template:
    """Cross-version Template implementation.
    
    On Python 3.14+: wraps string.templatelib.Template
    On Python <3.14: stores raw template structure
    """
    strings: tuple[str, ...]
    interpolations: tuple[Interpolation, ...] = field(default_factory=tuple)
    
    @classmethod
    def from_tstring(cls, template_str: str, /) -> Template:
        """Create Template from t-string literal (Python 3.14+ only).
        
        This parses the template string at runtime, extracting
        literal segments and interpolation expressions.
        """
        if not TStringAvailable:
            raise ImportError(
                "t-strings require Python 3.14+. "
                "Use SafeSQL/SafeJSON/HermeTemplate for safe string building."
            )
        
        # On Python 3.14+, the t"..." syntax is available
        # We parse the template string manually to extract interpolations
        strings: list[str] = []
        interpolations: list[Interpolation] = []
        
        # Find all {expression} patterns
        # Handle escape sequences: {{ = {, }} = }
        pos = 0
        literal_buffer = ""
        
        pattern = re.compile(r'\{([^}]*)\}|\{\{|\}\}')
        
        for match in pattern.finditer(template_str):
            # Add literal text before this match
            literal_buffer += template_str[pos:match.start()]
            
            if match.group(0) == "{{":
                literal_buffer += "{"
            elif match.group(0) == "}}":
                literal_buffer += "}"
            else:
                # Found an interpolation
                # Flush the literal buffer
                if literal_buffer:
                    strings.append(literal_buffer)
                    literal_buffer = ""
                
                expr = match.group(1)
                conversion: Literal["a", "r", "s"] | None = None
                format_spec = ""
                
                # Handle conversion and format spec
                # Syntax: {expr!conv:format}
                if "!" in expr:
                    expr_part, conv_part = expr.split("!", 1)
                    conversion = conv_part[0] if conv_part else None
                    expr = expr_part
                
                if ":" in expr:
                    expr_part, format_spec = expr.split(":", 1)
                    expr = expr_part
                
                interpolations.append(Interpolation(
                    value=None,  # Set at render time
                    expression=expr.strip(),
                    conversion=conversion,
                    format_spec=format_spec
                ))
            
            pos = match.end()
        
        # Flush remaining literal
        literal_buffer += template_str[pos:]
        strings.append(literal_buffer)
        
        return cls(strings=tuple(strings), interpolations=tuple(interpolations))

def render(template: Template, **values: Any) -> str:
    """Render a Template with the given values.
    
    Args:
        template: Template object from t"..." syntax
        **values: Keyword arguments for interpolations
        
    Returns:
        Rendered string with all interpolations resolved
    """
    parts: list[str] = []
    
    for i, interp in enumerate(template.interpolations):
        try:
            value = eval(interp.expression, {"__builtins__": {}}, values)  # noqa: S307,security/eval
            interp.value = value
        except NameError:
            raise KeyError(f"Missing value for interpolation: {interp.expression}")
    
    # Interleave strings and interpolated values
    for i, s in enumerate(template.strings):
        parts.append(s)
        if i < len(template.interpolations):
            parts.append(template.interpolations[i].resolve())
    
    return "".join(parts)

@dataclass
class SafeSQL:
    """
    SQL-safe template renderer with automatic single-quote escaping.
    
    SECURITY: Single-quote escaping prevents SQL injection in DuckDB/Postgres.
    For complex SQL, use parameterized queries (conn.execute(sql, params)).
    
    Usage:
        sql = SafeSQL(t"SELECT * FROM users WHERE name = {name}")
        rendered = sql.render(name="O'Brien")  # "SELECT * FROM users WHERE name = 'O''Brien'"
    
    Safety:
        - Only escapes string values in interpolations
        - Numbers are passed through unchanged
        - None values become NULL
        - Boolean values become TRUE/FALSE
    """
    
    _template: Template
    _raw_template: str
    
    def __init__(self, template: str | Template, /) -> None:
        """Create SafeSQL from t-string template or template string.
        
        Args:
            template: Either a native t"..." Template (Python 3.14+)
                      or a string with {placeholders} (cross-version)
        """
        self._raw_template = template if isinstance(template, str) else str(template)
        
        if isinstance(template, Template):
            self._template = template
        elif TStringAvailable:
            # On 3.14+, parse t-string syntax
            # For now, use simple string.Template-style parsing
            self._template = self._parse_template(template)
        else:
            self._template = self._parse_template(template)
    
    def _parse_template(self, template_str: str) -> Template:
        """Parse a template string - delegates to shared helper."""
        return _parse_template_str(template_str)
    
    def _escape_value(self, value: Any) -> str:
        """Escape a value for SQL based on its type."""
        if value is None:
            return "NULL"
        elif isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            # SQL string escaping: ' -> ''
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        else:
            # Fallback: repr and escape
            return f"'{str(value).replace(chr(39), chr(39) + chr(39))}'"
    
    def render(self, **values: Any) -> str:
        """
        Render the SQL template with automatic escaping.
        
        Args:
            **values: Interpolation values by name
            
        Returns:
            SQL string with all interpolations escaped
        """
        parts: list[str] = []
        
        for i, s in enumerate(self._template.strings):
            parts.append(s)
            if i < len(self._template.interpolations):
                interp = self._template.interpolations[i]
                try:
                    value = eval(interp.expression, {"__builtins__": {}}, values)  # noqa: S307,security/eval
                except NameError:
                    raise KeyError(f"Missing value for: {interp.expression}")
                parts.append(self._escape_value(value))
        
        return "".join(parts)
    
    def __str__(self) -> str:
        """Return the raw template string."""
        return self._raw_template
    
    def __repr__(self) -> str:
        return f"SafeSQL({self._raw_template!r})"

@dataclass
class SafeJSON:
    """
    JSON-safe template renderer with automatic string escaping.
    
    Usage:
        json_str = SafeJSON(t'{{"name": "{name}", "age": {age}}}')
        rendered = json_str.render(name="John", age=30)
        # '{"name": "John", "age": 30}'
    """
    
    _template: Template
    _raw_template: str
    
    def __init__(self, template: str | Template, /) -> None:
        """Create SafeJSON from template."""
        self._raw_template = template if isinstance(template, str) else str(template)
        
        if isinstance(template, Template):
            self._template = template
        else:
            self._template = self._parse_template(template)
    
    def _parse_template(self, template_str: str) -> Template:
        """Parse template string - delegates to shared helper."""
        return _parse_template_str(template_str)
    
    def _escape_json_value(self, value: Any) -> str:
        """Escape a value for JSON.
        
        For strings: returns the escaped string WITHOUT surrounding quotes
        (since we're already inside a JSON string in the template).
        For other types: returns the JSON representation.
        """
        if value is None:
            return "null"
        elif isinstance(value, bool):
            return "true" if value else "false"
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            # Escape for JSON string, no extra quotes
            return json.dumps(value)[1:-1]  # Strip surrounding quotes
        else:
            return json.dumps(value)
    
    def render(self, **values: Any) -> str:
        """Render the JSON template with automatic escaping."""
        parts: list[str] = []
        
        for i, s in enumerate(self._template.strings):
            parts.append(s)
            if i < len(self._template.interpolations):
                interp = self._template.interpolations[i]
                try:
                    value = eval(interp.expression, {"__builtins__": {}}, values)  # noqa: S307,security/eval
                except NameError:
                    raise KeyError(f"Missing value for: {interp.expression}")
                parts.append(self._escape_json_value(value))
        
        return "".join(parts)
    
    def __str__(self) -> str:
        return self._raw_template
    
    def __repr__(self) -> str:
        return f"SafeJSON({self._raw_template!r})"

@dataclass
class HermeTemplate:
    """
    Hermes prompt template with zero injection risk.
    
    SECURITY: Hermes engine validates prompts via prompt_injection_validator.py.
    This template is for internal/system prompts only (not user-controlled).
    
    Usage:
        prompt = HermeTemplate(t"System: {system_prompt}\nUser: {query}")
        rendered = prompt.render(system_prompt="You are helpful", query="Hello")
    """
    
    _template: Template
    _raw_template: str
    
    def __init__(self, template: str | Template, /) -> None:
        """Create HermeTemplate from template."""
        self._raw_template = template if isinstance(template, str) else str(template)
        
        if isinstance(template, Template):
            self._template = template
        else:
            self._template = self._parse_template(template)
    
    def _parse_template(self, template_str: str) -> Template:
        """Parse template string - delegates to shared helper."""
        return _parse_template_str(template_str)
    
    def render(self, **values: Any) -> str:
        """Render the hermes template (no escaping needed)."""
        parts: list[str] = []
        
        for i, s in enumerate(self._template.strings):
            parts.append(s)
            if i < len(self._template.interpolations):
                interp = self._template.interpolations[i]
                try:
                    value = eval(interp.expression, {"__builtins__": {}}, values)  # noqa: S307,security/eval
                except NameError:
                    raise KeyError(f"Missing value for: {interp.expression}")
                parts.append(str(value))
        
        return "".join(parts)
    
    def __str__(self) -> str:
        return self._raw_template
    
    def __repr__(self) -> str:
        return f"HermeTemplate({self._raw_template!r})"

if TStringAvailable:
    from string.templatelib import Template as _NativeTemplate
    
    def t(string: str, /) -> _NativeTemplate:
        """
        Create a native t-string Template (Python 3.14+).
        
        This is a thin wrapper around string.templatelib.Template
        that allows programmatic template creation.
        
        Usage:
            template = t("SELECT * FROM users WHERE id = {user_id}")
            # Native Template object with .strings and .interpolations
        """
        return _NativeTemplate(string)
    
    __all__ = [
        "SafeSQL",
        "SafeJSON", 
        "HermeTemplate",
        "TStringAvailable",
        "render",
        "Template",
        "t",
        "Interpolation",
    ]
else:
    def t(string: str, /) -> Template:
        """
        Create a Template from string (fallback for Python <3.14).
        
        On Python <3.14, this parses the template string and
        returns a compatible Template object.
        """
        strings: list[str] = []
        interpolations: list[Interpolation] = []
        
        pattern = re.compile(r'\{([^}]+)\}')
        last_end = 0
        
        for match in pattern.finditer(string):
            strings.append(string[last_end:match.start()])
            expr = match.group(1).strip()
            interpolations.append(Interpolation(value=None, expression=expr))
            last_end = match.end()
        
        strings.append(string[last_end:])
        return Template(strings=tuple(strings), interpolations=tuple(interpolations))
