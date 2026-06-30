"""
Sprint 8TB probe tests — MalwareBazaarClient extract_iocs.
Sprint: 8TB
Area: MalwareBazaar Client
"""

from pathlib import Path

import pytest

from hledac.universal.intelligence.exposure_clients import MalwareBazaarClient


class TestMBExtractIocsAllFields:
    """extract_iocs returns all IOC types from MB response."""

    def test_extracts_sha256_md5_sha1(self):
        """sha256_hash, md5_hash, sha1_hash → correct types."""
        mb = MalwareBazaarClient(cache_dir=Path("/tmp"))
        resp = {
            "data": [
                {
                    "sha256_hash": "abc123",
                    "md5_hash": "def456",
                    "sha1_hash": "ghi789",
                }
            ]
        }
        result = mb.extract_iocs(resp)
        types = [t for _, t in result]
        values = [v for v, _ in result]
        assert ("abc123", "sha256") in result
        assert ("def456", "md5") in result
        assert ("ghi789", "sha1") in result

    def test_extracts_tags_as_malware_family(self):
        """tags list → (tag, 'malware_family') tuples."""
        mb = MalwareBazaarClient(cache_dir=Path("/tmp"))
        resp = {
            "data": [
                {
                    "sha256_hash": "abc",
                    "tags": ["lockbit", "ransomware", "xpack"],
                }
            ]
        }
        result = mb.extract_iocs(resp)
        families = [(v, t) for v, t in result if t == "malware_family"]
        assert ("lockbit", "malware_family") in families
        assert ("ransomware", "malware_family") in families
        assert ("xpack", "malware_family") in families

    def test_extracts_vendor_intel_c2_ips(self):
        """vendor_intel entries with ip field → (ip, 'ipv4') tuples."""
        mb = MalwareBazaarClient(cache_dir=Path("/tmp"))
        resp = {
            "data": [
                {
                    "sha256_hash": "abc",
                    "vendor_intel": {
                        "ANYRUN": {"ip": "1.2.3.4"},
                        "HYBRID": {"ip": "5.6.7.8", "country": "XX"},
                    },
                }
            ]
        }
        result = mb.extract_iocs(resp)
        ips = [(v, t) for v, t in result if t == "ipv4"]
        assert ("1.2.3.4", "ipv4") in ips
        assert ("5.6.7.8", "ipv4") in ips

    def test_empty_data_returns_empty(self):
        """mb_resp with no data → empty list."""
        mb = MalwareBazaarClient(cache_dir=Path("/tmp"))
        resp = {"query_status": "ok", "data": []}
        result = mb.extract_iocs(resp)
        assert result == []
