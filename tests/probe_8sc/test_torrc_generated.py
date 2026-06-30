"""Sprint 8SC: torrc generation test."""

import tempfile
from pathlib import Path

import pytest

from hledac.universal.transport.tor_transport import _generate_torrc


def test_torrc_generated():
    """_generate_torrc() creates file with required directives."""
    with tempfile.TemporaryDirectory() as tmp:
        torrc_path = Path(tmp) / "torrc"
        _generate_torrc(torrc_path)

        assert torrc_path.exists(), "torrc file must be created"
        content = torrc_path.read_text()

        assert "SocksPort 9050" in content
        assert "ControlPort 9051" in content
        assert "NumEntryGuards 3" in content
        assert "DataDirectory" in content
        assert "MaxCircuitDirtiness 600" in content
        assert "IsolateSOCKSAuth 1" in content
