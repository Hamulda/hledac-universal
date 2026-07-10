"""
Source tier enumeration for acquisition lane prioritization.

Extracted from runtime/sprint_scheduler.py (Phase 1 of modular decomposition).
F289: SprintSchedulerConfig removed — canonical version lives in runtime/sprint_scheduler.py.
"""


from enum import Enum, auto


class SourceTier(Enum):
    """Feed source priority tier."""
    SURFACE = auto()        # high-value real-time feeds (news, alerts)
    STRUCTURED_TI = auto()  # structured threat intel feeds
    DEEP = auto()           # archive, historical, passive DNS
    ARCHIVE = auto()        # Wayback, archive.org
    OTHER = auto()          # everything else


# Tier ordering (high -> low priority)
_TIER_ORDER: list[SourceTier] = [
    SourceTier.SURFACE,
    SourceTier.STRUCTURED_TI,
    SourceTier.DEEP,
    SourceTier.ARCHIVE,
    SourceTier.OTHER,
]
