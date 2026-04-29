"""F206AA: Public branch diagnosis — isolated discovery + pipeline seam probe."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hledac.universal.discovery.duckduckgo_adapter import (
    DiscoveryBatchResult,
    DiscoveryHit,
)


FIXTURE_HITS = [
    DiscoveryHit(
        query="ransomware infrastructure leak",
        title="BlackLock Ransomware Infrastructure Compromised",
        url="https://redteamnews.com/threat-intelligence/apt-news/blacklock-ransomware-infrastructure-compromised-through-leak-site-vulnerability/",
        snippet="BlackLock ransomware infrastructure was compromised through a leak site vulnerability.",
        source="duckduckgo",
        rank=0,
        retrieved_ts=0.0,
        score=0.95,
        reason="exact_domain",
    ),
    DiscoveryHit(
        query="ransomware infrastructure leak",
        title="Ransomware Gang Leaked Data - Dark Web Report",
        url="https://example-onion сайт.com/hidden-page",
        snippet="Infrastructure details exposed.",
        source="duckduckgo",
        rank=1,
        retrieved_ts=0.0,
        score=0.88,
        reason="keyword_match",
    ),
]


@pytest.fixture
def mock_discovery_result():
    """Return a DiscoveryBatchResult with 2 fixture hits."""
    return DiscoveryBatchResult(
        hits=tuple(FIXTURE_HITS),
        error=None,
        fallback_triggered=None,
    )


@pytest.fixture
def mock_discovery_result_empty():
    """Return an empty DiscoveryBatchResult."""
    return DiscoveryBatchResult(hits=(), error=None, fallback_triggered=None)


@pytest.fixture
def mock_discovery_result_error():
    """Return a DiscoveryBatchResult with a backend error (F206AB taxonomy)."""
    return DiscoveryBatchResult(hits=(), error="rate_limited", fallback_triggered=None)


@pytest.fixture
def mock_async_fetch():
    """Mock async_fetch_public_text to return a successful fetch result."""
    mock_result = MagicMock()
    mock_result.url = "https://redteamnews.com/threat-intelligence/apt-news/blacklock-ransomware-infrastructure-compromised-through-leak-site-vulnerability/"
    mock_result.content_text = "<html><body>Ransomware infrastructure compromised through leak site vulnerability. Contact: admin@ransomware.com</body></html>"
    mock_result.fetched_bytes = 156
    mock_result.status_code = 200
    mock_result.error = None
    return AsyncMock(return_value=mock_result)
