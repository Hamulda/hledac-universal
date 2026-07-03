"""D.8 — Onion v3: exactly 56-char base32 hits onion_v3; short misses."""
import sys

sys.path.insert(0, ".")

from hledac.universal.utils.patterns.pattern_matcher import match_text, configure_patterns, get_default_bootstrap_patterns


def test_onion_v3_56char_hit():
    """56-char v3 onion address hits onion_v3."""
    configure_patterns(get_default_bootstrap_patterns())
    # 56-char base32 string (charset: a-z excl l + 2-7)
    # Repeated charset gives valid v3 onion prefix
    text = "abcdefghijkmnopqrstuvwxyz234567abcdefghijkmnopqrstuvwxyz.onion"
    hits = match_text(text)
    labels = [h.label for h in hits]
    assert "onion_v3" in labels, f"Expected onion_v3 hit, got: {labels}"


def test_onion_short_no_v3_hit():
    """Short .onion (legacy) does NOT trigger onion_v3 regex."""
    configure_patterns(get_default_bootstrap_patterns())
    text = "abc.onion short"
    hits = match_text(text)
    labels = [h.label for h in hits]
    # Legacy .onion literal may still hit via AC automaton but NOT onion_v3 regex
    # The _RE_ONION_V3 regex requires 56 chars so this should not be onion_v3
    onion_v3_labels = [l for l in labels if l == "onion_v3"]
    assert len(onion_v3_labels) == 0, (
        f"Short onion should NOT trigger onion_v3 regex: {labels}"
    )
