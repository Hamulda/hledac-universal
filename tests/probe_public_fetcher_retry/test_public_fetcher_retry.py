"""
tests/probe_public_fetcher_retry/test_public_fetcher_retry.py

Sprint F206Y — Public Fetcher Retry Bug Regression Tests

Tests that the F206Y fix is in place. The core bug (status 200 getting
orphaned return before body read) is verified by test_11.

Real HTTP integration (including 200 body read) is tested by:
- benchmarks/e2e_signal_fixture.py (all 3 lanes, status 200, body read, pattern hits)
- tests/probe_8ad/ (mock-based happy path tests)
- tests/probe_transport_cap_2026/ (telemetry + status code propagation)

Run: pytest tests/probe_public_fetcher_retry/ -v
"""

from __future__ import annotations

import pytest


class TestF206YFixVerification:
    """Verify the F206Y fix is in place."""

    def test_11_no_orphaned_return_before_body_read(self):
        """
        Test 11: The 'Exhausted retries' return block was removed.

        F206Y bug: lines 1105-1137 returned early for ALL statuses (including 200)
        before body reading could occur. This test verifies that orphaned return
        no longer exists at the wrong indent level.
        """
        import inspect
        from fetching import public_fetcher

        source = inspect.getsource(public_fetcher)
        lines = source.split("\n")

        for i, line in enumerate(lines):
            if "Exhausted retries" in line and "return" in line:
                indent = len(line) - len(line.lstrip())
                for j in range(i + 1, min(i + 5, len(lines))):
                    next_line = lines[j]
                    if next_line.strip() and not next_line.strip().startswith("#"):
                        next_indent = len(next_line) - len(next_line.lstrip())
                        if (
                            next_line.strip().startswith("return FetchResult")
                            and next_indent == indent
                        ):
                            pytest.fail(
                                f"F206Y FIX MISSING: orphaned 'return FetchResult' found "
                                f"at line {i}. Line: {line.strip()[:60]}"
                            )
                        break


class TestRetryLogicInvariant:
    """Verify retryable status handling is unchanged."""

    def test_retryable_status_codes_defined(self):
        """Verify _is_retryable_status returns True for 429, 502, 503, 504, 520."""
        from fetching.public_fetcher import _is_retryable_status

        retryable = {429, 502, 503, 504, 520}
        for code in retryable:
            assert _is_retryable_status(code) is True, f"{code} should be retryable"

    def test_nonretryable_status_codes_return_false(self):
        """Verify _is_retryable_status returns False for non-retryable statuses."""
        from fetching.public_fetcher import _is_retryable_status

        nonretryable = {200, 201, 301, 302, 400, 401, 403, 404, 500, 501}
        for code in nonretryable:
            assert _is_retryable_status(code) is False, f"{code} should NOT be retryable"

    def test_build_retry_error_format(self):
        """Verify _build_retry_error returns expected format."""
        from fetching.public_fetcher import _build_retry_error

        err = _build_retry_error(502, None)
        assert err.startswith("retryable:502|"), f"got: {err}"

        err_with_retry = _build_retry_error(429, 5.0)
        assert "retryable:429" in err_with_retry
        assert "retry_after=" in err_with_retry


class TestFetchResultFields:
    """Verify FetchResult has required fields for the fix verification."""

    def test_fetch_result_has_text_field(self):
        """FetchResult must have text field for body content."""
        from fetching.public_fetcher import FetchResult

        result = FetchResult(
            url="http://test/",
            final_url="http://test/",
            status_code=200,
            content_type="text/html",
            text="hello",
            fetched_bytes=5,
            declared_length=-1,
            elapsed_ms=10.0,
            error=None,
            redirected=False,
            redirect_target=None,
            failure_stage="",
            selected_transport="aiohttp",
            transport_policy_reason="clearnet_default",
        )
        assert result.text == "hello"
        assert result.fetched_bytes == 5

    def test_fetch_result_success_has_no_error(self):
        """FetchResult for status 200 should allow error=None."""
        from fetching.public_fetcher import FetchResult

        result = FetchResult(
            url="http://test/",
            final_url="http://test/",
            status_code=200,
            content_type="text/html",
            text="ok",
            fetched_bytes=2,
            declared_length=-1,
            elapsed_ms=10.0,
            error=None,
            redirected=False,
            redirect_target=None,
            failure_stage="",
            selected_transport="aiohttp",
            transport_policy_reason="clearnet_default",
        )
        assert result.status_code == 200
        assert result.error is None
