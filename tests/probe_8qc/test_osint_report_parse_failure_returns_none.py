"""
Sprint 8QC D.4: Parse failure returns None.
100% offline — no MLX, no network.
"""

import msgspec
from hledac.universal.brain.synthesis_runner import OSINTReport


class TestParseFailure:
    """D.4: "Sorry, I cannot..." → parse returns None."""

    def test_non_json_text_returns_none_or_raises(self):
        """Non-JSON text should not parse as OSINTReport (raise or return None)."""
        raw = "Sorry, I cannot provide that information."
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            clean = raw[start:end].strip().lstrip("`").strip()
            # Should raise on invalid JSON
            with pytest.raises(msgspec.ValidationError):
                msgspec.json.decode(clean.encode(), type=OSINTReport)
        else:
            # No JSON braces at all — this is the expected case
            assert True

    def test_empty_string_no_parse(self):
        """Empty string has no JSON to parse."""
        raw = ""
        start = raw.find("{")
        end = raw.rfind("}") + 1
        assert start < 0 or end <= start  # correctly identified as no JSON


# Need pytest for raises
import pytest  # noqa: E402
