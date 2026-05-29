"""
Tests for network/passive_fingerprint.py and intelligence/passive_fingerprint.py

Tests:
  - test_cloudflare_detection: cf-ray / cf-cache-status headers → cloud_provider="Cloudflare"
  - test_waf_confidence_scoring: Imperva incap_ses → waf_detected + waf_confidence > 0.8
  - test_ahocorasick_fallback: ImportError for ahocorasick → regex fallback still detects CMS
"""

from unittest.mock import patch


class TestCloudflareDetection:
    """Cloudflare identification via cf-ray and cf-cache-status headers."""

    def test_cloudflare_detection(self):
        """Headers with cf-ray and cf-cache-status produce cloud_provider='Cloudflare'."""
        from hledac.universal.intelligence.passive_fingerprint import _extract_tech_stack

        headers = {
            "cf-ray": "abc123def456-fra",
            "cf-cache-status": "HIT",
            "server": "nginx",
        }
        html_head = "<html><head></head><body>Test</body></html>"
        cookies = []

        result = _extract_tech_stack(headers, html_head, cookies)

        # cf-ray sets cdn_provider (not cloud_provider, which is for AWS/GCP/Azure headers)
        assert result.cdn_provider == "Cloudflare", (
            f"Expected cdn_provider='Cloudflare', got '{result.cdn_provider}'"
        )
        assert "cf-ray" in result.raw_signals, "raw_signals should contain cf-ray"

    def test_cloudflare_no_false_positive(self):
        """Non-Cloudflare CDN headers should not set cloud_provider to Cloudflare."""
        from hledac.universal.intelligence.passive_fingerprint import _extract_tech_stack

        headers = {
            "server": "nginx",
            "x-cache": "Hits 123",
        }
        html_head = "<html><head></head></html>"
        cookies = []

        result = _extract_tech_stack(headers, html_head, cookies)

        # Should not be Cloudflare since there's no cf-ray
        assert result.cloud_provider != "Cloudflare"


class TestWAFConfidenceScoring:
    """WAF detection and confidence scoring via headers and cookies."""

    def test_imperva_incap_ses_cookie(self):
        """Imperva incap_ses cookie sets waf_detected='Imperva' and waf_confidence > 0.8."""
        from hledac.universal.intelligence.passive_fingerprint import _extract_tech_stack

        headers = {"server": "nginx"}
        html_head = "<html><head></head></html>"
        cookies = ["incap_ses_123_456=abc; visid_incap_123456=xyz"]

        result = _extract_tech_stack(headers, html_head, cookies)

        assert result.waf_detected == "Imperva", (
            f"Expected waf_detected='Imperva', got '{result.waf_detected}'"
        )
        assert result.waf_confidence > 0.8, (
            f"Expected waf_confidence > 0.8, got {result.waf_confidence}"
        )
        assert "imperva" in result.raw_signals.get("waf_signal", "").lower()

    def test_cloudflare_waf_403(self):
        """Cloudflare 403 + cf-ray + error page sets waf_confidence = 0.95."""
        from hledac.universal.intelligence.passive_fingerprint import _extract_tech_stack

        headers = {
            "cf-ray": "abc123-fra",
            "status": "403",
            ":status": "403",
        }
        html_head = (
            "<html><head></head><body>"
            "Access denied. Error 1020 checking your browser.</body></html>"
        )
        cookies = []

        result = _extract_tech_stack(headers, html_head, cookies)

        assert result.waf_detected == "Cloudflare WAF", (
            f"Expected waf_detected='Cloudflare WAF', got '{result.waf_detected}'"
        )
        assert result.waf_confidence == 0.95, (
            f"Expected waf_confidence=0.95, got {result.waf_confidence}"
        )

    def test_aws_waf_cookie(self):
        """aws-waf-request header sets waf_detected='AWS WAF'."""
        from hledac.universal.intelligence.passive_fingerprint import _extract_tech_stack

        headers = {"aws-waf-request": "token123", "server": "nginx"}
        html_head = "<html><head></head></html>"
        cookies = []

        result = _extract_tech_stack(headers, html_head, cookies)

        assert result.waf_detected == "AWS WAF", (
            f"Expected waf_detected='AWS WAF', got '{result.waf_detected}'"
        )
        assert result.waf_confidence == 0.85, (
            f"Expected waf_confidence=0.85, got {result.waf_confidence}"
        )


class TestAhoCorasickFallback:
    """CMS detection via ahocorasick when available and regex fallback when not."""

    def test_ahocorasick_fallback(self):
        """When ahocorasick import fails, regex fallback detects CMS."""
        from hledac.universal.intelligence.passive_fingerprint import _extract_tech_stack

        headers = {"server": "nginx"}
        html_head = (
            "<html><head>"
            '<meta name="generator" content="WordPress 6.4">'
            "</head></html>"
        )
        cookies = []

        # Patch ahocorasick import to raise ImportError
        import builtins
        original_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "ahocorasick":
                raise ImportError("No module named 'ahocorasick'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=fake_import):
            result = _extract_tech_stack(headers, html_head, cookies)

        assert result.cms is not None, "CMS must be detected via regex fallback"
        assert result.cms.lower() == "wordpress", (
            f"Expected cms='WordPress', got '{result.cms}'"
        )
        assert "cms_regex" in result.raw_signals, (
            "raw_signals should contain cms_regex from regex fallback"
        )

    def test_ahocorasick_available_detects_wordpress(self):
        """When ahocorasick is available, it detects CMS in HTML."""
        from hledac.universal.intelligence.passive_fingerprint import _extract_tech_stack

        headers = {"server": "nginx"}
        html_head = (
            "<html><head>"
            '<meta name="generator" content="WordPress 6.4.2">'
            "</head></html>"
        )
        cookies = []

        # ahocorasick is a real dependency in this env, so test without mocking
        result = _extract_tech_stack(headers, html_head, cookies)

        assert result.cms is not None, "CMS must be detected"
        # Both ahocorasick and regex can detect it
        has_cms_signal = (
            "cms_ahocorasick" in result.raw_signals
            or "cms_regex" in result.raw_signals
        )
        assert has_cms_signal, "raw_signals must contain CMS detection signal"
