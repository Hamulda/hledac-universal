"""
Discovery replay / cassette system for HTTP interaction recording.

Sprint F239A: Provides VCR-style replay of discovery adapter HTTP calls.
When replay_enabled is True, reads cached responses from disk instead of
making live HTTP requests.

This module is loaded eagerly by discovery adapters (circl_pdns, duckduckgo)
at import time. The replay functions are no-ops when replay_enabled=False.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

# Replay subsystem state — all False/None by default (live mode)
replay_enabled: bool = False
replay_strict_enabled: bool = False


def read_cassette(cassette_path: str) -> dict[str, Any] | None:
    """Read a cassette JSON file. Returns None if file missing or invalid."""
    if not replay_enabled:
        return None
    try:
        import orjson
        with open(cassette_path, "rb") as f:
            return orjson.loads(f.read())
    except Exception:
        return None


def write_cassette(cassette_path: str, data: dict[str, Any]) -> None:
    """Write a cassette JSON file. No-op when replay_enabled=False."""
    if not replay_enabled:
        return
    try:
        import pathlib

        import orjson
        pathlib.Path(cassette_path).parent.mkdir(parents=True, exist_ok=True)
        with open(cassette_path, "wb") as f:
            f.write(orjson.dumps(data))
    except Exception:
        pass


# TYPE_CHECKING block — imported only at type-checking time, not at runtime.
# duckduckgo_adapter is excluded from runtime import to prevent circular deps.
if TYPE_CHECKING:
    pass
