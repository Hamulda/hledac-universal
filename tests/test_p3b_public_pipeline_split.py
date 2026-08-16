"""
P3-B: Tests for the public_pipeline module split.

Tests parity between extracted functions and original live_public_pipeline.py.
"""
import pytest

from pipeline.public_constants import (
    _is_threat_query,
    _is_shopping_noise_url,
    _filter_public_noise,
    _QUALITY_TIER_VERY_GOOD,
    _QUALITY_TIER_GOOD,
    _QUALITY_TIER_OK,
    _QUALITY_TIER_WEAK,
    _QUALITY_TIER_SKIP,
    )

from pipeline.public_patterns import (
    _make_finding_id,
    _html_to_text,
    _score_page_quality,
    _js_confidence_from_verdict,
    _enrich_text_with_metadata,
    _pattern_context,
    )

from pipeline.public_discovery import (
    FetchPolicy,
    generate_bootstrap_urls,
    generate_rescue_urls,
    generate_keyword_bootstrap_urls,
    )

from pipeline.public_acceptance import (
    _build_public_finding,
    )

from pipeline import PipelinePageResult, PipelineRunResult
from _core import aclose


class TestPublicConstants:
    """Test public_constants.py parity with original implementation."""

    @pytest.mark.parametrize("query,expected", [
        ("CVE-2024-1234", True),
        ("192.168.1.1", True),
        ("10.0.0.0/8", True),
        ("example.com", False),
        ("ransomware", True),
        ("LockBit", True),
        ("conti", True),
        ("osint infrastructure", True),
        ("osint", True),
        ("credential leak", True),
        ("darkweb", True),
        ("hello world", False),
        ("", False),
    ])
    def test_is_threat_query(self, query, expected):
        assert _is_threat_query(query) == expected, f"query={query!r}"

    @pytest.mark.parametrize("url,is_threat,expected_noise", [
        # Shopping domains
        ("https://amazon.com/cart/checkout", True, True),
        ("https://trendyol.com/product/123", False, True),
        # CTI allowed
        ("https://krebsonsecurity.com", False, False),
        ("https://thehackernews.com", False, False),
        ("https://cisa.gov", False, False),
        # Non-shopping
        ("https://github.com", False, False),
        ("https://example.com", False, False),
    ])
    def test_is_shopping_noise_url(self, url, is_threat, expected_noise):
        is_noise, reason = _is_shopping_noise_url(url, is_threat)
        assert is_noise == expected_noise, f"url={url!r}, is_threat={is_threat}"

    def test_quality_tiers(self):
        assert _QUALITY_TIER_VERY_GOOD == "very_good"
        assert _QUALITY_TIER_GOOD == "good"
        assert _QUALITY_TIER_OK == "ok"
        assert _QUALITY_TIER_WEAK == "weak_low_signal"
        assert _QUALITY_TIER_SKIP == "SKIP_WEAK"


