"""Sprint 8SC: Pattern V5 — Monero address."""

import pytest

from hledac.universal.utils.patterns.pattern_matcher import (
    configure_default_bootstrap_patterns_if_empty,
    match_text,
    reset_pattern_matcher,
)


def test_pattern_v5_xmr_addr():
    """Monero mainnet address (95 chars, starts 4) → xmr_address hit."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    # Valid Monero mainnet address - 95 chars starting with 4
    # Use realistic Monero address characters (uppercase mix)
    xmr = "4" + "A" * 94
    text = f"Donate XMR: {xmr}"
    hits = match_text(text)

    labels = {h.label for h in hits}
    assert "xmr_address" in labels


def test_pattern_v5_xmr_addr_lowercase():
    """XMR address with mixed case still matches."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    # Mix of valid XMR chars after "4" prefix
    xmr = "4" + "8k" * 47  # "48k48k..." = 94 chars
    text = f"Payment: {xmr}"
    hits = match_text(text)

    labels = {h.label for h in hits}
    assert "xmr_address" in labels
