"""
core.__main__ — Canonical sprint entry point (F350M-R legacy re-export).

ROLE: CANONICAL SPRINT OWNER (per sprint_entrypoint.py:2056).

This module exists for backward compatibility and test infrastructure that
patches `hledac.universal.core.__main__.run_sprint`. The actual implementation
lives in `runtime.sprint_entrypoint.run_sprint`; this module re-exports it.

Canonical path referenced by:
- tests/test_r0_nonfeed_reality_lock.py
- runtime/sprint_entrypoint.py (canonical_sprint_owner documentation)
- Various probe/audit reports
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from hledac.universal.runtime.sprint_entrypoint import (
    SprintFlags,
    run_sprint as _runtime_run_sprint,
)

# Re-export for backward compatibility (canonical path)
run_sprint = _runtime_run_sprint

__all__ = ["run_sprint", "SprintFlags"]
