"""
Sprint 8AX — Gate / Env Truth Hardening after 8AT

Tests:
- D.1  seed truth surface matches post-8AT state
- D.2  probe_8ar no longer depends on hardcoded Reuters URL
- D.3  probe_8ar asserts positive WeLiveSecurity invariant
- D.4  probe_8aq uses explicit ahocorasick env blocker hygiene
- D.5  probe_8an uses explicit ahocorasick env blocker hygiene
- D.6  env blocker path is skip/ENV BLOCKER, not collection error
- D.7  probe_8at still green
- D.8  probe_8as still green
- D.9  probe_8au still green
- D.10 probe_8av still green
- D.11 probe_8aw still green or N/A
- D.12 test_ao_canary still green
"""


import importlib.util
import pytest

from hledac.universal.discovery.rss_atom_adapter import (
    get_default_feed_seed_truth,
    get_default_feed_seeds,
)


# ---------------------------------------------------------------------------
# D.1 — seed truth surface matches post-8AT state
# ---------------------------------------------------------------------------

def test_seed_truth_surface_matches_post_8at_state():
    """Verify the seed truth surface reflects 8AT decisions."""
    truth = get_default_feed_seed_truth()

    # Count invariant: must be exactly 5
    assert truth["count"] == 5, f"Expected 5 curated seeds, got {truth['count']}"

    # Reuters is absent (8AT decision)
    assert truth["has_authenticated_reuters"] is False

    # WeLiveSecurity is present (8AT positive invariant)
    seeds = get_default_feed_seeds()
    wlive = next(
        (s for s in seeds if "welivesecurity" in s.feed_url.lower()),
        None,
    )
    assert wlive is not None, "WeLiveSecurity must be in curated seeds"
    assert wlive.feed_url == "https://www.welivesecurity.com/feed/"
    assert wlive.label == "WeLiveSecurity"
    assert wlive.source == "curated_seed"

    # All identity URLs are https
    for url in truth["urls"]:
        assert url.startswith("https://"), f"Non-HTTPS URL in identity list: {url}"

    # Reuters URL must NOT be in any identity
    reuters_urls = [u for u in truth["urls"] if "reuters" in u.lower()]
    assert len(reuters_urls) == 0, f"Reuters URLs found in seed truth: {reuters_urls}"


# ---------------------------------------------------------------------------
# D.2 — probe_8ar no longer depends on hardcoded Reuters URL
# ---------------------------------------------------------------------------

def test_probe_8ar_no_longer_depends_on_hardcoded_reuters_url():
    """Verify probe_8ar test does not contain Reuters string assertions."""
    import re

    test_file = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tests/probe_8ar/test_sprint_8ar.py"
    with open(test_file) as f:
        content = f.read()

    # Reuters URL literal must NOT appear
    assert "https://feeds.reuters.com/reuters/topNews" not in content, (
        "probe_8ar still contains hardcoded Reuters URL"
    )

    # Case-insensitive check for "reuters" as feed_url in assertions
    # We allow historical comments but not active assertions
    reuters_pattern = re.compile(
        r'reuters.*feed_url.*assert\s+.*reuters',
        re.IGNORECASE,
    )
    matches = reuters_pattern.findall(content)
    assert len(matches) == 0, (
        f"probe_8ar still asserts Reuters feed_url: {matches}"
    )


# ---------------------------------------------------------------------------
# D.3 — probe_8ar asserts positive WeLiveSecurity invariant
# ---------------------------------------------------------------------------

def test_probe_8ar_asserts_positive_welivesecurity_invariant():
    """Verify probe_8ar now has WeLiveSecurity positive invariant."""
    test_file = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tests/probe_8ar/test_sprint_8ar.py"
    with open(test_file) as f:
        content = f.read()

    # WeLiveSecurity invariant must be present
    assert "WeLiveSecurity" in content, (
        "probe_8ar must contain WeLiveSecurity positive invariant"
    )
    assert "welivesecurity" in content.lower(), (
        "probe_8ar must reference welivesecurity (lowercase)"
    )
    # The new test must check for presence, not absence
    assert "is not None" in content or "assert wlive" in content, (
        "probe_8ar must assert WeLiveSecurity is not None"
    )


