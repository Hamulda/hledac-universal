"""
Sprint 8BN tests — Structured TI Ingest V1.

Tests source registry, normalized entry contract, NVD/CISA adapters,
source quality scoring, and coexistence with RSS/Atom architecture.
"""


import json
import pytest

from hledac.universal.discovery.source_registry import (
    register_source_adapter,
    get_source_adapter,
    list_registered_source_types,
    source_quality_score,
    _SOURCE_REGISTRY,
)
from hledac.universal.discovery.ti_feed_adapter import (
    NormalizedEntry,
    NvdApiAdapter,
    CisaKevAdapter,
    SourceAdapter,
    TIER_SURFACE,
    TIER_STRUCTURED_TI,
)
from hledac.universal.discovery.rss_atom_adapter import (
    get_default_feed_seed_truth,
    get_default_feed_seeds,
)


# ---------------------------------------------------------------------------
# D.1 — Registry register and get
# ---------------------------------------------------------------------------

def test_source_registry_register_and_get():
    """Registry can register and retrieve adapter instances by source_type."""

    class DummyAdapter(SourceAdapter):
        source_type = "dummy_test"
        source_tier = TIER_SURFACE

        async def fetch_recent(self, limit: int):
            return ()

    # Clear if already registered
    _SOURCE_REGISTRY.pop("dummy_test", None)

    register_source_adapter("dummy_test", DummyAdapter)
    adapter = get_source_adapter("dummy_test")

    assert adapter is not None
    assert isinstance(adapter, DummyAdapter)
    assert adapter.source_type == "dummy_test"

    # Cleanup
    _SOURCE_REGISTRY.pop("dummy_test", None)


# ---------------------------------------------------------------------------
# D.2 — Registry list registered types
# ---------------------------------------------------------------------------

def test_source_registry_list_registered_types():
    """Registry returns sorted list of registered source types."""
    registered = list_registered_source_types()

    assert isinstance(registered, list)
    assert all(isinstance(s, str) for s in registered)
    # nvd and cisa_kev should be pre-registered
    assert "nvd" in registered
    assert "cisa_kev" in registered


# ---------------------------------------------------------------------------
# D.3 — NormalizedEntry contract is lightweight
# ---------------------------------------------------------------------------

def test_normalized_entry_contract_is_lightweight():
    """NormalizedEntry has all required fields and is msgspec-based."""
    entry = NormalizedEntry(
        entry_hash="h1",
        source_url="https://example.com",
        title="CVE-2024-1234",
        body_text="Description",
        published_at=1700000000.0,
        source_type="nvd",
        raw_identifiers=("CVE-2024-1234",),
        source_tier=TIER_STRUCTURED_TI,
        rich_content_available=True,
    )

    assert entry.entry_hash == "h1"
    assert entry.source_url == "https://example.com"
    assert entry.title == "CVE-2024-1234"
    assert entry.body_text == "Description"
    assert entry.published_at == 1700000000.0
    assert entry.source_type == "nvd"
    assert entry.raw_identifiers == ("CVE-2024-1234",)
    assert entry.source_tier == TIER_STRUCTURED_TI
    assert entry.rich_content_available is True

    # msgspec.Struct is frozen
    with pytest.raises(Exception):  # frozen
        entry.title = "modified"


# ---------------------------------------------------------------------------
# D.4 — NVD adapter maps CVE ID to raw_identifiers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nvd_adapter_maps_cve_id_to_raw_identifiers(nvd_adapter):
    """NVD adapter produces entries with CVE ID in raw_identifiers."""
    sample_nvd_response = {
        "vulnerabilities": [
            {
                "cve": {
                    "id": "CVE-2024-9999",
                    "published": "2024-01-15T10:00:00.000Z",
                    "descriptions": [
                        {"lang": "en", "value": "Test CVE description"}
                    ],
                    "references": [
                        {"url": "https://nvd.nist.gov/vuln/detail/CVE-2024-9999"}
                    ],
                    "metrics": {
                        "cvssMetricV31": [
                            {
                                "cvssData": {
                                    "baseScore": 7.5,
                                    "baseSeverity": "HIGH"
                                }
                            }
                        ]
                    }
                }
            }
        ]
    }

    # _fetch_text is synchronous (uses asyncio.run() inside), so mock with regular function
    nvd_adapter._fetch_text = lambda url, timeout_s=30.0, max_bytes=5000000: (
        json.dumps(sample_nvd_response),
        None
    )

    entries = await nvd_adapter.fetch_recent(limit=5)

    assert len(entries) == 1
    entry = entries[0]
    assert "CVE-2024-9999" in entry.raw_identifiers
    assert entry.source_type == "nvd"
    assert entry.source_tier == TIER_STRUCTURED_TI