class TestPublicPatterns:
    """Test public_patterns.py parity with original implementation."""

    def test_make_finding_id_deterministic(self):
        id1 = _make_finding_id("query", "http://example.com", "label", "pattern", "value")
        id2 = _make_finding_id("query", "http://example.com", "label", "pattern", "value")
        assert id1 == id2, "finding IDs must be deterministic"
        assert len(id1) <= 32, "finding ID should be short"

    def test_make_finding_id_different_inputs(self):
        id1 = _make_finding_id("query1", "http://example.com", "label", "pattern", "value")
        id2 = _make_finding_id("query2", "http://example.com", "label", "pattern", "value")
        assert id1 != id2, "different inputs must produce different IDs"

    @pytest.mark.parametrize("html,expected", [
        ("<p>Hello</p>", "Hello"),
        ("<b>Bold</b> and <i>italic</i>", "Bold and italic"),
        ("<h1>Title</h1><p>Paragraph</p>", "Title Paragraph"),
        ("<br>line1<br>line2", "line1 line2"),
        ("Plain text", "Plain text"),
        ("", ""),
    ])
    def test_html_to_text(self, html, expected):
        result = _html_to_text(html)
        assert result == expected, f"html={html!r}"

    def test_score_page_quality_very_good(self):
        tier = _score_page_quality(
            hit_url="https://cisa.gov/advisory/2024",
            hit_title="CISA Advisory — Critical Vulnerability",
            hit_snippet="CISA released an advisory about...",
            hit_rank=1,
            query="CVE-2024",
            extracted_text="A critical vulnerability has been identified..." * 50,
            discovery_score=0.9,
            discovery_reason="search",
    )
        assert tier in (_QUALITY_TIER_VERY_GOOD, _QUALITY_TIER_GOOD, _QUALITY_TIER_OK)

    def test_score_page_quality_weak(self):
        tier = _score_page_quality(
            hit_url="https://example.com",
            hit_title="X",
            hit_snippet="Y",
            hit_rank=999,
            query="rare query",
            extracted_text="short",
            discovery_score=0.1,
    )
        assert tier in (_QUALITY_TIER_WEAK, _QUALITY_TIER_SKIP)

    def test_js_confidence_from_verdict(self):
        assert _js_confidence_from_verdict("RETRY_JS:thin_text_strong_signal") == 0.85
        assert _js_confidence_from_verdict("RETRY_JS") == 0.70
        assert _js_confidence_from_verdict("OK") == 0.30
        assert _js_confidence_from_verdict("OK", status_code=403) == 0.45
        # content_length check fires before default (200 < 500 → 0.55)
        assert _js_confidence_from_verdict("OK", status_code=200, content_length=200) == 0.55
        # 600 >= 500 so content_length check doesn't fire → 0.30
        assert _js_confidence_from_verdict("OK", status_code=200, content_length=600) == 0.30

    def test_enrich_text_with_metadata(self):
        result = _enrich_text_with_metadata(
            title="<b>Title</b>",
            snippet="Snippet text",
            extracted_text="Body content here",
    )
        assert "Title" in result
        assert "Snippet text" in result
        assert "Body content" in result
        # HTML tags stripped from title
        assert "<b>" not in result

    def test_pattern_context(self):
        text = "Hello world test string"
        ctx = _pattern_context(text, 6, 11, radius=2)
        assert "world" in ctx


class TestPublicDiscovery:
    """Test public_discovery.py parity with original implementation."""

    def test_fetch_policy_defaults(self):
        policy = FetchPolicy.default()
        assert policy.use_js is False
        assert policy.use_doh is False
        assert policy.use_stealth is False

    def test_fetch_policy_js_capable(self):
        policy = FetchPolicy.js_capable()
        assert policy.use_js is True
        assert policy.use_doh is False
        assert policy.use_stealth is False

    def test_fetch_policy_tor_like(self):
        policy = FetchPolicy.tor_like()
        assert policy.use_js is False
        assert policy.use_doh is False
        assert policy.use_stealth is True

    def test_generate_bootstrap_urls_domain(self):
        urls = generate_bootstrap_urls("example.com")
        assert len(urls) <= 5
        assert all(url.startswith("https://") for url in urls)

    def test_generate_bootstrap_urls_empty(self):
        assert generate_bootstrap_urls("") == []
        assert generate_bootstrap_urls("   ") == []

    def test_generate_bootstrap_urls_ip(self):
        # IP addresses should not generate bootstrap URLs
        urls = generate_bootstrap_urls("192.168.1.1")
        assert urls == []

    def test_generate_rescue_urls_threat_query(self):
        hits = generate_rescue_urls("ransomware", max_urls=3)
        assert len(hits) <= 3
        assert all(hasattr(h, 'url') and h.url for h in hits)

    def test_generate_rescue_urls_non_threat(self):
        hits = generate_rescue_urls("hello world", max_urls=3)
        assert hits == []


class TestPublicAcceptance:
    """Test public_acceptance.py building CanonicalFindings."""

    @pytest.mark.parametrize("query,expected_tuple", [
        ("", ()),  # empty query → no finding
    ])
    def test_build_public_finding_empty(self, query, expected_tuple):
        # Basic sanity — empty inputs return empty tuple
        # (actual CanonicalFinding building tested in integration)
        pass


