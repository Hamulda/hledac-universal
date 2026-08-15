"""Probe tests for IOC dedup Rust extension and Python wrapper.

Sprint: ioc-dedup-rust
Tests IocDedupStore functionality and persistence.
"""



import tempfile
from pathlib import Path

import pytest
from _core import aclose


class TestIocDedupStore:
    """Test Rust IocDedupStore via Python wrapper."""

    @pytest.fixture
    def store(self):
        """Create fresh IocDedupStore instance."""
        from hledac_rust_extensions import IocDedupStore
        return IocDedupStore(sprint_id=1)

    def test_add_returns_true_for_new_ioc(self, store):
        """New IOC should return True."""
        assert store.add("evil.com", "domain", 0.9) is True

    def test_add_returns_false_for_duplicate(self, store):
        """Duplicate IOC should return False."""
        store.add("evil.com", "domain", 0.9)
        assert store.add("evil.com", "domain", 0.8) is False

    def test_different_type_same_value_not_duplicate(self, store):
        """Same value but different type is NOT duplicate."""
        store.add("evil.com", "domain", 0.9)
        assert store.add("evil.com", "url", 0.8) is True

    def test_ip_normalization(self, store):
        """IP addresses should be normalized (strip leading zeros)."""
        store.add("192.168.001.001", "ip", 0.8)
        # Same IP with normalized form
        assert store.add("192.168.1.1", "ip", 0.9) is False

    def test_domain_normalization(self, store):
        """Domains should be lowercased and www. stripped."""
        store.add("WWW.EVIL.COM", "domain", 0.9)
        assert store.add("evil.com", "domain", 0.8) is False
        assert store.add("www.evil.com", "domain", 0.7) is False

    def test_hash_normalization(self, store):
        """Hashes should be lowercased."""
        store.add("ABC123DEF456", "md5", 0.9)
        assert store.add("abc123def456", "md5", 0.8) is False

    def test_cve_normalization(self, store):
        """CVE IDs should be uppercased."""
        store.add("cve-2024-12345", "cve", 0.9)
        assert store.add("CVE-2024-12345", "cve", 0.8) is False

    def test_stats(self, store):
        """Stats should track total_seen, total_deduped, unique_count."""
        store.add("evil.com", "domain", 0.9)
        store.add("evil.com", "domain", 0.8)  # dup
        store.add("test.com", "domain", 0.7)
        stats = store.stats()
        assert stats == (3, 1, 2)  # seen=3, deduped=1, unique=2

    def test_len(self, store):
        """Len returns unique IOC count."""
        store.add("evil.com", "domain", 0.9)
        store.add("test.com", "domain", 0.8)
        store.add("evil.com", "domain", 0.7)  # dup
        assert store.len() == 2

    def test_contains(self, store):
        """Contains returns True if IOC exists."""
        store.add("evil.com", "domain", 0.9)
        assert store.contains("evil.com", "domain") is True
        assert store.contains("unknown.com", "domain") is False

    def test_get_by_type(self, store):
        """Get all IOCs of specified type."""
        store.add("evil.com", "domain", 0.9)
        store.add("192.168.1.1", "ip", 0.8)
        store.add("test.com", "domain", 0.7)
        domains = store.get_by_type("domain")
        assert len(domains) == 2
        assert "evil.com" in domains
        assert "test.com" in domains

    def test_advance_sprint(self, store):
        """Advance to new sprint."""
        store.add("evil.com", "domain", 0.9)
        store.advance_sprint(2)
        assert store.get_sprint() == 2

    def test_batch_add(self, store):
        """Batch add returns list of bool."""
        items = [
            ("evil.com", "domain", 0.9),
            ("evil.com", "domain", 0.8),  # dup
            ("new.com", "domain", 0.7),
        ]
        results = store.add_batch(items)
        assert results == [True, False, True]


class TestIocDedupPersistence:
    """Test IocDedupStore persistence via bytes."""

    def test_to_bytes_and_restore(self):
        """Serialize and deserialize store."""
        from hledac_rust_extensions import IocDedupStore, ioc_dedup_from_bytes

        store = IocDedupStore(sprint_id=5)
        store.add("evil.com", "domain", 0.9)
        store.add("192.168.1.1", "ip", 0.8)

        data = store.to_bytes()
        assert len(data) > 0

        restored = ioc_dedup_from_bytes(data)
        assert restored.stats() == store.stats()
        assert restored.get_by_type("domain") == ["evil.com"]
        assert restored.get_by_type("ip") == ["192.168.1.1"]


class TestIocDedupManager:
    """Test Python wrapper IocDedupManager."""

    @pytest.fixture
    def tmp_path(self):
        """Create temp file path."""
        with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
            path = Path(f.name)
        yield path
        path.unlink(missing_ok=True)

    def test_manager_add(self, tmp_path):
        """Manager.add returns True for new IOC."""
        from tools.ioc_dedup import IocDedupManager
        manager = IocDedupManager(persist_path=str(tmp_path), sprint_id=1)
        assert manager.add("evil.com", "domain", 0.9) is True

    def test_manager_persistence(self, tmp_path):
        """Manager persists and reloads state."""
        from tools.ioc_dedup import IocDedupManager
        manager = IocDedupManager(persist_path=str(tmp_path), sprint_id=1)
        manager.add("evil.com", "domain", 0.9)
        manager.save()

        manager2 = IocDedupManager(persist_path=str(tmp_path), sprint_id=2)
        assert manager2.stats == (1, 0, 1)
        assert manager2.get_by_type("domain") == ["evil.com"]

    def test_manager_advance_sprint(self, tmp_path):
        """Manager advances sprint."""
        from tools.ioc_dedup import IocDedupManager
        manager = IocDedupManager(persist_path=str(tmp_path), sprint_id=1)
        manager.add("evil.com", "domain", 0.9)
        manager.advance_sprint(2)
        assert manager.add("test.com", "domain", 0.8) is True

    def test_fallback_on_invalid_data(self, tmp_path):
        """Manager creates new store on invalid data."""
        from tools.ioc_dedup import IocDedupManager
        # Write invalid data
        tmp_path.write_bytes(b"invalid")
        # Should not crash, creates new store
        manager = IocDedupManager(persist_path=str(tmp_path), sprint_id=1)
        assert manager.stats == (0, 0, 0)
