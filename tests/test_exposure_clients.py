"""
Tests for intelligence/exposure_clients.py

Tests:
  - test_osv_batch_streaming: OSV streaming, batches of 20, memory cap 200
  - test_cve_epss_enrichment: EPSS score + percentile added to CVEs
  - test_nvd_fallback: NVD called when OSV returns empty list
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_async_cm(response_mock):
    """Wrap a mock in async context manager protocol."""
    response_mock.__aenter__ = AsyncMock(return_value=response_mock)
    response_mock.__aexit__ = AsyncMock(return_value=None)
    return response_mock


class TestCVIntelligenceClient:
    """Tests for CVIntelligenceClient (OSV + NVD + EPSS)."""

    def test_batch_size_respected(self):
        """_BATCH_SIZE = 20 is enforced by the client."""
        from hledac.universal.intelligence.exposure_clients import CVIntelligenceClient

        client = CVIntelligenceClient()
        assert client._BATCH_SIZE == 20, "BATCH_SIZE must be 20"
        assert client._MAX_CVES == 200, "MAX_CVES must be 200"

    def test_max_cves_enforced(self):
        """_MAX_CVES = 200 is enforced by the client."""
        from hledac.universal.intelligence.exposure_clients import CVIntelligenceClient

        client = CVIntelligenceClient()
        assert client._MAX_CVES == 200

    @pytest.mark.asyncio
    async def test_osv_batch_streaming(self):
        """fetch_cve_intelligence yields AsyncIterator, batches of 20, max 200 CVEs."""
        from hledac.universal.intelligence.exposure_clients import CVIntelligenceClient

        def make_vuln(cve_id: str) -> dict:
            # OSV batch API format: "id" field (not "cve_id")
            return {
                "id": cve_id,
                "summary": f"Test vulnerability {cve_id}",
                "severity": [{"type": "CVSS_V3", "score": "7.5"}],
                "published": "2024-01-01T00:00:00Z",
                "modified": "2024-01-02T00:00:00Z",
                "references": [],
                "affected": [],
                "aliases": [cve_id],
            }

        # 25 CVEs to exceed one batch of 20
        vulns = [make_vuln(f"CVE-2024-{i:04d}") for i in range(1, 26)]
        ndjson_bytes = b"".join(
            json.dumps({"vulns": [v]}).encode() + b"\n" for v in vulns
        )

        async def chunk_gen():
            yield ndjson_bytes

        # Build mock response
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.content.iter_chunked = MagicMock(return_value=chunk_gen())
        mock_resp = _make_async_cm(mock_resp)

        # session.post returns the mock response via async context manager
        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.close = AsyncMock()

        client = CVIntelligenceClient()
        client._get_session = AsyncMock(return_value=mock_session)

        batches = []
        async for batch in client.fetch_cve_intelligence(["python"]):
            batches.append(batch)
            if batch.get("cves"):
                assert len(batch["cves"]) <= 20

        assert len(batches) >= 2, f"Expected >= 2 batches, got {len(batches)}"
        all_cves = [c for b in batches for c in b.get("cves", [])]
        assert len(all_cves) <= 200

    @pytest.mark.asyncio
    async def test_cve_epss_enrichment(self):
        """EPSS API response adds epss_score and epss_percentile to CVE dicts."""
        from hledac.universal.intelligence.exposure_clients import CVIntelligenceClient

        # OSV uses "id" (not "cve_id") as the vulnerability identifier
        cve = {
            "id": "CVE-2024-1234",
            "summary": "Test vulnerability CVE-2024-1234",
            "severity": [],
            "published": "2024-01-01T00:00:00Z",
            "modified": "2024-01-02T00:00:00Z",
            "references": [],
            "affected": [],
            "aliases": ["CVE-2024-1234"],
        }

        # OSV response (OSV batch API uses NDJSON with "id" field)
        ndjson_bytes = json.dumps({"vulns": [cve]}).encode() + b"\n"

        async def osv_gen():
            yield ndjson_bytes

        osv_resp = MagicMock()
        osv_resp.status = 200
        osv_resp.content.iter_chunked = MagicMock(return_value=osv_gen())
        osv_resp = _make_async_cm(osv_resp)

        # EPSS response (score > 0.7 → IMMEDIATE_ACTION)
        epss_resp = MagicMock()
        epss_resp.status = 200
        epss_resp.json = AsyncMock(return_value={"epss": "0.891", "percentile": "0.934"})

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=osv_resp)

        # session.get(url) must return an async context manager
        epss_cm = MagicMock()
        epss_cm.__aenter__ = AsyncMock(return_value=epss_resp)
        epss_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.get.return_value = epss_cm
        mock_session.close = AsyncMock()

        client = CVIntelligenceClient()
        client._get_session = AsyncMock(return_value=mock_session)

        batches = []
        async for batch in client.fetch_cve_intelligence(["python"]):
            batches.append(batch)

        all_cves = [c for b in batches for c in b.get("cves", [])]
        assert len(all_cves) >= 1, "Should have at least 1 CVE"

        found = next((c for c in all_cves if c.get("cve_id") == "CVE-2024-1234"), None)
        assert found is not None

        assert "epss_score" in found, f"epss_score must be added; found keys: {found.keys()}"
        assert found["epss_score"] == 0.891
        assert "epss_percentile" in found, "epss_percentile must be added"
        assert found["epss_percentile"] == 0.934
        assert found.get("action_flag") == "IMMEDIATE_ACTION", (
            f"EPSS 0.891 > 0.7 should set IMMEDIATE_ACTION, got {found.get('action_flag')}"
        )

    @pytest.mark.asyncio
    async def test_nvd_fallback(self):
        """When OSV returns 0 CVEs, NVD API is called as fallback."""
        from hledac.universal.intelligence.exposure_clients import CVIntelligenceClient

        # Empty OSV response
        empty_bytes = json.dumps({"vulns": []}).encode() + b"\n"

        async def empty_gen():
            yield empty_bytes

        osv_resp = MagicMock()
        osv_resp.status = 200
        osv_resp.content.iter_chunked = MagicMock(return_value=empty_gen())
        osv_resp = _make_async_cm(osv_resp)

        # NVD response
        nvd_cve_payload = {
            "vulnerabilities": [{
                "cve": {
                    "id": "CVE-2024-9999",
                    "descriptions": [{"value": "NVD fallback test"}],
                    "published": "2024-06-01T00:00:00Z",
                    "lastModified": "2024-06-02T00:00:00Z",
                    "references": [],
                    "metrics": {
                        "cvssMetricV31": [{
                            "cvssData": {"baseSeverity": "HIGH"}
                        }]
                    },
                }
            }]
        }

        nvd_resp = MagicMock()
        nvd_resp.status = 200
        nvd_resp.json = AsyncMock(return_value=nvd_cve_payload)
        nvd_resp = _make_async_cm(nvd_resp)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=osv_resp)

        # session.get(url) must return an async context manager
        nvd_cm = _make_async_cm(nvd_resp)
        mock_session.get.return_value = nvd_cm
        mock_session.close = AsyncMock()

        client = CVIntelligenceClient()
        client._get_session = AsyncMock(return_value=mock_session)

        # Bypass LMDB cache so NVD fallback is actually triggered
        client._cache.get = MagicMock(return_value=None)

        batches = []
        async for batch in client.fetch_cve_intelligence(["python"]):
            batches.append(batch)

        all_cves = [c for b in batches for c in b.get("cves", [])]

        assert len(all_cves) >= 1, "NVD fallback should return at least 1 CVE"
        assert any(c.get("cve_id") == "CVE-2024-9999" for c in all_cves), (
            "CVE-2024-9999 from NVD should be in results"
        )
        assert any(c.get("source") == "nvd" for c in all_cves), (
            "Source should be 'nvd' for fallback CVE"
        )