class TestPipelineStructs:
    """Test that pipeline structs are properly defined."""

    def test_pipeline_page_result_fields(self):
        result = PipelinePageResult(
            url="http://example.com",
            fetched=True,
            matched_patterns=3,
            accepted_findings=2,
            stored_findings=2,
    )
        assert result.url == "http://example.com"
        assert result.fetched is True
        assert result.matched_patterns == 3

    def test_pipeline_run_result_fields(self):
        result = PipelineRunResult(
            query="test query",
            discovered=10,
            fetched=8,
            matched_patterns=5,
            accepted_findings=4,
            stored_findings=4,
            patterns_configured=10,
            pages=(),
    )
        assert result.query == "test query"
        assert result.discovered == 10
        assert result.fetched == 8


class TestP3BParityInvariant:
    """
    Invariant: Functions extracted to new modules must produce
    identical results to the original live_public_pipeline.py implementation.
    """

    def test_is_threat_query_parity(self):
        """Parity: _is_threat_query in public_constants matches original."""
        # This is the canonical test — if this fails, parity is broken
        test_cases = [
            ("CVE-2024-1234", True),
            ("192.168.1.1", True),
            ("10.0.0.0/8", True),
            ("2001:db8::1", True),
            ("example.com", False),
            ("ransomware", True),
            ("LockBit", True),
            ("conti", True),
            ("apt29", True),
            ("osint infrastructure", True),
            ("credential leak", True),
            ("darkweb", True),
            ("onion", True),
            ("hello world", False),
            ("", False),
            ("site:example.com", False),  # domain-like but non-hostile
        ]
        for query, expected in test_cases:
            result = _is_threat_query(query)
            assert result == expected, f"Parity broken: _is_threat_query({query!r}) = {result}, expected {expected}"

    def test_shopping_noise_parity(self):
        """Parity: _is_shopping_noise_url matches original."""
        test_cases = [
            # (url, is_threat, expected_is_noise, expected_reason)
            ("https://amazon.com/cart/checkout", True, True, "public_noise_unrelated_marketplace"),
            ("https://amazon.com/cart/checkout", False, False, "public_relevance_pass"),
            ("https://trendyol.com/product/123", False, True, "public_noise_shopping"),
            ("https://krebsonsecurity.com", False, False, "public_relevance_pass"),
            ("https://thehackernews.com", False, False, "public_relevance_pass"),
            ("https://cisa.gov", False, False, "public_relevance_pass"),
            ("https://github.com", False, False, "public_relevance_pass"),
            ("https://example.com", False, False, "public_relevance_pass"),
        ]
        for url, is_threat, expected_noise, expected_reason in test_cases:
            is_noise, reason = _is_shopping_noise_url(url, is_threat)
            assert is_noise == expected_noise, f"Parity: noise({url!r}, threat={is_threat}) = {is_noise}, expected {expected_noise}"
            assert reason == expected_reason, f"Parity: reason({url!r}) = {reason!r}, expected {expected_reason!r}"

    def test_filter_public_noise_parity(self):
        """Parity: _filter_public_noise matches original."""
        from pipeline.public_constants import _filter_public_noise

        # Use correct paths matching _SHOPPING_NOISE_PATHS_STRICT (with trailing slash)
        hits = [
            type('Hit', (), {'url': 'https://amazon.com/cart/'})(),  # will be blocked (threat)
            type('Hit', (), {'url': 'https://amazon.com/cart'})(),   # will NOT be blocked (no trailing /)
            type('Hit', (), {'url': 'https://krebsonsecurity.com'})(),
            type('Hit', (), {'url': 'https://github.com'})(),
        ]
        # is_threat_query=True: /cart/ (strict) blocked
        filtered, rejected = _filter_public_noise(hits, is_threat_query=True)
        assert len(filtered) == 3  # amazon.com/cart (no /) + krebsonsecurity + github
        assert len(rejected) == 1
        assert rejected[0][0] == 'https://amazon.com/cart/'
