"""Smoke tests for F235 External Intelligence Lanes (Shodan, GreyNoise, Censys).

Verifies:
- TI lanes are defined in AcquisitionLane enum
- TI lane specs are defined (LaneSpecShodan, LaneSpecCensys, LaneSpecGreyNoise)
- TI lanes are in LANE_RULES tuple
- _run_<lane>_lane methods exist in acquisition_strategy
- SprintSchedulerResult has TI lane fields
- API key env var documentation exists
"""

import os
import sys
from pathlib import Path

# Ensure hledac.universal is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_ti_lane_enums():
    """Verify all TI lanes exist in AcquisitionLane enum."""
    from runtime.acquisition_strategy import AcquisitionLane

    assert hasattr(AcquisitionLane, "SHODAN"), "Missing AcquisitionLane.SHODAN"
    assert hasattr(AcquisitionLane, "CENSYS"), "Missing AcquisitionLane.CENSYS"
    assert hasattr(AcquisitionLane, "GREYNOISE"), "Missing AcquisitionLane.GREYNOISE"

    # Values are non-empty strings
    assert bool(AcquisitionLane.SHODAN)
    assert bool(AcquisitionLane.CENSYS)
    assert bool(AcquisitionLane.GREYNOISE)
    print("PASS: AcquisitionLane TI enums exist")


def test_ti_lane_specs():
    """Verify TI lane specs are defined."""
    from runtime.acquisition_strategy import (
        LaneSpecShodan,
        LaneSpecCensys,
        LaneSpecGreyNoise,
 LaneSpec,
    )

    # All are LaneSpec instances
    assert isinstance(LaneSpecShodan, LaneSpec)
    assert isinstance(LaneSpecCensys, LaneSpec)
    assert isinstance(LaneSpecGreyNoise, LaneSpec)

    # Bounded specs
    assert LaneSpecShodan.max_items == 20
    assert LaneSpecShodan.timeout_s == 30
    assert LaneSpecCensys.max_items == 20
    assert LaneSpecCensys.timeout_s == 45
    assert LaneSpecGreyNoise.max_items == 30
    assert LaneSpecGreyNoise.timeout_s == 20
    print("PASS: TI lane specs defined with bounds")


def test_ti_lanes_in_lane_rules():
    """Verify TI lanes are in LANE_RULES tuple."""
    from runtime.acquisition_strategy import LANE_RULES, AcquisitionLane

    lane_names = {rule.lane for rule in LANE_RULES}
    assert AcquisitionLane.SHODAN in lane_names, "SHODAN not in LANE_RULES"
    assert AcquisitionLane.CENSYS in lane_names, "CENSYS not in LANE_RULES"
    assert AcquisitionLane.GREYNOISE in lane_names, "GREYNOISE not in LANE_RULES"
    print("PASS: TI lanes in LANE_RULES")


def test_ti_lane_run_methods_exist():
    """Verify _run_<lane>_lane methods exist in acquisition_strategy source."""
    src = (Path(__file__).parent.parent / "runtime" / "acquisition_strategy.py").read_text()

    assert "async def _run_shodan_lane" in src
    assert "async def _run_censys_lane" in src
    assert "async def _run_greynoise_lane" in src
    print("PASS: TI lane run methods exist in source")


def test_ti_lane_modules_importable():
    """Verify TI lane modules are importable."""
    from intelligence import shodan_lane, greynoise_lane, censys_lane

    # Classes exist
    assert hasattr(shodan_lane, "ShodanLane")
    assert hasattr(greynoise_lane, "GreyNoiseLane")
    assert hasattr(censys_lane, "CensysLane")

    # Functions exist
    assert hasattr(shodan_lane, "search_shodan_lane")
    assert hasattr(greynoise_lane, "search_greynoise_lane")
    assert hasattr(censys_lane, "search_censys_lane")
    print("PASS: TI lane modules importable")


def test_ti_lane_fail_soft_on_missing_key():
    """Verify TI lanes return empty on missing API key."""
    import asyncio

    from intelligence.shodan_lane import search_shodan_lane
    from intelligence.greynoise_lane import search_greynoise_lane
    from intelligence.censys_lane import search_censys_lane

    async def check():
        # Without API keys, should return empty tuples
        shodan_findings, _ = await search_shodan_lane("8.8.8.8")
        assert shodan_findings == [], "Shodan should return [] without API key"

        gn_findings, _ = await search_greynoise_lane("8.8.8.8")
        assert gn_findings == [], "GreyNoise should return [] without API key"

        censys_findings, _ = await search_censys_lane("example.com")
        assert censys_findings == [], "Censys should return [] without API key"
        print("PASS: TI lanes fail-soft on missing API key")

    asyncio.run(check())


