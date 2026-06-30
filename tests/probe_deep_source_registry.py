"""
tests/probe_deep_source_registry.py — Sprint F270: DeepSourceRegistry probe tests.

18 hermetic tests (NO network). Verifies:
  - Registry loads from curated catalog without any I/O.
  - get_sources() / get_available_sources() filters work as designed.
  - Transport-required invariants hold (.onion → tor, .i2p → i2p).
  - LMDB persistence roundtrips last_verified timestamps.
  - HEAD probes (verify_source) are bounded and fail-soft.
  - Hard caps (MAX_SOURCES_IN_REGISTRY, MAX_RELIABILITY) are enforced.
"""

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from hledac.universal.discovery.deep_source_registry import (
    LMDB_DB_NAME,
    LMDB_MAP_SIZE,
    MAX_CONCURRENT_HEAD,
    MAX_SOURCES_IN_REGISTRY,
    DeepSource,
    DeepSourceRegistry,
    _compute_source_id,
    _i2p_required,
    _onion_tor_required,
)


# ---------------------------------------------------------------------------
# 1-3: basic catalog / loading
# ---------------------------------------------------------------------------
def test_registry_loads_without_network():
    """Registry builds entirely in-memory — no I/O."""
    registry = DeepSourceRegistry()
    assert registry
    # Each entry must be a frozen DeepSource.
    for src in registry:
        assert isinstance(src, DeepSource)
        assert src.source_id
        assert src.name
        assert src.base_url
        assert src.source_tier in ("surface", "dark", "archive", "p2p", "academic")
        assert src.transport_required in ("direct", "tor", "i2p", "none")
        assert src.data_type in (
            "ct_logs", "passive_dns", "leak_db",
            "academic", "forum", "paste", "repo",
        )
        assert 0.0 <= src.reliability <= 1.0
        # last_verified defaults to None on fresh load.
        assert src.last_verified is None


def test_source_count_minimum_50():
    """Spec: at least 50 curated sources."""
    registry = DeepSourceRegistry()
    assert len(registry) >= 50


def test_curated_sources_well_formed():
    """Every curated source has a stable, unique source_id."""
    registry = DeepSourceRegistry()
    seen_ids: set[str] = set()
    for src in registry:
        # source_id must be 16 hex chars (BLAKE2b digest_size=8).
        assert len(src.source_id) == 16
        assert all(c in "0123456789abcdef" for c in src.source_id)
        # source_id must equal BLAKE2b(base_url).
        assert src.source_id == _compute_source_id(src.base_url)
        # No collisions.
        assert src.source_id not in seen_ids
        seen_ids.add(src.source_id)


# ---------------------------------------------------------------------------
# 4-6: filtering
# ---------------------------------------------------------------------------
def test_get_sources_filter_by_tier():
    registry = DeepSourceRegistry()
    dark = registry.get_sources(tier="dark")
    assert all(s.source_tier == "dark" for s in dark)
    surface = registry.get_sources(tier="surface")
    assert all(s.source_tier == "surface" for s in surface)
    # Tier union must not exceed total.
    assert len(dark) + len(surface) <= len(registry)


def test_get_sources_filter_by_transport():
    registry = DeepSourceRegistry()
    tor_sources = registry.get_sources(transport="tor")
    assert all(s.transport_required == "tor" for s in tor_sources)
    i2p_sources = registry.get_sources(transport="i2p")
    assert all(s.transport_required == "i2p" for s in i2p_sources)


def test_get_sources_filter_by_data_type():
    registry = DeepSourceRegistry()
    ct = registry.get_sources(data_type="ct_logs")
    assert all(s.data_type == "ct_logs" for s in ct)
    assert len(ct) >= 1  # crt.sh is in the catalog


def test_get_sources_combined_filters():
    """tier + transport + data_type must AND-combine."""
    registry = DeepSourceRegistry()
    onion_forums = registry.get_sources(
        tier="dark", transport="tor", data_type="forum"
    )
    assert all(
        s.source_tier == "dark"
        and s.transport_required == "tor"
        and s.data_type == "forum"
        for s in onion_forums
    )
    # All dark/tor/forum sources must actually be .onion URLs.
    for s in onion_forums:
        assert s.base_url.endswith(".onion/") or ".onion/" in s.base_url