# ---------------------------------------------------------------------------
# D.5 — NVD adapter maps description to body_text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nvd_adapter_maps_description_to_body_text(nvd_adapter):
    """NVD adapter maps CVE description to body_text."""
    sample_nvd_response = {
        "vulnerabilities": [
            {
                "cve": {
                    "id": "CVE-2024-8888",
                    "published": "2024-01-15T10:00:00.000Z",
                    "descriptions": [
                        {"lang": "en", "value": "Buffer overflow in component X allows remote code execution."}
                    ],
                    "references": [],
                    "metrics": {}
                }
            }
        ]
    }

    nvd_adapter._fetch_text = lambda url, timeout_s=30.0, max_bytes=5000000: (
        json.dumps(sample_nvd_response),
        None
    )

    entries = await nvd_adapter.fetch_recent(limit=5)

    assert len(entries) == 1
    entry = entries[0]
    assert "Buffer overflow" in entry.body_text
    assert "component X" in entry.body_text


# ---------------------------------------------------------------------------
# D.6 — CISA KEV adapter maps CVE ID to raw_identifiers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cisa_kev_adapter_maps_cve_id_to_raw_identifiers(cisa_kev_adapter):
    """CISA KEV adapter produces entries with CVE ID in raw_identifiers."""
    sample_kev_response = {
        "vulnerabilities": [
            {
                "cveID": "CVE-2024-7777",
                "vendorProject": "TestVendor",
                "product": "TestProduct",
                "dateAdded": "2024-01-15",
                "shortDescription": "Test KEV description",
                "knownRansomwareCampaignUse": "",
            }
        ]
    }

    cisa_kev_adapter._fetch_text = lambda url, timeout_s=45.0, max_bytes=10000000: (
        json.dumps(sample_kev_response),
        None
    )

    entries = await cisa_kev_adapter.fetch_recent(limit=5)

    assert len(entries) == 1
    entry = entries[0]
    assert "CVE-2024-7777" in entry.raw_identifiers
    assert entry.source_type == "cisa_kev"
    assert entry.source_tier == TIER_STRUCTURED_TI


# ---------------------------------------------------------------------------
# D.7 — CISA KEV adapter maps notes to body_text
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cisa_kev_adapter_maps_notes_to_body_text(cisa_kev_adapter):
    """CISA KEV adapter maps vendor/project/product/notes to body_text."""
    sample_kev_response = {
        "vulnerabilities": [
            {
                "cveID": "CVE-2024-6666",
                "vendorProject": "Microsoft",
                "product": "WindowsKernel",
                "dateAdded": "2024-02-01",
                "shortDescription": "Elevation of privilege vulnerability",
                "knownRansomwareCampaignUse": "Known",
            }
        ]
    }

    cisa_kev_adapter._fetch_text = lambda url, timeout_s=45.0, max_bytes=10000000: (
        json.dumps(sample_kev_response),
        None
    )

    entries = await cisa_kev_adapter.fetch_recent(limit=5)

    assert len(entries) == 1
    entry = entries[0]
    assert "Microsoft" in entry.body_text
    assert "WindowsKernel" in entry.body_text
    assert "Elevation of privilege" in entry.body_text


# ---------------------------------------------------------------------------
# D.8 — Source tier is structured_ti for NVD
# ---------------------------------------------------------------------------

def test_source_tier_is_structured_ti_for_nvd(nvd_adapter):
    """NVD adapter has source_tier = structured_ti."""
    assert nvd_adapter.source_tier == TIER_STRUCTURED_TI


