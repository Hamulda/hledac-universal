"""
Stub for hledac.cortex.director — module does not exist.
hledac.cortex has commander.py but no director.py. Created per Sprint F214.

Resolution path:
    GhostDirector was intended to coordinate cortex modules (commander, evaluator, etc.)
    but the architecture never required a director-level coordinator.
    The sprint scheduler directly orchestrates via commander.py.
    This stub is deprecated and should be removed once all call sites are gone.
"""
from __future__ import annotations


import logging

logger = logging.getLogger(__name__)

__all__ = []  # Deprecate: no public API needed


class GhostDirector:
    """
    Deprecated: hledac.cortex.director was never implemented.

    Use hledac.cortex.commander.Commander directly for cortex coordination.
    This class raises NotImplementedError to surface dead call sites.
    """
    __slots__ = ()

    def __init__(self, *_: object, **__: object) -> None:
        logger.warning(
            "GhostDirector is deprecated. "
            "Use hledac.cortex.commander.Commander directly. "
            "GhostDirector will be removed in a future sprint."
        )
        raise NotImplementedError(
            "GhostDirector stub — use hledac.cortex.commander.Commander instead. "
            "See compat/cortex_director.py for resolution context."
        )
