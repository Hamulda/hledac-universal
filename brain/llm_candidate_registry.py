"""
STUB MODULE — hledac.universal.brain.llm_candidate_registry
Status: Planned, not yet implemented.
See IMPLEMENTATION_ROADMAP.md for implementation priority.
"""
from __future__ import annotations


import logging
from typing import Any

logger = logging.getLogger(__name__)
__all__: list[str] = []


def __getattr__(name: str) -> Any:
    logger.debug("Stub %s.%s accessed", __name__, name)
    return None