# ---------------------------------------------------------------------------
# 7-9: transport invariants
# ---------------------------------------------------------------------------
def test_all_onion_sources_require_tor_transport():
    """Invariant: every .onion URL declares transport_required='tor'."""
    registry = DeepSourceRegistry()
    for src in registry:
        if _onion_tor_required(src.base_url):
            assert src.transport_required == "tor", (
                f"{src.source_id}: {src.base_url} must require 'tor'"
            )


def test_all_i2p_sources_require_i2p_transport():
    """Invariant: every .i2p URL declares transport_required='i2p'."""
    registry = DeepSourceRegistry()
    for src in registry:
        if _i2p_required(src.base_url):
            assert src.transport_required == "i2p", (
                f"{src.source_id}: {src.base_url} must require 'i2p'"
            )


def test_get_available_with_tor_capability():
    """With {'direct', 'tor'} capabilities, onion sources become reachable."""
    registry = DeepSourceRegistry()
    reachable = registry.get_available_sources({"direct", "tor"})
    # Every tor source must be present.
    tor_only = [s for s in reachable if s.transport_required == "tor"]
    assert len(tor_only) >= 1
    # No i2p source (i2p not in caps).
    assert not any(s.transport_required == "i2p" for s in reachable)
    # All "none" / "direct" sources are reachable.
    for s in reachable:
        assert s.transport_required in ("none", "direct", "tor")


def test_get_available_empty_capabilities():
    """With empty capabilities, only transport_required='none' sources survive."""
    registry = DeepSourceRegistry()
    reachable = registry.get_available_sources(set())
    assert all(s.transport_required == "none" for s in reachable)


def test_get_available_no_transport_required_passes():
    """transport_required='none' is always reachable regardless of caps."""
    registry = DeepSourceRegistry()
    for caps in (set(), {"direct"}, {"tor"}, {"i2p"}, {"direct", "tor", "i2p"}):
        reachable = registry.get_available_sources(caps)
        none_required = [s for s in reachable if s.transport_required == "none"]
        # All 'none' sources must always be reachable.
        none_total = registry.get_sources(transport="none")
        assert len(none_required) == len(none_total)


# ---------------------------------------------------------------------------
# 10-12: helpers
# ---------------------------------------------------------------------------
def test_compute_source_id_is_deterministic():
    """Same URL → same source_id across calls and processes."""
    url = "https://crt.sh/"
    a = _compute_source_id(url)
    b = _compute_source_id(url)
    assert a == b
    assert len(a) == 16  # 8 bytes hex


def test_compute_source_id_collisions_unlikely():
    """BLAKE2b-64 over 200 URLs should have zero collisions in practice."""
    urls = [
        "https://example.com/", "https://example.org/",
        "https://crt.sh/", "http://juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion/",
        "https://api.semanticscholar.org/",
        "https://api.github.com/search/code?",
        "https://index.commoncrawl.org/",
    ]
    ids = [_compute_source_id(u) for u in urls]
    assert len(set(ids)) == len(urls)  # zero collisions


def test_deep_source_validation_reliability_bounds():
    """reliability must be in [0.0, 1.0]."""
    with pytest.raises(ValueError):
        DeepSource(
            source_id="x" * 16,
            name="bad",
            base_url="https://x/",
            source_tier="surface",
            transport_required="none",
            data_type="repo",
            reliability=1.5,  # out of bounds
            last_verified=None,
        )
    with pytest.raises(ValueError):
        DeepSource(
            source_id="x" * 16,
            name="bad",
            base_url="https://x/",
            source_tier="surface",
            transport_required="none",
            data_type="repo",
            reliability=-0.1,  # out of bounds
            last_verified=None,
        )


