"""
Sprint 65: No-Toggles Invariant Tests

CI-safe tests that verify no toggle flags exist in critical files.
"""

import re
from pathlib import Path

import pytest

# Paths to check
CHECK_FILES = [
    "hledac/universal/federated/model_store.py",
    "hledac/universal/transport/tor_transport.py",
    "hledac/universal/transport/nym_transport.py",
    "hledac/universal/transport/__init__.py",
]

# Patterns that should NOT exist
FORBIDDEN_PATTERNS = [
    r'_AVAILABLE\s*=',
    r'ENCRYPTION_AVAILABLE',
    r'ENABLE_',
    r'USE_',
    r'if\s+\w+_AVAILABLE\s*:',
]


def read_file(path: str) -> str:
    """Read file content."""
    full_path = Path("/Users/vojtechhamada/PycharmProjects/Hledac") / path
    return full_path.read_text()


class TestNoToggles:
    """Tests verifying no toggle flags exist in critical files."""

    @pytest.mark.parametrize("file_path", CHECK_FILES)
    def test_no_toggles_in_file(self, file_path):
        """Verify no toggle flags exist in the file."""
        content = read_file(file_path)

        for pattern in FORBIDDEN_PATTERNS:
            matches = re.findall(pattern, content)
            assert not matches, f"Found forbidden pattern '{pattern}' in {file_path}: {matches}"


class TestTransportImportSafety:
    """Test that transport module imports don't crash."""

    def test_import_transport_no_crash(self):
        """Verify transport module can be imported."""
        # This should not raise even if aiohttp_socks is not available
        import hledac.universal.transport as t

        # Verify expected exports
        assert hasattr(t, 'Transport')
        assert hasattr(t, 'InMemoryTransport')
        assert hasattr(t, 'TransportResolver')
        assert hasattr(t, 'TransportContext')

        # Verify no flag exports
        assert not hasattr(t, 'TOR_AVAILABLE')
        assert not hasattr(t, 'NYM_AVAILABLE')

    def test_model_store_plaintext_works(self):
        """Verify ModelStore works in plaintext mode without crypto deps."""
        import tempfile

        import numpy as np

        # Create temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            from hledac.universal.federated.model_store import ModelStore

            store = ModelStore(path=tmpdir)

            # Test basic put/get (plaintext)
            weights = {"layer1": np.array([1.0, 2.0], dtype=np.float32)}
            store.put_model(1, weights)

            loaded = store.get_model(1)
            assert loaded is not None
            assert "layer1" in loaded

            store.close()


class TestTransportResolver:
    """Test TransportResolver works correctly."""

    def test_resolver_instantiation(self):
        """Verify TransportResolver can be instantiated."""
        from hledac.universal.transport import TransportResolver

        resolver = TransportResolver()
        assert resolver is not None

    def test_transport_context_creation(self):
        """Verify TransportContext works."""
        from hledac.universal.transport import TransportContext

        ctx = TransportContext(
            requires_anonymity=True,
            risk_level="high",
            allow_inmemory=False
        )
        assert ctx.requires_anonymity is True
        assert ctx.risk_level == "high"
        assert ctx.allow_inmemory is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