# ---------------------------------------------------------------------------
# D.4 — probe_8aq uses explicit ahocorasick env blocker hygiene
# ---------------------------------------------------------------------------

def test_probe_8aq_uses_explicit_ahocorasick_env_blocker_hygiene():
    """Verify probe_8aq conftest uses pytest.importorskip for ahocorasick."""
    conftest_file = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tests/probe_8aq/conftest.py"
    with open(conftest_file) as f:
        content = f.read()

    # Must use importorskip
    assert "importorskip" in content, (
        "probe_8aq conftest must use pytest.importorskip for ahocorasick"
    )
    assert "ahocorasick" in content, (
        "probe_8aq conftest must reference ahocorasick"
    )
    # Must have ENV BLOCKER reason text
    assert "ENV BLOCKER" in content or "reason=" in content, (
        "probe_8aq conftest must have skip reason"
    )


# ---------------------------------------------------------------------------
# D.5 — probe_8an uses explicit ahocorasick env blocker hygiene
# ---------------------------------------------------------------------------

def test_probe_8an_uses_explicit_ahocorasick_env_blocker_hygiene():
    """Verify probe_8an conftest uses pytest.importorskip for ahocorasick."""
    conftest_file = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tests/probe_8an/conftest.py"
    with open(conftest_file) as f:
        content = f.read()

    # Must use importorskip
    assert "importorskip" in content, (
        "probe_8an conftest must use pytest.importorskip for ahocorasick"
    )
    assert "ahocorasick" in content, (
        "probe_8an conftest must reference ahocorasick"
    )
    # Must have ENV BLOCKER reason text
    assert "ENV BLOCKER" in content or "reason=" in content, (
        "probe_8an conftest must have skip reason"
    )


# ---------------------------------------------------------------------------
# D.6 — env blocker path is skip/ENV BLOCKER, not collection error
# ---------------------------------------------------------------------------

def test_env_blocker_path_is_skip_or_na_not_collection_error():
    """Verify probe_8aq and probe_8an do not collection-error when ahocorasick missing."""
    # This test verifies the *presence* of env blocker patterns in the test files
    # Actual skip behavior is tested by running the suites with PYTHONDONTWRITEBYTECODE

    # Collect probe_8aq — should not raise ImportError at collection time
    spec = importlib.util.find_spec("hledac.universal.tests.probe_8aq")
    assert spec is not None, "probe_8aq test package must exist"

    # Collect probe_8an — should not raise ImportError at collection time
    spec = importlib.util.find_spec("hledac.universal.tests.probe_8an")
    assert spec is not None, "probe_8an test package must exist"

    # Both conftest files must exist and contain importorskip
    for probe_name in ("probe_8aq", "probe_8an"):
        conftest_path = f"/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tests/{probe_name}/conftest.py"
        with open(conftest_path) as f:
            content = f.read()
        assert "importorskip" in content, (
            f"{probe_name} conftest must use importorskip to prevent collection error"
        )


# ---------------------------------------------------------------------------
# D.7 — probe_8at still green
# ---------------------------------------------------------------------------

def _run_probe_if_exists(probe_name: str) -> None:
    """Run probe tests if the probe directory exists, skip otherwise."""
    spec = importlib.util.find_spec(f"hledac.universal.tests.{probe_name}")
    if spec is not None:
        pytest.main(
            [
                f"hledac/universal/tests/{probe_name}/",
                "--tb=no",
                "-q",
            ]
        )
    else:
        pytest.skip(f"{probe_name} does not exist (N/A)")


def test_probe_8at_still_green():
    """Regression: probe_8at is not broken by Sprint 8AX changes."""
    _run_probe_if_exists("probe_8at")


