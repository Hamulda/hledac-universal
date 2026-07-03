"""D.5 — BTC bech32 (bc1) address hits btc_address."""
import sys

sys.path.insert(0, ".")

from hledac.universal.utils.patterns.pattern_matcher import match_text, configure_patterns, get_default_bootstrap_patterns


def test_btc_bech32_hit():
    """Standard bc1 bech32 address hits btc_address."""
    configure_patterns(get_default_bootstrap_patterns())
    text = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t7 bitcoin payment"
    hits = match_text(text)
    labels = [h.label for h in hits]
    assert "btc_address" in labels, f"Expected btc_address hit, got: {labels}"