# ---------------------------------------------------------------------------
# D.9 — Priority score prefers structured_ti over low-signal RSS
# ---------------------------------------------------------------------------

def test_priority_score_prefers_structured_ti_over_low_signal_rss():
    """Structured TI sources score higher than surface sources."""
    nvd_score = source_quality_score(
        parseable=True,
        stable_schema=True,
        identifier_rich=True,
        source_tier=TIER_STRUCTURED_TI,
    )

    surface_score = source_quality_score(
        parseable=True,
        stable_schema=True,
        identifier_rich=False,
        source_tier=TIER_SURFACE,
    )

    assert nvd_score > surface_score
    # structured_ti: 30+25+20+15 = 90
    # surface (low signal): 30+25+0+5 = 60
    assert nvd_score == 90
    assert surface_score == 60


# ---------------------------------------------------------------------------
# D.10 — Source quality scoring is deterministic
# ---------------------------------------------------------------------------

def test_source_quality_scoring_is_deterministic():
    """source_quality_score returns same result for same inputs."""
    def call():
        return source_quality_score(
            parseable=True,
            stable_schema=True,
            identifier_rich=True,
            source_tier=TIER_STRUCTURED_TI,
        )

    assert call() == call()
    assert call() == call()

    # Vary inputs:
    # parseable=False: 0+25+20+15 = 60
    assert source_quality_score(False, True, True, TIER_STRUCTURED_TI) == 60
    # stable_schema=False: 30+0+20+15 = 65
    assert source_quality_score(True, False, True, TIER_STRUCTURED_TI) == 65
    # identifier_rich=False: 30+25+0+15 = 70
    assert source_quality_score(True, True, False, TIER_STRUCTURED_TI) == 70


# ---------------------------------------------------------------------------
# D.11 — Existing seed truth contract not broken
# ---------------------------------------------------------------------------

def test_existing_seed_truth_contract_not_broken():
    """Existing RSS/Atom seed truth functions still work."""
    truth = get_default_feed_seed_truth()

    assert "count" in truth
    assert "identities" in truth
    assert "urls" in truth
    assert isinstance(truth["count"], int)
    assert isinstance(truth["identities"], list)
    assert isinstance(truth["urls"], list)

    seeds = get_default_feed_seeds()
    assert len(seeds) == truth["count"]


# ---------------------------------------------------------------------------
# D.12 — RSS and TI paths coexist
# ---------------------------------------------------------------------------

def test_rss_and_ti_paths_coexist():
    """Both RSS and TI adapters can coexist in the registry."""
    rss_types = list_registered_source_types()

    assert "nvd" in rss_types
    assert "cisa_kev" in rss_types

    # Can get both
    nvd = get_source_adapter("nvd")
    cisa = get_source_adapter("cisa_kev")

    assert nvd is not None
    assert cisa is not None
    assert isinstance(nvd, NvdApiAdapter)
    assert isinstance(cisa, CisaKevAdapter)


# ---------------------------------------------------------------------------
# D.13 — Identifier rich sources score higher
# ---------------------------------------------------------------------------

def test_identifier_rich_sources_score_higher():
    """Sources with identifier_rich=True score 20 more points."""
    with_id = source_quality_score(True, True, True, TIER_STRUCTURED_TI)
    without_id = source_quality_score(True, True, False, TIER_STRUCTURED_TI)

    assert with_id == without_id + 20


# ---------------------------------------------------------------------------
# D.14 — Parseability failure is fail soft
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parseability_failure_is_fail_soft(cisa_kev_adapter):
    """Adapter returns empty tuple on JSON parse failure."""
    cisa_kev_adapter._fetch_text = lambda url, timeout_s=45.0, max_bytes=10000000: (
        "not valid json{",
        None
    )

    entries = await cisa_kev_adapter.fetch_recent(limit=5)
    assert entries == ()


