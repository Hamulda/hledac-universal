"""Sprint 8SC: Pattern V5 — Dark market."""

import pytest

from hledac.universal.patterns.pattern_matcher import (
    configure_default_bootstrap_patterns_if_empty,
    match_text,
    reset_pattern_matcher,
)


def test_pattern_v5_dark_market():
    """darknet market + PGP required + escrow service → dark_market hit."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    text = "darknet market PGP required escrow service vendor shop"
    hits = match_text(text)

    labels = [h.label for h in hits]
    assert "dark_market" in labels
    # Multiple hits
    dark_market_hits = [h for h in hits if h.label == "dark_market"]
    assert len(dark_market_hits) >= 2


def test_pattern_v5_crypto_payment():
    """monero + xmr wallet → crypto_payment hit."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    text = "We accept monero and xmr wallet addresses"
    hits = match_text(text)

    labels = {h.label for h in hits}
    assert "crypto_payment" in labels


def test_pattern_v5_dark_protocol():
    """yggdrasil + freenet → dark_protocol hits."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    text = "yggdrasil network freenet alternative"
    hits = match_text(text)

    labels = {h.label for h in hits}
    assert "dark_protocol" in labels
