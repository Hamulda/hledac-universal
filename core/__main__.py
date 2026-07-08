"""
Deprecated shim: core/__main__.py has moved to runtime/sprint_entrypoint.py.

F320: Decoupling core/ from runtime/ — sprint entrypoint moved to:
    runtime/sprint_entrypoint.py

Canonical path: python -m hledac.universal --sprint
    → root __main__.main() --sprint
    → runtime.sprint_entrypoint.run_sprint()

Usage:
    python -m hledac.universal --sprint --query "LockBit ransomware" --duration 1800
    python -m hledac.universal.runtime.sprint_entrypoint --sprint --query "..." --duration 1800
"""
from __future__ import annotations

import warnings

warnings.warn(
    "hledac.universal.core.__main__ is deprecated. "
    "Use hledac.universal.runtime.sprint_entrypoint instead. "
    "Canonical path: python -m hledac.universal --sprint",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export all public names from new location for backward compatibility
from hledac.universal.runtime.sprint_entrypoint import (  # noqa: E402
    SprintFlags,
    run_sprint,
    run_pre_sprint_checks,
    write_sprint_delta,
    dry_run_sprint,
    run_ct_pivot,
    run_semantic_pivot,
    main,
    _is_meaningful_run,
    _runtime_truth,
    _make_sprint_id,
    AcqReportPayload,
    acq_payload_to_dict,
)

__all__ = [
    "SprintFlags",
    "run_sprint",
    "run_pre_sprint_checks",
    "write_sprint_delta",
    "dry_run_sprint",
    "run_ct_pivot",
    "run_semantic_pivot",
    "main",
    "_is_meaningful_run",
    "_runtime_truth",
    "_make_sprint_id",
    "AcqReportPayload",
    "acq_payload_to_dict",
]