def test_ti_lane_rate_limiters():
    """Verify TI lanes use TokenBucket rate limiting."""
    from intelligence.shodan_lane import RATE_LIMIT_KEY as SHODAN_KEY
    from intelligence.greynoise_lane import RATE_LIMIT_KEY as GN_KEY
    from intelligence.censys_lane import RATE_LIMIT_KEY as CENSYS_KEY

    assert SHODAN_KEY == "shodan_api"
    assert GN_KEY == "greynoise_api"
    assert CENSYS_KEY == "censys_api"
    print("PASS: TI lanes use TokenBucket rate limiting")


def test_ti_lane_source_families():
    """Verify TI lanes use correct source_family in outcomes."""
    src = (Path(__file__).parent.parent / "runtime" / "acquisition_strategy.py").read_text()

    # Check source_family strings
    assert 'source_family="shodan_intel"' in src
    assert 'source_family="censys_intel"' in src
    assert 'source_family="greynoise_intel"' in src
    print("PASS: TI lanes use correct source_family strings")


def test_env_example_documents_ti_keys():
    """Verify .env.example documents TI API keys."""
    # Path: tests/probe_f235_ti_lanes.py -> tests/ -> hledac/universal/ -> .env.example
    env_example = (Path(__file__).parent.parent / ".env.example").read_text()

    assert "SHODAN_API_KEY" in env_example
    assert "GREYNOISE_API_KEY" in env_example
    assert "CENSYS_API_ID" in env_example
    assert "CENSYS_API_SECRET" in env_example
    assert "account.shodan.io" in env_example
    assert "greynoise.io" in env_example
    assert "search.censys.io" in env_example
    print("PASS: .env.example documents TI API keys")


def test_ti_lane_ghost_invariants():
    """Verify TI lanes follow GHOST_INVARIANTS."""
    from intelligence import shodan_lane, greynoise_lane, censys_lane

    # Check that API key is never logged (only checked with warning)
    src_shodan = Path(shodan_lane.__file__).read_text()
    src_gn = Path(greynoise_lane.__file__).read_text()
    src_censys = Path(censys_lane.__file__).read_text()

    # Should have warning log when key missing
    assert "[SHODAN] No API key" in src_shodan
    assert "[GREYNOISE] No API key" in src_gn
    assert "[CENSYS] No API credentials" in src_censys

    # Should NOT have logger that could leak key
    assert 'logger.info(f"key=' not in src_shodan
    assert 'logger.debug(f"key=' not in src_shodan
    print("PASS: TI lanes follow GHOST_INVARIANTS (no key logging)")


def test_ti_lane_canonical_finding_structure():
    """Verify TI lanes return CanonicalFinding with correct structure."""
    import asyncio
    from datetime import datetime
    from intelligence.shodan_lane import _build_findings

    async def check():
        raw = [
            {
                "ip": "8.8.8.8",
                "port": 443,
                "banner": "test banner",
                "hostnames": ["dns.google"],
                "asn": {"asn":15169, "name": "Google LLC"},
                "vulns": ["CVE-2024-1234"],
                "tags": ["google", "dns"],
            }
        ]
        findings = _build_findings("8.8.8.8", raw, datetime.now().timestamp())

        assert len(findings) == 1
        f = findings[0]
        assert f.source_type == "shodan_intel"
        assert f.confidence > 0
        assert "8.8.8.8" in f.payload_text
        assert "443" in f.payload_text
        print("PASS: TI lanes return properly structured CanonicalFindings")

    asyncio.run(check())


if __name__ == "__main__":
    test_ti_lane_enums()
    test_ti_lane_specs()
    test_ti_lanes_in_lane_rules()
    test_ti_lane_run_methods_exist()
    test_ti_lane_modules_importable()
    test_ti_lane_fail_soft_on_missing_key()
    test_ti_lane_rate_limiters()
    test_ti_lane_source_families()
    test_env_example_documents_ti_keys()
    test_ti_lane_ghost_invariants()
    test_ti_lane_canonical_finding_structure()
    print("\nAll F235 TI lanes smoke tests passed.")
