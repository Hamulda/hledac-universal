"""Sprint 8SC: CT pivot SAN parsing."""

import pytest

from hledac.universal.intelligence.ct_log_client import CTLogClient


def test_ct_pivot_parse_san():
    """_parse_crt_response() extracts SAN names correctly, excludes source."""
    ct = CTLogClient.__new__(CTLogClient)
    ct._cache_dir = None
    ct._last_request = 0.0

    raw = [
        {
            "name_value": "www.example.com\nsub.example.com\n*.wildcard.example.com",
            "issuer_name": "CN=DigiCert, O=DigiCert Inc, C=US",
            "not_before": "2024-01-01 00:00:00",
        },
        {
            "name_value": "mail.example.com",
            "issuer_name": "CN=Let's Encrypt, O=Let's Encrypt, C=US",
            "not_before": "2024-06-01 00:00:00",
        },
    ]

    result = ct._parse_crt_response("example.com", raw)

    assert result["domain"] == "example.com"
    assert "www.example.com" in result["san_names"]
    assert "sub.example.com" in result["san_names"]
    assert "mail.example.com" in result["san_names"]
    # Source domain excluded
    assert "example.com" not in result["san_names"]
    assert "wildcard.example.com" in result["san_names"]
    # Issuers
    assert "DigiCert" in result["issuers"]
    assert "Let's Encrypt" in result["issuers"]
    # Timestamps
    assert result["first_cert"] > 0
    assert result["last_cert"] > 0
    assert result["cert_count"] == 2
