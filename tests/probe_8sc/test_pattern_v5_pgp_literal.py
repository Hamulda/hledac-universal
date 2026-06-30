"""Sprint 8SC: Pattern V5 — PGP literal."""

import pytest

from hledac.universal.patterns.pattern_matcher import (
    configure_default_bootstrap_patterns_if_empty,
    match_text,
    reset_pattern_matcher,
)


def test_pattern_v5_pgp_literal():
    """-----BEGIN PGP PUBLIC KEY BLOCK----- → pgp_artifact hit."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    text = "My PGP key:\n-----BEGIN PGP PUBLIC KEY BLOCK-----\n..."
    hits = match_text(text)

    labels = {h.label for h in hits}
    assert "pgp_artifact" in labels


def test_pattern_v5_pgp_fingerprint_regex():
    """PGP fingerprint (40 hex chars) → pgp_fingerprint hit via regex."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    # Valid PGP fingerprint with spaces: 10 groups of 4 hex chars
    fp = "1234 5678 9ABC DEF0 1234 5678 9ABC DEF0 1234 5678"
    text = f"PGP: {fp}"
    hits = match_text(text)

    labels = {h.label for h in hits}
    assert "pgp_fingerprint" in labels


def test_pattern_v5_pgp_fingerprint_no_spaces():
    """PGP fingerprint without spaces also matches."""
    reset_pattern_matcher()
    configure_default_bootstrap_patterns_if_empty()

    fp = "123456789ABCDEF0123456789ABCDEF012345678"
    text = f"Key: {fp}"
    hits = match_text(text)

    labels = {h.label for h in hits}
    assert "pgp_fingerprint" in labels
