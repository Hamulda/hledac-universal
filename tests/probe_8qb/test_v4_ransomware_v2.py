"""D.7 — Ransomware groups V2: lockbit, alphv, rhysida, bl00dy, 8base."""
import sys

sys.path.insert(0, ".")

from hledac.universal.utils.patterns.pattern_matcher import match_text, configure_patterns, get_default_bootstrap_patterns


def test_ransomware_lockbit():
    configure_patterns(get_default_bootstrap_patterns())
    hits = match_text("lockbit 3.0 ransomware affiliate")
    labels = [h.label for h in hits]
    assert "ransomware_group" in labels, f"Expected ransomware_group, got: {labels}"


def test_ransomware_alphv_blackcat():
    configure_patterns(get_default_bootstrap_patterns())
    hits = match_text("alphv blackcat ransomware group")
    labels = [h.label for h in hits]
    assert "ransomware_group" in labels, f"Expected ransomware_group, got: {labels}"


def test_ransomware_rhysida():
    configure_patterns(get_default_bootstrap_patterns())
    hits = match_text("rhysida group ransomware demand")
    labels = [h.label for h in hits]
    assert "ransomware_group" in labels, f"Expected ransomware_group, got: {labels}"


def test_ransomware_bl00dy():
    configure_patterns(get_default_bootstrap_patterns())
    hits = match_text("bl00dy ransomware infection")
    labels = [h.label for h in hits]
    assert "ransomware_group" in labels, f"Expected ransomware_group, got: {labels}"


def test_ransomware_8base():
    configure_patterns(get_default_bootstrap_patterns())
    hits = match_text("8base ransomware attack")
    labels = [h.label for h in hits]
    assert "ransomware_group" in labels, f"Expected ransomware_group, got: {labels}"