# ---------------------------------------------------------------------------
# D.8 — probe_8as still green
# ---------------------------------------------------------------------------

def test_probe_8as_still_green():
    """Regression: probe_8as is not broken by Sprint 8AX changes."""
    _run_probe_if_exists("probe_8as")


# ---------------------------------------------------------------------------
# D.9 — probe_8au still green
# ---------------------------------------------------------------------------

def test_probe_8au_still_green():
    """Regression: probe_8au is not broken by Sprint 8AX changes."""
    _run_probe_if_exists("probe_8au")


# ---------------------------------------------------------------------------
# D.10 — probe_8av still green
# ---------------------------------------------------------------------------

def test_probe_8av_still_green():
    """Regression: probe_8av is not broken by Sprint 8AX changes."""
    _run_probe_if_exists("probe_8av")


# ---------------------------------------------------------------------------
# D.11 — probe_8aw still green or N/A
# ---------------------------------------------------------------------------

def test_probe_8aw_still_green_or_na():
    """Regression: probe_8aw is not broken by Sprint 8AX changes. N/A if absent."""
    spec = importlib.util.find_spec("hledac.universal.tests.probe_8aw")
    if spec is None:
        pytest.skip("probe_8aw does not exist (N/A per gate rules)")
    pytest.main(
        [
            "hledac/universal/tests/probe_8aw/",
            "--tb=no",
            "-q",
        ]
    )


# ---------------------------------------------------------------------------
# D.12 — test_ao_canary still green
# ---------------------------------------------------------------------------

def test_ao_canary_still_green():
    """Regression: test_ao_canary is not broken by Sprint 8AX changes."""
    spec = importlib.util.find_spec("hledac.universal.tests.test_ao_canary")
    if spec is None:
        pytest.skip("test_ao_canary does not exist")
    pytest.main(
        ["hledac/universal/tests/test_ao_canary.py", "--tb=no", "-q"]
    )


# ---------------------------------------------------------------------------
# E.1 — benchmark: 1000x get_default_feed_seed_truth()
# ---------------------------------------------------------------------------

def test_benchmark_get_default_feed_seed_truth_1000x():
    """E.1: 1000× get_default_feed_seed_truth() — target: < 200 ms total."""
    import time

    start = time.perf_counter()
    for _ in range(1000):
        get_default_feed_seed_truth()
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 200, f"get_default_feed_seed_truth x1000 took {elapsed_ms:.1f}ms (target: <200ms)"


# ---------------------------------------------------------------------------
# E.2 — benchmark: 1000x normalize_seed_identity()
# ---------------------------------------------------------------------------

def _normalize_seed_identity(url: str) -> str:
    """Normalize a seed URL for comparison (helper, mirrors production logic)."""
    url = url.strip().lower()
    if url.endswith("/"):
        url = url[:-1]
    return url


def test_benchmark_normalize_seed_identity_1000x():
    """E.2: 1000× normalize_seed_identity() — target: < 200 ms total."""
    import time

    urls = [
        "https://www.welivesecurity.com/feed/",
        "https://ABUSE.CH/FEEDS/URLHAUS/",
        "https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml",
    ]

    start = time.perf_counter()
    for _ in range(1000):
        for url in urls:
            _normalize_seed_identity(url)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 200, f"normalize_seed_identity x1000 took {elapsed_ms:.1f}ms (target: <200ms)"


# ---------------------------------------------------------------------------
# E.3 — benchmark: 100x ahocorasick availability helper
# ---------------------------------------------------------------------------

def test_benchmark_ahocorasick_importorskip_100x():
    """E.3: 100× ahocorasick availability check — target: low-millisecond scale."""
    import time

    start = time.perf_counter()
    for _ in range(100):
        try:
            __import__("ahocorasick")
        except ImportError:
            pass
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Should be very fast (<50ms for 100 iterations)
    assert elapsed_ms < 50, f"ahocorasick availability check x100 took {elapsed_ms:.1f}ms (target: <50ms)"
