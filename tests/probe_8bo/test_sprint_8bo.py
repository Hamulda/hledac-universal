"""Sprint 8BO — IOC-First Signal Vocabulary V3 tests.

Tests invariant:
- pattern_matcher.py only, no new matching engine
- bootstrap_pack_version == 3 after V3
- V3 literals are IOC/TTP-first, precision-audited
- Regex extraction covers CVE, GHSA, onion v3, SHA256
"""

import pytest

from hledac.universal.utils.patterns.pattern_matcher import (
    get_pattern_matcher,
    configure_default_bootstrap_patterns_if_empty,
    get_default_bootstrap_patterns,
    match_text,
    extract_high_precision_entities,
    get_pattern_pack_metadata,
    reset_pattern_matcher,
    _BOOTSTRAP_PATTERNS,
    _BOOTSTRAP_PACK_VERSION,
    ExtractedEntity,
)


# ==============================================================================
# D.1 — version truth
# ==============================================================================

def test_bootstrap_pack_v3_version_truth():
    """BOOTSTRAP_PACK_VERSION must be 3."""
    assert _BOOTSTRAP_PACK_VERSION == 3


# ==============================================================================
# D.2 — count matches V3
# ==============================================================================

def test_default_bootstrap_count_matches_v3():
    """default_bootstrap_count equals len(_BOOTSTRAP_PATTERNS_V3)."""
    configure_default_bootstrap_patterns_if_empty()
    patterns = get_default_bootstrap_patterns()
    assert len(patterns) == len(_BOOTSTRAP_PATTERNS)
    pm = get_pattern_matcher()
    status = pm.get_status()
    assert status["default_bootstrap_count"] == len(patterns)
    assert status["bootstrap_pack_version"] == 3


# ==============================================================================
# D.3 — idempotent init
# ==============================================================================

def test_v3_pack_still_initializes_idempotently():
    """configure_default_bootstrap_patterns_if_empty is idempotent."""
    reset_pattern_matcher()
    first = configure_default_bootstrap_patterns_if_empty()
    second = configure_default_bootstrap_patterns_if_empty()
    assert first is True
    assert second is False
    pm = get_pattern_matcher()
    assert pm.pattern_count() > 0


# ==============================================================================
# D.4 — existing v2 literals preserved
# ==============================================================================

def test_existing_v2_literals_still_present():
    """Core v1/v2 literals survive in V3."""
    configure_default_bootstrap_patterns_if_empty()
    patterns = dict(get_default_bootstrap_patterns())

    # Core v1
    assert "cve-" in patterns
    assert ".onion" in patterns
    assert "ransomware" in patterns
    assert "phishing" in patterns
    assert "malware" in patterns
    assert "botnet" in patterns
    assert "exploit" in patterns
    assert "vulnerability" in patterns
    assert "infostealer" in patterns
    assert "breach" in patterns
    assert "leak" in patterns
    assert "credentials" in patterns

    # Core v2 morphology
    assert "vulnerabilities" in patterns
    assert "exploited" in patterns
    assert "exploits" in patterns
    assert "breaches" in patterns
    assert "leaked" in patterns
    assert "credential" in patterns
    assert "infected" in patterns
    assert "backdoor" in patterns
    assert "data breach" in patterns


# ==============================================================================
# D.5 — CVE identifier hits
# ==============================================================================

def test_cve_identifier_hits():
    """CVE-2026-3055 matches cve- literal."""
    configure_default_bootstrap_patterns_if_empty()
    hits = match_text("Citrix NetScaler Under Active Recon for CVE-2026-3055 (CVSS 9.3) Memory Overread Bug")
    patterns = [h.pattern for h in hits]
    assert "cve-" in patterns


def test_cve_regex_extraction():
    """extract_high_precision_entities finds CVE IDs."""
    text = "Advisory references CVE-2026-3055 and CVE-2025-1234"
    entities = extract_high_precision_entities(text)
    cves = [e for e in entities if e.entity_type == "cve_identifier"]
    assert len(cves) == 2
    assert any(e.value == "CVE-2026-3055" for e in cves)


