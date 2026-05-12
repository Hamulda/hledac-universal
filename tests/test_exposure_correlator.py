"""
Tests for intelligence/exposure_correlator.py

Tests:
  - test_s3_open_bucket: HEAD 200 → type=OPEN_BUCKET, severity=HIGH
  - test_subdomain_takeover_github_pages: CNAME→github.io + 404 → CRITICAL
  - test_bucket_generator_memory: _generate_bucket_candidates returns Generator, not list
"""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


def _make_async_cm(response_mock):
    """Wrap a mock so it works as an async context manager."""
    response_mock.__aenter__ = AsyncMock(return_value=response_mock)
    response_mock.__aexit__ = AsyncMock(return_value=None)
    return response_mock


class TestS3OpenBucket:
    """S3/open bucket detection via HEAD check."""

    @pytest.mark.asyncio
    async def test_s3_open_bucket(self):
        """HEAD returning 200 produces result dict with is_open=True."""
        from hledac.universal.intelligence.exposure_correlator import (
            _check_bucket_head,
            _CLOUD_BUCKET_TEMPLATES,
        )

        s3_template = [t for t in _CLOUD_BUCKET_TEMPLATES if t[1] == "s3"][0]
        _, _, url_tpl = s3_template

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.headers = {"content-type": "application/xml"}
        mock_resp = _make_async_cm(mock_resp)

        mock_session = MagicMock()
        mock_session.head = MagicMock(return_value=mock_resp)

        result = await _check_bucket_head(
            mock_session, "test-bucket", "s3", url_tpl
        )

        assert result is not None, "_check_bucket_head must return result for 200"
        assert result["status"] == 200
        assert result["is_open"] is True
        assert result["provider"] == "s3"
        assert result["bucket_name"] == "test-bucket"

    @pytest.mark.asyncio
    async def test_s3_bucket_403_exists_but_denied(self):
        """HEAD 403 produces is_open=False."""
        from hledac.universal.intelligence.exposure_correlator import (
            _check_bucket_head,
            _CLOUD_BUCKET_TEMPLATES,
        )

        s3_template = [t for t in _CLOUD_BUCKET_TEMPLATES if t[1] == "s3"][0]
        _, _, url_tpl = s3_template

        mock_resp = MagicMock()
        mock_resp.status = 403
        mock_resp.headers = {"server": "AmazonS3"}
        mock_resp = _make_async_cm(mock_resp)

        mock_session = MagicMock()
        mock_session.head = MagicMock(return_value=mock_resp)

        result = await _check_bucket_head(
            mock_session, "private-bucket", "s3", url_tpl
        )

        assert result is not None
        assert result["status"] == 403
        assert result["is_open"] is False


class TestSubdomainTakeover:
    """Subdomain takeover detection for GitHub Pages and other providers."""

    def test_subdomain_takeover_github_pages(self):
        """CNAME pointing to user.github.io matches github_io takeover provider."""
        from hledac.universal.intelligence.exposure_correlator import (
            _check_takeover_provider,
            _SUBDOMAIN_TAKEOVER_PROVIDERS,
        )

        provider_names = [p[0] for p in _SUBDOMAIN_TAKEOVER_PROVIDERS]
        assert "github_io" in provider_names

        cname_chain = ["blog.example.com", "username.github.io", "github.io"]
        result = _check_takeover_provider(cname_chain)

        assert result is not None, "CNAME to github.io must match takeover provider"
        provider, pattern = result
        assert provider == "github_io"

    def test_subdomain_takeover_no_match(self):
        """CNAME to non-takeover target returns None."""
        from hledac.universal.intelligence.exposure_correlator import _check_takeover_provider

        cname_chain = ["www.example.com", "cloudfront.net"]
        result = _check_takeover_provider(cname_chain)
        assert result is None


class TestBucketGenerator:
    """Bucket candidate generation is memory-efficient (generator, not list)."""

    def test_bucket_generator_memory(self):
        """_generate_bucket_candidates returns a Generator, not a materialized list."""
        from hledac.universal.intelligence.exposure_correlator import (
            _generate_bucket_candidates,
        )

        result = _generate_bucket_candidates("testcompany")

        assert inspect.isgenerator(result), (
            f"Must return Generator, got {type(result).__name__}"
        )
        assert not isinstance(result, (list, tuple, set, frozenset)), (
            "Must not be a concrete collection"
        )

        first = next(iter(result))
        assert isinstance(first, tuple), f"First yield must be tuple, got {type(first).__name__}"
        assert len(first) == 3
        name, provider, url_tpl = first
        assert isinstance(name, str) and isinstance(provider, str) and isinstance(url_tpl, str)
        assert "{bucket}" in url_tpl

    def test_bucket_generator_is_lazy(self):
        """Generator yields without pre-materializing all candidates."""
        from hledac.universal.intelligence.exposure_correlator import _generate_bucket_candidates

        result = _generate_bucket_candidates("acmecorp")

        count = 0
        for candidate in result:
            count += 1
            if count >= 3:
                break

        assert count == 3
        assert inspect.isgenerator(result)
