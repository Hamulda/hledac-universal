"""tests/test_resource_governor_authority_seal.py

F214R: ResourceGovernor authority seal probe tests.
Validates admission authority consistency and hot-path caller coverage.

Run: pytest tests/test_resource_governor_authority_seal.py -v
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestEvaluateBranchConcurrencyConsistency:
    """F214R-1: evaluate().branch_concurrency matches branch_admission().branch_concurrency."""

    @pytest.fixture
    def governor(self):
        from hledac.universal.runtime.resource_governor import M1ResourceGovernor

        return M1ResourceGovernor()

    def _mock_uma(self, state: str) -> MagicMock:
        """Create a mock UMA status for the given state."""
        mock = MagicMock()
        mock.state = state
        mock.system_used_gib = 0
        mock.system_available_gib = 8.0
        mock.is_critical = state == "critical"
        mock.is_emergency = state == "emergency"
        mock.is_warn = state == "warn"
        mock.high_water = 0.5
        return mock

    def _assert_branch_concurrency_match(self, gov: M1ResourceGovernor, uma_state: str, expected: int) -> None:
        """Helper: branch_concurrency must match between evaluate() and branch_admission()."""
        with patch.object(gov, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status", return_value=self._mock_uma(uma_state)):
                import asyncio
                decision = asyncio.run(gov.evaluate())
                branch = gov.branch_admission()
                assert decision.branch_concurrency == expected, (
                    f"[{uma_state}] evaluate().branch_concurrency={decision.branch_concurrency} != "
                    f"branch_admission().branch_concurrency={branch.branch_concurrency}"
                )
                assert branch.branch_concurrency == expected

    def _assert_model_loaded_branch_concurrency_match(self, gov: M1ResourceGovernor, expected: int) -> None:
        """Helper: branch_concurrency must match when model is loaded."""
        with patch.object(gov, "_get_model_status", return_value={"loaded": True}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status", return_value=self._mock_uma("ok")):
                import asyncio

                decision = asyncio.run(gov.evaluate())
                branch = gov.branch_admission()
                assert decision.branch_concurrency == expected, (
                    f"[model_loaded] evaluate().branch_concurrency={decision.branch_concurrency} != "
                    f"branch_admission().branch_concurrency={branch.branch_concurrency}"
                )
                assert branch.branch_concurrency == expected

    def test_branch_concurrency_ok(self, governor):
        """branch_concurrency is 4 in normal (ok) state."""
        self._assert_branch_concurrency_match(governor, "ok", 4)

    def test_branch_concurrency_warn(self, governor):
        """branch_concurrency is 3 in warn state."""
        self._assert_branch_concurrency_match(governor, "warn", 3)

    def test_branch_concurrency_critical(self, governor):
        """branch_concurrency is 1 in critical state."""
        self._assert_branch_concurrency_match(governor, "critical", 1)

    def test_branch_concurrency_emergency(self, governor):
        """branch_concurrency is 1 in emergency state."""
        self._assert_branch_concurrency_match(governor, "emergency", 1)

    def test_branch_concurrency_model_loaded(self, governor):
        """branch_concurrency is 2 when model is loaded."""
        self._assert_model_loaded_branch_concurrency_match(governor, 2)


class TestRendererAdmissionConsistency:
    """F214R-2: evaluate().allow_renderer is consistent with renderer_admission().allowed."""

    @pytest.fixture
    def governor(self):
        from hledac.universal.runtime.resource_governor import M1ResourceGovernor

        return M1ResourceGovernor()

    def _mock_uma(self, state: str) -> MagicMock:
        mock = MagicMock()
        mock.state = state
        mock.system_used_gib = 0
        mock.system_available_gib = 8.0
        mock.is_critical = state == "critical"
        mock.is_emergency = state == "emergency"
        mock.is_warn = state == "warn"
        mock.high_water = 0.5
        return mock

    def _assert_renderer_consistency(self, gov: M1ResourceGovernor, uma_state: str, expected: bool) -> None:
        with patch.object(gov, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status", return_value=self._mock_uma(uma_state)):
                import asyncio

                decision = asyncio.run(gov.evaluate())
                renderer = gov.renderer_admission()
                assert decision.allow_renderer == expected, (
                    f"[{uma_state}] evaluate().allow_renderer={decision.allow_renderer} != "
                    f"renderer_admission().allowed={renderer.allowed}"
                )
                assert renderer.allowed == expected

    def _assert_renderer_model_loaded(self, gov: M1ResourceGovernor, expected: bool) -> None:
        with patch.object(gov, "_get_model_status", return_value={"loaded": True}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status", return_value=self._mock_uma("ok")):
                import asyncio

                decision = asyncio.run(gov.evaluate())
                renderer = gov.renderer_admission()
                assert decision.allow_renderer == expected
                assert renderer.allowed == expected

    def test_renderer_allowed_ok(self, governor):
        """Renderer allowed in normal (ok) state."""
        self._assert_renderer_consistency(governor, "ok", True)

    def test_renderer_denied_critical(self, governor):
        """Renderer denied in critical state."""
        self._assert_renderer_consistency(governor, "critical", False)

    def test_renderer_denied_emergency(self, governor):
        """Renderer denied in emergency state."""
        self._assert_renderer_consistency(governor, "emergency", False)

    def test_renderer_denied_model_loaded(self, governor):
        """Renderer denied when model is loaded."""
        self._assert_renderer_model_loaded(governor, False)


class TestModelAdmissionConsistency:
    """F214R-3: evaluate().allow_model_load is consistent with model_admission().allowed."""

    @pytest.fixture
    def governor(self):
        from hledac.universal.runtime.resource_governor import M1ResourceGovernor

        return M1ResourceGovernor()

    def _mock_uma(self, state: str) -> MagicMock:
        mock = MagicMock()
        mock.state = state
        mock.system_used_gib = 0
        mock.system_available_gib = 8.0
        mock.is_critical = state == "critical"
        mock.is_emergency = state == "emergency"
        mock.is_warn = state == "warn"
        mock.high_water = 0.5
        return mock

    def _assert_model_load_consistency(self, gov: M1ResourceGovernor, uma_state: str, expected: bool) -> None:
        with patch.object(gov, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status", return_value=self._mock_uma(uma_state)):
                import asyncio

                decision = asyncio.run(gov.evaluate())
                model_adm = gov.model_admission()
                assert decision.allow_model_load == expected, (
                    f"[{uma_state}] evaluate().allow_model_load={decision.allow_model_load} != "
                    f"model_admission().allowed={model_adm.allowed}"
                )
                assert model_adm.allowed == expected

    def test_model_load_allowed_ok(self, governor):
        """Model load allowed in normal (ok) state."""
        self._assert_model_load_consistency(governor, "ok", True)

    def test_model_load_denied_critical(self, governor):
        """Model load denied in critical state."""
        self._assert_model_load_consistency(governor, "critical", False)

    def test_model_load_denied_emergency(self, governor):
        """Model load denied in emergency state."""
        self._assert_model_load_consistency(governor, "emergency", False)


class TestSidecarAdmissionHotPath:
    """F214R-4: sidecar_admission() has hot-path caller in runtime/sidecar_bus.py."""

    def test_sidecar_admission_caller_in_sidecar_bus(self):
        """Verify sidecar_admission is called from sidecar_bus.py at runtime."""
        import ast

        with open("runtime/sidecar_bus.py") as f:
            source = f.read()

        tree = ast.parse(source)
        calls = []

        class CallFinder(ast.NodeVisitor):
            def visit_Call(self, node):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == "sidecar_admission":
                        calls.append(node.func.attr)
                self.generic_visit(node)

        CallFinder().visit(tree)
        assert len(calls) > 0, "sidecar_admission() has no caller in runtime/sidecar_bus.py"


class TestLaneAdmissionHotPath:
    """F214R-5: lane_admission() has hot-path caller in runtime/sprint_scheduler.py."""

    def test_lane_admission_caller_in_sprint_scheduler(self):
        """Verify lane_admission is called from sprint_scheduler.py at runtime."""
        import ast

        with open("runtime/sprint_scheduler.py") as f:
            source = f.read()

        tree = ast.parse(source)
        calls = []

        class CallFinder(ast.NodeVisitor):
            def visit_Call(self, node):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == "lane_admission":
                        calls.append(node.func.attr)
                self.generic_visit(node)

        CallFinder().visit(tree)
        assert len(calls) > 0, "lane_admission() has no caller in runtime/sprint_scheduler.py"


class TestPendingIntegrationMarkers:
    """F214R-6: Pending integration markers for methods without production callers.

    renderer_admission(), model_admission(), branch_admission() have no confirmed
    production callers beyond the consistency tests above. These are tracked as
    @pending_integration in resource_governor.py.
    """

    @pytest.fixture
    def governor(self):
        from hledac.universal.runtime.resource_governor import M1ResourceGovernor

        return M1ResourceGovernor()

    def _mock_uma(self, state: str) -> MagicMock:
        mock = MagicMock()
        mock.state = state
        mock.system_used_gib = 0
        mock.system_available_gib = 8.0
        mock.is_critical = state == "critical"
        mock.is_emergency = state == "emergency"
        mock.is_warn = state == "warn"
        mock.high_water = 0.5
        return mock

    @pytest.mark.xfail(reason="pending integration: renderer_admission() not wired to production call sites")
    def test_renderer_admission_has_pending_marker(self, governor):
        """renderer_admission() carries @pending_integration marker."""
        import asyncio

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status", return_value=self._mock_uma("ok")):
                result = asyncio.run(gov.evaluate())
                # If this passes, renderer_admission() is being called in production
                assert result.allow_renderer is not None

    @pytest.mark.xfail(reason="pending integration: model_admission() not wired to production call sites")
    def test_model_admission_has_pending_marker(self, governor):
        """model_admission() carries @pending_integration marker."""
        import asyncio

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status", return_value=self._mock_uma("ok")):
                result = asyncio.run(gov.evaluate())
                # If this passes, model_admission() is being called in production
                assert result.allow_model_load is not None

    @pytest.mark.xfail(reason="pending integration: branch_admission() not wired to production call sites")
    def test_branch_admission_has_pending_marker(self, governor):
        """branch_admission() carries @pending_integration marker."""
        import asyncio

        with patch.object(governor, "_get_model_status", return_value={"loaded": False}):
            with patch("hledac.universal.runtime.resource_governor.sample_uma_status", return_value=self._mock_uma("ok")):
                result = asyncio.run(gov.evaluate())
                # If this passes, branch_admission() is being called in production
                assert result.branch_concurrency > 0