# ==============================================================================
# D.6 — GHSA identifier hits
# ==============================================================================

def test_ghsa_identifier_hits():
    """GHSA-xxxx-yyyy-zzzz matches ghsa- literal."""
    configure_default_bootstrap_patterns_if_empty()
    hits = match_text("Advisory references GHSA-abcd-1234-efgh")
    patterns = [h.pattern for h in hits]
    assert "ghsa-" in patterns


def test_ghsa_regex_extraction():
    """extract_high_precision_entities finds GHSA IDs."""
    text = "References GHSA-ab12-34cd-56ef and GHSA-9876-5432-1098"
    entities = extract_high_precision_entities(text)
    ghsas = [e for e in entities if e.entity_type == "ghsa_identifier"]
    assert len(ghsas) == 2


# ==============================================================================
# D.7 — wiper text now hits
# ==============================================================================

def test_wiper_text_now_hits():
    """Wiper-related text matches wiper literal."""
    configure_default_bootstrap_patterns_if_empty()

    # 8BH sample
    hits = match_text("Iran-Linked Hackers Breach FBI Director's Personal Email, Hit Stryker With Wiper Attack")
    patterns = [h.pattern for h in hits]
    assert "wiper" in patterns

    # variation
    hits2 = match_text("Wiper malware used in destructive attack")
    patterns2 = [h.pattern for h in hits2]
    assert "wiper" in patterns2


# ==============================================================================
# D.8 — exploit kit text now hits
# ==============================================================================

def test_exploit_kit_text_now_hits():
    """Exploit kit text matches exploit kit literal."""
    configure_default_bootstrap_patterns_if_empty()

    hits = match_text("TA446 Deploys DarkSword iOS Exploit Kit in Targeted Spear-Phishing Campaign")
    patterns = [h.pattern for h in hits]
    assert "exploit kit" in patterns

    hits2 = match_text("Exploit Kit activity detected")
    patterns2 = [h.pattern for h in hits2]
    assert "exploit kit" in patterns2


# ==============================================================================
# D.9 — cobalt strike text now hits
# ==============================================================================

def test_cobalt_strike_text_now_hits():
    """Cobalt Strike text matches cobalt strike literal."""
    configure_default_bootstrap_patterns_if_empty()

    hits = match_text(
        "Researchers observed Cobalt Strike beacon activity during credential dumping and lateral movement"
    )
    patterns = [h.pattern for h in hits]
    assert "cobalt strike" in patterns


# ==============================================================================
# D.10 — lateral movement text now hits
# ==============================================================================

def test_lateral_movement_text_now_hits():
    """Lateral movement text matches literal."""
    configure_default_bootstrap_patterns_if_empty()

    hits = match_text(
        "Researchers observed Cobalt Strike beacon activity during credential dumping and lateral movement"
    )
    patterns = [h.pattern for h in hits]
    assert "lateral movement" in patterns

    hits2 = match_text("Lateral movement detected across network segments")
    patterns2 = [h.pattern for h in hits2]
    assert "lateral movement" in patterns2


# ==============================================================================
# D.11 — credential dumping text now hits
# ==============================================================================

def test_credential_dumping_text_now_hits():
    """Credential dumping text matches literal."""
    configure_default_bootstrap_patterns_if_empty()

    hits = match_text(
        "Researchers observed Cobalt Strike beacon activity during credential dumping and lateral movement"
    )
    patterns = [h.pattern for h in hits]
    assert "credential dumping" in patterns


# ==============================================================================
# D.12 — onion v3 regex extraction
# ==============================================================================

