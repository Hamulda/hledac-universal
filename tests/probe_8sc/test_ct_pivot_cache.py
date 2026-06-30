"""Sprint 8SC: CT pivot cache hit."""

import tempfile
import time
from pathlib import Path

import pytest

from hledac.universal.intelligence.ct_log_client import CTLogClient


@pytest.mark.asyncio
async def test_ct_pivot_cache(tmp_path):
    """Cache file exists → pivot_domain() reads from cache, no HTTP."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    domain = "example.com"

    # Pre-write cache
    import orjson
    cache_path = cache_dir / f"{xxhash_int(domain)}.json"
    cached = {
        "domain": domain,
        "san_names": ["sub.example.com", "www.example.com"],
        "issuers": ["DigiCert"],
        "first_cert": 123456.0,
        "last_cert": 999999.0,
        "cert_count": 5,
    }
    cache_path.write_bytes(orjson.dumps(cached))

    ct = CTLogClient(cache_dir)

    # Use a mock session (won't be called due to cache)
    class FakeSession:
        pass

    result = await ct.pivot_domain(domain, FakeSession())

    assert result["domain"] == domain
    assert result["san_names"] == ["sub.example.com", "www.example.com"]
    assert result["cert_count"] == 5


def xxhash_int(s: str) -> str:
    import xxhash
    return xxhash.xxh64(s.encode()).hexdigest()
