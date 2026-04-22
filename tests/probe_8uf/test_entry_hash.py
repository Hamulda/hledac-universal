"""Test entry hash computation (Sprint 8UF B.4)."""
import pytest
import sys
sys.path.insert(0, '.')

from hledac.universal.discovery.rss_atom_adapter import _entry_hash


class TestEntryHash:
    """Entry hash dedup tests."""

    def test_entry_hash_consistent(self):
        """Same inputs produce same hash."""
        h1 = _entry_hash("Critical Vuln in Product X", "2025-01-15")
        h2 = _entry_hash("Critical Vuln in Product X", "2025-01-15")
        assert h1 == h2, "Same inputs should produce identical hash"

    def test_entry_hash_different_title_differs(self):
        """Different titles produce different hashes."""
        h1 = _entry_hash("Title A", "2025-01-01")
        h2 = _entry_hash("Title B", "2025-01-01")
        assert h1 != h2, "Different titles should produce different hashes"

    def test_entry_hash_empty_handled(self):
        """Empty title and date are handled gracefully."""
        h = _entry_hash("", "")
        assert h is not None
        assert len(h) > 0

    def test_entry_hash_none_inputs(self):
        """None inputs are handled."""
        h = _entry_hash(None, None)
        assert h is not None
        assert len(h) > 0

    def test_entry_hash_deterministic(self):
        """Hash is deterministic across multiple calls."""
        title = "APT28 Phishing Campaign 2025"
        date = "2025-03-15"
        hashes = [_entry_hash(title, date) for _ in range(5)]
        assert len(set(hashes)) == 1, "Hash must be deterministic"
