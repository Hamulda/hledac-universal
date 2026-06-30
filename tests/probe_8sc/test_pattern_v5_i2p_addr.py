"""Sprint 8SC: Pattern V5 — I2P address."""

import pytest

from hledac.universal.patterns.pattern_matcher import (
    configure_default_bootstrap_patterns_if_empty,
    match_text,
    reset_pattern_matcher,
)


def test_pattern_v5_i2p_addr():
    """I2P B32 address (.b32.i2p) → i2p_address hit via regex."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    # Valid I2P B32: exactly 52 base32 chars + .b32.i2p
    # base32 charset: a-z (26) + 2-7 (6)
    # 52 chars: 26 lowercase + 6 digits + 20 lowercase
    base52 = "abcdefghijklmnopqrstuvwxyz" + "234567" + "abcdefghijklmnopqrst"
    i2p_addr = base52 + ".b32.i2p"
    text = f"Visit {i2p_addr}"
    hits = match_text(text)

    labels = {h.label for h in hits}
    assert "i2p_address" in labels


def test_pattern_v5_i2p_literal():
    """.i2p literal → dark_protocol hit."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    text = "The .i2p network is anonymous"
    hits = match_text(text)

    labels = {h.label for h in hits}
    assert "dark_protocol" in labels
