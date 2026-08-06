"""
Dark query types — hledac_hypothesis.types.query
=================================================



Extracted from hledac_hypothesis._types (C4 Sprint Refactoring).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import msgspec


class DarkQueryType(Enum):
    """Types of dark surface queries for unindexed source expansion."""
    ONION = "onion"
    IPFS = "ipfs"
    PASTE = "paste"
    I2P = "i2p"


class DarkQuery(msgspec.Struct, frozen=True, gc=False):
    """
    Query for exploring dark/unindexed surface.

    Invariant: All dark queries MUST transit via Tor/I2P transport.
    NEVER route through aiohttp clearnet.
    """
    query_type: DarkQueryType
    query: str
    priority: float  # 0-1, higher = explore first
    source_iocs: tuple[str, ...] = ()  # IOC refs for context — empty tuple default
    reasoning: str = ""  # Why this query was generated


class _DarkQueryListResponse(msgspec.Struct, gc=False):
    """Response model for Hermes LLM dark query generation."""
    queries: list[dict[str, Any]] = msgspec.field(default_factory=list)


__all__ = [
    "DarkQueryType",
    "DarkQuery",
    "_DarkQueryListResponse",
]
