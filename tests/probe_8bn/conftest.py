"""
Test fixtures for Sprint 8BN.
"""


import pytest

from hledac.universal.discovery.ti_feed_adapter import (
    CisaKevAdapter,
    NormalizedEntry,
    NvdApiAdapter,
    TIER_SURFACE,
    TIER_STRUCTURED_TI,
)
from hledac.universal.discovery.source_registry import (
    register_source_adapter,
    get_source_adapter,
    list_registered_source_types,
    source_quality_score,
)


@pytest.fixture
def nvd_adapter():
    """NVD API adapter instance."""
    return NvdApiAdapter()


@pytest.fixture
def cisa_kev_adapter():
    """CISA KEV adapter instance."""
    return CisaKevAdapter()


@pytest.fixture
def sample_nvd_entry():
    """Sample NVD NormalizedEntry for testing."""
    return NormalizedEntry(
        entry_hash="abc123",
        source_url="https://nvd.nist.gov/vuln/detail/CVE-2024-1234",
        title="CVE-2024-1234",
        body_text="Test vulnerability description. CVSS: 9.8",
        published_at=1700000000.0,
        source_type="nvd",
        raw_identifiers=("CVE-2024-1234",),
        source_tier=TIER_STRUCTURED_TI,
        rich_content_available=True,
    )


@pytest.fixture
def sample_cisa_kev_entry():
    """Sample CISA KEV NormalizedEntry for testing."""
    return NormalizedEntry(
        entry_hash="def456",
        source_url="https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
        title="CVE-2024-5678",
        body_text="CISA KEV vendorProject: TestVendor product: TestProduct",
        published_at=1700000000.0,
        source_type="cisa_kev",
        raw_identifiers=("CVE-2024-5678",),
        source_tier=TIER_STRUCTURED_TI,
        rich_content_available=False,
    )
