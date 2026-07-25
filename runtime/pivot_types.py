"""PivotTask — standalone DTO for the agentic pivot loop.

F350M-R: Extracted from archive/scheduler_archives/sprint_scheduler_v1_archived.py
to break the lazy-import chain that was keeping the v1 archived module live.

Canonical import path:
    from hledac.universal.runtime.pivot_types import PivotTask
"""

from __future__ import annotations

import msgspec


class PivotTask(msgspec.Struct, frozen=True, gc=False):
    """Pivot task pro agentic pivot loop -- prioritizován podle confidence * degree."""

    priority: float
    ioc_type: str
    ioc_value: str
    task_type: str


__all__ = ["PivotTask"]