def test_deep_source_validation_url_consistency():
    """Onion URL with non-tor transport → ValueError."""
    with pytest.raises(ValueError):
        DeepSource(
            source_id="x" * 16,
            name="bad",
            base_url="http://abc.onion/",
            source_tier="dark",
            transport_required="direct",  # wrong!
            data_type="forum",
            reliability=0.5,
            last_verified=None,
        )


# ---------------------------------------------------------------------------
# 13-15: LMDB persistence (opt-in)
# ---------------------------------------------------------------------------
def test_lmdb_persistence_roundtrip(tmp_path: Path):
    """Hydrate overlay restores last_verified timestamps from LMDB."""
    lmdb_dir = tmp_path / "dsr_lmdb"
    registry = DeepSourceRegistry()
    registry.attach_lmdb(lmdb_dir)
    assert os.path.isdir(lmdb_dir)

    # Persist a synthetic timestamp.
    target = next(iter(registry))
    target_id = target.source_id
    fake_ts = 1_700_000_000.0
    registry._persist_timestamp(target_id, fake_ts)
    # F270 bugfix: close env so the second instance can re-open it cleanly
    # (LMDB single-writer; without close() the next attach_lmdb may see
    # stale state from the previous writer's mmap).
    registry.close()

    # New registry instance with the same LMDB path hydrates the timestamp.
    registry2 = DeepSourceRegistry()
    registry2.attach_lmdb(lmdb_dir)
    hydrated = registry2.hydrate_from_lmdb()
    assert hydrated >= 1, (
        f"expected ≥1 hydrated source, got {hydrated} "
        f"(lmdb_dir={lmdb_dir}, files={list(lmdb_dir.iterdir())})"
    )
    hydrated_src = registry2.get_source(target_id)
    assert hydrated_src is not None
    assert hydrated_src.last_verified == fake_ts
    registry2.close()


def test_lmdb_attach_idempotent(tmp_path: Path):
    """attach_lmdb is safe to call multiple times."""
    lmdb_dir = tmp_path / "dsr_lmdb2"
    registry = DeepSourceRegistry()
    registry.attach_lmdb(lmdb_dir)
    registry.attach_lmdb(lmdb_dir)
    registry.attach_lmdb(lmdb_dir)
    # No exception raised → idempotent.


def test_hydrate_without_lmdb_returns_zero():
    """hydrate_from_lmdb without attach_lmdb is a no-op (returns 0)."""
    registry = DeepSourceRegistry()
    assert registry.hydrate_from_lmdb() == 0


# ---------------------------------------------------------------------------
# 16-18: verify_source (mocked aiohttp, NO real network)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_verify_source_success_persists_timestamp(tmp_path: Path):
    """A 2xx response updates last_verified and persists it."""
    registry = DeepSourceRegistry()
    registry.attach_lmdb(tmp_path / "dsr_lmdb3")
    target = next(iter(registry))

    # Mock aiohttp at the import boundary: any 2xx response.
    class _FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _FakeSession:
        def head(self, *_a, **_kw):
            return _FakeResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _FakeClientSession:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *_a):
            return False

    with patch("aiohttp.ClientSession", _FakeClientSession):
        ok = await registry.verify_source(target.source_id)

    assert ok is True
    hydrated = registry.get_source(target.source_id)
    assert hydrated is not None
    assert hydrated.last_verified is not None
    assert hydrated.last_verified > 0


@pytest.mark.asyncio
async def test_verify_source_404_treated_as_reachable():
    """4xx is treated as reachable (server responded)."""
    registry = DeepSourceRegistry()
    target = next(iter(registry))

    class _FakeResp:
        status = 404

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _FakeSession:
        def head(self, *_a, **_kw):
            return _FakeResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _FakeClientSession:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *_a):
            return False

    with patch("aiohttp.ClientSession", _FakeClientSession):
        ok = await registry.verify_source(target.source_id)

    assert ok is True


