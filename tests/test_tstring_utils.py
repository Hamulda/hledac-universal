"""
Tests for utils/tstring.py (PEP 750 t-string utilities)
"""

import logging
from string.templatelib import Template

from utils.tstring import convert, render, t


class TestRender:
    """Test render() function."""

    def test_basic_render(self) -> None:
        """Basic interpolation renders correctly."""
        sprint_id = "sprint-123"
        count = 42
        template = t"Sprint {sprint_id} completed with {count} findings"
        result = render(template)
        assert result == "Sprint sprint-123 completed with 42 findings"

    def test_render_with_repr_conversion(self) -> None:
        """Repr conversion (!r) works."""
        name = "Alice"
        template = t"Name: {name!r}"
        result = render(template)
        assert result == "Name: 'Alice'"

    def test_render_with_str_conversion(self) -> None:
        """Str conversion (!s) works."""
        value = 42
        template = t"Value: {value!s}"
        result = render(template)
        assert result == "Value: 42"

    def test_render_with_format_spec(self) -> None:
        """Format spec (:0.2f) works."""
        value = 3.14159
        template = t"Pi: {value:.2f}"
        result = render(template)
        assert result == "Pi: 3.14"

    def test_render_with_int_format(self) -> None:
        """Integer formatting works."""
        value = 42
        template = t"Value: {value:05d}"
        result = render(template)
        assert result == "Value: 00042"

    def test_render_multiple_interpolations(self) -> None:
        """Multiple interpolations render in order."""
        a = "hello"
        b = 123
        c = 3.14
        template = t"{a} / {b} / {c:.1f}"
        result = render(template)
        assert result == "hello / 123 / 3.1"

    def test_render_single_literal(self) -> None:
        """Template with no interpolations returns literal."""
        template = t"No interpolations"
        result = render(template)
        assert result == "No interpolations"


class TestConvert:
    """Test convert() helper."""

    def test_convert_repr(self) -> None:
        """Repr conversion."""
        result = convert("hello", "r")
        assert result == "'hello'"

    def test_convert_str(self) -> None:
        """Str conversion."""
        result = convert(123, "s")
        assert result == "123"

    def test_convert_ascii(self) -> None:
        """Ascii conversion."""
        result = convert("héllo", "a")
        assert result == "'h\\xe9llo'"

    def test_convert_none(self) -> None:
        """No conversion returns value unchanged."""
        result = convert(42, None)
        assert result == 42


class TestTFunction:
    """Test t() factory function."""

    def test_t_creates_template(self) -> None:
        """t() creates a Template object."""
        template = t("Hello {name}")
        assert isinstance(template, Template)

    def test_t_renderable(self) -> None:
        """Template from t() is renderable (manual interpolation)."""
        # t() is a passthrough - Template stores literal braces
        # The native t"..." syntax evaluates interpolations at parse time
        template = t("Hello {name}")  # literal braces, no evaluation
        assert isinstance(template, Template)
        # t() doesn't do interpolation - that requires native t"..." syntax
        assert "{name}" in template.strings[0]


class TestLoggingIntegration:
    """Test t-string integration with logging."""

    def test_logger_accepts_rendered_template(self) -> None:
        """Logger accepts render(t'...') output."""
        sprint_id = "sprint-123"
        count = 42

        captured: list[str] = []

        class CaptureHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record.getMessage())

        logger = logging.getLogger("test_logging")
        logger.addHandler(CaptureHandler())
        logger.setLevel(logging.INFO)

        logger.info(render(t"Sprint {sprint_id} started with {count} findings"))

        assert len(captured) == 1
        assert captured[0] == "Sprint sprint-123 started with 42 findings"

    def test_nested_object_access(self) -> None:
        """Nested attribute access in interpolation."""

        class Config:
            name = "test-config"

        config = Config()
        template = t"Config name: {config.name}"
        result = render(template)
        assert result == "Config name: test-config"


class TestNativeTSyntax:
    """Test native t"..." syntax (Python 3.14+)."""

    def test_native_t_string_basic(self) -> None:
        """Native t"..." syntax works."""
        sprint_id = "my-sprint"
        # This is evaluated at parse time
        template = t"Sprint {sprint_id} started"
        result = render(template)
        assert result == "Sprint my-sprint started"

    def test_native_t_string_with_len(self) -> None:
        """Native t"..." with len() function."""
        items = [1, 2, 3]
        template = t"Count: {len(items)}"
        result = render(template)
        assert result == "Count: 3"

    def test_native_t_string_with_expression(self) -> None:
        """Native t"..." with expression in interpolation."""
        x = 10
        y = 20
        template = t"Sum: {x + y}"
        result = render(template)
        assert result == "Sum: 30"

    def test_native_t_string_with_format(self) -> None:
        """Native t"..." with format spec."""
        ratio = 0.756
        template = t"Ratio: {ratio:.1%}"
        result = render(template)
        assert result == "Ratio: 75.6%"
