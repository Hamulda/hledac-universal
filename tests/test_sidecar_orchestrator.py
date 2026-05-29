# SPDX-License-Identifier: MIT
"""
tests/test_sidecar_orchestrator.py — F226 advisory callback seal
=================================================================

Verifies the bounded advisory callback seam in SidecarOrchestrator:
- Exactly 3 named callbacks cross the scheduler facade via getattr
- No new `getattr(self._scheduler, "_run_*")` calls may be added
  without updating this test (extraction trigger)

Canonical callback names (stable API surface):
  1. _run_ct_to_passivedns_pivot_advisory  (R5, pivot advisory)
  2. _run_bgp_advisory_sidecar             (F234, BGP adapter)
  3. _run_wayback_cdx_deep_sidecar         (F234, Wayback CDX adapter)
"""

from __future__ import annotations

import ast
import pathlib

# Absolute path to the module under test
_SIDEAR_ORCHESTRATOR = pathlib.Path(__file__).parents[1] / "runtime" / "sidecar_orchestrator.py"


class TestAdvisoryCallbackSeal:
    """Seal test: only the 3 named callbacks may cross the scheduler facade."""

    # Permitted scheduler getattr callback names — must match docstring above
    PERMITTED_SCHEDULER_CALLBACKS = frozenset({
        "_run_ct_to_passivedns_pivot_advisory",
    })

    # Methods that do NOT use getattr (self-contained adapters)
    SELF_CONTAINED_ADVISORIES = frozenset({
        "_run_bgp_advisory_sidecar",
        "_run_wayback_cdx_deep_sidecar",
    })

    @classmethod
    def _parse_getattr_scheduler_callbacks(cls) -> set[str]:
        """Extract getattr(self._scheduler, "_run_*") callback names from source.

        Only "_run_" prefixed attributes are considered scheduler advisory callbacks.
        Other getattr accesses (e.g. _duckdb_store, _governor) are plain dependency
        injection, not advisory callbacks, and do not require test updates.
        """
        source = _SIDEAR_ORCHESTRATOR.read_text()
        tree = ast.parse(source)
        callbacks: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "getattr"
                    and len(node.args) >= 2
                ):
                    if (
                        isinstance(node.args[0], ast.Attribute)
                        and node.args[0].attr == "_scheduler"
                    ):
                        if isinstance(node.args[1], ast.Constant) and isinstance(
                            node.args[1].value, str
        ):
                            if node.args[1].value.startswith("_run_"):
                                callbacks.add(str(node.args[1].value))
        return callbacks

    def test_only_permitted_scheduler_callbacks_via_getattr(self):
        """Exactly the permitted callbacks cross the scheduler facade via getattr."""
        found = self._parse_getattr_scheduler_callbacks()
        extra = found - self.PERMITTED_SCHEDULER_CALLBACKS
        missing = self.PERMITTED_SCHEDULER_CALLBACKS - found
        assert not extra, f"Unexpected getattr scheduler callbacks: {extra}"
        assert not missing, f"Missing permitted callbacks: {missing}"

    def test_self_contained_advisories_have_no_scheduler_getattr(self):
        """Self-contained advisories (adapters) must NOT use getattr on scheduler."""
        source = _SIDEAR_ORCHESTRATOR.read_text()
        for name in self.SELF_CONTAINED_ADVISORIES:
            # Find the method body and verify no getattr(self._scheduler, ...) inside
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
                    for child in ast.walk(node):
                        if (
                            isinstance(child, ast.Call)
                            and isinstance(child.func, ast.Name)
                            and child.func.id == "getattr"
                            and len(child.args) >= 2
                        ):
                            if (
                                isinstance(child.args[0], ast.Attribute)
                                and child.args[0].attr == "_scheduler"
                            ):
                                raise AssertionError(
                                    f"{name} must not use getattr(self._scheduler, ...)"
                                )

    def test_callback_names_documented(self):
        """All permitted callbacks must be documented in the module docstring."""
        source = _SIDEAR_ORCHESTRATOR.read_text()
        docstring = ast.get_docstring(ast.parse(source)) or ""
        for name in self.PERMITTED_SCHEDULER_CALLBACKS | self.SELF_CONTAINED_ADVISORIES:
            assert name in docstring, f"Callback {name} not documented in module docstring"