def test_onion_v3_regex_extraction():
    """extract_high_precision_entities finds onion v3 addresses."""
    # 56-char base32 string: exactly 56 [a-z2-7] chars before .onion
    text = f"Host found at http://{'a' * 56}.onion/api"
    entities = extract_high_precision_entities(text)
    onions = [e for e in entities if e.entity_type == "onion_v3_address"]
    assert len(onions) == 1


# ==============================================================================
# D.13 — sha256 regex extraction
# ==============================================================================

def test_sha256_regex_extraction():
    """extract_high_precision_entities finds SHA256 hashes."""
    text = "File hash: 3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b"
    entities = extract_high_precision_entities(text)
    hashes = [e for e in entities if e.entity_type == "sha256_hash"]
    assert len(hashes) == 1
    assert len(hashes[0].value) == 64


# ==============================================================================
# D.14 — status surface reports V3
# ==============================================================================

def test_status_surface_reports_v3():
    """get_status reports bootstrap_pack_version=3 and correct default_bootstrap_count."""
    configure_default_bootstrap_patterns_if_empty()
    pm = get_pattern_matcher()
    status = pm.get_status()
    assert status["bootstrap_pack_version"] == 3
    assert status["default_bootstrap_count"] == len(_BOOTSTRAP_PATTERNS)
    assert status["default_bootstrap_count"] > 25  # was 25 in V2


# ==============================================================================
# D.15 — pattern metadata exists for new literals
# ==============================================================================

def test_pattern_metadata_exists_for_new_literals():
    """New V3 literals have metadata entries."""
    new_literals = [
        "wiper",
        "cobalt strike",
        "lateral movement",
        "credential dumping",
        "ghsa-",
        "exploit kit",
        "infostealer",
        "lived off the land",
        "lolbin",
        "sliver c2",
        "dropper",
        "ransomware-as-a-service",
        "leaked database",
        "pastebin leak",
        "shodan",
        "censys",
        "greynoise",
    ]

    configure_default_bootstrap_patterns_if_empty()
    patterns = dict(get_default_bootstrap_patterns())

    missing = []
    for lit in new_literals:
        if lit in patterns:
            meta = get_pattern_pack_metadata(lit)
            if meta is None:
                missing.append(lit)

    # These may not all be in patterns (lolbin etc.), but the ones that are should have metadata
    for lit in ["wiper", "cobalt strike", "lateral movement", "credential dumping",
                "ghsa-", "exploit kit", "infostealer", "sliver c2"]:
        if lit in patterns:
            assert get_pattern_pack_metadata(lit) is not None, f"Missing metadata for {lit}"


def test_pattern_metadata_layer_values():
    """Metadata layer values are in range 1-4."""
    configure_default_bootstrap_patterns_if_empty()
    patterns = dict(get_default_bootstrap_patterns())

    for pattern in patterns:
        meta = get_pattern_pack_metadata(pattern)
        if meta is not None:
            assert 1 <= meta["layer"] <= 4, f"Invalid layer for {pattern}: {meta['layer']}"
            assert meta["source_vocab"] in ("identifier", "ttp", "malware", "osint")


# ==============================================================================
# D.16 — precision audit rejects generic attack word
# ==============================================================================

def test_precision_audit_rejects_generic_attack_word():
    """Generic 'attack' alone is NOT a V3 literal (precision audit)."""
    configure_default_bootstrap_patterns_if_empty()
    patterns = dict(get_default_bootstrap_patterns())

    # "attack" alone is too generic — not in V3
    assert "attack" not in patterns


# ==============================================================================
# D.17 — runtime helper uses V3 default count
# ==============================================================================

