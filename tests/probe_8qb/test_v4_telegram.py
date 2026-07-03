"""D.6 — Telegram t.me/ link hits telegram_link."""
import sys

sys.path.insert(0, ".")

from hledac.universal.utils.patterns.pattern_matcher import match_text, configure_patterns, get_default_bootstrap_patterns


def test_telegram_tme_hit():
    """t.me/ link hits telegram_link label."""
    configure_patterns(get_default_bootstrap_patterns())
    text = "join t.me/darkmarket-channel for leaked data"
    hits = match_text(text)
    labels = [h.label for h in hits]
    assert "telegram_link" in labels, f"Expected telegram_link hit, got: {labels}"
