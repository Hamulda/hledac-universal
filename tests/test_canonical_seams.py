"""
CI gate for canonical seams (Issue #23).

Backs AGENTS.md > CANONICAL ENTRY POINTS. Fails the build if any canonical
entry point drifts — renamed, re-typed, or flipped async/sync. This is the
machine-readable guard that keeps AGENTS.md and reality in lockstep.

Run:
    pytest tests/test_canonical_seams.py -x
"""

from __future__ import annotations

import pytest

from tools.audit.check_canonical_seams import SEAMS, _check_one, check_all_seams


def test_canonical_seams_registered():
    # The four canonical entry points from Issue #23 must all be tracked.
    assert len(SEAMS) >= 4, "expected at least the 4 canonical entry points (fetch/write/ioc/mlx)"


def test_canonical_seams_exist_and_typed():
    results = check_all_seams()
    failed = [r for r in results if not r.ok]
    assert not failed, (
        "Canonical seam drift detected (AGENTS.md CANONICAL ENTRY POINTS):\n"
        + "\n".join(f"  - {r.name}: {r.detail}" for r in failed)
    )


@pytest.mark.parametrize("spec", SEAMS, ids=[s.name for s in SEAMS])
def test_each_canonical_seam(spec):
    result = _check_one(spec)
    assert result.ok, f"{spec.name}: {result.detail}"