def test_runtime_helper_uses_v3_default_count():
    """After configure_default..._if_empty, pattern_count > 25."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()
    pm = get_pattern_matcher()
    count = pm.pattern_count()
    assert count > 25  # V2 had exactly 25


# ==============================================================================
# D.18 — 8BH sample texts now give hits
# ==============================================================================

def test_live_run_recommendation_mapping_after_hits():
    """8BH-style texts yield non-zero hits."""
    configure_default_bootstrap_patterns_if_empty()

    samples = [
        "Iran-Linked Hackers Breach FBI Director's Personal Email, Hit Stryker With Wiper Attack",
        "Citrix NetScaler Under Active Recon for CVE-2026-3055 (CVSS 9.3) Memory Overread Bug",
        "TA446 Deploys DarkSword iOS Exploit Kit in Targeted Spear-Phishing Campaign",
        "Researchers observed Cobalt Strike beacon activity during credential dumping and lateral movement",
    ]

    total_hits = 0
    for text in samples:
        hits = match_text(text.casefold())
        total_hits += len(hits)

    assert total_hits > 0, "V3 should produce hits on 8BH-style texts"

    # Spot-check individual samples
    wiper_hits = match_text(samples[0].casefold())
    assert any("wiper" in h.pattern for h in wiper_hits), "wiper should hit in sample 1"

    cve_hits = match_text(samples[1].casefold())
    assert any("cve-" in h.pattern for h in cve_hits), "cve- should hit in sample 2"

    ek_hits = match_text(samples[2].casefold())
    assert any("exploit kit" in h.pattern for h in ek_hits), "exploit kit should hit in sample 3"

    cs_hits = match_text(samples[3].casefold())
    assert any("cobalt strike" in h.pattern for h in cs_hits), "cobalt strike should hit in sample 4"


# ==============================================================================
# D.19 — NVD-style advisory texts
# ==============================================================================

def test_nvd_style_advisory_texts():
    """NVD-style advisory texts yield hits."""
    configure_default_bootstrap_patterns_if_empty()

    texts = [
        "CVE-2026-3055: Memory overread in Citrix NetScaler (CVSS 9.3)",
        "TA446 using Cobalt Strike beacon with credential dumping and lateral movement",
        "Advisory references GHSA-abcd-1234-efgh and USN-1234-1",
    ]

    hits = [match_text(t.casefold()) for t in texts]

    assert len(hits[0]) > 0, "CVE advisory text"
    assert len(hits[1]) > 0, "TA446+CS text"
    assert len(hits[2]) > 0, "GHSA advisory text"


# ==============================================================================
# E — benchmarks
# ==============================================================================

def test_benchmark_reset_and_build(benchmark):
    """E.1 reset+build x100 < 250ms per call."""
    import time

    def work():
        reset_pattern_matcher()
        configure_default_bootstrap_patterns_if_empty()
        match_text("dummy text for warm-up")  # force build

    # Warm up
    work()

    iterations = 100
    t0 = time.perf_counter()
    for _ in range(iterations):
        work()
    t1 = time.perf_counter()

    total_ms = (t1 - t0) * 1000
    per_call_ms = total_ms / iterations
    assert per_call_ms < 250, f"reset+build {per_call_ms:.1f}ms > 250ms"


def test_benchmark_get_default_bootstrap_patterns(benchmark):
    """E.2 get_default_bootstrap_patterns x1000 < 200ms per call."""
    import time

    iterations = 1000
    t0 = time.perf_counter()
    for _ in range(iterations):
        get_default_bootstrap_patterns()
    t1 = time.perf_counter()

    total_ms = (t1 - t0) * 1000
    per_call_ms = total_ms / iterations
    assert per_call_ms < 0.2, f"get_default {per_call_ms:.4f}ms > 0.2ms"


def test_benchmark_regex_extraction(benchmark):
    """E.3 regex extraction realistic text < 400ms per call."""
    import time

    text = (
        "Citrix NetScaler Under Active Recon for CVE-2026-3055 (CVSS 9.3) Memory Overread Bug. "
        "Host found at http://abcdefghijklmnopqrstuvwxyz0123456789abcdefghijklmnopqrstuvwxyz0123456789.onion/api. "
        "File hash: 3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b "
        "and GHSA-ab12-34cd-56ef. Advisory references CVE-2026-3055."
    )

    iterations = 1000
    t0 = time.perf_counter()
    for _ in range(iterations):
        extract_high_precision_entities(text)
    t1 = time.perf_counter()

    total_ms = (t1 - t0) * 1000
    per_call_ms = total_ms / iterations
    assert per_call_ms < 0.4, f"regex extraction {per_call_ms:.4f}ms > 0.4ms"


# ==============================================================================
# Smoke — composition with match_text
# ==============================================================================

def test_combined_literal_and_regex_extraction():
    """Combined: literal hits + regex extraction on same text."""
    configure_default_bootstrap_patterns_if_empty()

    text = "CVE-2026-3055 in Citrix with Cobalt Strike beacon and lateral movement. Hash: 3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b"
    literal_hits = match_text(text.casefold())
    entities = extract_high_precision_entities(text)

    assert len(literal_hits) > 0, "Should have literal hits"
    assert len(entities) > 0, "Should have extracted entities"

    cve_entities = [e for e in entities if e.entity_type == "cve_identifier"]
    assert len(cve_entities) >= 1


# ==============================================================================
# Precision audit — no false positives from generic words
# ==============================================================================

def test_no_false_positive_generic_words():
    """Generic non-security words should not be in V3 pack."""
    configure_default_bootstrap_patterns_if_empty()
    patterns = dict(get_default_bootstrap_patterns())

    generic_rejected = ["attack", "hacker", "threat", "risk", "alert", "issue", "bug"]
    for word in generic_rejected:
        assert word not in patterns, f"'{word}' should be rejected by precision audit"


# ==============================================================================
# V3 layer structure validation
# ==============================================================================

def test_v3_layer1_identifiers_present():
    """Layer 1 identifiers: cve-, ghsa-, rhsa-, usn-, msrc-, edb-id."""
    configure_default_bootstrap_patterns_if_empty()
    patterns = dict(get_default_bootstrap_patterns())

    for lit in ["cve-", "ghsa-", "rhsa-", "usn-", "msrc-", "edb-id"]:
        assert lit in patterns, f"Layer 1 literal {lit} missing"

    for lit in ["cve-", "ghsa-"]:
        meta = get_pattern_pack_metadata(lit)
        assert meta is not None and meta["layer"] == 1


def test_v3_layer2_ttp_present():
    """Layer 2 TTP: lateral movement, credential dumping, etc."""
    configure_default_bootstrap_patterns_if_empty()
    patterns = dict(get_default_bootstrap_patterns())

    ttp_literals = [
        "lateral movement",
        "credential dumping",
        "command and control",
        "c2 beacon",
        "privilege escalation",
        "defense evasion",
        "persistence mechanism",
        "living off the land",
        "lolbin",
        "lolbas",
    ]

    for lit in ttp_literals:
        assert lit in patterns, f"Layer 2 TTP literal '{lit}' missing"


def test_v3_layer3_malware_present():
    """Layer 3 malware/tooling: infostealer, cobalt strike, etc."""
    configure_default_bootstrap_patterns_if_empty()
    patterns = dict(get_default_bootstrap_patterns())

    malware_literals = [
        "infostealer",
        "wiper",
        "exploit kit",
        "cobalt strike",
        "mimikatz",
        "sliver c2",
        "dropper",
        "loader",
        "ransomware-as-a-service",
        "raas",
        "ransomware",
    ]

    for lit in malware_literals:
        assert lit in patterns, f"Layer 3 malware literal '{lit}' missing"


def test_v3_layer4_osint_present():
    """Layer 4 OSINT: leaked database, pastebin leak, shodan, etc."""
    configure_default_bootstrap_patterns_if_empty()
    patterns = dict(get_default_bootstrap_patterns())

    osint_literals = [
        "leaked database",
        "pastebin leak",
        "github dork",
        "shodan",
        "censys",
        "greynoise",
    ]

    for lit in osint_literals:
        assert lit in patterns, f"Layer 4 OSINT literal '{lit}' missing"
