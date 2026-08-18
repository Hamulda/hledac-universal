"""Tests for utils/tstring_compat.py - PEP 750 t-string compatibility layer."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from utils.tstring_compat import (
    SafeSQL,
    SafeJSON,
    HermeTemplate,
    TStringAvailable,
    t,
    Template,
    Interpolation,
    render,
)

# Python version marker
IS_PYTHON_314 = sys.version_info >= (3, 14)


class TestSafeSQL:
    """Tests for SQL-safe template rendering."""

    def test_basic_escaping(self) -> None:
        """Test basic SQL escaping with single quotes."""
        sql = SafeSQL('SELECT * FROM users WHERE name = {name}')
        result = sql.render(name="O'Brien")
        assert result == "SELECT * FROM users WHERE name = 'O''Brien'"

    def test_numeric_passthrough(self) -> None:
        """Test that numeric values are passed through without quotes."""
        sql = SafeSQL('SELECT * FROM users WHERE id = {user_id}')
        result = sql.render(user_id=42)
        assert result == "SELECT * FROM users WHERE id = 42"

    def test_mixed_types(self) -> None:
        """Test SQL with mixed string and numeric parameters."""
        sql = SafeSQL(
            'SELECT * FROM users WHERE name = {name} AND id = {user_id} AND active = {active}'
        )
        result = sql.render(name="Alice", user_id=123, active=True)
        assert "'Alice'" in result
        assert "123" in result
        assert "TRUE" in result

    def test_none_value(self) -> None:
        """Test NULL handling for SQL."""
        sql = SafeSQL('SELECT * FROM users WHERE deleted = {deleted}')
        result = sql.render(deleted=None)
        assert result == "SELECT * FROM users WHERE deleted = NULL"

    def test_boolean_values(self) -> None:
        """Test boolean handling."""
        sql = SafeSQL('SELECT * FROM x WHERE flag = {flag}')
        assert sql.render(flag=True) == "SELECT * FROM x WHERE flag = TRUE"
        assert sql.render(flag=False) == "SELECT * FROM x WHERE flag = FALSE"

    def test_injection_prevention(self) -> None:
        """Test SQL injection prevention."""
        sql = SafeSQL('SELECT * FROM users WHERE name = {name}')
        malicious = "'; DROP TABLE users; --"
        result = sql.render(name=malicious)
        # Single quotes should be escaped
        assert "''" in result
        # Injection attempt should not create valid SQL
        assert "DROP" not in result.upper()

    def test_empty_string(self) -> None:
        """Test handling of empty strings."""
        sql = SafeSQL('SELECT * FROM users WHERE name = {name}')
        result = sql.render(name='')
        assert result == "SELECT * FROM users WHERE name = ''"

    def test_escaped_braces(self) -> None:
        """Test escaped braces in SQL."""
        sql = SafeSQL('SELECT * FROM {{schema}}.{table}')
        result = sql.render(table='users')
        assert result == "SELECT * FROM {schema}.'users'"
        assert "{schema}" in result  # Escaped braces should be literal

    def test_float_values(self) -> None:
        """Test float value handling."""
        sql = SafeSQL('SELECT * FROM x WHERE price = {price}')
        result = sql.render(price=19.99)
        assert result == "SELECT * FROM x WHERE price = 19.99"

    def test_multiple_quotes(self) -> None:
        """Test strings with multiple quotes."""
        sql = SafeSQL('SELECT * FROM users WHERE name = {name}')
        result = sql.render(name="O'Brien said 'hello'")
        assert "O''Brien" in result
        assert "'hello''" in result


class TestSafeJSON:
    """Tests for JSON-safe template rendering."""

    def test_basic_string_escaping(self) -> None:
        """Test basic JSON string escaping."""
        # Template: {{"name": "{name}"}}
        template = chr(123) + chr(123) + '"name": "' + chr(123) + 'name' + chr(125) + '"' + chr(125) + chr(125)
        json_t = SafeJSON(template)
        result = json_t.render(name='John')
        assert result == '{"Name": "John"}'

    def test_numeric_value(self) -> None:
        """Test numeric value in JSON (no quotes)."""
        # Template: {{"age": {age}}}
        template = chr(123) + chr(123) + '"age": ' + chr(123) + 'age' + chr(125) + chr(125)
        json_t = SafeJSON(template)
        result = json_t.render(age=30)
        assert result == '{"age": 30}'

    def test_boolean_values(self) -> None:
        """Test boolean values in JSON."""
        # Template: {{"active": {active}}}
        template = chr(123) + chr(123) + '"active": ' + chr(123) + 'active' + chr(125) + chr(125)
        json_t = SafeJSON(template)
        assert json_t.render(active=True) == '{"active": true}'
        assert json_t.render(active=False) == '{"active": false}'

    def test_null_value(self) -> None:
        """Test null value in JSON."""
        # Template: {{"value": {val}}}
        template = chr(123) + chr(123) + '"value": ' + chr(123) + 'val' + chr(125) + chr(125)
        json_t = SafeJSON(template)
        result = json_t.render(val=None)
        assert result == '{"value": null}'

    def test_special_characters(self) -> None:
        """Test escaping of special characters in JSON."""
        # Template: {{"msg": "{msg}"}}
        template = chr(123) + chr(123) + '"msg": "' + chr(123) + 'msg' + chr(125) + '"' + chr(125) + chr(125)
        json_t = SafeJSON(template)
        result = json_t.render(msg='He said: "hello"')
        assert '\\\\"' in result or '\\"' in result  # Escaped quotes

    def test_nested_braces(self) -> None:
        """Test nested braces in JSON."""
        # Template: {{"data": {{"inner": "{value}"}}}}
        inner = chr(123) + chr(123) + '"inner": "' + chr(123) + 'value' + chr(125) + '"' + chr(125) + chr(125)
        outer = chr(123) + chr(123) + '"data": ' + inner + chr(125) + chr(125)
        json_t = SafeJSON(outer)
        result = json_t.render(value='test')
        assert '{"data": {"inner": "test"}}' in result or result.count('{') >= 3


class TestHermeTemplate:
    """Tests for HermeTemplate (no escaping, for prompts)."""

    def test_basic_prompt(self) -> None:
        """Test basic prompt rendering."""
        prompt = HermeTemplate('System: {system}\nUser: {query}')
        result = prompt.render(system='You are helpful', query='Hello')
        assert 'You are helpful' in result
        assert 'Hello' in result

    def test_no_escaping(self) -> None:
        """Test that content is NOT escaped (for prompts)."""
        prompt = HermeTemplate('User: {input}')
        result = prompt.render(input='<script>alert(1)</script>')
        # Content should be passed through as-is
        assert '<script>alert(1)</script>' in result

    def test_multiple_interpolations(self) -> None:
        """Test multiple interpolations."""
        prompt = HermeTemplate('{greeting}, {name}!')
        result = prompt.render(greeting='Hello', name='World')
        assert result == 'Hello, World!'


class TestTemplate:
    """Tests for the base Template class."""

    def test_basic_template(self) -> None:
        """Test basic template rendering."""
        strings = ('Hello ', '', '!')
        interpolations = (
            Interpolation(value='World', expression='name'),
            Interpolation(value=42, expression='count'),
        )
        template = Template(strings=strings, interpolations=interpolations)
        result = render(template, name='World', count=42)
        assert result == 'Hello World42!'

    def test_format_spec(self) -> None:
        """Test format specification."""
        interp = Interpolation(value=3.14159, expression='pi', format_spec='.2f')
        assert interp.resolve() == '3.14'

    def test_conversion_flags(self) -> None:
        """Test conversion flags."""
        # Test repr conversion
        interp = Interpolation(value=[1, 2, 3], expression='lst', conversion='r')
        result = interp.resolve()
        assert '[' in result  # repr of list


class TestRender:
    """Tests for the render function."""

    def test_render_with_kwargs(self) -> None:
        """Test render with keyword arguments."""
        strings = ('A', 'B', 'C')
        interpolations = (
            Interpolation(value=None, expression='x'),
            Interpolation(value=None, expression='y'),
        )
        template = Template(strings=strings, interpolations=interpolations)
        result = render(template, x='1', y='2')
        assert result == 'A1B2C'


class TestPython314TString:
    """Tests for native Python 3.14+ t-string support."""

    def test_tstring_available(self) -> None:
        """Test TStringAvailable flag."""
        assert isinstance(TStringAvailable, bool)

    @pytest.mark.skipif(not IS_PYTHON_314, reason='Requires Python 3.14+')
    def test_native_t_function(self) -> None:
        """Test native t() function on Python 3.14+."""
        template = t('SELECT * FROM x WHERE id = {user_id}')
        assert hasattr(template, 'strings')
        assert hasattr(template, 'interpolations')

    @pytest.mark.skipif(IS_PYTHON_314, reason='Python < 3.14')
    def test_fallback_behavior(self) -> None:
        """Test fallback on Python < 3.14."""
        # On Python < 3.14, t() should raise an error
        with pytest.raises(NotImplementedError):
            t('SELECT * FROM x')
