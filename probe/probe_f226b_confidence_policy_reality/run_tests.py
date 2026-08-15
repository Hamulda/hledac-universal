#!/usr/bin/env python3
"""
Direct runner for F226B confidence policy reality tests.
Bypasses hledac.universal.__init__ chain to avoid import breakage.
"""

import sys
from pathlib import Path

_root = Path(__file__).parent
sys.path.insert(0, str(_root))

# Import the modules we need directly (no package init)
# NOTE: 'intelligence' is a legacy alias for 'recon' via PEP 562 lazy loading.
# Use canonical 'recon' imports for clarity.
import recon.confidence_policy
import recon.social_identity_miner
import coordinators.claims_coordinator
from _core import aclose

def run_tests():
    from recon.confidence_policy import (
        compute_confidence, _SOURCE_BASELINES,
        FEED, PUBLIC, CT, WAYBACK, PASSIVE_DNS, SOCIAL, PLANNER, STEALTH,
        MIN_CONFIDENCE, MAX_CONFIDENCE,
    )
    from recon.social_identity_miner import SocialIdentityMiner, SOCIAL_MIN_CONFIDENCE
    from coordinators.claims_coordinator import ClaimsCoordinator

    passed = 0
    failed = 0

    # Test 1: no local _BASELINES inside compute_confidence
    import inspect, re
    src = inspect.getsource(compute_confidence)
    if '_BASELINES =' not in src:
        print("PASS: no local _BASELINES in compute_confidence")
        passed += 1
    else:
        print("FAIL: local _BASELINES still present")
        failed += 1

    # Test 2: _SOURCE_BASELINES keys match constants
    expected = {"FEED", "PUBLIC", "CT", "WAYBACK", "PASSIVE_DNS", "SOCIAL", "PLANNER", "STEALTH"}
    if set(_SOURCE_BASELINES.keys()) == expected:
        print("PASS: _SOURCE_BASELINES keys match constants")
        passed += 1
    else:
        print(f"FAIL: _SOURCE_BASELINES keys {set(_SOURCE_BASELINES.keys())} != {expected}")
        failed += 1

    # Test 3: values match
    ok = all(_SOURCE_BASELINES[k] == v for k, v in [
        ("FEED", FEED), ("PUBLIC", PUBLIC), ("CT", CT), ("WAYBACK", WAYBACK),
        ("PASSIVE_DNS", PASSIVE_DNS), ("SOCIAL", SOCIAL), ("PLANNER", PLANNER), ("STEALTH", STEALTH)
    ])
    if ok:
        print("PASS: _SOURCE_BASELINES values match constants")
        passed += 1
    else:
        print("FAIL: _SOURCE_BASELINES values != constants")
        failed += 1

    # Test 4: claims_coordinator imports compute_confidence
    claims_path = _root / "coordinators" / "claims_coordinator.py"
    claims_src = claims_path.read_text()
    if "compute_confidence" in claims_src:
        print("PASS: claims_coordinator imports compute_confidence")
        passed += 1
    else:
        print("FAIL: claims_coordinator does not import compute_confidence")
        failed += 1

    # Test 5: _derive_confidence calls compute_confidence
    match = re.search(r'def _derive_confidence\([^)]+\)[^:]*:.*?(?=\n    def |\nclass |\Z)',
                      claims_src, re.DOTALL)
    if match and "compute_confidence(" in match.group(0):
        print("PASS: _derive_confidence calls compute_confidence")
        passed += 1
    else:
        print("FAIL: _derive_confidence does not call compute_confidence")
        failed += 1

    # Test 6: social_identity_miner imports compute_confidence
    sim_path = _root / "intelligence" / "social_identity_miner.py"
    sim_src = sim_path.read_text()
    if "compute_confidence" in sim_src:
        print("PASS: social_identity_miner imports compute_confidence")
        passed += 1
    else:
        print("FAIL: social_identity_miner does not import compute_confidence")
        failed += 1

    # Test 7: _compute_confidence calls compute_confidence
    sim_match = re.search(r'def _compute_confidence\([^)]+\)[^:]*:.*?(?=\n    def |\nclass |\Z)',
                          sim_src, re.DOTALL)
    if sim_match and "compute_confidence(" in sim_match.group(0):
        print("PASS: _compute_confidence calls compute_confidence")
        passed += 1
    else:
        print("FAIL: _compute_confidence does not call compute_confidence")
        failed += 1

    # Test 8: ClaimsCoordinator confidence <= 0.75
    coord = ClaimsCoordinator()
    text = "A" * 50 + " https://example.com some content here"
    evidence = {"source_type": "ct", "title": "Example Title", "summary": "Example summary"}
    conf = coord._derive_confidence(text, evidence, "Example Title", "Example summary")
    if conf <= 0.75 and conf >= MIN_CONFIDENCE:
        print(f"PASS: ClaimsCoordinator confidence {conf} bounded in [0.10, 0.75]")
        passed += 1
    else:
        print(f"FAIL: ClaimsCoordinator confidence {conf} not bounded")
        failed += 1

    # Test 9: provenance raises claims confidence
    evidence_no_prov = {"source_type": "public"}
    evidence_with_prov = {"source_type": "public", "source": "test_source", "provenance": "test"}
    conf_no = coord._derive_confidence("Confirmed report", evidence_no_prov, "", "")
    conf_yes = coord._derive_confidence("Confirmed report", evidence_with_prov, "", "")
    if conf_yes > conf_no:
        print("PASS: provenance raises claims confidence")
        passed += 1
    else:
        print("FAIL: provenance does not raise claims confidence")
        failed += 1

    # Test 10: URL/IOC raises claims confidence
    conf_no_ioc = coord._derive_confidence("Some claim without identifiers",
                                            {"source_type": "public"}, "", "")
    conf_yes_ioc = coord._derive_confidence("Visit https://example.com or admin@domain.com",
                                            {"source_type": "public"}, "", "")
    if conf_yes_ioc > conf_no_ioc:
        print("PASS: URL/IOC raises claims confidence")
        passed += 1
    else:
        print("FAIL: URL/IOC does not raise claims confidence")
        failed += 1

    # Test 11: CT baseline > PUBLIC baseline
    conf_ct = coord._derive_confidence("Certificate observed", {"source_type": "ct"}, "", "")
    conf_pub = coord._derive_confidence("Certificate observed", {"source_type": "public"}, "", "")
    if conf_ct > conf_pub:
        print("PASS: CT baseline > PUBLIC baseline")
        passed += 1
    else:
        print("FAIL: CT baseline not higher than PUBLIC")
        failed += 1

    # Test 12: social bare profile meets SOCIAL_MIN_CONFIDENCE
    miner = SocialIdentityMiner()
    conf_bare = miner._compute_confidence("github", "testuser", [], [])
    if conf_bare >= SOCIAL_MIN_CONFIDENCE:
        print(f"PASS: bare profile {conf_bare} >= SOCIAL_MIN_CONFIDENCE={SOCIAL_MIN_CONFIDENCE}")
        passed += 1
    else:
        print(f"FAIL: bare profile {conf_bare} < SOCIAL_MIN_CONFIDENCE")
        failed += 1

    # Test 13: social rich profile > bare profile
    conf_rich = miner._compute_confidence("github", "testuser",
                                           ["example.com", "test.io"],
                                           ["user@example.com"])
    if conf_rich > conf_bare:
        print("PASS: rich profile > bare profile")
        passed += 1
    else:
        print("FAIL: rich profile not > bare profile")
        failed += 1

    # Test 14: social confidence bounded <= 0.95
    conf_max = miner._compute_confidence("github", "testuser12345",
                                         ["a.com", "b.com", "c.com", "d.com"],
                                         ["a@a.com", "b@b.com", "c@c.com"])
    if conf_max <= MAX_CONFIDENCE:
        print(f"PASS: social confidence {conf_max} <= MAX_CONFIDENCE=0.95")
        passed += 1
    else:
        print(f"FAIL: social confidence {conf_max} > 0.95")
        failed += 1

    # Test 15: no MLX in claims_coordinator
    if not hasattr(coordinators.claims_coordinator, 'mlx'):
        print("PASS: no MLX in claims_coordinator")
        passed += 1
    else:
        print("FAIL: MLX imported in claims_coordinator")
        failed += 1

    # Test 16: no MLX in social_identity_miner
    if not hasattr(intelligence.social_identity_miner, 'mlx'):
        print("PASS: no MLX in social_identity_miner")
        passed += 1
    else:
        print("FAIL: MLX imported in social_identity_miner")
        failed += 1

    print(f"\n{'='*50}")
    print(f"RESULTS: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)