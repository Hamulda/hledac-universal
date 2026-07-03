"""D.4 — BTC legacy: 26+ chars hits btc_address; <26 chars misses."""
import sys

sys.path.insert(0, ".")

from hledac.universal.utils.patterns.pattern_matcher import match_text, configure_patterns, get_default_bootstrap_patterns


def test_btc_legacy_34chars_hit():
    """Standard 34-char P2PKH address (bc1...example) hits btc_address."""
    configure_patterns(get_default_bootstrap_patterns())
    # Valid 34-char P2PKH: starts with 1, valid base58 chars after
    text = "1A2B3C4D5E6F7G8H9J1A2B3C4D5E6F7G8H"
    hits = match_text(text)
    labels = [h.label for h in hits]
    assert "btc_address" in labels, f"Expected btc_address hit, got: {labels}"


def test_btc_legacy_short_no_hit():
    """Too-short base58 string (<26 chars) must NOT trigger btc_address."""
    configure_patterns(get_default_bootstrap_patterns())
    text = "1abc123"
    hits = match_text(text)
    labels = [h.label for h in hits]
    assert "btc_address" not in labels, f"Should NOT hit btc_address: {labels}"
