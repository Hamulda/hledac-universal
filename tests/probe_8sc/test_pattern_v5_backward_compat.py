"""Sprint 8SC: Pattern V5 backward compatibility."""

import pytest

from hledac.universal.patterns.pattern_matcher import (
    configure_default_bootstrap_patterns_if_empty,
    match_text,
    reset_pattern_matcher,
    _BOOTSTRAP_PATTERNS,
)


def test_pattern_v5_backward_compat():
    """V4 patterns still present and functional."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    # V4 patterns that must still work — use text that clearly matches
    # Note: cve- literal maps to vulnerability_id (not cve_identifier, which is from regex post-pass)
    v4_tests = [
        # CVE — bootstrap literal "cve-" → vulnerability_id
        ("See CVE-2024-1234 for details", "vulnerability_id"),
        # GHSA
        ("GHSA-abcd-1234-efgh vulnerability", "ghsa_identifier"),
        # Bitcoin legacy (base58check)
        ("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa", "btc_address"),
        # Telegram
        ("t.me/username123", "telegram_link"),
        # Ransomware
        ("lockbit ransomware group", "ransomware_group"),
        # Malware
        ("cobalt strike beacon detected", "offensive_tool"),
        # Security incident
        ("data breach exposed credentials", "security_incident"),
        # Bitcoin bech32
        ("bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh", "btc_address"),
        # MISP UUID
        ("a1b2c3d4-e5f6-7890-abcd-ef1234567890", "misp_uuid"),
    ]

    for text, expected_label in v4_tests:
        hits = match_text(text)
        labels = {h.label for h in hits}
        assert expected_label in labels, f"V4 pattern failed for '{text}' → expected '{expected_label}' in {labels}"


def test_pattern_v5_all_v4_labels_present():
    """All V4 labels from bootstrap are still in patterns."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    # Collect all labels from bootstrap
    labels_in_bootstrap = {label for _, label in _BOOTSTRAP_PATTERNS}

    # Critical V4 labels that must be preserved in bootstrap
    # Note: cve_identifier is from regex, not bootstrap literal
    critical_v4_labels = [
        "vulnerability_id",
        "attack_technique", "malware_type", "offensive_tool",
        "threat_type", "security_incident", "osint_source",
        "darknet_domain", "bitcoin_payment", "telegram_link",
        "misp_indicator", "paste_site", "credential_leak",
        "ransomware_group",
    ]
    for v4_label in critical_v4_labels:
        assert v4_label in labels_in_bootstrap, f"V4 label {v4_label} missing from bootstrap"