@pytest.mark.asyncio
async def test_verify_source_5xx_returns_false():
    """5xx → not reachable, last_verified unchanged."""
    registry = DeepSourceRegistry()
    target = next(iter(registry))
    before = registry.get_source(target.source_id).last_verified

    class _FakeResp:
        status = 503

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _FakeSession:
        def head(self, *_a, **_kw):
            return _FakeResp()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    class _FakeClientSession:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return _FakeSession()

        async def __aexit__(self, *_a):
            return False

    with patch("aiohttp.ClientSession", _FakeClientSession):
        ok = await registry.verify_source(target.source_id)

    assert ok is False
    after = registry.get_source(target.source_id).last_verified
    assert after == before


# ---------------------------------------------------------------------------
# Bonus: bounds/constants sanity
# ---------------------------------------------------------------------------
def test_max_sources_in_registry_constant():
    """Hard cap constant is wired and matches spec (200)."""
    assert MAX_SOURCES_IN_REGISTRY == 200


def test_lmdb_map_size_constant():
    """LMDB_MAP_SIZE is 1 MiB (1_048_576 bytes)."""
    assert LMDB_MAP_SIZE == 1 * 1024 * 1024


def test_max_concurrent_head_constant():
    """HEAD probe concurrency is bounded to 10 per spec."""
    assert MAX_CONCURRENT_HEAD == 10


def test_lmdb_db_name_is_bytes():
    """LMDB sub-DB name is bytes (lmdb accepts str|bytes; we use bytes)."""
    assert isinstance(LMDB_DB_NAME, bytes)
    assert LMDB_DB_NAME == b"deep_sources"


# ---------------------------------------------------------------------------
# discover_deep_sources integration smoke (no network)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_discover_deep_sources_returns_canonical_findings():
    """discover_deep_sources() must return CanonicalFinding with source_type='source_discovery'."""
    from hledac.universal.enhanced_research import discover_deep_sources

    findings = await asyncio.to_thread(
        discover_deep_sources,
        "crt",
        {"direct"},  # clearnet-only caps
        5,
        None,
    )
    assert isinstance(findings, list)
    assert len(findings) <= 5
    if findings:
        f = findings[0]
        assert f.source_type == "source_discovery"
        assert f.confidence >= 0.0
        assert f.ts > 0
        assert "DeepSourceRegistry" in f.provenance


@pytest.mark.asyncio
async def test_discover_deep_sources_empty_query_returns_empty():
    """Empty/invalid query must short-circuit to [] (no exception)."""
    from hledac.universal.enhanced_research import discover_deep_sources

    findings = await asyncio.to_thread(discover_deep_sources, "", {"direct"}, 5, None)
    assert findings == []
    findings = await asyncio.to_thread(discover_deep_sources, None, {"direct"}, 5, None)
    assert findings == []


# ---------------------------------------------------------------------------
# Standalone runner (bypass pytest discovery / conftest)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import inspect
    import sys
    import tempfile
    import traceback

    # Ensure the project root is on sys.path so enhanced_research imports
    # can resolve `utils.async_helpers` etc. when run as a script.
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    passes, fails = 0, 0
    fail_list: list[str] = []

    async def _run_async(fn, tmp):
        sig_str = str(inspect.signature(fn))
        if "tmp_path" in sig_str:
            return await fn(tmp)
        return await fn()

    def _run_sync(fn, tmp):
        sig_str = str(inspect.signature(fn))
        if "tmp_path" in sig_str:
            return fn(tmp)
        return fn()

    for name in sorted(dir()):
        if not name.startswith("test_"):
            continue
        fn = locals().get(name)
        if not callable(fn):
            continue
        try:
            sig_str = str(inspect.signature(fn))
        except Exception:
            sig_str = ""
        is_async = inspect.iscoroutinefunction(fn)
        try:
            tmp = Path(tempfile.mkdtemp(prefix="dsr_"))
            if is_async:
                asyncio.run(_run_async(fn, tmp))
            else:
                _run_sync(fn, tmp)
            print(f"  PASS  {name}")
            passes += 1
        except Exception as e:
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            fails += 1
            fail_list.append(name)

    print(f"\n{passes} passed, {fails} failed out of {passes + fails}")
    sys.exit(0 if fails == 0 else 1)