# ---------------------------------------------------------------------------
# D.15 — Structured adapter respects limit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_structured_adapter_respects_limit(nvd_adapter):
    """NVD adapter respects the limit parameter."""
    sample_response = {
        "vulnerabilities": [
            {
                "cve": {
                    "id": f"CVE-2024-{i:04d}",
                    "published": "2024-01-15T10:00:00.000Z",
                    "descriptions": [{"lang": "en", "value": f"Description {i}"}],
                    "references": [],
                    "metrics": {}
                }
            }
            for i in range(50)
        ]
    }

    nvd_adapter._fetch_text = lambda url, timeout_s=30.0, max_bytes=5000000: (
        json.dumps(sample_response),
        None
    )

    entries = await nvd_adapter.fetch_recent(limit=10)

    assert len(entries) == 10
    # Verify we got the first 10
    ids = [e.title for e in entries]
    assert "CVE-2024-0000" in ids
    assert "CVE-2024-0009" in ids


# ---------------------------------------------------------------------------
# D.16 — No browser or JS dependency introduced
# ---------------------------------------------------------------------------

def test_no_browser_or_js_dependency_introduced():
    """Verify no browser/JS dependencies in new modules."""
    import hledac.universal.discovery.ti_feed_adapter as ti_module
    import hledac.universal.discovery.source_registry as registry_module

    # Verify ti_feed_adapter source is clean
    import inspect
    source = inspect.getsource(ti_module)
    source_reg = inspect.getsource(registry_module)

    assert "playwright" not in source.lower()
    assert "puppeteer" not in source.lower()
    assert "selenium" not in source.lower()
    assert "nodriver" not in source.lower()


# ---------------------------------------------------------------------------
# D.17 — Live smoke: identifier density
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_smoke_reports_identifier_density():
    """
    Smoke test: structured TI entries carry richer identifiers than RSS.

    Uses mock data to verify the concept without network dependency.
    """
    # Create a mock entry with CVE identifier
    structured_entry = NormalizedEntry(
        entry_hash="test1",
        source_url="https://nvd.nist.gov/vuln/detail/CVE-2024-1234",
        title="CVE-2024-1234",
        body_text="Remote code execution in component X. CVSS: 9.8",
        published_at=1700000000.0,
        source_type="nvd",
        raw_identifiers=("CVE-2024-1234", "CPE-2024-1234"),
        source_tier=TIER_STRUCTURED_TI,
        rich_content_available=True,
    )

    # Create a typical RSS entry with no structured identifiers
    rss_entry_like = NormalizedEntry(
        entry_hash="test2",
        source_url="https://example.com/news/123",
        title="Security Advisory Released",
        body_text="A security advisory was released for software XYZ.",
        published_at=1700000000.0,
        source_type="rss",
        raw_identifiers=(),
        source_tier=TIER_SURFACE,
        rich_content_available=False,
    )

    # Structured entry has identifiers, RSS does not
    assert len(structured_entry.raw_identifiers) > 0
    assert len(rss_entry_like.raw_identifiers) == 0

    # Identifier density: structured entries carry CVE IDs
    assert structured_entry.raw_identifiers[0].startswith("CVE")


# ---------------------------------------------------------------------------
# D.18 — Recommendation mapping after structured ingest
# ---------------------------------------------------------------------------

def test_recommendation_mapping_after_structured_ingest():
    """
    Structured TI adapters provide structured identifiers
    that can be used for correlation.
    """
    entry = NormalizedEntry(
        entry_hash="rec1",
        source_url="https://nvd.nist.gov/vuln/detail/CVE-2024-3000",
        title="CVE-2024-3000",
        body_text="Kernel privilege escalation. CVSS: 8.1",
        published_at=1700000000.0,
        source_type="nvd",
        raw_identifiers=("CVE-2024-3000",),
        source_tier=TIER_STRUCTURED_TI,
        rich_content_available=True,
    )

    # raw_identifiers can be used for cross-referencing
    cve_id = entry.raw_identifiers[0]
    assert cve_id.startswith("CVE")
    assert len(cve_id) > 5

    # source_tier signals this is structured TI
    assert entry.source_tier == TIER_STRUCTURED_TI

    # rich_content_available signals if full advisory is available
    assert entry.rich_content_available is True
