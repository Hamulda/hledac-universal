"""Sprint 8SC: torrc not overwritten if exists."""

import tempfile
from pathlib import Path

import pytest

from hledac.universal.transport.tor_transport import _generate_torrc


def test_torrc_not_overwritten():
    """Existing torrc is not overwritten (mtime preserved)."""
    with tempfile.TemporaryDirectory() as tmp:
        torrc_path = Path(tmp) / "torrc"
        original = "Custom torrc content\nSocksPort 9999\n"
        torrc_path.write_text(original)
        original_mtime = torrc_path.stat().st_mtime

        _generate_torrc(torrc_path)

        assert torrc_path.read_text() == original
        assert torrc_path.stat().st_mtime == original_mtime
